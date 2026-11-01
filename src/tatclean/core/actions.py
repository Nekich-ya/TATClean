from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import tempfile
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psutil

from tatclean.core.config import Settings
from tatclean.core.gaming import GamingService
from tatclean.core.gaming_models import GamingRequest
from tatclean.core.models import (
    ActionResult,
    ProcessInfo,
    ServiceInfo,
    TaskProgress,
    TweakSnapshot,
    format_bytes,
)
from tatclean.core.paths import filesystem_path, validate_ancestry
from tatclean.core.state import StateStore
from tatclean.core.tasks import CancelledError, TaskContext
from tatclean.native.processes import HIGH_PRIORITY_CLASS, ProcessManager
from tatclean.native.registry import RecordingRegistry
from tatclean.native.services import (
    SERVICE_DEFINITIONS,
    SERVICE_DISABLED,
    SERVICE_RUNNING,
    SERVICE_STOPPED,
    STATE_NAMES,
    ServiceManager,
)
from tatclean.native.system import (
    current_user_sid,
    empty_recycle_bin,
    is_admin,
    system_directory,
    validate_fixed_drive,
)


@dataclass(frozen=True, slots=True)
class ActionSpec:
    id: str
    title: str
    description: str
    warning: str
    requires_admin: bool = False
    irreversible: bool = False


ACTION_SPECS = {
    "flush_dns": ActionSpec("flush_dns", "Flush DNS cache", "Clear cached DNS resolver entries.",
                            "Addresses will be resolved again. This can help stale DNS entries, but does not increase internet bandwidth."),
    "clear_arp": ActionSpec("clear_arp", "Clear ARP cache", "Rebuild cached local network neighbor entries.",
                            "Administrator access required. Local network connections may briefly pause while neighbors are rediscovered.", True),
    "reset_winsock": ActionSpec("reset_winsock", "Reset Winsock catalog", "Repair the Windows socket provider catalog.",
                                "Administrator access and a reboot are required. VPNs, security providers or custom network software may need repair. Use for troubleshooting, not routine optimization.", True, True),
    "empty_recycle": ActionSpec("empty_recycle", "Empty Recycle Bin", "Permanently remove this user's recycled items on all drives.",
                               "This permanently removes ALL items in your Recycle Bin, including documents you may want to recover. It cannot be undone by TAT Clean.", False, True),
    "optimize_volume": ActionSpec("optimize_volume", "Optimize selected drive", "Let Windows choose TRIM/retrim or defragmentation for the selected drive.",
                                  "Runs defrag /O /U /V on one fixed local drive. Windows selects the appropriate operation; this is not a forced SSD defragmentation. Disk activity can be substantial. Do not interrupt power.", True),
    "disable_recording": ActionSpec("disable_recording", "Disable background game capture", "Disable the per-user Game DVR background recording flag.",
                                    "A verified System Restore point requires administrator access. Background 'Record what happened' clips stop; manual capture and Game Bar are untouched. The original per-user flag is saved; restart affected games/apps to apply.", True),
    "restore_recording": ActionSpec("restore_recording", "Restore game capture settings", "Restore the saved Game DVR baseline.",
                                    "Only the background-recording value changed by TAT Clean will be restored. Manual capture and unrelated registry settings are not touched."),
    "disable_service": ActionSpec("disable_service", "Disable selected service", "Save the service baseline, stop it and disable startup.",
                                  "Administrator access and a verified System Restore point are required. Read the service consequences carefully. Startup type, delayed-start flag and running state are saved first.", True),
    "restore_service": ActionSpec("restore_service", "Restore selected service", "Restore this service's original configuration and running state.",
                                  "Administrator access required. Restoration will not silently overwrite a conflicting startup-type change made outside TAT Clean.", True),
    "trim_memory": ActionSpec("trim_memory", "Trim selected working sets", "Request a one-time working-set reduction for selected user processes.",
                              "This is temporary, not a cure for memory pressure. Trimmed pages may be paged back in and cause stutters. Windows normally manages RAM better without repeated forced trimming."),
    "enable_priority": ActionSpec("enable_priority", "Enable game high priority", "Use HIGH priority for one recognized running game.",
                                  "High priority can starve background work and may worsen responsiveness. Real-time priority is never used. The original priority is saved."),
    "restore_priority": ActionSpec("restore_priority", "Restore game priority", "Restore this game's saved priority.",
                                   "The process identity and owner are checked again. Reused PIDs are never modified."),
    "restore_all": ActionSpec("restore_all", "Restore all saved changes", "Restore saved recording, service and live-process priority baselines.",
                              "Administrator access is needed for saved service changes. Deleted cache files, the Recycle Bin and network resets cannot be restored. Conflicting external changes are reported rather than overwritten."),
    "restore_priorities": ActionSpec("restore_priorities", "Restore saved game priorities", "Restore original priorities of games changed by this application.",
                                     "Only live processes with matching identity, owner and session can be changed."),
    "gaming_apply": ActionSpec("gaming_apply", "Optimize selected gaming settings", "Apply only the selected gaming/latency tweaks.",
                               "A verified Windows System Restore point and durable original-state backups are required before changes. Experimental settings can worsen frame pacing, compatibility, network overhead or power use. Failed batches attempt reverse-order rollback; conflicting baselines are retained.", True),
    "gaming_restore": ActionSpec("gaming_restore", "Revert saved gaming settings", "Restore only this application's saved gaming baselines.",
                                 "System registry/power changes need administrator rights. External conflicts are not overwritten. A restore point does not recover deleted cache files. Reboot may be required."),
    "release_timer": ActionSpec("release_timer", "Release timer resolution request", "Release only this application's timer request.",
                                "Other processes can keep a finer timer resolution active. Their requests are not released or modified."),
    "create_restore_point": ActionSpec("create_restore_point", "Create System Restore point", "Create and verify a new Windows System Restore point.",
                                       "System Protection must already be enabled. Windows can enforce a 24-hour creation quota. The application does not bypass or change that policy.", True),
}


