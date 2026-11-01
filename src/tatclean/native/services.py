from __future__ import annotations

import ctypes
import re
import time
from ctypes import wintypes
from dataclasses import dataclass

from tatclean.core.tasks import TaskContext
from tatclean.native.winapi import Handle, bind, check_bool, library, raise_last_error

SERVICE_STOPPED = 1
SERVICE_RUNNING = 4
SERVICE_AUTO_START = 2
SERVICE_DEMAND_START = 3
SERVICE_DISABLED = 4

SERVICE_DEFINITIONS = {
    "WSearch": (
        "Windows Search indexing",
        "Disabling indexing can reduce background disk work, but makes Start, Explorer and Outlook searches slower.",
    ),
    "DiagTrack": (
        "Connected User Experiences & Telemetry",
        "Disables this diagnostic collection service. It does not remove all Windows telemetry or override organizational policy.",
    ),
    "dmwappushservice": (
        "WAP push message routing",
        "Optional diagnostic/device-management routing service. Disabling can affect managed-device messaging; leave it enabled on managed PCs.",
    ),
}

STATE_NAMES = {1: "Stopped", 2: "Starting", 3: "Stopping", 4: "Running", 5: "Resuming", 6: "Pausing", 7: "Paused"}


class ServiceStatus(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in (
        "type", "state", "controls", "win32_exit", "service_exit", "checkpoint", "wait_hint",
    )]


class ServiceStatusProcess(ctypes.Structure):
    _fields_ = ServiceStatus._fields_ + [("pid", wintypes.DWORD), ("flags", wintypes.DWORD)]


class ServiceConfiguration(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD), ("start_type", wintypes.DWORD), ("error_control", wintypes.DWORD),
        ("binary_path", wintypes.LPWSTR), ("load_group", wintypes.LPWSTR), ("tag", wintypes.DWORD),
        ("dependencies", wintypes.LPWSTR), ("account", wintypes.LPWSTR), ("display", wintypes.LPWSTR),
    ]


class DelayedAutoStart(ctypes.Structure):
    _fields_ = [("enabled", wintypes.BOOL)]


@dataclass(frozen=True, slots=True)
class ServiceState:
    name: str
    state: int
    start_type: int
    delayed: bool


