"""Interfaces comunes de los adaptadores de sistema operativo."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class PlatformError(RuntimeError):
    """Error devuelto por el sistema operativo al ejecutar una operación."""


@dataclass
class SystemPrinter:
    name: str
    driver: str = ""
    port_name: str = ""
    uri: str = ""
    ip: str = ""
    port: int | None = None
    status: str = ""
    enabled: bool = True
    jobs: int = 0
    shared: bool = False
    os: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class SystemDriver:
    name: str
    ref: str               # identificador para el SO (nombre en Windows, ppd/modelo CUPS en macOS)
    manufacturer: str = ""
    version: str = ""
    kind: str = ""         # installed | ppd_file | model | raw


@dataclass
class PrintJob:
    job_id: str
    printer: str
    status: str = ""
    size: str = ""
    submitted: str = ""


@dataclass
class OpResult:
    ok: bool
    message: str
    detail: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class DriverRef:
    """Controlador elegido para crear una impresora."""
    kind: str        # installed (Windows) | ppd_file | model | raw
    ref: str         # nombre del driver / ruta del PPD / id lpinfo
    label: str = ""
    package_id: int | None = None


class PrinterBackend(ABC):
    os_name: str = ""

    @abstractmethod
    def list_printers(self) -> list[SystemPrinter]: ...

    @abstractmethod
    def get_printer(self, name: str) -> SystemPrinter | None: ...

    @abstractmethod
    def list_drivers(self) -> list[SystemDriver]: ...

    @abstractmethod
    def create_printer(self, name: str, driver: DriverRef, ip: str, port: int) -> OpResult: ...

    @abstractmethod
    def remove_printer(self, name: str) -> OpResult: ...

    @abstractmethod
    def set_address(self, name: str, ip: str, port: int) -> OpResult: ...

    @abstractmethod
    def set_driver(self, name: str, driver: DriverRef) -> OpResult: ...

    @abstractmethod
    def rename_printer(self, old: str, new: str) -> OpResult: ...

    @abstractmethod
    def list_jobs(self, name: str | None = None) -> list[PrintJob]: ...

    @abstractmethod
    def clear_queue(self, name: str) -> OpResult: ...

    @abstractmethod
    def restart_queue(self, name: str) -> OpResult: ...

    @abstractmethod
    def print_test_page(self, name: str) -> OpResult:
        """Página de prueba a través del controlador del sistema."""

    @abstractmethod
    def print_raw(self, name: str, data: bytes, title: str = "eREDES") -> OpResult:
        """Envía bytes sin procesar (ESC/POS) a la cola."""

    @abstractmethod
    def wait_job(self, name: str, job_id: str, timeout: float = 30) -> OpResult:
        """Espera a que el SO confirme el trabajo como completado o con error."""

    @abstractmethod
    def spooler_status(self) -> OpResult: ...

    @abstractmethod
    def restart_spooler(self) -> OpResult: ...

    @abstractmethod
    def install_driver_package(self, path: str, pkg_type: str, driver_names: list[str]) -> OpResult: ...


class NetworkConfigBackend(ABC):
    @abstractmethod
    def snapshot(self, interface: str) -> dict: ...

    @abstractmethod
    def add_secondary_ip(self, interface: str, ip: str, mask: str) -> OpResult: ...

    @abstractmethod
    def remove_secondary_ip(self, interface: str, ip: str, snapshot: dict) -> OpResult: ...

    @abstractmethod
    def verify_restored(self, interface: str, snapshot: dict) -> OpResult: ...
