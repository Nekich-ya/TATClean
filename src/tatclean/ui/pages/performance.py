from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from tatclean.core.actions import ACTION_SPECS
from tatclean.core.models import VolumeInfo, format_bytes
from tatclean.ui.widgets import Button, GlassCard, PageHeader, text_label


class PerformancePage(QWidget):
    action_requested = pyqtSignal(str, object)

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        self.buttons: list[Button] = []
        self.volumes: dict[str, VolumeInfo] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(PageHeader("Performance tweaks", "Focused Windows maintenance tools—not unverified boost switches."))
        layout.addWidget(text_label("NETWORK REPAIR", "eyebrow"))
        network = QGridLayout()
        network.setSpacing(14)
        for column, (action, button_text) in enumerate((
            ("flush_dns", "Flush DNS"), ("clear_arp", "Clear ARP"), ("reset_winsock", "Reset Winsock"),
        )):
            spec = ACTION_SPECS[action]
            card = GlassCard()
            card.body.addWidget(text_label(spec.title, "cardTitle", wrap=True))
            card.body.addWidget(text_label(spec.description, "muted", wrap=True))
            card.body.addWidget(text_label(spec.warning, "muted", wrap=True))
            card.body.addStretch()
            if spec.requires_admin:
                card.body.addWidget(text_label("Administrator required" + (" · reboot" if action == "reset_winsock" else ""), "eyebrow"))
            button = Button(button_text, symbol="network")
            button.clicked.connect(lambda _checked=False, key=action: self.action_requested.emit(key, {}))
            card.body.addWidget(button)
            self.buttons.append(button)
            network.addWidget(card, 0, column)
        layout.addLayout(network)

        layout.addWidget(text_label("STORAGE MAINTENANCE", "eyebrow"))
        storage = GlassCard()
        row = QHBoxLayout()
        explanation = QVBoxLayout()
        explanation.addWidget(text_label("Windows-managed drive optimization", "cardTitle", wrap=True))
        explanation.addWidget(text_label("defrag /O selects the appropriate operation for the drive: TRIM/retrim where supported, or defragmentation where appropriate. Only fixed local drives are offered.", "muted", wrap=True))
        explanation.addWidget(text_label("Administrator required · disk activity may last several minutes", "eyebrow", wrap=True))
        row.addLayout(explanation, 2)
        controls = QVBoxLayout()
        self.drive = QComboBox()
        self.drive.setMinimumWidth(230)
        self.drive.setAccessibleName("Fixed drive to optimize")
        self.drive.currentIndexChanged.connect(self._drive_changed)
        controls.addWidget(self.drive)
        self.drive_details = text_label("Discovering fixed volumes…", "muted", wrap=True)
        controls.addWidget(self.drive_details)
        self.optimize = Button("Optimize selected drive", symbol="disk")
        self.optimize.clicked.connect(lambda: self.action_requested.emit("optimize_volume", {"drive": self.drive.currentData()}))
        controls.addWidget(self.optimize)
        self.buttons.append(self.optimize)
        row.addLayout(controls, 1)
        storage.body.addLayout(row)
        layout.addWidget(storage)

        recycle = GlassCard()
        row = QHBoxLayout()
        column = QVBoxLayout()
        column.addWidget(text_label("Empty Recycle Bin", "cardTitle"))
        column.addWidget(text_label("Permanently remove this user's recycled items on all drives. This is separate from cache cleaning and always requires explicit confirmation.", "muted", wrap=True))
        row.addLayout(column, 1)
        self.recycle = Button("Empty Recycle Bin", symbol="trash")
        self.recycle.clicked.connect(lambda: self.action_requested.emit("empty_recycle", {}))
        self.buttons.append(self.recycle)
        row.addWidget(self.recycle)
        recycle.body.addLayout(row)
        layout.addWidget(recycle)
        layout.addWidget(text_label("Network resets repair specific faults; they do not increase bandwidth. Cache removal primarily recovers storage, not CPU speed. Leave a healthy system's settings alone unless you have a reason to change them.", "muted", wrap=True))
        layout.addStretch()
        self.set_busy(True)

    def initialize(self, volumes: tuple[VolumeInfo, ...]) -> None:
        selected = self.drive.currentData()
        self.volumes = {volume.drive: volume for volume in volumes}
        self.drive.clear()
        for volume in volumes:
            self.drive.addItem(f"{volume.drive}  ·  {volume.media}", volume.drive)
        index = self.drive.findData(selected)
        if index >= 0:
            self.drive.setCurrentIndex(index)
        self._drive_changed()

    def _drive_changed(self, *_args) -> None:
        volume = self.volumes.get(self.drive.currentData())
        if volume:
            self.drive_details.setText(f"{volume.filesystem} · {format_bytes(volume.free)} free of {format_bytes(volume.total)}")
        else:
            self.drive_details.setText("No available fixed local volume")

    def set_busy(self, busy: bool) -> None:
        for button in self.buttons:
            button.setEnabled(not busy)
        self.drive.setEnabled(not busy)
        self.optimize.setEnabled(not busy and bool(self.volumes))
