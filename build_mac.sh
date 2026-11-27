#!/bin/bash
# Construye eREDES.app para macOS (ejecutar en un Mac).
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python packaging/make_icons.py
.venv/bin/pyinstaller --noconfirm --clean --distpath dist --workpath build packaging/eREDES.spec
# Firma ad-hoc (permite abrir la app en este y otros Mac con clic derecho > Abrir)
codesign --force --deep --sign - dist/eREDES.app
ditto -c -k --keepParent dist/eREDES.app dist/eREDES-macOS.zip
echo "Listo: dist/eREDES.app  y  dist/eREDES-macOS.zip"
