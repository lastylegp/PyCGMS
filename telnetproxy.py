import asyncio
import datetime
import threading
import msvcrt   # nur Windows-Konsole
from pathlib import Path

# <<< HIER anpassen >>>
REMOTE_HOST = "192.168.178.22"   # Telnet-Ziel (Host/IP)
REMOTE_PORT = 3000                   # Telnet-Port
LISTEN_HOST = "127.0.0.1"          # Lokales Interface für ZOC
LISTEN_PORT = 2323                 # Port, auf den ZOC verbinden soll
LOGFILE = Path("telnet_hex_log.txt")
# <<< ENDE anpassen >>>


log_lock = threading.Lock()
log_file = None  # wird pro Verbindung gesetzt
active_parsers = []  # für F1-Reset


def ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


def to_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def crc16_xmodem(data: bytes) -> int:
    """
    CRC16-CCITT (XMODEM) – Polynom 0x1021, Initialwert 0x0000.
    Wird bei YMODEM im CRC-Mode verwendet.
    """
    crc = 0x0000
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) & 0xFFFF) ^ 0x1021
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF


class YModemParser:
    """
    YMODEM-Parser / Analyzer pro Richtung (IN oder OUT).
    - erkennt SOH/STX Datenblöcke
    - Header-Blöcke (Block 0) mit Dateiname + Dateigröße
    - Multi-File: Datei-Index (x) und am Ende Gesamtzahl (y)
    - CRC prüfen
    - Resends zählen
    - bei EOT eine Statistik ausgeben
    """
    def __init__(self, direction: str):
        self.direction = direction   # "IN" oder "OUT"
        self.buffer = bytearray()

        # Block-Statistik
        self.total_blocks = 0
        self.crc_ok = 0
        self.crc_error = 0
        self.block_counts = {}  # blk_no -> Anzahl gesehen

        # File-Statistik (pro YMODEM-Batch)
        self.files = []         # Liste von Dicts {index, name, size}
        self.total_files = None # wird am Ende (Null-Header) gesetzt

    def reset(self):
        """Wird aufgerufen, wenn du F1 drückst (neuer Mitschnitt)."""
        self.buffer.clear()
        self.total_blocks = 0
        self.crc_ok = 0
        self.crc_error = 0
        self.block_counts.clear()
        self.files.clear()
        self.total_files = None

    def stats_string(self) -> str:
        lines = [
            "----- YMODEM STATISTIK -----",
            f"Richtung         : {self.direction}",
            f"Blöcke gesamt    : {self.total_blocks}",
            f"CRC OK           : {self.crc_ok}",
            f"CRC Fehler       : {self.crc_error}",
            f"Dateien erkannt  : {len(self.files)}"
        ]

        # Resends
        resend_blocks = {bn: c for bn, c in self.block_counts.items() if c > 1}
        if resend_blocks:
            rs = ", ".join(f"Block {bn} x{cnt}" for bn, cnt in sorted(resend_blocks.items()))
            lines.append(f"Resends          : {rs}")
        else:
            lines.append("Resends          : keine erkannt")

        # Datei-Liste, wenn bekannt
        if self.total_files:
            lines.append(f"Gesamtdateien    : {self.total_files}")
            lines.append("Dateiliste:")
            for f in self.files:
                lines.append(
                    f"  Datei {f['index']} von {self.total_files}: "
                    f"{f['name'] or '(leer)'} – "
                    + (f"{f['size']} Bytes" if f['size'] is not None else "Größe unbekannt")
                )

        lines.append("----------------------------")
        return "\n".join(lines)

    def feed(self, data: bytes):
        events = []

        # 1) Einzelne Kontrollzeichen (EOT etc.)
        ctrl_map = {
            0x04: "EOT (End Of Transmission)",
            0x06: "ACK (Block bestätigt)",
            0x15: "NAK (Block fehlerhaft, erneut senden)",
            0x18: "CA (Cancel / Abbruch)",
            0x43: "'C' (CRC-Start vom Empfänger)",
        }
        for b in data:
            if b in ctrl_map:
                events.append(f"CTRL {ctrl_map[b]} erkannt (0x{b:02X})")
                # Bei EOT Statistik ausgeben
                if b == 0x04:
                    events.append(self.stats_string())

        # 2) SOH/STX-Frames erkennen
        self.buffer.extend(data)

        while True:
            if len(self.buffer) < 3:
                break

            # Suche nach SOH (0x01) / STX (0x02)
            start_idx = None
            for i, b in enumerate(self.buffer):
                if b in (0x01, 0x02):
                    start_idx = i
                    break

            if start_idx is None:
                # Müll abschneiden, Buffer klein halten
                if len(self.buffer) > 2:
                    self.buffer = self.buffer[-2:]
                break

            if start_idx > 0:
                del self.buffer[:start_idx]

            if len(self.buffer) < 3:
                break

            start_byte = self.buffer[0]
            block_size = 128 if start_byte == 0x01 else 1024
            frame_len = 3 + block_size + 2  # SOH/STX + blk + ~blk + data + CRC(2)

            if len(self.buffer) < frame_len:
                break  # noch nicht komplett angekommen

            frame = bytes(self.buffer[:frame_len])
            del self.buffer[:frame_len]

            soh_stx = frame[0]
            blk_no = frame[1]
            blk_inv = frame[2]

            # Blocknummer / Komplement prüfen
            if (blk_no + blk_inv) & 0xFF != 0xFF:
                events.append(
                    f"YMODEM: ungültiger Block: blk={blk_no}, ~blk={blk_inv}"
                )
                continue

            data_bytes = frame[3:3 + block_size]
            crc_recv = (frame[3 + block_size] << 8) | frame[3 + block_size + 1]
            crc_calc = crc16_xmodem(data_bytes)

            self.total_blocks += 1
            self.block_counts[blk_no] = self.block_counts.get(blk_no, 0) + 1
            count = self.block_counts[blk_no]

            block_type = "SOH (128-Byte-Block)" if soh_stx == 0x01 else "STX (1024-Byte-Block)"

            # CRC prüfen
            if crc_calc == crc_recv:
                self.crc_ok += 1
                crc_info = f"CRC OK (0x{crc_calc:04X})"
            else:
                self.crc_error += 1
                crc_info = (
                    f"CRC FEHLER! empfangen=0x{crc_recv:04X}, "
                    f"berechnet=0x{crc_calc:04X}"
                )

            # Resend?
            resend_info = ""
            if count > 1:
                resend_info = f" (RESEND, {count}. Mal)"

            # HEADER-BLOCK (Block 0)
            if blk_no == 0:
                filename = ""
                filesize = None
                raw_size = ""

                zero1 = data_bytes.find(0)
                if zero1 != -1:
                    filename = data_bytes[:zero1].decode("ascii", errors="ignore")

                    rest = data_bytes[zero1 + 1:]
                    zero2 = rest.find(0)
                    if zero2 != -1:
                        raw_size = rest[:zero2].decode("ascii", errors="ignore").strip()
                        try:
                            filesize = int(raw_size) if raw_size else None
                        except ValueError:
                            filesize = None

                # Leerer Header = Ende eines Multi-File-Transfers
                if not filename and not raw_size:
                    # Gesamtzahl Dateien steht jetzt fest
                    self.total_files = len(self.files)
                    events.append(
                        "YMODEM: Kopfblock mit leerem Dateinamen – "
                        f"Ende der Multi-File-Übertragung, Gesamtdateien: {self.total_files}"
                    )
                    # zusätzliche kompakte Zusammenfassung
                    events.append(self.stats_string())
                    continue

                # "normale" Datei – neuer File-Eintrag
                file_index = len(self.files) + 1
                self.files.append({
                    "index": file_index,
                    "name": filename,
                    "size": filesize,
                })

                # Wenn die Gesamtzahl noch unbekannt ist, schreiben wir "von ?"
                total_str = str(self.total_files) if self.total_files else "?"

                header_block = (
                    "===== YMODEM HEADER BLOCK =====\n"
                    f"Richtung  : {self.direction}\n"
                    f"Blocktyp  : {block_type}\n"
                    f"Blocknr.  : {blk_no}{resend_info}\n"
                    f"Datei-Nr. : {file_index} von {total_str}\n"
                    f"Datei     : {filename or '(leer)'}\n"
                    "Größe     : "
                    + (f"{filesize} Bytes" if filesize is not None
                       else f"unbekannt" + (f" (Roh: '{raw_size}')" if raw_size else ""))
                    + "\n"
                    f"{crc_info}\n"
                    "==============================="
                )
                events.append(header_block)
            else:
                # normale Datenblöcke kompakt loggen
                events.append(
                    f"{block_type} Block {blk_no}{resend_info} – {crc_info}"
                )

        return events


