from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRect, QRectF, QSize, Qt, pyqtProperty
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tatclean.ui.icons import icon


def text_label(text: str = "", style: str = "", *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(wrap)
    if style:
        label.setObjectName(style)
    return label


class Button(QPushButton):
    def __init__(self, text: str, *, kind: str = "secondary", symbol: str = "", parent=None):
        super().__init__(text, parent)
        self.kind = kind
        self._hover = 0.0
        self._animation = QPropertyAnimation(self, b"hoverAmount", self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(text)
        self.setFixedHeight(40)
        if symbol:
            self.setIcon(icon(symbol, "#111112" if kind == "primary" else "#D1D1D6"))
        self.setIconSize(QSize(17, 17))
        if kind == "nav":
            self.setCheckable(True)
            self.setFixedHeight(46)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    @pyqtProperty(float)
    def hoverAmount(self) -> float:
        return self._hover

    @hoverAmount.setter
    def hoverAmount(self, value: float) -> None:
        self._hover = value
        self.update()

    def _animate(self, destination: float) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._hover)
        self._animation.setEndValue(destination)
        self._animation.start()

    def enterEvent(self, event) -> None:
        self._animate(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._animate(0.0)
        super().leaveEvent(event)

    def sizeHint(self) -> QSize:
        if self.kind == "nav":
            return QSize(190, 46)
        width = self.fontMetrics().horizontalAdvance(self.text()) + 36
        if not self.icon().isNull():
            width += 26
        return QSize(width, 40)

    def setText(self, text: str) -> None:
        super().setText(text)
        self.setAccessibleName(text)
        self.updateGeometry()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        primary, nav = self.kind == "primary", self.kind == "nav"
        if primary:
            base, hover, foreground = QColor("#E5E5EA"), QColor("#FFFFFF"), QColor("#111112")
        elif nav:
            base = QColor("#202022") if self.isChecked() else QColor("#080808")
            hover, foreground = QColor("#29292C"), QColor("#FFFFFF" if self.isChecked() else "#8E8E93")
        else:
            base = QColor("#101011" if self.kind == "ghost" else "#1C1C1E")
            hover, foreground = QColor("#2C2C2E"), QColor("#E5E5EA")
        if not self.isEnabled():
            base, hover, foreground = QColor("#171719"), QColor("#171719"), QColor("#5A5A5E")
        fraction = self._hover if self.isEnabled() else 0
        background = QColor.fromRgbF(*(
            first + (second - first) * fraction
            for first, second in zip(base.getRgbF()[:3], hover.getRgbF()[:3], strict=True)
        ))
        if self.isDown():
            background = background.darker(115)
        border = QColor("#D1D1D6" if self.hasFocus() else "#363638")
        if nav or self.kind == "ghost":
            border = QColor("#D1D1D6") if self.hasFocus() else background
        painter.setPen(QPen(border, 1))
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12 if nav else 9, 12 if nav else 9)
        font = self.font()
        font.setWeight(QFont.Weight.DemiBold if primary or self.isChecked() else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(foreground)
        symbol_size = 19 if nav else 17
        text_width = painter.fontMetrics().horizontalAdvance(self.text())
        has_icon = not self.icon().isNull()
        content_width = text_width + (symbol_size + 9 if has_icon else 0)
        x = 15 if nav else max(12, (self.width() - content_width) // 2)
        if has_icon:
            self.icon().paint(painter, QRect(x, (self.height() - symbol_size) // 2, symbol_size, symbol_size),
                              Qt.AlignmentFlag.AlignCenter, QIcon.Mode.Normal if self.isEnabled() else QIcon.Mode.Disabled)
            x += symbol_size + (12 if nav else 9)
        painter.drawText(QRect(x, 0, max(0, self.width() - x - 10), self.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())


class GlassCard(QFrame):
    def __init__(self, parent=None, *, padding: int = 20):
        super().__init__(parent)
        self.setObjectName("glass")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(12)


class MetricCard(GlassCard):
    def __init__(self, title: str, symbol: str):
        super().__init__()
        row = QHBoxLayout()
        row.addWidget(text_label(title, "muted"))
        row.addStretch()
        graphic = QLabel()
        graphic.setPixmap(icon(symbol).pixmap(20, 20))
        row.addWidget(graphic)
        self.body.addLayout(row)
        self.value = text_label("—", "metric")
        self.body.addWidget(self.value)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.body.addWidget(self.bar)
        self.caption = text_label("Waiting for live metrics…", "muted", wrap=True)
        self.body.addWidget(self.caption)
        self.setMinimumHeight(175)

    def update_metric(self, value: str, percent: float, caption: str) -> None:
        self.value.setText(value)
        self.bar.setValue(max(0, min(100, round(percent))))
        self.caption.setText(caption)


class PageHeader(QWidget):
    def __init__(self, title: str, caption: str):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        column = QVBoxLayout()
        column.setSpacing(7)
        column.addWidget(text_label(title, "heading"))
        column.addWidget(text_label(caption, "muted", wrap=True))
        layout.addLayout(column, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(9)
        layout.addLayout(self.actions)


def scroll_page(content: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(content)
    return area


class ConfirmDialog(QDialog):
    def __init__(
        self, title: str, message: str, *, irreversible: bool = False,
        proceed_text: str = "Continue", parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("TAT Clean · Confirm operation")
        self.setModal(True)
        self.setMinimumWidth(540)
        self.setMaximumWidth(720)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 26, 26, 26)
        layout.setSpacing(18)
        top = QHBoxLayout()
        graphic = QLabel()
        graphic.setPixmap(icon("shield", "#FFFFFF", 28).pixmap(28, 28))
        top.addWidget(graphic)
        title_label = text_label(title, "cardTitle", wrap=True)
        top.addWidget(title_label, 1)
        layout.addLayout(top)
        body = text_label(message, "muted", wrap=True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 0)
        content_layout.addWidget(body)
        content_layout.addStretch()
        self.message_scroll = QScrollArea()
        self.message_scroll.setWidgetResizable(True)
        self.message_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.message_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.message_scroll.setWidget(content)
        self.message_scroll.setFixedHeight(min(380, max(100, self.screen().availableGeometry().height() - 250)))
        layout.addWidget(self.message_scroll)
        self.acknowledge = QCheckBox("I understand this operation cannot be undone by TAT Clean.")
        self.acknowledge.setVisible(irreversible)
        layout.addWidget(self.acknowledge)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = Button("Cancel")
        cancel.clicked.connect(self.reject)
        self.proceed = Button(proceed_text, kind="primary")
        self.proceed.setEnabled(not irreversible)
        self.proceed.clicked.connect(self.accept)
        self.acknowledge.toggled.connect(self.proceed.setEnabled)
        buttons.addWidget(cancel)
        buttons.addWidget(self.proceed)
        layout.addLayout(buttons)
        cancel.setFocus()


class FeedbackBar(GlassCard):
    def __init__(self):
        super().__init__(padding=14)
        self.headline = text_label("", "cardTitle", wrap=True)
        self.message = text_label("", "muted", wrap=True)
        row = QHBoxLayout()
        row.addWidget(self.headline, 1)
        close = Button("Dismiss", kind="ghost")
        close.setFixedHeight(30)
        close.clicked.connect(self.hide)
        row.addWidget(close)
        self.body.addLayout(row)
        self.body.addWidget(self.message)
        self.hide()

    def show_message(self, title: str, message: str) -> None:
        self.headline.setText(title)
        self.message.setText(message)
        self.show()


class ActivityLog(QWidget):
    def __init__(self):
        super().__init__()
        self._expanded = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        header = QHBoxLayout()
        header.addWidget(text_label("ACTIVITY LOG", "eyebrow"))
        header.addStretch()
        clear = Button("Clear view", kind="ghost")
        clear.setFixedHeight(28)
        self.toggle = Button("Hide log", kind="ghost")
        self.toggle.setFixedHeight(28)
        header.addWidget(clear)
        header.addWidget(self.toggle)
        layout.addLayout(header)
        self.output = QPlainTextEdit()
        self.output.setObjectName("activity")
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(700)
        self.output.setMinimumHeight(95)
        self.output.setMaximumHeight(135)
        self.output.setFont(QFont("Consolas", 9))
        self.output.setAccessibleName("System maintenance activity log")
        layout.addWidget(self.output)
        clear.clicked.connect(self.output.clear)
        self.toggle.clicked.connect(self._toggle)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self.output.setVisible(self._expanded)
        self.toggle.setText("Hide log" if self._expanded else "Show log")

    def append(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        # Plain text, not HTML; subprocess output cannot inject UI markup.
        message = message.replace(chr(0), "")
        if len(message) > 8000:
            message = message[:8000] + "\n[UI preview truncated; full output is in the local file log.]"
        self.output.appendPlainText(f"{timestamp}  {message}")
