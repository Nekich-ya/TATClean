# Deployment

## Supported environment

- 64-bit Python **3.11+**, Windows 10 **1809+** or Windows 11.
- Source defaults: PyQt6 `>=6.7,<7`, psutil `>=5.9.8,<8`.
- Release-pinned dependencies are in `requirements-lock.txt` and `requirements-dev.lock.txt`.
- Launch uses standard privileges (`asInvoker`); explicit UAC restart is available in Settings. No driver, elevated service or startup task is installed.

## Build and validate

```powershell
python -m venv .venv
.\build.ps1
```

The build installs pinned build/test dependencies into the chosen environment, runs static checks and tests, creates the icon/version resources, packages the application with PyInstaller and runs an isolated `--smoke-test`. The smoke test initializes the UI and reads system status only; its preferences/audits/restore points/logs use a disposable temporary directory and no existing tweak is restored.

Outputs:

```text
dist/TATClean/TATClean.exe
dist/TATClean/_internal/...        Python, Qt, psutil and plugin runtime
dist/TATClean/README.md
dist/TATClean/docs/...
dist/TATClean-1.1.0-windows-x64.zip
dist/TATClean-1.1.0-source.zip
dist/SHA256SUMS.txt
```

Ship the **entire `TATClean` folder**, not just the executable. A one-folder bundle is deliberate: startup is predictable and native plugin/runtime files are inspectable, without repeated one-file extraction into Temp. No network access is needed to run the packaged application.

The default build tests before release. `-SkipTests` is intended only after the same checkout has already passed CI or local validation. All generated build/distribution files stay in this project. Close any existing TAT Clean instance before running a packaged smoke test; the single-instance guard also applies to tests.

## Alternative Nuitka build

The primary PyInstaller release path is validated. This optional recipe requires its own compilation/clean-machine testing before being advertised as a supported binary build. Nuitka converts Python to native code; it does not make Windows registry/network tweaks safer or guarantee faster games.

Use 64-bit Python 3.11–3.13 and a supported MSVC/Windows SDK (or a compiler supported by your Nuitka version). See [Nuitka's setup guide](https://nuitka.net/user-documentation/tutorial-setup-and-build.html). Compiler/cache downloads may be requested interactively; approve them only after review.

```powershell
.\.venv\Scripts\python.exe -m pip install Nuitka ordered-set zstandard
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
.\.venv\Scripts\python.exe tools\create_icon.py
.\.venv\Scripts\python.exe -m nuitka --mode=standalone --enable-plugin=pyqt6 --include-package=tatclean --include-data-dir=src/tatclean/ui/assets=tatclean/ui/assets --include-distribution-metadata=PyQt6 --include-distribution-metadata=PyQt6-Qt6 --include-distribution-metadata=PyQt6-sip --include-distribution-metadata=psutil --windows-console-mode=disable --windows-icon-from-ico=assets/app.ico --output-dir=dist/nuitka --output-filename=TATClean.exe run.py
```

Keep the entire generated `*.dist` directory under `dist/nuitka`; use the exact output path reported by Nuitka. Start with standalone, not onefile, so missing Qt plugins/data can be diagnosed. Application runtime detection recognizes PyInstaller's `sys.frozen` and Nuitka's `__compiled__` marker for app-root protection/UAC relaunch.

Run the generated executable with `--smoke-test`, then verify native UI assets, standard-user launch, UAC cancellation, timer-request release, and invasive actions **only in an approved disposable VM**. Copy README/LICENSE/third-party notices and verify that required dependency/Python license texts are present; metadata inclusion alone does not prove legal compliance. Sign/repackage/checksum only after validation. Debug startup failures with the console enabled rather than suppressing error information.

No Nuitka executable has been built/validated as part of the current release; it remains an alternative procedure. Official references: [Nuitka use cases](https://nuitka.net/user-documentation/use-cases.html), [common issues](https://nuitka.net/user-documentation/common-issue-solutions.html).

## Optional installer

With Inno Setup **6.3+** installed:

```powershell
& 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' installer\TATClean.iss
```

This creates `dist/TATClean-Setup-1.1.0.exe`. The installer defaults to `%LOCALAPPDATA%\Programs\TAT Clean`, runs without administrator rights, offers an unchecked desktop shortcut, does not force-close running applications, and warns before uninstall that persistent Windows tweaks must be restored first. It preserves local preferences and restore points.

## Signing and release controls

Generated binaries are **unsigned** until you sign them with your own trusted code-signing identity. Do not invent a publisher certificate or bypass Windows SmartScreen. Sign the final executable (and installer, if used) using your organization's signing process and a trusted timestamp server; re-create ZIP/checksums **after** signing. Never distribute private keys in this repository.

Review [third-party notices](../THIRD_PARTY_NOTICES.md), including PyQt6 GPLv3/commercial licensing and Qt obligations. The binary bundle contains dependency license metadata; the build includes an application-source archive but does not purport to supply every dependency's complete corresponding source or a commercial license.

Recommended release checks:

1. Run tests on Python 3.11, 3.12 and 3.13 / representative Windows 10 and 11 PCs.
2. Verify standard-user launch and UAC cancellation, then test invasive actions only on a disposable VM with snapshots and approval.
3. Check SSD/HDD/BitLocker/virtual-storage behavior, browser versions and antivirus locking.
4. Verify registry/service rollback, crash recovery, original-state restoration and PID reuse handling.
5. Inspect UI at different DPI levels and ensure status logs/confirmations are readable.
6. Validate signatures and distributed SHA-256 hashes, and retain the pinned dependency list and test results.

The checked-in CI workflow covers Windows Python-version tests and a packaged read-only smoke test. It does not run destructive real-system maintenance, publish a release, install the optional installer or sign with an unprovided certificate.