def clear_log():
    """
    F1-Handler:
    - Logdatei löschen
    - YMODEM-Parser zurücksetzen (damit Statistik & File-Zählung bei 0 beginnen)
    """
    with log_lock:
        global log_file
        LOGFILE.write_text("")  # Datei leeren
        if log_file is not None:
            log_file.seek(0)
            log_file.write(f"{ts()} [INFO] Log via F1 gelöscht – neuer Mitschnitt beginnt hier\n")
            log_file.flush()

        # Parser zurücksetzen
        for p in active_parsers:
            p.reset()

    print("[INFO] Log und YMODEM-Status zurückgesetzt (F1 gedrückt)")


def keyboard_watcher():
    """Lauscht in der Konsole auf F1 und ruft dann clear_log() auf."""
    print("Drücke F1 in diesem Fenster, um Log + YMODEM-Statistik zu resetten …")
    while True:
        ch = msvcrt.getch()

        # Funktionstasten: Prefix + Scan-Code
        if ch in (b"\x00", b"\xe0"):
            scan = msvcrt.getch()
            # F1 = Scan-Code 59 (0x3B) -> ';'
            if scan == b";":
                clear_log()


async def pipe(reader, writer, direction: str, parser: YModemParser):
    global log_file
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                break

            hex_str = to_hex(data)
            events = parser.feed(data)

            with log_lock:
                # Rohdaten mit IN/OUT in Hex loggen
                log_file.write(
                    f"{ts()} [{direction}] {len(data)} bytes: {hex_str}\n"
                )
                # YMODEM-Ereignisse (auch mit Richtung im Prefix)
                for ev in events:
                    log_file.write(
                        f"{ts()} [{direction}][YMODEM] {ev}\n"
                    )
                log_file.flush()

            writer.write(data)
            await writer.drain()

    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def handle_client(client_reader, client_writer):
    global log_file

    # Parser pro Richtung (IN / OUT)
    ymodem_out = YModemParser("OUT")  # ZOC -> Gerät
    ymodem_in = YModemParser("IN")    # Gerät -> ZOC

    with log_lock:
        # Parser registrieren (für F1-Reset)
        active_parsers.clear()
        active_parsers.extend([ymodem_out, ymodem_in])

        log_file = open(LOGFILE, "a+", encoding="utf-8")
        log_file.write(f"{ts()} [INFO] Neue Verbindung\n")
        log_file.flush()

    try:
        server_reader, server_writer = await asyncio.open_connection(
            REMOTE_HOST, REMOTE_PORT
        )

        with log_lock:
            log_file.write(
                f"{ts()} [INFO] Verbunden mit {REMOTE_HOST}:{REMOTE_PORT}\n"
            )
            log_file.flush()

        await asyncio.gather(
            pipe(client_reader, server_writer, "OUT", ymodem_out),
            pipe(server_reader, client_writer, "IN", ymodem_in),
        )

    finally:
        with log_lock:
            log_file.write(f"{ts()} [INFO] Verbindung beendet\n")
            log_file.flush()


async def main():
    # Tastatur-Thread (F1-Handling) starten
    threading.Thread(target=keyboard_watcher, daemon=True).start()

    server = await asyncio.start_server(handle_client, LISTEN_HOST, LISTEN_PORT)
    print(
        f"Proxy läuft auf {LISTEN_HOST}:{LISTEN_PORT}, "
        f"leitet nach {REMOTE_HOST}:{REMOTE_PORT} weiter"
    )

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
