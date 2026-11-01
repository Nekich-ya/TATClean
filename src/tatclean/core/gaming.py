from __future__ import annotations

import copy
import os
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psutil

from tatclean.core.gaming_models import (
    GAMING_FEATURES,
    GamingRequest,
    GamingResult,
    GamingSnapshot,
    GamingState,
)
from tatclean.core.models import TaskProgress
from tatclean.core.state import StateStore
from tatclean.core.tasks import CancelledError, TaskContext
from tatclean.native.gaming_registry import (
    GamingRegistry,
    RegistrySetting,
    interface_guid,
    registry_settings,
)
from tatclean.native.gpu import gpu_scheduling_capabilities
from tatclean.native.power import PowerPlans, power_guid
from tatclean.native.restore_points import RestorePoints
from tatclean.native.system import current_user_sid, is_admin
from tatclean.native.timer import TimerResolution
from tatclean.native.visual import VisualEffects


def compatible(current: Any, before: Any, expected: Any) -> bool:
    """Allow recovery of partial writes, but not unrelated external changes."""
    if isinstance(current, list):
        return (isinstance(before, list) and isinstance(expected, list)
                and len(current) == len(before) == len(expected)
                and all(now in (old, new) for now, old, new in zip(current, before, expected, strict=True)))
    if isinstance(current, dict):
        return (isinstance(before, dict) and isinstance(expected, dict)
                and set(current) == set(before) == set(expected)
                and all(current[key] in (before[key], expected[key]) for key in current))
    return current in (before, expected)


@dataclass(slots=True)
class GamingChange:
    key: str
    kind: str
    before: Any
    expected: Any
    previous: dict[str, Any] | None
    settings: tuple[RegistrySetting, ...] = ()
    mutated: bool = False


