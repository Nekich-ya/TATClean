from __future__ import annotations

import ctypes
import os
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import PureWindowsPath

from tatclean.core.config import PROTECTED_PROCESSES
from tatclean.core.models import ProcessInfo
from tatclean.native.system import TokenUser, current_user_sid
from tatclean.native.winapi import Handle, Kernel, bind, check_bool, filetime_ns, library

HIGH_PRIORITY_CLASS = 0x80
PRIORITY_NAMES = {
    0x40: "Idle", 0x4000: "Below normal", 0x20: "Normal", 0x8000: "Above normal",
    0x80: "High", 0x100: "Real-time",
}
ALLOWED_PRIORITY_CLASSES = frozenset((0x40, 0x4000, 0x20, 0x8000, 0x80))


class ProcessManager:
    def __init__(self):
        self.kernel = Kernel()
        self.open_process = bind(self.kernel.dll, "OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE)
        self.get_times = bind(self.kernel.dll, "GetProcessTimes", [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)], wintypes.BOOL)
        self.get_priority = bind(self.kernel.dll, "GetPriorityClass", [wintypes.HANDLE], wintypes.DWORD)
        self.set_priority = bind(self.kernel.dll, "SetPriorityClass", [wintypes.HANDLE, wintypes.DWORD], wintypes.BOOL)
        self.trim_working_set = bind(self.kernel.dll, "SetProcessWorkingSetSize", [wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t], wintypes.BOOL)
        self.get_image = bind(self.kernel.dll, "QueryFullProcessImageNameW", [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)
        self.get_session = bind(self.kernel.dll, "ProcessIdToSessionId", [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)
        self.user_sid = current_user_sid()
        self.session = self.session_id(os.getpid())

    def session_id(self, pid: int) -> int:
        value = wintypes.DWORD()
        check_bool(self.get_session(pid, ctypes.byref(value)))
        return value.value

    def _owner_sid(self, handle: Handle) -> str:
        security = library("advapi32")
        open_token = bind(security, "OpenProcessToken", [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)], wintypes.BOOL)
        get_token = bind(security, "GetTokenInformation", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)
        convert = bind(security, "ConvertSidToStringSidW", [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)], wintypes.BOOL)
        release = bind(self.kernel.dll, "LocalFree", [ctypes.c_void_p], ctypes.c_void_p)
        token = wintypes.HANDLE()
        check_bool(open_token(handle.value, 0x8, ctypes.byref(token)))
        with Handle(token.value, self.kernel.close_handle) as token_handle:
            needed = wintypes.DWORD()
            get_token(token_handle.value, 1, None, 0, ctypes.byref(needed))
            buffer = ctypes.create_string_buffer(needed.value)
            check_bool(get_token(token_handle.value, 1, buffer, len(buffer), ctypes.byref(needed)))
            sid = ctypes.cast(buffer, ctypes.POINTER(TokenUser)).contents.sid
            text = ctypes.c_void_p()
            check_bool(convert(sid, ctypes.byref(text)))
            try:
                return ctypes.wstring_at(text)
            finally:
                release(text)

    @contextmanager
    def eligible_handle(self, process: ProcessInfo, access: int):
        if process.pid <= 4 or process.pid == os.getpid() or not process.manageable:
            raise PermissionError("System/protected processes and TAT Clean itself cannot be modified.")
        if process.name.casefold() in PROTECTED_PROCESSES:
            raise PermissionError("Protected Windows process.")
        with Handle(self.open_process(access | 0x1000, False, process.pid), self.kernel.close_handle) as handle:
            created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
            check_bool(self.get_times(handle.value, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel_time), ctypes.byref(user_time)))
            if abs(filetime_ns(created) / 1e9 - process.create_time) > 0.002:
                raise ValueError("This PID now belongs to another process. Refresh the process list.")
            image, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            check_bool(self.get_image(handle.value, 0, image, ctypes.byref(length)))
            name = PureWindowsPath(image.value).name.casefold()
            if name != process.name.casefold() or name in PROTECTED_PROCESSES:
                raise PermissionError("Process executable identity changed or is protected.")
            if self.session_id(process.pid) != self.session or self._owner_sid(handle) != self.user_sid:
                raise PermissionError("Only processes owned by this user in this session can be modified.")
            yield handle

    def priority(self, process: ProcessInfo) -> int:
        with self.eligible_handle(process, 0) as handle:
            value = self.get_priority(handle.value)
            check_bool(value)
            return value

    def change_priority(self, process: ProcessInfo, priority: int, expected: int | None = None) -> int:
        if priority not in ALLOWED_PRIORITY_CLASSES:
            raise ValueError("Real-time priority is deliberately unsupported.")
        with self.eligible_handle(process, 0x200) as handle:
            original = self.get_priority(handle.value)
            check_bool(original)
            if original not in ALLOWED_PRIORITY_CLASSES:
                raise PermissionError("A real-time or unknown-priority process cannot be modified.")
            if expected is not None and original != expected:
                raise ValueError("Priority changed during the operation; refresh and retry.")
            check_bool(self.set_priority(handle.value, priority))
            return original

    def trim(self, process: ProcessInfo) -> None:
        with self.eligible_handle(process, 0x100) as handle:
            check_bool(self.trim_working_set(handle.value, ctypes.c_size_t(-1).value, ctypes.c_size_t(-1).value))


def foreground_pid() -> int:
    user = library("user32")
    get_window = bind(user, "GetForegroundWindow", [], wintypes.HWND)
    get_pid = bind(user, "GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD)
    pid = wintypes.DWORD()
    window = get_window()
    if window:
        get_pid(window, ctypes.byref(pid))
    return pid.value
