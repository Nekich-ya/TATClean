from __future__ import annotations

from PyQt6.QtCore import QSignalBlocker, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QMenu, QWidget

from tatclean.core.models import VolumeInfo
from tatclean.ui.widgets import Button, text_label


class DriveSelector(QWidget):
    changed = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.actions: dict[str, QAction] = {}
        self.volumes: dict[str, VolumeInfo] = {}
        self._updating = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.all = QCheckBox("All local drives")
        self.all.setToolTip("Select all available fixed/RAM/removable drives. Network, optical and folder-mount reparse paths are never scanned.")
        self.all.toggled.connect(self._all_changed)
        layout.addWidget(self.all)
        self.picker = Button("Choose drives", symbol="disk")
        self.menu = QMenu(self)
        self.picker.setMenu(self.menu)
        layout.addWidget(self.picker)
        self.caption = text_label("System/profile drive scope", "muted", wrap=True)
        layout.addWidget(self.caption, 1)

    def initialize(self, volumes: tuple[VolumeInfo, ...], defaults: tuple[str, ...]) -> None:
        prior = self.selected() if self.actions else frozenset(defaults)
        was_all = self.all.isChecked()
        self._updating = True
        self.menu.clear()
        self.actions.clear()
        self.volumes = {volume.drive: volume for volume in volumes}
        for volume in volumes:
            title = f"{volume.drive} · {volume.drive_type}"
            action = self.menu.addAction(title)
            action.setCheckable(True)
            action.setEnabled(volume.cleanable)
            action.setChecked(volume.cleanable and (was_all or volume.drive in prior))
            action.setToolTip(volume.reason)
            action.toggled.connect(self._emit)
            self.actions[volume.drive] = action
        self._updating = False
        self._emit()

    def selected(self) -> frozenset[str]:
        return frozenset(drive for drive, action in self.actions.items() if action.isEnabled() and action.isChecked())

    def _all_changed(self, checked: bool) -> None:
        if self._updating:
            return
        self._updating = True
        for action in self.actions.values():
            action.setChecked(checked and action.isEnabled())
        self._updating = False
        self._emit()

    def _emit(self, *_args) -> None:
        if self._updating:
            return
        selected = self.selected()
        eligible = {drive for drive, action in self.actions.items() if action.isEnabled()}
        blocker = QSignalBlocker(self.all)
        self.all.setChecked(bool(eligible) and selected == eligible)
        del blocker
        self.caption.setText(", ".join(sorted(selected)) + " · disposable paths only" if selected else "No drive selected")
        self.changed.emit(selected)

    def set_busy(self, busy: bool) -> None:
        self.all.setEnabled(not busy)
        self.picker.setEnabled(not busy)
