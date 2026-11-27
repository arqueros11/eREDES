"""Componentes visuales reutilizables de eREDES."""
from __future__ import annotations

from collections import deque
from typing import Callable

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFrame,
                               QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from app.core.tasks import Task, run_task

from . import icons, theme


def hex_alpha(color: str, alpha: int) -> str:
    c = QColor(color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def button(text: str, kind: str = "", icon_name: str | None = None, on_click: Callable | None = None,
           tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if icon_name:
        color = "#FFFFFF" if kind == "Primary" else (theme.current().danger if kind == "Danger" else theme.current().text)
        b.setIcon(icons.icon(icon_name, color))
        b.setIconSize(QSize(18, 18))
    if on_click:
        b.clicked.connect(on_click)
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.PointingHandCursor)
    return b


class Card(QFrame):
    def __init__(self, title: str = "", subtitle: str = "", icon_name: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(18, 16, 18, 16)
        self.root.setSpacing(10)
        self.header = None
        if title:
            self.header = QHBoxLayout()
            self.header.setSpacing(10)
            if icon_name:
                ic = QLabel()
                ic.setPixmap(icons.pixmap(icon_name, theme.current().primary, 22))
                self.header.addWidget(ic)
            tl = QVBoxLayout()
            tl.setSpacing(0)
            t = QLabel(title)
            t.setObjectName("CardTitle")
            tl.addWidget(t)
            if subtitle:
                self.setToolTip(subtitle)  # el detalle queda como ayuda emergente, no como texto
            self.header.addLayout(tl, 1)
            self.root.addLayout(self.header)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        self.root.addLayout(self.body, 1)

    def add_header_widget(self, w: QWidget) -> None:
        if self.header is not None:
            self.header.addWidget(w)

    def add(self, w, stretch: int = 0):
        if isinstance(w, QWidget):
            self.body.addWidget(w, stretch)
        else:
            self.body.addLayout(w, stretch)
        return w


class StatCard(QFrame):
    clicked = Signal()

    def __init__(self, icon_name: str, label: str, value: str = "—", hint: str = "", color: str | None = None):
        super().__init__()
        self.setObjectName("Card")
        self.setCursor(Qt.PointingHandCursor)
        self.color = color or theme.current().primary
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(14)
        self.bubble = QLabel()
        self.bubble.setObjectName("IconBubble")
        self.bubble.setFixedSize(52, 52)
        self.bubble.setAlignment(Qt.AlignCenter)
        self.icon_name = icon_name
        self._paint_bubble()
        lay.addWidget(self.bubble, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.lbl = QLabel(label)
        self.lbl.setObjectName("StatLabel")
        self.val = QLabel(value)
        self.val.setObjectName("StatValue")
        self.hint = QLabel(hint)
        self.hint.setObjectName("StatHint")
        self.hint.setWordWrap(True)
        for w in (self.lbl, self.hint):
            w.setWordWrap(True)
            w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            w.setMinimumWidth(0)
        col.addWidget(self.lbl)
        col.addWidget(self.val)
        col.addWidget(self.hint)
        lay.addLayout(col, 1)
        self.setMinimumHeight(96)

    def _paint_bubble(self):
        self.bubble.setStyleSheet(f"background:{hex_alpha(self.color, 38)}; border-radius:12px;")
        self.bubble.setPixmap(icons.pixmap(self.icon_name, self.color, 28))

    def set(self, value: str, hint: str | None = None, color: str | None = None):
        self.val.setText(value)
        if hint is not None:
            self.hint.setText(hint)
        if color and color != self.color:
            self.color = color
            self._paint_bubble()

    def mousePressEvent(self, e):  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(e)


class Badge(QLabel):
    def __init__(self, text: str = "", color: str | None = None):
        super().__init__(text)
        self.setObjectName("Badge")
        self.setAlignment(Qt.AlignCenter)
        self.set(text, color or theme.current().muted)

    def set(self, text: str, color: str):
        self.setText(text)
        self.setStyleSheet(f"background:{hex_alpha(color, 40)}; color:{color}; border-radius:10px;"
                           f" padding:2px 9px; font-size:11px; font-weight:700;")


class Banner(QFrame):
    KINDS = {"info": ("mdi6.information-outline", "info"), "warning": ("mdi6.alert-circle", "warning"),
             "error": ("mdi6.close-circle", "danger"), "ok": ("mdi6.check-circle", "success")}

    def __init__(self, text: str = "", kind: str = "info"):
        super().__init__()
        self.setObjectName("Banner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(10)
        self.ic = QLabel()
        self.txt = QLabel(text)
        self.txt.setWordWrap(True)
        self.txt.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.ic, 0, Qt.AlignTop)
        lay.addWidget(self.txt, 1)
        self.set(text, kind)

    def set(self, text: str, kind: str = "info"):
        ic, attr = self.KINDS.get(kind, self.KINDS["info"])
        color = getattr(theme.current(), attr)
        self.setStyleSheet(f"#Banner {{ background:{hex_alpha(color, 26)}; border:1px solid {hex_alpha(color, 90)};"
                           f" border-radius:10px; }} QLabel {{ background: transparent; }}")
        self.ic.setPixmap(icons.pixmap(ic, color, 18))
        self.txt.setText(text)
        self.setVisible(bool(text))


SORT_ROLE = Qt.UserRole + 1


class _Item(QTableWidgetItem):
    """Celda que ordena por una clave numérica cuando existe (IPs, números)."""

    def __lt__(self, other):  # noqa: D105
        a, b = self.data(SORT_ROLE), other.data(SORT_ROLE)
        if a is not None and b is not None:
            return a < b
        return super().__lt__(other)


def _sort_key(val):
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    if isinstance(val, str) and val.count(".") == 3:
        parts = val.split(".")
        if all(p.isdigit() and int(p) < 256 for p in parts):
            return float(int.from_bytes(bytes(int(p) for p in parts), "big"))
    return None


def make_table(headers: list[str], stretch_col: int | None = None, selectable_rows: bool = True) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    t.setShowGrid(False)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    if selectable_rows:
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setWordWrap(False)
    t.setSortingEnabled(True)
    h = t.horizontalHeader()
    h.setHighlightSections(False)
    h.setSectionResizeMode(QHeaderView.Interactive)
    h.setStretchLastSection(stretch_col is None)
    if stretch_col is not None:
        h.setSectionResizeMode(stretch_col, QHeaderView.Stretch)
    t.verticalHeader().setDefaultSectionSize(40)
    return t


def fill_table(t: QTableWidget, rows: list[list], colors: dict[int, Callable[[str], str | None]] | None = None,
               data: list | None = None, autosize: bool | None = None) -> None:
    t.setUpdatesEnabled(False)
    t.setSortingEnabled(False)
    t.setRowCount(0)
    t.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            it = _Item("" if val is None else str(val))
            key = _sort_key(val)
            if key is not None:
                it.setData(SORT_ROLE, key)
            if colors and c in colors:
                col = colors[c](str(val))
                if col:
                    it.setForeground(QColor(col))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
            if c == 0 and data is not None:
                it.setData(Qt.UserRole, data[r])
            t.setItem(r, c, it)
    t.setSortingEnabled(True)
    # ajustar columnas solo la primera vez que hay datos (o si se pide): evita tirones al refrescar
    if autosize or (autosize is None and rows and not t.property("_sized")):
        t.resizeColumnsToContents()
        for c in range(t.columnCount()):
            if t.columnWidth(c) > 380:
                t.setColumnWidth(c, 380)
        t.setProperty("_sized", True)
    t.setUpdatesEnabled(True)


def selected_data(t: QTableWidget):
    rows = t.selectionModel().selectedRows() if t.selectionModel() else []
    if not rows:
        return None
    it = t.item(rows[0].row(), 0)
    return it.data(Qt.UserRole) if it else None


def filter_table(t: QTableWidget, text: str) -> None:
    text = text.lower().strip()
    for r in range(t.rowCount()):
        show = not text or any(text in (t.item(r, c).text().lower() if t.item(r, c) else "")
                               for c in range(t.columnCount()))
        t.setRowHidden(r, not show)


class Spinner(QWidget):
    """Indicador de actividad giratorio (estilo macOS). Solo consume CPU mientras está visible."""

    def __init__(self, size: int = 22):
        super().__init__()
        self.setFixedSize(size, size)
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)

    def _tick(self):
        self.angle = (self.angle + 9) % 360
        self.update()

    def showEvent(self, e):  # noqa: N802
        self.timer.start()
        super().showEvent(e)

    def hideEvent(self, e):  # noqa: N802
        self.timer.stop()
        super().hideEvent(e)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = theme.current()
        m = 3
        r = QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)
        p.setPen(QPen(QColor(pal.border), 3))
        p.drawEllipse(r)
        p.setPen(QPen(QColor(pal.primary), 3, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r, -self.angle * 16, 100 * 16)
        p.end()


class ProgressLine(QWidget):
    """Barra de progreso animada: avanza suavemente y, si no se conoce el avance,
    muestra un segmento en movimiento (nunca se queda quieta)."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(6)
        self.target = 0.0
        self.shown = 0.0
        self.phase = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)

    def setValue(self, v: int):  # noqa: N802
        self.target = max(0.0, min(100.0, float(v)))

    def reset(self):
        self.target = self.shown = 0.0

    def _tick(self):
        self.shown += (self.target - self.shown) * 0.18
        self.phase = (self.phase + 0.012) % 1.0
        self.update()

    def showEvent(self, e):  # noqa: N802
        self.timer.start()
        super().showEvent(e)

    def hideEvent(self, e):  # noqa: N802
        self.timer.stop()
        super().hideEvent(e)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = theme.current()
        w, h = self.width(), self.height()
        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        p.fillPath(track, QColor(pal.surface2))
        p.setClipPath(track)
        if self.shown > 0.5:
            fill = QPainterPath()
            fill.addRoundedRect(QRectF(0, 0, w * self.shown / 100, h), h / 2, h / 2)
            p.fillPath(fill, QColor(pal.primary))
        # brillo/segmento en movimiento: indica actividad aunque el porcentaje no cambie
        seg = w * 0.25
        x = -seg + (w + seg) * self.phase
        g = QColor(pal.primary if self.shown <= 0.5 else "#FFFFFF")
        g.setAlpha(255 if self.shown <= 0.5 else 90)
        seg_path = QPainterPath()
        seg_path.addRoundedRect(QRectF(x, 0, seg, h), h / 2, h / 2)
        p.fillPath(seg_path, g)
        p.end()


class TaskPanel(QFrame):
    """Barra de progreso + mensaje + botón Cancelar para una tarea en segundo plano."""
    finished = Signal()

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.spinner = Spinner(22)
        lay.addWidget(self.spinner)
        self.bar = ProgressLine()
        self.msg = QLabel("")
        self.msg.setObjectName("Muted")
        self.msg.setMinimumWidth(220)
        self.cancel_btn = button("Cancelar", "Ghost", "mdi6.stop", self._cancel)
        lay.addWidget(self.bar, 1)
        lay.addWidget(self.msg)
        lay.addWidget(self.cancel_btn)
        self.task: Task | None = None
        self.hide()

    @property
    def busy(self) -> bool:
        return self.task is not None

    def run(self, fn, *args, on_result=None, on_error=None, on_partial=None, cancellable=True, **kwargs) -> Task | None:
        if self.task is not None:
            return None
        self.bar.reset()
        self.msg.setText("Procesando…")
        self.cancel_btn.setVisible(cancellable)
        self.cancel_btn.setEnabled(True)
        self.show()

        def done():
            self.task = None
            self.hide()
            self.finished.emit()

        def cancelled():
            from .bus import notify
            notify("warning", "Operación cancelada", "")

        self.task = run_task(fn, *args, on_result=on_result, on_error=on_error or _default_error,
                             on_progress=self._progress, on_partial=on_partial, on_finished=done,
                             on_cancelled=cancelled, **kwargs)
        return self.task

    def _progress(self, pct: int, msg: str):
        self.bar.setValue(pct)
        if msg:
            self.msg.setText(msg)

    def _cancel(self):
        if self.task:
            self.task.cancel()
            self.cancel_btn.setEnabled(False)
            self.msg.setText("Cancelando…")


def _default_error(msg: str):
    from .bus import notify
    notify("error", "Error en la operación", msg)


def confirm(parent, title: str, text: str, details: str = "", danger: bool = False,
            ok_text: str = "Confirmar", require_check: str | None = None) -> bool:
    d = QDialog(parent)
    d.setWindowTitle(title)
    d.setMinimumWidth(460)
    lay = QVBoxLayout(d)
    lay.setContentsMargins(22, 20, 22, 18)
    lay.setSpacing(12)
    head = QHBoxLayout()
    ic = QLabel()
    p = theme.current()
    ic.setPixmap(icons.pixmap("mdi6.alert-circle" if danger else "mdi6.information-outline",
                              p.danger if danger else p.primary, 28))
    head.addWidget(ic, 0, Qt.AlignTop)
    t = QLabel(f"<b style='font-size:15px'>{title}</b><br><span>{text}</span>")
    t.setWordWrap(True)
    head.addWidget(t, 1)
    lay.addLayout(head)
    if details:
        box = QPlainTextEdit(details)
        box.setReadOnly(True)
        box.setMaximumHeight(150)
        box.setObjectName("Mono")
        lay.addWidget(box)
    chk = None
    if require_check:
        chk = QCheckBox(require_check)
        lay.addWidget(chk)
    bb = QDialogButtonBox()
    okb = button(ok_text, "Danger" if danger else "Primary")
    cb = button("Cancelar", "Ghost")
    bb.addButton(cb, QDialogButtonBox.RejectRole)
    bb.addButton(okb, QDialogButtonBox.AcceptRole)
    bb.accepted.connect(d.accept)
    bb.rejected.connect(d.reject)
    if chk:
        okb.setEnabled(False)
        chk.toggled.connect(okb.setEnabled)
    lay.addWidget(bb)
    return d.exec() == QDialog.Accepted


def info_dialog(parent, title: str, text: str, details: str = "", kind: str = "info") -> None:
    d = QDialog(parent)
    d.setWindowTitle(title)
    d.setMinimumWidth(480)
    lay = QVBoxLayout(d)
    lay.setContentsMargins(22, 20, 22, 18)
    b = Banner(text, kind)
    lay.addWidget(QLabel(f"<b style='font-size:15px'>{title}</b>"))
    lay.addWidget(b)
    if details:
        box = QPlainTextEdit(details)
        box.setReadOnly(True)
        box.setObjectName("Mono")
        box.setMinimumHeight(120)
        lay.addWidget(box)
    bb = QDialogButtonBox()
    okb = button("Cerrar", "Primary")
    bb.addButton(okb, QDialogButtonBox.AcceptRole)
    bb.accepted.connect(d.accept)
    lay.addWidget(bb)
    d.exec()


class Sparkline(QWidget):
    def __init__(self, maxlen: int = 60, max_value: float | None = 100.0, color: str | None = None,
                 suffix: str = "%"):
        super().__init__()
        self.values: deque = deque(maxlen=maxlen)
        self.max_value = max_value
        self.color = color
        self.suffix = suffix
        self.setMinimumHeight(70)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def push(self, v: float):
        self.values.append(max(0.0, float(v)))
        self.update()

    def clear(self):
        self.values.clear()
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = theme.current()
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor(pal.border), 1, Qt.DashLine))
        for i in range(1, 4):
            y = h * i / 4
            p.drawLine(0, int(y), w, int(y))
        if len(self.values) < 2:
            p.end()
            return
        mx = self.max_value or max(max(self.values) * 1.15, 1)
        n = self.values.maxlen or len(self.values)
        step = w / max(1, n - 1)
        offset = n - len(self.values)
        path = QPainterPath()
        pts = []
        for i, v in enumerate(self.values):
            x = (offset + i) * step
            y = h - 4 - (min(v, mx) / mx) * (h - 10)
            pts.append((x, y))
        path.moveTo(*pts[0])
        for x, y in pts[1:]:
            path.lineTo(x, y)
        color = QColor(self.color or pal.primary)
        fill = QPainterPath(path)
        fill.lineTo(pts[-1][0], h)
        fill.lineTo(pts[0][0], h)
        fill.closeSubpath()
        fc = QColor(color)
        fc.setAlpha(45)
        p.fillPath(fill, fc)
        p.setPen(QPen(color, 2))
        p.drawPath(path)
        p.setPen(QColor(pal.muted))
        f = p.font()
        f.setPointSize(9)
        p.setFont(f)
        p.drawText(QRectF(0, 0, w - 4, 14), Qt.AlignRight, f"máx {mx:.0f}{self.suffix}")
        p.end()


class Gauge(QWidget):
    """Indicador circular para el speed test."""

    def __init__(self, label: str, unit: str = "Mbps", max_value: float = 100):
        super().__init__()
        self.label = label
        self.unit = unit
        self.max_value = max_value
        self.value: float | None = None
        self.color: str | None = None
        self.setMinimumSize(170, 170)

    def set(self, v: float | None, color: str | None = None):
        self.value = v
        if v is not None and v > self.max_value:
            self.max_value = max(100, ((int(v) // 100) + 1) * 100)
        self.color = color
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = theme.current()
        side = min(self.width(), self.height()) - 16
        r = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        pen = QPen(QColor(pal.surface2), 12, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(r, 225 * 16, -270 * 16)
        if self.value is not None:
            frac = min(1.0, self.value / self.max_value) if self.max_value else 0
            pen.setColor(QColor(self.color or pal.primary))
            p.setPen(pen)
            p.drawArc(r, 225 * 16, int(-270 * 16 * frac))
        f = QFont(self.font())
        # valor (centro)
        p.setPen(QColor(pal.text))
        f.setPixelSize(int(side * 0.22))
        f.setBold(True)
        p.setFont(f)
        txt = "—" if self.value is None else (f"{self.value:.0f}" if self.value >= 100 else f"{self.value:.1f}")
        top = r.top()
        p.drawText(QRectF(r.left(), top + side * 0.30, side, side * 0.26), Qt.AlignCenter, txt)
        # unidad (debajo del valor)
        f.setPixelSize(max(11, int(side * 0.085)))
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(pal.muted))
        p.drawText(QRectF(r.left(), top + side * 0.56, side, side * 0.12), Qt.AlignCenter, self.unit)
        # etiqueta (en la abertura inferior del arco)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(r.left(), top + side * 0.82, side, side * 0.14), Qt.AlignCenter, self.label.upper())
        p.end()


class ResponsiveGrid(QWidget):
    """Reorganiza tarjetas en columnas según el ancho disponible."""

    def __init__(self, min_col_width: int = 260, spacing: int = 14):
        super().__init__()
        self.min_col = min_col_width
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(spacing)
        self.items: list[QWidget] = []
        self._cols = 0

    def add(self, w: QWidget):
        self.items.append(w)
        self._relayout(force=True)

    def _relayout(self, force: bool = False):
        cols = max(1, min(len(self.items) or 1, self.width() // self.min_col if self.width() > 0 else 4))
        if cols == self._cols and not force:
            return
        self._cols = cols
        for w in self.items:
            self.grid.removeWidget(w)
        for i, w in enumerate(self.items):
            self.grid.addWidget(w, i // cols, i % cols)
        for c in range(8):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._relayout()


class KeyValue(QWidget):
    def __init__(self, columns: int = 1):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(7)
        self.columns = columns
        self.labels: dict[str, QLabel] = {}

    def set_items(self, items: dict):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.labels.clear()
        for i, (k, v) in enumerate(items.items()):
            r, c = divmod(i, self.columns)
            kl = QLabel(k)
            kl.setObjectName("Muted")
            vl = QLabel("" if v is None else str(v))
            vl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            vl.setWordWrap(True)
            self.grid.addWidget(kl, r, c * 2)
            self.grid.addWidget(vl, r, c * 2 + 1)
            self.labels[k] = vl
        for c in range(self.columns):
            self.grid.setColumnStretch(c * 2 + 1, 1)


def scroll_page(content: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    content.setObjectName("ScrollContent")
    sa.setWidget(content)
    return sa


class Toast(QFrame):
    COLORS = {"ok": "success", "error": "danger", "warning": "warning", "info": "info"}
    ICONS = {"ok": "mdi6.check-circle", "error": "mdi6.close-circle", "warning": "mdi6.alert-circle",
             "info": "mdi6.information-outline"}

    def __init__(self, parent, kind: str, title: str, message: str, timeout_ms: int = 5000):
        super().__init__(parent)
        self.setObjectName("Toast")
        color = getattr(theme.current(), self.COLORS.get(kind, "info"))
        self.setStyleSheet(f"#Toast {{ border-left: 4px solid {color}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 10, 12)
        lay.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(icons.pixmap(self.ICONS.get(kind, "mdi6.information-outline"), color, 22))
        lay.addWidget(ic, 0, Qt.AlignTop)
        txt = QLabel(f"<b>{title}</b>" + (f"<br><span style='color:{theme.current().muted}'>{message}</span>"
                                          if message else ""))
        txt.setWordWrap(True)
        txt.setFixedWidth(300)
        lay.addWidget(txt, 1)
        close = QPushButton()
        close.setObjectName("Ghost")
        close.setIcon(icons.icon("mdi6.close", theme.current().muted))
        close.setFixedSize(26, 26)
        close.clicked.connect(self.dismiss)
        lay.addWidget(close, 0, Qt.AlignTop)
        self.adjustSize()
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.anim = QPropertyAnimation(self.effect, b"opacity", self)
        self.anim.setDuration(220)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.start()
        QTimer.singleShot(timeout_ms if kind != "error" else timeout_ms * 2, self.dismiss)

    def dismiss(self):
        if not self.isVisible():
            return
        self.anim.stop()
        self.anim.setStartValue(self.effect.opacity())
        self.anim.setEndValue(0.0)
        self.anim.finished.connect(self._gone)
        self.anim.start()

    def _gone(self):
        self.hide()
        self.deleteLater()
        if self.parent() and hasattr(self.parent(), "relayout_toasts"):
            QTimer.singleShot(0, self.parent().relayout_toasts)


class UpdateDialog(QDialog):
    """Descarga e instala una actualización con progreso animado y opción de cancelar."""

    def __init__(self, parent, info):
        super().__init__(parent)
        self.info = info
        self.ok = False
        self.setWindowTitle("Actualizando eREDES")
        self.setMinimumWidth(460)
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(14)
        from app.core.paths import version_label
        lay.addWidget(QLabel(f"<b style='font-size:18px'>Actualizando a {version_label(info.latest)}</b>"))
        self.panel = TaskPanel()
        lay.addWidget(self.panel)
        self.panel.finished.connect(self._finished)
        from app.core import updater

        def work(ctx):
            z = updater.download(info, ctx.progress, lambda: ctx.cancelled)
            ctx.progress(100, "Instalando…")
            updater.install(z)
            return True

        def done(_r):
            self.ok = True

        def err(m):
            from .bus import notify
            notify("error", "No se pudo actualizar", m)
        self.panel.run(work, on_result=done, on_error=err)

    def _finished(self):
        if self.ok:
            self.accept()
        else:
            self.reject()


class CountdownDialog(QDialog):
    """Diálogo que se acepta solo al terminar la cuenta atrás (o con el botón principal)."""

    def __init__(self, parent, title: str, text: str, seconds: int = 10, ok_text: str = "Aceptar",
                 cancel_text: str = "Cancelar"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        self.left = seconds
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(14)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icons.pixmap("mdi6.download", theme.current().primary, 30))
        head.addWidget(ic)
        head.addWidget(QLabel(f"<b style='font-size:18px'>{title}</b>"), 1)
        lay.addLayout(head)
        self.msg = QLabel(text)
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        self.count = QLabel("")
        self.count.setObjectName("Muted")
        lay.addWidget(self.count)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button(cancel_text, "Ghost", None, self.reject))
        self.ok_btn = button(ok_text, "Primary", "mdi6.download", self.accept)
        row.addWidget(self.ok_btn)
        lay.addLayout(row)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self._tick(first=True)

    def _tick(self, first: bool = False):
        if not first:
            self.left -= 1
        if self.left <= 0:
            self.timer.stop()
            self.accept()
            return
        self.count.setText(f"Empieza en {self.left} s…")
