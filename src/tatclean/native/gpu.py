from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from tatclean.core.gaming_models import GpuInfo
from tatclean.native.winapi import bind, library


class DisplayDevice(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("name", wintypes.WCHAR * 32),
                ("description", wintypes.WCHAR * 128), ("flags", wintypes.DWORD),
                ("identifier", wintypes.WCHAR * 128), ("key", wintypes.WCHAR * 128)]


class Luid(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.LONG)]


class OpenAdapter(ctypes.Structure):
    _fields_ = [("name", wintypes.WCHAR * 32), ("adapter", wintypes.UINT),
                ("luid", Luid), ("source", wintypes.UINT)]


class QueryAdapter(ctypes.Structure):
    _fields_ = [("adapter", wintypes.UINT), ("kind", ctypes.c_int),
                ("data", ctypes.c_void_p), ("size", wintypes.UINT)]


class CloseAdapter(ctypes.Structure):
    _fields_ = [("adapter", wintypes.UINT)]


def gpu_scheduling_capabilities() -> tuple[GpuInfo, ...]:
    """Read-only WDDM capability probe. Unknown/failed probes never authorize a toggle.

    KMTQAITYPE_WDDM_2_7_CAPS=70 and its four-byte bitfield come from the Windows
    SDK. This system-oriented query can change across Windows/driver versions.
    """
    if sys.getwindowsversion().build < 19041:
        return (GpuInfo("Windows display", False, False, reason="HAGS requires Windows 10 2004+ or Windows 11."),)
    user, gdi = library("user32"), library("gdi32")
    enumerate_devices = bind(user, "EnumDisplayDevicesW", [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DisplayDevice), wintypes.DWORD], wintypes.BOOL)
    try:
        open_adapter = bind(gdi, "D3DKMTOpenAdapterFromGdiDisplayName", [ctypes.POINTER(OpenAdapter)], ctypes.c_long)
        query_adapter = bind(gdi, "D3DKMTQueryAdapterInfo", [ctypes.POINTER(QueryAdapter)], ctypes.c_long)
        close_adapter = bind(gdi, "D3DKMTCloseAdapter", [ctypes.POINTER(CloseAdapter)], ctypes.c_long)
    except AttributeError:
        return (GpuInfo("Windows display", None, None, reason="The graphics capability API is unavailable."),)
    results, seen = [], set()
    for index in range(64):
        display = DisplayDevice()
        display.size = ctypes.sizeof(display)
        if not enumerate_devices(None, index, ctypes.byref(display), 0):
            break
        if display.flags & 8 or not display.flags & 1:  # Only attached, non-mirroring display adapters prove support.
            continue
        opened = OpenAdapter()
        opened.name = display.name
        status = open_adapter(ctypes.byref(opened))
        if status < 0:
            results.append(GpuInfo(display.description or display.name, None, None, reason=f"Capability query unavailable (0x{status & 0xffffffff:08X})."))
            continue
        try:
            identity = (opened.luid.low, opened.luid.high)
            if identity in seen:
                continue
            seen.add(identity)
            bits = wintypes.UINT()
            query = QueryAdapter(opened.adapter, 70, ctypes.cast(ctypes.byref(bits), ctypes.c_void_p), ctypes.sizeof(bits))
            status = query_adapter(ctypes.byref(query))
            if status < 0:
                results.append(GpuInfo(display.description or display.name, None, None, reason=f"Driver capability query failed (0x{status & 0xffffffff:08X})."))
            else:
                results.append(GpuInfo(display.description or display.name, bool(bits.value & 1), bool(bits.value & 2), bool(bits.value & 4)))
        finally:
            close_adapter(ctypes.byref(CloseAdapter(opened.adapter)))
    return tuple(results) or (GpuInfo("No visible display adapter", None, None, reason="No supported adapter could be verified."),)
