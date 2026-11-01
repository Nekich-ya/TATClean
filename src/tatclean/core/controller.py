from __future__ import annotations

import logging
import platform
import sys
import time
import winreg
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import psutil
from PyQt6.QtCore import QObject, pyqtSignal

from tatclean import __version__
from tatclean.core.actions import ACTION_SPECS, ActionService
from tatclean.core.catalog import build_catalog
from tatclean.core.cleaner import CleanupEngine
from tatclean.core.config import Settings, SettingsStore, atomic_json_write
from tatclean.core.drive_catalog import build_drive_catalog
from tatclean.core.manifest import prune_manifests
from tatclean.core.models import (
    ActionResult,
    CleanReport,
    Initialization,
    ProcessInfo,
    ScanReport,
    TaskFailure,
    TaskProgress,
    TweakSnapshot,
    format_bytes,
)
from tatclean.core.paths import Locations, PathPolicy
from tatclean.core.processes import ActivityGuard, MetricsWorker, process_snapshot
from tatclean.core.state import StateStore
from tatclean.core.tasks import TaskContext, TaskManager
from tatclean.native.files import WindowsFileDeleter
from tatclean.native.system import fixed_volumes, mounted_volumes, relaunch_elevated
from tatclean.runtime import is_packaged


@dataclass(frozen=True, slots=True)
class RuntimeBundle:
    initialization: Initialization
    engine: CleanupEngine
    actions: ActionService
    processes: tuple[ProcessInfo, ...]
    tweaks: TweakSnapshot
    backup_keys: frozenset[str]


@dataclass(frozen=True, slots=True)
class ActionBundle:
    result: ActionResult
    processes: tuple[ProcessInfo, ...] | None
    tweaks: TweakSnapshot | None
    backup_keys: frozenset[str]


def system_details() -> dict[str, str]:
    build = sys.getwindowsversion().build
    name, release = f"Windows {'11' if build >= 22000 else '10'}", ""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
            name = winreg.QueryValueEx(key, "ProductName")[0]
            if build >= 22000:
                name = name.replace("Windows 10", "Windows 11")
            try:
                release = winreg.QueryValueEx(key, "DisplayVersion")[0]
            except FileNotFoundError:
                release = ""
    except OSError:
        pass
    cpu = platform.processor() or "Unavailable"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            cpu = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except OSError:
        pass
    return {
        "os": f"{name} {release} · build {build}".strip(),
        "cpu": cpu,
        "cores": f"{psutil.cpu_count(logical=False) or '?'} physical / {psutil.cpu_count() or '?'} logical",
        "architecture": f"{platform.machine()} · {platform.architecture()[0]}",
    }


