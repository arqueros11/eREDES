# eREDES — Printer & Network Manager

Aplicación **de escritorio nativa** (no web) para técnicos de redes e impresoras POS.
Funciona **sin internet** (todo es local); solo el Speed Test necesita conexión.

| Sistema | Paquete | Cómo se abre |
|---|---|---|
| macOS 11+ (Apple Silicon / Intel) | `eREDES.app` | Doble clic (la 1.ª vez: clic derecho → Abrir) |
| Windows 10 / 11 | carpeta `eREDES\` con `eREDES.exe` | Doble clic (pide administrador) |

## Herramientas

| Herramienta | Qué hace |
|---|---|
| **Impresoras** | Lista las impresoras reales del sistema. Alta con 4 campos: Nombre · Controlador · IP · Puerto. Editar, cambiar IP/puerto/driver, eliminar, prueba de impresión (ticket ESC/POS, página del sistema o directa TCP), vaciar/reiniciar cola, reiniciar servicio de impresión. |
| **Escáner** | Interfaces (IP, máscara, gateway, DNS, MAC, Wi-Fi/Ethernet). Escaneo por red, rango o IP: ICMP + TCP + ARP + DNS inverso + web + SNMP. Tabla IP · MAC · Hostname · Fabricante · Puertos · Estado · Tipo. "Usar en nueva impresora". Comprobación de disponibilidad de IP (nunca afirma que esté libre si no responde). |
| **Drivers** | Biblioteca local en `DRIVERS/` (Fabricante → Modelo → Paquetes). Importa archivos, ZIP y carpetas; INF, EXE, MSI, PKG, PPD, DMG. SHA-256, duplicados, firmas digitales, compatibilidad SO/arquitectura, instalación con validaciones. |
| **Recuperar IP** | Encuentra impresoras configuradas en otra subred (Bonjour), añade una IP temporal secundaria (sin tocar la IP principal), escanea la subred de fábrica, cambia la IP con el perfil del fabricante, verifica y **restaura la red** (también al cerrar la app). |
| **Diagnóstico** | Ping, puerto TCP, cola, servicio, controlador, estado ESC/POS real (papel, tapa, cortador, errores) y SNMP (Printer-MIB). Estados: Correcto · Error · Desconectado · No disponible · No compatible · No se pudo determinar. Prueba de conectividad a internet. |
| **Speed Test** | Descarga, subida, ping, jitter y pérdida reales contra Cloudflare. |
| **Monitor** | CPU, RAM, disco, procesos, temperatura (si el SO la expone), SMART, sistema y servicio de impresión. |
| **Informes** | PDF de la intervención (equipo, SO, red, dispositivos, impresoras, drivers, diagnósticos, pruebas, errores) + historial y logs. |
| **Ajustes** | Tema claro/oscuro, puertos, tiempos, comunidad SNMP, subredes de fábrica, copia de seguridad/restauración. |

## Biblioteca de drivers

```
DRIVERS/
├── EPSON/   TM-T20/  TM-T88/  OTROS/
├── BIENEX/  (marca propia — no es BIXOLON)
├── POS-D/
├── XPRINTER/
├── CBX/
└── OTRAS_MARCAS/
```

* Copie carpetas/archivos aquí o use **Importar**. Pulse **Sincronizar**: no hace falta tocar el código para añadir marcas o modelos.
* Archivos sueltos dentro de una marca se agrupan en el modelo `_GENERAL`.
* Ubicación: junto a `eREDES.exe` en Windows; `~/Documents/eREDES/DRIVERS` en la app de macOS; la raíz del proyecto en desarrollo.
* Antes de instalar se verifica: que esté dentro de la biblioteca, hash SHA-256 sin cambios, SO y arquitectura, firma digital y requisitos (filtros CUPS de un PPD). Paquetes sin firma piden confirmación explícita.

| Formato | Windows | macOS |
|---|---|---|
| INF | `pnputil /add-driver` + `Add-PrinterDriver` (nombres leídos del INF) | No aplica |
| EXE / MSI | Instalador del fabricante; detecta los drivers nuevos al terminar | No aplica |
| PKG / DMG | No aplica | `installer -pkg` con contraseña de administrador |
| PPD | No aplica | Se usa directamente al crear la cola (`lpadmin -P`) |

## Limitaciones importantes (documentadas)

* **macOS 13+ ya no admite colas "raw"**. Para térmicas ESC/POS, eREDES incluye el PPD
  *eREDES ESC/POS Generic RAW*, que envía texto y datos ESC/POS sin conversión. Para imprimir
  documentos/PDF desde apps de macOS hace falta el driver oficial del fabricante.
* **Windows**: crear impresoras, puertos e instalar drivers requiere administrador (el `.exe` lo pide al abrir).
* **IP temporal en Windows con DHCP**: usa `dhcpstaticipcoexistence` (Windows 10 2004+ / 11). Si no está disponible no se toca la red; conecte la impresora por cable directo.
* **ESC/POS**: el estado en tiempo real (DLE EOT) funciona en Epson TM y en la mayoría de clones. Si la respuesta no es válida se muestra "No compatible"; nunca se inventan errores. Los atascos no se informan de forma específica en ESC/POS.
* **Temperatura de CPU**: macOS y Windows no la exponen sin herramientas adicionales → "No disponible".

### Por fabricante (cambio de IP)

| Marca | Estado del perfil | Qué falta para automatizar |
|---|---|---|
| EPSON | Web (UB-E0x, IP de fábrica habitual 192.168.192.168) | — (alternativa oficial: EpsonNet Config) |
| XPRINTER | Web (IP de fábrica habitual 192.168.123.100) | Protocolo UDP de la utilidad oficial (no documentado públicamente) |
| CBX, BIENEX, POS-D | Plantilla incompleta (bloqueada) | Modelo exacto, procedimiento oficial y, si es por comando, la secuencia de bytes del fabricante |

Los perfiles son JSON (`app/resources/ip_profiles/`). Para añadir uno propio copie una plantilla en
`data/ip_profiles/` con `method` = `web`, `http_post` o `raw_tcp` (marcadores `{ip0}..{ip3}`, `{mask0}..`, `{gw0}..`).

## Desarrollo

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python main.py
```

