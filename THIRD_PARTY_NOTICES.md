# Third-party notices

The project's own source is licensed under MIT (`LICENSE`). That does **not** override the licenses of its dependencies or automatically permit proprietary distribution of a combined binary.

| Component | Release-pinned version | License / distribution considerations |
| --- | --- | --- |
| PyQt6 | 6.12.0 | GPLv3 for the installed community wheel; a separate commercial license is available from Riverbank Computing. |
| Qt 6 runtime (`PyQt6-Qt6`) | 6.12.0 | The supplied wheel includes LGPLv3 terms; module and third-party license requirements must be reviewed. |
| PyQt6-sip runtime | 13.13.0 | BSD 2-Clause (Phil Thompson / Riverbank Computing). |
| psutil | 7.2.2 | BSD 3-Clause (its copyright holders/contributors). |
| Python runtime | Build interpreter version | Python Software Foundation license and bundled component notices. |
| PyInstaller bootloader | 6.22.3 | GPL terms with the PyInstaller bootloader distribution exception; see its bundled license. |

PyInstaller packaging includes each runtime package's distribution metadata and license text under `TATClean/_internal/*dist-info/` (either `LICENSE` or `licenses/LICENSE`). The build interpreter's Python license is included under `_internal/third_party/Python/` when available. System Segoe UI/Consolas fonts are read from the installed Windows OS only and are **not redistributed**.

The build also provides an application-source ZIP. You remain responsible for applicable combined-work licensing, complete corresponding-source/source-offer obligations (including dependencies), license notices and relinking requirements before conveying binaries to others. Review PyQt6/Qt terms or use properly licensed commercial dependencies for a proprietary release. A source ZIP by itself is not a claim that every third-party licensing obligation has been satisfied.

Official information/source locations:

- PyQt6: <https://www.riverbankcomputing.com/software/pyqt/>
- Qt licensing: <https://www.qt.io/licensing/>
- psutil: <https://github.com/giampaolo/psutil>
- Python: <https://www.python.org/psf/license/>
- PyInstaller: <https://pyinstaller.org/en/stable/license.html>

Build-only testing tools retain their own licenses. No private code-signing key, commercial dependency license or publisher certificate is supplied by this project.
