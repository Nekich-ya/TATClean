# Gaming / latency controls (1.1)

## Apply and revert

Dashboard **Optimize Now** applies only checked, available cards on **Gaming & Latency**. Game Mode is the conservative initial selection; experimental controls are opt-in. Each card also has an individual apply/revert action. Checkbox or mode changes alone never mutate Windows.

1. Restart as administrator with the same Windows account when requested.
2. Select specific features and exact IPv4 interfaces; review their warnings.
3. The worker creates a real `Checkpoint-Computer -RestorePointType MODIFY_SETTINGS` point and verifies its unique description/new sequence through CIM `root/default:SystemRestore`.
4. Durable original values are committed before every mutation. A pending/applied journal preserves the first original while recording the requested state.
5. Failed batches roll back completed steps in reverse order, with an uncancelled recovery context. External conflicts/failed rollback leave durable records for review.
6. Revert only restores owned settings; it does not activate a broad Windows System Restore operation or recover deleted files.

System Protection must already be enabled. If point creation is blocked, unsupported, unverified or subject to Windows' creation quota, no tweak is applied. The app never changes `SystemRestorePointCreationFrequency` or enables protection silently. A point created by this instance can be re-verified/reused for one hour; a newly launched instance does not assume that a previous point is suitable. Legacy service/background-capture mutation also uses this checkpoint gate.

## Exact changes

| Feature | Scope / values | Limitations |
| --- | --- | --- |
| Timer | `NtQueryTimerResolution` and `NtSetTimerResolution(5000 or 10000, TRUE)` in 100ns units | Exactly one application-owned request; changing mode balances the prior release. Exit/revert releases only this process's request. Modern Windows process/occlusion rules and other active requests can limit the effect on games. No global timer-policy or BCDEdit overrides. |
| TCP/Nagle | `HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\{selected GUID}`: `TcpAckFrequency=1`, `TCPNoDelay=1` (DWORD) | Existing, verifiable IPv4 interfaces only; never create an interface or sweep every key. TCP-only, not UDP; can increase packet overhead. Restart adapter/affected software or Windows as needed. |
| MMCSS | `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile`: `SystemResponsiveness=10`; `Tasks\Games`: `Scheduling Category="High"`, `Priority=8`, `GPU Priority=8`, `SFIO Priority="High"` | Values below 10% are clamped by Windows to 20%, so 0 is not a 0% reserve. Only MMCSS-registered threads participate; scheduling-category rules govern actual priorities. GPU/SFIO priority fields are not verified frame-rate accelerators. No process is forced into REALTIME. Restart required/recommended. |
| HAGS | `HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers\HwSchMode=2` (enable) or `1` (disable) | Windows 10 2004+ / Windows 11 and verified supported hardware/driver. Read-only WDDM 2.7 capability query uses the installed SDK layout; failure/unknown results disable the control. Internal/system-oriented graphics interfaces can change across versions. Restart required. |
| Game Mode | `HKCU\Software\Microsoft\GameBar`: `AllowAutoGameMode` / `AutoGameModeEnabled=1 or 0` | Per-user flags only; machine/organizational policies are not overridden. |
| Ultimate Performance | `powercfg -duplicatescheme e9a42b02-d5df-448d-aa00-03f14749eb61 <application UUID>`, name/ownership marker, `-setactive` | Previous active GUID saved first. Only the generated plan is removed after restoring the original. Many Modern Standby systems restrict available plans; failure rolls back. Increased heat/power/battery use is possible. |
| Visual effects | Public SPI getters/setters for client/window/menu/tooltip/selection/list/combo animation and classic drop shadows; HKCU `VisualFXSetting=2`, `EnableTransparency=0`, `ListviewShadow=0` | Original native booleans and named registry values saved. No blind overwrite of the opaque `UserPreferencesMask`; unrelated bits/preferences are preserved. Some Explorer/transparency changes need sign-out. Accessibility/appearance changes may be undesirable. |

## Honest performance expectations

There is no synthetic "latency score" or FPS gain claim. Timer resolution is not a frame-time measurement or a guaranteed change to another process's scheduler. TCP tweaks cannot reduce a UDP game's network latency; maximum MMCSS fields do not force GPU boost clocks. HAGS, power and visual changes can improve or worsen a particular workload. Benchmark representative games on your own driver/hardware, make one change at a time, and revert regressions.

These controls cover the requested Hone-inspired feature set, not reverse-engineered proprietary algorithms or a verified full-feature/performance-parity claim. No overclocking, security-disabling, driver replacement, kernel-policy hacks or forced process killing is performed.

## Multi-drive cleanup

The drive picker supports individual/multiple drives and **All local drives**. Mounted network, optical and folder-mounted reparse volumes are visible but disabled. Removable media is scanned only when selected and revalidated as such. Known OS/profile paths remain at their real locations, including relocated profiles; nothing is fabricated on `C:` or redirected to the selected secondary drive.

Recognized secondary paths include `Temp` / `TMP` and Steam `steamapps\shadercache`, with bounded Steam library metadata discovery. Game installations, `common`, workshop data, saves, `.egstore`, `%WINDIR%\Installer` and package caches are never considered abandoned based on names/extensions. Old `.exe`/`.msi` and related installers are opt-in, at least seven days old, confined to Temp paths and skipped while installers are running. Their age/path makes them cleanup candidates—not proof of uninstall/orphan status.

Duplicate-only crash-dump targets are mutually exclusive with all-dump targets for the same path. SHA-256 hashing occurs in the worker through verified exclusive handles. One copy per equal-content group is removed from the deletion manifest. At cleanup, that retained copy is revalidated, rehashed and kept locked while each duplicate is rehashed/deleted through its own verified handle. A missing/changed/locked keeper means the remaining copy is preserved. Unique dumps are never deleted by duplicate-only cleanup.

System Restore does not restore cache deletions, Recycle Bin contents or deleted dumps/installers. Keep useful diagnostic/media files outside disposable paths, and always review the selected scopes.