```bash
.venv/bin/python -m pytest -q tests
```

Pruebas sin impresora física: `tools/escpos_emulator.py` emula una térmica ESC/POS por TCP (solo para pruebas).

## Versiones y botón "Actualizar"

* La versión se muestra como **ERMA.V1.0.0** (abajo a la izquierda, junto al botón **Actualizar**).
* El botón consulta las *Releases* del repositorio de GitHub configurado en **Ajustes → Actualizaciones**
  (por defecto `arqueros11/eREDES`), descarga el `.zip` de su sistema, verifica tamaño y SHA-256,
  reemplaza el programa y lo reinicia. **Nunca toca** `DRIVERS`, `data`, `logs` ni `reports`.
* Al abrir, la app busca versiones nuevas en silencio; sin internet simplemente no avisa.
* Repositorio privado: añada un token de GitHub (solo lectura) en Ajustes. Público: no hace falta nada.

**Publicar una versión nueva** (desde el Mac, con el código en GitHub):

```bash
./publicar_version.sh 1.0.1
```

Cambia la versión a ERMA.V1.0.1, ejecuta las pruebas, crea la etiqueta y la sube. GitHub compila
Windows y Mac (unos 10 minutos) y publica `eREDES-Windows.zip` y `eREDES-macOS.zip` en *Releases*.
Desde ese momento, el botón **Actualizar** de cada PC la instala.

## Compilar

* macOS: `./build_mac.sh` → `dist/eREDES.app` y `dist/eREDES-macOS.zip`
* Windows (en un PC con Windows): `build_windows.bat` → `dist\eREDES\eREDES.exe`, `dist\eREDES-Windows.zip`
  y acceso directo "eREDES" en el Escritorio. Ver `LEEME-WINDOWS.txt`.
* Ambos automáticamente: `.github/workflows/build.yml` (GitHub Actions). PyInstaller no compila Windows desde un Mac.

## Estructura

```
main.py                 arranque
app/core/               rutas, validación, comandos seguros, tareas en segundo plano, logs, ajustes
app/database/           SQLite (fabricantes, modelos, drivers, impresoras, historial, diagnósticos, informes, logs)
app/drivers/            biblioteca, analizadores (INF/PPD/PKG/EXE/MSI/DMG) e instaladores con validaciones
app/platform/windows/   PowerShell PrintManagement, pnputil, winspool RAW, IP temporal
app/platform/macos/     CUPS (lpadmin/lpstat/lp), installer, ifconfig alias
app/network/            interfaces, ping/TCP/ARP, OUI, SNMP, mDNS, escáner, recuperación de IP
app/printers/           orquestación de alta/edición/pruebas y protocolo ESC/POS
app/diagnostics/        diagnóstico de impresoras y conectividad
app/speed_test/         speed test
app/system_monitor/     CPU, RAM, disco, procesos, SMART
app/reports/            informes PDF
app/ui/                 interfaz PySide6 (estilo Apple, claro/oscuro)
tests/                  25 pruebas (incluye integración real con CUPS)
```

Datos: `data/eredes.db`, logs en `logs/`, informes en `reports/`.
