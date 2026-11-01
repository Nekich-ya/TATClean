from __future__ import annotations

import ctypes
import os
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_READONLY = 0x1
FILE_READ_ATTRIBUTES = 0x80
FILE_LIST_DIRECTORY = 0x1
DELETE = 0x10000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
WINDOWS_EPOCH_NS = 11644473600000000000


def require_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("TAT Clean's native operations require Windows 10 or Windows 11.")


def library(name: str):
    require_windows()
    return ctypes.WinDLL(name, use_last_error=True)


def bind(dll, name: str, arguments: list, result):
    function = getattr(dll, name)
    function.argtypes = arguments
    function.restype = result
    return function


def raise_last_error() -> None:
    raise ctypes.WinError(ctypes.get_last_error())


def check_bool(result) -> None:
    if not result:
        raise_last_error()


@contextmanager
def com_apartment():
    ole = library("ole32")
    initialize = bind(ole, "CoInitializeEx", [ctypes.c_void_p, wintypes.DWORD], ctypes.c_long)
    uninitialize = bind(ole, "CoUninitialize", [], None)
    result = initialize(None, 0x2)
    changed_mode = (result & 0xffffffff) == 0x80010106
    if result < 0 and not changed_mode:
        raise OSError(f"COM initialization failed (HRESULT 0x{result & 0xffffffff:08X}).")
    try:
        yield
    finally:
        if result >= 0:
            uninitialize()


def long_path(path: Path | str) -> str:
    text = os.path.abspath(str(path))
    if text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def normalized_path(path: Path | str) -> str:
    text = str(path)
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(os.path.normpath(os.path.abspath(text)))


def filetime_ns(value: wintypes.FILETIME) -> int:
    ticks = (value.dwHighDateTime << 32) | value.dwLowDateTime
    return ticks * 100 - WINDOWS_EPOCH_NS


class Handle:
    def __init__(self, value, close):
        if value is None or value == INVALID_HANDLE_VALUE:
            raise_last_error()
        self.value = value
        self._close = close

    def close(self) -> None:
        if self.value is not None:
            value, self.value = self.value, None
            self._close(value)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Kernel:
    def __init__(self):
        self.dll = library("kernel32")
        self.create_file = bind(
            self.dll, "CreateFileW",
            [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE], wintypes.HANDLE,
        )
        self.close_handle = bind(self.dll, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL)
        self.final_path = bind(
            self.dll, "GetFinalPathNameByHandleW",
            [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD], wintypes.DWORD,
        )

    def open_file(self, path: Path, access: int, share: int, flags: int) -> Handle:
        return Handle(
            self.create_file(long_path(path), access, share, None, OPEN_EXISTING, flags, None),
            self.close_handle,
        )

    def get_final_path(self, handle: Handle) -> str:
        buffer = ctypes.create_unicode_buffer(32768)
        length = self.final_path(handle.value, buffer, len(buffer), 0)
        if not length:
            raise_last_error()
        if length >= len(buffer):
            raise OSError("Final path exceeds the supported Windows path length.")
        return normalized_path(buffer.value)
