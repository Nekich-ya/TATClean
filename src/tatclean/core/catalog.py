from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import replace
from pathlib import Path

from tatclean.core.config import Settings
from tatclean.core.models import CleanupTarget, Risk
from tatclean.core.paths import (
    Locations,
    UnsafePathError,
    filesystem_path,
    is_reparse,
    is_within,
    lstat,
    normalize,
    validate_ancestry,
)

CATEGORIES = (
    "Temporary files", "Windows & update caches", "Browsers & applications",
    "Games & development engines", "Crash reports & logs",
)


def _local_candidate(root: Path) -> bool:
    if not root.is_absolute() or str(root).startswith("\\\\"):
        return False
    if os.name == "nt":
        from tatclean.native.system import is_fixed_local_path
        return is_fixed_local_path(root)
    return True


def _profiles(root: Path) -> tuple[str, ...]:
    names = {"Default"}
    try:
        if not _local_candidate(root):
            return ("Default",)
        validate_ancestry(root)
        with os.scandir(filesystem_path(root)) as entries:
            for entry in entries:
                if (
                    (entry.name == "Guest Profile" or entry.name.startswith("Profile "))
                    and entry.is_dir(follow_symlinks=False)
                    and not is_reparse(lstat(root / entry.name))
                ):
                    names.add(entry.name)
    except (OSError, UnsafePathError):
        pass
    return tuple(sorted(names))


