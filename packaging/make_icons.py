"""Genera los iconos de la app (PNG, ICNS para macOS, ICO para Windows) a partir del logotipo vectorial."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QGuiApplication  # noqa: E402

app = QGuiApplication(sys.argv)
from app.ui.icons import logo_pixmap  # noqa: E402

out = Path(__file__).resolve().parent
logo_pixmap(1024).save(str(out / "icon.png"))
logo_pixmap(256).save(str(out / "icon.ico"))
if sys.platform == "darwin":
    iconset = out / "icon.iconset"
    iconset.mkdir(exist_ok=True)
    for s in (16, 32, 128, 256, 512):
        logo_pixmap(s).save(str(iconset / f"icon_{s}x{s}.png"))
        logo_pixmap(s * 2).save(str(iconset / f"icon_{s}x{s}@2x.png"))
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out / "icon.icns")], check=True)
    shutil.rmtree(iconset)
print("Iconos generados en", out)