class ServiceManager:
    def __init__(self):
        self.dll = library("advapi32")
        self.open_manager = bind(self.dll, "OpenSCManagerW", [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD], wintypes.HANDLE)
        self.open_service = bind(self.dll, "OpenServiceW", [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD], wintypes.HANDLE)
        self.close_service = bind(self.dll, "CloseServiceHandle", [wintypes.HANDLE], wintypes.BOOL)
        self.query_status = bind(self.dll, "QueryServiceStatusEx", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)
        self.query_config = bind(self.dll, "QueryServiceConfigW", [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)
        self.query_config2 = bind(self.dll, "QueryServiceConfig2W", [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)
        self.change_config = bind(
            self.dll, "ChangeServiceConfigW",
            [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR,
             wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD), wintypes.LPCWSTR,
             wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR], wintypes.BOOL,
        )
        self.change_config2 = bind(self.dll, "ChangeServiceConfig2W", [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL)
        self.control = bind(self.dll, "ControlService", [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(ServiceStatus)], wintypes.BOOL)
        self.start_service = bind(self.dll, "StartServiceW", [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL)

    def _open(self, name: str, access: int) -> Handle:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name):
            raise ValueError("Invalid Windows service name.")
        with Handle(self.open_manager(None, None, 0x1), self.close_service) as manager:
            return Handle(self.open_service(manager.value, name, access), self.close_service)

    @staticmethod
    def _allow_mutation(name: str) -> None:
        if name not in SERVICE_DEFINITIONS:
            raise ValueError("This service is not approved for modification.")

    def _status(self, handle: Handle) -> ServiceStatusProcess:
        status = ServiceStatusProcess()
        needed = wintypes.DWORD()
        check_bool(self.query_status(handle.value, 0, ctypes.byref(status), ctypes.sizeof(status), ctypes.byref(needed)))
        return status

    def state(self, name: str) -> int:
        with self._open(name, 0x4) as handle:
            return self._status(handle).state

    def snapshot(self, name: str) -> ServiceState:
        with self._open(name, 0x1 | 0x4) as handle:
            needed = wintypes.DWORD()
            self.query_config(handle.value, None, 0, ctypes.byref(needed))
            if not needed.value or needed.value > 65536:
                raise_last_error()
            buffer = ctypes.create_string_buffer(needed.value)
            check_bool(self.query_config(handle.value, buffer, len(buffer), ctypes.byref(needed)))
            configuration = ctypes.cast(buffer, ctypes.POINTER(ServiceConfiguration)).contents
            delayed = DelayedAutoStart()
            check_bool(self.query_config2(handle.value, 3, ctypes.byref(delayed), ctypes.sizeof(delayed), ctypes.byref(needed)))
            return ServiceState(name, self._status(handle).state, configuration.start_type, bool(delayed.enabled))

    def set_start_type(self, name: str, start_type: int, delayed: bool = False) -> None:
        self._allow_mutation(name)
        if start_type not in (SERVICE_AUTO_START, SERVICE_DEMAND_START, SERVICE_DISABLED):
            raise ValueError("Boot/system drivers cannot be changed by TAT Clean.")
        with self._open(name, 0x2) as handle:
            check_bool(self.change_config(handle.value, 0xffffffff, start_type, 0xffffffff,
                                          None, None, None, None, None, None, None))
            if start_type == SERVICE_AUTO_START:
                configuration = DelayedAutoStart(delayed)
                check_bool(self.change_config2(handle.value, 3, ctypes.byref(configuration)))

    def _wait(self, handle: Handle, goal: int, context: TaskContext, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            context.checkpoint()
            status = self._status(handle)
            if status.state == goal:
                return
            if goal == SERVICE_RUNNING and status.state == SERVICE_STOPPED and status.win32_exit:
                raise OSError(f"Service did not start (Windows error {status.win32_exit}).")
            if time.monotonic() >= deadline:
                raise TimeoutError("Service transition timed out; the saved baseline is retained.")
            context.cancel_event.wait(0.25)

    def stop(self, name: str, context: TaskContext, timeout: float = 60) -> None:
        self._allow_mutation(name)
        with self._open(name, 0x4 | 0x20) as handle:
            state = self._status(handle).state
            if state == SERVICE_STOPPED:
                return
            if state == 2:
                self._wait(handle, SERVICE_RUNNING, context, timeout)
                state = self._status(handle).state
            if state != 3:
                status = ServiceStatus()
                if not self.control(handle.value, 1, ctypes.byref(status)) and ctypes.get_last_error() != 1062:
                    raise_last_error()
            self._wait(handle, SERVICE_STOPPED, context, timeout)

    def start(self, name: str, context: TaskContext, timeout: float = 60) -> None:
        self._allow_mutation(name)
        with self._open(name, 0x4 | 0x10 | 0x40) as handle:
            state = self._status(handle).state
            if state == SERVICE_RUNNING:
                return
            if state == 3:
                self._wait(handle, SERVICE_STOPPED, context, timeout)
                state = SERVICE_STOPPED
            if state == 2:
                self._wait(handle, SERVICE_RUNNING, context, timeout)
                return
            if state == 7:
                status = ServiceStatus()
                check_bool(self.control(handle.value, 3, ctypes.byref(status)))
                self._wait(handle, SERVICE_RUNNING, context, timeout)
                return
            if not self.start_service(handle.value, 0, None) and ctypes.get_last_error() != 1056:
                raise_last_error()
            self._wait(handle, SERVICE_RUNNING, context, timeout)
