from __future__ import annotations

from functools import lru_cache

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

PATHS = {
    "dashboard": '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    "clean": '<path d="m14 3 7 7-10 10-7 1 1-7Z"/><path d="m12 5 7 7M4 17l3 3M17 3l4 4"/>',
    "performance": '<path d="M3 17a9 9 0 1 1 18 0"/><path d="m12 14 5-6"/><circle cx="12" cy="15" r="2"/><path d="M5 20h14"/>',
    "ram": '<rect x="5" y="5" width="14" height="14" rx="3"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 2v3m6-3v3M9 19v3m6-3v3M2 9h3m-3 6h3m14-6h3m-3 6h3"/>',
    "settings": '<path d="m9 3-1 3-3 1v3l-2 2 2 2v3l3 1 1 3h6l1-3 3-1v-3l2-2-2-2V7l-3-1-1-3Z"/><circle cx="12" cy="12" r="3"/>',
    "shield": '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
    "disk": '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M3 14h18"/><circle cx="17" cy="17" r=".5"/><path d="M6 17h5"/>',
    "network": '<circle cx="12" cy="5" r="2"/><rect x="2" y="15" width="6" height="5" rx="1"/><rect x="16" y="15" width="6" height="5" rx="1"/><path d="M12 7v4M5 15v-4h14v4"/>',
    "trash": '<path d="M4 6h16M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7"/>',
    "refresh": '<path d="M20 7a8 8 0 1 0 0 10M20 3v5h-5"/>',
    "arrow": '<path d="M4 12h15m-5-5 5 5-5 5"/>',
    "close": '<path d="m6 6 12 12M6 18 18 6"/>',
    "chevron": '<path d="m8 5 7 7-7 7"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
    "game": '<path d="M7 7h10c3 0 5 10 4 12s-4 0-6-3H9c-2 3-5 5-6 3S4 7 7 7Z"/><path d="M7 10v5m-2-2h4m6-1h.01m3 2h.01"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
    "folder": '<path d="M3 6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v10H3Z"/>',
    "download": '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
}

BRAND_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256"><rect width="256" height="256" rx="54" fill="#080808"/><rect x="2" y="2" width="252" height="252" rx="52" fill="none" stroke="#55555a" stroke-width="4"/><path d="M62 72h132M128 72v114M67 186h122" fill="none" stroke="#ffffff" stroke-width="22" stroke-linecap="round"/><path d="M75 110h28M153 147h28" stroke="#8e8e93" stroke-width="10" stroke-linecap="round"/></svg>'


@lru_cache(maxsize=128)
def icon(name: str, color: str = "#D1D1D6", size: int = 24) -> QIcon:
    body = PATHS[name]
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    result = QIcon()
    for scale in (1, 2, 3):
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        result.addPixmap(pixmap)
    return result


def brand_icon() -> QIcon:
    renderer = QSvgRenderer(QByteArray(BRAND_SVG.encode()))
    result = QIcon()
    for size in (32, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        result.addPixmap(pixmap)
    return result
