"""Ejecución de tareas en segundo plano para no congelar la interfaz.

Uso:
    task = run_task(funcion, arg1, on_result=..., on_error=..., on_progress=...)
    task.cancel()

`funcion` recibe como primer argumento un TaskContext con:
    ctx.progress(porcentaje:int, mensaje:str)
    ctx.cancelled -> bool
    ctx.check()  -> lanza TaskCancelled si se canceló
"""
from __future__ import annotations

import logging
import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger("eredes.tasks")


class TaskCancelled(Exception):
    pass


class TaskContext:
    def __init__(self, signals: "TaskSignals"):
        self._signals = signals
        self._cancel = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()

    def check(self) -> None:
        if self._cancel.is_set():
            raise TaskCancelled()

    def progress(self, pct: int, msg: str = "") -> None:
        # limitar a ~20 actualizaciones/s para no saturar la interfaz
        import time
        now = time.monotonic()
        if pct < 100 and now - getattr(self, "_last_p", 0) < 0.05:
            return
        self._last_p = now
        try:
            self._signals.progress.emit(int(max(0, min(100, pct))), msg)
        except RuntimeError:
            pass

    def emit(self, payload: Any) -> None:
        """Envía un resultado parcial (por ejemplo, un host encontrado)."""
        try:
            self._signals.partial.emit(payload)
        except RuntimeError:
            pass


class TaskSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(int, str)
    partial = Signal(object)
    cancelled = Signal()
    finished = Signal()


class Task(QRunnable):
    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.setAutoDelete(False)
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = TaskSignals()
        self.ctx = TaskContext(self.signals)

    def cancel(self) -> None:
        self.ctx.cancel()

    def _emit(self, sig_name: str, *args) -> None:
        """Emite sin fallar si la interfaz ya se destruyó (cierre de la app)."""
        try:
            getattr(self.signals, sig_name).emit(*args)
        except RuntimeError:
            pass

    def run(self) -> None:  # noqa: D401
        try:
            res = self.fn(self.ctx, *self.args, **self.kwargs)
            self._emit("cancelled" if self.ctx.cancelled else "result", *(() if self.ctx.cancelled else (res,)))
        except TaskCancelled:
            self._emit("cancelled")
        except Exception as e:  # noqa: BLE001
            log.error("Error en tarea %s: %s\n%s", getattr(self.fn, "__name__", self.fn), e,
                      traceback.format_exc())
            self._emit("error", str(e) or e.__class__.__name__)
        finally:
            self._emit("finished")


_active: set["Task"] = set()


def run_task(fn: Callable, *args, on_result: Callable | None = None, on_error: Callable | None = None,
             on_progress: Callable | None = None, on_partial: Callable | None = None,
             on_finished: Callable | None = None, on_cancelled: Callable | None = None,
             **kwargs) -> Task:
    task = Task(fn, *args, **kwargs)
    if on_result:
        task.signals.result.connect(on_result)
    if on_error:
        task.signals.error.connect(on_error)
    if on_progress:
        task.signals.progress.connect(on_progress)
    if on_partial:
        task.signals.partial.connect(on_partial)
    if on_cancelled:
        task.signals.cancelled.connect(on_cancelled)
    if on_finished:
        task.signals.finished.connect(on_finished)
    _active.add(task)
    task.signals.finished.connect(lambda: _active.discard(task))
    QThreadPool.globalInstance().start(task)
    return task


def cancel_all() -> None:
    for t in list(_active):
        t.cancel()
