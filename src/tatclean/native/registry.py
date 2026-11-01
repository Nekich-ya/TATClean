from __future__ import annotations

import base64
import winreg
from typing import Any

RECORDING_VALUES = (
    (r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "HistoricalCaptureEnabled"),
)


def _encode(value: Any) -> Any:
    return {"binary": base64.b64encode(value).decode("ascii")} if isinstance(value, bytes) else value


def _decode(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"binary"}:
        return base64.b64decode(value["binary"], validate=True)
    return value


class RecordingRegistry:
    """Only the per-user background recording flag is changed; manual capture is untouched."""

    def read(self) -> list[dict[str, Any]]:
        entries = []
        for path, name in RECORDING_VALUES:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
                    value, kind = winreg.QueryValueEx(key, name)
                entries.append({"path": path, "name": name, "exists": True, "kind": kind, "value": _encode(value)})
            except FileNotFoundError:
                entries.append({"path": path, "name": name, "exists": False, "kind": winreg.REG_DWORD, "value": None})
        return entries

    @staticmethod
    def is_disabled(entries: list[dict[str, Any]]) -> bool:
        return all(entry["exists"] and entry["kind"] == winreg.REG_DWORD and entry["value"] == 0 for entry in entries)

    def disable(self) -> None:
        for path, name in RECORDING_VALUES:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 0)

    def restore(self, entries: list[dict[str, Any]]) -> None:
        if len(entries) != len(RECORDING_VALUES):
            raise ValueError("Invalid Game DVR backup.")
        for entry, (path, name) in zip(entries, RECORDING_VALUES, strict=True):
            if entry.get("path") != path or entry.get("name") != name:
                raise ValueError("Backup contains an unapproved registry path.")
            if entry["exists"]:
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key, name, 0, int(entry["kind"]), _decode(entry["value"]))
            else:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass  # Do not delete registry keys created/used by other software.
