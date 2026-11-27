"""Ping continuo a una o varias IPs (como `ping -t`, pero de muchas a la vez)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import probes

MAX_IPS = 256


@dataclass
class PingState:
    ip: str
    name: str = ""
    sent: int = 0
    received: int = 0
    last_rtt: float | None = None
    up: bool | None = None          # None = aún sin datos
    since: float = field(default_factory=time.time)   # momento del último cambio de estado
    rtts: list = field(default_factory=list)

    @property
    def lost(self) -> int:
        return self.sent - self.received

    @property
    def loss_pct(self) -> float:
        return 100.0 * self.lost / self.sent if self.sent else 0.0

    @property
    def avg_rtt(self) -> float | None:
        return sum(self.rtts) / len(self.rtts) if self.rtts else None

    def update(self, rtt: float | None, now: float | None = None) -> str | None:
        """Registra una ronda. Devuelve 'up' / 'down' si cambió el estado (no en la primera ronda)."""
        now = now or time.time()
        self.sent += 1
        alive = rtt is not None
        if alive:
            self.received += 1
            self.last_rtt = rtt
            self.rtts = (self.rtts + [rtt])[-60:]
        prev = self.up
        self.up = alive
        if prev is None:
            self.since = now
            return None
        if prev != alive:
            self.since = now
            return "up" if alive else "down"
        return None


def ping_loop(ctx, ips: list[str], interval: float = 1.0, timeout_ms: int = 900) -> int:
    """Hace ping a todas las IPs cada `interval` segundos hasta que se cancele.

    Emite ctx.emit({"round": n, "replies": {ip: rtt}}) en cada ronda. Devuelve el número de rondas.
    """
    ips = ips[:MAX_IPS]
    rnd = 0
    while not ctx.cancelled:
        t0 = time.monotonic()
        replies = probes.ping_sweep(ips, timeout_ms, retries=0, workers=min(64, max(1, len(ips))))
        rnd += 1
        ctx.emit({"round": rnd, "replies": replies})
        while not ctx.cancelled and time.monotonic() - t0 < interval:
            time.sleep(0.05)
    return rnd
