# Architecture

## Project layout

```text
TATClean/
├── run.py                     Source-tree entry point
├── pyproject.toml             Package, GUI entry point, tools and dependencies
├── requirements*.txt          Flexible and release-pinned dependency sets
├── build.ps1                  Validate, build, smoke-test, ZIP and checksum
├── TATClean.spec               PyInstaller one-folder configuration
├── assets/                    Windows manifest, version resources, generated icon
├── installer/TATClean.iss      Optional complete Inno Setup per-user installer
├── tools/create_icon.py        Vector-to-ICO build utility
├── src/tatclean/
│   ├── app.py                  Bootstrap, single instance, logging, lifecycle
│   ├── core/
│   │   ├── models.py           Typed immutable messages and summaries
│   │   ├── config.py           Validated settings and atomic JSON writes
│   │   ├── state.py            SQLite restore points / action audit; rotating logs
│   │   ├── paths.py            Allowlist, protected paths and no-link policy
│   │   ├── catalog.py          Explicit cache scopes and browser profile discovery
│   │   ├── drive_catalog.py    Disposable secondary-drive/Steam/installer/dump scopes
│   │   ├── gaming_models.py    Validated granular requests and immutable UI snapshots
│   │   ├── gaming.py           Checkpoint-gated journals and atomic gaming batches
│   │   ├── manifest.py         Streaming SQLite candidates and SHA-256 integrity
│   │   ├── cleaner.py          Read-only scan and checked deletion orchestration
│   │   ├── processes.py        Activity guard, snapshots, QThread telemetry
│   │   ├── actions.py          Whitelisted commands and reversible tweak transactions
│   │   ├── tasks.py            QThread jobs, cancellation and single-task scheduling
│   │   └── controller.py       Presentation facade and asynchronous shutdown
│   ├── native/
│   │   ├── winapi.py           Typed Win32 bindings, handles and COM scope
│   │   ├── files.py            Pinned ancestry and exclusive handle deletion
│   │   ├── system.py           Known folders, volumes, Recycle Bin, DWM and UAC
│   │   ├── processes.py        Process identity/SID/session checks and Win32 controls
│   │   ├── services.py         Service Control Manager query/configuration APIs
│   │   ├── timer.py            Balanced process-owned Nt timer requests
│   │   ├── gpu.py              Read-only WDDM/HAGS capability detection
│   │   ├── power.py            Owned Ultimate plan creation/activation/restoration
│   │   ├── restore_points.py   Encoded PowerShell/CIM point creation and verification
│   │   ├── gaming_registry.py  Exact MMCSS/HAGS/Game Mode/TCP/visual registry scopes
│   │   ├── visual.py           Native SPI preference capture and restoration
│   │   └── registry.py         HKCU HistoricalCaptureEnabled flag (manual capture untouched)
│   └── ui/
│       ├── theme.py            Black/silver palette and translucent card styles
│       ├── icons.py            In-memory scalable SVG icons
│       ├── widgets.py          Animated controls, confirmations and bounded logs
│       ├── window.py           Sidebar, page stack, task status and lifecycle
│       ├── drive_selector.py   Mounted-drive selection without unsafe root sweeps
│       └── pages/              Dashboard, cleaner, performance, gaming, RAM/services, settings
├── tests/                     Unit, native scratch-file, task and Qt integration tests
└── .github/workflows/          Windows Python-version validation and build workflow
```

## Threading and lifecycle

- Widgets and Qt models are updated only on the GUI thread through queued signals.
- `TaskWorker(QThread)` runs catalog discovery, scans, cleanup, commands, process snapshots, preference writes and diagnostic export outside the GUI thread.
- `TaskManager` admits one maintenance task at a time, preventing overlapping cleanup/tweak transactions. It retains each thread until `finished`, then delivers its result, allowing a callback to safely schedule the next task.
- `MetricsWorker(QThread)` independently samples CPU, RAM, disk usage and uptime. Its first CPU reading is warmed up rather than reporting a misleading immediate zero.
- Cancellation uses `threading.Event` checkpoints. Progress is throttled to at most about ten ordinary updates per second, with explicit target/final notifications. No `QThread.terminate()` is used.
- Closing requests cancellation, stops telemetry, waits asynchronously for task completion, restores saved live-game priorities if configured, persists preferences and only then releases the window. Service rollback uses a fresh, uncancelled context so cancellation cannot abandon a recovery mid-transition.

