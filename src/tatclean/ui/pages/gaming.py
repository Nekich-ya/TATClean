from __future__ import annotations

from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tatclean.core.gaming_models import GAMING_FEATURES, GamingRequest, GamingSnapshot, GamingState
from tatclean.ui.widgets import Button, GlassCard, PageHeader, text_label


class GamingCard(GlassCard):
    action_requested = pyqtSignal(str)
    restore_requested = pyqtSignal(str)

    def __init__(self, feature):
        super().__init__()
        self.feature = feature
        self.state: GamingState | None = None
        self._busy = True
        self.include = QCheckBox(feature.title)
        self.include.setChecked(feature.default_selected)
        self.include.setStyleSheet("font-weight: 600; font-size: 14px;")
        self.include.setToolTip("Include this specific change in Optimize Now. Nothing applies when a checkbox changes.")
        self.body.addWidget(self.include)
        self.body.addWidget(text_label(feature.description, "muted", wrap=True))
        self.body.addWidget(text_label(feature.warning, "muted", wrap=True))
        self.mode = QComboBox()
        if feature.key == "timer":
            self.mode.addItem("Request 0.5 ms", 500)
            self.mode.addItem("Request 1.0 ms", 1000)
        elif feature.key in ("hags", "game_mode"):
            self.mode.addItem("Enable", True)
            self.mode.addItem("Disable", False)
        else:
            self.mode.hide()
        self.mode.setAccessibleName(feature.title + " requested mode")
        self.body.addWidget(self.mode)
        self.body.addStretch()
        self.status = text_label("Reading current state…", "muted", wrap=True)
        self.body.addWidget(self.status)
        controls = QHBoxLayout()
        self.apply = Button("Apply only this", symbol="performance")
        self.apply.clicked.connect(lambda: self.action_requested.emit(feature.key))
        self.restore = Button("Release request" if feature.key == "timer" else "Revert original", symbol="refresh")
        self.restore.clicked.connect(lambda: self.restore_requested.emit(feature.key))
        controls.addWidget(self.apply)
        controls.addWidget(self.restore)
        self.body.addLayout(controls)
        self.set_busy(True)

    def update_state(self, state: GamingState) -> None:
        self.state = state
        self.status.setText(state.status + ("\n" + state.reason if state.reason else ""))
        self.set_busy(self._busy)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        available = self.state is not None and self.state.available
        self.include.setEnabled(not busy and available)
        self.apply.setEnabled(not busy and available)
        self.mode.setEnabled(not busy and available)
        self.restore.setEnabled(not busy and self.state is not None and self.state.saved)


