from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import asdict, replace
from pathlib import Path

from tatclean.core.models import FileRecord, ScanReport, TaskProgress
from tatclean.core.paths import UnsafePathError, is_reparse, is_within, normalize
from tatclean.core.tasks import TaskContext


def record_bytes(record: FileRecord) -> bytes:
    return (json.dumps(
        [record.path, record.target_id, record.size, record.mtime_ns, str(record.inode),
         str(record.device), record.nlinks, record.content_hash, record.keeper_json], ensure_ascii=False, separators=(",", ":")
    ) + "\n").encode("utf-8")


class ManifestWriter:
    def __init__(self, path: Path, scan_id: str, target_ids: tuple[str, ...], created_at: float):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb"):
            pass
        self.database = sqlite3.connect(path)
        self.database.executescript(
            """
            PRAGMA trusted_schema = OFF;
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE files(
                sequence INTEGER PRIMARY KEY,
                path_key TEXT NOT NULL UNIQUE,
                path TEXT NOT NULL,
                target_id TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns TEXT NOT NULL,
                inode TEXT NOT NULL,
                device TEXT NOT NULL,
                nlinks INTEGER NOT NULL,
                content_hash TEXT NOT NULL DEFAULT '',
                keeper_json TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX target_files ON files(target_id, sequence);
            CREATE TABLE keepers(hash TEXT NOT NULL,size INTEGER NOT NULL,record TEXT NOT NULL,PRIMARY KEY(hash,size));
            PRAGMA user_version = 2;
            """
        )
        self.database.executemany("INSERT INTO meta VALUES(?,?)", (
            ("scan_id", scan_id), ("targets", json.dumps(target_ids)),
            ("created_at", repr(created_at)), ("completed", "0"),
        ))
        self._digest = hashlib.sha256()
        self._pending = 0

    def add(self, record: FileRecord) -> bool:
        cursor = self.database.execute(
            "INSERT OR IGNORE INTO files(path_key,path,target_id,size,mtime_ns,inode,device,nlinks) VALUES(?,?,?,?,?,?,?,?)",
            (normalize(record.path).casefold(), record.path, record.target_id, record.size,
             str(record.mtime_ns), str(record.inode), str(record.device), record.nlinks),
        )
        if cursor.rowcount:
            self._digest.update(record_bytes(record))
            self._pending += 1
            if self._pending >= 1000:
                self.database.commit()
                self._pending = 0
            return True
        return False

    def finish(self, completed: bool) -> str:
        # Duplicate-only planning can remove keepers/unique dumps and add content evidence.
        digest_builder = hashlib.sha256()
        for row in self.database.execute("SELECT path,target_id,size,mtime_ns,inode,device,nlinks,content_hash,keeper_json FROM files ORDER BY sequence"):
            path, target, size, mtime, inode, device, links, content_hash, keeper_json = row
            digest_builder.update(record_bytes(FileRecord(path, target, size, int(mtime), int(inode), int(device), links, content_hash, keeper_json)))
        digest = digest_builder.hexdigest()
        self.database.execute("UPDATE meta SET value=? WHERE key='completed'", ("1" if completed else "0",))
        self.database.execute("INSERT INTO meta VALUES('digest',?)", (digest,))
        self.database.commit()
        return digest

    def plan_duplicates(self, targets: dict, policy, backend, context: TaskContext) -> None:
        ids = tuple(key for key, target in targets.items() if target.duplicate_only)
        if not ids:
            return
        placeholders = ",".join("?" for _key in ids)
        last = 0
        while True:
            rows = self.database.execute(
                f"SELECT sequence,path,target_id,size,mtime_ns,inode,device,nlinks FROM files WHERE target_id IN ({placeholders}) AND sequence>? ORDER BY sequence LIMIT 128",
                (*ids, last),
            ).fetchall()
            if not rows:
                break
            for sequence, path, target_id, size, mtime, inode, device, links in rows:
                context.checkpoint()
                last = sequence
                record = FileRecord(path, target_id, size, int(mtime), int(inode), int(device), links)
                try:
                    content_hash = backend.hash_file(record, targets[target_id].root, policy, context)
                except (OSError, ValueError) as error:
                    context.log(f"Preserved unverified dump {Path(path).name}: {error}")
                    self.database.execute("DELETE FROM files WHERE sequence=?", (sequence,))
                    continue
                keeper = self.database.execute("SELECT record FROM keepers WHERE hash=? AND size=?", (content_hash, size)).fetchone()
                if keeper is None:
                    kept = replace(record, content_hash=content_hash)
                    self.database.execute("INSERT INTO keepers VALUES(?,?,?)", (content_hash, size, json.dumps(asdict(kept), separators=(",", ":"))))
                    self.database.execute("DELETE FROM files WHERE sequence=?", (sequence,))
                else:
                    self.database.execute("UPDATE files SET content_hash=?,keeper_json=? WHERE sequence=?", (content_hash, keeper[0], sequence))
            self.database.commit()

    def counts(self) -> dict[str, tuple[int, int]]:
        return {key: (count, size) for key, count, size in self.database.execute("SELECT target_id,count(*),coalesce(sum(size),0) FROM files GROUP BY target_id")}

    def duplicate_totals(self) -> tuple[int, int]:
        return self.database.execute("SELECT count(*),coalesce(sum(size),0) FROM files WHERE keeper_json<>''").fetchone()

    def close(self) -> None:
        self.database.close()


