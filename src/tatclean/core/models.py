from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from tatclean.core.gaming_models import GamingSnapshot


class Risk(StrEnum):
    STANDARD = "Standard"
    CAUTION = "Opt-in"


class SkipReason(StrEnum):
    RECENT = "Recent file"
    LOCK_FILE = "Lock or profile data"
    REPARSE = "Link / reparse point"
    HARD_LINK = "Hard-linked file"
    PERMISSION = "Access denied"
    LOCKED = "In use / locked"
    CHANGED = "Changed since scan"
    OUTSIDE_SCOPE = "Outside approved scope"
    FILTERED = "Not a cache file"
    ACTIVE_PROCESS = "Application running"
    SERVICE_BUSY = "Windows Update service active"
    MISSING = "No longer present"
    ERROR = "I/O error"


@dataclass(frozen=True, slots=True)
class CleanupTarget:
    id: str
    category: str
    label: str
    root: Path
    description: str
    risk: Risk = Risk.STANDARD
    default_selected: bool = True
    minimum_age_hours: int = 24
    requires_admin: bool = False
    processes: tuple[str, ...] = ()
    services: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ("*",)
    recursive: bool = True
    available: bool = True
    unavailable_reason: str = ""
    exclude_patterns: tuple[str, ...] = ()
    duplicate_only: bool = False
    volume_id: str = ""

    def matches(self, path: Path) -> bool:
        """Matching never broadens a target beyond its fixed root."""
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            return False
        if not relative.parts or (not self.recursive and len(relative.parts) != 1):
            return False
        name = path.name.casefold()
        return (any(fnmatch.fnmatchcase(name, pattern.casefold()) for pattern in self.patterns)
                and not any(fnmatch.fnmatchcase(name, pattern.casefold()) for pattern in self.exclude_patterns))


@dataclass(frozen=True, slots=True)
class FileRecord:
    path: str
    target_id: str
    size: int
    mtime_ns: int
    inode: int
    device: int
    nlinks: int
    content_hash: str = ""
    keeper_json: str = ""


@dataclass(frozen=True, slots=True)
class DeleteOutcome:
    removed: bool
    reason: SkipReason | None = None
    message: str = ""


@dataclass(slots=True)
class TargetSummary:
    target_id: str
    examined: int = 0
    files: int = 0
    bytes: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    status: str = "Not scanned"

    def skip(self, reason: SkipReason, count: int = 1) -> None:
        self.skipped[reason.value] = self.skipped.get(reason.value, 0) + count


@dataclass(frozen=True, slots=True)
class ScanReport:
    scan_id: str
    manifest_path: Path
    created_at: float
    target_ids: tuple[str, ...]
    summaries: tuple[TargetSummary, ...]
    files: int
    bytes: int
    digest: str
    completed: bool
    duration: float
    duplicate_files: int = 0
    duplicate_bytes: int = 0


@dataclass(slots=True)
class CleanReport:
    removed_files: int = 0
    removed_bytes: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    duration: float = 0.0

    def skip(self, reason: SkipReason, count: int = 1, error: str = "") -> None:
        self.skipped[reason.value] = self.skipped.get(reason.value, 0) + count
        if error and len(self.errors) < 50:
            self.errors.append(error)

    @property
    def skipped_files(self) -> int:
        return sum(self.skipped.values())


@dataclass(frozen=True, slots=True)
class TaskProgress:
    message: str
    percent: int | None = None
    current: int = 0
    total: int = 0
    bytes: int = 0
    target_id: str = ""


@dataclass(frozen=True, slots=True)
class TaskFailure:
    message: str
    details: str
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class ActionResult:
    action_id: str
    title: str
    success: bool
    message: str
    details: str = ""
    reboot_required: bool = False
    cancelled: bool = False
    duration: float = 0.0


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    pid: int
    name: str
    create_time: float
    memory: int
    priority: int
    username: str
    game: bool
    foreground: bool
    manageable: bool
    protected_reason: str = ""


@dataclass(frozen=True, slots=True)
class ServiceInfo:
    name: str
    title: str
    description: str
    installed: bool
    state: str
    start_type: int
    backed_up: bool
    error: str = ""


@dataclass(frozen=True, slots=True)
class TweakSnapshot:
    services: tuple[ServiceInfo, ...]
    recording_disabled: bool
    recording_backed_up: bool
    recording_error: str = ""
    gaming: GamingSnapshot | None = None


@dataclass(frozen=True, slots=True)
class VolumeInfo:
    drive: str
    label: str
    filesystem: str
    media: str
    total: int
    free: int
    drive_type: str = "Fixed"
    cleanable: bool = True
    reason: str = ""
    root_path: str = ""


@dataclass(frozen=True, slots=True)
class Metrics:
    cpu_percent: float
    memory_total: int
    memory_used: int
    memory_available: int
    drive_total: int
    drive_free: int
    uptime: float
    process_count: int


@dataclass(frozen=True, slots=True)
class Initialization:
    targets: tuple[CleanupTarget, ...]
    volumes: tuple[VolumeInfo, ...]
    system_details: dict[str, str]
    mounted: tuple[VolumeInfo, ...] = ()


def format_bytes(value: int | float) -> str:
    amount = max(0.0, float(value))
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:,.0f} {unit}" if unit == "B" else f"{amount:,.1f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{days}d {hours}h" if days else f"{hours}h {minutes}m"