class GamingPage(QWidget):
    action_requested = pyqtSignal(str, object)

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        self._busy = True
        self.snapshot: GamingSnapshot | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        header = PageHeader("Gaming & latency", "Selected, reversible policies—not promised FPS or ping gains. Benchmark each change and revert regressions.")
        self.boost = Button("Optimize Now", kind="primary", symbol="performance")
        self.boost.clicked.connect(self._boost)
        self.revert = Button("Revert gaming tweaks", symbol="refresh")
        self.revert.clicked.connect(lambda: self.action_requested.emit("gaming_restore", {}))
        header.actions.addWidget(self.revert)
        header.actions.addWidget(self.boost)
        layout.addWidget(header)
        protection = GlassCard(padding=16)
        row = QHBoxLayout()
        column = QVBoxLayout()
        column.addWidget(text_label("Checkpoint protection before changes", "cardTitle"))
        self.checkpoint = text_label("A verified System Restore point is required. Protection/quota policies are not bypassed. Exact backups are saved before every mutation.", "muted", wrap=True)
        column.addWidget(self.checkpoint)
        row.addLayout(column, 1)
        self.create_point = Button("Create restore point", symbol="shield")
        self.create_point.clicked.connect(lambda: self.action_requested.emit("create_restore_point", {}))
        row.addWidget(self.create_point)
        protection.body.addLayout(row)
        layout.addWidget(protection)

        adapters = GlassCard(padding=16)
        adapters.body.addWidget(text_label("TCP interface scope", "cardTitle"))
        adapters.body.addWidget(text_label("Choose exact IPv4 adapters for the Nagle experiment. This does not alter UDP traffic or silently modify every network interface.", "muted", wrap=True))
        self.adapters = QListWidget()
        self.adapters.setMinimumHeight(80)
        self.adapters.setMaximumHeight(130)
        self.adapters.setAccessibleName("IPv4 interfaces selected for TCP tuning")
        adapters.body.addWidget(self.adapters)
        layout.addWidget(adapters)

        grid = QGridLayout()
        grid.setSpacing(14)
        self.cards = {}
        for index, feature in enumerate(GAMING_FEATURES):
            card = GamingCard(feature)
            card.action_requested.connect(self._single)
            card.restore_requested.connect(self._restore)
            card.include.toggled.connect(self._buttons)
            self.cards[feature.key] = card
            grid.addWidget(card, index // 2, index % 2)
        layout.addLayout(grid)
        self.gpu_details = text_label("Reading GPU scheduling support…", "muted", wrap=True)
        layout.addWidget(self.gpu_details)
        layout.addWidget(text_label("Timer requests are always released when this app exits. Persistent registry/power/visual settings remain until you revert them. System Restore cannot restore deleted cache files. No overclocking, security-service disabling, BCDEdit or kernel timer-policy overrides are applied.", "muted", wrap=True))
        layout.addStretch()
        self.set_busy(True)

    def request(self, feature: str = "") -> GamingRequest:
        selected = (feature,) if feature else tuple(key for key, card in self.cards.items() if card.include.isChecked() and card.state and card.state.available)
        ids = tuple(self.adapters.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.adapters.count()) if self.adapters.item(index).checkState() == Qt.CheckState.Checked)
        request = GamingRequest(selected, self.cards["timer"].mode.currentData(), ids,
                                bool(self.cards["hags"].mode.currentData()), bool(self.cards["game_mode"].mode.currentData()))
        request.validate()
        return request

    def _boost(self) -> None:
        try:
            request = self.request()
        except ValueError as error:
            self.checkpoint.setText(str(error))
            return
        self.action_requested.emit("gaming_apply", {"request": request})

    def _single(self, feature: str) -> None:
        try:
            request = self.request(feature)
        except ValueError as error:
            self.checkpoint.setText(str(error))
            return
        self.action_requested.emit("gaming_apply", {"request": request})

    def _restore(self, feature: str) -> None:
        self.action_requested.emit("release_timer" if feature == "timer" else "gaming_restore", {} if feature == "timer" else {"feature": feature})

    def update_snapshot(self, snapshot: GamingSnapshot) -> None:
        self.snapshot = snapshot
        for state in snapshot.states:
            if state.key in self.cards:
                self.cards[state.key].update_state(state)
        prior = {self.adapters.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.adapters.count()) if self.adapters.item(index).checkState() == Qt.CheckState.Checked}
        was_empty = self.adapters.count() == 0
        blocker = QSignalBlocker(self.adapters)
        self.adapters.clear()
        selected_active = False
        for interface in snapshot.interfaces:
            item = QListWidgetItem(f"{interface.name} · {', '.join(interface.ipv4)} · {'up' if interface.active else 'inactive'}")
            item.setData(Qt.ItemDataRole.UserRole, interface.guid)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            selected = interface.guid in prior or (was_empty and interface.active and not selected_active)
            item.setCheckState(Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked)
            selected_active |= selected and interface.active
            self.adapters.addItem(item)
        del blocker
        if snapshot.timer:
            for index in range(self.cards["timer"].mode.count()):
                item = self.cards["timer"].mode.model().item(index)
                microseconds = self.cards["timer"].mode.itemData(index)
                item.setEnabled(snapshot.timer.finest_us <= microseconds <= snapshot.timer.coarsest_us)
        self.gpu_details.setText("\n".join(f"{gpu.name}: HAGS {'supported' if gpu.supported is True else 'unsupported' if gpu.supported is False else 'unknown'}; currently {'enabled' if gpu.enabled else 'disabled' if gpu.enabled is False else 'unknown'}. {gpu.reason}" for gpu in snapshot.gpus))
        if snapshot.checkpoint:
            self.checkpoint.setText(f"Verified session restore point #{snapshot.checkpoint.sequence}. It is re-verified before reuse (up to one hour); original tweak baselines are also durable.")
        self._buttons()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        for card in self.cards.values():
            card.set_busy(busy)
        self.adapters.setEnabled(not busy)
        self.create_point.setEnabled(not busy)
        self._buttons()

    def _buttons(self, *_args) -> None:
        self.boost.setEnabled(not self._busy and any(card.include.isChecked() and card.state and card.state.available for card in self.cards.values()))
        self.revert.setEnabled(not self._busy and any(card.state and card.state.saved for card in self.cards.values()))
