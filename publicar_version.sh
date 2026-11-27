#!/bin/bash
# Publica una nueva versión de eREDES:  ./publicar_version.sh 1.0.1
# 1) cambia APP_VERSION, 2) prueba, 3) hace commit + etiqueta ERMA.V1.0.1, 4) sube a GitHub.
# GitHub compila Windows y Mac y crea la Release; las apps instaladas la verán en "Actualizar".
set -e
cd "$(dirname "$0")"
V="$1"
if [[ ! "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Uso: ./publicar_version.sh 1.0.1"; exit 1
fi
ACTUAL=$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' app/core/paths.py)
echo "Versión actual: ERMA.V$ACTUAL  ->  nueva: ERMA.V$V"
sed -i '' "s/^APP_VERSION = \".*\"/APP_VERSION = \"$V\"/" app/core/paths.py
sed -i '' "s/\"CFBundleShortVersionString\": \".*\"/\"CFBundleShortVersionString\": \"$V\"/; s/\"CFBundleVersion\": \".*\"/\"CFBundleVersion\": \"$V\"/" packaging/eREDES.spec
.venv/bin/python -m pytest -q tests
git add -A
git commit -m "ERMA.V$V"
git tag "ERMA.V$V"
git push origin HEAD
git push origin "ERMA.V$V"
echo "Listo. GitHub está compilando ERMA.V$V (pestaña Actions). En unos 10 minutos aparecerá en Releases."
