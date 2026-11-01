# Safety, scope and operational limitations

## Consent and destructive operations

Cache removal is permanent, not secure erasure, and is not reversible. The preview and confirmation acknowledge this before deletion. Emptying the Recycle Bin has a separate acknowledgement and includes every recycled item owned by this user on all drives. Winsock reset is a separate troubleshooting action with explicit VPN/provider and restart warnings. No action is automatically executed after UAC restart.

Native commands are fixed argument lists with absolute System32 executable paths and `shell=False`. Drive input is validated to one fixed local volume. Output is bounded and commands have timeouts. Cancellation can stop the launched command process, but cannot promise that Windows has reversed work already done. Recycle Bin shell calls cannot be interrupted safely; the GUI remains responsive while waiting.

## Protected data and filesystem guards

- No user Documents, Desktop, Downloads, Pictures, Music, Videos or application-state subtree is eligible, including known-folder redirects such as OneDrive or another local drive.
- No Windows System32, SysWOW64, WinSxS, servicing, Boot or SystemApps subtree is eligible.
- No whole browser profile, history, cookies, passwords, sessions, local storage, IndexedDB or extension directory is targeted.
- Telegram account stores/keys, Roblox installed Versions, Unreal/Unity projects and the Unreal Zen live database are excluded.
- Links/junctions/reparse points and multiply linked files are never followed/deleted. Read-only attributes, file locks and access-control restrictions are never defeated.
- Root directories, ACLs and ownership are not removed or rewritten. No scheduled reboot deletions are queued.
- Nonstandard TEMP values pointing at a broad/protected folder fall back to the known Local AppData Temp location. When TEMP aliases Windows Temp, the stricter admin/48-hour rules remain in force.

Native ancestor locks and final-handle identity checks close common directory-replacement and filename-race hazards. No user-space cleaner can defend against a compromised kernel, malicious storage driver or an administrator modifying application memory. Do not run unsigned/untrusted modifications as administrator.

## App and service activity

Recognized application executables are skipped while running, including background browser processes. An unverifiable process list fails closed for app-specific targets. Unknown games/renderers should be added to Settings; generic Temp files additionally depend on minimum age and exclusive access. Antivirus or another application may transiently lock a candidate: skipping it is intentional.

Windows Update payload cleanup needs administrator access, stopped `wuauserv` and `BITS`, and no recognized installer/update worker. The app **does not stop** these services for you. Leave this opt-in target alone during pending installations/restarts; do not stop update services solely for routine space recovery. The update database and installed updates are not targeted.

Service toggles only cover `WSearch`, `DiagTrack` and optional `dmwappushservice`. Changing indexing can degrade search (including Outlook); diagnostic routing changes can affect managed devices. Antivirus, firewall, RPC, event logging, Windows Update and other core services are never offered as toggles. This is not a complete telemetry removal or corporate-policy bypass tool.

Service/background-capture and gaming registry mutations first require a verified Windows System Restore point. Protection/quota settings are not overridden; creation failure blocks the operation. Revert still uses exact saved baselines and does not force a system-wide restore.

The recording toggle changes only `HKCU\Software\Microsoft\Windows\CurrentVersion\GameDVR\HistoricalCaptureEnabled`. Manual recording, screenshots, overall Game DVR enablement and machine/organizational policies are not changed. Restart affected games/Game Bar to apply the background-recording change.

## Performance claims

- Windows uses Prefetch to improve launch times; removing it may make launches slower.
- Shader/derived caches must rebuild, potentially causing long compilation delays and game stutter.
- Working-set trimming is temporary and can cause page faults, disk activity and worse performance. No repeated automatic trimming is supported.
- HIGH game priority is optional and can hurt other work. REALTIME is forbidden. Only explicitly recognized user/session-owned processes can be changed; system/security processes and this app are protected.
- `defrag /O` lets Windows select appropriate optimization, including retrim on supported SSDs. Device seek-penalty information is a hint, not a guarantee about every virtual/SAN/storage device. No SSD firmware settings or TRIM policy are changed.
- DNS/ARP/Winsock operations repair particular issues; they do not improve bandwidth or guarantee lower latency.
- The 0.5/1.0 ms timer request is balanced and released on exit; it is not a global game-timer guarantee. TCP/Nagle settings cannot benefit UDP traffic. HAGS/MMCSS/Ultimate Performance are opt-in and can worsen frame pacing, compatibility, power use or background responsiveness. See [exact scopes](GAMING.md).

## Multi-drive / duplicate safeguards

All-drive selection means recognized disposable paths on selected local fixed/RAM/removable volumes, not every file on a drive. Network/optical/folder-mount roots are not traversed. Secondary game installations/saves, Windows Installer and package stores are never inferred to be junk. Temp-path installers are opt-in and at least seven days old. Duplicate-only dumps keep one content-verified copy locked during each deletion; a changed or missing keeper preserves remaining copies.

## Versions, applications and diagnostics

Cache paths are explicit common locations, not a guarantee that every app version, portable install or redirected profile uses them. Unavailable targets stay visible when "Installed caches only" is unchecked. Refresh targets after installing or running an app that creates a new cache. Shared/live databases and ambiguous media/download folders are intentionally omitted rather than guessed.

Scan manifests stay local and expire for cleaning after an hour. Only app-owned UUID-named manifests older than two days are pruned. Action history has configurable 7–90 day retention; restore points are never age-pruned. File logs are size-rotated (active 2 MB file plus up to five backups). Logs/manifests can contain local cache paths and action details—treat them as private.

Exported JSON diagnostics exclude user identity, manifest paths, saved registry values and raw command output. They include hardware/OS information, drive summaries, action IDs/messages and restore-point counts; review before sharing. The application makes no network requests or automatic update checks.

## Recovery and uninstall

Use Settings → **Restore saved changes** before uninstalling. Use the same Windows account for elevation/restoration. Service restoration needs administrator rights. If a game exits, its obsolete priority record is removed without touching a newly reused PID. External conflicts and failed restorations retain their records for review; no force-restore switch silently overwrites them.

After a crash or power loss, persistent baselines remain in `%LOCALAPPDATA%\TATClean\state.sqlite`. Relaunch the app, review its log and restore saved changes. Keep the SQLite file and any `-wal`/`-shm` sidecars together while the app is running; close the app cleanly before copying state. Uninstall removes binaries, not these local restore points, and does not automatically reverse service/recording changes.

Validate releases on representative Windows, antivirus, storage-driver and application versions before organizational deployment. Tests exercise real Win32 filesystem guards only on disposable fixtures; invasive operations are mocked. This is comprehensive implementation and regression coverage, not a claim of universal compatibility or zero risk.
