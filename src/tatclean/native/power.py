from __future__ import annotations

import ctypes
import re
import uuid
from ctypes import wintypes
from typing import Any

from tatclean.core.tasks import TaskContext
from tatclean.native.system import GUID, system_directory
from tatclean.native.winapi import bind, library

ULTIMATE_TEMPLATE = "e9a42b02-d5df-448d-aa00-03f14749eb61"
GUID_PATTERN = re.compile(r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b")


def power_guid(value: str) -> str:
    if not isinstance(value, str) or not GUID_PATTERN.fullmatch(value):
        raise ValueError("Power schemes require an exact GUID.")
    return str(uuid.UUID(value))


class PowerPlans:
    def __init__(self, runner: Any):
        self.runner = runner
        self.executable = system_directory() / "powercfg.exe"
        self._fresh: set[str] = set()

    def _run(self, arguments: tuple[str, ...], context: TaskContext) -> str:
        output = self.runner.run((str(self.executable), *arguments), context, timeout=60)
        if output.returncode:
            raise OSError(f"powercfg failed (exit {output.returncode}): {output.text}")
        return output.text

    def active(self, context: TaskContext) -> str:
        text = self._run(("/getactivescheme",), context)
        guids = GUID_PATTERN.findall(text)
        if len(guids) != 1:
            raise ValueError("The active power-scheme GUID could not be verified.")
        return power_guid(guids[0])

    def schemes(self, context: TaskContext) -> frozenset[str]:
        text = self._run(("/list",), context)
        return frozenset(power_guid(value) for value in GUID_PATTERN.findall(text))

    def description(self, scheme: str) -> str:
        identifier = GUID.from_buffer_copy(uuid.UUID(power_guid(scheme)).bytes_le)
        read = bind(library("powrprof"), "PowerReadDescription",
                    [wintypes.HKEY, ctypes.POINTER(GUID), ctypes.c_void_p, ctypes.c_void_p,
                     ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD)
        size = wintypes.DWORD()
        code = read(None, ctypes.byref(identifier), None, None, None, ctypes.byref(size))
        if code not in (0, 234) or not 2 <= size.value <= 8192:
            raise OSError("The power plan ownership marker could not be read.")
        buffer = ctypes.create_string_buffer(size.value)
        if read(None, ctypes.byref(identifier), None, None, buffer, ctypes.byref(size)):
            raise OSError("The power plan description could not be verified.")
        return buffer.raw[:size.value].decode("utf-16-le").rstrip("\x00")

    @staticmethod
    def _marker(marker: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{32}", marker):
            raise ValueError("Invalid application power-plan ownership marker.")
        return "TATClean-owned:" + marker

    def create_ultimate(self, destination: str, marker: str, context: TaskContext) -> None:
        guid = power_guid(destination)
        if guid in self.schemes(context):
            raise ValueError("Refusing to replace an existing power scheme.")
        self._run(("-duplicatescheme", ULTIMATE_TEMPLATE, guid), context)
        if guid not in self.schemes(context):
            raise OSError("Windows did not create the requested Ultimate Performance scheme.")
        self._fresh.add(guid)
        self._run(("-changename", guid, "TAT Clean - Ultimate Performance", self._marker(marker)), context)

    def activate(self, scheme: str, context: TaskContext) -> None:
        guid = power_guid(scheme)
        if guid not in self.schemes(context):
            raise ValueError("The saved power plan no longer exists. It will not be replaced with an assumed default.")
        self._run(("-setactive", guid), context)
        if self.active(context) != guid:
            raise OSError("Windows did not activate the requested plan; this system may restrict power schemes.")

    def delete_owned(self, scheme: str, marker: str, context: TaskContext) -> None:
        guid = power_guid(scheme)
        if guid == ULTIMATE_TEMPLATE:
            raise ValueError("The Microsoft template cannot be deleted.")
        if guid not in self.schemes(context):
            return
        if self.description(guid) != self._marker(marker) and guid not in self._fresh:
            raise ValueError("This plan does not carry the saved application ownership marker; it will not be deleted.")
        if self.active(context) == guid:
            raise ValueError("Activate the original plan before removing the application-owned plan.")
        self._run(("-delete", guid), context)
        self._fresh.discard(guid)
