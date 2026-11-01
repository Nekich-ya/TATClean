from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from tatclean import APP_NAME, __version__
from tatclean.core.actions import ACTION_SPECS
from tatclean.core.controller import AppController
from tatclean.core.gaming_models import GAMING_FEATURES
from tatclean.core.models import (
    ActionResult,
    CleanReport,
    Initialization,
    Metrics,
    Risk,
    ScanReport,
    TaskFailure,
    TaskProgress,
    format_bytes,
    format_duration,
)
from tatclean.native.services import SERVICE_DEFINITIONS
from tatclean.native.system import apply_dark_window
from tatclean.ui.icons import brand_icon
from tatclean.ui.pages.cleaner import CleanerPage
from tatclean.ui.pages.dashboard import DashboardPage
from tatclean.ui.pages.gaming import GamingPage
from tatclean.ui.pages.performance import PerformancePage
from tatclean.ui.pages.ram_services import RamServicesPage
from tatclean.ui.pages.settings import SettingsPage
from tatclean.ui.widgets import (
    ActivityLog,
    Button,
    ConfirmDialog,
    FeedbackBar,
    GlassCard,
    scroll_page,
    text_label,
)


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController, *, auto_initialize: bool = True):
        super().__init__()
        self.controller = controller
        self._ready = False
        self._allow_close = False
        self._relaunching = False
        self._active_action = ""
        self.setWindowTitle(f"{APP_NAME} · System maintenance")
        self.setWindowIcon(brand_icon())
        self.setMinimumSize(1100, 740)
        screen = self.screen().availableGeometry()
        self.resize(min(controller.settings.window_width, max(1100, screen.width() - 40)),
                    min(controller.settings.window_height, max(740, screen.height() - 50)))
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(228)
        navigation = QVBoxLayout(sidebar)
        navigation.setContentsMargins(16, 28, 16, 20)
        navigation.setSpacing(8)
        brand = text_label("TAT CLEAN")
        brand.setStyleSheet("font-size: 23px; font-weight: 600; letter-spacing: 1px;")
        navigation.addWidget(brand)
        navigation.addWidget(text_label("WINDOWS MAINTENANCE", "eyebrow"))
        navigation.addSpacing(27)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons = []
        for index, (label, symbol) in enumerate((
            ("Dashboard", "dashboard"), ("Deep Cleaner", "clean"),
            ("Performance Tweaks", "performance"), ("Gaming & Latency", "game"), ("RAM & Services", "ram"), ("Settings", "settings"),
        )):
            button = Button(label, kind="nav", symbol=symbol)
            self.nav_group.addButton(button, index)
            self.nav_buttons.append(button)
            navigation.addWidget(button)
        self.nav_buttons[0].setChecked(True)
        self.nav_group.idClicked.connect(self.navigate)
        navigation.addStretch()
        guard = GlassCard(padding=13)
        guard.body.addWidget(text_label("GUARDS ACTIVE", "eyebrow"))
        guard.body.addWidget(text_label("Preview before deletion\nLocked files preserved\nNo silent system changes", "muted", wrap=True))
        navigation.addWidget(guard)
        navigation.addSpacing(12)
        self.privilege = text_label("●  Administrator" if controller.admin else "●  Standard access", "muted")
        navigation.addWidget(self.privilege)
        self.uptime = text_label("Uptime · —", "muted")
        navigation.addWidget(self.uptime)
        navigation.addWidget(text_label(f"Version {__version__}", "eyebrow"))
        outer.addWidget(sidebar)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 28, 28, 22)
        layout.setSpacing(15)
        self.stack = QStackedWidget()
        self.dashboard = DashboardPage()
        self.cleaner = CleanerPage()
        self.performance = PerformancePage()
        self.gaming = GamingPage()
        self.ram_services = RamServicesPage()
        self.settings = SettingsPage(controller.settings, controller.locations.state, controller.admin)
        self.stack.addWidget(scroll_page(self.dashboard))
        self.stack.addWidget(self.cleaner)
        self.stack.addWidget(scroll_page(self.performance))
        self.stack.addWidget(scroll_page(self.gaming))
        self.stack.addWidget(scroll_page(self.ram_services))
        self.stack.addWidget(scroll_page(self.settings))
        layout.addWidget(self.stack, 1)
        self.feedback = FeedbackBar()
        layout.addWidget(self.feedback)
        task_area = QWidget()
        task_layout = QVBoxLayout(task_area)
        task_layout.setContentsMargins(0, 0, 0, 0)
        task_layout.setSpacing(9)
        status = QHBoxLayout()
        self.status = text_label("Initializing local system overview…", "muted")
        self.status.setMinimumWidth(0)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        status.addWidget(self.status, 1)
        self.reboot = text_label("Restart required", "pill")
        self.reboot.hide()
        status.addWidget(self.reboot)
        self.cancel_button = Button("Cancel operation", symbol="close")
        self.cancel_button.setFixedHeight(31)
        self.cancel_button.clicked.connect(controller.cancel)
        status.addWidget(self.cancel_button)
        task_layout.addLayout(status)
        self.progress = QProgressBar()
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Background maintenance progress")
        task_layout.addWidget(self.progress)
        layout.addWidget(task_area)
        self.activity = ActivityLog()
        layout.addWidget(self.activity)
        outer.addWidget(content, 1)
        if screen.height() < 820:
            self.activity._toggle()
        self._connect()
        self._busy_changed(True, "Initializing local system overview")
        self.activity.append("Safety-first mode: preview and explicit consent are required for deletion or system changes.")
        if auto_initialize:
            QTimer.singleShot(0, controller.initialize)

    def _connect(self) -> None:
        controller = self.controller
        controller.tasks.busy_changed.connect(self._busy_changed)
        controller.tasks.progress.connect(self._progress_changed)
        controller.tasks.log_message.connect(self.activity.append)
        controller.initialized.connect(self._initialized)
        controller.scan_finished.connect(self._scanned)
        controller.clean_finished.connect(self._cleaned)
        controller.action_finished.connect(self._action_finished)
        controller.processes_updated.connect(self.ram_services.update_processes)
        controller.tweaks_updated.connect(self._tweaks_updated)
        controller.backups_updated.connect(self.settings.update_backups)
        controller.metrics_updated.connect(self._metrics)
        controller.settings_updated.connect(self._settings_updated)
        controller.failed.connect(self._failed)
        controller.shutdown_ready.connect(self._shutdown_ready)
        controller.elevation_started.connect(self._elevated)
        self.dashboard.scan_requested.connect(self._quick_scan)
        self.dashboard.performance_requested.connect(lambda: self.navigate(2))
        self.dashboard.gaming_requested.connect(lambda: self.navigate(3))
        self.dashboard.optimize_requested.connect(self._request_boost)
        self.cleaner.scan_requested.connect(self._request_scan)
        self.cleaner.clean_requested.connect(self._request_clean)
        self.cleaner.refresh_requested.connect(controller.initialize)
        self.cleaner.selection_changed.connect(controller.invalidate_scan)
        self.performance.action_requested.connect(self._request_action)
        self.gaming.action_requested.connect(self._request_action)
        self.ram_services.action_requested.connect(self._request_action)
        self.ram_services.refresh_requested.connect(controller.refresh_processes)
        self.settings.action_requested.connect(self._request_action)
        self.settings.save_requested.connect(controller.save_settings)
        self.settings.elevation_requested.connect(self._request_elevation)
        self.settings.open_data_requested.connect(self._open_data)
        self.settings.export_requested.connect(self._export)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_dark_window(int(self.winId()))

    def navigate(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)

    def _initialized(self, data: Initialization) -> None:
        self._ready = True
        self.dashboard.initialize(data)
        self.dashboard.update_scan(None)
        defaults = tuple(dict.fromkeys((self.controller.locations.windows.drive.upper(), self.controller.locations.local.drive.upper())))
        self.cleaner.initialize(data.targets, self.controller.settings.minimum_age_hours, data.mounted, defaults)
        self.performance.initialize(data.volumes)
        self._busy_changed(self.controller.tasks.busy, "")
        self.status.setText("Ready · no maintenance runs automatically")

    def _busy_changed(self, busy: bool, title: str) -> None:
        effective = busy or self.controller.closing or not self._ready
        for page in (self.dashboard, self.cleaner, self.performance, self.gaming, self.ram_services):
            page.set_busy(effective)
        self.settings.set_busy(busy or self.controller.closing)
        if not self._ready:
            self.cleaner.refresh_button.setEnabled(not busy and not self.controller.closing)
            self.settings.save.setEnabled(False)
            self.settings.export.setEnabled(False)
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(busy and not self.controller.closing and self._active_action not in ("empty_recycle", "elevation"))
        if busy:
            self.feedback.hide()
            self.progress.setRange(0, 0)
            self.status.setText(title)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.status.setText("Closing safely…" if self.controller.closing else "Ready · no maintenance runs automatically")
            self._active_action = ""

    def _progress_changed(self, progress: TaskProgress) -> None:
        self.status.setText(progress.message)
        self.status.setToolTip(progress.message)
        if progress.percent is None:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(max(0, min(progress.percent, 100)))
        self.cleaner.update_progress(progress)

    def _quick_scan(self) -> None:
        self.navigate(1)
        self.cleaner.select_standard()
        self._request_scan(self.cleaner.selected_ids())

    def _request_boost(self) -> None:
        if self.controller.tasks.busy or self.controller.closing:
            return
        try:
            request = self.gaming.request()
        except ValueError as error:
            self.navigate(3)
            self.feedback.show_message("Choose gaming tweaks", str(error))
            return
        self._request_action("gaming_apply", {"request": request})

    def _tweaks_updated(self, snapshot) -> None:
        self.ram_services.update_tweaks(snapshot)
        if snapshot.gaming is not None:
            self.gaming.update_snapshot(snapshot.gaming)

    def _request_scan(self, target_ids: tuple[str, ...]) -> None:
        if self.controller.tasks.busy or self.controller.closing:
            return
        if not target_ids:
            self.feedback.show_message("Choose a target", "Select at least one installed cache target before scanning.")
            return
        self.cleaner.clear_report()
        self._active_action = "scan"
        self.controller.scan(tuple(target_ids))

    def _scanned(self, report: ScanReport) -> None:
        self.cleaner.apply_scan(report)
        self.dashboard.update_scan(report)
        self.progress.setValue(100 if report.completed else 0)
        self.status.setText(f"{'Scan complete' if report.completed else 'Scan cancelled'} · {report.files:,} candidates · {format_bytes(report.bytes)} logical bytes")

    def _confirm(self, title: str, message: str, *, irreversible: bool = False, proceed: str = "Continue") -> bool:
        dialog = ConfirmDialog(title, message, irreversible=irreversible, proceed_text=proceed, parent=self)
        apply_dark_window(int(dialog.winId()))
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _request_clean(self) -> None:
        scan = self.cleaner.report
        if self.controller.tasks.busy or scan is None or not scan.completed or not scan.files:
            return
        if scan is not self.controller.current_scan or scan.target_ids != self.cleaner.selected_ids():
            self.feedback.show_message("Scan required", "The selection changed. Run another scan before cleaning.")
            return
        targets = [self.cleaner.targets[key] for key in scan.target_ids]
        categories = ", ".join(dict.fromkeys(target.category for target in targets))
        advanced = [target.label for target in targets if target.risk == Risk.CAUTION]
        message = (
            f"Permanently delete up to {scan.files:,} reviewed files ({format_bytes(scan.bytes)} logical size). Actual freed disk space may differ.\n\n"
            f"Selected groups: {categories}.\n\n"
            "Only scanned files will be considered. File identity, age, directory ancestry, active applications and exclusive file access are checked again. Changed, protected or busy files are skipped. Directory roots are preserved.\n\n"
            "This cannot be undone by TAT Clean. Deleted cache files are not moved to the Recycle Bin."
        )
        if advanced:
            listed = ", ".join(advanced[:5]) + (f" (+{len(advanced)-5} more)" if len(advanced) > 5 else "")
            message += f"\n\nOpt-in scopes: {listed}. Clearing caches can cause rebuild delays or remove useful diagnostics. Windows Update caches are skipped unless both update services are already stopped."
        if self._confirm("Clean reviewed files?", message, irreversible=True, proceed="Delete reviewed files"):
            self._active_action = "clean"
            self.controller.clean()

    def _cleaned(self, report: CleanReport) -> None:
        self.cleaner.clear_report()
        self.dashboard.update_scan(None)
        reasons = "; ".join(f"{key}: {value:,}" for key, value in report.skipped.items() if value)
        message = f"Removed {report.removed_files:,} files · {format_bytes(report.removed_bytes)} logical bytes · {report.skipped_files:,} skipped."
        if reasons:
            message += "\n" + reasons
        self.feedback.show_message("Cleanup cancelled" if report.cancelled else "Cleanup complete", message)
        self.status.setText(message.splitlines()[0])
        self.activity.append(message)
        self.progress.setValue(0 if report.cancelled else 100)

    def _request_action(self, action_id: str, parameters: dict) -> None:
        if self.controller.tasks.busy or self.controller.closing:
            return
        spec = ACTION_SPECS.get(action_id)
        if spec is None:
            return
        if spec.requires_admin and not self.controller.admin:
            if self._confirm("Administrator access required", f"{spec.title} requires administrator privileges. Restart with Windows UAC approval? The action will NOT run automatically after restart.", proceed="Restart with UAC"):
                self._request_elevation(confirm=False)
            return
        message = spec.warning
        title = spec.title
        if action_id == "gaming_apply":
            request = parameters.get("request")
            if request is None:
                return
            selected = [feature for feature in GAMING_FEATURES if feature.key in request.features]
            message += "\n\nSelected tweaks only:\n" + "\n".join(f"• {feature.title}: {feature.warning}" for feature in selected)
            if "timer" in request.features:
                message += f"\nTimer request: {request.timer_us / 1000:g} ms (released on exit)."
            if "nagle" in request.features:
                message += f"\nSelected IPv4 adapters: {len(request.interface_ids)}. UDP traffic is unchanged."
            message += "\n\nIf System Protection is disabled, the creation quota applies, or the restore point cannot be verified, no tweaks are applied. A freshly created point from this session may be re-verified and reused for one hour."
        elif action_id == "gaming_restore":
            message += "\n\nScope: " + str(parameters.get("feature", "all saved gaming tweaks"))
        if action_id in ("disable_service", "restore_service"):
            name = parameters.get("service", "")
            if name not in SERVICE_DEFINITIONS:
                return
            title += " · " + name
            message += "\n\n" + SERVICE_DEFINITIONS[name][1]
        elif action_id == "optimize_volume":
            message += "\n\nSelected drive: " + str(parameters.get("drive", ""))
        elif action_id == "trim_memory":
            selected = parameters.get("processes", ())
            message += f"\n\nSelected processes: {len(selected)}. " + ", ".join(f"{item.name} ({item.pid})" for item in selected[:6])
        elif action_id in ("enable_priority", "restore_priority"):
            process = parameters.get("process")
            if process:
                message += f"\n\nGame: {process.name} · PID {process.pid}"
        if self._confirm(title + "?", message, irreversible=spec.irreversible):
            self._active_action = action_id
            self.controller.perform(action_id, dict(parameters))

    def _action_finished(self, result: ActionResult) -> None:
        self.progress.setValue(100 if result.success else 0)
        self.status.setText(result.message)
        self.status.setToolTip(result.message)
        if not result.success or result.reboot_required:
            self.feedback.show_message("Restart required" if result.reboot_required else "Operation cancelled" if result.cancelled else "Operation not completed", result.message + (" See the activity log for details." if result.details else ""))
        if result.reboot_required:
            self.reboot.show()
        if result.action_id == "export_diagnostics":
            self.activity.append(result.message)

    def _settings_updated(self, settings) -> None:
        self.settings.apply_settings(settings)
        self.cleaner.clear_report()
        self.dashboard.update_scan(None)
        self.activity.append("Preferences saved locally; rebuilding cache activity guards.")

    def _metrics(self, metrics: Metrics) -> None:
        self.dashboard.update_metrics(metrics)
        self.ram_services.update_metrics(metrics)
        self.uptime.setText("Uptime · " + format_duration(metrics.uptime))

    def _failed(self, failure: TaskFailure) -> None:
        if self._active_action in ("scan", "clean"):
            self.cleaner.clear_report()
            self.dashboard.update_scan(None)
            self.controller.invalidate_scan()
        self.activity.append(failure.message)
        if failure.details:
            self.activity.append(failure.details)
        if not self.controller.closing:
            self.feedback.show_message("Operation cancelled" if failure.cancelled else "Operation not completed", failure.message)
            self.status.setText(failure.message)

    def _request_elevation(self, *, confirm: bool = True) -> None:
        if self.controller.tasks.busy or self.controller.admin or self.controller.closing:
            return
        if (self.settings.has_unsaved_changes()
                and not self._confirm("Restart without saving preferences?", "Your unsaved Settings edits will be discarded. Save them first, or explicitly continue with the last saved preferences.", proceed="Discard edits and restart")):
            return
        if confirm and not self._confirm("Restart as administrator?", "Windows will show a UAC consent dialog. Use the same Windows account. No cleanup or tweak is automatically run after restart, and saved game priorities are restored before this instance closes.", proceed="Restart with UAC"):
            return
        self._active_action = "elevation"
        self.controller.elevate()

    def _elevated(self) -> None:
        self._relaunching = True
        self.close()

    def _open_data(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.controller.locations.state)))

    def _export(self) -> None:
        documents = self.controller.locations.documents or self.controller.locations.profile / "Documents"
        default = documents / f"TATClean-diagnostics-{datetime.now():%Y%m%d}.json"
        filename, _filter = QFileDialog.getSaveFileName(self, "Export local diagnostics", str(default), "JSON diagnostics (*.json)")
        if filename:
            path = Path(filename)
            if path.suffix.casefold() != ".json":
                path = Path(str(path) + ".json")
                if path.exists() and not self._confirm("Overwrite diagnostic file?", f"The final JSON filename already exists:\n{path}\n\nIts current contents will be replaced.", irreversible=True, proceed="Overwrite file"):
                    return
            self.controller.export_diagnostics(path)

    def closeEvent(self, event) -> None:
        if self._allow_close:
            event.accept()
            return
        event.ignore()
        if self.controller.closing:
            return
        if (not self._relaunching and self.settings.has_unsaved_changes()
                and not self._confirm("Exit without saving preferences?", "The preferences edited on the Settings page have not been saved. Only those unsaved edits will be discarded; existing restore points remain intact.", proceed="Discard edits and exit")):
            return
        if (self.controller.tasks.busy and not self._relaunching
                and not self._confirm("Cancel operation and exit?", "The application will request cancellation and wait for its workers to finish safely. Native shell operations may not stop immediately. Deleted files and already-applied network changes cannot be undone; service rollback and priority restoration will be allowed to finish.", proceed="Cancel and exit")):
            return
        self.controller.set_window_size(self.width(), self.height())
        self.controller.request_shutdown()
        self.status.setText("Closing safely; waiting for background work and saved priority restoration…")

    def _shutdown_ready(self, warning: str) -> None:
        if warning:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("TAT Clean · Exit warning")
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText("Some exit-time restoration or preference saves were not completed. Failed restore points remain available in Settings.")
            box.setDetailedText(warning[:8000])
            box.exec()
        self._allow_close = True
        self.close()
