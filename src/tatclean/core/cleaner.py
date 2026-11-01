from __future__ import annotations

import json
import os
import stat
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

from tatclean.core.config import Settings
from tatclean.core.manifest import ManifestReader, ManifestWriter
from tatclean.core.models import (
    CleanReport,
    CleanupTarget,
    DeleteOutcome,
    FileRecord,
    ScanReport,
    SkipReason,
    TargetSummary,
    TaskProgress,
)
from tatclean.core.paths import (
    PROFILE_DIRECTORIES,
    PathPolicy,
    UnsafePathError,
    filesystem_path,
    is_preserved,
    is_reparse,
    lstat,
    normalize,
)
from tatclean.core.processes import ActivityGuard
from tatclean.core.tasks import CancelledError, TaskContext
from tatclean.native.files import error_reason

SCAN_LIFETIME_SECONDS = 3600


class DeletionBackend(Protocol):
    def pin_root(self, root: Path, policy: PathPolicy) -> AbstractContextManager: ...
    def pin_subdirectory(self, directory: Path, pinned) -> AbstractContextManager: ...
    def delete(self, record: FileRecord, pinned, cutoff_ns: int) -> DeleteOutcome: ...
    def hash_file(self, record: FileRecord, root: Path, policy: PathPolicy, context: TaskContext) -> str: ...
    def pin_keeper(self, record: FileRecord, root: Path, policy: PathPolicy, context: TaskContext) -> AbstractContextManager: ...


