"""Bus de eventos de la interfaz (comunicación entre páginas)."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class AppBus(QObject):
    theme_changed = Signal(str)
    notify = Signal(str, str, str)            # tipo (ok|error|warning|info), título, mensaje
    navigate = Signal(str)                    # clave de página
    new_printer = Signal(str, int)            # IP, puerto -> abre el formulario de alta
    diagnose_printer = Signal(str, int, str)  # IP, puerto, cola
    recovery_target = Signal(str)             # IP encontrada en otra subred
    assign_free_ip = Signal(str)              # IP libre elegida en el escáner -> nueva IP de una impresora
    data_changed = Signal(str)                # área: printers | drivers | scan | history


bus = AppBus()


def notify(kind: str, title: str, message: str = "") -> None:
    bus.notify.emit(kind, title, message)
