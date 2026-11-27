"""Ejecución segura de comandos del sistema (sin shell=True).

* run():            ejecuta un comando con timeout y captura salida.
* run_powershell(): ejecuta un script PowerShell codificado (-EncodedCommand),
                    evitando problemas de comillas e inyección.
* run_admin_macos(): ejecuta un comando como administrador usando el diálogo
                    nativo de macOS (osascript ... with administrator privileges).
"""
from __future__ import annotations

import base64
import logging
import shlex
import subprocess
from dataclasses import dataclass

from .platform_info import IS_MACOS, IS_WINDOWS

log = logging.getLogger("eredes.shell")

CREATE_NO_WINDOW = 0x08000000


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def output(self) -> str:
        return (self.stdout or "") + (("\n" + self.stderr) if self.stderr else "")

    def short_error(self) -> str:
        if self.timed_out:
            return "Tiempo de espera agotado."
        txt = (self.stderr or self.stdout or "").strip()
        return txt[-800:] if txt else f"Código de salida {self.returncode}"


def run(args: list[str], timeout: float = 30, input_text: str | None = None,
        env: dict | None = None) -> CommandResult:
    kwargs: dict = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW
    log.debug("RUN %s", args if not args or args[0] != "osascript" else ["osascript", "..."])
    try:
        p = subprocess.run(
            args, capture_output=True, timeout=timeout, input=input_text.encode() if input_text else None,
            env=env, **kwargs,
        )
        out = _decode(p.stdout)
        err = _decode(p.stderr)
        res = CommandResult(list(args), p.returncode, out, err)
    except subprocess.TimeoutExpired as e:
        res = CommandResult(list(args), -1, _decode(e.stdout or b""), _decode(e.stderr or b""), timed_out=True)
    except FileNotFoundError:
        res = CommandResult(list(args), 127, "", f"Comando no encontrado: {args[0]}")
    except OSError as e:
        res = CommandResult(list(args), 126, "", f"Error ejecutando {args[0]}: {e}")
    if not res.ok:
        log.debug("RC=%s %s", res.returncode, res.short_error()[:300])
    return res


def _decode(b: bytes) -> str:
    if not b:
        return ""
    for enc in ("utf-8", "cp850", "cp1252", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def ps_quote(value) -> str:
    """Cita un valor como literal de PowerShell entre comillas simples."""
    return "'" + str(value).replace("'", "''") + "'"


def run_powershell(script: str, timeout: float = 60) -> CommandResult:
    prelude = (
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$ProgressPreference='SilentlyContinue';"
    )
    full = prelude + script
    encoded = base64.b64encode(full.encode("utf-16-le")).decode("ascii")
    return run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", encoded],
        timeout=timeout,
    )


def _applescript_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def run_admin_macos(args: list[str], timeout: float = 300, prompt: str | None = None) -> CommandResult:
    """Ejecuta `args` como root mostrando el diálogo de autenticación de macOS."""
    if not IS_MACOS:
        return CommandResult(args, 1, "", "run_admin_macos solo está disponible en macOS")
    cmd = " ".join(shlex.quote(a) for a in args)
    script = f"do shell script {_applescript_string(cmd)} with administrator privileges"
    if prompt:
        script += f" with prompt {_applescript_string(prompt)}"
    res = run(["osascript", "-e", script], timeout=timeout)
    res.args = list(args)
    if res.returncode != 0 and "-128" in res.stderr:
        res.stderr = "Autenticación cancelada por el usuario."
    return res
