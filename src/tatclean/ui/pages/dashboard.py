from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from tatclean.core.models import Initialization, Metrics, ScanReport, format_bytes, format_duration
from tatclean.ui.widgets import Button, GlassCard, MetricCard, PageHeader, text_label


class DashboardPage(QWidget):
    scan_requested = pyqtSignal()
    performance_requested = pyqtSignal()
    optimize_requested = pyqtSignal()
    gaming_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)
        layout.addWidget(PageHeader("System overview", "Live measurements. Deliberate maintenance. Nothing runs without your approval."))
        metrics = QHBoxLayout()
        metrics.setSpacing(14)
        self.cpu = MetricCard("CPU utilization", "performance")
        self.memory = MetricCard("Memory in use", "ram")
        self.storage = MetricCard("System drive free", "disk")
        for card in (self.cpu, self.memory, self.storage):
            metrics.addWidget(card, 1)
        layout.addLayout(metrics)

        boost = GlassCard(padding=17)
        row = QHBoxLayout()
        column = QVBoxLayout()
        column.addWidget(text_label("Gaming optimization, with a rollback plan", "cardTitle"))
        column.addWidget(text_label("Optimize only the granular selections on Gaming & Latency. A verified restore point and saved baselines are required; experimental changes are opt-in.", "muted", wrap=True))
        row.addLayout(column, 1)
        self.customize = Button("Choose tweaks", symbol="settings")
        self.customize.clicked.connect(self.gaming_requested)
        self.optimize = Button("Optimize Now", kind="primary", symbol="performance")
        self.optimize.clicked.connect(self.optimize_requested)
        row.addWidget(self.customize)
        row.addWidget(self.optimize)
        boost.body.addLayout(row)
        layout.addWidget(boost)

        bottom = QHBoxLayout()
        bottom.setSpacing(14)
        preview = GlassCard()
        preview.body.addWidget(text_label("CLEANUP PREVIEW", "eyebrow"))
        self.candidates = text_label("—", "metric")
        preview.body.addWidget(self.candidates)
        self.preview_caption = text_label("Inspect disposable caches before deleting anything.", "muted", wrap=True)
        preview.body.addWidget(self.preview_caption)
        self.scan_button = Button("Scan standard targets", kind="primary", symbol="clean")
        self.scan_button.clicked.connect(self.scan_requested)
        preview.body.addWidget(self.scan_button)
        self.coverage = text_label("Discovering supported caches…", "muted", wrap=True)
        preview.body.addWidget(self.coverage)
        preview.body.addStretch()
        bottom.addWidget(preview, 1)

        system = GlassCard()
        system.body.addWidget(text_label("SYSTEM DETAILS", "eyebrow"))
        rows = QGridLayout()
        rows.setHorizontalSpacing(20)
        rows.setVerticalSpacing(13)
        self.details = {}
        for row, (key, label) in enumerate((
            ("os", "Windows"), ("cpu", "Processor"), ("cores", "CPU cores / threads"),
            ("architecture", "Architecture"), ("uptime", "Uptime"), ("processes", "Active processes"),
        )):
            rows.addWidget(text_label(label, "muted"), row, 0)
            value = text_label("—", wrap=True)
            rows.addWidget(value, row, 1)
            self.details[key] = value
        rows.setColumnStretch(1, 1)
        system.body.addLayout(rows)
        system.body.addStretch()
        bottom.addWidget(system, 2)
        layout.addLayout(bottom)

        guard = GlassCard(padding=17)
        row = QHBoxLayout()
        column = QVBoxLayout()
        column.addWidget(text_label("Protected by design", "cardTitle"))
        column.addWidget(text_label("Recent files, running applications, links and profile data are preserved. System changes have saved restore points where possible.", "muted", wrap=True))
        row.addLayout(column, 1)
        self.maintenance = Button("Maintenance tools", symbol="arrow")
        self.maintenance.clicked.connect(self.performance_requested)
        row.addWidget(self.maintenance)
        guard.body.addLayout(row)
        layout.addWidget(guard)
        layout.addStretch()
        self.set_busy(True)

    def set_busy(self, busy: bool) -> None:
        self.scan_button.setEnabled(not busy)
        self.maintenance.setEnabled(not busy)
        self.optimize.setEnabled(not busy)
        self.customize.setEnabled(not busy)

    def initialize(self, data: Initialization) -> None:
        for key in ("os", "cpu", "cores", "architecture"):
            self.details[key].setText(data.system_details.get(key, "Unavailable"))
        available = sum(target.available for target in data.targets)
        self.coverage.setText(f"{available} installed cache targets · {len(data.targets)} supported scopes")

    def update_metrics(self, metrics: Metrics) -> None:
        self.cpu.update_metric(f"{metrics.cpu_percent:.0f}%", metrics.cpu_percent, "Measured every 2 seconds")
        memory_percent = metrics.memory_used * 100 / max(1, metrics.memory_total)
        self.memory.update_metric(format_bytes(metrics.memory_used), memory_percent,
                                  f"{format_bytes(metrics.memory_available)} available of {format_bytes(metrics.memory_total)}")
        used_percent = (1 - metrics.drive_free / max(1, metrics.drive_total)) * 100
        self.storage.update_metric(format_bytes(metrics.drive_free), used_percent,
                                   f"{format_bytes(metrics.drive_total)} total · {used_percent:.0f}% occupied")
        self.details["uptime"].setText(format_duration(metrics.uptime))
        self.details["processes"].setText(f"{metrics.process_count:,}")

    def update_scan(self, report: ScanReport | None) -> None:
        self.candidates.setText(format_bytes(report.bytes) if report else "—")
        if report:
            state = "Scan complete" if report.completed else "Partial scan; rescan required"
            self.preview_caption.setText(f"{report.files:,} candidate files · {state}\nLogical sizes only; actual disk savings may differ.")
        else:
            self.preview_caption.setText("Inspect disposable caches before deleting anything.")
