"""Monitor del sistema en tiempo real (sin bloquear la interfaz)."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QTabWidget, QVBoxLayout, QWidget

from app.core.tasks import run_task

from .. import theme
from ..widgets import sort_table, Card, KeyValue, Sparkline, StatCard, fill_table, make_table
from .base import BasePage


def _snap(ctx):
    from app.system_monitor import monitor
    return monitor.snapshot()


def _static(ctx):
    from app.system_monitor import monitor
    return {"info": monitor.system_info(), "smart": monitor.smart_status(), "services": monitor.services_summary()}


class MonitorPage(BasePage):
    key = "monitor"
    title = "Monitor"

    def __init__(self):
        super().__init__()
        pal = theme.current()
        top = QHBoxLayout()
        top.setSpacing(14)
        self.c_cpu = StatCard("mdi6.cpu-64-bit", "CPU", color=pal.primary)
        self.c_ram = StatCard("mdi6.memory", "RAM", color=pal.accent)
        self.c_disk = StatCard("mdi6.harddisk", "Disco", color=pal.warning)
        self.c_tmp = StatCard("mdi6.chip", "Temperatura", color=pal.danger)
        for c in (self.c_cpu, self.c_ram, self.c_disk, self.c_tmp):
            top.addWidget(c)
        self.root.addLayout(top)

        charts = QHBoxLayout()
        charts.setSpacing(14)
        cc = Card("CPU", icon_name="mdi6.chart-areaspline")
        self.sp_cpu = Sparkline(90, 100, pal.primary)
        cc.add(self.sp_cpu)
        rc = Card("RAM", icon_name="mdi6.chart-areaspline")
        self.sp_ram = Sparkline(90, 100, pal.accent)
        rc.add(self.sp_ram)
        charts.addWidget(cc)
        charts.addWidget(rc)
        self.root.addLayout(charts)

        tabs = QTabWidget()
        self.tabs = tabs
        self.t_cpu = make_table(["Proceso", "PID", "CPU %", "RAM"], stretch_col=0)
        self.t_mem = make_table(["Proceso", "PID", "RAM", "RAM %"], stretch_col=0)
        self.t_disk = make_table(["Unidad", "Montaje", "Sistema", "Capacidad", "Usado", "Libre", "Uso %"], stretch_col=1)
        sysw = QWidget()
        sv = QVBoxLayout(sysw)
        self.kv = KeyValue(2)
        sv.addWidget(self.kv)
        self.t_smart = make_table(["Disco", "Modelo", "Estado SMART"], stretch_col=1)
        self.t_smart.setMaximumHeight(140)
        sv.addWidget(self.t_smart)
        sv.addStretch(1)
        tabs.addTab(self.t_cpu, "Procesos · CPU")
        tabs.addTab(self.t_mem, "Procesos · RAM")
        tabs.addTab(self.t_disk, "Discos")
        tabs.addTab(sysw, "Sistema")
        card = Card()
        card.add(tabs, 1)
        self.root.addWidget(card, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self._busy = False

    def first_show(self):
        run_task(_static, on_result=self._static)

    def on_show(self):
        super().on_show()
        self._tick()
        self.timer.start(2000)

    def on_hide(self):
        self.timer.stop()

    def _tick(self):
        if self._busy:
            return
        self._busy = True
        run_task(_snap, on_result=self._apply, on_finished=lambda: setattr(self, "_busy", False))

    def _apply(self, s):
        from app.system_monitor.monitor import fmt_bytes
        pal = theme.current()
        self.c_cpu.set(f"{s.cpu_percent:.0f}%", f"{s.cores_logical} núcleos", pal.danger if s.cpu_percent > 85 else pal.primary)
        self.c_ram.set(f"{s.mem_percent:.0f}%", f"{fmt_bytes(s.mem_used)} / {fmt_bytes(s.mem_total)}",
                       pal.danger if s.mem_percent > 90 else pal.accent)
        d = s.disks[0] if s.disks else None
        if d:
            self.c_disk.set(f"{d['percent']:.0f}%", f"{fmt_bytes(d['free'])} libres")
        self.c_tmp.set(s.temperature if "°" in s.temperature else "—", "" if "°" in s.temperature else "No disponible")
        self.sp_cpu.push(s.cpu_percent)
        self.sp_ram.push(s.mem_percent)
        tab = self.tabs.currentIndex()  # solo se rellena la tabla visible (menos trabajo en la interfaz)
        if tab == 0:
            fill_table(self.t_cpu, [[p["name"], p["pid"], round(p["cpu"], 1), fmt_bytes(p["mem"])] for p in s.top_cpu])
            sort_table(self.t_cpu, 2, True)
        elif tab == 1:
            fill_table(self.t_mem, [[p["name"], p["pid"], fmt_bytes(p["mem"]), round(p["mem_pct"], 1)]
                                    for p in s.top_mem])
            sort_table(self.t_mem, 3, True)
        elif tab == 2:
            fill_table(self.t_disk, [[x["device"], x["mount"], x["fs"], fmt_bytes(x["total"]), fmt_bytes(x["used"]),
                                  fmt_bytes(x["free"]), x["percent"]] for x in s.disks])

    def _static(self, d):
        pal = theme.current()
        self.kv.set_items({**d["info"], **d["services"]})
        fill_table(self.t_smart, [[x["disk"], x["name"], x["status"]] for x in d["smart"]] or
                   [["—", "—", "SMART no disponible"]],
                   colors={2: lambda st: pal.success if st.lower().startswith(("verified", "healthy")) else pal.warning})
