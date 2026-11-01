from __future__ import annotations

import hashlib
import re
import stat
import winreg
from dataclasses import replace
from pathlib import Path

from tatclean.core.catalog import CATEGORIES
from tatclean.core.config import Settings
from tatclean.core.models import CleanupTarget, Risk, VolumeInfo
from tatclean.core.paths import (
    Locations,
    UnsafePathError,
    is_within,
    lstat,
    normalize,
    validate_ancestry,
)

INSTALLERS = ("*.msi", "*.exe", "*.msix", "*.msixbundle", "*.msp", "*.cab")
DUMPS = ("*.dmp", "*.mdmp")


def steam_library_paths() -> tuple[Path, ...]:
    """Read bounded Valve library metadata, never search installed game trees."""
    roots = set()
    for hive, key_path, value in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    ):
        try:
            with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ) as key:
                root = Path(winreg.QueryValueEx(key, value)[0])
            if root.is_absolute() and not str(root).startswith("\\\\"):
                validate_ancestry(root)
                roots.add(root)
        except (OSError, ValueError):
            continue
    for root in tuple(roots):
        metadata = root / "steamapps" / "libraryfolders.vdf"
        try:
            validate_ancestry(metadata)
            if lstat(metadata).st_size > 2 * 1024 * 1024:
                continue
            text = metadata.read_text(encoding="utf-8", errors="replace")
            for match in re.finditer(r'"path"\s*"((?:[^"\\]|\\.)*)"', text):
                path = Path(match.group(1).replace("\\\\", "\\").replace('\\"', '"'))
                if path.is_absolute() and not str(path).startswith("\\\\"):
                    roots.add(path)
        except (OSError, ValueError):
            continue
    return tuple(roots)


def build_drive_catalog(locations: Locations, settings: Settings, base: tuple[CleanupTarget, ...],
                        volumes: tuple[VolumeInfo, ...], *, libraries: tuple[Path, ...] | None = None) -> tuple[CleanupTarget, ...]:
    targets = [replace(target, volume_id=target.root.drive.upper()) for target in base]
    library_roots = steam_library_paths() if libraries is None else libraries
    volume_by_root = [(volume, Path(volume.root_path or volume.drive + "\\")) for volume in volumes if volume.cleanable]
    existing_temp = {normalize(target.root): target for target in targets if target.id in ("user-temp", "local-temp", "system-temp")}

    def add(root: Path, key: str, label: str, category: str, volume: VolumeInfo, *, patterns=("*",),
            exclude=(), caution=False, duplicate=False, age=24, processes=(), admin=False):
        if any(is_within(root, protected) for protected in locations.protected_subtrees()):
            return
        try:
            validate_ancestry(root)
            available = stat.S_ISDIR(lstat(root).st_mode)
            reason = "" if available else "Not a cache directory"
        except (OSError, UnsafePathError) as error:
            available, reason = False, str(error)
        targets.append(CleanupTarget(key, category, label, root,
                                     "Disposable paths only. Installed games, save data, download folders, Windows Installer and package stores are never scanned. Cache rebuilds or permanent loss of diagnostic copies are possible.",
                                     risk=Risk.CAUTION if caution else Risk.STANDARD,
                                     default_selected=not caution, minimum_age_hours=age, requires_admin=admin,
                                     processes=processes, patterns=patterns, available=available, unavailable_reason=reason,
                                     exclude_patterns=exclude, duplicate_only=duplicate, volume_id=volume.drive))

    for volume, root in volume_by_root:
        if not root.is_absolute() or str(root).startswith("\\\\"):
            continue
        prefix = hashlib.sha256((volume.drive + str(root)).encode()).hexdigest()[:10]
        candidates = (root / "Temp", root / "TMP")
        for directory in candidates:
            if normalize(directory) not in existing_temp:
                key = hashlib.sha256(str(directory).encode()).hexdigest()[:10]
                add(directory, f"drive-temp-{prefix}-{key}", f"{volume.drive} · {directory.name}", CATEGORIES[0], volume,
                    exclude=(*INSTALLERS, *DUMPS), caution=volume.drive_type == "Removable")
                existing_temp[normalize(directory)] = targets[-1] if targets and targets[-1].root == directory else None
        for library in (*library_roots, root / "SteamLibrary", root / "Steam", root / "Program Files (x86)" / "Steam"):
            if not is_within(library, root) or any(normalize(target.root) == normalize(library / "steamapps" / "shadercache") for target in targets):
                continue
            key = hashlib.sha256(str(library).encode()).hexdigest()[:10]
            add(library / "steamapps" / "shadercache", f"steam-shaders-{key}", f"{volume.drive} · Steam shader cache", CATEGORIES[3], volume,
                caution=True, age=72, processes=("steam.exe", "steamwebhelper.exe", *settings.game_executables))

    # Separate diagnostics/installers from routine Temp cleanup, including OS/profile Temp.
    for index, target in enumerate(tuple(targets)):
        if target.category != CATEGORIES[0]:
            continue
        targets[index] = replace(target, exclude_patterns=(*target.exclude_patterns, *INSTALLERS, *DUMPS))
        volume = next((item for item, _root in volume_by_root if item.drive == target.volume_id), None)
        if volume is None:
            # Isolated/test locations or a redirected profile still retain their real anchor.
            volume = VolumeInfo(target.volume_id or target.root.drive, "", "", "Fixed", 0, 0)
        suffix = hashlib.sha256(str(target.root).encode()).hexdigest()[:10]
        add(target.root, "temp-installers-" + suffix, f"{target.volume_id} · old installers in {target.label}", CATEGORIES[0], volume,
            patterns=INSTALLERS, caution=True, age=168, processes=("msiexec.exe", "setup.exe", "SetupHost.exe", "TiWorker.exe"), admin=target.requires_admin)
        add(target.root, "temp-dumps-" + suffix, f"{target.volume_id} · all old Temp crash dumps", CATEGORIES[4], volume,
            patterns=DUMPS, caution=True, age=168, processes=("WerFault.exe", "WerFaultSecure.exe"), admin=target.requires_admin)
        add(target.root, "duplicate-dumps-" + suffix, f"{target.volume_id} · duplicate dumps only (keep one)", CATEGORIES[4], volume,
            patterns=DUMPS, caution=True, duplicate=True, age=168, processes=("WerFault.exe", "WerFaultSecure.exe"), admin=target.requires_admin)
    crash = locations.local / "CrashDumps"
    if not any(target.duplicate_only and target.root == crash for target in targets):
        volume = next((item for item, _root in volume_by_root if item.drive == crash.drive.upper()),
                      VolumeInfo(crash.drive.upper(), "", "", "Fixed", 0, 0))
        add(crash, "duplicate-user-crash-dumps", "Duplicate user crash dumps only (keep one)", CATEGORIES[4], volume,
            patterns=DUMPS, caution=True, duplicate=True, age=168, processes=("WerFault.exe", "WerFaultSecure.exe"))
    if len({target.id for target in targets}) != len(targets):
        raise ValueError("Drive catalog contains duplicate target IDs.")
    # OS paths retain their actual known-folder locations; never synthesize C:\Windows.
    os_drive = locations.windows.drive.upper()
    return tuple(sorted(targets, key=lambda target: (target.volume_id != os_drive, target.category, target.id)))
