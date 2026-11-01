from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from tatclean import __version__
from tatclean.core.config import Settings, validate_games
from tatclean.ui.widgets import Button, GlassCard, PageHeader, text_label


class SettingsPage(QWidget):
    save_requested = pyqtSignal(object)
    elevation_requested = pyqtSignal()
    action_requested = pyqtSignal(str, object)
    open_data_requested = pyqtSignal()
    export_requested = pyqtSignal()

    def __init__(self, settings: Settings, state_directory: Path, admin: bool):
        super().__init__()
        self.setObjectName("page")
        self._settings = settings
        self._admin = admin
        self._busy = True
        self._backup_count = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        header = PageHeader("Settings", "Local preferences, saved restore points and diagnostics. No telemetry or cloud account.")
        self.save = Button("Save preferences", kind="primary", symbol="check")
        self.save.clicked.connect(self._save)
        header.actions.addWidget(self.save)
        layout.addWidget(header)
        self.validation = text_label("", "muted", wrap=True)
        self.validation.hide()
        layout.addWidget(self.validation)

        policy = GlassCard()
        policy.body.addWidget(text_label("Cleanup policy", "cardTitle"))
        form = QFormLayout()
        form.setHorizontalSpacing(28)
        form.setVerticalSpacing(13)
        self.age = QSpinBox()
        self.age.setRange(1, 720)
        self.age.setSuffix(" hours")
        self.age.setMaximumWidth(200)
        self.age.setAccessibleName("Minimum cleanup file age in hours")
        form.addRow(text_label("Minimum file age", "muted"), self.age)
        self.retention = QSpinBox()
        self.retention.setRange(7, 90)
        self.retention.setSuffix(" days")
        self.retention.setMaximumWidth(200)
        self.retention.setAccessibleName("Action audit history retention in days")
        form.addRow(text_label("Action history retention", "muted"), self.retention)
        policy.body.addLayout(form)
        self.restore_exit = QCheckBox("Restore saved game priorities when TAT Clean closes")
        policy.body.addWidget(self.restore_exit)
        policy.body.addWidget(text_label("Targets may enforce a longer minimum age. Active-process, lock-file, profile-data and reparse-point safeguards cannot be disabled. File logs are size-rotated (five 2 MB backups); saved restore points are never age-pruned.", "muted", wrap=True))
        layout.addWidget(policy)

        games = GlassCard()
        games.body.addWidget(text_label("Recognized game executables", "cardTitle"))
        games.body.addWidget(text_label("One .exe filename per line—not full paths. Only these games can use the high-priority toggle. They also protect known shader caches while running; core Windows/security executables cannot be added.", "muted", wrap=True))
        self.games = QPlainTextEdit()
        self.games.setObjectName("code")
        self.games.setFont(QFont("Consolas", 10))
        self.games.setMinimumHeight(110)
        self.games.setMaximumHeight(150)
        self.games.setAccessibleName("Recognized game executable names, one per line")
        games.body.addWidget(self.games)
        layout.addWidget(games)

        restore = GlassCard()
        row = QHBoxLayout()
        description = QVBoxLayout()
        description.addWidget(text_label("Restore original tweaks", "cardTitle"))
        self.backup_status = text_label("Checking saved baselines…", "muted", wrap=True)
        description.addWidget(self.backup_status)
        description.addWidget(text_label("Restores Game DVR, listed services and live-game priorities changed by TAT Clean. Cache deletions, Recycle Bin contents and network resets cannot be undone. External configuration conflicts are reported, not overwritten.", "muted", wrap=True))
        row.addLayout(description, 1)
        self.restore_all = Button("Restore saved changes", symbol="refresh")
        self.restore_all.clicked.connect(lambda: self.action_requested.emit("restore_all", {}))
        row.addWidget(self.restore_all)
        restore.body.addLayout(row)
        layout.addWidget(restore)

        privilege = GlassCard()
        row = QHBoxLayout()
        column = QVBoxLayout()
        column.addWidget(text_label("Administrator privileges", "cardTitle"))
        column.addWidget(text_label("Currently elevated" if admin else "Currently using standard user access", "muted"))
        column.addWidget(text_label("Elevation is requested only when you choose it. No privileged background service is installed. Use the same Windows account for elevation so per-user baselines remain accessible.", "muted", wrap=True))
        row.addLayout(column, 1)
        self.elevate = Button("Restart as administrator", symbol="shield")
        self.elevate.clicked.connect(self.elevation_requested)
        row.addWidget(self.elevate)
        privilege.body.addLayout(row)
        layout.addWidget(privilege)

        tools = QHBoxLayout()
        self.open_data = Button("Open local data folder", symbol="folder")
        self.open_data.setToolTip(str(state_directory))
        self.open_data.clicked.connect(self.open_data_requested)
        self.export = Button("Export diagnostics", symbol="download")
        self.export.clicked.connect(self.export_requested)
        tools.addWidget(self.open_data)
        tools.addWidget(self.export)
        tools.addStretch()
        tools.addWidget(text_label(f"TAT Clean {__version__} · Windows 10 / 11", "muted"))
        layout.addLayout(tools)
        layout.addStretch()
        self.apply_settings(settings)
        self.set_busy(True)

    def apply_settings(self, settings: Settings) -> None:
        self._settings = settings
        self.age.setValue(settings.minimum_age_hours)
        self.retention.setValue(settings.audit_retention_days)
        self.restore_exit.setChecked(settings.restore_priority_on_exit)
        self.games.setPlainText("\n".join(settings.game_executables))
        self.validation.hide()

    def _save(self) -> None:
        try:
            names = validate_games(tuple(self.games.toPlainText().splitlines()))
        except ValueError as exc:
            self.validation.setText("Preferences were not saved: " + str(exc))
            self.validation.show()
            return
        settings = replace(self._settings, minimum_age_hours=self.age.value(),
                           audit_retention_days=self.retention.value(),
                           restore_priority_on_exit=self.restore_exit.isChecked(), game_executables=names)
        self.validation.hide()
        self.save_requested.emit(settings)

    def update_backups(self, keys: frozenset[str]) -> None:
        self._backup_count = len(keys)
        services = sum(key.startswith("service:") for key in keys)
        priorities = sum(key.startswith("priority:") for key in keys)
        recording = int("recording" in keys)
        gaming = sum(key.startswith("gaming:") for key in keys)
        self.backup_status.setText(f"{len(keys)} saved baselines · {recording} recording · {services} service · {priorities} process priority · {gaming} gaming/latency")
        self.restore_all.setEnabled(not self._busy and bool(keys))

    def has_unsaved_changes(self) -> bool:
        names = tuple(line.strip() for line in self.games.toPlainText().splitlines() if line.strip())
        return (
            self.age.value() != self._settings.minimum_age_hours
            or self.retention.value() != self._settings.audit_retention_days
            or self.restore_exit.isChecked() != self._settings.restore_priority_on_exit
            or names != self._settings.game_executables
        )

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget in (self.save, self.age, self.retention, self.restore_exit, self.games, self.open_data, self.export):
            widget.setEnabled(not busy)
        self.elevate.setEnabled(not busy and not self._admin)
        self.restore_all.setEnabled(not busy and self._backup_count > 0)