@dataclass(frozen=True, slots=True)
class CommandOutput:
    returncode: int
    text: str
    duration: float


class CommandRunner:
    """No shell, no PATH lookup, bounded output, timeouts and cooperative cancellation."""

    def __init__(self, output_directory: Path | None = None):
        self.output_directory = output_directory

    def run(self, arguments: tuple[str, ...], context: TaskContext, timeout: float = 60) -> CommandOutput:
        if not arguments or not Path(arguments[0]).is_absolute():
            raise ValueError("Native commands must use an absolute executable path.")
        context.checkpoint()
        if self.output_directory is not None:
            validate_ancestry(self.output_directory.parent)
            with suppress(FileExistsError):
                os.mkdir(filesystem_path(self.output_directory))
            validate_ancestry(self.output_directory)
        began = time.monotonic()
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with tempfile.TemporaryFile(mode="w+b", dir=self.output_directory) as output:
            process = subprocess.Popen(
                arguments, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                shell=False, creationflags=flags, close_fds=True,
            )
            try:
                while process.poll() is None:
                    context.checkpoint()
                    elapsed = time.monotonic() - began
                    if elapsed > timeout:
                        raise TimeoutError(f"Native command exceeded {timeout:g} seconds.")
                    context.report(TaskProgress(f"Running {Path(arguments[0]).name} · {int(elapsed)}s elapsed"))
                    context.cancel_event.wait(0.15)
            except BaseException:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                raise
            output.flush()
            length = output.seek(0, os.SEEK_END)
            output.seek(max(0, length - 65536))
            raw = output.read(65536)
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = raw.decode("utf-16", errors="replace")
        elif raw.startswith(b"\xef\xbb\xbf"):
            text = raw.decode("utf-8-sig", errors="replace")
        elif raw and raw.count(b"\x00") > len(raw) // 8:
            text = raw.decode("utf-16-le", errors="replace")
        else:
            code_page = ctypes.windll.kernel32.GetOEMCP() if os.name == "nt" else 65001
            text = raw.decode(f"cp{code_page}" if code_page != 65001 else "utf-8", errors="replace")
        if length > 65536:
            text = "[Output truncated to the last 64 KB]\n" + text
        return CommandOutput(process.returncode, text.strip(), time.monotonic() - began)


