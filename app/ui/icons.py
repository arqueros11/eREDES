"""Iconos (Material Design vía qtawesome) y logotipo de eREDES dibujado vectorialmente."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap

from . import theme

try:
    import qtawesome as qta
except Exception:  # noqa: BLE001
    qta = None

NAV = {
    "scanner": "mdi6.radar",
    "printers": "mdi6.printer-outline",
    "drivers": "mdi6.folder-cog-outline",
    "recovery": "mdi6.ip-network-outline",
    "diagnostics": "mdi6.stethoscope",
    "speedtest": "mdi6.speedometer",
    "monitor": "mdi6.chart-areaspline",
    "reports": "mdi6.file-document-outline",
    "settings": "mdi6.cog-outline",
}


def icon(name: str, color: str | None = None, size: int | None = None) -> QIcon:
    if qta is None:
        return QIcon()
    try:
        return qta.icon(name, color=color or theme.current().text)
    except Exception:  # noqa: BLE001
        return QIcon()


def pixmap(name: str, color: str, size: int = 20) -> QPixmap:
    return icon(name, color).pixmap(size, size)


def logo_mark(height: int = 32, dark: bool | None = None) -> QPixmap:
    """Logotipo oficial 'eR' (fondo transparente). Versión clara para el modo oscuro."""
    from app.core.paths import resource_dir
    if dark is None:
        dark = theme.current().name == "dark"
    pm = QPixmap(str(resource_dir() / ("logo_dark.png" if dark else "logo.png")))
    if pm.isNull():
        return QPixmap()
    return _hidpi(pm.scaledToHeight(height * 2, Qt.SmoothTransformation))


def _hidpi(pm: QPixmap) -> QPixmap:
    pm.setDevicePixelRatio(2.0)
    return pm


def logo_pixmap(size: int = 64) -> QPixmap:
    """Icono de la aplicación: logo 'eR' sobre cuadrado blanco redondeado (estilo macOS)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    m = size * 0.09  # margen de la rejilla de iconos de macOS
    r = QRectF(m, m, size - 2 * m, size - 2 * m)
    path = QPainterPath()
    path.addRoundedRect(r, r.width() * 0.225, r.width() * 0.225)
    p.fillPath(path, QColor("#FFFFFF"))
    p.setPen(QColor("#E5E5EA"))
    p.drawPath(path)
    from app.core.paths import resource_dir
    logo = QPixmap(str(resource_dir() / "logo.png"))
    if not logo.isNull():
        w = r.width() * 0.70
        h = w * logo.height() / logo.width()
        p.drawPixmap(QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h).toRect(),
                     logo.scaledToWidth(int(w * 2), Qt.SmoothTransformation))
    p.end()
    return pm


def app_icon() -> QIcon:
    ic = QIcon()
    for s in (16, 32, 64, 128, 256, 512):
        ic.addPixmap(logo_pixmap(s))
    return ic


def ensure_check_image() -> str:
    """PNG de la marca de verificación para las casillas (QSS necesita un archivo)."""
    from app.core import paths
    f = paths.data_dir() / "ui_check.png"
    if not f.exists():
        pm = pixmap("mdi6.check-bold", "#FFFFFF", 32)
        pm.save(str(f))
    return f.as_posix()