class AppController(QObject):
    initialized = pyqtSignal(object)
    scan_finished = pyqtSignal(object)
    clean_finished = pyqtSignal(object)
    action_finished = pyqtSignal(object)
    processes_updated = pyqtSignal(object, object)
    tweaks_updated = pyqtSignal(object)
    backups_updated = pyqtSignal(object)
    settings_updated = pyqtSignal(object)
    metrics_updated = pyqtSignal(object)
    failed = pyqtSignal(object)
    shutdown_ready = pyqtSignal(str)
    elevation_started = pyqtSignal()

    def __init__(
        self, locations: Locations, settings_store: SettingsStore,
        settings: Settings, state: StateStore, admin: bool, parent=None,
    ):
        super().__init__(parent)
        self.locations = locations
        self.settings_store = settings_store
        self.settings = settings
        self.state = state
        self.admin = admin
        self.tasks = TaskManager(self)
        self.tasks.failed.connect(self._task_failed)
        self.tasks.idle.connect(self._maybe_finalize)
        self.initialization: Initialization | None = None
        self.engine: CleanupEngine | None = None
        self.actions: ActionService | None = None
        self.current_scan: ScanReport | None = None
        self.metrics: MetricsWorker | None = None
        self.backup_keys: frozenset[str] = frozenset()
        self._closing = False
        self._finalizing = False
        self._final_work_done = False
        self._shutdown_warning = ""
        self._shutdown_emitted = False

    @property
    def closing(self) -> bool:
        return self._closing

    def initialize(self) -> bool:
        if self._closing:
            return False
        settings = self.settings

        def work(context: TaskContext) -> RuntimeBundle:
            context.report(TaskProgress("Discovering explicit local cache scopes…"), force=True)
            context.checkpoint()
            mounted = mounted_volumes()
            targets = build_drive_catalog(self.locations, settings, build_catalog(self.locations, settings), mounted)
            application_root = Path(__file__).resolve().parents[3]
            if is_packaged():
                application_root = Path(sys.executable).parent
            policy = PathPolicy(
                tuple(target.root for target in targets), self.locations.protected_exact(),
                (*self.locations.protected_subtrees(), application_root),
                removable_roots=tuple(target.root for target in targets if any(volume.drive == target.volume_id and volume.drive_type == "Removable" for volume in mounted)),
            )
            actions = ActionService(self.state)
            engine = CleanupEngine(targets, policy, self.locations.state / "scans",
                                   ActivityGuard(actions.services), WindowsFileDeleter(), lambda: self.admin)
            context.checkpoint()
            context.report(TaskProgress("Reading Windows, volume and process status…"), force=True)
            initialization = Initialization(targets, fixed_volumes(), system_details(), mounted)
            processes = process_snapshot(settings)
            tweaks = actions.tweak_snapshot()
            prune_manifests(self.locations.state / "scans")
            self.state.prune_audit(settings.audit_retention_days)
            context.checkpoint()
            return RuntimeBundle(initialization, engine, actions, processes, tweaks, frozenset(self.state.backups()))

        return self.tasks.start("Initializing local system overview", work, self._initialized)

    def _initialized(self, bundle: RuntimeBundle) -> None:
        self.initialization, self.engine, self.actions = bundle.initialization, bundle.engine, bundle.actions
        self.current_scan = None
        self.backup_keys = bundle.backup_keys
        self.initialized.emit(bundle.initialization)
        self.processes_updated.emit(bundle.processes, bundle.backup_keys)
        self.tweaks_updated.emit(bundle.tweaks)
        self.backups_updated.emit(bundle.backup_keys)
        if not self._closing and self.metrics is None:
            self.metrics = MetricsWorker(self.locations.windows.anchor, self)
            self.metrics.updated.connect(self.metrics_updated)
            self.metrics.unavailable.connect(lambda message: self.tasks.log_message.emit("Metrics unavailable: " + message))
            self.metrics.finished.connect(self._try_shutdown_ready)
            self.metrics.start()

    def invalidate_scan(self) -> None:
        self.current_scan = None

    def scan(self, target_ids: tuple[str, ...]) -> bool:
        if self.engine is None or self._closing:
            return False
        engine, settings = self.engine, self.settings
        self.current_scan = None
        return self.tasks.start("Scanning selected caches", lambda context: engine.scan(target_ids, settings, context), self._scanned)

    def _scanned(self, report: ScanReport) -> None:
        self.current_scan = report
        self.scan_finished.emit(report)

    def clean(self) -> bool:
        if self.engine is None or self.current_scan is None or self._closing:
            return False
        engine, scan, settings = self.engine, self.current_scan, self.settings
        def work(context: TaskContext) -> CleanReport:
            report = engine.clean(scan, settings, context)
            result = ActionResult("clean", "Clean reviewed files", not report.cancelled and not report.errors,
                                  f"Removed {report.removed_files:,} files ({format_bytes(report.removed_bytes)} logical bytes); {report.skipped_files:,} skipped.",
                                  "\n".join(report.errors), cancelled=report.cancelled, duration=report.duration)
            try:
                self.state.audit(result)
            except Exception:
                logging.getLogger("tatclean").exception("Could not save cleanup audit")
            return report
        return self.tasks.start("Cleaning reviewed files", work, self._cleaned)

    def _cleaned(self, report: CleanReport) -> None:
        self.current_scan = None
        self.clean_finished.emit(report)

    def refresh_processes(self) -> bool:
        if self.actions is None or self._closing:
            return False
        actions, settings = self.actions, self.settings

        def work(context: TaskContext):
            context.report(TaskProgress("Reading user processes and optional service states…"), force=True)
            context.checkpoint()
            return process_snapshot(settings), actions.tweak_snapshot(), frozenset(self.state.backups())

        def received(bundle):
            processes, tweaks, keys = bundle
            self.backup_keys = keys
            self.processes_updated.emit(processes, keys)
            self.tweaks_updated.emit(tweaks)
            self.backups_updated.emit(keys)

        return self.tasks.start("Refreshing processes and services", work, received)

    def perform(self, action_id: str, parameters: dict[str, Any]) -> bool:
        if self.actions is None or self._closing or action_id not in ACTION_SPECS:
            return False
        actions, settings = self.actions, self.settings

        def work(context: TaskContext) -> ActionBundle:
            result = actions.execute(action_id, parameters, settings, context)
            processes, tweaks = None, None
            try:
                # Read even after cancellation to show the actual final/rolled-back state.
                processes = process_snapshot(settings)
                tweaks = actions.tweak_snapshot()
            except Exception as exc:
                context.log(f"Could not refresh status after the action: {exc}")
            return ActionBundle(result, processes, tweaks, frozenset(self.state.backups()))

        return self.tasks.start(ACTION_SPECS[action_id].title, work, self._performed)

    def _performed(self, bundle: ActionBundle) -> None:
        self.backup_keys = bundle.backup_keys
        if bundle.processes is not None:
            self.processes_updated.emit(bundle.processes, bundle.backup_keys)
        if bundle.tweaks is not None:
            self.tweaks_updated.emit(bundle.tweaks)
        self.backups_updated.emit(bundle.backup_keys)
        self.action_finished.emit(bundle.result)

    def save_settings(self, settings: Settings) -> bool:
        if self._closing:
            return False
        validated = Settings.from_dict(asdict_settings(settings))
        validated = replace(validated, window_width=self.settings.window_width, window_height=self.settings.window_height)

        def work(context: TaskContext) -> Settings:
            context.checkpoint()
            self.settings_store.save(validated)
            self.state.prune_audit(validated.audit_retention_days)
            return validated

        def saved(value: Settings):
            self.settings = value
            self.current_scan = None
            self.settings_updated.emit(value)
            self.initialize()  # Rebuild game activity guards using the new immutable settings.

        return self.tasks.start("Saving local preferences", work, saved)

    def set_window_size(self, width: int, height: int) -> None:
        self.settings = replace(self.settings, window_width=max(1100, min(width, 7680)),
                                window_height=max(740, min(height, 4320)))

    def export_diagnostics(self, path: Path) -> bool:
        if self._closing or self.initialization is None:
            return False
        initialization, scan = self.initialization, self.current_scan

        def work(context: TaskContext) -> ActionResult:
            context.checkpoint()
            history = [{key: item.get(key) for key in (
                "created_at", "action_id", "success", "message", "reboot_required",
            )} for item in self.state.recent_actions(30)]
            payload = {
                "application": "TAT Clean", "version": __version__, "exported_at": time.time(),
                "system": initialization.system_details, "elevated": self.admin,
                "volumes": [{"drive": volume.drive, "filesystem": volume.filesystem,
                             "media": volume.media, "total": volume.total, "free": volume.free}
                            for volume in initialization.volumes],
                "scan": {"files": scan.files, "logical_bytes": scan.bytes, "completed": scan.completed} if scan else None,
                "restore_point_count": len(self.state.backups()), "recent_actions": history,
                "privacy": "No user identity, manifest file paths, registry values or raw command output are included. Review before sharing.",
            }
            reserved = (self.settings_store.path, self.state.path)
            if path in reserved or path.suffix.casefold() != ".json":
                raise ValueError("Export diagnostics to a separate .json file, not application state.")
            atomic_json_write(path, payload)
            return ActionResult("export_diagnostics", "Export diagnostics", True, f"Diagnostics saved to {path}.")

        return self.tasks.start("Exporting local diagnostics", work, self.action_finished.emit)

    def elevate(self) -> bool:
        if self.admin or self._closing:
            return False

        def work(context: TaskContext) -> None:
            context.checkpoint()
            relaunch_elevated()

        return self.tasks.start("Requesting Windows administrator approval", work, lambda _: self.elevation_started.emit())

    def cancel(self) -> None:
        if not self._finalizing:
            self.tasks.cancel()

    def request_shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.tasks.cancel()
        if self.metrics is not None:
            self.metrics.stop()
        self._maybe_finalize()

    def _maybe_finalize(self) -> None:
        if not self._closing or self.tasks.busy or self._finalizing:
            return
        self._finalizing = True
        actions, settings = self.actions, self.settings

        def finish(context: TaskContext) -> str:
            warning = ""
            if actions is not None:
                try:
                    actions.gaming.shutdown(context)
                except Exception as error:
                    warning = f"Timer request release could not finish: {error}"
            if actions is not None and settings.restore_priority_on_exit:
                result = actions.execute("restore_priorities", {}, settings, context)
                if not result.success:
                    warning += "\n" + result.message + "\n" + result.details
            try:
                self.settings_store.save(settings)
            except OSError as exc:
                warning += f"\nCould not save window preferences: {exc}"
            return warning.strip()

        def finished(warning: str) -> None:
            self._shutdown_warning = warning
            self._final_work_done = True
            self._try_shutdown_ready()

        self.tasks.start("Closing safely and restoring game priorities", finish, finished)

    def _try_shutdown_ready(self) -> None:
        if (
            self._closing and self._final_work_done and not self._shutdown_emitted
            and (self.metrics is None or not self.metrics.isRunning())
        ):
            self._shutdown_emitted = True
            self.shutdown_ready.emit(self._shutdown_warning)

    def _task_failed(self, failure: TaskFailure) -> None:
        if self._closing and self._finalizing:
            self._shutdown_warning = failure.message
            self._final_work_done = True
            self._try_shutdown_ready()
        self.failed.emit(failure)


def asdict_settings(settings: Settings) -> dict[str, Any]:
    return {
        "minimum_age_hours": settings.minimum_age_hours,
        "audit_retention_days": settings.audit_retention_days,
        "restore_priority_on_exit": settings.restore_priority_on_exit,
        "game_executables": list(settings.game_executables),
        "window_width": settings.window_width,
        "window_height": settings.window_height,
    }
