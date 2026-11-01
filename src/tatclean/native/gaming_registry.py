from __future__ import annotations

import base64
import ipaddress
import uuid
import winreg
from dataclasses import dataclass
from typing import Any

import psutil

from tatclean.core.gaming_models import NetworkInterface

TCP_ROOT = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
MMCSS_ROOT = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
HIVES = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}


@dataclass(frozen=True, slots=True)
class RegistrySetting:
    hive: str
    path: str
    name: str
    value: Any
    kind: int = winreg.REG_DWORD


def interface_guid(value: str) -> str:
    if not isinstance(value, str) or len(value) not in (36, 38):
        raise ValueError("A network interface must use an exact GUID, not a registry path.")
    parsed = uuid.UUID(value.strip("{}"))
    return "{" + str(parsed).upper() + "}"


def registry_settings(feature: str, *, enabled: bool = True, interface: str = "") -> tuple[RegistrySetting, ...]:
    if feature == "game_mode":
        return tuple(RegistrySetting("HKCU", r"Software\Microsoft\GameBar", name, int(enabled)) for name in ("AllowAutoGameMode", "AutoGameModeEnabled"))
    if feature == "hags":
        return (RegistrySetting("HKLM", r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode", 2 if enabled else 1),)
    if feature == "mmcss":
        # Microsoft clamps reserves below 10% to 20%; zero is not a valid 0% reserve.
        return (
            RegistrySetting("HKLM", MMCSS_ROOT, "SystemResponsiveness", 10),
            RegistrySetting("HKLM", MMCSS_ROOT + r"\Tasks\Games", "Scheduling Category", "High", winreg.REG_SZ),
            RegistrySetting("HKLM", MMCSS_ROOT + r"\Tasks\Games", "Priority", 8),
            RegistrySetting("HKLM", MMCSS_ROOT + r"\Tasks\Games", "GPU Priority", 8),
            RegistrySetting("HKLM", MMCSS_ROOT + r"\Tasks\Games", "SFIO Priority", "High", winreg.REG_SZ),
        )
    if feature == "nagle":
        path = TCP_ROOT + "\\" + interface_guid(interface)
        return tuple(RegistrySetting("HKLM", path, name, 1) for name in ("TcpAckFrequency", "TCPNoDelay"))
    if feature == "visual":
        return (
            RegistrySetting("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects", "VisualFXSetting", 2),
            RegistrySetting("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "EnableTransparency", 0),
            RegistrySetting("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced", "ListviewShadow", 0),
        )
    raise ValueError("This registry feature is not approved.")


def encode_data(value: Any) -> Any:
    return {"binary": base64.b64encode(value).decode("ascii")} if isinstance(value, bytes) else value


def decode_data(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) != {"binary"}:
            raise ValueError("Invalid registry binary encoding.")
        return base64.b64decode(value["binary"], validate=True)
    return value


class GamingRegistry:
    def _validate(self, settings: tuple[RegistrySetting, ...]) -> None:
        allowed = {(item.hive, item.path.casefold(), item.name.casefold()) for feature in ("game_mode", "hags", "mmcss", "visual") for item in registry_settings(feature)}
        for item in settings:
            identity = (item.hive, item.path.casefold(), item.name.casefold())
            if identity in allowed:
                continue
            prefix = TCP_ROOT + "\\"
            if item.hive == "HKLM" and item.path.startswith(prefix) and item.name in ("TcpAckFrequency", "TCPNoDelay"):
                guid = interface_guid(item.path[len(prefix):])
                if item.path != prefix + guid:
                    raise ValueError("Noncanonical network registry scope.")
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, item.path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
                    pass  # Never create a synthetic interface or modify the parent tree.
                continue
            raise ValueError("Unapproved gaming registry address.")

    def read(self, settings: tuple[RegistrySetting, ...]) -> list[dict[str, Any]]:
        self._validate(settings)
        result = []
        for setting in settings:
            item = {"hive": setting.hive, "path": setting.path, "name": setting.name,
                    "exists": False, "kind": setting.kind, "data": None}
            try:
                with winreg.OpenKey(HIVES[setting.hive], setting.path, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_WOW64_64KEY) as key:
                    data, kind = winreg.QueryValueEx(key, setting.name)
                item.update(exists=True, kind=kind, data=encode_data(data))
            except FileNotFoundError:
                pass
            result.append(item)
        return result

    def expected(self, settings: tuple[RegistrySetting, ...]) -> list[dict[str, Any]]:
        self._validate(settings)
        return [{"hive": item.hive, "path": item.path, "name": item.name, "exists": True,
                 "kind": item.kind, "data": encode_data(item.value)} for item in settings]

    def write(self, settings: tuple[RegistrySetting, ...], values: list[dict[str, Any]]) -> None:
        self._validate(settings)
        if len(settings) != len(values):
            raise ValueError("Incomplete registry snapshot.")
        # Validate every entry before touching the first value.
        for setting, item in zip(settings, values, strict=True):
            if (item.get("hive"), item.get("path"), item.get("name")) != (setting.hive, setting.path, setting.name):
                raise ValueError("Registry snapshot scope does not match the approved feature.")
            if not isinstance(item.get("exists"), bool) or not isinstance(item.get("kind"), int):
                raise ValueError("Invalid registry snapshot type.")
            if item["exists"]:
                decode_data(item["data"])
        for setting, item in zip(settings, values, strict=True):
            if item["exists"]:
                with winreg.CreateKeyEx(HIVES[setting.hive], setting.path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
                    winreg.SetValueEx(key, setting.name, 0, item["kind"], decode_data(item["data"]))
            else:
                try:
                    with winreg.OpenKey(HIVES[setting.hive], setting.path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
                        winreg.DeleteValue(key, setting.name)
                except FileNotFoundError:
                    pass

    def interfaces(self) -> tuple[NetworkInterface, ...]:
        stats = psutil.net_if_stats()
        result = []
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, TCP_ROOT, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as root:
                names = [winreg.EnumKey(root, index) for index in range(winreg.QueryInfoKey(root)[0])]
        except OSError:
            return ()
        for name in names:
            try:
                guid = interface_guid(name)
                path = TCP_ROOT + "\\" + guid
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                    addresses = []
                    for value in ("IPAddress", "DhcpIPAddress"):
                        try:
                            data, _kind = winreg.QueryValueEx(key, value)
                            for candidate in data if isinstance(data, list) else [data]:
                                address = ipaddress.IPv4Address(candidate)
                                if not address.is_unspecified:
                                    addresses.append(str(address))
                        except (FileNotFoundError, ipaddress.AddressValueError, TypeError):
                            continue
                friendly = guid
                connection = r"SYSTEM\CurrentControlSet\Control\Network\{4D36E972-E325-11CE-BFC1-08002BE10318}" + "\\" + guid + r"\Connection"
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, connection) as key:
                        friendly = str(winreg.QueryValueEx(key, "Name")[0])
                except OSError:
                    pass
                status = stats.get(friendly)
                if addresses:
                    result.append(NetworkInterface(guid, friendly, tuple(dict.fromkeys(addresses)), bool(status and status.isup)))
            except (OSError, ValueError):
                continue
        return tuple(sorted(result, key=lambda item: (not item.active, item.name.casefold())))
