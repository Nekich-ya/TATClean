from __future__ import annotations

import ctypes
import hashlib
import re
import shutil
import subprocess
import sys
import uuid
from ctypes import wintypes
from pathlib import Path

import psutil

from tatclean.core.models import VolumeInfo
from tatclean.native.winapi import (
    FILE_SHARE_READ,
    FILE_SHARE_WRITE,
    OPEN_EXISTING,
    Handle,
    Kernel,
    bind,
    check_bool,
    com_apartment,
    library,
    raise_last_error,
)
from tatclean.runtime import is_packaged

FOLDERS = {
    "profile": "5E6C858F-0E22-4760-9AFE-EA3317B67173",
    "local": "F1B32785-6FBA-4FCF-9D55-7B8E7F157091",
    "roaming": "3EB685DB-65F9-4CF6-A03A-E3EF65729F3D",
    "program_data": "62AB5D82-FDC1-4DC3-A9DD-070D1D495D97",
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}


class GUID(ctypes.Structure):
    _fields_ = [
        ("data1", wintypes.DWORD), ("data2", wintypes.WORD), ("data3", wintypes.WORD),
        ("data4", ctypes.c_ubyte * 8),
    ]


def known_folder(name: str) -> Path:
    identifier = GUID.from_buffer_copy(uuid.UUID(FOLDERS[name]).bytes_le)
    shell = library("shell32")
    get_folder = bind(
        shell, "SHGetKnownFolderPath",
        [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)],
        ctypes.c_long,
    )
    release = bind(library("ole32"), "CoTaskMemFree", [ctypes.c_void_p], None)
    pointer = ctypes.c_void_p()
    result = get_folder(ctypes.byref(identifier), 0x4000, None, ctypes.byref(pointer))  # KF_FLAG_DONT_VERIFY
    if result < 0:
        raise OSError(f"Cannot resolve the {name} known folder (HRESULT 0x{result & 0xffffffff:08X}).")
    try:
        return Path(ctypes.wstring_at(pointer))
    finally:
        release(pointer)


