from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GamingRequest:
    features: tuple[str, ...]
    timer_us: int = 500
    interface_ids: tuple[str, ...] = ()
    hags_enabled: bool = True
    game_mode_enabled: bool = True

    def validate(self) -> None:
        allowed = {"timer", "nagle", "mmcss", "hags", "game_mode", "power", "visual"}
        if not self.features or len(set(self.features)) != len(self.features):
            raise ValueError("Select at least one unique gaming tweak.")
        if not set(self.features) <= allowed:
            raise ValueError("Unknown gaming tweak.")
        if isinstance(self.timer_us, bool) or self.timer_us not in (500, 1000):
            raise ValueError("Timer resolution must be 0.5 ms or 1.0 ms.")
        if not isinstance(self.hags_enabled, bool) or not isinstance(self.game_mode_enabled, bool):
            raise ValueError("Gaming flags must be booleans.")
        if "nagle" in self.features and not 1 <= len(self.interface_ids) <= 32:
            raise ValueError("Choose between 1 and 32 IPv4 network interfaces.")
        if len(set(self.interface_ids)) != len(self.interface_ids):
            raise ValueError("Network interface selection contains duplicates.")


@dataclass(frozen=True, slots=True)
class GamingFeature:
    key: str
    title: str
    description: str
    warning: str
    reboot: bool = False
    default_selected: bool = False


GAMING_FEATURES = (
    GamingFeature("timer", "Timer resolution", "Request 0.5 ms or 1.0 ms through NtSetTimerResolution.",
                  "Higher timer frequency can increase CPU activity, heat and battery use. Modern Windows limits per-process/occluded timer effects; this does not force a game's frame pacing or guarantee lower input lag."),
    GamingFeature("nagle", "TCP / Nagle tuning", "Set TcpAckFrequency=1 and TCPNoDelay=1 on selected IPv4 interfaces.",
                  "TCP-only experimentation: many games use UDP and will not benefit. Small-packet overhead can increase. No bandwidth, ping or latency improvement is guaranteed; adapters/VPN software may need restarting.", True),
    GamingFeature("mmcss", "MMCSS Games profile", "Set the Games scheduling profile and the minimum recognized SystemResponsiveness reserve.",
                  "Applies only to threads registered with MMCSS. High scheduling categories can reduce background/audio headroom. GPU Priority is a policy field, not a proven GPU speed boost. No process is given REALTIME priority.", True),
    GamingFeature("hags", "Hardware GPU scheduling", "Toggle HAGS only when an eligible display driver reports support.",
                  "Requires supported hardware/driver and Windows 10 2004+ or Windows 11. Restart required. Depending on the game/driver, frame pacing can improve or worsen.", True),
    GamingFeature("game_mode", "Windows Game Mode", "Toggle the per-user Windows Game Mode flags.",
                  "Windows decides how Game Mode is applied. Organization policies and app compatibility are not overridden; restart affected games.", default_selected=True),
    GamingFeature("power", "Ultimate Performance plan", "Duplicate Microsoft's template and activate an application-owned scheme.",
                  "Can increase power draw, fan noise, heat and battery drain. Many Modern Standby systems do not allow this plan. The previous active plan is saved; built-in/user plans are never deleted."),
    GamingFeature("visual", "Reduced Windows visual effects", "Disable supported animation/drop-shadow settings and transparency.",
                  "Changes Windows appearance/accessibility preferences, not a game's graphics quality. Native animation settings apply immediately; some Explorer/transparency effects need sign-out. Only changed settings are reverted."),
)


@dataclass(frozen=True, slots=True)
class TimerInfo:
    coarsest_us: int
    finest_us: int
    current_us: int
    requested_us: int | None = None


@dataclass(frozen=True, slots=True)
class GpuInfo:
    name: str
    supported: bool | None
    enabled: bool | None
    default_enabled: bool | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class NetworkInterface:
    guid: str
    name: str
    ipv4: tuple[str, ...]
    active: bool


@dataclass(frozen=True, slots=True)
class GamingState:
    key: str
    status: str
    available: bool = True
    reason: str = ""
    saved: bool = False


@dataclass(frozen=True, slots=True)
class RestorePointInfo:
    sequence: int
    description: str
    created_at: str


@dataclass(frozen=True, slots=True)
class GamingSnapshot:
    states: tuple[GamingState, ...]
    timer: TimerInfo | None
    gpus: tuple[GpuInfo, ...]
    interfaces: tuple[NetworkInterface, ...]
    checkpoint: RestorePointInfo | None = None


@dataclass(frozen=True, slots=True)
class GamingResult:
    success: bool
    message: str
    details: str = ""
    reboot_required: bool = False
    cancelled: bool = False