def build_catalog(locations: Locations, settings: Settings) -> tuple[CleanupTarget, ...]:
    """Explicit disposable directories only. No whole-profile or whole-drive scans."""
    targets: list[CleanupTarget] = []

    def add(
        key: str, category: str, label: str, root: Path, description: str,
        *, caution: bool = False, admin: bool = False, age: int = 24,
        processes: tuple[str, ...] = (), services: tuple[str, ...] = (),
        patterns: tuple[str, ...] = ("*",), recursive: bool = True,
        selected: bool | None = None,
    ) -> None:
        available, reason = False, ""
        try:
            if not _local_candidate(root):
                reason = "Network/removable paths are not supported"
            else:
                validate_ancestry(root)
                metadata = lstat(root)
                available = stat.S_ISDIR(metadata.st_mode) and not is_reparse(metadata)
                reason = "" if available else "Not a supported cache directory"
        except FileNotFoundError:
            reason = "Not installed / no cache"
        except (OSError, UnsafePathError) as exc:
            reason = str(exc)
        targets.append(CleanupTarget(
            id=key, category=category, label=label, root=root,
            description=description, risk=Risk.CAUTION if caution else Risk.STANDARD,
            default_selected=not caution if selected is None else selected,
            minimum_age_hours=age, requires_admin=admin, processes=processes,
            services=services, patterns=patterns, recursive=recursive, available=available,
            unavailable_reason=reason,
        ))

    # Reject a misconfigured TEMP pointing at Documents, a drive root, or another broad folder.
    temp = locations.temp
    if (not _local_candidate(temp)
            or temp.name.casefold() not in ("temp", "tmp", "temporary files")
            or normalize(temp) in {normalize(path) for path in locations.protected_exact()}
            or any(is_within(temp, path) for path in locations.protected_subtrees())):
        temp = locations.local / "Temp"
    is_system_temp = normalize(temp) == normalize(locations.windows / "Temp")
    add("user-temp", CATEGORIES[0], "User temporary files", temp,
        "Old temporary files only. Recent, locked, linked and protected application files are preserved.",
        admin=is_system_temp, age=48 if is_system_temp else 24)
    if normalize(temp) != normalize(locations.local / "Temp"):
        add("local-temp", CATEGORIES[0], "Local AppData temporary files", locations.local / "Temp",
            "The standard per-user Windows temporary folder.")
    if not is_system_temp:
        add("system-temp", CATEGORIES[0], "Windows temporary files", locations.windows / "Temp",
            "Administrator access required. Files younger than 48 hours are always preserved.", admin=True, age=48)
    add("prefetch", CATEGORIES[1], "Windows Prefetch", locations.windows / "Prefetch",
        "Opt-in: cached launch traces help Windows start applications faster. Clearing them is not a general speed improvement.",
        caution=True, admin=True, age=168, patterns=("*.pf", "*.db"))
    add("windows-update", CATEGORIES[1], "Windows Update download cache",
        locations.windows / "SoftwareDistribution" / "Download",
        "Only downloaded update payloads; never DataStore. Cleanup requires administrator rights and both Windows Update and BITS to already be stopped. Services are never stopped automatically.",
        caution=True, admin=True, age=72, services=("wuauserv", "BITS"),
        processes=("TiWorker.exe", "TrustedInstaller.exe", "MoUsoCoreWorker.exe", "UsoClient.exe", "wuauclt.exe", "SetupHost.exe", "DismHost.exe", "WaaSMedicAgent.exe"))

    renderers = (
        "UnrealEditor.exe", "UE4Editor.exe", "Unity.exe", "UnityShaderCompiler.exe",
        "ShaderCompileWorker.exe", "RobloxStudioBeta.exe", "blender.exe",
        *settings.game_executables,
    )
    for key, label, relative in (
        ("directx", "DirectX shader cache", ("D3DSCache",)),
        ("nvidia-dx", "NVIDIA DirectX shader cache", ("NVIDIA", "DXCache")),
        ("nvidia-gl", "NVIDIA OpenGL shader cache", ("NVIDIA", "GLCache")),
        ("amd-dx", "AMD DirectX shader cache", ("AMD", "DxCache")),
        ("amd-dxc", "AMD DX compiler cache", ("AMD", "DxcCache")),
        ("amd-gl", "AMD OpenGL shader cache", ("AMD", "GLCache")),
    ):
        add(key, CATEGORIES[1], label, locations.local.joinpath(*relative),
            "Opt-in: shader caches will be rebuilt and can cause first-launch stutter. Known running games/renderers and locked files are skipped.",
            caution=True, processes=renderers)

    for browser, base, executable in (
        ("Chrome", locations.local / "Google" / "Chrome" / "User Data", "chrome.exe"),
        ("Edge", locations.local / "Microsoft" / "Edge" / "User Data", "msedge.exe"),
        ("Brave", locations.local / "BraveSoftware" / "Brave-Browser" / "User Data", "brave.exe"),
        ("Edge WebView2", locations.local / "Microsoft" / "EdgeWebView" / "User Data", "msedgewebview2.exe"),
    ):
        prefix = browser.lower().replace(" ", "-")
        for profile in _profiles(base):
            profile_id = hashlib.sha256(profile.encode()).hexdigest()[:10]
            for cache in ("Cache", "Code Cache", "GPUCache", "DawnCache", "GrShaderCache"):
                add(f"{prefix}-{profile_id}-{cache.lower().replace(' ', '-')}", CATEGORIES[2],
                    f"{browser} · {profile} · {cache}", base / profile / cache,
                    "Disposable cache only. Cookies, saved passwords, history, extensions, sessions and offline profile storage are never targeted.",
                    processes=(executable,))
        for cache in ("ShaderCache", "GrShaderCache", "GraphiteDawnCache"):
            add(f"{prefix}-shared-{cache.lower()}", CATEGORIES[2], f"{browser} · shared {cache}",
                base / cache, "Shared rendering cache; rebuilt by the browser.", processes=(executable,))
        add(f"{prefix}-crashpad", CATEGORIES[4], f"{browser} crash reports", base / "Crashpad" / "reports",
            "Old browser crash dumps, not profile data.", processes=(executable,), age=168,
            patterns=("*.dmp", "*.mdmp", "*.log", "*.tmp"))

    for channel, executable in (
        ("discord", "Discord.exe"), ("discordcanary", "DiscordCanary.exe"), ("discordptb", "DiscordPTB.exe"),
    ):
        for cache in ("Cache", "Code Cache", "GPUCache"):
            add(f"{channel}-{cache.lower().replace(' ', '-')}", CATEGORIES[2], f"{channel.title()} · {cache}",
                locations.roaming / channel / cache,
                "Electron caches only. Accounts, local storage, settings and downloaded user files are preserved.", processes=(executable,))
        add(f"{channel}-logs", CATEGORIES[4], f"{channel.title()} logs", locations.roaming / channel / "logs",
            "Old diagnostic logs.", age=168, processes=(executable,), patterns=("*.log", "*.dmp", "*.tmp"))
        add(f"{channel}-crash", CATEGORIES[4], f"{channel.title()} crash reports",
            locations.roaming / channel / "Crashpad" / "reports", "Old crash dumps.", age=168,
            processes=(executable,), patterns=("*.dmp", "*.mdmp", "*.log", "*.tmp"))

    telegram = locations.roaming / "Telegram Desktop"
    for key, relative in (
        ("telegram-cache", ("tdata", "user_data", "cache")),
        ("telegram-temp", ("tdata", "temp")),
        ("telegram-user-temp", ("tdata", "user_data", "temp")),
    ):
        add(key, CATEGORIES[2], "Telegram Desktop · " + key.removeprefix("telegram-"),
            telegram.joinpath(*relative),
            "Opt-in: disposable Telegram cache/temp folders only; media may need re-downloading. The tdata account store, key files and user download folder are never cleaned.",
            caution=True, processes=("Telegram.exe",))
    add("telegram-logs", CATEGORIES[4], "Telegram Desktop diagnostic log folder", telegram / "logs",
        "Dedicated diagnostic log folder only, when present; no traversal of the tdata account store.",
        age=168, processes=("Telegram.exe",), patterns=("*.txt", "*.log", "*.dmp", "*.tmp"))

    for relative in (("Apple Computer", "Safari", "Cache"), ("WebKit", "Cache")):
        add("webkit-" + "-".join(relative).lower().replace(" ", "-"), CATEGORIES[2],
            "WebKit · " + " / ".join(relative), locations.local.joinpath(*relative),
            "Explicit WebKit/Safari cache directories, when installed.",
            processes=("Safari.exe", "WebKitNetworkProcess.exe", "WebKitWebProcess.exe"))
    add("safari-cache-db", CATEGORIES[2], "Safari / WebKit cache database", locations.local / "Apple Computer" / "Safari",
        "Only Cache.db and its disposable companion files. No browser history or bookmarks.",
        recursive=False, patterns=("Cache.db", "Cache.db-*"),
        processes=("Safari.exe", "WebKitNetworkProcess.exe", "WebKitWebProcess.exe"))

    roblox_processes = ("RobloxPlayerBeta.exe", "RobloxStudioBeta.exe", "RobloxPlayerInstaller.exe", "RobloxStudioInstaller.exe")
    for key, relative, label in (
        ("roblox-logs", locations.local / "Roblox" / "logs", "Roblox logs"),
        ("roblox-http", locations.local / "Roblox" / "http", "Roblox HTTP asset cache"),
        ("roblox-cache", locations.local / "Roblox" / "Cache", "Roblox cache"),
        ("roblox-temp", temp / "Roblox", "Roblox temporary cache"),
    ):
        add(key, CATEGORIES[3], label, relative,
            "Only disposable Roblox cache/log directories. Installed Versions, saved settings and projects are not touched.",
            processes=roblox_processes, patterns=("*.log", "*.dmp", "*.tmp", "*.txt") if key.endswith("logs") else ("*",))

    unreal_processes = ("UnrealEditor.exe", "UE4Editor.exe", "UnrealEditor-Cmd.exe", "UE4Editor-Cmd.exe", "ShaderCompileWorker.exe", "ZenServer.exe")
    unreal_roots = {locations.local / "UnrealEngine" / "Common" / "DerivedDataCache"}
    try:
        unreal_base = locations.local / "UnrealEngine"
        if not _local_candidate(unreal_base):
            raise UnsafePathError("Nonlocal engine directories are unsupported")
        validate_ancestry(unreal_base)
        with os.scandir(filesystem_path(unreal_base)) as entries:
            for entry in entries:
                if entry.name[:1].isdigit() and entry.is_dir(follow_symlinks=False):
                    unreal_roots.add(unreal_base / entry.name / "DerivedDataCache")
    except (OSError, UnsafePathError):
        pass
    for root in sorted(unreal_roots):
        key = hashlib.sha256(str(root).encode()).hexdigest()[:10]
        add(f"unreal-ddc-{key}", CATEGORIES[3], f"Unreal Engine · {root.parent.name} derived data", root,
            "Opt-in: derived assets will be regenerated. Project folders and the live Zen data store are never targeted.",
            caution=True, age=72, processes=unreal_processes)
    for cache in ("ShaderCache", "Common/ShaderCache"):
        add("unreal-" + cache.lower().replace("/", "-"), CATEGORIES[3], f"Unreal Engine · {cache}",
            locations.local / "UnrealEngine" / cache,
            "Opt-in: shader recompilation may take time. Close Unreal Editor and shader workers first.",
            caution=True, age=72, processes=unreal_processes)
    for cache in ("ShaderCache", "cache/shaders", "cache/shader", "Caches/ShaderCache"):
        add("unity-" + cache.lower().replace("/", "-"), CATEGORIES[3], f"Unity · {cache}",
            locations.local / "Unity" / cache,
            "Opt-in: disposable user-level shader cache only; project Library and Asset folders are never touched.",
            caution=True, age=72, processes=("Unity.exe", "UnityShaderCompiler.exe"))

    add("user-crash-dumps", CATEGORIES[4], "Windows user crash dumps", locations.local / "CrashDumps",
        "Dumps older than seven days. Retain them if troubleshooting a crash.", age=168,
        processes=("WerFault.exe", "WerFaultSecure.exe"), patterns=("*.dmp", "*.mdmp", "*.log", "*.tmp"))
    for directory in ("ReportArchive", "ReportQueue"):
        add("wer-" + directory.lower(), CATEGORIES[4], f"Windows Error Reporting · {directory}",
            locations.program_data / "Microsoft" / "Windows" / "WER" / directory,
            "Opt-in: old diagnostic reports only. Queued reports may not yet have been submitted.",
            caution=True, admin=True, age=168, processes=("WerFault.exe", "WerFaultSecure.exe"))

    # An unavailable entry remains visible, showing the suite's coverage without inventing paths.
    protected = {normalize(path) for path in locations.protected_exact()}
    ids: set[str] = set()
    result = []
    for target in targets:
        if target.id in ids or normalize(target.root) in protected or target.root.parent == target.root:
            raise UnsafePathError(f"Unsafe or duplicate catalog entry: {target.id}")
        ids.add(target.id)
        result.append(replace(target, root=Path(os.path.abspath(target.root))))
    return tuple(result)
