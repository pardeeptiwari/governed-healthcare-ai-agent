import re
from datetime import date
from pathlib import Path

from rank_bm25 import BM25Okapi


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def front_matter(text: str) -> tuple[dict[str, str], str]:
    parts = re.split(r"(?m)^---[ \t]*$", text, maxsplit=2)
    if len(parts) != 3 or parts[0]:
        raise ValueError("Expected document front matter")
    metadata = {}
    for line in parts[1].strip().splitlines():
        key, separator, value = line.partition(":")
        if not separator or not key or not value.strip() or key in metadata:
            raise ValueError("Invalid document metadata")
        metadata[key] = value.strip()
    return metadata, parts[2]


def policy_rows(content: str):
    fence = ""
    for line in content.splitlines():
        marker = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$", line)
        if marker:
            if not fence:
                fence = marker[1]
            elif marker[1].startswith(fence) and not marker[2].strip():
                fence = ""
            continue
        if fence or not line.startswith("|") or not line.rstrip().endswith("|"):
            continue
        cells = tuple(value.strip() for value in line.rstrip()[1:-1].split("|"))
        if len(cells) >= 2 and cells[0] not in {"Benefit", "Topic", "Status", "CARC", "Service"} and words(cells[0]):
            yield cells


class Knowledge:
    """Index only policy rows and their provenance."""

    def __init__(self, root: Path):
        self.rows = {}
        self.quarantined = 0
        for tenant in ("plan_a", "plan_b"):
            rows = []
            folder = root / tenant
            if folder.is_symlink() or folder.resolve().parent != root.resolve():
                raise ValueError(f"Invalid policy directory for {tenant}")
            for path in sorted(folder.glob("*.md")):
                if path.is_symlink():
                    self.quarantined += 1
                    continue
                try:
                    meta, content = front_matter(path.read_text(encoding="utf-8-sig"))
                    valid = meta["tenant"] == tenant and bool(re.fullmatch(r"[A-Z]+", meta["product_code"]))
                    effective = date.fromisoformat(meta["effective_date"])
                    valid = valid and int(meta["plan_year"]) == effective.year
                except (KeyError, ValueError, UnicodeError):
                    valid = False
                if not valid:
                    self.quarantined += 1
                    continue
                for cells in policy_rows(content):
                    rows.append((meta["product_code"], effective, path.name, cells))
            if not rows:
                raise ValueError(f"No policy data for {tenant}")
            self.rows[tenant] = rows

    def find(self, tenant: str, product: str, when: date, query: str,
             all_products: bool = False) -> tuple[str, tuple[str, ...]] | None:
        query_words = words(query)
        query_terms = set(query_words)
        eligible = [
            row for row in self.rows.get(tenant, ())
            if row[1].year == when.year and row[1] <= when
            and row[0] in ({"ALL"} if all_products else {"ALL", product})
            and set(words(row[3][0])) & query_terms
        ]
        if not eligible:
            return None
        exact = [row for row in eligible if words(row[3][0]) == query_words]
        if exact:
            best = max(exact, key=lambda row: (row[0] == product, row[1]))
            if any(row[:2] == best[:2] and row[3] != best[3] for row in exact):
                return None
            return best[2], best[3]
        corpus = [words(row[3][0]) for row in eligible]
        scores = BM25Okapi(corpus).get_scores(query_words)

        def rank(index: int):
            product_code, effective, _, _ = eligible[index]
            terms = corpus[index]
            return (
                len(set(terms) & query_terms), float(scores[index]),
                product_code == product, effective,
            )

        best = max(range(len(eligible)), key=rank)
        best_rank = rank(best)
        source, cells = eligible[best][2:]
        if any(rank(index) == best_rank and row[3] != cells for index, row in enumerate(eligible)):
            return None
        return source, cells
