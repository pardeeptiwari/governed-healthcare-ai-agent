import re
from datetime import date

from rank_bm25 import BM25Okapi


def words(text):
    return re.findall(r"[a-z0-9]+", text.lower())


class Knowledge:
    """Index only policy rows and their provenance."""

    def __init__(self, root):
        self.rows = {}
        self.quarantined = 0
        for tenant in ("plan_a", "plan_b"):
            rows = []
            for path in sorted((root / tenant).glob("*.md")):
                if path.is_symlink():
                    self.quarantined += 1
                    continue
                try:
                    _, header, content = path.read_text(encoding="utf-8").split("---", 2)
                    meta = dict(line.split(": ", 1) for line in header.strip().splitlines())
                    valid = meta["tenant"] == tenant and bool(re.fullmatch(r"[A-Z]+", meta["product_code"]))
                    effective = date.fromisoformat(meta["effective_date"])
                    valid = valid and int(meta["plan_year"]) == effective.year
                except (KeyError, ValueError, UnicodeError):
                    valid = False
                if not valid:
                    self.quarantined += 1
                    continue
                for line in content.splitlines():
                    if not line.startswith("|"):
                        continue
                    cells = tuple(value.strip() for value in line.strip("|").split("|"))
                    if len(cells) < 2 or cells[0] in {"Benefit", "Topic", "Status", "CARC", "Service"}:
                        continue
                    if not words(cells[0]):
                        continue
                    rows.append((meta["product_code"], effective, path.name, cells))
            if not rows:
                raise ValueError(f"No policy data for {tenant}")
            self.rows[tenant] = rows

    def find(self, tenant, product, when, query, all_products=False):
        eligible = [
            row for row in self.rows[tenant]
            if row[1].year == when.year and row[1] <= when
            and row[0] in ({"ALL"} if all_products else {"ALL", product})
            and set(words(row[3][0])) & set(words(query))
        ]
        if not eligible:
            return None
        corpus = [words(row[3][0]) for row in eligible]
        scores = BM25Okapi(corpus).get_scores(words(query))
        index = max(range(len(eligible)), key=lambda n: (
            eligible[n][3][0].lower() == query.lower(),
            len(set(corpus[n]) & set(words(query))),
            float(scores[n]), eligible[n][0] == product, eligible[n][1],
        ))
        return eligible[index][2:]

