from __future__ import annotations

from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tatclean.core.catalog import CATEGORIES
from tatclean.core.models import (
    CleanupTarget,
    Risk,
    ScanReport,
    TaskProgress,
    VolumeInfo,
    format_bytes,
)
from tatclean.core.paths import normalize
from tatclean.ui.drive_selector import DriveSelector
from tatclean.ui.widgets import Button, GlassCard, PageHeader, text_label


class CleanerPage(QWidget):
    scan_requested = pyqtSignal(object)
    clean_requested = pyqtSignal()
    refresh_requested = pyqtSignal()
    selection_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        self.targets: dict[str, CleanupTarget] = {}
        self.items: dict[str, QTreeWidgetItem] = {}
        self.report: ScanReport | None = None
        self._busy = True
        self._populated = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(15)
        header = PageHeader("Deep cleaner", "Choose precise cache scopes. Scan first, review the estimate, then clean.")
        self.refresh_button = Button("Refresh targets", symbol="refresh")
        self.refresh_button.clicked.connect(self.refresh_requested)
        header.actions.addWidget(self.refresh_button)
        layout.addWidget(header)

        summary = GlassCard(padding=14)
        row = QHBoxLayout()
        for title, attribute in (("ESTIMATED CANDIDATES", "size_label"), ("ELIGIBLE FILES", "files_label")):
            column = QVBoxLayout()
            column.setSpacing(4)
            column.addWidget(text_label(title, "eyebrow"))
            value = text_label("—")
            value.setStyleSheet("font-size: 23px; font-weight: 600;")
            setattr(self, attribute, value)
            column.addWidget(value)
            row.addLayout(column, 1)
        rule = QVBoxLayout()
        rule.addWidget(text_label("SAFE CLEANUP GUARD", "eyebrow"))
        self.age_rule = text_label("Recent files protected · minimum age 24h", "muted", wrap=True)
        rule.addWidget(self.age_rule)
        row.addLayout(rule, 2)
        summary.body.addLayout(row)
        layout.addWidget(summary)
        self.drives = DriveSelector()
        self.drives.changed.connect(self._drive_changed)
        layout.addWidget(self.drives)

        toolbar = QHBoxLayout()
        self.standard = Button("Standard only", kind="ghost")
        self.none = Button("Select none", kind="ghost")
        self.standard.clicked.connect(self.select_standard)
        self.none.clicked.connect(self.select_none)
        toolbar.addWidget(self.standard)
        toolbar.addWidget(self.none)
        self.installed_only = QCheckBox("Installed caches only")
        self.installed_only.setChecked(True)
        self.installed_only.toggled.connect(self._filter)
        toolbar.addWidget(self.installed_only)
        toolbar.addStretch()
        self.scan_button = Button("Scan selected", symbol="refresh")
        self.scan_button.clicked.connect(lambda: self.scan_requested.emit(self.selected_ids()))
        self.clean_button = Button("Clean reviewed files", kind="primary", symbol="clean")
        self.clean_button.clicked.connect(self.clean_requested)
        toolbar.addWidget(self.scan_button)
        toolbar.addWidget(self.clean_button)
        layout.addLayout(toolbar)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(("CACHE TARGET", "CANDIDATES", "FILES", "STATUS"))
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setMinimumHeight(170)
        self.tree.setAccessibleName("Cleanup categories and cache target selection")
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.tree.setColumnWidth(3, 220)
        self.tree.itemChanged.connect(self._selection_changed)
        layout.addWidget(self.tree, 1)
        self.hint = text_label("Loading local cache scopes…", "muted", wrap=True)
        layout.addWidget(self.hint)
        self._update_buttons()

    def initialize(self, targets: tuple[CleanupTarget, ...], age_hours: int, mounted: tuple[VolumeInfo, ...] = (), default_drives: tuple[str, ...] = ()) -> None:
        previous = set(self.selected_ids()) if self._populated else None
        blocker = QSignalBlocker(self.tree)
        self.tree.clear()
        self.targets = {target.id: target for target in targets}
        self.items.clear()
        for category in CATEGORIES:
            group = QTreeWidgetItem(self.tree, (category, "", "", ""))
            group.setFlags(group.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
            group.setCheckState(0, Qt.CheckState.Unchecked)
            for target in targets:
                if target.category != category:
                    continue
                suffix = " · opt-in" if target.risk == Risk.CAUTION else ""
                if target.requires_admin:
                    suffix += " · admin"
                if target.volume_id:
                    suffix += " · " + target.volume_id
                item = QTreeWidgetItem(group, (target.label + suffix, "—", "—", "Ready to scan" if target.available else target.unavailable_reason or "Not installed / no cache"))
                item.setData(0, Qt.ItemDataRole.UserRole, target.id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                checked = target.default_selected if previous is None else target.id in previous
                item.setCheckState(0, Qt.CheckState.Checked if checked and target.available else Qt.CheckState.Unchecked)
                item.setToolTip(0, f"{target.description}\n\n{target.root}\nMinimum age: {max(age_hours, target.minimum_age_hours)} hours")
                item.setToolTip(3, target.unavailable_reason)
                if not target.available:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                self.items[target.id] = item
            group.setExpanded(True)
        del blocker
        self._populated = True
        self.drives.initialize(mounted, default_drives)
        self.age_rule.setText(f"Recent files protected · minimum age {age_hours}h\nSome targets enforce a longer age.")
        self._filter()
        self.clear_report()
        self.hint.setText("Only cache files are targeted. No profile data, links, directory roots or in-use files are forcibly removed. Estimates are logical sizes, not guaranteed disk savings.")

    def selected_ids(self) -> tuple[str, ...]:
        selected_drives = self.drives.selected()
        return tuple(key for key, item in self.items.items() if self.targets[key].available
                     and (not self.drives.actions or self.targets[key].volume_id in selected_drives)
                     and item.checkState(0) == Qt.CheckState.Checked)

    def _selection_changed(self, _item=None, column: int = 0) -> None:
        if column != 0:
            return
        if _item is not None and _item.checkState(0) == Qt.CheckState.Checked:
            target = self.targets.get(_item.data(0, Qt.ItemDataRole.UserRole))
            if target:
                blocker = QSignalBlocker(self.tree)
                for key, other in self.targets.items():
                    if key == target.id or normalize(other.root) != normalize(target.root):
                        continue
                    if (target.duplicate_only and not other.duplicate_only and other.matches(other.root / "example.dmp")) or (not target.duplicate_only and target.matches(target.root / "example.dmp") and other.duplicate_only):
                        self.items[key].setCheckState(0, Qt.CheckState.Unchecked)
                del blocker
        self.clear_report()
        self.selection_changed.emit()

    def _drive_changed(self, _drives) -> None:
        self._filter()
        self._selection_changed()

    def _filter(self, *_args) -> None:
        installed_only = self.installed_only.isChecked()
        selected = self.drives.selected()
        for key, item in self.items.items():
            item.setHidden((installed_only and not self.targets[key].available)
                           or (bool(self.drives.actions) and self.targets[key].volume_id not in selected))
        for index in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(index)
            group.setHidden(all(group.child(child).isHidden() for child in range(group.childCount())))

    def select_standard(self) -> None:
        blocker = QSignalBlocker(self.tree)
        for key, item in self.items.items():
            target = self.targets[key]
            item.setCheckState(0, Qt.CheckState.Checked if target.available and target.risk == Risk.STANDARD and target.default_selected else Qt.CheckState.Unchecked)
        del blocker
        self._selection_changed()

    def select_none(self) -> None:
        blocker = QSignalBlocker(self.tree)
        for item in self.items.values():
            item.setCheckState(0, Qt.CheckState.Unchecked)
        del blocker
        self._selection_changed()

    def clear_report(self) -> None:
        self.report = None
        self.size_label.setText("—")
        self.files_label.setText("—")
        self._update_buttons()

    def apply_scan(self, report: ScanReport) -> None:
        self.report = report
        self.size_label.setText(format_bytes(report.bytes))
        self.files_label.setText(f"{report.files:,}")
        for summary in report.summaries:
            item = self.items.get(summary.target_id)
            if item:
                item.setText(1, format_bytes(summary.bytes))
                item.setText(2, f"{summary.files:,}")
                item.setText(3, summary.status)
                reasons = "\n".join(f"{reason}: {count:,}" for reason, count in summary.skipped.items())
                item.setToolTip(3, summary.status + ("\n\n" + reasons if reasons else ""))
        self.hint.setText(
            "Review the selected scopes and estimates. Deletion is permanent; active/changed files will be skipped again."
            if report.completed else "Scan cancelled. Run another complete scan before cleaning."
        )
        if report.duplicate_files:
            self.hint.setText(self.hint.text() + f" {report.duplicate_files:,} duplicate dump copies ({format_bytes(report.duplicate_bytes)}); a verified copy is retained per group.")
        self._update_buttons()

    def update_progress(self, progress: TaskProgress) -> None:
        if progress.target_id in self.items and self._busy:
            self.items[progress.target_id].setText(3, progress.message)
            self.items[progress.target_id].setToolTip(3, progress.message)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.tree.setEnabled(not busy)
        self.drives.set_busy(busy)
        for widget in (self.standard, self.none, self.installed_only, self.refresh_button):
            widget.setEnabled(not busy)
        self._update_buttons()

    def _update_buttons(self) -> None:
        selection = self.selected_ids()
        self.scan_button.setEnabled(not self._busy and bool(selection))
        reviewed = self.report is not None and self.report.completed and self.report.files > 0 and self.report.target_ids == selection
        self.clean_button.setEnabled(not self._busy and reviewed)