## Scan and deletion pipeline

1. Build a fixed catalog from Windows known folders, explicit cache suffixes and recognized browser profiles. Arbitrary drive, project and whole-profile cleanup is deliberately unsupported.
2. Validate roots against protected paths and reject reparse ancestry. More-specific nested targets own their subtrees even when unselected, preventing a broad Temp selection from bypassing an app's switch.
3. Check administrative access, application names and required idle service states.
4. Walk with `os.scandir` without following reparse points. Preserve locks, profile stores, hard links, special files and files newer than the strictest minimum age.
5. Stream eligible file identities/sizes/timestamps into a UUID-named SQLite manifest. Batches commit every 1,000 entries; no in-memory list of every candidate is needed. Duplicate paths use a normalized unique key.
6. Finalize a SHA-256 digest. Only a complete preview younger than an hour may be cleaned.
7. Open one read-only SQLite snapshot and verify all records/counts/bytes against the in-memory preview before deleting anything.
8. Pin every root ancestor using `FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES` with read/write sharing but **without delete sharing**. Metadata-only access is insufficient to enforce the required rename lock. Pin each descendant directory chain while scanning/deleting its files.
9. Open each file with `DELETE | FILE_READ_ATTRIBUTES`, exclusive sharing, and `FILE_FLAG_OPEN_REPARSE_POINT`. Verify its final path, file ID/volume, size, timestamp, link count, age and attributes.
10. Mark the file for deletion using `SetFileInformationByHandle(FileDispositionInfo)` and close the **same** handle. Never reopen by pathname to delete. Busy/changed/denied files are skips. If an app starts during cleaning, stop that target and promptly release directory pins.

Directory roots and permissions are preserved. Reported removed bytes are logical file sizes, not measured filesystem allocation recovery. Compression, sparse files, hard links, snapshots and concurrent disk activity make an exact "space freed" calculation unreliable.

Manifest schema 2 also carries duplicate content hashes and keeper fingerprints. Duplicate-only planning streams verified hashes into SQLite, removes unique/keeper files from the deletion manifest and recomputes its digest. Keeper evidence participates in verification. Cleanup rehashes the keeper/copy and holds the keeper's exclusive handle while deleting a duplicate, preserving a last copy even if the original was changed or removed after preview.

## Reversible operations

Baselines are persisted before any mutation, and repeated changes never replace the first original. Service snapshots include startup type, delayed auto-start flag and running state. Registry snapshots preserve existence, type and value, including binary encoding. Priority snapshots include process creation time, executable identity and original priority.

Restoration only uses whitelisted native operations. Conflicting service startup types, registry values or process priorities are reported and the restore point retained. Process handles are checked against creation time, executable name, current user SID and current session, preventing PID-reuse or cross-user modification.

Gaming journals distinguish immutable original state from pending/applied expected state. Multi-feature and multi-adapter batches retain each undo token before proceeding; cancellation/error recovery runs in reverse order with a fresh context. Native visual preferences are restored through SPI rather than replacing opaque mask bits. Power plans carry an ownership marker and only owned clones are removed. System Restore is independently verified before mutation; it is not used to silently undo unrelated system activity. See [gaming architecture/scopes](GAMING.md).

## Presentation

The UI uses a `#000000` base and `rgba(20,20,20,178)` charcoal cards (approximately 70% alpha), silver accents and 12px card corners. Hover colors animate over 150ms. SVG icons render at multiple device-pixel ratios; Qt6 and the packaged manifest support per-monitor DPI. Native dark title bars and Windows 11 rounded window corners are best-effort, supported DWM attributes.

The card effect is layered translucent glass styling—not a claim to blur the actual desktop behind every widget. No undocumented acrylic hooks or blur effects that obscure text are used. Process rows use a lazy `QAbstractTableModel`/proxy, and the visible activity log is plain text with bounded line/message counts.