class ManifestReader:
    def __init__(self, report: ScanReport, directory: Path):
        path = report.manifest_path
        if (
            not is_within(path, directory, include_root=False)
            or path.parent != directory
            or path.name != f"scan_{report.scan_id}.sqlite"
            or not re.fullmatch(r"[a-f0-9]{32}", report.scan_id)
        ):
            raise UnsafePathError("Manifest is outside the application's scan directory.")
        if is_reparse(path.lstat()):
            raise UnsafePathError("Linked scan manifests are not allowed.")
        self.database = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
        try:
            self.database.execute("PRAGMA trusted_schema = OFF")
            self.database.execute("PRAGMA query_only = ON")
            self.database.execute("BEGIN")  # Pin one immutable SQLite read snapshot until cleanup ends.
            metadata = dict(self.database.execute("SELECT key,value FROM meta"))
            if (
                self.database.execute("PRAGMA user_version").fetchone()[0] != 2
                or metadata.get("scan_id") != report.scan_id
                or metadata.get("completed") != "1"
                or tuple(json.loads(metadata.get("targets", "[]"))) != report.target_ids
                or float(metadata.get("created_at", "0")) != report.created_at
                or metadata.get("digest") != report.digest
            ):
                raise ValueError("The scan manifest is incomplete or has changed. Scan again.")
        except BaseException:
            self.database.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.database.close()

    def files(self, target_id: str | None = None) -> Iterator[FileRecord]:
        columns = "path,target_id,size,mtime_ns,inode,device,nlinks,content_hash,keeper_json"
        cursor = self.database.execute(
            f"SELECT {columns} FROM files ORDER BY sequence" if target_id is None else
            f"SELECT {columns} FROM files WHERE target_id=? ORDER BY sequence",
            () if target_id is None else (target_id,),
        )
        for path, target, size, mtime, inode, device, nlinks, content_hash, keeper_json in cursor:
            if not isinstance(path, str) or not isinstance(target, str) or size < 0 or nlinks < 1:
                raise ValueError("Invalid file record in scan manifest.")
            yield FileRecord(path, target, size, int(mtime), int(inode), int(device), nlinks, content_hash, keeper_json)

    def verify(self, report: ScanReport, context: TaskContext) -> None:
        digest, count, size = hashlib.sha256(), 0, 0
        for record in self.files():
            context.checkpoint()
            if record.target_id not in report.target_ids:
                raise ValueError("Manifest contains an unselected cleanup target.")
            if record.keeper_json:
                keeper = FileRecord(**json.loads(record.keeper_json))
                if keeper.target_id not in report.target_ids or keeper.content_hash != record.content_hash or not re.fullmatch(r"[a-f0-9]{64}", record.content_hash):
                    raise ValueError("Duplicate-keeper evidence is invalid.")
            digest.update(record_bytes(record))
            count += 1
            size += record.size
            context.report(TaskProgress("Verifying scan integrity…", current=count, total=report.files))
        if digest.hexdigest() != report.digest or count != report.files or size != report.bytes:
            raise ValueError("Scan contents changed after preview. No files were deleted; scan again.")


def prune_manifests(directory: Path, *, keep: Path | None = None, days: int = 2) -> None:
    """Prune only this app's UUID-named, old manifests; never recurse."""
    if not directory.exists():
        return
    cutoff = time.time() - days * 86400
    with os.scandir(directory) as entries:
        for entry in entries:
            path = Path(entry.path)
            if path == keep or not re.fullmatch(r"scan_[a-f0-9]{32}\.sqlite", entry.name):
                continue
            try:
                metadata = path.lstat()
                if not is_reparse(metadata) and entry.is_file(follow_symlinks=False) and metadata.st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue
