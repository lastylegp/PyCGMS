"""
File Transfer Protokolle für BBS Terminal
Unterstützt: XModem, XModem-CRC, XModem-1K, YModem, ZModem, Punter

Nutzt xmodem Library für XModem (pip install xmodem)
"""

import time
import struct
import os
import socket
from enum import Enum

# Versuche xmodem Library zu laden
try:
    from xmodem import XMODEM, XMODEM1k
    HAS_XMODEM_LIB = True
except ImportError:
    HAS_XMODEM_LIB = False
    print("Warning: xmodem library not found. Install with: pip install xmodem")
    print("Falling back to built-in implementation (may have issues)")


class TransferProtocol(Enum):
    """Verfügbare Transfer-Protokolle"""
    XMODEM = "XModem"
    XMODEM_CRC = "XModem-CRC"
    XMODEM_1K = "XModem-1K"
    YMODEM = "YModem"
    ZMODEM = "ZModem"
    PUNTER = "Punter"              # C1 + Multi autodetect, no timeouts
    TURBOMODEM = "TurboModem"      # Ultra-fast! 10-20x faster than XModem
    # HIGH-SPEED PROTOCOLS (für LAN - maximaler Speed)
    # YMODEM_G entfernt - funktioniert nicht zuverlässig über Telnet
    RAWTCP = "RawTCP"              # Zero overhead, maximum line speed


class TransferSpeed(Enum):
    """Transfer-Geschwindigkeits-Profile"""
    TURBO = "turbo"       # Für schnelle, stabile Verbindungen
    FAST = "fast"         # Standard Internet
    NORMAL = "normal"     # Konservativ
    SLOW = "slow"         # Für problematische BBS
    LOCAL = "local"       # Für lokale Verbindungen (kein Netzwerk-Delay)


# Transfer-Profile mit Timing-Einstellungen
# Format: (inter_block_delay, post_ack_delay, timeout_multiplier)
TRANSFER_PROFILES = {
    TransferSpeed.TURBO:  (0.02, 0.01, 0.5),   # 20ms, 10ms, kurze Timeouts
    TransferSpeed.FAST:   (0.05, 0.02, 1.0),   # 50ms, 20ms, normal
    TransferSpeed.NORMAL: (0.15, 0.05, 1.5),   # 150ms, 50ms, länger
    TransferSpeed.SLOW:   (0.30, 0.10, 2.0),   # 300ms, 100ms, sehr konservativ
    TransferSpeed.LOCAL:  (0.50, 0.20, 3.0),   # 500ms, 200ms, für lokale BBS
}


# XModem Konstanten
SOH = 0x01  # Start of Header (128 byte blocks)
STX = 0x02  # Start of Header (1024 byte blocks)
EOT = 0x04  # End of Transmission
ACK = 0x06  # Acknowledge
NAK = 0x15  # Negative Acknowledge
CAN = 0x18  # Cancel
CRC = 0x43  # 'C' - Request CRC mode

# Timing Konstanten (Default - kann durch Profile überschrieben werden)
INTER_BLOCK_DELAY = 0.15  # 150ms zwischen Blocks (Standard)


