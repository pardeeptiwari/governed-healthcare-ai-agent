import json
import sqlite3
from contextlib import closing, contextmanager


class State:
    def __init__(self, filename):
        self.filename = filename
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS turns (
                conversation TEXT, id TEXT, fingerprint TEXT NOT NULL, receipt TEXT, audit TEXT,
                PRIMARY KEY (conversation, id))""")

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

    def finish(self, identifier, payload, turn, receipt, audit):
        with self.transaction() as db:
            db.execute("UPDATE conversations SET payload=? WHERE id=?", (json.dumps(payload), identifier))
            db.execute("UPDATE turns SET receipt=?, audit=? WHERE conversation=? AND id=?",
                       (json.dumps(receipt), json.dumps(audit), identifier, turn))

    def audit(self, identifier):
        with self.transaction() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT audit FROM turns WHERE conversation=? AND audit IS NOT NULL ORDER BY rowid", (identifier,)
            )]