def windows_directory() -> Path:
    function = bind(library("kernel32"), "GetWindowsDirectoryW", [wintypes.LPWSTR, wintypes.UINT], wintypes.UINT)
    buffer = ctypes.create_unicode_buffer(32768)
    length = function(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise_last_error()
    return Path(buffer.value)


def system_directory() -> Path:
    function = bind(library("kernel32"), "GetSystemDirectoryW", [wintypes.LPWSTR, wintypes.UINT], wintypes.UINT)
    buffer = ctypes.create_unicode_buffer(32768)
    length = function(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise_last_error()
    return Path(buffer.value)


def temporary_directory() -> Path:
    function = bind(library("kernel32"), "GetTempPathW", [wintypes.DWORD, wintypes.LPWSTR], wintypes.DWORD)
    buffer = ctypes.create_unicode_buffer(32768)
    length = function(len(buffer), buffer)
    if not length or length >= len(buffer):
        raise_last_error()
    return Path(buffer.value)


def is_admin() -> bool:
    return bool(bind(library("shell32"), "IsUserAnAdmin", [], wintypes.BOOL)())


class StorageQuery(ctypes.Structure):
    _fields_ = [("property", wintypes.DWORD), ("query_type", wintypes.DWORD), ("extra", ctypes.c_ubyte)]


class SeekPenalty(ctypes.Structure):
    _fields_ = [("version", wintypes.DWORD), ("size", wintypes.DWORD), ("incurs", ctypes.c_ubyte)]


def media_type(drive: str) -> str:
    """Query a volume's reported seek penalty; virtual/unsupported devices stay unknown."""
    kernel = Kernel()
    control = bind(
        kernel.dll, "DeviceIoControl",
        [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p], wintypes.BOOL,
    )
    try:
        with Handle(
            kernel.create_file(
                "\\\\.\\" + drive, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                OPEN_EXISTING, 0, None,
            ), kernel.close_handle,
        ) as handle:
            query = StorageQuery(7, 0, 0)  # StorageDeviceSeekPenaltyProperty
            answer = SeekPenalty()
            returned = wintypes.DWORD()
            if not control(
                handle.value, 0x2D1400, ctypes.byref(query), ctypes.sizeof(query),
                ctypes.byref(answer), ctypes.sizeof(answer), ctypes.byref(returned), None,
            ) or returned.value < 9:
                return "Windows-managed"
            return "HDD / seek penalty" if answer.incurs else "SSD / no seek penalty"
    except OSError:
        return "Windows-managed"


def fixed_volumes() -> tuple[VolumeInfo, ...]:
    kernel = library("kernel32")
    get_drives = bind(kernel, "GetLogicalDrives", [], wintypes.DWORD)
    get_type = bind(kernel, "GetDriveTypeW", [wintypes.LPCWSTR], wintypes.UINT)
    get_info = bind(
        kernel, "GetVolumeInformationW",
        [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
         ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR,
         wintypes.DWORD], wintypes.BOOL,
    )
    mask = get_drives()
    if not mask:
        raise_last_error()
    result = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        drive = f"{chr(65 + index)}:"
        root = drive + "\\"
        if get_type(root) != 3:  # DRIVE_FIXED only; never network/removable drives.
            continue
        label, filesystem = ctypes.create_unicode_buffer(261), ctypes.create_unicode_buffer(261)
        serial, maximum, flags = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
        if not get_info(root, label, len(label), ctypes.byref(serial), ctypes.byref(maximum),
                        ctypes.byref(flags), filesystem, len(filesystem)):
            continue
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        result.append(VolumeInfo(drive, label.value, filesystem.value, media_type(drive), usage.total, usage.free))
    return tuple(result)


def validate_fixed_drive(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z]:", value):
        raise ValueError("Choose a fixed local drive such as C:.")
    drive = value.upper()
    get_type = bind(library("kernel32"), "GetDriveTypeW", [wintypes.LPCWSTR], wintypes.UINT)
    if get_type(drive + "\\") != 3:
        raise ValueError("Storage optimization is limited to fixed local drives.")
    return drive


def is_fixed_local_path(path: Path) -> bool:
    get_type = bind(library("kernel32"), "GetDriveTypeW", [wintypes.LPCWSTR], wintypes.UINT)
    return get_type(path.anchor) in (3, 6)  # Fixed disk or local RAM disk; never SMB/removable.


def drive_type(path: Path) -> int:
    get_type = bind(library("kernel32"), "GetDriveTypeW", [wintypes.LPCWSTR], wintypes.UINT)
    return int(get_type(path.anchor))


def mounted_volumes() -> tuple[VolumeInfo, ...]:
    """Enumerate mounted letters without querying network/optical media contents."""
    kernel = library("kernel32")
    mask = bind(kernel, "GetLogicalDrives", [], wintypes.DWORD)()
    if not mask:
        raise_last_error()
    fixed = {volume.drive: volume for volume in fixed_volumes()}
    labels = {0: "Unknown", 1: "Unavailable", 2: "Removable", 3: "Fixed", 4: "Network", 5: "Optical", 6: "RAM disk"}
    get_info = bind(kernel, "GetVolumeInformationW",
                    [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                     ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD], wintypes.BOOL)
    result = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        drive = f"{chr(65 + index)}:"
        root = Path(drive + "\\")
        kind = drive_type(root)
        if drive in fixed:
            result.append(fixed[drive])
            continue
        if kind not in (2, 6):
            result.append(VolumeInfo(drive, "", "", labels.get(kind, "Unknown"), 0, 0,
                                     labels.get(kind, "Unknown"), False, "Network/optical/unavailable volumes are listed but never scanned."))
            continue
        label, filesystem = ctypes.create_unicode_buffer(261), ctypes.create_unicode_buffer(261)
        serial, maximum, flags = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
        try:
            if not get_info(str(root), label, len(label), ctypes.byref(serial), ctypes.byref(maximum), ctypes.byref(flags), filesystem, len(filesystem)):
                raise_last_error()
            usage = shutil.disk_usage(root)
            result.append(VolumeInfo(drive, label.value, filesystem.value, labels[kind], usage.total, usage.free, labels[kind]))
        except OSError as error:
            result.append(VolumeInfo(drive, "", "", labels[kind], 0, 0, labels[kind], False, str(error)))
    # Folder-mounted volumes are visible, but are not traversed as reparse-point roots.
    seen = {str(Path(volume.drive + "\\")).casefold() for volume in result}
    try:
        for partition in psutil.disk_partitions(all=True):
            root = Path(partition.mountpoint)
            if str(root).casefold() not in seen and root.is_absolute() and not str(root).startswith("\\\\"):
                result.append(VolumeInfo(str(root), "", partition.fstype, "Folder mount", 0, 0,
                                         "Folder mount", False, "Reparse/mount-point traversal is disabled; use a drive letter when available.", str(root)))
    except (psutil.Error, OSError):
        pass
    return tuple(result)


class RecycleInformation(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("bytes", ctypes.c_longlong), ("items", ctypes.c_longlong)]


def recycle_bin_size() -> tuple[int, int]:
    query = bind(library("shell32"), "SHQueryRecycleBinW", [wintypes.LPCWSTR, ctypes.POINTER(RecycleInformation)], ctypes.c_long)
    information = RecycleInformation()
    information.size = ctypes.sizeof(information)
    with com_apartment():
        result = query(None, ctypes.byref(information))
    if result < 0:
        raise OSError(f"Recycle Bin query failed (HRESULT 0x{result & 0xffffffff:08X}).")
    return max(0, information.items), max(0, information.bytes)


def empty_recycle_bin() -> tuple[int, int]:
    items, size = recycle_bin_size()
    if not items:
        return 0, 0
    empty = bind(library("shell32"), "SHEmptyRecycleBinW", [wintypes.HWND, wintypes.LPCWSTR, wintypes.DWORD], ctypes.c_long)
    with com_apartment():
        result = empty(None, None, 0x1 | 0x2 | 0x4)  # Our dialog handles user confirmation.
    if result < 0:
        raise OSError(f"Recycle Bin cleanup failed (HRESULT 0x{result & 0xffffffff:08X}).")
    return items, size


class TokenUser(ctypes.Structure):
    _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]


def current_user_sid() -> str:
    kernel = Kernel()
    get_process = bind(kernel.dll, "GetCurrentProcess", [], wintypes.HANDLE)
    local_free = bind(kernel.dll, "LocalFree", [ctypes.c_void_p], ctypes.c_void_p)
    security = library("advapi32")
    open_token = bind(security, "OpenProcessToken", [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)], wintypes.BOOL)
    get_token = bind(security, "GetTokenInformation", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)
    convert = bind(security, "ConvertSidToStringSidW", [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)], wintypes.BOOL)
    token = wintypes.HANDLE()
    check_bool(open_token(get_process(), 0x8, ctypes.byref(token)))
    with Handle(token.value, kernel.close_handle) as handle:
        needed = wintypes.DWORD()
        get_token(handle.value, 1, None, 0, ctypes.byref(needed))
        if not needed.value:
            raise_last_error()
        buffer = ctypes.create_string_buffer(needed.value)
        check_bool(get_token(handle.value, 1, buffer, len(buffer), ctypes.byref(needed)))
        user = ctypes.cast(buffer, ctypes.POINTER(TokenUser)).contents
        text = ctypes.c_void_p()
        check_bool(convert(user.sid, ctypes.byref(text)))
        try:
            return ctypes.wstring_at(text)
        finally:
            local_free(text)


