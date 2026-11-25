import json
import sqlite3
from contextlib import closing, contextmanager
from hashlib import sha256


class State:
    def __init__(self, filename):
        self.filename = filename
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS turns (
                conversation TEXT, id TEXT, fingerprint TEXT NOT NULL, receipt TEXT, audit TEXT,
                PRIMARY KEY (conversation, id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS active_filings (
                tenant TEXT NOT NULL, member TEXT NOT NULL, conversation TEXT NOT NULL,
                operation_key TEXT NOT NULL, body TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tenant, member))""")
            db.execute("""INSERT OR IGNORE INTO active_filings (tenant, member, conversation, operation_key, body)
                SELECT json_extract(payload, '$.tenant'), json_extract(payload, '$.member'), id,
                       json_extract(payload, '$.intent.key'), json_extract(payload, '$.intent.body')
                FROM conversations
                WHERE json_extract(payload, '$.intent.state') IN ('sending', 'uncertain')
                  AND json_extract(payload, '$.intent.key') IS NOT NULL""")
            db.execute("""CREATE TABLE IF NOT EXISTS filing_operations (
                operation_key TEXT PRIMARY KEY, tenant TEXT NOT NULL, member TEXT NOT NULL,
                conversation TEXT NOT NULL, turn_id TEXT, body_hash TEXT NOT NULL,
                status TEXT NOT NULL, recoveries INTEGER NOT NULL DEFAULT 0)""")
            db.execute("""CREATE TABLE IF NOT EXISTS filing_attempts (
                id INTEGER PRIMARY KEY, operation_key TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, decision TEXT)""")
            for key, tenant, member, conversation, body in db.execute(
                "SELECT operation_key, tenant, member, conversation, body FROM active_filings"
            ).fetchall():
                db.execute("""INSERT OR IGNORE INTO filing_operations
                    (operation_key, tenant, member, conversation, body_hash, status)
                    VALUES (?, ?, ?, ?, ?, 'OUTCOME_UNKNOWN')""",
                    (key, tenant, member, conversation, sha256(body.encode()).hexdigest()))

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.filename)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def create(self, identifier, payload):
        with self.transaction() as db:
            db.execute("INSERT INTO conversations VALUES (?,?)", (identifier, json.dumps(payload)))

    def load(self, identifier):
        with self.transaction() as db:
            row = db.execute("SELECT payload FROM conversations WHERE id=?", (identifier,)).fetchone()
            return json.loads(row[0]) if row else None

    def reserve(self, identifier, turn, digest):
        with self.transaction() as db:
            row = db.execute("SELECT fingerprint, receipt FROM turns WHERE conversation=? AND id=?",
                             (identifier, turn)).fetchone()
            if row:
                return row
            db.execute("INSERT INTO turns VALUES (?,?,?,NULL,NULL)", (identifier, turn, digest))
            return None

    def save(self, identifier, payload):
        with self.transaction() as db:
            db.execute("UPDATE conversations SET payload=? WHERE id=?", (json.dumps(payload), identifier))

    def begin_filing(self, identifier: str, payload: dict, turn_id: str | None = None) -> bool:
        with self.transaction() as db:
            reserved = db.execute(
                """INSERT INTO active_filings (tenant, member, conversation, operation_key, body)
                   VALUES (?, ?, ?, ?, ?) ON CONFLICT (tenant, member) DO NOTHING""",
                (payload["tenant"], payload["member"], identifier,
                 payload["intent"]["key"], payload["intent"]["body"]),
            )
            if not reserved.rowcount:
                return False
            intent = payload["intent"]
            db.execute("""INSERT INTO filing_operations
                (operation_key, tenant, member, conversation, turn_id, body_hash, status)
                VALUES (?, ?, ?, ?, ?, ?, 'SENDING')""",
                (intent["key"], payload["tenant"], payload["member"], identifier, turn_id,
                 sha256(intent["body"].encode()).hexdigest()))
            db.execute("UPDATE conversations SET payload=? WHERE id=?", (json.dumps(payload), identifier))
            return True

    def finish(self, identifier, payload, turn, receipt, audit):
        with self.transaction() as db:
            intent = payload.get("intent")
            if intent and intent["state"] == "uncertain":
                saved = json.loads(db.execute(
                    "SELECT payload FROM conversations WHERE id=?", (identifier,)
                ).fetchone()[0]).get("intent", {})
                if saved.get("key") == intent["key"] and saved["state"] == "filed":
                    payload["intent"] = saved
            db.execute("UPDATE conversations SET payload=? WHERE id=?", (json.dumps(payload), identifier))
            db.execute("UPDATE turns SET receipt=?, audit=? WHERE conversation=? AND id=?",
                       (json.dumps(receipt), json.dumps(audit), identifier, turn))
            if receipt["outcome"] == "filed":
                db.execute("UPDATE filing_operations SET status='FILED' WHERE operation_key=?",
                           (payload["intent"]["key"],))
                db.execute(
                    """DELETE FROM active_filings
                       WHERE tenant=? AND member=? AND conversation=? AND operation_key=?""",
                    (payload["tenant"], payload["member"], identifier, payload["intent"]["key"]),
                )
            elif payload.get("intent", {}).get("state") == "uncertain":
                db.execute("""UPDATE filing_operations SET status='OUTCOME_UNKNOWN'
                    WHERE operation_key=? AND status='SENDING'""", (payload["intent"]["key"],))

    def start_attempt(self, key: str) -> int:
        with self.transaction() as db:
            return db.execute("INSERT INTO filing_attempts (operation_key) VALUES (?)", (key,)).lastrowid

    def finish_attempt(self, attempt: int, decision: str) -> None:
        with self.transaction() as db:
            db.execute("UPDATE filing_attempts SET decision=? WHERE id=?", (decision, attempt))

    def recover_interrupted(self) -> None:
        with self.transaction() as db:
            db.execute("""UPDATE filing_operations SET status='OUTCOME_UNKNOWN'
                       WHERE status IN ('SENDING', 'RECOVERING')""")

    def claim_recovery(self, durable_replay: bool) -> tuple | None:
        with self.transaction() as db:
            if not durable_replay:
                db.execute("UPDATE filing_operations SET status='MANUAL_REVIEW' WHERE status='OUTCOME_UNKNOWN'")
                return None
            db.execute("""UPDATE filing_operations SET status='MANUAL_REVIEW'
                       WHERE status='OUTCOME_UNKNOWN' AND recoveries>=2""")
            row = db.execute("""SELECT o.operation_key, o.tenant, o.member, a.body, o.body_hash
                FROM filing_operations o JOIN active_filings a USING (operation_key)
                WHERE o.status='OUTCOME_UNKNOWN' ORDER BY o.rowid LIMIT 1""").fetchone()
            if row:
                db.execute("""UPDATE filing_operations SET status='RECOVERING', recoveries=recoveries+1
                           WHERE operation_key=?""", (row[0],))
            return row

    def complete_recovery(self, key: str, receipt: dict | None, *, manual: bool = False) -> None:
        with self.transaction() as db:
            row = db.execute("""SELECT conversation, turn_id FROM filing_operations
                WHERE operation_key=? AND status='RECOVERING'""", (key,)).fetchone()
            if not row:
                return
            if receipt is None:
                db.execute("""UPDATE filing_operations SET status=CASE WHEN ? OR recoveries>=2
                    THEN 'MANUAL_REVIEW' ELSE 'OUTCOME_UNKNOWN' END WHERE operation_key=?""", (manual, key))
                return
            identifier, turn = row
            payload = json.loads(db.execute("SELECT payload FROM conversations WHERE id=?", (identifier,)).fetchone()[0])
            if payload.get("intent", {}).get("key") == key:
                payload["intent"].update(state="filed", receipt=receipt)
                payload["intent"].pop("body", None)
                db.execute("UPDATE conversations SET payload=? WHERE id=?", (json.dumps(payload), identifier))
            if turn:
                db.execute("UPDATE turns SET receipt=? WHERE conversation=? AND id=?",
                           (json.dumps(receipt), identifier, turn))
            db.execute("UPDATE filing_operations SET status='FILED' WHERE operation_key=?", (key,))
            db.execute("DELETE FROM active_filings WHERE operation_key=?", (key,))

    def audit(self, identifier):
        with self.transaction() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT audit FROM turns WHERE conversation=? AND audit IS NOT NULL ORDER BY rowid", (identifier,)
            )]
