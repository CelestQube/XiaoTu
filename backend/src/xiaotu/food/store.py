from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from uuid import uuid4


def canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def fingerprint(value: dict) -> str:
    return sha256(canonical(value).encode("utf-8")).hexdigest()


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection(self, *, write: bool = False):
        conn = sqlite3.connect(self.path, timeout=3, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except BaseException:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()

    def schema(self, script: str) -> None:
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(script)


class Ledger:
    def __init__(self, path: str | Path):
        self.db = Database(path)
        self.db.schema("""
            CREATE TABLE IF NOT EXISTS food_quotes (
                id TEXT PRIMARY KEY, actor TEXT NOT NULL,
                details TEXT NOT NULL, expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS food_operations (
                id TEXT PRIMARY KEY, actor TEXT NOT NULL, quote_id TEXT NOT NULL UNIQUE,
                details TEXT NOT NULL, digest TEXT NOT NULL, status TEXT NOT NULL,
                expires_at REAL NOT NULL,
                deadline_at REAL, lease_id TEXT, lease_until REAL,
                order_id TEXT
            );
            CREATE TABLE IF NOT EXISTS food_trace (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL,
                ts REAL NOT NULL, event TEXT NOT NULL, attempt INTEGER NOT NULL, code TEXT NOT NULL
            );
        """)

    def quote(self, actor: str, details: dict, expires_at: float) -> None:
        with self.db.connection(write=True) as db:
            db.execute("INSERT INTO food_quotes VALUES (?, ?, ?, ?)",
                       (details["quoteId"], actor, canonical(details), expires_at))

    def prepare(self, actor: str, quote_id: str, now: float) -> tuple[str, dict | None]:
        with self.db.connection(write=True) as db:
            operation = db.execute("SELECT * FROM food_operations WHERE actor = ? AND quote_id = ?",
                                   (actor, quote_id)).fetchone()
            if operation is not None:
                return "existing", dict(operation)
            quote = db.execute("SELECT * FROM food_quotes WHERE actor = ? AND id = ?", (actor, quote_id)).fetchone()
            if quote is None:
                return "missing", None
            if quote["expires_at"] <= now:
                return "expired", None
            operation_id = "op_" + uuid4().hex
            details = quote["details"]
            db.execute("""INSERT INTO food_operations (id, actor, quote_id, details, digest, status, expires_at)
                          VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                       (operation_id, actor, quote_id, details, fingerprint(json.loads(details)), quote["expires_at"]))
            return "created", dict(db.execute("SELECT * FROM food_operations WHERE id = ?", (operation_id,)).fetchone())

    def get(self, actor: str, operation_id: str) -> dict | None:
        with self.db.connection() as db:
            row = db.execute("SELECT * FROM food_operations WHERE actor = ? AND id = ?",
                             (actor, operation_id)).fetchone()
            return dict(row) if row is not None else None

    def by_quote(self, actor: str, quote_id: str) -> tuple[dict | None, dict | None]:
        with self.db.connection() as db:
            quote = db.execute("SELECT * FROM food_quotes WHERE actor = ? AND id = ?", (actor, quote_id)).fetchone()
            operation = db.execute("SELECT * FROM food_operations WHERE actor = ? AND quote_id = ?",
                                   (actor, quote_id)).fetchone()
            return (dict(quote) if quote is not None else None, dict(operation) if operation is not None else None)

    def claim(self, actor: str, operation_id: str, now: float, budget: float) -> tuple[dict | None, str | None]:
        with self.db.connection(write=True) as db:
            row = db.execute("SELECT * FROM food_operations WHERE actor = ? AND id = ?", (actor, operation_id)).fetchone()
            if row is None or row["status"] != "pending":
                return (dict(row) if row is not None else None, None)
            if row["expires_at"] <= now:
                db.execute("UPDATE food_operations SET status = 'rejected' WHERE id = ?", (operation_id,))
                return dict(row) | {"status": "rejected"}, None
            lease, deadline = uuid4().hex, now + budget
            db.execute("""UPDATE food_operations SET status = 'confirming', deadline_at = ?, lease_until = ?, lease_id = ?
                          WHERE id = ?""", (deadline, deadline + 0.25, lease, operation_id))
            return dict(row) | {"status": "confirming", "deadline_at": deadline,
                                "lease_until": deadline + 0.25, "lease_id": lease}, lease

    def settle(self, actor: str, operation_id: str, status: str, *,
               lease: str | None = None, order_id: str | None = None) -> dict | None:
        with self.db.connection(write=True) as db:
            if status == "placed":
                # A validated durable receipt is definitive, including during reconciliation
                # after the original application process is no longer running.
                db.execute("""UPDATE food_operations SET status = 'placed', order_id = ?
                              WHERE id = ? AND actor = ? AND status IN ('confirming', 'unknown')""",
                           (order_id, operation_id, actor))
            else:
                db.execute("""UPDATE food_operations SET status = ?
                              WHERE id = ? AND actor = ? AND status = 'confirming' AND lease_id = ?""",
                           (status, operation_id, actor, lease))
            row = db.execute("SELECT * FROM food_operations WHERE actor = ? AND id = ?", (actor, operation_id)).fetchone()
            return dict(row) if row is not None else None

    def trace(self, operation_id: str, now: float, event: str, attempt: int, code: str) -> None:
        with self.db.connection(write=True) as db:
            db.execute("INSERT INTO food_trace(operation_id, ts, event, attempt, code) VALUES (?, ?, ?, ?, ?)",
                       (operation_id, now, event, attempt, code))

    def trace_rows(self, operation_id: str) -> list[dict]:
        with self.db.connection() as db:
            return [dict(r) for r in db.execute(
                "SELECT operation_id, ts, event, attempt, code FROM food_trace WHERE operation_id = ? ORDER BY seq",
                (operation_id,),
            )]
