from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable

import psutil
from PyQt6.QtCore import QThread, pyqtSignal

from tatclean.core.config import PROTECTED_PROCESSES, Settings
from tatclean.core.models import CleanupTarget, Metrics, ProcessInfo, SkipReason
from tatclean.native.processes import foreground_pid
from tatclean.native.services import SERVICE_STOPPED, ServiceManager


def running_process_names() -> tuple[frozenset[str], bool]:
    names: set[str] = set()
    complete = True
    try:
        if hasattr(psutil.process_iter, "cache_clear"):
            psutil.process_iter.cache_clear()
        for process in psutil.process_iter(("name",), ad_value=None):
            name = process.info.get("name")
            if name:
                names.add(name.casefold())
            else:
                complete = False
    except (psutil.Error, OSError):
        complete = False
    return frozenset(names), complete


class ActivityGuard:
    def __init__(
        self,
        services: ServiceManager,
        names_provider: Callable[[], tuple[frozenset[str], bool]] = running_process_names,
        refresh_interval: float = 0.75,
    ):
        self.services = services
        self.names_provider = names_provider
        self.refresh_interval = refresh_interval
        self._updated = float("-inf")
        self._names: frozenset[str] = frozenset()
        self._complete = False
        self._states: dict[str, int | None] = {}

    def refresh(self, *, force: bool = False) -> None:
        if force or time.monotonic() - self._updated >= self.refresh_interval:
            self._names, self._complete = self.names_provider()
            self._states.clear()
            self._updated = time.monotonic()

    def blocked(self, target: CleanupTarget) -> tuple[SkipReason, str] | None:
        self.refresh()
        if target.processes:
            if not self._complete:
                return SkipReason.ACTIVE_PROCESS, "Process list could not be fully verified; retry later."
            matches = sorted(set(name.casefold() for name in target.processes) & self._names)
            if matches:
                return SkipReason.ACTIVE_PROCESS, "Close " + ", ".join(matches) + " first."
        for service in target.services:
            if service not in self._states:
                try:
                    self._states[service] = self.services.state(service)
                except OSError:
                    self._states[service] = None
            if self._states[service] != SERVICE_STOPPED:
                return SkipReason.SERVICE_BUSY, f"{service} is active or unverifiable; it must already be stopped."
        return None


def process_snapshot(settings: Settings) -> tuple[ProcessInfo, ...]:
    current_user = psutil.Process().username().casefold()
    game_names = {name.casefold() for name in settings.game_executables}
    foreground = foreground_pid()
    results = []
    if hasattr(psutil.process_iter, "cache_clear"):
        psutil.process_iter.cache_clear()
    for process in psutil.process_iter(
        ("pid", "name", "username", "create_time", "memory_info", "nice"), ad_value=None
    ):
        data = process.info
        if not data.get("name") or not data.get("create_time"):
            continue
        username = data.get("username") or ""
        if username.casefold() != current_user:
            continue
        name = data["name"]
        reason = ""
        if data["pid"] == os.getpid():
            reason = "This application"
        elif name.casefold() in PROTECTED_PROCESSES or data["pid"] <= 4:
            reason = "Protected Windows process"
        memory = data.get("memory_info")
        results.append(ProcessInfo(
            pid=data["pid"], name=name, create_time=data["create_time"],
            memory=memory.rss if memory else 0, priority=int(data.get("nice") or 0x20),
            username=username, game=name.casefold() in game_names,
            foreground=data["pid"] == foreground, manageable=not reason, protected_reason=reason,
        ))
    return tuple(sorted(results, key=lambda item: (not item.game, not item.foreground, -item.memory, item.pid)))


class MetricsWorker(QThread):
    updated = pyqtSignal(object)
    unavailable = pyqtSignal(str)

    def __init__(self, drive: str, parent=None, interval: float = 2.0):
        super().__init__(parent)
        self.drive = drive
        self.interval = interval
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()
        self.requestInterruption()

    def run(self) -> None:
        psutil.cpu_percent(interval=None)
        # Warm up CPU measurement instead of presenting the first, meaningless 0% sample.
        if self._stop_event.wait(0.3):
            return
        last_error = ""
        while not self._stop_event.is_set():
            try:
                memory = psutil.virtual_memory()
                disk = psutil.disk_usage(self.drive)
                self.updated.emit(Metrics(
                    cpu_percent=psutil.cpu_percent(interval=None), memory_total=memory.total,
                    memory_used=memory.used, memory_available=memory.available,
                    drive_total=disk.total, drive_free=disk.free,
                    uptime=max(0, time.time() - psutil.boot_time()), process_count=len(psutil.pids()),
                ))
                last_error = ""
            except (psutil.Error, OSError) as exc:
                if str(exc) != last_error:
                    last_error = str(exc)
                    self.unavailable.emit(last_error)
            self._stop_event.wait(self.interval)