class SingleInstance:
    def __init__(self, *, handoff: bool = False):
        kernel = Kernel()
        create = bind(kernel.dll, "CreateMutexW", [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE)
        self.release = bind(kernel.dll, "ReleaseMutex", [wintypes.HANDLE], wintypes.BOOL)
        wait = bind(kernel.dll, "WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD)
        identity = hashlib.sha256(current_user_sid().encode()).hexdigest()[:24]
        value = create(None, True, "Local\\TATClean_" + identity)
        error = ctypes.get_last_error()
        self.handle = Handle(value, kernel.close_handle)
        self.owned = error != 183  # ERROR_ALREADY_EXISTS: initial ownership was ignored.
        if not self.owned and handoff:
            self.owned = wait(self.handle.value, 45000) in (0, 0x80)
        if not self.owned:
            self.handle.close()
            raise RuntimeError("TAT Clean is already running in this Windows session.")

    def close(self) -> None:
        if self.owned and self.handle.value is not None:
            self.release(self.handle.value)
            self.owned = False
        self.handle.close()


class ShellExecuteInformation(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
    ]


def relaunch_elevated() -> None:
    if is_packaged():
        arguments = ["--handoff"]
        directory = Path(sys.executable).parent
    else:
        launcher = Path(__file__).resolve().parents[3] / "run.py"
        if launcher.is_file():
            arguments = [str(launcher), "--handoff"]
            directory = launcher.parent
        else:
            arguments = ["-m", "tatclean", "--handoff"]
            directory = Path.home()
    information = ShellExecuteInformation()
    information.cbSize = ctypes.sizeof(information)
    information.fMask = 0x40
    information.lpVerb = "runas"
    information.lpFile = sys.executable
    information.lpParameters = subprocess.list2cmdline(arguments)
    information.lpDirectory = str(directory)
    information.nShow = 1
    execute = bind(library("shell32"), "ShellExecuteExW", [ctypes.POINTER(ShellExecuteInformation)], wintypes.BOOL)
    with com_apartment():
        check_bool(execute(ctypes.byref(information)))
    if information.hProcess:
        Kernel().close_handle(information.hProcess)


def apply_dark_window(handle: int) -> None:
    try:
        set_attribute = bind(library("dwmapi"), "DwmSetWindowAttribute", [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD], ctypes.c_long)
        dark = wintypes.BOOL(True)
        set_attribute(handle, 20, ctypes.byref(dark), ctypes.sizeof(dark))
        corners = wintypes.DWORD(2)
        set_attribute(handle, 33, ctypes.byref(corners), ctypes.sizeof(corners))
    except (OSError, AttributeError):
        # Rounded corners are optional on Windows 10; no undocumented APIs are used.
        return