class CleanupEngine:
    def __init__(
        self,
        targets: tuple[CleanupTarget, ...],
        policy: PathPolicy,
        manifest_directory: Path,
        activity: ActivityGuard,
        deletion: DeletionBackend,
        admin_provider: Callable[[], bool],
    ):
        self.targets = {target.id: target for target in targets}
        self.policy = policy
        self.manifest_directory = manifest_directory
        self.activity = activity
        self.deletion = deletion
        self.admin_provider = admin_provider
        self._root_keys = {normalize(target.root) for target in targets}

    def _selected(self, target_ids: tuple[str, ...]) -> tuple[CleanupTarget, ...]:
        if not target_ids or len(set(target_ids)) != len(target_ids):
            raise ValueError("Select at least one unique cleanup target.")
        if any(key not in self.targets for key in target_ids):
            raise ValueError("Unknown cleanup target; reload the application.")
        selected = tuple(self.targets[key] for key in target_ids)
        for duplicate in (target for target in selected if target.duplicate_only):
            if any(not target.duplicate_only and normalize(target.root) == normalize(duplicate.root)
                   and target.matches(target.root / "example.dmp") for target in selected):
                raise ValueError("Choose either all crash dumps or duplicate-only cleanup for a given path, not both.")
        return selected

    def scan(self, target_ids: tuple[str, ...], settings: Settings, context: TaskContext) -> ScanReport:
        selected = self._selected(target_ids)
        began, created_at = time.monotonic(), time.time()
        scan_id = uuid.uuid4().hex
        path = self.manifest_directory / f"scan_{scan_id}.sqlite"
        writer = ManifestWriter(path, scan_id, target_ids, created_at)
        summaries = {target.id: TargetSummary(target.id) for target in selected}
        completed = True
        duplicate_files, duplicate_bytes = 0, 0
        context.log(f"Read-only scan started: {len(selected)} selected targets.")
        try:
            self.activity.refresh(force=True)
            # Specific targets own nested roots; a parent Temp scan cannot bypass their switches.
            for target in sorted(selected, key=lambda item: len(item.root.parts), reverse=True):
                context.checkpoint()
                summary = summaries[target.id]
                context.report(TaskProgress(f"Inspecting {target.label}…", target_id=target.id), force=True)
                if target.requires_admin and not self.admin_provider():
                    summary.status = "Administrator required"
                    summary.skip(SkipReason.PERMISSION)
                    continue
                blocked = self.activity.blocked(target)
                if blocked:
                    reason, message = blocked
                    summary.status = message
                    summary.skip(reason)
                    context.log(f"Skipped {target.label}: {message}")
                    continue
                try:
                    with self.deletion.pin_root(target.root, self.policy) as pinned:
                        self._scan_target(target, summary, settings, writer, pinned, context)
                except UnsafePathError as exc:
                    summary.skip(SkipReason.OUTSIDE_SCOPE)
                    summary.status = str(exc)
                except OSError as exc:
                    summary.skip(error_reason(exc))
                    summary.status = "Not installed / no cache" if isinstance(exc, FileNotFoundError) else str(exc)
                else:
                    if summary.status == "Not scanned":
                        summary.status = f"{summary.files:,} eligible · {sum(summary.skipped.values()):,} skipped entries"
                context.log(f"{target.label}: {summary.status}")
            writer.plan_duplicates({target.id: target for target in selected}, self.policy, self.deletion, context)
            counts = writer.counts()
            for summary in summaries.values():
                summary.files, summary.bytes = counts.get(summary.target_id, (0, 0))
                if self.targets[summary.target_id].duplicate_only:
                    summary.status = f"{summary.files:,} duplicate copies · one verified copy retained per group"
            duplicate_files, duplicate_bytes = writer.duplicate_totals()
        except CancelledError:
            completed = False
            context.log("Scan cancelled. Partial previews cannot be cleaned.")
        finally:
            try:
                digest = writer.finish(completed)
            finally:
                writer.close()
        values = tuple(summaries[target.id] for target in selected)
        report = ScanReport(
            scan_id, path, created_at, target_ids, values,
            sum(item.files for item in values), sum(item.bytes for item in values),
            digest, completed, time.monotonic() - began,
            duplicate_files, duplicate_bytes,
        )
        context.report(TaskProgress(
            "Scan complete" if completed else "Scan cancelled", 100 if completed else None,
            current=report.files, total=report.files, bytes=report.bytes,
        ), force=True)
        return report

    def _scan_target(
        self, target: CleanupTarget, summary: TargetSummary, settings: Settings,
        writer: ManifestWriter, pinned, context: TaskContext,
    ) -> None:
        cutoff_ns = time.time_ns() - max(settings.minimum_age_hours, target.minimum_age_hours) * 3600 * 10**9
        directories = [target.root]
        while directories:
            context.checkpoint()
            directory = directories.pop()
            try:
                with self.deletion.pin_subdirectory(directory, pinned), os.scandir(filesystem_path(directory)) as entries:
                    for entry in entries:
                        context.checkpoint()
                        path = directory / entry.name
                        try:
                            self.policy.validate_file(path, target.root)
                            # DirEntry.stat on Windows may return zero inode/device/link counts.
                            # lstat obtains the full identity used during native handle revalidation.
                            metadata = lstat(path)
                            if is_reparse(metadata):
                                summary.skip(SkipReason.REPARSE)
                                continue
                            if stat.S_ISDIR(metadata.st_mode):
                                if (
                                    path.name.casefold() in PROFILE_DIRECTORIES
                                    or normalize(path) in self._root_keys
                                ):
                                    summary.skip(SkipReason.FILTERED)
                                elif target.recursive:
                                    directories.append(path)
                                continue
                            summary.examined += 1
                            if not stat.S_ISREG(metadata.st_mode):
                                summary.skip(SkipReason.FILTERED)
                            elif is_preserved(path, target.root):
                                summary.skip(SkipReason.LOCK_FILE)
                            elif not target.matches(path):
                                summary.skip(SkipReason.FILTERED)
                            elif metadata.st_nlink != 1:
                                summary.skip(SkipReason.HARD_LINK)
                            elif metadata.st_mtime_ns >= cutoff_ns:
                                summary.skip(SkipReason.RECENT)
                            else:
                                record = FileRecord(
                                    str(path), target.id, metadata.st_size, metadata.st_mtime_ns,
                                    metadata.st_ino, metadata.st_dev, metadata.st_nlink,
                                )
                                if writer.add(record):
                                    summary.files += 1
                                    summary.bytes += metadata.st_size
                        except UnsafePathError:
                            summary.skip(SkipReason.OUTSIDE_SCOPE)
                        except OSError as exc:
                            summary.skip(error_reason(exc))
                        context.report(TaskProgress(
                            f"{target.label}: {summary.examined:,} files inspected",
                            current=summary.examined, bytes=summary.bytes, target_id=target.id,
                        ))
                        if summary.examined % 128 == 0:
                            blocked = self.activity.blocked(target)
                            if blocked:
                                summary.status = blocked[1]
                                summary.skip(blocked[0])
                                return
            except UnsafePathError:
                summary.skip(SkipReason.REPARSE)
            except OSError as exc:
                summary.skip(error_reason(exc))

    def clean(self, scan: ScanReport, settings: Settings, context: TaskContext) -> CleanReport:
        if not scan.completed:
            raise ValueError("An interrupted scan cannot be cleaned. Run a complete scan.")
        if not -5 <= time.time() - scan.created_at <= SCAN_LIFETIME_SECONDS:
            raise ValueError("This preview has expired. Scan again before deleting files.")
        selected = self._selected(scan.target_ids)
        report, began, processed = CleanReport(), time.monotonic(), 0
        counts = {summary.target_id: summary.files for summary in scan.summaries}
        context.log(f"Cleanup requested for {scan.files:,} scanned files. Deletion is permanent.")
        try:
            with ManifestReader(scan, self.manifest_directory) as manifest:
                manifest.verify(scan, context)
                self.activity.refresh(force=True)
                for target in selected:
                    context.checkpoint()
                    total = counts.get(target.id, 0)
                    if not total:
                        continue
                    if target.requires_admin and not self.admin_provider():
                        report.skip(SkipReason.PERMISSION, total)
                        processed += total
                        continue
                    blocked = self.activity.blocked(target)
                    if blocked:
                        report.skip(blocked[0], total)
                        context.log(f"Skipped {target.label}: {blocked[1]}")
                        processed += total
                        continue
                    cutoff_ns = time.time_ns() - max(settings.minimum_age_hours, target.minimum_age_hours) * 3600 * 10**9
                    target_removed = 0
                    target_processed = 0
                    try:
                        with self.deletion.pin_root(target.root, self.policy) as pinned:
                            for record in manifest.files(target.id):
                                context.checkpoint()
                                blocked = self.activity.blocked(target)
                                path = Path(record.path)
                                if blocked:
                                    # Release directory pins promptly if an application starts.
                                    remaining = max(0, total - target_processed)
                                    report.skip(blocked[0], remaining)
                                    processed += remaining
                                    target_processed += remaining
                                    context.log(f"Stopped cleaning {target.label}: {blocked[1]}")
                                    break
                                if not target.matches(path) or is_preserved(path, target.root) or any(
                                    normalize(parent) in self._root_keys and normalize(parent) != normalize(target.root)
                                    for parent in path.parents
                                    if parent != target.root and len(parent.parts) > len(target.root.parts)
                                ):
                                    outcome = DeleteOutcome(False, SkipReason.OUTSIDE_SCOPE)
                                else:
                                    if record.keeper_json:
                                        keeper = FileRecord(**json.loads(record.keeper_json))
                                        try:
                                            with self.deletion.pin_keeper(keeper, self.targets[keeper.target_id].root, self.policy, context):
                                                outcome = self.deletion.delete(record, pinned, cutoff_ns, context)
                                        except (OSError, ValueError) as error:
                                            outcome = DeleteOutcome(False, SkipReason.CHANGED, str(error))
                                    else:
                                        outcome = self.deletion.delete(record, pinned, cutoff_ns)
                                if outcome.removed:
                                    report.removed_files += 1
                                    target_removed += 1
                                    report.removed_bytes += record.size
                                else:
                                    report.skip(outcome.reason or SkipReason.ERROR, error=outcome.message)
                                processed += 1
                                target_processed += 1
                                context.report(TaskProgress(
                                    f"Cleaning {target.label}…", min(100, int(processed * 100 / max(1, scan.files))),
                                    current=processed, total=scan.files, bytes=report.removed_bytes, target_id=target.id,
                                ))
                    except UnsafePathError as exc:
                        remaining = max(0, total - target_processed)
                        report.skip(SkipReason.OUTSIDE_SCOPE, remaining, str(exc))
                        processed += remaining
                    except OSError as exc:
                        remaining = max(0, total - target_processed)
                        report.skip(error_reason(exc), remaining, str(exc))
                        processed += remaining
                    context.log(f"{target.label}: {target_removed:,} files removed.")
        except CancelledError:
            report.cancelled = True
            context.log("Cleanup cancelled. Files already removed cannot be restored.")
        report.duration = time.monotonic() - began
        context.report(TaskProgress(
            "Cleanup cancelled" if report.cancelled else "Cleanup complete",
            min(100, int(processed * 100 / max(1, scan.files))) if report.cancelled else 100,
            current=processed, total=scan.files, bytes=report.removed_bytes,
        ), force=True)
        return report
