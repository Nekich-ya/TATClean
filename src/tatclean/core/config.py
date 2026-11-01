from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_GAMES = (
    "RobloxPlayerBeta.exe",
    "FortniteClient-Win64-Shipping.exe",
    "VALORANT-Win64-Shipping.exe",
    "cs2.exe",
    "dota2.exe",
    "r5apex.exe",
    "Minecraft.Windows.exe",
    "RocketLeague.exe",
    "GTA5.exe",
    "Cyberpunk2077.exe",
    "overwatch.exe",
)

# These processes cannot become game targets, even through a modified settings file.
PROTECTED_PROCESSES = frozenset(
    name.casefold()
    for name in (
        "System", "Registry", "Idle", "smss.exe", "csrss.exe", "wininit.exe",
        "services.exe", "lsass.exe", "lsaiso.exe", "svchost.exe", "winlogon.exe",
        "fontdrvhost.exe", "dwm.exe", "audiodg.exe", "sihost.exe", "taskhostw.exe",
        "explorer.exe", "MsMpEng.exe", "MsSense.exe", "SecurityHealthService.exe",
        "SecurityHealthSystray.exe", "TrustedInstaller.exe", "TiWorker.exe",
        "SearchIndexer.exe", "SearchHost.exe", "SearchApp.exe", "spoolsv.exe",
        "MemCompression", "WmiPrvSE.exe", "conhost.exe", "dllhost.exe", "WUDFHost.exe",
    )
)


def validate_games(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if len(values) > 128:
        raise ValueError("At most 128 game executable names are supported.")
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("Game executable names must be text.")
        value = value.strip()
        if not value:
            continue
        if (
            not re.fullmatch(r"[\w .()+-]{1,180}\.exe", value, flags=re.IGNORECASE)
            or value.casefold() in PROTECTED_PROCESSES
        ):
            raise ValueError(f"Not an allowed game executable name: {value!r}")
        if value.casefold() not in seen:
            result.append(value)
            seen.add(value.casefold())
    return tuple(result)


@dataclass(frozen=True, slots=True)
class Settings:
    minimum_age_hours: int = 24
    audit_retention_days: int = 14
    restore_priority_on_exit: bool = True
    game_executables: tuple[str, ...] = DEFAULT_GAMES
    window_width: int = 1320
    window_height: int = 900

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        def integer(key: str, default: int, minimum: int, maximum: int) -> int:
            value = data.get(key, default)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key} must be an integer.")
            if not minimum <= value <= maximum:
                raise ValueError(f"{key} must be between {minimum} and {maximum}.")
            return value

        restore = data.get("restore_priority_on_exit", True)
        if not isinstance(restore, bool):
            raise ValueError("restore_priority_on_exit must be a boolean.")
        games = data.get("game_executables", list(DEFAULT_GAMES))
        if not isinstance(games, (list, tuple)):
            raise ValueError("game_executables must be a list.")
        return cls(
            minimum_age_hours=integer("minimum_age_hours", 24, 1, 720),
            audit_retention_days=integer("audit_retention_days", 14, 7, 90),
            restore_priority_on_exit=restore,
            game_executables=validate_games(games),
            window_width=integer("window_width", 1320, 1100, 7680),
            window_height=integer("window_height", 900, 740, 4320),
        )


def atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.load_warning = ""

    def load(self) -> Settings:
        with self._lock:
            if not self.path.exists():
                return Settings()
            try:
                if self.path.stat().st_size > 65536:
                    raise ValueError("Settings file exceeds the 64 KB limit.")
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("schema_version", 1) != 1:
                    raise ValueError("Unsupported settings format.")
                return Settings.from_dict(data)
            except (OSError, ValueError, TypeError) as exc:
                # Preserve unfamiliar/corrupt input rather than overwriting it silently.
                backup = self.path.with_name(f"settings.corrupt-{time.time_ns()}.json")
                try:
                    self.path.rename(backup)
                    location = str(backup)
                except OSError:
                    location = str(self.path)
                self.load_warning = f"Settings could not be loaded: {exc}. Original: {location}"
                return Settings()

    def save(self, settings: Settings) -> None:
        with self._lock:
            validated = Settings.from_dict(asdict(settings))
            atomic_json_write(self.path, {"schema_version": 1, **asdict(validated)})
