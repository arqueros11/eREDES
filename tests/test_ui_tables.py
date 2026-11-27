"""Regresión: ordenar tablas con varias filas no debe cerrar la app (fallo de ERMA.V1.0.1)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_sort_many_rows(qapp):
    from app.ui.widgets import fill_table, make_table, selected_data, sort_table
    t = make_table(["IP", "Nombre", "Valor"])
    rows = [[f"192.168.1.{i}", f"Impresora {i}", float(i % 7)] for i in (10, 2, 254, 1, 33, 100, 9)]
    fill_table(t, rows, data=[r[0] for r in rows])
    sort_table(t, 0)
    assert [t.item(r, 0).text() for r in range(t.rowCount())] == \
        ["192.168.1.1", "192.168.1.2", "192.168.1.9", "192.168.1.10", "192.168.1.33", "192.168.1.100", "192.168.1.254"]
    sort_table(t, 2, True)
    vals = [float(t.item(r, 2).text()) for r in range(t.rowCount())]
    assert vals == sorted(vals, reverse=True)
    # la fila seleccionada devuelve su dato correcto tras ordenar
    t.selectRow(0)
    assert selected_data(t) == t.item(0, 0).text()
    # rellenar de nuevo mantiene el orden elegido (como en los refrescos del Monitor)
    for _ in range(30):
        fill_table(t, rows, data=[r[0] for r in rows])
    t.horizontalHeader().sectionClicked.emit(1)
    assert t.rowCount() == len(rows)