class ActionService:
    def __init__(
        self, state: StateStore, services: ServiceManager | None = None,
        registry: RecordingRegistry | None = None, processes: ProcessManager | None = None,
        runner: CommandRunner | None = None,
        gaming: GamingService | None = None,
    ):
        self.state = state
        self.services = services or ServiceManager()
        self.registry = registry or RecordingRegistry()
        self.processes = processes or ProcessManager()
        self.runner = runner or CommandRunner(state.path.parent / "command-output")
        self.user_sid = current_user_sid()
        self.system_directory = system_directory()
        self.gaming = gaming or GamingService(state, self.runner, owner=self.user_sid)

    def execute(
        self, action_id: str, parameters: dict[str, Any], settings: Settings, context: TaskContext,
    ) -> ActionResult:
        if action_id not in ACTION_SPECS:
            raise ValueError("Unknown maintenance action.")
        spec, began = ACTION_SPECS[action_id], time.monotonic()
        context.log(f"Requested: {spec.title}.")
        try:
            context.checkpoint()
            if spec.requires_admin and not is_admin():
                raise PermissionError("Restart TAT Clean as administrator to perform this action.")
            result = self._dispatch(action_id, parameters, settings, context)
        except CancelledError as exc:
            result = ActionResult(action_id, spec.title, False, str(exc), cancelled=True)
        except Exception as exc:
            logging.getLogger("tatclean").exception("Maintenance action failed: %s", action_id)
            result = ActionResult(action_id, spec.title, False, str(exc) or type(exc).__name__)
        result = ActionResult(
            result.action_id, result.title, result.success, result.message, result.details,
            result.reboot_required, result.cancelled, time.monotonic() - began,
        )
        # An audit write failure must not disguise the fact a native operation already ran.
        try:
            self.state.audit(result)
        except Exception:
            logging.getLogger("tatclean").exception("Could not persist action audit")
            context.log("Warning: action completed but its audit record could not be saved.")
        context.log(f"{'Completed' if result.success else 'Not completed'}: {result.message}")
        if result.details:
            context.log(result.details)
        return result

    def _dispatch(self, action: str, parameters: dict[str, Any], settings: Settings, context: TaskContext) -> ActionResult:
        spec = ACTION_SPECS[action]
        if action in ("gaming_apply", "gaming_restore", "release_timer", "create_restore_point"):
            if action == "gaming_apply":
                request = parameters.get("request")
                if not isinstance(request, GamingRequest):
                    raise ValueError("A validated selection of gaming settings is required.")
                outcome = self.gaming.apply(request, context)
            elif action == "gaming_restore":
                outcome = self.gaming.restore(context, str(parameters.get("feature", "")))
            elif action == "release_timer":
                outcome = self.gaming.release_timer(context)
            else:
                point = self.gaming.checkpoints.ensure(context, force_new=True)
                return ActionResult(action, spec.title, True, f"Verified System Restore point #{point.sequence}.")
            return ActionResult(action, spec.title, outcome.success, outcome.message, outcome.details,
                                reboot_required=outcome.reboot_required, cancelled=outcome.cancelled)
        if action in ("flush_dns", "clear_arp", "reset_winsock", "optimize_volume"):
            commands = {
                "flush_dns": ("ipconfig.exe", "/flushdns"),
                "clear_arp": ("netsh.exe", "interface", "ip", "delete", "arpcache"),
                "reset_winsock": ("netsh.exe", "winsock", "reset"),
            }
            if action == "optimize_volume":
                drive = validate_fixed_drive(str(parameters.get("drive", "")))
                command = ("defrag.exe", drive, "/O", "/U", "/V")
            else:
                command = commands[action]
            output = self.runner.run(
                (str(self.system_directory / command[0]), *command[1:]), context,
                timeout=7200 if action == "optimize_volume" else 60,
            )
            success = output.returncode == 0
            message = f"{spec.title} completed." if success else f"Windows command failed (exit code {output.returncode}). See the log."
            return ActionResult(action, spec.title, success, message, output.text,
                                reboot_required=success and action == "reset_winsock")
        if action == "empty_recycle":
            context.report(TaskProgress("Emptying Recycle Bin… OS operation cannot be interrupted safely."), force=True)
            items, size = empty_recycle_bin()
            return ActionResult(action, spec.title, True, f"Removed {items:,} recycled items ({format_bytes(size)} estimated).")
        if action == "disable_recording":
            message = self._disable_recording(context)
        elif action == "restore_recording":
            message = self._restore_recording(context)
        elif action in ("disable_service", "restore_service"):
            name = str(parameters.get("service", ""))
            if name not in SERVICE_DEFINITIONS:
                raise ValueError("Only the listed optional services can be changed.")
            message = self._disable_service(name, context) if action == "disable_service" else self._restore_service(name, context)
        elif action == "trim_memory":
            selected = parameters.get("processes", ())
            if not isinstance(selected, tuple) or not 1 <= len(selected) <= 100:
                raise ValueError("Select between 1 and 100 user processes.")
            trimmed, errors = 0, []
            for index, process in enumerate(selected):
                context.checkpoint()
                if not isinstance(process, ProcessInfo):
                    raise ValueError("Invalid process selection.")
                try:
                    self.processes.trim(process)
                    trimmed += 1
                except (OSError, ValueError) as exc:
                    errors.append(f"{process.name} ({process.pid}): {exc}")
                context.report(TaskProgress("Trimming selected working sets…", int((index + 1) * 100 / len(selected))), force=True)
            return ActionResult(action, spec.title, not errors,
                                f"Trimmed {trimmed}/{len(selected)} working sets. Savings are temporary.", "\n".join(errors))
        elif action in ("enable_priority", "restore_priority"):
            process = parameters.get("process")
            if not isinstance(process, ProcessInfo):
                raise ValueError("Select one running game.")
            if process.name.casefold() not in {name.casefold() for name in settings.game_executables}:
                raise ValueError("Add this game's executable to Settings before changing its priority.")
            message = self._enable_priority(process, context) if action == "enable_priority" else self._restore_priority(self.priority_key(process), context)
        elif action in ("restore_all", "restore_priorities"):
            return self._restore_many(action, settings, context)
        else:
            raise ValueError("Unsupported action.")
        return ActionResult(action, spec.title, True, message)

    def _check_backup_owner(self, backup: dict[str, Any]) -> None:
        if backup.get("owner") != self.user_sid:
            raise PermissionError("This restore point belongs to a different Windows user.")

    def _disable_recording(self, context: TaskContext) -> str:
        current = self.registry.read()
        if self.registry.is_disabled(current):
            return "Game DVR is already disabled; existing baselines were left unchanged."
        if self.state.backup("recording"):
            raise ValueError("A Game DVR baseline exists. Restore it before applying another change.")
        self.gaming.checkpoints.ensure(context)
        if self.registry.read() != current:
            raise ValueError("Recording settings changed during checkpoint creation; no setting was overwritten.")
        baseline = self.state.save_original("recording", {"owner": self.user_sid, "entries": current})
        mutation_started = False
        try:
            context.checkpoint()
            mutation_started = True
            self.registry.disable()
        except Exception:
            if not mutation_started:
                self.state.remove_backup("recording")
                raise
            try:
                now = self.registry.read()
                for value, original in zip(now, baseline["entries"], strict=True):
                    if value != original and not self.registry.is_disabled([value]):
                        raise ValueError("Recording changed externally during rollback; baseline retained.")
                self.registry.restore(baseline["entries"])
                self.state.remove_backup("recording")
            except Exception as rollback:
                raise RuntimeError(f"Game DVR change and rollback failed; the restore point is retained: {rollback}") from rollback
            raise
        return "Background game capture disabled. Original registry values saved; restart affected apps."

    def _restore_recording(self, context: TaskContext) -> str:
        baseline = self.state.backup("recording")
        if not baseline:
            return "No Game DVR change made by TAT Clean needs restoration."
        self._check_backup_owner(baseline)
        current = self.registry.read()
        for now, original in zip(current, baseline["entries"], strict=True):
            if now != original and not self.registry.is_disabled([now]):
                raise ValueError("Game DVR was changed outside TAT Clean. Refusing to overwrite the conflicting values.")
        context.checkpoint()
        self.registry.restore(baseline["entries"])
        self.state.remove_backup("recording")
        return "Original Game DVR values restored. Restart affected apps to apply."

    def _disable_service(self, name: str, context: TaskContext) -> str:
        key = "service:" + name
        current = self.services.snapshot(name)
        if current.start_type == SERVICE_DISABLED:
            return f"{name} is already disabled; no new baseline was created."
        if current.state not in (SERVICE_STOPPED, SERVICE_RUNNING):
            raise ValueError("Wait for the service to finish its transition before changing it.")
        if self.state.backup(key):
            raise ValueError("A baseline already exists for this service. Restore it first.")
        self.gaming.checkpoints.ensure(context)
        if self.services.snapshot(name) != current:
            raise ValueError("Service state changed during checkpoint creation; retry after reviewing its status.")
        baseline = self.state.save_original(key, {"owner": self.user_sid, **asdict(current)})
        mutation_started = False
        try:
            context.checkpoint()
            mutation_started = True
            self.services.set_start_type(name, SERVICE_DISABLED)
            self.services.stop(name, context)
        except Exception:
            if not mutation_started:
                self.state.remove_backup(key)
                raise
            try:
                now = self.services.snapshot(name)
                if now.start_type not in (SERVICE_DISABLED, baseline["start_type"]):
                    raise ValueError("Service startup changed externally during rollback; baseline retained.")
                self._apply_service_baseline(name, baseline, TaskContext())
                self.state.remove_backup(key)
            except Exception as rollback:
                raise RuntimeError(f"Service change and rollback failed; saved baseline retained: {rollback}") from rollback
            raise
        return f"{name} stopped and disabled. Original startup and running state saved."

    def _apply_service_baseline(self, name: str, baseline: dict[str, Any], context: TaskContext) -> None:
        self._check_backup_owner(baseline)
        if baseline.get("name") != name or baseline.get("state") not in (SERVICE_STOPPED, SERVICE_RUNNING):
            raise ValueError("Invalid service restore point.")
        self.services.set_start_type(name, int(baseline["start_type"]), bool(baseline["delayed"]))
        if baseline["state"] == SERVICE_RUNNING:
            self.services.start(name, context)
        else:
            self.services.stop(name, context)

    def _restore_service(self, name: str, context: TaskContext) -> str:
        key = "service:" + name
        baseline = self.state.backup(key)
        if not baseline:
            return f"No saved change for {name}."
        self._check_backup_owner(baseline)
        current = self.services.snapshot(name)
        if current.start_type not in (SERVICE_DISABLED, baseline["start_type"]):
            raise ValueError(f"{name}'s startup type was changed outside TAT Clean. The baseline is retained for review.")
        context.checkpoint()
        self._apply_service_baseline(name, baseline, context)
        self.state.remove_backup(key)
        return f"{name}'s original startup, delayed-start flag and running state restored."

    @staticmethod
    def priority_key(process: ProcessInfo) -> str:
        return f"priority:{process.pid}:{int(process.create_time * 10**7)}"

    def _enable_priority(self, process: ProcessInfo, context: TaskContext) -> str:
        key = self.priority_key(process)
        current = self.processes.priority(process)
        if current == HIGH_PRIORITY_CLASS:
            return "The game is already using high priority; no new baseline was created."
        if self.state.backup(key):
            raise ValueError("This game's priority changed outside TAT Clean. Restore its saved baseline first.")
        self.state.save_original(key, {"owner": self.user_sid, "process": asdict(process), "priority": current})
        try:
            context.checkpoint()
            self.processes.change_priority(process, HIGH_PRIORITY_CLASS, expected=current)
        except Exception:
            self.state.remove_backup(key)
            raise
        return f"{process.name} is now HIGH priority. The original priority is saved."

    def _restore_priority(self, key: str, context: TaskContext) -> str:
        baseline = self.state.backup(key)
        if not baseline:
            return "No saved priority change for this game."
        self._check_backup_owner(baseline)
        process = ProcessInfo(**baseline["process"])
        if not re.fullmatch(r"priority:\d+:\d+", key) or self.priority_key(process) != key:
            raise ValueError("Invalid priority restore point.")
        try:
            live = psutil.Process(process.pid)
            same = abs(live.create_time() - process.create_time) <= 0.002
        except psutil.NoSuchProcess:
            same = False
        if not same:
            self.state.remove_backup(key)
            return f"{process.name} exited; obsolete priority baseline removed without touching any process."
        current = self.processes.priority(process)
        if current not in (HIGH_PRIORITY_CLASS, baseline["priority"]):
            raise ValueError("Priority was changed outside TAT Clean; saved baseline retained.")
        context.checkpoint()
        self.processes.change_priority(process, int(baseline["priority"]), expected=current)
        self.state.remove_backup(key)
        return f"{process.name}'s original priority restored."

    def _restore_many(self, action: str, settings: Settings, context: TaskContext) -> ActionResult:
        backups = self.state.backups("priority:" if action == "restore_priorities" else "")
        backups = {key: value for key, value in backups.items() if not key.startswith("gaming:")}
        failures, restored = [], 0
        for index, key in enumerate(backups):
            context.checkpoint()
            try:
                if key == "recording":
                    message = self._restore_recording(context)
                elif key.startswith("service:") and key[8:] in SERVICE_DEFINITIONS:
                    if not is_admin():
                        raise PermissionError("Administrator access required for " + key[8:])
                    message = self._restore_service(key[8:], context)
                elif key.startswith("priority:"):
                    message = self._restore_priority(key, context)
                else:
                    raise ValueError("Unrecognized restore point retained: " + key)
                restored += 1
                context.log(message)
            except CancelledError:
                raise
            except Exception as exc:
                failures.append(f"{key}: {exc}")
            context.report(TaskProgress("Restoring saved changes…", int((index + 1) * 100 / max(1, len(backups)))), force=True)
        reboot = False
        gaming_message = ""
        if action == "restore_all":
            gaming_result = self.gaming.restore(context)
            reboot = gaming_result.reboot_required
            gaming_message = " " + gaming_result.message
            context.log(gaming_result.message)
            if not gaming_result.success:
                failures.append(gaming_result.details or gaming_result.message)
        return ActionResult(action, ACTION_SPECS[action].title, not failures,
                            f"Restored {restored}/{len(backups)} recording/service/process changes.{gaming_message} Failed/conflicting baselines are retained.", "\n".join(failures), reboot_required=reboot)

    def tweak_snapshot(self) -> TweakSnapshot:
        services = []
        for name, (title, description) in SERVICE_DEFINITIONS.items():
            backed_up = bool(self.state.backup("service:" + name))
            try:
                snapshot = self.services.snapshot(name)
                services.append(ServiceInfo(name, title, description, True,
                                            STATE_NAMES.get(snapshot.state, "Unknown"), snapshot.start_type, backed_up))
            except OSError as exc:
                absent = getattr(exc, "winerror", None) == 1060
                services.append(ServiceInfo(name, title, description, not absent,
                                            "Not installed" if absent else "Unavailable", -1, backed_up, str(exc)))
        try:
            disabled = self.registry.is_disabled(self.registry.read())
            error = ""
        except OSError as exc:
            disabled, error = False, str(exc)
        return TweakSnapshot(tuple(services), disabled, bool(self.state.backup("recording")), error, self.gaming.snapshot())
