"""Fábrica de adaptadores según el sistema operativo."""
from __future__ import annotations

from app.core.platform_info import IS_MACOS, IS_WINDOWS

from .base import NetworkConfigBackend, PrinterBackend

_printer: PrinterBackend | None = None
_net: NetworkConfigBackend | None = None


def printer_backend() -> PrinterBackend:
    global _printer
    if _printer is None:
        if IS_WINDOWS:
            from .windows.printers import WindowsPrinterBackend
            _printer = WindowsPrinterBackend()
        elif IS_MACOS:
            from .macos.printers import MacPrinterBackend
            _printer = MacPrinterBackend()
        else:  # Linux usa CUPS igual que macOS
            from .macos.printers import MacPrinterBackend
            _printer = MacPrinterBackend()
    return _printer


def network_config_backend() -> NetworkConfigBackend:
    global _net
    if _net is None:
        if IS_WINDOWS:
            from .windows.network import WindowsNetworkConfig
            _net = WindowsNetworkConfig()
        else:
            from .macos.network import MacNetworkConfig
            _net = MacNetworkConfig()
    return _net
