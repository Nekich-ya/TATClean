from __future__ import annotations

from PyQt6.QtCore import (
    QAbstractTableModel,
    QItemSelectionModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from tatclean.core.actions import ActionService
from tatclean.core.models import Metrics, ProcessInfo, ServiceInfo, TweakSnapshot, format_bytes
from tatclean.native.processes import HIGH_PRIORITY_CLASS, PRIORITY_NAMES
from tatclean.native.services import SERVICE_DEFINITIONS, SERVICE_DISABLED
from tatclean.ui.widgets import Button, GlassCard, PageHeader, text_label

SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_ROOT_INDEX = QModelIndex()


class ProcessTableModel(QAbstractTableModel):
    HEADERS = ("PROCESS", "PID", "WORKING SET", "PRIORITY", "CONTEXT", "RESTORE POINT")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.processes: tuple[ProcessInfo, ...] = ()
        self.backups: frozenset[str] = frozenset()

    def update(self, processes: tuple[ProcessInfo, ...], backups: frozenset[str]) -> None:
        self.beginResetModel()
        self.processes = processes
        self.backups = backups
        self.endResetModel()

    def rowCount(self, parent=_ROOT_INDEX) -> int:
        return 0 if parent.isValid() else len(self.processes)

    def columnCount(self, parent=_ROOT_INDEX) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        if role == Qt.ItemDataRole.TextAlignmentRole and orientation == Qt.Orientation.Horizontal:
            horizontal = Qt.AlignmentFlag.AlignRight if section in (1, 2) else Qt.AlignmentFlag.AlignLeft
            return horizontal | Qt.AlignmentFlag.AlignVCenter
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.processes):
            return None
        process = self.processes[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return process
        if role == SORT_ROLE:
            return (process.name.casefold(), process.pid, process.memory, process.priority,
                    int(process.game), int(ActionService.priority_key(process) in self.backups))[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            context = "Game" if process.game else "User process"
            if process.foreground:
                context += " · foreground"
            if not process.manageable:
                context = "Protected"
            return (
                process.name, str(process.pid), format_bytes(process.memory),
                PRIORITY_NAMES.get(process.priority, "Unknown"), context,
                "Saved" if ActionService.priority_key(process) in self.backups else "—",
            )[index.column()]
        if role == Qt.ItemDataRole.ForegroundRole and not process.manageable:
            return QColor("#636366")
        if role == Qt.ItemDataRole.ToolTipRole:
            return process.protected_reason or f"{process.name} · PID {process.pid}\nOwner/session and process creation time are revalidated before any change."
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in (1, 2):
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        return None


class ProcessFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.query = ""
        self.games_only = False
        self.show_protected = False
        self.setSortRole(SORT_ROLE)

    def set_filters(self, query: str, games_only: bool, show_protected: bool) -> None:
        self.query, self.games_only, self.show_protected = query.casefold(), games_only, show_protected
        self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:
        model = self.sourceModel()
        process = model.processes[row]
        return (
            (not self.games_only or process.game)
            and (self.show_protected or process.manageable)
            and (not self.query or self.query in process.name.casefold() or self.query in str(process.pid))
        )


class ServiceCard(GlassCard):
    action_requested = pyqtSignal(str, object)

    def __init__(self, name: str, title: str, description: str):
        super().__init__()
        self.name = name
        self.info: ServiceInfo | None = None
        self._busy = True
        self._action = "disable_service"
        self.body.addWidget(text_label(title, "cardTitle", wrap=True))
        self.body.addWidget(text_label(description, "muted", wrap=True))
        self.body.addStretch()
        row = QHBoxLayout()
        self.status = text_label("Checking service…", "muted", wrap=True)
        row.addWidget(self.status, 1)
        self.button = Button("Disable", symbol="shield")
        self.button.clicked.connect(lambda: self.action_requested.emit(self._action, {"service": self.name}))
        row.addWidget(self.button)
        self.body.addLayout(row)
        self.button.setEnabled(False)

    def apply(self, info: ServiceInfo) -> None:
        self.info = info
        startup = {2: "automatic", 3: "manual", 4: "disabled"}.get(info.start_type, "unknown startup")
        self.status.setText(f"{info.state} · {startup}\nAdministrator required")
        self.status.setToolTip(info.error)
        self._update()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update()

    def _update(self) -> None:
        if self.info is None:
            self.button.setEnabled(False)
            return
        self._action = "restore_service" if self.info.backed_up else "disable_service"
        disabled_elsewhere = self.info.start_type == SERVICE_DISABLED and not self.info.backed_up
        self.button.setText("Restore original" if self.info.backed_up else "Already disabled" if disabled_elsewhere else "Disable")
        self.button.setEnabled(not self._busy and self.info.installed and not self.info.error and not disabled_elsewhere)


class RamServicesPage(QWidget):
    refresh_requested = pyqtSignal()
    action_requested = pyqtSignal(str, object)

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        self._busy = True
        self._tweaks: TweakSnapshot | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(17)
        header = PageHeader("RAM & services", "One-time process tools and reversible Windows toggles. No automatic memory purging.")
        self.refresh_button = Button("Refresh processes", symbol="refresh")
        self.refresh_button.clicked.connect(self.refresh_requested)
        header.actions.addWidget(self.refresh_button)
        layout.addWidget(header)
        memory = GlassCard(padding=14)
        memory.body.addWidget(text_label("AVAILABLE MEMORY", "eyebrow"))
        self.memory_label = text_label("Waiting for live metrics…", "cardTitle", wrap=True)
        memory.body.addWidget(self.memory_label)
        memory.body.addWidget(text_label("Working-set trimming reduces resident pages temporarily. Paging and reloading can make an application slower; use only when troubleshooting.", "muted", wrap=True))
        layout.addWidget(memory)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by executable name or PID")
        self.search.setAccessibleName("Filter running user processes")
        self.games = QCheckBox("Games only")
        self.protected = QCheckBox("Show protected")
        filters.addWidget(self.search, 1)
        filters.addWidget(self.games)
        filters.addWidget(self.protected)
        layout.addLayout(filters)
        self.model = ProcessTableModel(self)
        self.proxy = ProcessFilter(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(37)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 6):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(260)
        self.table.setMaximumHeight(330)
        self.table.setAccessibleName("Running user process working sets and priorities")
        self.table.selectionModel().selectionChanged.connect(self._update_buttons)
        self.table.horizontalHeader().sectionClicked.connect(self._sort)
        self._sort_order = Qt.SortOrder.AscendingOrder
        self._sort_column = -1
        layout.addWidget(self.table)
        for source in (self.search.textChanged, self.games.toggled, self.protected.toggled):
            source.connect(self._filter)
        controls = QHBoxLayout()
        self.count = text_label("Discovering user processes…", "muted", wrap=True)
        controls.addWidget(self.count, 1)
        self.trim = Button("Trim selected working sets", symbol="ram")
        self.trim.clicked.connect(lambda: self.action_requested.emit("trim_memory", {"processes": self.selected_processes()}))
        self.priority = Button("Enable game high priority", symbol="game")
        self.priority.clicked.connect(self._priority_clicked)
        controls.addWidget(self.trim)
        controls.addWidget(self.priority)
        layout.addLayout(controls)

        layout.addWidget(text_label("REVERSIBLE WINDOWS TOGGLES", "eyebrow"))
        cards = QGridLayout()
        cards.setSpacing(14)
        recording = GlassCard()
        recording.body.addWidget(text_label("Xbox Game Bar background capture", "cardTitle", wrap=True))
        recording.body.addWidget(text_label("Disable only Game DVR's per-user 'Record what happened' background flag. Manual capture/Game Bar are untouched. A verified restore point needs administrator access; restart affected apps to apply.", "muted", wrap=True))
        recording.body.addStretch()
        row = QHBoxLayout()
        self.recording_status = text_label("Checking recording settings…", "muted", wrap=True)
        row.addWidget(self.recording_status, 1)
        self.recording_button = Button("Disable capture", symbol="game")
        self.recording_button.clicked.connect(self._recording_clicked)
        row.addWidget(self.recording_button)
        recording.body.addLayout(row)
        cards.addWidget(recording, 0, 0)
        self.service_cards = {}
        for index, (name, (title, description)) in enumerate(SERVICE_DEFINITIONS.items(), start=1):
            card = ServiceCard(name, title, description)
            card.action_requested.connect(self.action_requested)
            self.service_cards[name] = card
            cards.addWidget(card, index // 2, index % 2)
        layout.addLayout(cards)
        layout.addWidget(text_label("Antivirus, firewall, Windows Update, core OS services and system processes are not offered as optimization toggles. On a managed computer, consult your administrator before changing any service.", "muted", wrap=True))
        layout.addStretch()
        self.set_busy(True)

    def _sort(self, column: int) -> None:
        self._sort_order = Qt.SortOrder.DescendingOrder if self._sort_column == column and self._sort_order == Qt.SortOrder.AscendingOrder else Qt.SortOrder.AscendingOrder
        self._sort_column = column
        self.proxy.sort(column, self._sort_order)
        self.table.horizontalHeader().setSortIndicator(column, self._sort_order)
        self.table.horizontalHeader().setSortIndicatorShown(True)

    def _filter(self, *_args) -> None:
        self.proxy.set_filters(self.search.text(), self.games.isChecked(), self.protected.isChecked())
        self._update_buttons()

    def selected_processes(self) -> tuple[ProcessInfo, ...]:
        return tuple(index.data(Qt.ItemDataRole.UserRole) for index in self.table.selectionModel().selectedRows())

    def update_processes(self, processes: tuple[ProcessInfo, ...], backups: frozenset[str]) -> None:
        selected = {(process.pid, process.create_time) for process in self.selected_processes()}
        self.model.update(processes, backups)
        for row in range(self.proxy.rowCount()):
            index = self.proxy.index(row, 0)
            process = index.data(Qt.ItemDataRole.UserRole)
            if (process.pid, process.create_time) in selected:
                self.table.selectionModel().select(index, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.count.setText(f"{len(processes):,} user processes · select specific rows")
        self._update_buttons()

    def update_tweaks(self, snapshot: TweakSnapshot) -> None:
        self._tweaks = snapshot
        for info in snapshot.services:
            self.service_cards[info.name].apply(info)
        self.recording_status.setText("Disabled" if snapshot.recording_disabled else "Not disabled by TAT Clean")
        self.recording_status.setToolTip(snapshot.recording_error)
        self._update_buttons()

    def update_metrics(self, metrics: Metrics) -> None:
        self.memory_label.setText(f"{format_bytes(metrics.memory_available)} available of {format_bytes(metrics.memory_total)} · {format_bytes(metrics.memory_used)} in use")

    def _priority_clicked(self) -> None:
        selected = self.selected_processes()
        if len(selected) != 1:
            return
        process = selected[0]
        action = "restore_priority" if ActionService.priority_key(process) in self.model.backups else "enable_priority"
        self.action_requested.emit(action, {"process": process})

    def _recording_clicked(self) -> None:
        action = "restore_recording" if self._tweaks and self._tweaks.recording_backed_up else "disable_recording"
        self.action_requested.emit(action, {})

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.refresh_button.setEnabled(not busy)
        for widget in (self.search, self.games, self.protected, self.table):
            widget.setEnabled(not busy)
        for card in self.service_cards.values():
            card.set_busy(busy)
        self._update_buttons()

    def _update_buttons(self, *_args) -> None:
        selected = self.selected_processes()
        self.trim.setEnabled(not self._busy and 1 <= len(selected) <= 100 and all(process.manageable for process in selected))
        game = len(selected) == 1 and selected[0].manageable and selected[0].game
        saved = game and ActionService.priority_key(selected[0]) in self.model.backups
        already_high = game and selected[0].priority == HIGH_PRIORITY_CLASS and not saved
        self.priority.setText("Restore game priority" if saved else "Already high priority" if already_high else "Enable game high priority")
        self.priority.setEnabled(not self._busy and game and not already_high)
        snapshot = self._tweaks
        self.recording_button.setText("Restore original" if snapshot and snapshot.recording_backed_up else "Already disabled" if snapshot and snapshot.recording_disabled else "Disable capture")
        self.recording_button.setEnabled(not self._busy and snapshot is not None and not snapshot.recording_error and (not snapshot.recording_disabled or snapshot.recording_backed_up))