class GamingService:
    """Checkpoint-gated, journaled gaming changes with reverse-order batch rollback."""

    def __init__(
        self, state: StateStore, runner: Any, *, registry=None, timer=None, visual=None,
        power=None, checkpoints=None, gpu_provider: Callable = gpu_scheduling_capabilities,
        owner: str | None = None, admin_provider: Callable[[], bool] = is_admin,
    ):
        self.state = state
        self.registry = registry or GamingRegistry()
        self.timer = timer or TimerResolution()
        self.visual = visual or VisualEffects()
        self.power = power or PowerPlans(runner)
        self.checkpoints = checkpoints or RestorePoints(runner)
        self.gpu_provider = gpu_provider
        self.owner = owner or current_user_sid()
        self.admin_provider = admin_provider
        self.session = {"pid": os.getpid(), "created": psutil.Process().create_time()}

    def _owner(self, record: dict[str, Any]) -> None:
        if record.get("owner") != self.owner:
            raise PermissionError("This gaming restore point belongs to a different Windows user.")

    def _journal(self, key: str, kind: str, before: Any, expected: Any, previous,
                 **metadata) -> dict[str, Any]:
        payload = {"owner": self.owner, "kind": kind,
                   "original": copy.deepcopy(previous["original"] if previous else before),
                   "expected": copy.deepcopy(expected), "phase": "pending", **metadata}
        if previous:
            self._owner(previous)
            if previous.get("kind") != kind:
                raise ValueError("Gaming restore-point type changed.")
        else:
            saved = self.state.save_original(key, payload)
            if saved != payload:
                raise ValueError("A restore point was created concurrently; retry after reviewing it.")
        self.state.update_gaming_backup(key, payload)
        return payload

    def _finish_journal(self, key: str) -> None:
        payload = self.state.backup(key)
        if payload is None:
            raise ValueError("The durable gaming restore point disappeared.")
        payload["phase"] = "applied"
        self.state.update_gaming_backup(key, payload)

    def _restore_journal(self, change: GamingChange) -> None:
        if change.previous is None:
            self.state.remove_backup(change.key)
        else:
            self.state.update_gaming_backup(change.key, change.previous)

    @staticmethod
    def _registry_key(feature: str, interface: str = "") -> str:
        return "gaming:tcp:" + interface_guid(interface) if feature == "nagle" else "gaming:registry:" + feature

    def _record_settings(self, key: str, record: dict[str, Any]) -> tuple[RegistrySetting, ...]:
        if key.startswith("gaming:tcp:"):
            guid = interface_guid(key[len("gaming:tcp:"):])
            if record.get("feature") != "nagle" or record.get("interface") != guid:
                raise ValueError("Network restore-point scope is invalid.")
            return registry_settings("nagle", interface=guid)
        feature = key.removeprefix("gaming:registry:")
        if feature not in ("game_mode", "hags", "mmcss") or record.get("feature") != feature:
            raise ValueError("Unapproved registry restore-point scope.")
        return registry_settings(feature)

    def _registry_change(self, feature: str, context: TaskContext, *, enabled=True, interface="") -> GamingChange | None:
        settings = registry_settings(feature, enabled=enabled, interface=interface)
        key = self._registry_key(feature, interface)
        before, expected = self.registry.read(settings), self.registry.expected(settings)
        previous = self.state.backup(key)
        if previous:
            self._owner(previous)
            self._record_settings(key, previous)
            if not compatible(before, previous["original"], previous["expected"]):
                raise ValueError(f"{feature} changed outside TAT Clean; its baseline is retained.")
        if before == expected:
            return None
        self._journal(key, "registry", before, expected, previous, feature=feature,
                      interface=interface_guid(interface) if interface else "")
        change = GamingChange(key, "registry", before, expected, previous, settings)
        try:
            context.checkpoint()
            change.mutated = True
            self.registry.write(settings, expected)
            if self.registry.read(settings) != expected:
                raise OSError("Windows did not persist the requested registry values.")
            self._finish_journal(key)
        except Exception as error:
            self._recover_failed(change, error)
            raise
        return change

    def _visual_change(self, context: TaskContext) -> GamingChange | None:
        key, settings = "gaming:visual", registry_settings("visual")
        before = {"registry": self.registry.read(settings), "spi": self.visual.snapshot()}
        expected = {"registry": self.registry.expected(settings), "spi": dict.fromkeys(before["spi"], False)}
        previous = self.state.backup(key)
        if previous:
            self._owner(previous)
            if (not compatible(before["registry"], previous["original"]["registry"], previous["expected"]["registry"])
                    or not compatible(before["spi"], previous["original"]["spi"], previous["expected"]["spi"])):
                raise ValueError("Visual preferences changed outside TAT Clean; refusing to overwrite them.")
        if before == expected:
            return None
        self._journal(key, "visual", before, expected, previous)
        change = GamingChange(key, "visual", before, expected, previous, settings)
        try:
            context.checkpoint()
            change.mutated = True
            self.registry.write(settings, expected["registry"])
            self.visual.write(expected["spi"], context)
            actual = {"registry": self.registry.read(settings), "spi": self.visual.snapshot()}
            if actual != expected:
                raise OSError("Windows did not apply every visual-effect setting.")
            self._finish_journal(key)
        except Exception as error:
            self._recover_failed(change, error)
            raise
        return change

    def _timer_record(self, record: dict[str, Any]) -> None:
        self._owner(record)
        saved = record.get("session", {})
        if saved == self.session:
            return
        try:
            live = psutil.Process(int(saved["pid"]))
            if abs(live.create_time() - float(saved["created"])) <= 0.002:
                raise ValueError("The timer journal belongs to another live TAT Clean process. Close that instance first.")
        except (psutil.NoSuchProcess, KeyError, TypeError):
            return

    def _timer_change(self, microseconds: int, context: TaskContext) -> GamingChange | None:
        key, before = "gaming:timer", self.timer.snapshot().requested_us
        previous = self.state.backup(key)
        if previous:
            self._timer_record(previous)
            if previous.get("session") != self.session:
                self.state.remove_backup(key)
                previous = None  # Old process requests are released by Windows on process exit.
        if before == microseconds:
            return None
        self._journal(key, "timer", before, microseconds, previous, session=self.session)
        change = GamingChange(key, "timer", before, microseconds, previous)
        try:
            context.checkpoint()
            change.mutated = True
            self.timer.set(microseconds)
            self._finish_journal(key)
        except Exception as error:
            self._recover_failed(change, error)
            raise
        return change

    def _power_change(self, context: TaskContext) -> GamingChange | None:
        key = "gaming:power"
        before = self.power.active(context)
        previous = self.state.backup(key)
        if previous:
            self._owner(previous)
            original = previous["original"]
            created = power_guid(original["created"])
            if before not in (original["active"], previous["expected"]):
                raise ValueError("The active power plan changed outside TAT Clean; no plan will be overridden.")
            if self.power.description(created) != self.power._marker(original["marker"]):
                raise ValueError("The saved application plan no longer has its ownership marker.")
            if before == created:
                return None
        else:
            original = {"active": before, "created": str(uuid.uuid4()), "marker": uuid.uuid4().hex}
            created = original["created"]
        # Power baseline includes the identity of the *owned* plan, persisted before creation.
        payload = {"owner": self.owner, "kind": "power", "original": original,
                   "expected": created, "phase": "pending"}
        if previous is None and self.state.save_original(key, payload) != payload:
            raise ValueError("A power-plan journal appeared concurrently.")
        self.state.update_gaming_backup(key, payload)
        change = GamingChange(key, "power", before, created, previous)
        try:
            context.checkpoint()
            change.mutated = True
            if previous is None:
                self.power.create_ultimate(created, original["marker"], context)
            self.power.activate(created, context)
            self._finish_journal(key)
        except Exception as error:
            self._recover_failed(change, error)
            raise
        return change

    def _rollback(self, change: GamingChange, context: TaskContext) -> None:
        if not change.mutated:
            self._restore_journal(change)
            return
        if change.kind == "registry":
            now = self.registry.read(change.settings)
            if not compatible(now, change.before, change.expected):
                raise ValueError("External registry change detected during rollback; baseline retained.")
            self.registry.write(change.settings, change.before)
        elif change.kind == "visual":
            now_registry, now_spi = self.registry.read(change.settings), self.visual.snapshot()
            if not compatible(now_registry, change.before["registry"], change.expected["registry"]) or not compatible(now_spi, change.before["spi"], change.expected["spi"]):
                raise ValueError("External visual preference change detected during rollback; baseline retained.")
            self.visual.write(change.before["spi"], context)
            self.registry.write(change.settings, change.before["registry"])
        elif change.kind == "timer":
            if change.before is None:
                self.timer.release()
            else:
                self.timer.set(change.before)
        elif change.kind == "power":
            record = self.state.backup(change.key)
            if record is None:
                raise ValueError("Power-plan journal was removed during rollback.")
            current = self.power.active(context)
            if current not in (change.before, change.expected):
                raise ValueError("An external power-plan change prevents safe rollback.")
            self.power.activate(change.before, context)
            original = record["original"]
            if change.previous is None and original["created"] in self.power.schemes(context):
                self.power.delete_owned(original["created"], original["marker"], context)
        else:
            raise ValueError("Unrecognized rollback kind.")
        self._restore_journal(change)

    def _recover_failed(self, change: GamingChange, error: Exception) -> None:
        try:
            self._rollback(change, TaskContext())
        except Exception as rollback:
            raise RuntimeError(f"{error}; rollback could not finish: {rollback}. The durable restore point is retained.") from error

    def _preflight(self, request: GamingRequest) -> None:
        request.validate()
        if not self.admin_provider():
            raise PermissionError("Administrator access is required for checkpoint-protected gaming optimization.")
        if "hags" in request.features and not any(gpu.supported is True for gpu in self.gpu_provider()):
            raise ValueError("HAGS support could not be verified on an eligible GPU. The registry will not be changed.")
        if "game_mode" in request.features and sys.getwindowsversion().build < 15063:
            raise ValueError("Windows Game Mode is unavailable on this build.")
        if "timer" in request.features:
            resolution = self.timer.snapshot()
            if not resolution.finest_us <= request.timer_us <= resolution.coarsest_us:
                raise ValueError("The requested timer resolution is unsupported by this system.")
        if "nagle" in request.features:
            canonical = tuple(interface_guid(value) for value in request.interface_ids)
            if len(set(canonical)) != len(canonical):
                raise ValueError("Network interface selection contains duplicate GUID aliases.")
            present = {item.guid for item in self.registry.interfaces()}
            for value in request.interface_ids:
                if interface_guid(value) not in present:
                    raise ValueError("A selected IPv4 interface disappeared or is not verifiable.")

    def apply(self, request: GamingRequest, context: TaskContext) -> GamingResult:
        applied: list[GamingChange] = []
        reboot = False
        try:
            self._preflight(request)
            context.checkpoint()
            self.checkpoints.ensure(context)
            # Timer last: a persistent-tweak failure cannot leave a new request running.
            order = ("game_mode", "power", "visual", "mmcss", "nagle", "hags", "timer")
            selected = [feature for feature in order if feature in request.features]
            for index, feature in enumerate(selected):
                context.checkpoint()
                title = next(item.title for item in GAMING_FEATURES if item.key == feature)
                context.report(TaskProgress(f"Applying {title}…", int(index * 100 / len(selected))), force=True)
                if feature == "nagle":
                    changes = []
                    for value in request.interface_ids:
                        change = self._registry_change(feature, context, interface=interface_guid(value))
                        changes.append(change)
                        if change is not None:
                            applied.append(change)  # Retain each adapter undo token before the next can fail.
                elif feature in ("game_mode", "hags", "mmcss"):
                    enabled = request.game_mode_enabled if feature == "game_mode" else request.hags_enabled
                    changes = [self._registry_change(feature, context, enabled=enabled)]
                elif feature == "timer":
                    changes = [self._timer_change(request.timer_us, context)]
                elif feature == "power":
                    changes = [self._power_change(context)]
                else:
                    changes = [self._visual_change(context)]
                actual = [change for change in changes if change is not None]
                if feature != "nagle":
                    applied.extend(actual)
                if actual:
                    reboot |= next(item.reboot for item in GAMING_FEATURES if item.key == feature)
                context.log(f"{title}: {'applied with saved baseline' if actual else 'already in requested state; no new baseline'}.")
            context.report(TaskProgress("Selected gaming optimization completed", 100), force=True)
            return GamingResult(True, f"Applied {len(applied)} journaled changes. Timer requests are released on exit; persistent tweaks can be reverted.", reboot_required=reboot)
        except Exception as error:
            failures = []
            for change in reversed(applied):
                try:
                    self._rollback(change, TaskContext())
                except Exception as rollback:
                    failures.append(f"{change.key}: {rollback}")
            message = f"Gaming optimization stopped: {error}. Earlier changes were rolled back where safe."
            if failures:
                message += " Failed/conflicting restore points remain available."
            return GamingResult(False, message, "\n".join(failures), reboot_required=bool(failures), cancelled=isinstance(error, CancelledError))

    def _restore_key(self, key: str, context: TaskContext) -> bool:
        record = self.state.backup(key)
        if record is None:
            return False
        self._owner(record)
        kind = record.get("kind")
        context.checkpoint()
        if kind == "registry":
            settings = self._record_settings(key, record)
            current = self.registry.read(settings)
            if not compatible(current, record["original"], record["expected"]):
                raise ValueError("Registry values changed outside TAT Clean; baseline retained.")
            self.registry.write(settings, record["original"])
        elif kind == "visual" and key == "gaming:visual":
            settings = registry_settings("visual")
            now = {"registry": self.registry.read(settings), "spi": self.visual.snapshot()}
            if not compatible(now["registry"], record["original"]["registry"], record["expected"]["registry"]) or not compatible(now["spi"], record["original"]["spi"], record["expected"]["spi"]):
                raise ValueError("Visual settings changed externally; baseline retained.")
            self.visual.write(record["original"]["spi"], context)
            self.registry.write(settings, record["original"]["registry"])
        elif kind == "timer" and key == "gaming:timer":
            self._timer_record(record)
            if record.get("session") == self.session:
                self.timer.release()
        elif kind == "power" and key == "gaming:power":
            original = record["original"]
            if self.power.active(context) not in (power_guid(original["active"]), power_guid(record["expected"])):
                raise ValueError("Active plan changed externally; no unrelated plan will be overridden.")
            self.power.activate(original["active"], context)
            if original["created"] in self.power.schemes(context):
                self.power.delete_owned(original["created"], original["marker"], context)
        else:
            raise ValueError("Unapproved gaming restore-point kind/key.")
        self.state.remove_backup(key)
        return key in ("gaming:registry:hags", "gaming:registry:mmcss") or key.startswith("gaming:tcp:")

    def restore(self, context: TaskContext, feature: str = "") -> GamingResult:
        if feature and feature not in {item.key for item in GAMING_FEATURES}:
            raise ValueError("Unknown gaming restore scope.")
        keys = list(self.state.backups("gaming:"))
        if feature:
            prefixes = {"timer": "gaming:timer", "power": "gaming:power", "visual": "gaming:visual", "nagle": "gaming:tcp:"}
            prefix = prefixes.get(feature, "gaming:registry:" + feature)
            keys = [key for key in keys if key.startswith(prefix)]
        errors, restored, reboot = [], 0, False
        for index, key in enumerate(reversed(keys)):
            context.checkpoint()
            try:
                needs_admin = key not in ("gaming:timer", "gaming:visual", "gaming:registry:game_mode")
                if needs_admin and not self.admin_provider():
                    raise PermissionError("Administrator access required for this saved system tweak.")
                reboot |= self._restore_key(key, context)
                restored += 1
                context.log(f"Restored {key}.")
            except CancelledError:
                raise
            except Exception as error:
                errors.append(f"{key}: {error}")
            context.report(TaskProgress("Reverting saved gaming tweaks…", int((index + 1) * 100 / max(1, len(keys)))), force=True)
        return GamingResult(not errors, f"Reverted {restored}/{len(keys)} gaming restore points. Conflicts/failures are retained.", "\n".join(errors), reboot_required=reboot)

    def release_timer(self, context: TaskContext) -> GamingResult:
        context.checkpoint()
        record = self.state.backup("gaming:timer")
        if record:
            self._restore_key("gaming:timer", context)
        else:
            self.timer.release()
        return GamingResult(True, "Released this application's timer request. Other applications' requests are untouched.")

    def shutdown(self, context: TaskContext) -> None:
        if self.timer.snapshot().requested_us is not None:
            self.release_timer(context)

    def snapshot(self) -> GamingSnapshot:
        states, timer, gpus, interfaces = [], None, (), ()
        try:
            timer = self.timer.snapshot()
        except Exception as error:
            states.append(GamingState("timer", "Unavailable", False, str(error), bool(self.state.backup("gaming:timer"))))
        else:
            requested = f"owned {timer.requested_us / 1000:g} ms" if timer.requested_us else "no owned request"
            states.append(GamingState("timer", f"Reported {timer.current_us / 1000:g} ms · {requested}", saved=bool(self.state.backup("gaming:timer"))))
        try:
            gpus = self.gpu_provider()
        except Exception:
            gpus = ()
        try:
            interfaces = self.registry.interfaces()
        except Exception:
            interfaces = ()
        states.append(GamingState("nagle", f"{len(interfaces)} IPv4 interfaces", bool(interfaces), "No verifiable IPv4 interface" if not interfaces else "", bool(self.state.backups("gaming:tcp:"))))
        for feature in ("game_mode", "hags", "mmcss"):
            key = "gaming:registry:" + feature
            try:
                current = self.registry.read(registry_settings(feature))
                data = {value["name"]: value["data"] for value in current if value["exists"]}
                if feature == "hags":
                    support = any(gpu.supported is True for gpu in gpus)
                    desired = {1: "disabled", 2: "enabled"}.get(data.get("HwSchMode"), "driver default")
                    state = GamingState(feature, f"Requested {desired} · restart applies", support,
                                        "GPU support could not be verified" if not support else "", bool(self.state.backup(key)))
                elif feature == "game_mode":
                    status = "Enabled flags" if data.get("AutoGameModeEnabled") == 1 and data.get("AllowAutoGameMode") == 1 else "Disabled/default flags"
                    state = GamingState(feature, status, saved=bool(self.state.backup(key)))
                else:
                    state = GamingState(feature, "Preset applied" if current == self.registry.expected(registry_settings(feature)) else "Original/custom profile", saved=bool(self.state.backup(key)))
                states.append(state)
            except Exception as error:
                states.append(GamingState(feature, "Unavailable", False, str(error), bool(self.state.backup(key))))
        try:
            active = self.power.active(TaskContext())
            record = self.state.backup("gaming:power")
            status = "Application Ultimate plan active" if record and active == record["expected"] else "Active plan " + active
            states.append(GamingState("power", status, saved=bool(record)))
        except Exception as error:
            states.append(GamingState("power", "Unavailable", False, str(error), bool(self.state.backup("gaming:power"))))
        try:
            visual = self.visual.snapshot()
            states.append(GamingState("visual", "Animations reduced" if not any(visual.values()) else "Normal/custom visual preferences", saved=bool(self.state.backup("gaming:visual"))))
        except Exception as error:
            states.append(GamingState("visual", "Unavailable", False, str(error), bool(self.state.backup("gaming:visual"))))
        return GamingSnapshot(tuple(states), timer, tuple(gpus), tuple(interfaces), self.checkpoints.cached)
