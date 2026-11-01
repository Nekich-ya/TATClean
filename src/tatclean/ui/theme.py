import os
from pathlib import Path

from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PyQt6.QtWidgets import QApplication

STYLESHEET = """
QWidget { color: #F5F5F7; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog, QWidget#root, QWidget#page { background: #000000; }
QWidget#sidebar { background: #080808; border-right: 1px solid #202020; }
QFrame#glass { background: rgba(20, 20, 20, 178); border: 1px solid #2A2A2C; border-radius: 12px; }
QFrame#divider { background: #262628; border: none; }
QLabel { background: transparent; border: none; }
QLabel#muted { color: #8E8E93; }
QLabel#eyebrow { color: #8E8E93; font-size: 10px; font-weight: 600; letter-spacing: 1.5px; }
QLabel#heading { font-size: 28px; font-weight: 600; color: #FFFFFF; }
QLabel#cardTitle { font-size: 14px; font-weight: 600; color: #FFFFFF; }
QLabel#metric { font-size: 34px; font-weight: 600; color: #FFFFFF; }
QLabel#pill { color: #D1D1D6; background: #171719; border: 1px solid #323234; border-radius: 11px; padding: 4px 10px; font-size: 11px; }
QAbstractScrollArea { background: #0C0C0D; border: 1px solid #29292B; border-radius: 12px; }
QScrollArea { background: #000000; border: none; }
QScrollArea > QWidget > QWidget { background: #000000; }
QTreeWidget, QTableWidget, QTableView { background: #0C0C0D; alternate-background-color: #121213; color: #E5E5EA; selection-background-color: #2D2D30; selection-color: #FFFFFF; outline: none; }
QTreeWidget::item, QTableWidget::item, QTableView::item { padding: 7px 8px; border: none; }
QTreeWidget::item:hover, QTableWidget::item:hover, QTableView::item:hover { background: #1B1B1D; }
QHeaderView { background: #161617; }
QHeaderView::section { background: #161617; color: #8E8E93; padding: 10px 8px; border: none; border-bottom: 1px solid #29292B; font-size: 11px; font-weight: 600; }
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox { background: #111112; color: #F5F5F7; border: 1px solid #343436; border-radius: 8px; padding: 8px 10px; selection-background-color: #444448; selection-color: #FFFFFF; }
QPlainTextEdit#code, QPlainTextEdit#activity { font-family: 'Consolas'; font-size: 12px; }
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #A1A1A6; }
QLineEdit:disabled, QTextEdit:disabled, QSpinBox:disabled, QComboBox:disabled { color: #636366; border-color: #262628; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox QAbstractItemView { background: #171719; border: 1px solid #343436; padding: 5px; selection-background-color: #343438; }
QPushButton { background: #1C1C1E; color: #FFFFFF; border: 1px solid #3A3A3C; border-radius: 8px; padding: 9px 16px; }
QPushButton:hover { background: #2C2C2E; }
QPushButton:focus { border-color: #D1D1D6; }
QPushButton:disabled { color: #636366; border-color: #29292B; }
QCheckBox { spacing: 9px; background: transparent; color: #D1D1D6; }
QCheckBox::indicator { width: 15px; height: 15px; }
QCheckBox::indicator, QTreeView::indicator { width: 15px; height: 15px; border: 1px solid #54545A; background: #111112; border-radius: 4px; }
QCheckBox::indicator:hover, QTreeView::indicator:hover { border-color: #D1D1D6; }
QCheckBox::indicator:checked, QTreeView::indicator:checked { background: #E5E5EA; border-color: #E5E5EA; image: url("__CHECK_ICON__"); }
QCheckBox::indicator:indeterminate, QTreeView::indicator:indeterminate { background: #B1B1B6; border-color: #B1B1B6; image: url("__PARTIAL_ICON__"); }
QCheckBox::indicator:disabled, QTreeView::indicator:disabled { border-color: #303034; background: #171719; }
QCheckBox::indicator:checked:disabled, QTreeView::indicator:checked:disabled { background: #636366; border-color: #636366; }
QCheckBox:disabled { color: #636366; }
QProgressBar { background: #29292B; border: none; border-radius: 2px; text-align: center; }
QProgressBar::chunk { background: #D1D1D6; border-radius: 2px; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 5px 0; }
QScrollBar::handle:vertical { background: #414144; min-height: 28px; border-radius: 3px; }
QScrollBar::handle:vertical:hover { background: #636366; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QScrollBar:horizontal { background: transparent; height: 7px; margin: 0 5px; }
QScrollBar::handle:horizontal { background: #414144; min-width: 28px; border-radius: 3px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }
QToolTip { background: #222224; color: #F5F5F7; border: 1px solid #48484A; padding: 8px; }
"""


def apply_theme(application: QApplication) -> None:
    # Qt's offscreen platform on Windows may not discover native system fonts.
    # Register installed fonts in-process only; no fonts are installed or redistributed.
    if os.name == "nt":
        from tatclean.native.system import windows_directory
        fonts = windows_directory() / "Fonts"
        families = set(QFontDatabase.families())
        for family, filenames in (
            ("Segoe UI", ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf")),
            ("Consolas", ("consola.ttf", "consolab.ttf")),
        ):
            if family not in families:
                for filename in filenames:
                    path = fonts / filename
                    if path.is_file():
                        QFontDatabase.addApplicationFont(str(path))
    application.setStyle("Fusion")
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, "#000000"),
        (QPalette.ColorRole.WindowText, "#F5F5F7"),
        (QPalette.ColorRole.Base, "#111112"),
        (QPalette.ColorRole.AlternateBase, "#171719"),
        (QPalette.ColorRole.Text, "#F5F5F7"),
        (QPalette.ColorRole.Button, "#1C1C1E"),
        (QPalette.ColorRole.ButtonText, "#FFFFFF"),
        (QPalette.ColorRole.Highlight, "#454548"),
        (QPalette.ColorRole.HighlightedText, "#FFFFFF"),
        (QPalette.ColorRole.ToolTipBase, "#222224"),
        (QPalette.ColorRole.ToolTipText, "#FFFFFF"),
        (QPalette.ColorRole.Light, "#5A5A5E"),
        (QPalette.ColorRole.Dark, "#080808"),
        (QPalette.ColorRole.Link, "#FFFFFF"),
    ):
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#636366"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#636366"))
    application.setPalette(palette)
    font = QFont("Segoe UI", 10)
    application.setFont(font)
    assets = Path(__file__).resolve().parent / "assets"
    stylesheet = STYLESHEET.replace("__CHECK_ICON__", (assets / "check.svg").as_posix())
    stylesheet = stylesheet.replace("__PARTIAL_ICON__", (assets / "partial.svg").as_posix())
    application.setStyleSheet(stylesheet)
