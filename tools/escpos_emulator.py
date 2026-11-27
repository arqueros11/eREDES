"""Emulador de impresora ESC/POS por TCP — HERRAMIENTA DE PRUEBAS.

No forma parte de la aplicación. Sirve para probar eREDES sin una impresora física:
  * Escucha en un puerto TCP (por defecto 9100 en 127.0.0.1).
  * Guarda los bytes recibidos en un archivo.
  * Responde a DLE EOT n (10 04 n) con bytes de estado ESC/POS según el estado configurado.

Uso:
    python tools/escpos_emulator.py --port 9100 --out recibido.bin [--paper-out] [--cover-open]
"""
from __future__ import annotations

import argparse
import socket
import threading


class EscPosEmulator:
    def __init__(self, host="127.0.0.1", port=9100, out=None, paper_out=False, cover_open=False,
                 paper_near_end=False, cutter_error=False):
        self.host, self.port, self.out = host, port, out
        self.paper_out, self.cover_open = paper_out, cover_open
        self.paper_near_end, self.cutter_error = paper_near_end, cutter_error
        self.received = bytearray()
        self._sock = None
        self._stop = threading.Event()

    def status_byte(self, n: int) -> int:
        b = 0b00010010  # bits fijos: bit1=1, bit4=1
        if n == 1:
            if self.paper_out or self.cover_open:
                b |= 1 << 3  # offline
        elif n == 2:
            if self.cover_open:
                b |= 1 << 2
            if self.paper_out:
                b |= 1 << 5
            if self.paper_out or self.cover_open or self.cutter_error:
                b |= 1 << 6
        elif n == 3:
            if self.cutter_error:
                b |= 1 << 3
        elif n == 4:
            if self.paper_near_end:
                b |= 0b00001100
            if self.paper_out:
                b |= 0b01100000
        return b

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(5)
            buf = b""
            while not self._stop.is_set():
                try:
                    data = conn.recv(4096)
                except (socket.timeout, OSError):
                    break
                if not data:
                    break
                self.received.extend(data)
                buf += data
                i = 0
                while True:
                    i = buf.find(b"\x10\x04", i)
                    if i < 0 or i + 2 >= len(buf):
                        break
                    conn.sendall(bytes([self.status_byte(buf[i + 2])]))
                    i += 3
                buf = buf[-2:]
        if self.out:
            with open(self.out, "wb") as f:
                f.write(self.received)

    def start(self) -> "EscPosEmulator":
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self.port = self._sock.getsockname()[1]
        self._sock.listen(5)
        self._sock.settimeout(0.5)
        threading.Thread(target=self._loop, daemon=True).start()
        return self

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            self._sock.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--out", default="escpos_recibido.bin")
    ap.add_argument("--paper-out", action="store_true")
    ap.add_argument("--cover-open", action="store_true")
    ap.add_argument("--near-end", action="store_true")
    ap.add_argument("--cutter-error", action="store_true")
    a = ap.parse_args()
    emu = EscPosEmulator(a.host, a.port, a.out, a.paper_out, a.cover_open, a.near_end, a.cutter_error).start()
    print(f"Emulador ESC/POS escuchando en {a.host}:{emu.port} (Ctrl+C para salir)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        emu.stop()
