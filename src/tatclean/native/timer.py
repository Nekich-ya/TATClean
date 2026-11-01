from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from tatclean.core.gaming_models import TimerInfo
from tatclean.native.winapi import bind, library


def check_ntstatus(status: int, operation: str) -> None:
    if status < 0:
        raise OSError(f"{operation} failed (NTSTATUS 0x{status & 0xffffffff:08X}).")


class TimerResolution:
    """Own exactly one process request; never release another application's request."""

    def __init__(self):
        dll = library("ntdll")
        self._query = bind(dll, "NtQueryTimerResolution", [ctypes.POINTER(wintypes.ULONG)] * 3, ctypes.c_long)
        self._set = bind(dll, "NtSetTimerResolution", [wintypes.ULONG, ctypes.c_ubyte, ctypes.POINTER(wintypes.ULONG)], ctypes.c_long)
        self._requested: int | None = None
        self._lock = threading.RLock()

    def snapshot(self) -> TimerInfo:
        with self._lock:
            coarse, fine, current = wintypes.ULONG(), wintypes.ULONG(), wintypes.ULONG()
            check_ntstatus(self._query(ctypes.byref(coarse), ctypes.byref(fine), ctypes.byref(current)), "Timer query")
            return TimerInfo(coarse.value // 10, fine.value // 10, current.value // 10, self._requested)

    def _request(self, microseconds: int, acquire: bool) -> None:
        actual = wintypes.ULONG()
        check_ntstatus(self._set(microseconds * 10, int(acquire), ctypes.byref(actual)), "Timer request" if acquire else "Timer release")

    def set(self, microseconds: int) -> TimerInfo:
        if isinstance(microseconds, bool) or microseconds not in (500, 1000):
            raise ValueError("Only 0.5 ms and 1.0 ms timer requests are supported.")
        with self._lock:
            supported = self.snapshot()
            if not supported.finest_us <= microseconds <= supported.coarsest_us:
                raise ValueError(f"This system supports {supported.finest_us / 1000:g}–{supported.coarsest_us / 1000:g} ms, not the selected timer request.")
            if self._requested == microseconds:
                return self.snapshot()
            previous = self._requested
            if previous is not None:
                self._request(previous, False)
                self._requested = None
            try:
                self._request(microseconds, True)
                self._requested = microseconds
            except Exception:
                if previous is not None:
                    self._request(previous, True)
                    self._requested = previous
                raise
            return self.snapshot()

    def release(self) -> TimerInfo:
        with self._lock:
            if self._requested is not None:
                self._request(self._requested, False)
                self._requested = None
            return self.snapshot()
