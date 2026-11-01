from __future__ import annotations

import json
import logging
import logging.handlers
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tatclean.core.models import ActionResult


class StateStore:
    """Short-lived connections allow safe use from independent worker threads."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            probe = sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True)
            try:
                version = probe.execute("PRAGMA user_version").fetchone()[0]
                tables = {name for (name,) in probe.execute("SELECT name FROM sqlite_master WHERE type='table'") if not name.startswith("sqlite_")}
                if version not in (0, 1) or not tables <= {"backups", "audit"}:
                    raise RuntimeError("Unrecognized state database format; it was preserved without modification.")
            finally:
                probe.close()
        with self.connection() as database:
            database.executescript(
                """
                CREATE TABLE IF NOT EXISTS backups (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY,
                    created_at REAL NOT NULL,
                    action_id TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS audit_time ON audit(created_at);
                PRAGMA user_version = 1;
                """
            )

    @contextmanager
    def connection(self):
        database = sqlite3.connect(self.path, timeout=10)
        try:
            database.execute("PRAGMA busy_timeout = 10000")
            database.execute("PRAGMA trusted_schema = OFF")
            database.execute("PRAGMA journal_mode = WAL")
            database.execute("PRAGMA synchronous = FULL")
            with database:
                yield database
        finally:
            database.close()

    def save_original(self, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Never replace the first baseline with an already-modified state."""
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self.connection() as database:
            database.execute(
                "INSERT OR IGNORE INTO backups(key,payload,created_at) VALUES (?,?,?)",
                (key, encoded, time.time()),
            )
            row = database.execute("SELECT payload FROM backups WHERE key=?", (key,)).fetchone()
        return json.loads(row[0])

    def backup(self, key: str) -> dict[str, Any] | None:
        with self.connection() as database:
            row = database.execute("SELECT payload FROM backups WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def backups(self, prefix: str = "") -> dict[str, dict[str, Any]]:
        with self.connection() as database:
            rows = database.execute("SELECT key,payload FROM backups ORDER BY created_at").fetchall()
        return {key: json.loads(payload) for key, payload in rows if key.startswith(prefix)}

    def remove_backup(self, key: str) -> None:
        with self.connection() as database:
            database.execute("DELETE FROM backups WHERE key=?", (key,))

    def update_gaming_backup(self, key: str, payload: dict[str, Any]) -> None:
        """Update an application journal without ever replacing its first baseline."""
        if not key.startswith("gaming:"):
            raise ValueError("Only gaming journals can be updated through this method.")
        with self.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute("SELECT payload FROM backups WHERE key=?", (key,)).fetchone()
            if not row:
                raise ValueError("A durable original must be saved before updating its journal.")
            current = json.loads(row[0])
            for field in ("owner", "kind", "original"):
                if current.get(field) != payload.get(field):
                    raise ValueError(f"The immutable gaming baseline field {field!r} cannot change.")
            database.execute("UPDATE backups SET payload=? WHERE key=?",
                             (json.dumps(payload, ensure_ascii=False), key))

    def audit(self, result: ActionResult) -> None:
        with self.connection() as database:
            database.execute(
                "INSERT INTO audit(created_at,action_id,success,payload) VALUES (?,?,?,?)",
                (time.time(), result.action_id, int(result.success), json.dumps(asdict(result))),
            )

    def recent_actions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connection() as database:
            rows = database.execute(
                "SELECT created_at,payload FROM audit ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [{"created_at": timestamp, **json.loads(payload)} for timestamp, payload in rows]

    def prune_audit(self, days: int) -> None:
        with self.connection() as database:
            database.execute("DELETE FROM audit WHERE created_at < ?", (time.time() - days * 86400,))


def configure_logging(directory: Path) -> logging.Logger:
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("tatclean")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            directory / "tatclean.log", maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger
