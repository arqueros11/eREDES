"""Speed Test real (Cloudflare)."""
from __future__ import annotations


from PySide6.QtWidgets import QHBoxLayout, QLabel

from app.core import config
from app.database.db import get_db

from .. import theme
from ..bus import notify
from ..widgets import Card, Gauge, Sparkline, TaskPanel, button, fill_table, make_table
from .base import BasePage


class SpeedTestPage(BasePage):
    key = "speedtest"
    title = "Speed Test"

    def __init__(self):
        super().__init__()
        pal = theme.current()
        top = QHBoxLayout()
        self.start = button("Iniciar test", "Primary", "mdi6.play", self.run)
        self.start.setMinimumHeight(50)
        self.start.setMinimumWidth(200)
        top.addWidget(self.start)
        self.server = QLabel("")
        self.server.setObjectName("Muted")
        top.addWidget(self.server, 1)
        self.root.addLayout(top)

        gauges = QHBoxLayout()
        gauges.setSpacing(14)
        self.g_down = Gauge("Descarga")
        self.g_up = Gauge("Subida")
        self.g_ping = Gauge("Ping", "ms", 200)
        self.g_jit = Gauge("Jitter", "ms", 100)
        self.g_loss = Gauge("Pérdida", "%", 100)
        for g in (self.g_down, self.g_up, self.g_ping, self.g_jit, self.g_loss):
            c = Card()
            c.add(g)
            gauges.addWidget(c)
        self.root.addLayout(gauges)
        self.task = TaskPanel()
        self.task.finished.connect(lambda: self.start.setEnabled(True))
        self.root.addWidget(self.task)

        live = Card("En vivo", icon_name="mdi6.chart-areaspline")
        self.spark = Sparkline(maxlen=80, max_value=None, color=pal.primary, suffix=" Mbps")
        self.spark.setMinimumHeight(120)
        live.add(self.spark)
        self.root.addWidget(live)

        hist = Card("Historial", icon_name="mdi6.history")
        self.hist = make_table(["Fecha", "Descarga", "Subida", "Ping", "Jitter", "Pérdida", "Servidor"], stretch_col=6)
        hist.add(self.hist, 1)
        self.root.addWidget(hist, 1)

    def first_show(self):
        self._history()

    def _history(self):
        rows = get_db().query("SELECT * FROM speed_tests ORDER BY id DESC LIMIT 20")

        def f(v, unit=""):
            return "—" if v is None else f"{v:.1f}{unit}"
        fill_table(self.hist, [[r["ts"].replace("T", " ")[:16], f(r["download_mbps"], " Mbps"), f(r["upload_mbps"], " Mbps"),
                                f(r["ping_ms"], " ms"), f(r["jitter_ms"], " ms"), f(r["loss_pct"], " %"), r["server"]]
                               for r in rows])

    def run(self):
        from app.speed_test.engine import run_speedtest
        for g in (self.g_down, self.g_up, self.g_ping, self.g_jit, self.g_loss):
            g.set(None)
        self.spark.clear()
        self.start.setEnabled(False)
        self.server.setText("")
        self.task.run(run_speedtest, float(config.get("speedtest_duration_s") or 8), on_result=self._done,
                      on_partial=self._partial)

    def _partial(self, d):
        pal = theme.current()
        if "mbps" in d:
            self.spark.color = pal.primary if d["phase"] == "Descarga" else pal.accent
            self.spark.push(d["mbps"])
            (self.g_down if d["phase"] == "Descarga" else self.g_up).set(d["mbps"], self.spark.color)
        if "ping" in d:
            self.g_ping.set(d["ping"])
            self.g_jit.set(d["jitter"])

    def _done(self, r):
        pal = theme.current()
        self.g_down.set(r.download_mbps, pal.primary)
        self.g_up.set(r.upload_mbps, pal.accent)
        self.g_ping.set(r.ping_ms, pal.success if (r.ping_ms or 999) < 60 else pal.warning)
        self.g_jit.set(r.jitter_ms, pal.success if (r.jitter_ms or 999) < 20 else pal.warning)
        self.g_loss.set(r.loss_pct, pal.success if (r.loss_pct or 0) < 1 else pal.danger)
        self.server.setText(f"{r.server}  ·  {r.duration_s:.0f} s" + (f"  ·  {r.isp}" if r.isp else ""))
        if r.errors:
            notify("warning", "Test con avisos", "\n".join(r.errors))
        else:
            notify("ok", "Test completado", f"↓ {r.download_mbps:.1f}  ↑ {r.upload_mbps:.1f} Mbps")
        self._history()