class FileTransfer:
    """Base class für File Transfers"""
    
    # Punter Codes als Klassenvariablen für externen Zugriff
    PUNTER_GOO = b'GOO'
    PUNTER_BAD = b'BAD'
    PUNTER_ACK = b'ACK'
    PUNTER_SYN = b'SYN'
    PUNTER_SB = b'S/B'
    
    def __init__(self, connection, protocol=TransferProtocol.XMODEM_CRC, 
                 speed_profile=TransferSpeed.NORMAL, log_dir=None, debug=False):
        self.connection = connection
        self.protocol = protocol
        self.cancel_requested = False
        self.byte_buffer = bytearray()  # Buffer für empfangene Bytes
        
        # Transfer-Profil setzen
        self.speed_profile = speed_profile
        self._apply_speed_profile()
        
        # Debug-Logging - nur wenn debug=True
        self.debug_enabled = debug
        self.punter_debug = debug  # Detailliertes Punter Hex-Logging
        self.debug_log = []
        self.debug_file = None
        self.log_dir = log_dir
        if debug:
            self._init_debug_log(log_dir)
        
        # Live-Callback für GUI-Updates (IN/OUT Anzeige)
        self.live_callback = None
        
        # Manuelle Send-Unterstützung
        self.manual_send_queue = []  # Queue für manuelle Sends
        self.waiting_for_input = False  # Flag ob auf Input gewartet wird
        self.waiting_for_codes = []  # Welche Codes erwartet werden
        
        # Letzter empfangener Dateipfad (für High-Speed Protokolle)
        self.last_received_filepath = None
        
        # TurboModem Multi-File Support
        self.turbomodem_received_files = []
    
    def set_live_callback(self, callback):
        """
        Setzt Callback für Live IN/OUT Updates.
        callback(direction, data, description)
        direction: 'IN', 'OUT', 'WAIT', 'STATUS'
        """
        self.live_callback = callback
    
    def _live_update(self, direction, data, description=""):
        """Sendet Live-Update an GUI - nur wichtige Events"""
        if self.live_callback:
            try:
                self.live_callback(direction, data, description)
            except:
                pass
    
    def manual_send_goo(self):
        """Manuell GOO senden"""
        self._manual_send(self.PUNTER_GOO, "MANUAL GOO")
    
    def manual_send_ack(self):
        """Manuell ACK senden"""
        self._manual_send(self.PUNTER_ACK, "MANUAL ACK")
    
    def manual_send_sb(self):
        """Manuell S/B senden"""
        self._manual_send(self.PUNTER_SB, "MANUAL S/B")
    
    def manual_send_syn(self):
        """Manuell SYN senden"""
        self._manual_send(self.PUNTER_SYN, "MANUAL SYN")
    
    def _manual_send(self, code, description):
        """Führt manuellen Send aus"""
        hex_str = ' '.join(f'{b:02X}' for b in code)
        ascii_str = code.decode('ascii', errors='replace')
        self.log(f"    [MANUAL OUT] {hex_str} |{ascii_str}| - {description}")
        self._live_update('OUT', code, f"MANUAL: {ascii_str}")
        self.send_raw(code)
    
    def set_punter_debug(self, enabled):
        """Schaltet detailliertes Punter Hex-Logging ein/aus"""
        self.punter_debug = enabled
        self.log(f"Punter debug logging: {'ON' if enabled else 'OFF'}")
    
    def get_log_file(self):
        """Gibt den Pfad zur aktuellen Log-Datei zurück"""
        return self.debug_file
    
    def punter_log(self, message):
        """Loggt nur wenn punter_debug aktiviert ist"""
        if self.punter_debug:
            self.log(message)
    
    def _apply_speed_profile(self):
        """Wendet Transfer-Profil Einstellungen an"""
        if self.speed_profile in TRANSFER_PROFILES:
            delays = TRANSFER_PROFILES[self.speed_profile]
            self.inter_block_delay = delays[0]
            self.post_ack_delay = delays[1]
            self.timeout_multiplier = delays[2]
        else:
            # Default: NORMAL
            self.inter_block_delay = 0.15
            self.post_ack_delay = 0.05
            self.timeout_multiplier = 1.5
    
    def set_speed_profile(self, profile):
        """Ändert Transfer-Profil zur Laufzeit"""
        if isinstance(profile, str):
            profile = TransferSpeed(profile)
        self.speed_profile = profile
        self._apply_speed_profile()
        self.log(f"Speed profile changed to: {profile.value}")
        self.log(f"  inter_block_delay: {self.inter_block_delay}s")
        self.log(f"  post_ack_delay: {self.post_ack_delay}s")
        self.log(f"  timeout_multiplier: {self.timeout_multiplier}x")
    
    def _init_debug_log(self, log_dir=None):
        """Initialisiert Debug-Log-Datei"""
        if self.debug_enabled:
            import datetime
            import os
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Log-Verzeichnis bestimmen
            if log_dir:
                self.log_dir = log_dir
            else:
                # Standard: Aktuelles Verzeichnis oder Home
                self.log_dir = os.getcwd()
            
            # Stelle sicher, dass Verzeichnis existiert
            os.makedirs(self.log_dir, exist_ok=True)
            
            self.debug_file = os.path.join(self.log_dir, f"transfer_debug_{timestamp}.log")
            self.log(f"=== File Transfer Debug Log ===")
            self.log(f"Protocol: {self.protocol.name}")
            self.log(f"Speed Profile: {self.speed_profile.value}")
            self.log(f"  inter_block_delay: {self.inter_block_delay}s")
            self.log(f"  post_ack_delay: {self.post_ack_delay}s")
            self.log(f"  timeout_multiplier: {self.timeout_multiplier}x")
            self.log(f"Punter Debug: {self.punter_debug}")
            self.log(f"Log File: {self.debug_file}")
            self.log(f"Timestamp: {timestamp}")
            self.log(f"=" * 50)
    
    def set_log_dir(self, log_dir):
        """Setzt Log-Verzeichnis und erstellt neue Log-Datei"""
        import os
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self._init_debug_log(log_dir)
    
    def log(self, message):
        """Schreibt Debug-Message"""
        if self.debug_enabled:
            import datetime
            timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            log_line = f"[{timestamp}] {message}"
            self.debug_log.append(log_line)
            # print(log_line)  # Deaktiviert - nur ins File!
            
            # Schreibe auch in Datei
            if self.debug_file:
                try:
                    with open(self.debug_file, 'a', encoding='utf-8') as f:
                        f.write(log_line + '\n')
                        f.flush()  # Sofort schreiben
                except Exception as e:
                    print(f"Log write error: {e}")
    
    def log_bytes(self, direction, data, description=""):
        """Logged Byte-Daten in lesbarer Form"""
        if self.debug_enabled and data:
            if isinstance(data, int):
                data = bytes([data])
            elif isinstance(data, str):
                data = data.encode('latin-1')
            
            hex_str = ' '.join(f'{b:02X}' for b in data)
            ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data)
            
            self.log(f"{direction} {description}")
            self.log(f"  HEX:   {hex_str}")
            self.log(f"  ASCII: {ascii_str}")
            self.log(f"  LEN:   {len(data)} bytes")
        
    def cancel(self):
        """Bricht Transfer ab"""
        self.cancel_requested = True
    
    def send_raw(self, data):
        """Sendet rohe Bytes an BBS"""
        try:
            # Konvertiere zu bytes falls nötig
            if isinstance(data, str):
                data = data.encode('latin-1')
            elif isinstance(data, (list, bytearray)):
                data = bytes(data)
            
            self.log(f"[send_raw] Sending {len(data)} bytes...")
            
            # Benutze connection.send_raw() um das Traffic-Logging mitzunehmen
            if hasattr(self.connection, 'send_raw'):
                self.log(f"[send_raw] Using connection.send_raw()")
                result = self.connection.send_raw(data)
                self.log(f"[send_raw] connection.send_raw() returned: {result}")
                if result:
                    return len(data)
                else:
                    self.log(f"[send_raw] ERROR: connection.send_raw() returned False!")
                    return None
            
            # Fallback: Direkt über socket
            if hasattr(self.connection, 'socket') and self.connection.socket:
                self.log(f"[send_raw] Fallback: Using direct socket")
                sock = self.connection.socket
                connected = getattr(self.connection, 'connected', True)
                
                if connected:
                    try:
                        sock.sendall(data)
                        self.log(f"[send_raw] socket.sendall() OK")
                        return len(data)
                    except Exception as e:
                        self.log(f"[send_raw] ERROR: socket.sendall() {e}")
                        return None
            
            self.log(f"[send_raw] ERROR: No socket or send_raw found!")
            self.log(f"[send_raw] connection type: {type(self.connection)}")
            self.log(f"[send_raw] has send_raw: {hasattr(self.connection, 'send_raw')}")
            self.log(f"[send_raw] has socket: {hasattr(self.connection, 'socket')}")
            return None
            
        except Exception as e:
            self.log(f"send_raw ERROR: {e}")
            import traceback
            self.log(traceback.format_exc())
            return None
        
    def send_file(self, filepath, callback=None):
        """
        Sendet Datei mit gewähltem Protokoll
        
        Args:
            filepath: Pfad zur Datei ODER Liste von Dateien
            callback: Optional - Funktion(bytes_sent, total_bytes, status_msg)
        
        Returns:
            True bei Erfolg, False bei Fehler
        """
        # Punter Upload (autodetect via file count):
        # - Single File: _punter_send() - OHNE Header (BBS kennt Filename bereits)
        # - Multi-File: _punter_send_multi() - MIT Header pro Datei + End-Marker
        if self.protocol == TransferProtocol.PUNTER:
            if isinstance(filepath, list):
                if len(filepath) == 0:
                    self.log("✗ ERROR: Empty file list")
                    return False
                elif len(filepath) == 1:
                    return self._punter_send(filepath[0], callback)  # Single: No header
                else:
                    return self._punter_send_multi(filepath, callback)  # Multi: With headers
            else:
                return self._punter_send(filepath, callback)  # Single: No header
        
        # Multi-File: YMODEM, Punter, RAWTCP und TURBOMODEM unterstützen das nativ
        # Andere Protokolle: Nur erstes File nehmen
        if isinstance(filepath, list) and len(filepath) > 1:
            if self.protocol not in [TransferProtocol.YMODEM, TransferProtocol.RAWTCP,
                                      TransferProtocol.PUNTER,
                                      TransferProtocol.TURBOMODEM, TransferProtocol.ZMODEM]:
                self.log(f"⚠ {self.protocol.value} unterstützt kein Multi-File, nehme erste Datei")
                filepath = filepath[0]
        
        # Für XModem/XModem-1K: Nur Single-File möglich
        if self.protocol in [TransferProtocol.XMODEM, TransferProtocol.XMODEM_CRC, TransferProtocol.XMODEM_1K]:
            if isinstance(filepath, list):
                if len(filepath) == 0:
                    self.log("✗ ERROR: Empty file list")
                    return False
                filepath = filepath[0]  # XModem kann nur 1 File
            return self._xmodem_send(filepath, callback)
        
        # YModem: Kann Liste ODER Single-File
        elif self.protocol == TransferProtocol.YMODEM:
            if isinstance(filepath, list) and len(filepath) == 0:
                self.log("✗ ERROR: Empty file list")
                return False
            return self._ymodem_send(filepath, callback)
        
        # Andere Protokolle
        else:
            # RAWTCP: Unterstützt Batch nativ
            if self.protocol == TransferProtocol.RAWTCP:
                return self._rawtcp_send(filepath, callback)  # Akzeptiert String oder Liste
            
            # TurboModem: Unterstützt auch Multi-File!
            if self.protocol == TransferProtocol.TURBOMODEM:
                return self._turbomodem_send(filepath, callback)  # Akzeptiert String oder Liste
            
            # Restliche Protokolle: Single-File only (außer ZModem)
            if isinstance(filepath, list):
                if len(filepath) == 0:
                    self.log("✗ ERROR: Empty file list")
                    return False
                if self.protocol != TransferProtocol.ZMODEM:
                    filepath = filepath[0]
            
            if self.protocol == TransferProtocol.ZMODEM:
                return self._zmodem_send(filepath, callback)
            else:
                raise ValueError(f"Unbekanntes Protokoll: {self.protocol}")
    
    def receive_file(self, filepath, callback=None):
        """
        Empfängt Datei mit gewähltem Protokoll
        
        Args:
            filepath: Pfad zum Speichern (bei Punter: kann Verzeichnis sein)
            callback: Optional - Funktion(bytes_received, status_msg)
        Returns:
            True bei Erfolg, False bei Fehler
            
        Note:
            Nach erfolgreichem Transfer enthält self.last_received_filepath 
            den tatsächlichen Dateipfad (wichtig für Protokolle die den
            Dateinamen selbst übermitteln wie RAWTCP)
        """
        self.log(f"\n>>> receive_file() called")
        self.log(f"    filepath: {filepath}")
        self.log(f"    protocol: {self.protocol}")

        # Reset last received filepath
        self.last_received_filepath = None
        
        try:
            if self.protocol in [TransferProtocol.XMODEM, TransferProtocol.XMODEM_CRC, TransferProtocol.XMODEM_1K]:
                return self._xmodem_receive(filepath, callback)
            elif self.protocol == TransferProtocol.YMODEM:
                return self._ymodem_receive(filepath, callback)
            elif self.protocol == TransferProtocol.ZMODEM:
                return self._zmodem_receive(filepath, callback)
            elif self.protocol == TransferProtocol.PUNTER:
                self.log("    -> routing to _punter_receive()")
                return self._punter_receive(filepath, callback)
            elif self.protocol == TransferProtocol.TURBOMODEM:
                # TurboModem gibt (success, files_list) zurück für Multi-File Support
                success, received_files = self._turbomodem_receive(filepath, callback)
                # Speichere empfangene Dateien für späteren Zugriff
                self.turbomodem_received_files = received_files if received_files else []
                return success
            # HIGH-SPEED PROTOCOLS - return (success, filepath) tuple
            elif self.protocol == TransferProtocol.RAWTCP:
                self.log("    -> routing to _rawtcp_receive()")
                self.log(f"    -> connection type: {type(self.connection)}")
                success, actual_path = self._rawtcp_receive(filepath, callback)
                self.last_received_filepath = actual_path
                self.log(f"    -> _rawtcp_receive returned: success={success}, path={actual_path}")
                return success
            else:
                raise ValueError(f"Unbekanntes Protokoll: {self.protocol}")
        except Exception as e:
            self.log(f"ERROR in receive_file: {e}")
            import traceback
            self.log(traceback.format_exc())
            raise
    
    def _xmodem_send(self, filepath, callback):
        """XModem Send Implementation - nutzt xmodem Library wenn verfügbar"""
        self.log(f"\n{'='*60}")
        self.log(f"XMODEM SEND: {filepath}")
        self.log(f"Protocol: {self.protocol.value}")
        self.log(f"{'='*60}")
        
        if HAS_XMODEM_LIB:
            return self._xmodem_send_library(filepath, callback)
        else:
            return self._xmodem_send_builtin(filepath, callback)
    
    def _xmodem_send_library(self, filepath, callback):
        """XModem Send mit xmodem Library"""
        import os
        
        # getc/putc Interface für Library
        def getc(size, timeout=3):
            """Liest Bytes vom Client"""
            self.connection.settimeout(timeout)
            try:
                data = self.connection.get_received_data_raw(size)
                return data if data else None
            except:
                return None
        
        def putc(data, timeout=3):
            """Sendet Bytes an Client"""
            try:
                self.send_raw(data)
                return len(data)
            except:
                return None
        
        # Wähle richtigen XMODEM Typ
        if self.protocol == TransferProtocol.XMODEM_1K:
            modem = XMODEM1k(getc, putc)
        else:
            modem = XMODEM(getc, putc)
        
        # Sende Datei
        try:
            filesize = os.path.getsize(filepath)
            self.log(f"Sende {filesize} bytes...")
            
            # Progress-Tracking Stream Wrapper
            class ProgressStream:
                def __init__(self, file_obj, callback, total_size):
                    self.file = file_obj
                    self.callback = callback
                    self.total_size = total_size
                    self.bytes_sent = 0
                    self.last_update = 0
                
                def read(self, size):
                    data = self.file.read(size)
                    if data:
                        self.bytes_sent += len(data)
                        # Update alle 1024 bytes oder am Ende
                        if self.bytes_sent - self.last_update >= 1024 or self.bytes_sent >= self.total_size:
                            if self.callback:
                                self.callback(self.bytes_sent, self.total_size, 
                                            f"Sending block {self.bytes_sent // 1024}")
                            self.last_update = self.bytes_sent
                    return data
            
            with open(filepath, 'rb') as f:
                stream = ProgressStream(f, callback, filesize)
                # xmodem library handled alles (NAK/CRC warten, ACKs, etc.)
                success = modem.send(stream, retry=16, timeout=10)
                
                if success:
                    self.log("✓ XMODEM SEND ERFOLGREICH")
                    if callback:
                        callback(filesize, filesize, "Transfer complete")
                    return True
                else:
                    self.log("✗ XMODEM SEND FEHLGESCHLAGEN")
                    return False
        except Exception as e:
            self.log(f"ERROR: {str(e)}")
            return False
    
    def _xmodem_send_builtin(self, filepath, callback):
        """XModem Send Implementation"""
        self.log(f"\n{'='*60}")
        self.log(f"XMODEM SEND START")
        self.log(f"File: {filepath}")
        self.log(f"Protocol: {self.protocol.name}")
        
        try:
            with open(filepath, 'rb') as f:
                file_data = f.read()
            
            self.log(f"File loaded: {len(file_data)} bytes")
            
            # Bestimme Block-Größe
            if self.protocol == TransferProtocol.XMODEM_1K:
                block_size = 1024
                header = STX
                self.log(f"Using XModem-1K: block_size=1024, header=STX(0x{STX:02X})")
            else:
                block_size = 128
                header = SOH
                self.log(f"Using XModem/XModem-CRC: block_size=128, header=SOH(0x{SOH:02X})")
            
            use_crc = (self.protocol == TransferProtocol.XMODEM_CRC)
            self.log(f"Use CRC: {use_crc}")
            
            total_size = len(file_data)
            blocks_total = (total_size + block_size - 1) // block_size
            self.log(f"Total blocks: {blocks_total}")
            
            if callback:
                callback(0, total_size, "Warte auf Empfänger...")
            
            # Warte auf NAK oder 'C' vom Empfänger
            self.log(f"\nWaiting for start signal (NAK=0x{NAK:02X} or C=0x{CRC:02X})...")
            start_char = self._wait_for_start(use_crc, timeout=60)
            
            if start_char is None:
                self.log(f"ERROR: Timeout waiting for start signal!")
                if callback:
                    callback(0, total_size, "Fehler: Kein Start-Signal")
                return False
            
            self.log(f"Received start signal: 0x{start_char:02X}")
            
            # Sende Blöcke
            block_num = 1
            bytes_sent = 0
            
            for offset in range(0, total_size, block_size):
                if self.cancel_requested:
                    self.log(f"Transfer cancelled by user")
                    self._send_byte(CAN)
                    return False
                
                # Hole Block-Daten
                block_data = file_data[offset:offset + block_size]
                
                # Padding falls nötig
                if len(block_data) < block_size:
                    padding_needed = block_size - len(block_data)
                    block_data += b'\x1A' * padding_needed
                    self.log(f"\nBlock {block_num}: Added {padding_needed} bytes padding")
                else:
                    self.log(f"\nBlock {block_num}: Full block")
                
                # Sende Block mit Retries
                max_retries = 10
                for retry in range(max_retries):
                    self.log(f"Sending block {block_num} (attempt {retry+1}/{max_retries})")
                    
                    if self._send_block(block_num, block_data, header, use_crc):
                        self.log(f"Block {block_num} ACKed")
                        break
                    else:
                        self.log(f"Block {block_num} NAKed, retrying...")
                        if retry == max_retries - 1:
                            self.log(f"ERROR: Block {block_num} failed after {max_retries} retries")
                            return False
                
                bytes_sent += block_size
                block_num = (block_num + 1) % 256
                
                if callback:
                    callback(min(bytes_sent, total_size), total_size, f"Block {block_num-1}")
            
            # Sende EOT
            self.log(f"\nSending EOT...")
            self._send_byte(EOT)
            self.log_bytes(">>>", EOT, "EOT")
            
            # Warte auf ACK für EOT
            self.log(f"Waiting for EOT ACK...")
            if self._wait_for_ack(timeout=10):
                self.log(f"SUCCESS: Transfer complete!")
                if callback:
                    callback(total_size, total_size, "Erfolgreich!")
                return True
            else:
                self.log(f"ERROR: No ACK for EOT")
                if callback:
                    callback(total_size, total_size, "Fehler: Kein EOT ACK")
                return False
                
        except Exception as e:
            self.log(f"EXCEPTION in _xmodem_send: {e}")
            import traceback
            self.log(traceback.format_exc())
            if callback:
                callback(0, 0, f"Fehler: {e}")
            return False
            start_char = self._wait_for_start(use_crc)
            if not start_char:
                if callback:
                    callback(0, total_size, "Timeout - Empfänger antwortet nicht")
                return False
            
            # Sende Blöcke
            block_num = 1
            for i in range(0, total_size, block_size):
                if self.cancel_requested:
                    self.connection.send_raw(bytes([CAN]))  # RAW!
                    if callback:
                        callback(i, total_size, "Transfer abgebrochen")
                    return False
                
                # Erstelle Block
                block = file_data[i:i + block_size]
                if len(block) < block_size:
                    block += b'\x1A' * (block_size - len(block))  # Padding mit EOF
                
                # Sende Block mit Retries
                if not self._send_block(block, block_num, header, use_crc):
                    if callback:
                        callback(i, total_size, f"Fehler bei Block {block_num}")
                    return False
                
                if callback:
                    callback(i + len(block), total_size, f"Block {block_num}/{blocks_total}")
                
                block_num = (block_num + 1) % 256
            
            # Sende EOT
            self.connection.send_raw(bytes([EOT]))  # RAW!
            if self._wait_for_ack():
                if callback:
                    callback(total_size, total_size, "Transfer erfolgreich!")
                return True
            else:
                if callback:
                    callback(total_size, total_size, "EOT nicht bestätigt")
                return False
                
        except Exception as e:
            if callback:
                callback(0, 0, f"Fehler: {str(e)}")
            return False
    
    def _xmodem_receive(self, filepath, callback):
        """XModem Receive Implementation - nutzt xmodem Library wenn verfügbar"""
        self.log(f"\n{'='*60}")
        self.log(f"XMODEM RECEIVE: {filepath}")
        self.log(f"Protocol: {self.protocol.value}")
        self.log(f"{'='*60}")
        
        if HAS_XMODEM_LIB:
            return self._xmodem_receive_library(filepath, callback)
        else:
            return self._xmodem_receive_builtin(filepath, callback)
    
    def _xmodem_receive_library(self, filepath, callback):
        """XModem Receive mit xmodem Library"""
        # WICHTIG: KEIN Buffer Clear mehr!
        # Das Buffer Clear hat Daten gelöscht die das BBS bereits gesendet hatte
        # Die xmodem Library handhabt alte Daten korrekt durch Timeout/Retry
        
        # Kurze Pause damit BBS bereit ist
        import time
        time.sleep(0.1)  # 200ms → 100ms
        
        # getc/putc Interface für Library
        def getc(size, timeout=3):
            """Liest Bytes vom Client"""
            self.connection.settimeout(timeout)
            try:
                data = self.connection.get_received_data_raw(size)
                return data if data else None
            except:
                return None
        
        def putc(data, timeout=3):
            """Sendet Bytes an Client"""
            try:
                self.send_raw(data)
                return len(data)
            except:
                return None
        
        # Wähle richtigen XMODEM Typ
        if self.protocol == TransferProtocol.XMODEM_1K:
            modem = XMODEM1k(getc, putc)
        else:
            modem = XMODEM(getc, putc)
        
        # Empfange Datei
        try:
            self.log("Warte auf Daten...")
            
            # Progress-Tracking Stream Wrapper
            class ProgressStream:
                def __init__(self, file_obj, callback):
                    self.file = file_obj
                    self.callback = callback
                    self.bytes_received = 0
                    self.last_update = 0
                
                def write(self, data):
                    result = self.file.write(data)
                    if data:
                        self.bytes_received += len(data)
                        # Update alle 1024 bytes
                        if self.bytes_received - self.last_update >= 1024:
                            if self.callback:
                                # Callback mit 3 Parametern: (done, total, status)
                                # Bei Receive kennen wir total nicht, also 0
                                self.callback(
                                    self.bytes_received,
                                    0,  # Total unknown
                                    f"Receiving block {self.bytes_received // 1024}"
                                )
                            self.last_update = self.bytes_received
                    return result
            
            with open(filepath, 'wb') as f:
                stream = ProgressStream(f, callback)
                # xmodem library handled alles (CRC senden, NAKs, etc.)
                # Erhöhe retry auf 32 für Linux-Kompatibilität
                success = modem.recv(stream, retry=32, timeout=10)
                
                if success:
                    import os
                    filesize = os.path.getsize(filepath)
                    self.log(f"✓ XMODEM RECEIVE ERFOLGREICH ({filesize} bytes)")
                    if callback:
                        # Final callback mit korrekter Größe
                        callback(filesize, filesize, "Transfer complete")
                    return True
                else:
                    self.log("✗ XMODEM RECEIVE FEHLGESCHLAGEN")
                    return False
        except Exception as e:
            self.log(f"ERROR: {str(e)}")
            return False
    
    def _xmodem_receive_builtin(self, filepath, callback):
        """XModem Receive Implementation"""
        try:
            use_crc = (self.protocol == TransferProtocol.XMODEM_CRC)
            
            if callback:
                callback(0, 0, "Starte Empfang...")
            
            # Sende NAK oder 'C' um Transfer zu starten
            if use_crc:
                self.connection.send_raw(bytes([CRC]))  # 'C' = 0x43
            else:
                self.connection.send_raw(bytes([NAK]))  # NAK = 0x15
            
            received_data = bytearray()
            block_num = 1
            
            while not self.cancel_requested:
                # Warte auf Header
                header = self._read_byte(timeout=10)
                if header is None:
                    if callback:
                        callback(len(received_data), 0, "Timeout beim Warten auf Block")
                    return False
                
                if header == EOT:
                    # Transfer beendet
                    self.connection.send_raw(bytes([ACK]))  # RAW!
                    break
                
                if header == CAN:
                    if callback:
                        callback(len(received_data), 0, "Transfer vom Sender abgebrochen")
                    return False
                
                if header not in [SOH, STX]:
                    continue
                
                # Empfange Block
                block_size = 1024 if header == STX else 128
                block_data = self._receive_block(block_num, block_size, use_crc)
                
                if block_data:
                    received_data.extend(block_data)
                    self.connection.send_raw(bytes([ACK]))  # RAW!
                    block_num = (block_num + 1) % 256
                    
                    if callback:
                        callback(len(received_data), 0, f"Empfangen: {len(received_data)} bytes")
                else:
                    self.connection.send_raw(bytes([NAK]))  # RAW!
            
            # Speichere Datei
            with open(filepath, 'wb') as f:
                f.write(received_data)
            
            if callback:
                callback(len(received_data), len(received_data), "Empfang erfolgreich!")
            
            return True
            
        except Exception as e:
            if callback:
                callback(0, 0, f"Fehler: {str(e)}")
            return False
    
    def _send_block(self, block_num, block_data, header, use_crc):
        """Sendet einen XModem Block"""
        # Erstelle Block-Payload
        block_payload = bytearray()
        block_payload.append(header)
        block_payload.append(block_num)
        block_payload.append(255 - block_num)
        block_payload.extend(block_data)
        
        # Berechne und füge Checksum/CRC hinzu
        if use_crc:
            crc = self._calc_crc(block_data)
            block_payload.append((crc >> 8) & 0xFF)
            block_payload.append(crc & 0xFF)
            checksum_type = f"CRC=0x{crc:04X}"
        else:
            checksum = sum(block_data) % 256
            block_payload.append(checksum)
            checksum_type = f"Checksum=0x{checksum:02X}"
        
        self.log(f"  Header: 0x{header:02X}, Block#: {block_num}, ~Block#: {255-block_num}")
        self.log(f"  Data: {len(block_data)} bytes, {checksum_type}")
        
        # Sende kompletten Block ALS ROHE BYTES (nicht PETSCII!)
        self.connection.send_raw(bytes(block_payload))
        
        self.log_bytes(">>>", block_payload, f"Block {block_num}")
        
        # Warte auf Response (BBS braucht Zeit zum Empfangen)
        self.log(f"  Waiting for ACK/NAK...")
        response = self._read_byte(timeout=10)
        
        if response is None:
            self.log(f"  ERROR: Timeout waiting for response")
            return False
        
        self.log(f"  Response: 0x{response:02X}")
        
        if response == ACK:
            self.log(f"  Got ACK ✓")
            # WICHTIG: Kurze Pause nach ACK!
            # Gibt BBS Zeit sich auf nächsten Block vorzubereiten
            # Besonders wichtig bei lokaler Verbindung (niedrige Latenz)
            import time
            time.sleep(self.inter_block_delay)
            return True
        elif response == NAK:
            self.log(f"  Got NAK (retry needed)")
            return False
        elif response == CAN:
            self.log(f"  Got CAN (transfer cancelled)")
            return False
        else:
            self.log(f"  Got unexpected byte: 0x{response:02X}")
            return False
    
    def _receive_block(self, expected_block_num, block_size, use_crc):
        """Empfängt einen XModem Block"""
        # Lese Block-Nummer
        block_num = self._read_byte(timeout=1)
        block_num_comp = self._read_byte(timeout=1)
        
        if block_num is None or block_num_comp is None:
            return None
        
        if block_num != expected_block_num or block_num != (255 - block_num_comp):
            return None
        
        # Lese Daten
        block_data = bytearray()
        for i in range(block_size):
            byte = self._read_byte(timeout=1)
            if byte is None:
                return None
            block_data.append(byte)
        
        # Prüfe Checksum/CRC
        if use_crc:
            crc_high = self._read_byte(timeout=1)
            crc_low = self._read_byte(timeout=1)
            if crc_high is None or crc_low is None:
                return None
            expected_crc = (crc_high << 8) | crc_low
            actual_crc = self._calc_crc(block_data)
            if expected_crc != actual_crc:
                return None
        else:
            checksum = self._read_byte(timeout=1)
            if checksum is None:
                return None
            expected_checksum = sum(block_data) % 256
            if checksum != expected_checksum:
                return None
        
        return block_data
    
    def _wait_for_start(self, use_crc, timeout=60):
        """Wartet auf Start-Signal (NAK oder 'C')"""
        self.log(f"Waiting for start signal (timeout={timeout}s)...")
        if use_crc:
            self.log(f"  Expected: 'C' (0x{CRC:02X}) for CRC mode")
        else:
            self.log(f"  Expected: NAK (0x{NAK:02X}) for checksum mode")
        
        end_time = time.time() + timeout
        bytes_received = []
        
        while time.time() < end_time:
            byte = self._read_byte(timeout=1)
            if byte is not None:
                bytes_received.append(byte)
                self.log(f"  Received: 0x{byte:02X} ({chr(byte) if 32 <= byte < 127 else '?'})")
                
                if byte == CRC and use_crc:
                    self.log(f"  Got 'C' - CRC mode confirmed!")
                    return CRC
                elif byte == NAK:
                    self.log(f"  Got NAK - starting transfer")
                    return NAK
        
        self.log(f"  TIMEOUT! Received {len(bytes_received)} bytes:")
        for i, b in enumerate(bytes_received[:20]):  # Show first 20
            self.log(f"    [{i}] 0x{b:02X}")
        return None
    
    def _wait_for_ack(self, timeout=10):
        """Wartet auf ACK"""
        self.log(f"Waiting for ACK (timeout={timeout}s)...")
        byte = self._read_byte(timeout=timeout)
        if byte is None:
            self.log(f"  TIMEOUT waiting for ACK!")
            return False
        self.log(f"  Received: 0x{byte:02X}")
        if byte == ACK:
            self.log(f"  Got ACK ✓")
            return True
        else:
            self.log(f"  Expected ACK (0x{ACK:02X}), got 0x{byte:02X}")
            return False
    
    def _read_byte(self, timeout=1):
        """Liest ein Byte mit Timeout über connection.get_received_data()"""
        end_time = time.time() + timeout
        poll_count = 0
        
        while time.time() < end_time:
            poll_count += 1
            
            # Check für Cancel
            if self.cancel_requested:
                return None
            
            # Erst im Buffer schauen
            if len(self.byte_buffer) > 0:
                byte = self.byte_buffer.pop(0)
                if self.punter_debug and poll_count > 10:
                    self.log(f"    [POLL] Got byte from buffer after {poll_count} polls")
                return byte
            
            # Hole Daten über connection.get_received_data()
            if hasattr(self.connection, 'get_received_data'):
                # DEBUG: Prüfe Queue-Status
                has_data = False
                if hasattr(self.connection, 'has_received_data'):
                    has_data = self.connection.has_received_data()
                
                if has_data:
                    data = self.connection.get_received_data(timeout=0.1)
                    if self.punter_debug:
                        self.log(f"    [POLL] has_data=True, got: {data}")
                else:
                    data = self.connection.get_received_data(timeout=0.05)
                
                if data:
                    # DEBUG: Log empfangene Daten
                    if self.punter_debug:
                        if isinstance(data, bytes):
                            hex_str = ' '.join(f'{b:02X}' for b in data[:20])
                            self.log(f"    [RAW RECV] {len(data)} bytes: {hex_str}")
                            self._live_update('IN', data[:20], f"RAW: {len(data)} bytes")
                        elif isinstance(data, str):
                            hex_str = ' '.join(f'{ord(c):02X}' for c in data[:20])
                            self.log(f"    [RAW RECV STR] {len(data)} chars: {hex_str}")
                            self._live_update('IN', data[:20].encode('latin-1', errors='replace'), f"RAW STR: {len(data)} chars")
                    
                    # Konvertiere zu bytes falls nötig
                    if isinstance(data, str):
                        data = data.encode('latin-1')
                    elif isinstance(data, int):
                        return data
                    
                    # Füge zu Buffer hinzu
                    self.byte_buffer.extend(data)
                    
                    # Gib erstes Byte zurück
                    if len(self.byte_buffer) > 0:
                        byte = self.byte_buffer.pop(0)
                        return byte
            
            time.sleep(0.005)
        
        # Timeout - zeige Poll-Count
        if self.punter_debug and poll_count > 0:
            self.log(f"    [POLL] Timeout after {poll_count} polls (~{poll_count*5}ms polling)")
        return None
    
    def _wait_for_byte(self, expected_byte, timeout=10):
        """
        Wartet auf ein spezifisches Byte
        
        Args:
            expected_byte: Erwartetes Byte (z.B. ACK, NAK, CRC)
            timeout: Timeout in Sekunden
            
        Returns:
            True wenn Byte empfangen, False bei Timeout oder anderem Byte
        """
        end_time = time.time() + timeout
        
        while time.time() < end_time:
            byte = self._read_byte(timeout=0.1)
            if byte is not None:
                if byte == expected_byte:
                    return True
                else:
                    # Unerwartetes Byte - log es aber gib nicht auf
                    self.log(f"    ⚠ Unexpected byte: 0x{byte:02X}, expected 0x{expected_byte:02X}")
                    # Weiter warten falls es nur Noise war
            time.sleep(0.01)
        
        # Timeout
        return False
    
    def _calc_crc(self, data):
        """Berechnet CRC-16 für XModem"""
        crc = 0
        for byte in data:
            crc ^= (byte << 8)
            for _ in range(8):
                if crc & 0x8000:
                    crc = ((crc << 1) ^ 0x1021) & 0xFFFF
                else:
                    crc = (crc << 1) & 0xFFFF
        return crc
    
    # Placeholder für andere Protokolle
    def _ymodem_send(self, filepath, callback):
        """
        YModem Send Implementation
        
        WICHTIG:
        - 1 File: XModem-1K (KEIN Header Block 0)
        - 2+ Files: YModem Batch (MIT Header Blocks + End-of-Batch)
        """
        self.log(f"\n{'='*60}")
        self.log(f"YMODEM SEND START")
        self.log(f"{'='*60}")
        
        import os
        import time
        
        # Buffer clearing VOR Upload
        self.log("Clearing receive buffer before upload...")
        cleared = 0
        try:
            for _ in range(3):
                data = self.connection.get_received_data_raw(4096, timeout=0.1)
                if data:
                    cleared += len(data)
                time.sleep(0.1)
            self.log(f"✓ Buffer cleared: {cleared} bytes removed")
        except Exception as e:
            self.log(f"⚠ Buffer clear failed (non-critical): {e}")
        
        # Liste von Dateien
        if isinstance(filepath, list):
            files = filepath
        else:
            files = [filepath]
        
        total_files = len(files)
        self.log(f"Total files to send: {total_files}")
        
        # Prüfe ob Single-File oder Batch
        is_single_file = (total_files == 1)
        
        if is_single_file:
            self.log("✓ Single file mode: Using XModem-1K (no header)")
        else:
            self.log("✓ Batch mode: Using YModem (with headers)")
        
        # SINGLE FILE: XModem-1K ohne Header
        if is_single_file:
            file_path = files[0]
            
            if not os.path.exists(file_path):
                self.log(f"✗ ERROR: File not found: {file_path}")
                if callback:
                    callback(0, 0, f"Fehler: {file_path} nicht gefunden")
                return False
            
            filename = os.path.basename(file_path)
            filesize = os.path.getsize(file_path)
            
            self.log(f"\nFile: {filename}")
            self.log(f"Size: {filesize} bytes")
            
            # Warte auf 'C' vom Empfänger
            self.log("\nWaiting for receiver 'C'...")
            if not self._wait_for_start_signal(timeout=60):
                self.log("✗ Timeout waiting for 'C'")
                return False
            
            # WICHTIG: Pause nach 'C' damit BBS bereit ist!
            import time
            time.sleep(2.0)
            self.log("(2s pause for BBS to prepare)")
            
            # Sende Datei-Daten (XModem-1K, kein Header!)
            self.log("\nSending file data (XModem-1K)...")
            if not self._ymodem_send_data(file_path, filename, 1, 1, callback):
                self.log("✗ Data send FAILED")
                return False
            
            self.log("✓ File sent successfully")
            
        # BATCH: YModem mit Headers
        else:
            for file_idx, file_path in enumerate(files):
                if not os.path.exists(file_path):
                    self.log(f"✗ ERROR: File not found: {file_path}")
                    if callback:
                        callback(0, 0, f"Fehler: {file_path} nicht gefunden")
                    return False
                
                filename = os.path.basename(file_path)
                filesize = os.path.getsize(file_path)
                
                self.log(f"\n--- File {file_idx + 1}/{total_files} ---")
                self.log(f"Name: {filename}")
                self.log(f"Size: {filesize} bytes")
                
                # Block 0: Header
                self.log("\nPhase 1: Sending YModem header (Block 0)...")
                if not self._ymodem_send_header(filename, filesize, callback):
                    self.log("✗ Header send FAILED")
                    return False
                self.log("✓ Header sent successfully")
                
                # Datei-Daten
                self.log("\nPhase 2: Sending file data...")
                if not self._ymodem_send_data(file_path, filename, file_idx + 1, total_files, callback):
                    self.log("✗ Data send FAILED")
                    return False
                self.log("✓ File data sent successfully")
            
            # End of Batch: Null-Header
            self.log("\n--- End of Batch ---")
            self.log("Sending null header to signal end...")
            if not self._ymodem_send_header("", 0, callback):
                self.log("✗ End-of-batch header FAILED")
                return False
            self.log("✓ End-of-batch header sent")
        
        self.log(f"\n{'='*60}")
        self.log("✓ YMODEM SEND COMPLETED SUCCESSFULLY")
        self.log(f"{'='*60}\n")
        return True
    
    def _wait_for_start_signal(self, timeout=60):
        """
        Wartet auf 'C' (CRC Start) vom Empfänger
        
        Returns:
            True wenn 'C' empfangen
            False bei Timeout oder anderen Problemen
        """
        import time
        
        self.log(f"Waiting for receiver 'C' (timeout: {timeout}s)...")
        start_time = time.time()
        
        while (time.time() - start_time) < timeout:
            byte = self._read_byte(timeout=1)
            
            if byte == CRC:  # 'C' = 0x43
                self.log("✓ Received 'C' from receiver")
                return True
            
            if byte is not None:
                self.log(f"⚠ Unexpected byte: 0x{byte:02X} (expected 'C'=0x43)")
        
        self.log("✗ Timeout waiting for 'C'")
        return False
    
    def _ymodem_send_header(self, filename, filesize, callback):
        """Sendet YModem Block 0 (Filename + Size)"""
        import os
        
        if filename:
            self.log(f"  Preparing header: '{filename}' ({filesize} bytes)")
        else:
            self.log(f"  Preparing null header (end of batch)")
        
        # Warte auf 'C' (CRC Request)
        self.log("  Waiting for CRC request ('C' = 0x43)...")
        if not self._wait_for_byte(CRC, timeout=60):
            self.log("  ✗ ERROR: No CRC request received (timeout)")
            self.log(f"  Expected: 'C' (0x43), got nothing")
            return False
        self.log("  ✓ Received CRC request")
        
        # Block 0 Header erstellen
        if filename:
            # Entferne .prg Extension wenn vorhanden (C64 BBS Kompatibilität)
            clean_filename = filename
            if clean_filename.lower().endswith('.prg'):
                clean_filename = clean_filename[:-4]
                self.log(f"  Cleaned filename: '{filename}' → '{clean_filename}' (removed .prg)")
            
            # Filename + NULL + Filesize + NULL + Rest padding
            header_data = clean_filename.encode('ascii') + b'\x00'
            header_data += str(filesize).encode('ascii') + b'\x00'
            self.log(f"  Header content: '{clean_filename}\\x00{filesize}\\x00'")
        else:
            # Null-Header (End of Batch)
            header_data = b''
            self.log(f"  Header content: (empty)")
        
        # Padding auf 128 Bytes
        header_data = header_data.ljust(128, b'\x00')
        self.log(f"  Header padded to 128 bytes")
        
        # Sende Block 0
        self.log(f"  Sending Block 0...")
        if not self._send_block(0, header_data, SOH, use_crc=True):
            self.log("  ✗ Block 0 send failed")
            return False
        self.log("  ✓ Block 0 sent (ACK received)")
        
        # _send_block wartet bereits auf ACK!
        # Kein zweites ACK-Warten nötig!
        
        if filename:
            # Bei Datei-Header: Warte auf 'C' für Daten
            # WICHTIG: Kann bis zu 14+ Sekunden dauern! (siehe tcpser log)
            self.log("  Waiting for CRC request for data...")
            if not self._wait_for_byte(CRC, timeout=20):
                self.log("  ✗ ERROR: No CRC request for data")
                return False
            self.log("  ✓ Received CRC request (ready for data)")
            
            # WICHTIG: Pause nach 'C' damit BBS bereit ist!
            # tcpser log zeigt: 4+ Sekunden zwischen 'C' und Block 1!
            import time
            time.sleep(2.0)
            self.log("  (2s pause for BBS to prepare)")
        
        return True
    
    def _ymodem_send_data(self, filepath, filename, file_idx, total_files, callback):
        """
        Sendet Datei-Daten mit XModem-1K Protokoll
        
        Args:
            filepath: Voller Pfad der zu sendenden Datei
            filename: Display-Name für Callback
            file_idx: Aktueller File-Index (1-based)
            total_files: Gesamtzahl der Files
            callback: Progress callback
        """
        import os
        
        filesize = os.path.getsize(filepath)
        block_num = 1
        bytes_sent = 0
        
        with open(filepath, 'rb') as f:
            while True:
                # Lies 1024 Bytes
                block_data = f.read(1024)
                
                if not block_data:
                    break  # Dateiende
                
                # Padding wenn < 1024 Bytes
                if len(block_data) < 1024:
                    block_data = block_data.ljust(1024, b'\x1A')  # SUB padding
                
                # Sende Block mit Retry bei NAK
                max_retries = 10
                retry_count = 0
                success = False
                
                while retry_count <= max_retries:
                    if self._send_block(block_num, block_data, STX, use_crc=True):
                        success = True
                        break  # ACK empfangen ✓
                    
                    # NAK empfangen - Retry
                    retry_count += 1
                    if retry_count <= max_retries:
                        self.log(f"  Retry {retry_count}/{max_retries}...")
                        import time
                        time.sleep(0.5)  # Pause vor Retry
                    else:
                        self.log(f"  ✗ Block {block_num} failed after {max_retries} retries")
                
                if not success:
                    return False
                
                # Zähle nur echte File-Bytes (ohne Padding)
                actual_bytes = min(len(block_data), filesize - bytes_sent)
                bytes_sent += actual_bytes
                block_num = (block_num + 1) % 256
                
                if callback:
                    # Zeige File X/Y, filename und Bytes
                    if total_files > 1:
                        status = f"File {file_idx}/{total_files}: {filename} ({bytes_sent}/{filesize} bytes)"
                    else:
                        status = f"{filename} ({bytes_sent}/{filesize} bytes)"
                    callback(bytes_sent, filesize, status)
        
        # EOT senden
        self.log("Sende EOT...")
        self.send_raw(bytes([EOT]))
        
        # Warte auf ACK (erstes)
        if not self._wait_for_byte(ACK, timeout=10):
            self.log("ERROR: Kein erstes ACK nach EOT")
            return False
        self.log("✓ Erstes ACK nach EOT empfangen")
        
        # Warte auf zweites ACK
        # Log zeigt: BBS sendet 2x ACK nach EOT!
        if not self._wait_for_byte(ACK, timeout=2):
            self.log("⚠ Warning: Kein zweites ACK (non-critical)")
            # Nicht critical - manche BBS senden nur 1x ACK
        else:
            self.log("✓ Zweites ACK nach EOT empfangen")
        
        return True
    
    def _ymodem_receive(self, filepath, callback):
        """
        YModem Receive Implementation basierend auf funktionierendem tcpser Log
        Timing ist kritisch! Folgt dem Pattern aus dem Log exakt.
        """
        self.log(f"\n{'='*60}")
        self.log(f"YMODEM RECEIVE START")
        self.log(f"Target: {filepath}")
        self.log(f"{'='*60}")
        
        import os
        import time
        
        # Bestimme Zielverzeichnis
        if os.path.isdir(filepath):
            target_dir = filepath
        else:
            target_dir = os.path.dirname(filepath) or "."
        
        files_received = []
        file_count = 0
        
        while True:
            file_count += 1
            self.log(f"\n--- File #{file_count} ---")
            
            if file_count == 1:
                # FILE 1: Sende beide 'C'
                self.log("Sending first 'C' for header...")
                self.send_raw(bytes([CRC]))
                
                self.log("Waiting 8.5s...")
                time.sleep(8.5)
                
                self.log("Sending second 'C' for header...")
                self.send_raw(bytes([CRC]))
            else:
                # FILE 2+: ACK für EOT wurde schon gesendet
                # Pattern: ACK → 0.6s → 'C' → 8.5s → 'C'
                self.log("(ACK for EOT already sent by data receive)")
                self.log("Waiting 0.6s after ACK...")
                time.sleep(0.6)
                
                self.log("Sending first 'C' for header...")
                self.send_raw(bytes([CRC]))
                
                self.log("Waiting 8.5s...")
                time.sleep(8.5)
                
                self.log("Sending second 'C' for header...")
                self.send_raw(bytes([CRC]))
            
            # Warte auf Header Block
            self.log("Waiting for header block...")
            header_result = self._ymodem_receive_header_with_timeout(timeout=60)
            
            if header_result is None:
                self.log("✗ ERROR: No header received")
                # Bei File 2+ könnte das bedeuten: Batch ist fertig
                if file_count > 1:
                    self.log("  (Batch may be complete - no more files)")
                    break
                return False
            
            filename, filesize = header_result
            self.log(f"✓ Header received: '{filename}' ({filesize} bytes)")
            
            # Null-Header = End of Batch
            if not filename:
                self.log("✓ End-of-Batch (no more files)")
                # Sende ACK für End-of-Batch
                # (EOT oder NULL-Header, je nach BBS)
                self.send_raw(bytes([ACK]))
                break
            
            # PATTERN: ACK, ACK, wait 2.7s, ACK+C
            self.log("Sending ACK #1...")
            self.send_raw(bytes([ACK]))
            time.sleep(0.1)
            
            self.log("Sending ACK #2 (double-ACK)...")
            self.send_raw(bytes([ACK]))
            
            self.log("Waiting 2.7s...")
            time.sleep(2.7)
            
            self.log("Sending ACK #3 + 'C'...")
            self.send_raw(bytes([ACK, CRC]))
            
            # Zieldatei - bereinige Filename!
            # BBS könnte illegale Zeichen senden: / \ : * ? " < > |
            safe_filename = filename
            for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
                safe_filename = safe_filename.replace(char, '-')
            
            # Entferne auch führende/trailing spaces und dots
            safe_filename = safe_filename.strip('. ')
            
            if safe_filename != filename:
                self.log(f"⚠ Filename sanitized: '{filename}' → '{safe_filename}'")
            
            if not safe_filename:
                safe_filename = "download.dat"
            
            # Prüfe ob Extension vorhanden ist
            # Wenn nicht: füge .prg hinzu
            _, ext = os.path.splitext(safe_filename)
            if not ext:
                safe_filename += ".prg"
                self.log(f"⚠ No extension found, adding .prg: '{safe_filename}'")
                self.log(f"⚠ Filename was empty after sanitization, using: {safe_filename}")
            
            target_file = os.path.join(target_dir, safe_filename)
            self.log(f"Saving to: {target_file}")
            
            # Empfange Daten
            if not self._ymodem_receive_data_slow(target_file, filesize, safe_filename, file_count, callback):
                self.log("✗ Data receive FAILED")
                return False
            
            files_received.append(safe_filename)
            self.log(f"✓ File {file_count} completed: {filename}")
        
        self.log(f"\n✓ YMODEM RECEIVE COMPLETED")
        self.log(f"  Files received: {len(files_received)}")
        return True
    
    def _ymodem_receive_header(self):
        """
        Empfängt YModem Block 0 (Filename + Size)
        
        Returns:
            (filename, filesize) oder None bei Fehler
            ("", 0) bei End-of-Batch (EOT empfangen)
        """
        # Warte auf SOH (Start of Header) oder EOT (End-of-Batch)
        first_byte = self._read_byte(timeout=1)
        
        if first_byte is None:
            return None
        
        # EOT = End-of-Batch (keine weiteren Files)
        if first_byte == EOT:
            self.log(f"✓ Received EOT (0x04) = End-of-Batch")
            return ("", 0)
        
        if first_byte != SOH:
            self.log(f"ERROR: Erwartete SOH (0x01) oder EOT (0x04), bekam 0x{first_byte:02x}")
            return None
        
        # Empfange Block 0
        block_result = self._receive_block(0, 128, use_crc=True)
        
        if not block_result:
            return None
        
        block_data = block_result
        
        # Parse Header
        # Format: "filename\0filesize\0..."
        null_idx = block_data.find(b'\x00')
        
        if null_idx == 0:
            # Null-Header (End of Batch)
            return ("", 0)
        
        if null_idx == -1:
            self.log("ERROR: Ungültiger Header (kein NULL)")
            return None
        
        filename = block_data[:null_idx].decode('ascii', errors='ignore')
        
        # Filesize extrahieren
        rest = block_data[null_idx + 1:]
        size_end = rest.find(b'\x00')
        
        if size_end == -1:
            size_end = rest.find(b' ')
        
        if size_end > 0:
            try:
                filesize = int(rest[:size_end].decode('ascii'))
            except:
                filesize = 0
        else:
            filesize = 0
        
        self.log(f"Header empfangen: '{filename}' ({filesize} bytes)")
        
        return (filename, filesize)
    
    def _ymodem_receive_header_with_timeout(self, timeout=60):
        """
        Empfängt YModem Block 0 mit längerem Timeout
        Basierend auf funktionierendem tcpser Log Pattern
        
        Returns:
            (filename, filesize) oder None bei Fehler/Timeout
        """
        import time
        end_time = time.time() + timeout
        
        while time.time() < end_time:
            result = self._ymodem_receive_header()
            if result is not None:
                return result
            time.sleep(0.01)
        
        return None
    
    def _ymodem_receive_data_slow(self, filepath, filesize, filename, file_idx, callback):
        """
        Empfängt Datei-Daten mit LANGSAMEN ACKs
        Basierend auf tcpser Log: 3.5s Pause VOR jedem ACK!
        
        Args:
            filepath: Voller Pfad wo File gespeichert wird
            filesize: Erwartete Dateigröße
            filename: Display-Name für Callback
            file_idx: Aktueller File-Index (1-based)
            callback: Progress callback
        """
        import time
        
        block_num = 1
        bytes_received = 0
        
        with open(filepath, 'wb') as f:
            while bytes_received < filesize:
                # Empfange Block (STX für 1024-byte blocks)
                self.log(f"Waiting for Block {block_num}...")
                
                # Warte auf STX
                stx = self._read_byte(timeout=10)
                if stx is None:
                    self.log(f"✗ Timeout waiting for Block {block_num}")
                    return False
                
                if stx != STX:
                    self.log(f"✗ Expected STX (0x02), got 0x{stx:02X}")
                    return False
                
                # Lies Block#
                blk = self._read_byte(timeout=1)
                blk_comp = self._read_byte(timeout=1)
                
                if blk is None or blk_comp is None:
                    self.log(f"✗ Error reading block numbers")
                    return False
                
                if blk != block_num % 256 or blk != (255 - blk_comp):
                    self.log(f"✗ Block number mismatch")
                    return False
                
                # Lies 1024 bytes Data
                block_data = bytearray()
                for _ in range(1024):
                    byte = self._read_byte(timeout=1)
                    if byte is None:
                        self.log(f"✗ Error reading block data")
                        return False
                    block_data.append(byte)
                
                # Lies CRC (2 bytes)
                crc_high = self._read_byte(timeout=1)
                crc_low = self._read_byte(timeout=1)
                
                if crc_high is None or crc_low is None:
                    self.log(f"✗ Error reading CRC")
                    return False
                
                received_crc = (crc_high << 8) | crc_low
                
                # Berechne CRC
                calculated_crc = self._calc_crc(block_data)
                
                if received_crc != calculated_crc:
                    self.log(f"✗ CRC mismatch: got 0x{received_crc:04X}, expected 0x{calculated_crc:04X}")
                    # NAK senden
                    self.send_raw(bytes([NAK]))
                    continue
                
                # CRC OK - schreibe Daten
                to_write = min(1024, filesize - bytes_received)
                f.write(block_data[:to_write])
                bytes_received += to_write
                block_num += 1
                
                # WICHTIG: 0.5s Pause VOR ACK
                self.log(f"Block {block_num-1} OK, waiting 0.5s before ACK...")
                time.sleep(0.5)
                
                # Sende ACK
                self.send_raw(bytes([ACK]))
                self.log(f"ACK sent for Block {block_num-1}")
                
                # Kurze Pause nach ACK
                # Gibt BBS Zeit für nächsten Block (wichtig bei lokaler Verbindung)
                time.sleep(self.inter_block_delay)
                
                if callback:
                    # Zeige File #, filename und Bytes
                    if file_idx > 1:
                        # Bei Multi-File: Zeige File #
                        status = f"File {file_idx}: {filename} ({bytes_received}/{filesize} bytes)"
                    else:
                        # Bei Single-File: Kein "File 1"
                        status = f"{filename} ({bytes_received}/{filesize} bytes)"
                    callback(bytes_received, filesize, status)
        
        # WICHTIG: Nach dem letzten Block kommt noch EOT!
        self.log("All bytes received, waiting for EOT...")
        eot = self._read_byte(timeout=10)
        
        if eot != EOT:
            self.log(f"✗ Expected EOT (0x04), got 0x{format(eot, '02X') if eot else 'None'}")
            return False
        
        self.log("✓ EOT received (end of file)")
        
        # Pattern aus Log:
        # EOT empfangen → 2.2s wait → ACK
        self.log("Waiting 2.2s before ACK for EOT...")
        time.sleep(2.2)
        
        self.send_raw(bytes([ACK]))
        self.log("ACK sent for EOT")
        
        return True
    
    def _ymodem_receive_data(self, filepath, filesize, callback):
        """Empfängt Datei-Daten mit XModem-1K Protokoll"""
        # Sende 'C' für Daten
        self.send_raw(bytes([CRC]))
        
        block_num = 1
        bytes_received = 0
        
        # Bei filesize=0: Unbekannte Größe (XModem Fallback)
        unknown_size = (filesize == 0)
        
        with open(filepath, 'wb') as f:
            while True:
                # Empfange Block
                block_data = self._receive_block(block_num, 1024, use_crc=True)
                
                if block_data is False:
                    # EOT empfangen
                    break
                
                if not block_data:
                    self.log("ERROR: Block-Empfang fehlgeschlagen")
                    return False
                
                # Schreibe Daten
                if unknown_size:
                    # Bei unbekannter Größe: Schreibe alles (Padding wird später entfernt)
                    f.write(block_data)
                    bytes_received += len(block_data)
                else:
                    # Bei bekannter Größe: Ohne Padding am Ende
                    write_len = min(len(block_data), filesize - bytes_received)
                    f.write(block_data[:write_len])
                    bytes_received += write_len
                
                block_num = (block_num + 1) % 256
                
                # Sende ACK
                self.send_raw(bytes([ACK]))
                
                if callback:
                    if unknown_size:
                        status = f"Block {block_num - 1} empfangen ({bytes_received} bytes)"
                        callback(bytes_received, 0, status)  # 3 Parameter!
                    else:
                        status = f"Block {block_num - 1} empfangen ({bytes_received}/{filesize} bytes)"
                        callback(bytes_received, filesize, status)  # 3 Parameter!
                
                # Datei komplett? (nur bei bekannter Größe)
                if not unknown_size and bytes_received >= filesize:
                    break
        
        # Bei unbekannter Größe: Entferne SUB-Padding (0x1A) am Ende
        if unknown_size:
            import os
            # Lese Datei
            with open(filepath, 'rb') as f:
                data = f.read()
            # Entferne trailing 0x1A
            while data and data[-1] == 0x1A:
                data = data[:-1]
            # Schreibe zurück
            with open(filepath, 'wb') as f:
                f.write(data)
            self.log(f"Padding entfernt - finale Größe: {len(data)} bytes")
        
        # Warte auf EOT
        self.log("Warte auf EOT...")
        if not self._wait_for_byte(EOT, timeout=10):
            self.log("WARNING: Kein EOT empfangen")
        
        # ACK für EOT
        self.send_raw(bytes([ACK]))
        
        return True
    
    def _zmodem_send(self, filepath, callback):
        """ZModem Send - Native Implementation"""
        from zmodem import ZModemTransfer
        zm = ZModemTransfer(self)
        return zm.send(filepath, callback)
    
    def _zmodem_receive(self, filepath, callback):
        """ZModem Receive - Native Implementation"""
        from zmodem import ZModemTransfer
        zm = ZModemTransfer(self)
        # filepath ist bei ZModem ein Verzeichnis (Dateiname kommt vom Sender)
        save_dir = filepath if os.path.isdir(filepath) else os.path.dirname(filepath)
        return zm.receive(save_dir, callback)
    
    # ==================================================================================
    # PUNTER C1 PROTOCOL IMPLEMENTATION
    # Based on: https://www.pagetable.com/?p=1663
    # ==================================================================================
    
    # Punter Handshake Codes (3-byte ASCII)
    PUNTER_GOO = b'GOO'  # Ready / Block OK
    PUNTER_BAD = b'BAD'  # Block error, resend
    PUNTER_ACK = b'ACK'  # Acknowledge
    PUNTER_SB  = b'S/B'  # Send Block
    PUNTER_SYN = b'SYN'  # Sync
    
    def _punter_calc_checksums(self, data):
        """
        Berechnet Punter Checksums über data (ab Header Offset 4)
        
        Returns:
            (additive_checksum, cyclic_checksum) - beide 16-bit
        """
        # Additive Checksum: Summe aller Bytes
        additive = sum(data) & 0xFFFF
        
        # Cyclic Checksum: XOR mit 16-bit Links-Rotation nach jedem Byte
        cyclic = 0
        for byte in data:
            cyclic ^= byte
            # 16-bit rotate left
            cyclic = ((cyclic << 1) | (cyclic >> 15)) & 0xFFFF
        
        return additive, cyclic
    
    def _punter_make_block(self, payload, next_block_size, block_index):
        """
        Erstellt einen Punter Block mit Header
        
        Args:
            payload: Nutzdaten (bytes)
            next_block_size: Größe des nächsten Blocks (0-255)
            block_index: Block-Index (0xFFFF für letzten Block)
        
        Returns:
            Kompletter Block mit Header (bytes)
        """
        # Header ohne Checksums erstellen (ab Offset 4)
        header_rest = bytes([
            next_block_size & 0xFF,           # Offset 4: next block size
            block_index & 0xFF,               # Offset 5: block index low
            (block_index >> 8) & 0xFF         # Offset 6: block index high
        ])
        
        # Daten für Checksum-Berechnung (Header ab Offset 4 + Payload)
        checksum_data = header_rest + payload
        
        # Checksums berechnen
        additive, cyclic = self._punter_calc_checksums(checksum_data)
        
        self.punter_log(f"    [CHECKSUM] data_len={len(checksum_data)}, add={additive:04X}, cyc={cyclic:04X}")
        
        # Kompletten Block zusammenbauen
        block = bytes([
            additive & 0xFF,                  # Offset 0: additive low
            (additive >> 8) & 0xFF,           # Offset 1: additive high
            cyclic & 0xFF,                    # Offset 2: cyclic low
            (cyclic >> 8) & 0xFF              # Offset 3: cyclic high
        ]) + header_rest + payload
        
        return block
    
    def _punter_send_code(self, code):
        """Sendet einen Punter Code"""
        hex_str = ' '.join(f'{b:02X}' for b in code)
        ascii_str = code.decode('ascii', errors='replace')
        self.punter_log(f"    [OUT] {hex_str} |{ascii_str}|")
        self._live_update('OUT', code, ascii_str)
        self.send_raw(code)
    
    def _punter_send_block(self, block):
        """Sendet einen Punter Block"""
        hex_preview = ' '.join(f'{b:02X}' for b in block[:20])
        self.punter_log(f"    [OUT] Block ({len(block)} bytes): {hex_preview}...")
        self._live_update('OUT', block[:20], f"Block ({len(block)} bytes)")
        self.send_raw(block)
    
    # -- Punter C1 upload core: timeout-free sender primitives --
    # The sender side is reactive (wait code -> reply), so "no timeouts"
    # means: wait indefinitely, retry resends indefinitely. Loops exit only
    # on cancel/disconnect. Duplicate codes from peer stall-resends slide
    # through the 3-byte window harmlessly.

    def _pc1_wait_code(self, expected):
        """Wait indefinitely for one of the 3-byte codes (sliding window).

        Returns the matched code, or None on cancel/disconnect.
        """
        if isinstance(expected, bytes):
            expected = [expected]
        codes_str = ', '.join(c.decode('ascii', errors='replace') for c in expected)
        self._live_update('WAIT', None, f"Waiting for: {codes_str}")
        self.waiting_for_input = True
        self.waiting_for_codes = list(expected)
        window = bytearray()
        try:
            while not self._pc1_gone():
                b = self._pc1_get_byte(0.5)
                if b is None:
                    continue
                window.append(b)
                self._live_update('IN', bytes([b]), f"byte: 0x{b:02X}")
                if len(window) > 3:
                    window = window[-3:]
                if len(window) == 3:
                    for code in expected:
                        if bytes(window) == bytes(code):
                            self.punter_log(f"    [IN] matched {bytes(code)}")
                            self._live_update('IN', bytes(code),
                                              f"MATCHED: {bytes(code).decode('ascii', errors='replace')}")
                            return bytes(code)
            return None
        finally:
            self.waiting_for_input = False

    def _pc1_send_block_and_confirm(self, block, what):
        """Send a block; GOO confirms, BAD triggers ACK + resend on fresh S/B.

        Every GOO is answered with ACK (the peer's GOO/ACK handshake needs
        it). Returns True once confirmed, False on cancel/disconnect.
        """
        while not self._pc1_gone():
            self._punter_send_block(block)
            self.log(f"Sent {what} ({len(block)} bytes)")
            r = self._pc1_wait_code([self.PUNTER_GOO, self.PUNTER_BAD])
            if r is None:
                return False
            if r == self.PUNTER_GOO:
                self._punter_send_code(self.PUNTER_ACK)
                return True
            self.log(f"    BAD for {what} - ACK, wait S/B, resend")
            self._punter_send_code(self.PUNTER_ACK)
            if self._pc1_wait_code([self.PUNTER_SB]) is None:
                return False
        return False

    def _pc1_send_one(self, file_data, ftype, callback=None, label="file"):
        """One C1 file, sender side (spec phases A+B). No header/end marker.

        ftype: 0 = PRG, 1 = SEQ. Extra GOO/ACK rounds (which receivers like
        ours insert around the type block) are answered, so single- and
        double-round receivers both work.
        """
        if not file_data:
            self.log("ERROR: Refusing to send empty file (not representable in C1)")
            return False
        filesize = len(file_data)

        # Phase A: file type
        if self._pc1_wait_code([self.PUNTER_GOO]) is None:
            return False
        self._punter_send_code(self.PUNTER_ACK)
        # S/B advances; a stray GOO is an extra handshake round -> ACK it.
        while True:
            r = self._pc1_wait_code([self.PUNTER_SB, self.PUNTER_GOO])
            if r is None:
                return False
            if r == self.PUNTER_SB:
                break
            self._punter_send_code(self.PUNTER_ACK)
        type_block = self._punter_make_block(bytes([ftype & 0xFF]), 0xC9, 0xFFFF)
        if not self._pc1_send_block_and_confirm(type_block, "type block"):
            return False
        # Same extra-round tolerance before end-off A.
        while True:
            r = self._pc1_wait_code([self.PUNTER_SB, self.PUNTER_GOO])
            if r is None:
                return False
            if r == self.PUNTER_SB:
                break
            self._punter_send_code(self.PUNTER_ACK)
        # End-off A: S/B -> SYN, SYN -> 3x S/B (spec sender behavior).
        self._punter_send_code(self.PUNTER_SYN)
        if self._pc1_wait_code([self.PUNTER_SYN]) is None:
            return False
        for i in range(3):
            if self._pc1_gone():
                return False
            self._punter_send_code(self.PUNTER_SB)
            if i < 2:
                time.sleep(0.3)  # pacing between repeats, not a timeout

        # Phase B: receiver opens with GOO/ACK, then S/B for the header block.
        if self._pc1_wait_code([self.PUNTER_GOO]) is None:
            return False
        self._punter_send_code(self.PUNTER_ACK)
        if self._pc1_wait_code([self.PUNTER_SB]) is None:
            return False
        chunks = [file_data[i:i + 248] for i in range(0, filesize, 248)]
        header = self._punter_make_block(b'', len(chunks[0]) + 7, 0x0000)
        if not self._pc1_send_block_and_confirm(header, "header block"):
            return False

        # Data blocks.
        idx = 1
        pos = 0
        for n, chunk in enumerate(chunks):
            if self._pc1_gone():
                return False
            if self._pc1_wait_code([self.PUNTER_SB]) is None:
                return False
            last = (n == len(chunks) - 1)
            if last:
                blk = self._punter_make_block(bytes(chunk), len(chunk) + 7, 0xFFFF)
            else:
                remaining = filesize - pos - len(chunk)
                nxt = 255 if remaining >= 248 else remaining + 7
                blk = self._punter_make_block(bytes(chunk), nxt, idx)
            if not self._pc1_send_block_and_confirm(blk, f"datablock {idx}"):
                return False
            pos += len(chunk)
            idx += 1
            self.log(f"Datablock {idx - 1}: {len(chunk)} bytes, total {pos}/{filesize}")
            if callback:
                callback(pos, filesize, f"{label}: Block {idx - 1}")

        # End-off B.
        if self._pc1_wait_code([self.PUNTER_SB]) is None:
            return False
        self._punter_send_code(self.PUNTER_SYN)
        if self._pc1_wait_code([self.PUNTER_SYN]) is None:
            return False
        for i in range(3):
            if self._pc1_gone():
                return False
            self._punter_send_code(self.PUNTER_SB)
            if i < 2:
                time.sleep(0.3)  # pacing between repeats, not a timeout

        self.log(f"✓ Sent {filesize} bytes")
        if callback:
            callback(filesize, filesize, f"{label}: Complete!")
        return True

    def _punter_send(self, filepath, callback=None):
        """
        Punter C1 Send - single file, no header (the BBS already knows the
        name). Timeout-free: waits as long as the receiver needs.
        """
        import os

        self.log(f"\n{'='*60}")
        self.log(f"PUNTER C1 SEND (Single - No Header): {filepath}")
        self.log(f"{'='*60}")

        self._pc1_stash = bytearray()
        try:
            with open(filepath, 'rb') as f:
                file_data = f.read()
        except OSError as e:
            self.log(f"ERROR: Cannot read {filepath}: {e}")
            return False

        self.log(f"File size: {len(file_data)} bytes")
        ok = self._pc1_send_one(file_data, self._punter_ftype_of(filepath),
                                callback, os.path.basename(filepath))
        if ok:
            self.log(f"\n✓ PUNTER SEND COMPLETE: {filepath}")
        return ok

    @staticmethod
    def _punter_ftype_of(filepath):
        """0 = PRG, 1 = SEQ, by file extension."""
        import os
        ext = os.path.splitext(filepath)[1].lower()
        return 1 if ext in ['.seq', '.txt', '.s'] else 0
    
    def _punter_receive(self, filepath, callback=None):
        """
        Punter C1 Receive - single/multi autodetect, no timeout dependency.

        Protocol: https://www.pagetable.com/?p=1663 (receiver logic ported
        from CGTerm punter.c: every step is send-code-until-expected-reply).
        Per file (receiver side): GOO/ACK, 8-byte type block, GOO/ACK,
        S/B/SYN, SYN/S/B, GOO/ACK, 7-byte header block, data blocks,
        S/B/SYN, SYN/S/B. Multi-Punter wraps each file in
        16xTAB + FILENAME,P|S + CR ... 16xTAB + 16xEOT + CR (END marker).

        The mode is autodetected from the byte stream. Nothing aborts on
        silence: short internal windows only trigger re-sends, so laggy BBS
        just transfer slower. Stop via cancel.
        """
        import os

        self.log(f"\n{'='*60}")
        self.log(f"PUNTER C1 RECEIVE: {filepath}")
        self.log(f"{'='*60}")

        self.log(f"Connection type: {type(self.connection)}")
        if hasattr(self.connection, 'connected'):
            self.log(f"Connection connected: {self.connection.connected}")

        # Fresh per-transfer state for the C1 core
        self._pc1_stash = bytearray()
        self._pc1_last_index = 0
        self._pc1_last_payload = b''

        # Collect bytes already queued (e.g. header sent before F3 was hit)
        if hasattr(self.connection, 'receive_queue'):
            queued = 0
            while not self.connection.receive_queue.empty():
                try:
                    data = self.connection.receive_queue.get_nowait()
                except Exception:
                    break
                if data:
                    if isinstance(data, str):
                        data = data.encode('latin-1')
                    self.byte_buffer.extend(data)
                    queued += 1
            self.log(f"    Collected {queued} queued chunk(s), buffer={len(self.byte_buffer)} bytes")

        target_dir = filepath if os.path.isdir(filepath) else (os.path.dirname(filepath) or '.')
        received = []

        try:
            while True:
                if self._pc1_gone():
                    break

                start = self._pc1_wait_start()
                if start is None:  # cancel / disconnect
                    break
                if start[0] == 'end':
                    self.log(f"End marker - transfer complete ({len(received)} file(s))")
                    break

                if start[0] == 'header':
                    _, filename, ftype = start
                    self.log(f"Received header: {filename},{ftype}")
                    safe = filename
                    for ch in '/\\:*?"<>|':
                        safe = safe.replace(ch, '-')
                    ext = '.seq' if ftype.upper() == 'S' else '.prg'
                    if '.' not in safe:
                        safe = safe + ext

                    if not safe.endswith(ext):
                        safe = safe + ext
                        
                    # for correctness, if it is a starzip file remove the extension
                    if safe[0].isnumeric() and safe[1] == '!' and safe.endswith(ext):
                        safe = safe[:-len(ext)]

                    safe = safe.lower()
                    current = os.path.join(target_dir, safe) if os.path.isdir(filepath) else filepath
                    single_mode = False
                    opened = False
                elif start[0] == 'transfer-open':
                    # Our probe GOO was answered with ACK: the opening
                    # handshake is done, go straight to the type block.
                    self.log("Sender handshake - single file without header")
                    if os.path.isdir(filepath):
                        current = os.path.join(filepath, f"download_{int(time.time())}.prg")
                    else:
                        current = filepath
                    single_mode = True
                    opened = True
                else:  # unprompted GOO from sender: full flow incl. opening
                    self.log("Sender GOO - single file without header")
                    if os.path.isdir(filepath):
                        current = os.path.join(filepath, f"download_{int(time.time())}.prg")
                    else:
                        current = filepath
                    single_mode = True
                    opened = False

                if callback:
                    callback(0, 0, "Empfange Datei...")
                if self._pc1_receive_one(current, callback, opened=opened):
                    received.append(current)
                    self.log(f"\n✓ File {len(received)} received: {current}")
                else:
                    self.log(f"ERROR receiving file -> {current}")
                    if self._pc1_gone():
                        break
                    # Garbled start or slow BBS: back to sniffing, the probe
                    # loop re-syncs instead of giving up.
                    time.sleep(0.2)
                    continue

                if single_mode:
                    break  # single file done - no probing, no waiting
                time.sleep(0.1)

            if received:
                self.log(f"\n✓ PUNTER RECEIVE COMPLETE: {len(received)} file(s)")
                return True
            return False
        except Exception as e:
            self.log(f"ERROR: {str(e)}")
            import traceback
            self.log(traceback.format_exc())
            return len(received) > 0

    # -- Punter C1 download core: timeout-free primitives (CGTerm logic) --
    # Short internal windows below only trigger re-sends, never aborts, so
    # laggy links just transfer slower. Anything returning None/False means
    # cancel or disconnect - the only two ways these loops stop.
    _PC1_RESEND = 3.0   # stall window that triggers a re-send, never an abort

    def _pc1_gone(self):
        """True on user cancel or closed connection."""
        return self.cancel_requested or getattr(self.connection, 'connected', True) is False

    def _pc1_get_byte(self, window):
        """One stream byte within `window` seconds, else None. Never fails."""
        end = time.time() + window
        while time.time() < end:
            if self.cancel_requested:
                return None
            if len(self._pc1_stash) > 0:
                return self._pc1_stash.pop(0)
            if len(self.byte_buffer) > 0:
                return self.byte_buffer.pop(0)
            data = None
            if hasattr(self.connection, 'get_received_data'):
                try:
                    data = self.connection.get_received_data(timeout=0.05)
                except Exception:
                    data = None
            if data:
                if isinstance(data, str):
                    data = data.encode('latin-1')
                if len(data) == 1:
                    return data[0]
                self.byte_buffer.extend(data[1:])
                return data[0]
            if getattr(self.connection, 'connected', True) is False:
                return None
            time.sleep(0.005)
        return None

    def _pc1_recv_string(self, send):
        """Send `send`, collect 3 bytes; re-send on stall. None if gone."""
        self._punter_send_code(send)
        buf = bytearray()
        stall = time.time()
        while len(buf) < 3:
            if self._pc1_gone():
                return None
            b = self._pc1_get_byte(0.2)
            if b is not None:
                buf.append(b)
                stall = time.time()
            elif time.time() - stall >= self._PC1_RESEND:
                self._punter_send_code(send)
                stall = time.time()
        return bytes(buf)

    def _pc1_handshake(self, send, expect):
        """Send `send` until `expect` arrives (CGTerm punter_handshake)."""
        while not self._pc1_gone():
            r = self._pc1_recv_string(send)
            if r is None:
                return False
            if r == expect:
                return True
            if expect == self.PUNTER_ACK and r in (b'CKA', b'KAC'):
                # Misaligned ACK (see CGTerm): swallow rest, accept it
                self._pc1_get_byte(0.1)
                if r == b'CKA':
                    self._pc1_get_byte(0.1)
                return True
            self.punter_log(f"    [HANDSHAKE] want {expect} got {r} -> retry")
        return False

    def _pc1_recv_block(self, length, data_out=None):
        """Receive exactly `length` bytes (CGTerm punter_recv_block port).

        Returns next block size, or -1 on cancel/disconnect/remote-cancel.
        Payload (if length > 8) is appended to data_out.
        """
        while not self._pc1_gone():
            self._punter_send_code(self.PUNTER_SB)
            block = bytearray()
            stall = time.time()
            restart = False
            while len(block) < length:
                if self._pc1_gone():
                    return -1
                b = self._pc1_get_byte(0.2)
                if b is None:
                    if time.time() - stall >= 5.0:
                        self.punter_log("    [BLOCK] stall -> BAD/ACK restart")
                        if not self._pc1_handshake(self.PUNTER_BAD, self.PUNTER_ACK):
                            return -1
                        restart = True
                        break
                    continue
                stall = time.time()
                block.append(b)
                n = len(block)
                if n == 3 and bytes(block) == b'S/B':
                    self.log("    Remote cancelled transfer (S/B)")
                    return -1
                if n == 4 and bytes(block[:3]) == b'ACK':
                    if block[3] == 0x41:  # overlapping 'A'CKA: restart block
                        restart = True
                        break
                    block = block[3:]  # late ACK: skip it, keep trailing byte
                elif n == 8 and bytes(block[2:8]) in (b'ACKACK', b'CKACKA', b'KACKAC'):
                    restart = True  # lost sync: restart block
                    break
            if restart:
                continue
            add = block[0] | (block[1] << 8)
            cyc = block[2] | (block[3] << 8)
            calc_add, calc_cyc = self._punter_calc_checksums(bytes(block[4:]))
            if calc_add != add or calc_cyc != cyc:
                self.log(f"    CHECKSUM ERROR (got {add:04X}/{cyc:04X} "
                         f"calc {calc_add:04X}/{calc_cyc:04X}) -> BAD")
                if not self._pc1_handshake(self.PUNTER_BAD, self.PUNTER_ACK):
                    return -1
                continue
            self._pc1_last_index = block[5] | (block[6] << 8)
            self._pc1_last_payload = bytes(block[7:])
            self.punter_log(f"    [IN] block len={len(block)} next={block[4]} "
                            f"idx={self._pc1_last_index:04X}")
            if not self._pc1_handshake(self.PUNTER_GOO, self.PUNTER_ACK):
                return -1
            if data_out is not None:
                # Data block (type/header blocks pass no buffer). Note: a
                # data block may be only 8 bytes (1 payload byte), so this
                # must not depend on the block length.
                data_out.extend(block[7:])
            return block[4]
        return -1

    def _pc1_receive_one(self, filepath, callback=None, opened=False):
        """One C1 file transfer, receiver side (spec phases A+B).

        If opened is True, the opening GOO/ACK handshake was already
        completed by the sniffing probe - go straight to the type block.
        """
        import os
        display = os.path.basename(filepath) if filepath else "download"
        # Phase A: file type
        if not opened:
            if not self._pc1_handshake(self.PUNTER_GOO, self.PUNTER_ACK):
                return False
        if self._pc1_recv_block(8) < 0:
            return False
        if not self._pc1_handshake(self.PUNTER_GOO, self.PUNTER_ACK):
            return False
        if not self._pc1_handshake(self.PUNTER_SB, self.PUNTER_SYN):
            return False
        if not self._pc1_handshake(self.PUNTER_SYN, self.PUNTER_SB):
            return False
        if not self._pc1_handshake(self.PUNTER_GOO, self.PUNTER_ACK):
            return False
        # Phase B: file data (7-byte header block carries first next-size)
        nxt = self._pc1_recv_block(7)
        if nxt < 0:
            return False
        data = bytearray()
        blocks = 0
        while self._pc1_last_index < 0xFF00 and nxt >= 7:
            if self._pc1_gone():
                return False
            nxt = self._pc1_recv_block(nxt, data)
            if nxt < 0:
                return False
            blocks += 1
            self.log(f"Datablock {blocks}: total {len(data)}")
            if callback:
                callback(len(data), 0, f"{display}: Block {blocks}")
        # End-off B (sender emits 3x S/B here - drain the repeats)
        if self._pc1_handshake(self.PUNTER_SB, self.PUNTER_SYN):
            self._pc1_handshake(self.PUNTER_SYN, self.PUNTER_SB)
            self._pc1_drain_sb()
        else:
            self.log("Done, but closing handshake timed out")
        if len(data) == 0:
            self.log("ERROR: No data received")
            return False
        with open(filepath, 'wb') as f:
            f.write(data)
        self.log(f"✓ Received {len(data)} bytes -> {filepath}")
        if callback:
            callback(len(data), len(data), f"FILE_COMPLETE:{display}:{blocks}:{len(data)}")
            callback(len(data), len(data), f"{display}: Complete!")
        return True

    def _pc1_drain_sb(self):
        """Discard the repeated S/B the sender emits at end-off (spec bug);
        anything else is stashed for the next file sniffing."""
        idle_end = time.time() + 1.5
        hard_end = time.time() + 4.0
        tail = bytearray()
        while time.time() < min(idle_end, hard_end):
            if self._pc1_gone():
                break
            b = self._pc1_get_byte(0.2)
            if b is None:
                continue
            tail.append(b)
            if len(tail) >= 3 and bytes(tail[-3:]) == b'S/B':
                tail.clear()  # one repeat swallowed - more may follow (~1s apart)
                idle_end = time.time() + 1.5
            elif len(tail) > 6:
                break  # not S/B repeats - keep for sniffing
        if tail:
            self._pc1_stash.extend(tail)

    def _pc1_parse_header(self, hbuf):
        """Parse a TAB..CR line. Returns ('header', name, type)/('end',)/None."""
        if not hbuf:
            return None
        eots = sum(1 for b in hbuf if b == 0x04)
        text = bytes(b for b in hbuf if b != 0x04)
        try:
            s = text.decode('latin-1')
        except Exception:
            return None
        if eots >= 10 or (eots >= 1 and not any(c.isalnum() for c in s)):
            self.log("    END marker detected")
            return ('end',)
        if ',' in s:
            name, _, typ = s.rpartition(',')
            name = name.strip(' \x04')
            typ = (typ.strip(' \x00\x04')[:1].upper() or 'P')
            if any(c.isalnum() for c in name):
                return ('header', name, typ if typ in ('P', 'S') else 'P')
            return None
        # Header present but no ",P"/",S" type suffix: default to P (.prg).
        name = s.strip(' \x04')
        if any(c.isalnum() for c in name):
            return ('header', name, 'P')
        return None  # menu noise behind TABs - keep sniffing, never abort

    def _pc1_wait_start(self):
        """Probe loop: send GOO, scan for header / END / handshake reply.

        Returns ('header', name, ftype), ('transfer-open',) (our probe was
        ACKed - opening handshake done), ('transfer',) (unprompted sender
        GOO - opening still to do), ('end',) or None (cancel/disconnect).
        Never times out on silence; the probe spacing below only sets the
        re-send rate, a laggy BBS just answers later.
        """
        tabs = 0
        hbuf = bytearray()
        in_header = False
        tail = bytearray()
        next_probe = 0.0
        while not self._pc1_gone():
            now = time.time()
            if now >= next_probe:
                self._punter_send_code(self.PUNTER_GOO)
                next_probe = now + 2.0
            b = self._pc1_get_byte(0.2)
            if b is None:
                continue
            if b == 0x09:
                tabs += 1
                if tabs == 10:
                    hbuf = bytearray()
                    in_header = True
                tail.clear()
                continue
            if in_header:
                if b == 0x0D:
                    res = self._pc1_parse_header(hbuf)
                    tabs = 0
                    hbuf = bytearray()
                    in_header = False
                    tail.clear()
                    if res is not None:
                        return res
                    continue
                hbuf.append(b)
                if len(hbuf) > 64:
                    tabs = 0  # noise, not a header
                    hbuf = bytearray()
                    in_header = False
                continue
            tail.append(b)
            if len(tail) > 3:
                tail = tail[-3:]
            if bytes(tail) == b'ACK':
                return ('transfer-open',)
            if bytes(tail) == b'GOO':
                return ('transfer',)
            tabs = 0
        return None
    
    # ==================================================================================
    # MULTI-PUNTER SUPPORT
    # ==================================================================================
    
    def _punter_send_multi(self, filepaths, callback=None):
        """
        Multi-Punter Send: per file 16xTAB + FILENAME,P|S + CR, then the C1
        flow; after the last file 16xTAB + 16xEOT + CR (spec). No timeouts:
        each step waits as long as the receiver needs.
        """
        import os

        self.log(f"\n{'='*60}")
        self.log(f"PUNTER BATCH SEND: {len(filepaths) if isinstance(filepaths, list) else 1} files")
        self.log(f"{'='*60}")

        self._pc1_stash = bytearray()
        if isinstance(filepaths, str):
            filepaths = [filepaths]

        total_files = len(filepaths)

        for idx, filepath in enumerate(filepaths):
            if self._pc1_gone():
                return False
            filename = os.path.basename(filepath)
            ftype = 'S' if self._punter_ftype_of(filepath) == 1 else 'P'

            self.log(f"\n--- File {idx + 1}/{total_files}: {filename} ---")
            self._punter_send_file_header(filename, ftype)

            try:
                with open(filepath, 'rb') as f:
                    file_data = f.read()
            except OSError as e:
                self.log(f"ERROR: Cannot read {filepath}: {e}")
                return False

            if callback:
                def file_callback(sent, total, status, _i=idx, _n=filename):
                    callback(sent, total, f"[{_i + 1}/{total_files}] {_n}: {status}")
            else:
                file_callback = None

            if not self._pc1_send_one(file_data,
                                      1 if ftype == 'S' else 0,
                                      file_callback, filename):
                self.log(f"ERROR: Failed to send {filename}")
                return False
            self.log(f"File {idx + 1}/{total_files} complete: {filename}")

        # End marker after the last file (spec, not 5x$04$09).
        self._punter_send_end_marker()
        self.log(f"\n✓ PUNTER BATCH COMPLETE: {total_files} files sent")
        return True
    
    def _punter_send_file_header(self, filename, ftype='P'):
        """
        Sendet Multi-Punter Datei-Header (Spec: 16x TAB + Name,Typ + CR).

        Args:
            filename: Dateiname (max 16 Zeichen für C64)
            ftype: 'P' für PRG, 'S' für SEQ
        """
        # Dateiname auf 16 Zeichen begrenzen und bereinigen
        clean_name = filename[:16].upper()

        # Header bauen: 16x TAB (0x09) + Filename + "," + Type + CR (0x0D)
        header = bytearray()
        header.extend(b'\x09' * 16)  # 16x TAB (Spec)
        header.extend(clean_name.encode('ascii', errors='replace'))
        header.append(ord(','))
        header.append(ord(ftype.upper()))
        header.append(0x0D)  # CR

        hex_str = ' '.join(f'{b:02X}' for b in header)
        self.punter_log(f"    [OUT] Header: {hex_str}")
        self.log(f"    [OUT] Header: 16xTAB + {clean_name},{ftype} + CR")
        self.send_raw(bytes(header))
    
    def _punter_send_end_marker(self):
        """
        Sendet Punter Batch End-Marker
        
        Format: 16× TAB + 16× EOT + CR
        """
        end_marker = bytearray()
        end_marker.extend(b'\x09' * 16)  # 16× TAB
        end_marker.extend(b'\x04' * 16)  # 16× EOT
        end_marker.append(0x0D)          # CR
        
        hex_str = ' '.join(f'{b:02X}' for b in end_marker)
        self.punter_log(f"    [OUT] End marker: {hex_str}")
        self.log(f"    [OUT] End marker: 16xTAB + 16xEOT + CR")
        self.send_raw(bytes(end_marker))

    def _turbomodem_send(self, filepath, callback):
        """TurboModem Send - 10-20x faster than XModem! Supports Multi-File!"""
        from turbomodem import TurboModem
        import os
        
        # Multi-File oder Single-File?
        is_multi = isinstance(filepath, list)
        
        if is_multi:
            self.log(f"\n{'='*60}")
            self.log(f"TURBOMODEM MULTI-SEND: {len(filepath)} files")
            for f in filepath:
                self.log(f"  - {os.path.basename(f)}")
            self.log(f"{'='*60}")
        else:
            self.log(f"\n{'='*60}")
            self.log(f"TURBOMODEM SEND: {filepath}")
            self.log(f"{'='*60}")
        
        try:
            turbo = TurboModem(self.connection, debug=self.debug_enabled)
            
            if is_multi:
                # Multi-File: send_files() verwenden
                success, files_sent = turbo.send_files(filepath, callback)
                
                if success:
                    bps, duration = turbo.get_speed()
                    self.log(f"✓ TURBOMODEM MULTI-SEND ERFOLGREICH")
                    self.log(f"  Files sent: {files_sent}/{len(filepath)}")
                    self.log(f"  Duration: {duration:.2f}s")
                    self.log(f"  Speed: {bps/1024:.2f} KB/s")
                    self.log(f"  Blocks sent: {turbo.stats['blocks_sent']}")
                    self.log(f"  Retransmits: {turbo.stats['retransmits']}")
                else:
                    self.log(f"✗ TURBOMODEM MULTI-SEND FEHLGESCHLAGEN ({files_sent} von {len(filepath)} gesendet)")
            else:
                # Single-File: auch über send_files() für Direct Socket + Streaming
                success, files_sent = turbo.send_files([filepath], callback)
                
                if success:
                    bps, duration = turbo.get_speed()
                    self.log(f"✓ TURBOMODEM SEND ERFOLGREICH")
                    self.log(f"  Duration: {duration:.2f}s")
                    self.log(f"  Speed: {bps/1024:.2f} KB/s")
                    self.log(f"  Blocks sent: {turbo.stats['blocks_sent']}")
                    self.log(f"  Retransmits: {turbo.stats['retransmits']}")
                else:
                    self.log("✗ TURBOMODEM SEND FEHLGESCHLAGEN")
            
            return success
            
        except Exception as e:
            self.log(f"ERROR: {str(e)}")
            import traceback
            self.log(traceback.format_exc())
            return False
    
    def _turbomodem_receive(self, filepath, callback):
        """TurboModem Receive - 10-20x faster than XModem! Supports Multi-File!"""
        from turbomodem import TurboModem
        import os
        
        self.log(f"\n{'='*60}")
        self.log(f"TURBOMODEM RECEIVE (input): {filepath}")
        
        # WICHTIG: TurboModem benötigt NUR das Verzeichnis, nicht den Dateinamen!
        # Der Dateiname kommt vom Server
        if os.path.isfile(filepath):
            # Wenn filepath eine Datei ist, verwende nur das Verzeichnis
            target_dir = os.path.dirname(filepath)
            self.log(f"Input is file - using directory: {target_dir}")
        elif os.path.isdir(filepath):
            # Bereits ein Verzeichnis
            target_dir = filepath
            self.log(f"Input is directory: {target_dir}")
        else:
            # Existiert nicht - prüfe ob es ein Dateiname oder Verzeichnis ist
            if '.' in os.path.basename(filepath):
                # Hat Extension -> ist ein Dateiname, verwende Verzeichnis
                target_dir = os.path.dirname(filepath)
                self.log(f"Input has extension (temp file?) - using directory: {target_dir}")
            else:
                # Kein Punkt -> ist wahrscheinlich ein Verzeichnis
                target_dir = filepath
                self.log(f"Input looks like directory: {target_dir}")
        
        self.log(f"Target directory for TurboModem: {target_dir}")
        self.log(f"{'='*60}")
        
        try:
            turbo = TurboModem(self.connection, debug=self.debug_enabled)
            
            # MULTI-FILE: receive_files() empfängt alle Dateien bis TBND
            success, received_files = turbo.receive_files(target_dir, callback)
            
            if success and received_files:
                bps, duration = turbo.get_speed()
                self.log(f"✓ TURBOMODEM MULTI-RECEIVE ERFOLGREICH")
                self.log(f"  Files received: {len(received_files)}")
                for f in received_files:
                    self.log(f"    - {os.path.basename(f)}")
                self.log(f"  Duration: {duration:.2f}s")
                self.log(f"  Speed: {bps/1024:.2f} KB/s")
                self.log(f"  Blocks received: {turbo.stats['blocks_received']}")
                
                # Gib Liste der empfangenen Dateien zurück für Multi-File Support
                return True, received_files
            else:
                self.log("✗ TURBOMODEM RECEIVE FEHLGESCHLAGEN")
                return False, []
            
        except Exception as e:
            self.log(f"ERROR: {str(e)}")
            import traceback
            self.log(traceback.format_exc())
            return False, []

    # =========================================================================
    # HIGH-SPEED PROTOCOLS (für LAN - maximaler Speed)
    # =========================================================================
    
    # CRC-32 Tabelle für ZSTREAM
    _CRC32_TABLE = None
    
    @classmethod
    def _init_crc32_table(cls):
        """Initialisiert CRC-32 Lookup-Tabelle"""
        if cls._CRC32_TABLE is not None:
            return
        cls._CRC32_TABLE = []
        for i in range(256):
            crc = i
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0xEDB88320
                else:
                    crc >>= 1
            cls._CRC32_TABLE.append(crc)
    
    def _calc_crc32(self, data):
        """Schnelle CRC-32 Berechnung"""
        self._init_crc32_table()
        crc = 0xFFFFFFFF
        for byte in data:
            crc = self._CRC32_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
        return crc ^ 0xFFFFFFFF
    
    # =========================================================================
    # YMODEM-G wurde entfernt - funktioniert nicht zuverlässig über Telnet
    # =========================================================================
    
    def _read_bytes_fast(self, count, timeout=5.0):
        """
        Liest mehrere Bytes für High-Speed Protokolle
        Benutzt connection.get_received_data_raw() wenn verfügbar
        """
        self.log(f"[_read_bytes_fast] START - requesting {count} bytes, timeout={timeout}s")
        
        result = bytearray()
        
        # Buffer zuerst leeren
        if self.byte_buffer:
            self.log(f"[_read_bytes_fast] byte_buffer has {len(self.byte_buffer)} bytes")
        while self.byte_buffer and len(result) < count:
            result.append(self.byte_buffer.pop(0))
        
        if len(result) >= count:
            self.log(f"[_read_bytes_fast] Got all {count} bytes from buffer")
            return bytes(result)
        
        remaining = count - len(result)
        self.log(f"[_read_bytes_fast] Need {remaining} more bytes from connection")
        
        # BESTE METHODE: get_received_data_raw() - liest exakt N Bytes!
        if hasattr(self.connection, 'get_received_data_raw'):
            self.log(f"[_read_bytes_fast] Using get_received_data_raw for {remaining} bytes...")
            try:
                data = self.connection.get_received_data_raw(remaining, timeout=timeout)
                if data:
                    result.extend(data)
                    self.log(f"[_read_bytes_fast] Got {len(data)} bytes, total: {len(result)}/{count}")
                else:
                    self.log(f"[_read_bytes_fast] get_received_data_raw returned None")
            except Exception as e:
                self.log(f"[_read_bytes_fast] get_received_data_raw error: {e}")
        
        # Fallback: Direkt vom Socket
        elif hasattr(self.connection, 'socket') and self.connection.socket:
            self.log(f"[_read_bytes_fast] Fallback: direct socket read for {remaining} bytes...")
            sock = self.connection.socket
            end_time = time.time() + timeout
            old_timeout = sock.gettimeout()
            
            try:
                while len(result) < count and time.time() < end_time:
                    if self.cancel_requested:
                        return None
                    
                    time_left = end_time - time.time()
                    sock.settimeout(min(1.0, max(0.1, time_left)))
                    
                    try:
                        chunk = sock.recv(min(count - len(result), 65536))
                        if chunk:
                            result.extend(chunk)
                        else:
                            break
                    except socket.timeout:
                        continue
                    except BlockingIOError:
                        time.sleep(0.01)
                        continue
            finally:
                try:
                    sock.settimeout(old_timeout)
                except:
                    pass
        else:
            self.log(f"[_read_bytes_fast] ERROR: No read method available!")
            self.log(f"[_read_bytes_fast] connection type: {type(self.connection)}")
        
        if len(result) < count:
            self.log(f"[_read_bytes_fast] Timeout! Got {len(result)}/{count} bytes")
            if len(result) > 0:
                self.log(f"[_read_bytes_fast] Partial data: {' '.join(f'{b:02X}' for b in result[:50])}")
            return None
        
        return bytes(result)
    
    # =========================================================================
    # RAWTCP: Zero-Overhead Maximum Speed
    # =========================================================================
    
    RAWTCP_MAGIC = b'FAST'
    RAWTCP_HEADER = 0x01
    RAWTCP_DATA = 0x02
    RAWTCP_END = 0x03
    RAWTCP_OK = 0x04
    RAWTCP_READY = 0x10  # Server → Client: Bereit für Header
    RAWTCP_INIT = 0x11   # Client → Server: Bereit für Transfer
    RAWTCP_BATCH = 0x12  # Batch-Modus: mehrere Dateien
    
    def _rawtcp_send(self, filepath, callback):
        """
        RAWTCP Send - Maximaler Speed, minimaler Overhead
        Unterstützt Single-File und Batch-Upload
        
        Args:
            filepath: String (single file) oder List[String] (batch)
        
        Protocol:
        1. Client sends INIT (+ file count for batch)
        2. Server sends READY
        3. For each file:
           - Client sends header
           - Server sends OK
           - Client streams data
           - Client sends END
           - Server sends OK
        """
        import os
        import hashlib
        
        # Normalisiere zu Liste
        if isinstance(filepath, str):
            filepaths = [filepath]
        else:
            filepaths = list(filepath)
        
        num_files = len(filepaths)
        is_batch = num_files > 1
        
        self.log(f"\n{'='*60}")
        if is_batch:
            self.log(f"RAWTCP BATCH SEND: {num_files} files")
        else:
            self.log(f"RAWTCP SEND (Upload): {filepaths[0]}")
        self.log(f"RAWTCP Protocol Version: 3 (INIT-READY + Batch)")
        self.log(f"{'='*60}")
        
        # Prüfe alle Dateien
        total_size = 0
        for fp in filepaths:
            if not os.path.isfile(fp):
                self.log(f"ERROR: File not found: {fp}")
                return False
            total_size += os.path.getsize(fp)
        
        start_time = time.time()
        
        try:
            # Schritt 1: Sende INIT Signal mit Dateianzahl
            self.log(f"Sending INIT signal (files={num_files})...")
            # INIT: FAST + 0x11 + file_count (2 bytes)
            init_signal = struct.pack('>4sBH', self.RAWTCP_MAGIC, self.RAWTCP_INIT, num_files)
            self.send_raw(init_signal)
            self.log(f"Sent INIT: {' '.join(f'{b:02X}' for b in init_signal)}")
            
            # Schritt 2: Warte auf READY Signal vom Server
            self.log("Waiting for server READY signal...")
            ready_signal = self._read_bytes_fast(5, timeout=30)
            
            if ready_signal is None:
                self.log("ERROR: No READY signal received (timeout)")
                return False
            
            if len(ready_signal) < 5 or ready_signal[:4] != self.RAWTCP_MAGIC:
                self.log(f"ERROR: Invalid READY signal: {ready_signal[:10] if ready_signal else 'None'}")
                return False
            
            if ready_signal[4] != self.RAWTCP_READY:
                self.log(f"ERROR: Expected READY (0x10), got {ready_signal[4]:02X}")
                return False
            
            self.log("Got READY signal from server")
            
            # Schritt 3: Sende jede Datei
            total_bytes_sent = 0
            
            for file_idx, fp in enumerate(filepaths):
                filename = os.path.basename(fp)
                filesize = os.path.getsize(fp)
                
                self.log(f"\n--- File {file_idx+1}/{num_files}: {filename} ({filesize} bytes) ---")
                
                # Callback: File start
                if callback:
                    callback(total_bytes_sent, total_size, f"📤 {filename}", 
                            event='file_start', filename=filename)
                
                # Berechne Datei-Checksum
                with open(fp, 'rb') as f:
                    file_hash = hashlib.md5()
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        file_hash.update(chunk)
                    checksum = int.from_bytes(file_hash.digest()[:4], 'big')
                
                # Sende Header
                fname_bytes = filename.encode('utf-8')[:255]
                header = struct.pack('>4sQBBI',
                    self.RAWTCP_MAGIC, filesize, len(fname_bytes), self.RAWTCP_HEADER, checksum)
                header += fname_bytes
                self.send_raw(header)
                self.log(f"Sent header ({len(header)} bytes)")
                
                # Warte auf OK
                response = self._read_bytes_fast(5, timeout=10)
                if response is None or response[:4] != self.RAWTCP_MAGIC or response[4] != self.RAWTCP_OK:
                    self.log("ERROR: No RAWTCP handshake (OK)")
                    return False
                
                self.log(f"Streaming {filesize} bytes...")
                
                # Streame Dateidaten
                bytes_sent_file = 0
                chunk_size = 65536
                
                with open(fp, 'rb') as f:
                    while True:
                        data = f.read(chunk_size)
                        if not data:
                            break
                        
                        self.send_raw(data)
                        bytes_sent_file += len(data)
                        total_bytes_sent += len(data)
                        
                        if callback:
                            callback(total_bytes_sent, total_size, f"📤 {filename}")
                
                # Sende END Marker für diese Datei
                end_marker = struct.pack('>4sB', self.RAWTCP_MAGIC, self.RAWTCP_END)
                self.send_raw(end_marker)
                
                # Warte auf OK
                response = self._read_bytes_fast(5, timeout=10)
                if response is None or response[:4] != self.RAWTCP_MAGIC or response[4] != self.RAWTCP_OK:
                    self.log(f"WARNING: No final OK for file {filename}")
                
                self.log(f"✓ File complete: {filename}")
                
                # Callback: File complete
                if callback:
                    callback(total_bytes_sent, total_size, f"✓ {filename}",
                            event='file_complete', filename=filename, size=filesize)
            
            elapsed = time.time() - start_time
            speed = total_size / elapsed if elapsed > 0 else 0
            
            if is_batch:
                self.log(f"\n✓ RAWTCP BATCH: {num_files} files, {total_size} bytes in {elapsed:.2f}s ({speed/1024/1024:.2f} MB/s)")
            else:
                self.log(f"✓ RAWTCP: {total_size} bytes in {elapsed:.2f}s ({speed/1024/1024:.2f} MB/s)")
            
            if callback:
                callback(total_size, total_size, "Complete!")
            
            return True
            
        except Exception as e:
            self.log(f"ERROR: {e}")
            import traceback
            self.log(traceback.format_exc())
            return False
    
    def _rawtcp_receive(self, filepath, callback):
        """
        RAWTCP Receive - Maximaler Speed
        Unterstützt sowohl alte Version (ohne BATCH) als auch neue Version (mit BATCH).
        
        Returns:
            tuple: (success, actual_filepath or list of filepaths)
        """
        import os
        import hashlib
        
        self.log(f"\n{'='*60}")
        self.log(f"RAWTCP RECEIVE: {filepath}")
        self.log(f"RAWTCP Protocol Version: 3 (Batch-kompatibel)")
        self.log(f"{'='*60}")
        
        # Bestimme Zielverzeichnis
        if os.path.isdir(filepath):
            target_dir = filepath
        else:
            target_dir = os.path.dirname(filepath) or '.'
        
        start_time = time.time()
        received_files = []
        
        try:
            # Sende READY Signal
            self.log(">>> Sending READY signal...")
            ready_signal = struct.pack('>4sB', self.RAWTCP_MAGIC, self.RAWTCP_READY)
            if not self.send_raw(ready_signal):
                self.log("ERROR: Failed to send READY signal!")
                return False, None
            
            # Suche nach FAST magic im Datenstrom (überspringt BBS Text)
            self.log(">>> Searching for FAST magic in stream...")
            
            buffer = bytearray()
            timeout_end = time.time() + 30
            first_packet = None
            
            while time.time() < timeout_end:
                byte = self._read_byte(timeout=1.0)
                if byte is not None:
                    buffer.append(byte)
                    
                    # Suche FAST magic
                    idx = buffer.find(self.RAWTCP_MAGIC)
                    if idx >= 0:
                        # Brauchen mindestens 5 Bytes (magic + type)
                        if len(buffer) >= idx + 5:
                            pkt_type = buffer[idx + 4]
                            
                            if pkt_type == self.RAWTCP_BATCH:
                                # Neue Version: BATCH info
                                if len(buffer) >= idx + 7:
                                    first_packet = bytes(buffer[idx:idx+7])
                                    if idx > 0:
                                        self.log(f">>> Skipped {idx} bytes of BBS text")
                                    break
                            elif pkt_type == self.RAWTCP_HEADER:
                                # Alte Version: Direkt Header
                                if len(buffer) >= idx + 18:
                                    first_packet = bytes(buffer[idx:idx+18])
                                    if idx > 0:
                                        self.log(f">>> Skipped {idx} bytes of BBS text")
                                    # Lese noch den Dateinamen dazu
                                    name_len = first_packet[12]  # namelen Position
                                    while len(buffer) < idx + 18 + name_len:
                                        b = self._read_byte(timeout=1.0)
                                        if b:
                                            buffer.append(b)
                                    first_packet = bytes(buffer[idx:idx+18+name_len])
                                    break
            else:
                self.log("ERROR: Timeout - no FAST magic found")
                if buffer:
                    self.log(f">>> Received: {bytes(buffer[:100])!r}")
                return False, None
            
            # Parse first packet
            pkt_type = first_packet[4]
            
            if pkt_type == self.RAWTCP_BATCH:
                # Neue Version mit BATCH info
                num_files = struct.unpack('>H', first_packet[5:7])[0]
                self.log(f">>> BATCH mode: {num_files} files")
                pre_header = None
            else:
                # Alte Version - Header kam direkt
                num_files = 1
                self.log(">>> Legacy mode: single file (no BATCH header)")
                pre_header = first_packet  # Header schon gelesen
            
            is_batch = num_files > 1
            total_bytes = 0
            
            # Empfange jede Datei
            for file_idx in range(num_files):
                self.log(f"\n--- File {file_idx+1}/{num_files} ---")
                
                # Header lesen (falls nicht schon vorhanden)
                if pre_header and file_idx == 0:
                    header_data = pre_header
                else:
                    header_data = self._read_bytes_fast(18, timeout=30)
                    if not header_data:
                        self.log("ERROR: No header received")
                        break
                
                if header_data[:4] != self.RAWTCP_MAGIC:
                    self.log(f"ERROR: Invalid magic: {header_data[:4]}")
                    break
                
                filesize, name_len, pkt_type, checksum = struct.unpack('>QBBI', header_data[4:18])
                
                if pkt_type != self.RAWTCP_HEADER:
                    self.log(f"ERROR: Expected HEADER (0x01), got {pkt_type:02X}")
                    break
                
                # Dateiname lesen
                if pre_header and file_idx == 0 and len(pre_header) > 18:
                    filename = pre_header[18:18+name_len].decode('utf-8', errors='replace')
                else:
                    filename_bytes = self._read_bytes_fast(name_len, timeout=5)
                    if not filename_bytes:
                        self.log("ERROR: No filename received")
                        break
                    filename = filename_bytes.decode('utf-8', errors='replace')
                
                self.log(f"Receiving: {filename} ({filesize} bytes)")
                
                # Sende OK
                ok_response = struct.pack('>4sB', self.RAWTCP_MAGIC, self.RAWTCP_OK)
                self.send_raw(ok_response)
                
                # Callback: File start
                if callback:
                    callback(total_bytes, -1, f"📥 {filename}",
                            event='file_start', filename=filename)
                
                # Empfange Daten
                actual_filepath = os.path.join(target_dir, filename)
                bytes_received = 0
                file_hash = hashlib.md5()
                
                with open(actual_filepath, 'wb') as f:
                    remaining = filesize
                    while remaining > 0:
                        chunk_size = min(remaining, 65536)
                        data = self._read_bytes_fast(chunk_size, timeout=30)
                        if data is None:
                            self.log("ERROR: Incomplete transfer")
                            break
                        
                        f.write(data)
                        file_hash.update(data)
                        bytes_received += len(data)
                        remaining -= len(data)
                        
                        if callback:
                            callback(total_bytes + bytes_received, -1, f"📥 {filename}")
                
                total_bytes += bytes_received
                
                # Verifiziere Checksum
                actual_checksum = int.from_bytes(file_hash.digest()[:4], 'big')
                if actual_checksum != checksum:
                    self.log(f"WARNING: Checksum mismatch!")
                else:
                    self.log("Checksum OK")
                
                # Warte auf END Marker
                end_marker = self._read_bytes_fast(5, timeout=5)
                if end_marker and end_marker[4] == self.RAWTCP_END:
                    self.log("Got END marker")
                
                # Sende OK
                self.send_raw(ok_response)
                
                self.log(f"✓ File complete: {filename}")
                received_files.append(actual_filepath)
                
                # Callback: File complete
                if callback:
                    callback(total_bytes, -1, f"✓ {filename}",
                            event='file_complete', filename=filename, size=filesize)
            
            elapsed = time.time() - start_time
            speed = total_bytes / elapsed if elapsed > 0 else 0
            
            if is_batch:
                self.log(f"\n✓ BATCH: {len(received_files)} files, {total_bytes} bytes in {elapsed:.2f}s ({speed/1024/1024:.2f} MB/s)")
            else:
                self.log(f"✓ RAWTCP: {total_bytes} bytes in {elapsed:.2f}s ({speed/1024/1024:.2f} MB/s)")
            
            for fp in received_files:
                self.log(f"  Saved: {fp}")
            
            if callback:
                callback(total_bytes, total_bytes, "Complete!")
            
            # Return: Single file → string, Batch → list
            if is_batch:
                return True, received_files
            else:
                return True, received_files[0] if received_files else None
            
        except Exception as e:
            self.log(f"ERROR: {e}")
            import traceback
            self.log(traceback.format_exc())
            return False, received_files[0] if received_files else None
