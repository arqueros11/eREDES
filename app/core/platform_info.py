"""Detección del sistema operativo y arquitectura."""
from __future__ import annotations

import ctypes
import os
import platform
import sys

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def os_key() -> str:
    """Clave normalizada usada en la biblioteca de drivers: windows | macos | linux."""
    if IS_WINDOWS:
        return "windows"
    if IS_MACOS:
        return "macos"
    return "linux"


def os_label() -> str:
    if IS_WINDOWS:
        rel = platform.release()
        try:
            build = int(platform.version().split(".")[-1])
            if build >= 22000:
                rel = "11"
        except ValueError:
            pass
        return f"Windows {rel} ({platform.version()})"
    if IS_MACOS:
        return f"macOS {platform.mac_ver()[0]}"
    return f"{platform.system()} {platform.release()}"


def arch_key() -> str:
    """x64 | x86 | arm64"""
    m = platform.machine().lower()
    if m in ("amd64", "x86_64"):
        return "x64"
    if m in ("arm64", "aarch64"):
        return "arm64"
    if m in ("x86", "i386", "i686"):
        return "x86"
    return m or "desconocida"


def is_admin() -> bool:
    if IS_WINDOWS:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def relaunch_as_admin_windows() -> bool:
    """Reinicia la app con elevación UAC (solo Windows). Devuelve True si se lanzó."""
    if not IS_WINDOWS:
        return False
    try:
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
        exe = sys.executable
        if not getattr(sys, "frozen", False):
            params = f'"{os.path.abspath(sys.argv[0])}" {params}'
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)  # type: ignore[attr-defined]
        return rc > 32
    except Exception:
        return False
