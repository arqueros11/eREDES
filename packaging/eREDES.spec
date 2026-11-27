# -*- mode: python ; coding: utf-8 -*-
# Especificación PyInstaller para macOS (.app) y Windows (carpeta con eREDES.exe).
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

sys.path.insert(0, str(ROOT))  # para que collect_submodules encuentre el paquete app
hidden = collect_submodules("app")
assert any(m.startswith("app.ui.pages.") for m in hidden), "No se encontraron las páginas de la UI" + ["qtawesome", "reportlab.graphics.barcode.common"]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "app" / "resources"), "app/resources")],
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "requests", "charset_normalizer", "numpy",
              # módulos de Qt que eREDES no usa (reduce ~30 MB)
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick",
              "PySide6.QtQuickWidgets", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtDataVisualization",
              "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtVirtualKeyboard", "PySide6.QtDBus",
              "PySide6.QtNetwork", "PySide6.QtCharts", "PySide6.QtGraphs", "PySide6.QtSql", "PySide6.QtTest",
              "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtXml", "PySide6.QtBluetooth",
              "PySide6.QtPositioning", "PySide6.QtSerialPort", "PySide6.QtWebSockets"],
    noarchive=False,
)
# Quitar frameworks y plugins de Qt que entran por dependencias de plugins y eREDES no usa
_DROP = ("QtQml", "QtQuick", "QtVirtualKeyboard", "QtPdf", "QtOpenGL", "QtNetwork",  # QtDBus: lo necesita QtGui
         "platforminputcontexts", "networkinformation", "/tls/", "\\tls\\", "qpdf", "qtvirtualkeyboard",
         "QtDataVisualization", "opengl32sw")


def _keep(entry):
    name = (entry[0] + " " + str(entry[1])).replace("\\", "/")
    return not any(d.replace("\\", "/") in name for d in _DROP)


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="eREDES",
    console=False,
    icon=str(ROOT / "packaging" / ("icon.ico" if IS_WIN else "icon.icns")),
    uac_admin=IS_WIN,          # Windows: pide permisos de administrador al abrir (necesario para impresoras/drivers)
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="eREDES")
if IS_MAC:
    app = BUNDLE(
        coll,
        name="eREDES.app",
        icon=str(ROOT / "packaging" / "icon.icns"),
        bundle_identifier="com.eredes.manager",
        info_plist={
            "CFBundleName": "eREDES",
            "CFBundleDisplayName": "eREDES",
            "CFBundleShortVersionString": "1.0.3",
            "CFBundleVersion": "1.0.3",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSLocalNetworkUsageDescription": "eREDES escanea la red local para encontrar y configurar impresoras.",
            "NSBonjourServices": ["_pdl-datastream._tcp", "_printer._tcp", "_ipp._tcp", "_ipps._tcp", "_http._tcp"],
            "NSAppleEventsUsageDescription": "eREDES solicita permisos de administrador para instalar drivers y "
                                             "configurar la red.",
        },
    )
