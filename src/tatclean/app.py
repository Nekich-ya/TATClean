from __future__ import annotations

import argparse
import ctypes
import logging
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from tatclean import APP_NAME, __version__
from tatclean.ui.theme import apply_theme


def _startup_error(message: str) -> int:
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(APP_NAME)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(message)
    box.exec()
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="TAT Clean Windows maintenance suite")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--handoff", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-test", action="store_true", help="Show the UI briefly, perform read-only initialization, then exit (no maintenance actions).")
    options = parser.parse_args()
    application = QApplication([sys.argv[0]])
    application.setApplicationName(APP_NAME)
    application.setOrganizationName("TATClean")
    application.setApplicationVersion(__version__)
    apply_theme(application)
    if os.name != "nt":
        return _startup_error("TAT Clean requires 64-bit Windows 10 or Windows 11. No system operations were performed.")
    if ctypes.sizeof(ctypes.c_void_p) != 8 or sys.getwindowsversion().build < 17763:
        return _startup_error("Use 64-bit Python 3.11+ on Windows 10 (1809+) or Windows 11.")

    from tatclean.core.config import SettingsStore
    from tatclean.core.controller import AppController
    from tatclean.core.paths import Locations, validate_ancestry
    from tatclean.core.state import StateStore, configure_logging
    from tatclean.native.system import SingleInstance, is_admin, is_fixed_local_path
    from tatclean.ui.window import MainWindow

    instance = None
    smoke_directory = None
    try:
        instance = SingleInstance(handoff=options.handoff)
        locations = Locations.discover()
        if options.smoke_test:
            smoke_directory = tempfile.TemporaryDirectory(prefix="tatclean-smoke-")
            locations = replace(locations, state=Path(smoke_directory.name) / "state")
        if not is_fixed_local_path(locations.state):
            raise RuntimeError("Local application state requires a fixed/RAM volume, not a network/removable path.")
        validate_ancestry(locations.state.parent)
        locations.state.mkdir(exist_ok=True)
        validate_ancestry(locations.state)
        logger = configure_logging(locations.state / "logs")
        store = SettingsStore(locations.state / "settings.json")
        settings = store.load()
        state = StateStore(locations.state / "state.sqlite")
        controller = AppController(locations, store, settings, state, is_admin(), application)
        window = MainWindow(controller)
    except Exception as exc:
        logging.getLogger("tatclean").exception("Application startup failed")
        if instance is not None:
            instance.close()
        logger = logging.getLogger("tatclean")
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        if smoke_directory is not None:
            smoke_directory.cleanup()
        return _startup_error(f"TAT Clean could not start: {exc}")

    exit_code = 0

    def exception_hook(kind, value, tb) -> None:
        nonlocal exit_code
        logger.critical("Unhandled UI exception", exc_info=(kind, value, tb))
        controller.cancel()
        window.activity.append("Unexpected error: " + str(value))
        window.feedback.show_message("Unexpected error", "The current task was asked to cancel safely. See the local log for details.")
        if options.smoke_test:
            exit_code = 1
            controller.request_shutdown()

    sys.excepthook = exception_hook
    if store.load_warning:
        window.activity.append(store.load_warning)
    logger.info("Application started; elevated=%s; version=%s", controller.admin, __version__)
    if options.smoke_test:
        # All app state/restore points are isolated, so no real priorities can be restored.
        def finish_smoke(_data) -> None:
            QTimer.singleShot(1500, controller.request_shutdown)
        def failed_smoke(_failure) -> None:
            nonlocal exit_code
            exit_code = 1
            QTimer.singleShot(0, controller.request_shutdown)
        controller.initialized.connect(finish_smoke)
        controller.failed.connect(failed_smoke)
    try:
        window.show()
        result = application.exec()
        return exit_code or result
    finally:
        instance.close()
        sys.excepthook = sys.__excepthook__
        logger.info("Application exited")
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        if smoke_directory is not None:
            smoke_directory.cleanup()
