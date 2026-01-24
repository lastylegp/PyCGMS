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
    PUNTER = "Punter"              # Single-File Punter (kein Header)
    PUNTER_MULTI = "Multi-Punter"  # Multi-Punter Batch (Header pro Datei)
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
        # Punter Upload:
        # - Single File: _punter_send() - OHNE Header (BBS kennt Filename bereits)
        # - Multi-File: _punter_send_multi() - MIT Header pro Datei
        if self.protocol in [TransferProtocol.PUNTER, TransferProtocol.PUNTER_MULTI]:
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
                                     TransferProtocol.PUNTER, TransferProtocol.PUNTER_MULTI,
                                     TransferProtocol.TURBOMODEM]:
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
            
            # Restliche Protokolle: Single-File only
            if isinstance(filepath, list):
                if len(filepath) == 0:
                    self.log("✗ ERROR: Empty file list")
                    return False
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
            elif self.protocol in [TransferProtocol.PUNTER, TransferProtocol.PUNTER_MULTI]:
                # Beide Punter-Varianten verwenden Header
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
        if callback:
            callback(0, 0, "ZModem noch nicht implementiert")
        return False
    
    def _zmodem_receive(self, filepath, callback):
        if callback:
            callback(0, "ZModem noch nicht implementiert")
        return False
    
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
    
    def _punter_wait_for_code(self, expected_codes, timeout=30):
        """
        Wartet auf einen der erwarteten 3-Byte Punter Codes
        
        Args:
            expected_codes: Liste von erwarteten Codes (z.B. [b'GOO', b'BAD'])
            timeout: Timeout in Sekunden
        
        Returns:
            Empfangener Code oder None bei Timeout/Cancel
        """
        buffer = bytearray()
        end_time = time.time() + timeout
        
        # Live-Update: Zeige worauf gewartet wird
        codes_str = ', '.join(c.decode('ascii', errors='replace') for c in expected_codes)
        self._live_update('WAIT', None, f"Waiting for: {codes_str}")
        self.waiting_for_input = True
        self.waiting_for_codes = expected_codes
        
        while time.time() < end_time:
            # Check für Cancel
            if self.cancel_requested:
                self.log("    CANCELLED by user")
                self.waiting_for_input = False
                return None
            
            byte = self._read_byte(timeout=0.5)
            if byte is not None:
                buffer.append(byte)
                
                # Live-Update: Zeige empfangene Bytes
                self._live_update('IN', bytes([byte]), f"byte: 0x{byte:02X}")
                
                # Suche nach einem der erwarteten Codes im Buffer
                for code in expected_codes:
                    if len(buffer) >= len(code):
                        # Prüfe ob Code am Ende des Buffers ist
                        if buffer[-len(code):] == bytearray(code):
                            hex_str = ' '.join(f'{b:02X}' for b in buffer)
                            ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in buffer)
                            self.punter_log(f"    [IN] {hex_str} |{ascii_str}| -> matched {code}")
                            self._live_update('IN', code, f"MATCHED: {code.decode('ascii', errors='replace')}")
                            self.waiting_for_input = False
                            return code
                
                # Buffer nicht zu groß werden lassen
                if len(buffer) > 20:
                    buffer = buffer[-10:]
        
        self.waiting_for_input = False
        self.log(f"    TIMEOUT waiting for {expected_codes}")
        self._live_update('STATUS', None, f"TIMEOUT waiting for {codes_str}")
        if buffer:
            hex_str = ' '.join(f'{b:02X}' for b in buffer)
            ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in buffer)
            self.punter_log(f"    [IN] Buffer at timeout: {hex_str} |{ascii_str}|")
        else:
            self.punter_log(f"    [IN] Buffer at timeout: EMPTY (no data received)")
        return None
    
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
    
    def _punter_end_off_sequence(self):
        """
        Führt die End-Off Sequenz durch (Steps 7-16 bzw. 22-31)
        Buggy im Original: Sender sendet 3x S/B mit je 1s Pause
        """
        # Sende ACK
        self._punter_send_code(self.PUNTER_ACK)
        
        # Warte auf S/B
        code = self._punter_wait_for_code([self.PUNTER_SB], timeout=10)
        if code != self.PUNTER_SB:
            self.log("    WARNING: Expected S/B in end-off")
            return False
        
        # Sende SYN
        self._punter_send_code(self.PUNTER_SYN)
        
        # Warte auf SYN
        code = self._punter_wait_for_code([self.PUNTER_SYN], timeout=10)
        if code != self.PUNTER_SYN:
            self.log("    WARNING: Expected SYN in end-off")
            return False
        
        # Sende S/B und warte (3x mit je 1s Pause - Bug im Original)
        for i in range(3):
            self._punter_send_code(self.PUNTER_SB)
            time.sleep(1.0)
        
        return True
    
    def _punter_send(self, filepath, callback=None):
        """
        Punter C1 Send - Sendet eine EINZELNE Datei (OHNE Header!)
        
        Bei Single-File Upload:
        - Das BBS hat bereits den Upload-Modus gestartet
        - SENDER startet mit GOO um Transfer zu initiieren
        - Client sendet NUR die Daten, KEINEN Header!
        - Der Dateiname wurde bereits vom User im BBS eingegeben
        
        Flow:
        1. Sender sendet GOO um Transfer zu starten
        2. Punter Batch Flow (nur Daten, kein Header)
        3. End-Off mit 5x $04$09
        """
        import os
        
        self.log(f"\n{'='*60}")
        self.log(f"PUNTER C1 SEND (Single - No Header): {filepath}")
        self.log(f"{'='*60}")
        
        # SENDER startet mit GOO!
        self.log("Sender initiates with GOO...")
        self._punter_send_code(self.PUNTER_GOO)
        time.sleep(0.3)
        
        # Datei senden (OHNE vorherigen Header!)
        success = self._punter_send_after_header(filepath, callback)
        
        if success:
            # Single File Ende: 5x $04$09 senden
            self.log("\n--- Sending End-Off: 5x $04$09 ---")
            time.sleep(0.3)
            end_signal = bytes([0x04, 0x09])
            for i in range(5):
                self.send_raw(end_signal)
                self.log(f"    Sent $04$09 {i+1}/5")
                time.sleep(0.1)
            self.log(f"\n✓ PUNTER SEND COMPLETE: {filepath}")
        
        return success
    
    def _punter_receive(self, filepath, callback=None):
        """
        Punter C1 Receive - Empfängt eine oder mehrere Dateien
        
        Bei Multi-Punter: Nach jedem File prüfen ob ein weiterer Header kommt
        
        Flow:
        1. Client sendet GOO um Bereitschaft zu signalisieren
        2. BBS antwortet mit Header ODER GOO/ACK (Transfer-Modus)
        3. Header: 10×TAB + Filename,type + CR
        4. Punter Batch Flow
        5. Nach End-Off: Prüfen ob weiterer Header kommt
        """
        import os
        
        self.log(f"\n{'='*60}")
        self.log(f"PUNTER C1 RECEIVE: {filepath}")
        self.log(f"{'='*60}")
        
        # Debug: Connection info
        self.log(f"Connection type: {type(self.connection)}")
        if hasattr(self.connection, 'connected'):
            self.log(f"Connection connected: {self.connection.connected}")
        
        # Sammle alle Daten die schon in der Queue sind
        all_received = bytearray()
        if hasattr(self.connection, 'receive_queue'):
            queue_items = 0
            while not self.connection.receive_queue.empty():
                try:
                    data = self.connection.receive_queue.get_nowait()
                    if data:
                        if isinstance(data, str):
                            data = data.encode('latin-1')
                        all_received.extend(data)
                        queue_items += 1
                except:
                    break
            self.log(f"    Collected {queue_items} items from queue, total {len(all_received)} bytes")
        
        if len(all_received) > 0:
            self.byte_buffer.extend(all_received)
        
        file_count = 0
        target_dir = filepath if os.path.isdir(filepath) else os.path.dirname(filepath)
        
        try:
            if callback:
                callback(0, 0, "Signalisiere Bereitschaft...")
            
            # Sende GOO um dem BBS zu signalisieren dass wir bereit sind
            self.log("Sending GOO to trigger BBS...")
            self._punter_send_code(self.PUNTER_GOO)
            time.sleep(0.2)
            self._punter_send_code(self.PUNTER_GOO)
            time.sleep(0.2)
            self._punter_send_code(self.PUNTER_GOO)
            
            # Loop für mehrere Dateien (Multi-Punter)
            while True:
                if callback:
                    callback(0, 0, "Warte auf Header...")
                
                # Warte auf Header vom BBS (10×TAB + filename,type + CR)
                self.log("Waiting for file header (10xTAB + filename,type + CR)...")
                header = self._punter_wait_for_header(timeout=30)
                
                if header is None:
                    if file_count == 0:
                        self.log("ERROR: No header received")
                        self.log("TIP: Press F3 IMMEDIATELY when BBS shows 'Start Transfer'")
                        return False
                    else:
                        self.log(f"No more headers - transfer complete ({file_count} files)")
                        break
                
                if header == "END":
                    self.log(f"End marker received - transfer complete ({file_count} files)")
                    break
                
                if header == "TRANSFER_MODE":
                    # BBS ist schon im Transfer-Modus (Header wurde vom GUI konsumiert)
                    self.log("BBS already in transfer mode - proceeding without header")
                    filename = f"download_{int(time.time())}"
                    ftype = 'P'
                    
                    if os.path.isdir(filepath):
                        current_filepath = os.path.join(filepath, filename + ".PRG")
                    else:
                        current_filepath = filepath
                    
                    success = self._punter_receive_transfer_mode(current_filepath, callback)
                    if success:
                        file_count += 1
                        self.log(f"\n✓ File {file_count} received: {current_filepath}")
                    continue
                
                filename, ftype = header
                self.log(f"Received header: {filename},{ftype}")
                
                # Sanitize Filename
                safe_filename = filename.replace('/', '-').replace('\\', '-').replace(':', '-')
                safe_filename = safe_filename.replace('*', '-').replace('?', '-').replace('"', '-')
                safe_filename = safe_filename.replace('<', '-').replace('>', '-').replace('|', '-')
                if safe_filename != filename:
                    self.log(f"    Sanitized filename: {filename} -> {safe_filename}")
                
                # Füge Dateiendung hinzu basierend auf Typ
                ext = '.SEQ' if ftype.upper() == 'S' else '.PRG'
                if not safe_filename.upper().endswith(ext):
                    if '.' not in safe_filename:  # Nur wenn keine Endung vorhanden
                        safe_filename = safe_filename + ext
                        self.log(f"    Added extension: {safe_filename}")
                
                # Ziel-Pfad
                if os.path.isdir(filepath):
                    current_filepath = os.path.join(filepath, safe_filename)
                else:
                    current_filepath = filepath
                
                # Empfange Datei
                success = self._punter_receive_after_header(current_filepath, callback)
                
                if success:
                    file_count += 1
                    self.log(f"\n✓ File {file_count} received: {current_filepath}")
                    
                    # Nach erfolgreichem Download: Prüfen ob weitere Files kommen
                    # Sende GOO um dem BBS zu signalisieren dass wir bereit für mehr sind
                    self.log("Checking for more files...")
                    time.sleep(0.3)
                else:
                    self.log(f"ERROR: Failed to receive file {filename}")
                    break
            
            if file_count > 0:
                self.log(f"\n✓ PUNTER RECEIVE COMPLETE: {file_count} file(s)")
                return True
            else:
                return False
            
        except Exception as e:
            self.log(f"ERROR: {str(e)}")
            import traceback
            self.log(traceback.format_exc())
            return False
    
    def _punter_do_end_off_client(self):
        """
        End-Off Sequenz als Client/Receiver
        """
        # Warte auf ACK
        code = self._punter_wait_for_code([self.PUNTER_ACK, self.PUNTER_GOO], timeout=5)
        if code == self.PUNTER_GOO:
            self._punter_send_code(self.PUNTER_ACK)
        
        # Sende S/B
        self._punter_send_code(self.PUNTER_SB)
        
        # Warte auf SYN
        code = self._punter_wait_for_code([self.PUNTER_SYN], timeout=10)
        if code == self.PUNTER_SYN:
            self._punter_send_code(self.PUNTER_SYN)
            self.log("SYN exchange OK")
        
        # BBS sendet 3x S/B - warten und ignorieren
    
    def _punter_receive_transfer_mode(self, filepath, callback=None):
        """
        Punter Receive wenn BBS schon im Transfer-Modus ist
        (GOO+ACK wurde bereits empfangen, BBS erwartet S/B)
        
        Flow:
        [GOO+ACK bereits empfangen]
        S/B ->
                                 <- Block1 (8 bytes)
        GOO ->
                                 <- ACK
        S/B ->
                                 <- SYN (End-Off Phase A)
        SYN ->
                                 <- S/B
        [Phase B]
        GOO ->
                                 <- ACK  
        S/B ->
                                 <- Block2 (8 bytes)
        GOO ->
        [Datenblöcke]
                                 <- ACK
        S/B ->
                                 <- Datablock
        GOO ->
        ...
        """
        self.log(f"\n--- PUNTER RECEIVE (transfer mode): {filepath} ---")
        
        # Extrahiere Dateiname für Callback
        display_name = os.path.basename(filepath) if filepath else "download"
        
        try:
            # ============================================================
            # PHASE A: Block1
            # ============================================================
            self.log("Phase A: Block1 (BBS already sent GOO+ACK)")
            
            # BBS hat schon ACK gesendet, erwartet S/B
            self._punter_send_code(self.PUNTER_SB)
            
            # Empfange Block1 (8 Bytes) - mit Retry bei Checksum-Fehler
            max_retries = 3
            block1 = None
            for retry in range(max_retries):
                block1 = self._punter_receive_block(timeout=15)
                if block1 is not None:
                    break  # Erfolg!
                
                # Checksum-Fehler - sende BAD
                self.log(f"    BAD Block1 - sending BAD ({retry+1}/{max_retries})")
                self._punter_send_code(self.PUNTER_BAD)
                
                # Warte auf ACK und sende S/B für Retry
                code = self._punter_wait_for_code([self.PUNTER_ACK], timeout=10)
                if code == self.PUNTER_ACK:
                    self._punter_send_code(self.PUNTER_SB)
                else:
                    self.log("ERROR: No ACK after BAD for Block1")
                    break
            
            if block1 is None:
                self.log("ERROR: Failed to receive Block1 after retries")
                return False
            
            self.log(f"Block1 received: {len(block1.get('payload', b''))+7} bytes")
            
            # Sende GOO
            self._punter_send_code(self.PUNTER_GOO)
            
            # End-Off Phase A
            code = self._punter_wait_for_code([self.PUNTER_ACK], timeout=10)
            if code != self.PUNTER_ACK:
                self.log("WARNING: No ACK in end-off A")
            
            self._punter_send_code(self.PUNTER_SB)
            
            code = self._punter_wait_for_code([self.PUNTER_SYN], timeout=10)
            if code != self.PUNTER_SYN:
                self.log("WARNING: No SYN in end-off A")
            
            self._punter_send_code(self.PUNTER_SYN)
            
            code = self._punter_wait_for_code([self.PUNTER_SB], timeout=10)
            if code != self.PUNTER_SB:
                self.log("WARNING: No S/B in end-off A")
            
            # ============================================================
            # PHASE B: File Data (transfer_mode)
            # ============================================================
            self.log("Phase B: Data (transfer_mode)")
            
            # Flow nach S/B vom BBS:
            # Client: GOO (mehrmals falls nötig)
            # BBS: GOO
            # Client: GOO  
            # BBS: ACK
            # Client: S/B
            # BBS: Block2
            
            # Sende GOOs bis wir Antwort bekommen
            got_response = False
            for attempt in range(5):
                self._punter_send_code(self.PUNTER_GOO)
                time.sleep(0.2)
                
                # Prüfe ob Antwort da ist
                code = self._punter_wait_for_code([self.PUNTER_GOO, self.PUNTER_ACK, self.PUNTER_SB], timeout=2)
                if code is not None:
                    self.log(f"    Phase B response after {attempt+1} GOOs: {code}")
                    got_response = True
                    break
                self.log(f"    GOO attempt {attempt+1} - no response yet")
            
            if not got_response:
                self.log("ERROR: No response to Phase B GOOs")
                # Versuche trotzdem weiterzumachen
                code = None
            
            if code == self.PUNTER_GOO:
                # GOO empfangen - sende GOO zurück
                self._punter_send_code(self.PUNTER_GOO)
                
                # Jetzt auf ACK warten
                code = self._punter_wait_for_code([self.PUNTER_ACK], timeout=10)
                self.log(f"    After GOO exchange: {code}")
            
            if code == self.PUNTER_SB:
                # BBS hat schon S/B gesendet - direkt zum Block
                self.log("    BBS sent S/B - proceeding to block")
            elif code == self.PUNTER_ACK or code is None:
                # Sende S/B
                self._punter_send_code(self.PUNTER_SB)
            
            # Empfange Block2 (8 Bytes) - mit Retry bei Checksum-Fehler
            block2 = None
            for retry in range(max_retries):
                block2 = self._punter_receive_block(timeout=15)
                if block2 is not None:
                    break  # Erfolg!
                
                # Checksum-Fehler - sende BAD
                self.log(f"    BAD Block2 - sending BAD ({retry+1}/{max_retries})")
                self._punter_send_code(self.PUNTER_BAD)
                
                # Warte auf ACK und sende S/B für Retry
                code = self._punter_wait_for_code([self.PUNTER_ACK], timeout=10)
                if code == self.PUNTER_ACK:
                    self._punter_send_code(self.PUNTER_SB)
                else:
                    self.log("ERROR: No ACK after BAD for Block2")
                    break
            
            if block2 is None:
                self.log("ERROR: Failed to receive Block2 after retries")
                return False
            
            self.log(f"Block2 received: {len(block2.get('payload', b''))+7} bytes, next_size={block2['next_size']}")
            
            # Sende GOO
            self._punter_send_code(self.PUNTER_GOO)
            
            # Empfange Datenblöcke
            file_data = bytearray()
            block_count = 0
            next_block_size = block2['next_size']  # Größe des ersten Datenblocks
            
            while True:
                # Warte auf ACK
                code = self._punter_wait_for_code([self.PUNTER_ACK, self.PUNTER_SYN], timeout=10)
                if code is None:
                    self.log("No response - ending")
                    break
                if code == self.PUNTER_SYN:
                    self.log("SYN received - starting end-off")
                    self._punter_send_code(self.PUNTER_SYN)
                    break
                
                # Sende S/B
                self._punter_send_code(self.PUNTER_SB)
                
                # Empfange Block mit erwarteter Größe - mit Retry bei Checksum-Fehler
                max_retries = 3
                data_block = None
                for retry in range(max_retries):
                    data_block = self._punter_receive_block(timeout=20, expected_size=next_block_size)
                    if data_block is not None:
                        break  # Erfolg!
                    
                    # Checksum-Fehler - sende BAD und warte auf erneutes S/B
                    self.log(f"    BAD block - sending BAD ({retry+1}/{max_retries})")
                    self._punter_send_code(self.PUNTER_BAD)
                    
                    # Warte auf ACK + S/B für Retry
                    code = self._punter_wait_for_code([self.PUNTER_ACK], timeout=10)
                    if code == self.PUNTER_ACK:
                        self._punter_send_code(self.PUNTER_SB)
                    else:
                        self.log("ERROR: No ACK after BAD")
                        break
                
                if data_block is None:
                    self.log("ERROR: Failed to receive data block after retries")
                    break
                
                # Speichere next_size für nächsten Block
                next_block_size = data_block['next_size']
                
                if data_block['payload']:
                    file_data.extend(data_block['payload'])
                block_count += 1
                
                self.log(f"Datablock {block_count}: {len(data_block['payload']) if data_block['payload'] else 0} bytes, " +
                        f"total {len(file_data)}, is_last={data_block['is_last']}")
                
                if callback:
                    callback(len(file_data), 0, f"{display_name}: Block {block_count}")
                
                # Sende GOO
                self._punter_send_code(self.PUNTER_GOO)
                
                if data_block['is_last']:
                    break
            
            # End-Off Phase B
            code = self._punter_wait_for_code([self.PUNTER_ACK, self.PUNTER_SB], timeout=5)
            if code == self.PUNTER_ACK:
                self._punter_send_code(self.PUNTER_SB)
                code = self._punter_wait_for_code([self.PUNTER_SYN], timeout=5)
                if code == self.PUNTER_SYN:
                    self._punter_send_code(self.PUNTER_SYN)
            
            # Datei speichern
            if len(file_data) > 0:
                with open(filepath, 'wb') as f:
                    f.write(file_data)
                
                self.log(f"✓ Received {len(file_data)} bytes -> {filepath}")
                if callback:
                    # Sende FILE_COMPLETE Event für Dateiliste
                    callback(len(file_data), len(file_data), 
                            f"FILE_COMPLETE:{display_name}:{block_count}:{len(file_data)}")
                    callback(len(file_data), len(file_data), f"{display_name}: Complete!")
                return True
            else:
                self.log("ERROR: No data received")
                return False
            
        except Exception as e:
            self.log(f"ERROR: {str(e)}")
            import traceback
            self.log(traceback.format_exc())
            return False
        time.sleep(3.5)
        
        # Flush
        while self._read_byte(timeout=0.1) is not None:
            pass
        
        return True
    
    def _punter_receive_end_off_cbase(self):
        """Alias für Kompatibilität"""
        return self._punter_do_end_off_client()
    
    def _punter_receive_block(self, timeout=10, expected_size=None):
        """
        Empfängt einen Punter Block
        
        Block Format: [0-1] additive checksum, [2-3] cyclic checksum,
                      [4] next block size, [5-6] block index, [7+] payload
        
        Args:
            timeout: Timeout in Sekunden
            expected_size: Erwartete Block-Größe (aus vorherigem next_size)
                          Wenn None, wird 255 angenommen für Datenblöcke
        
        Returns:
            dict mit 'payload', 'next_size', 'block_index', 'is_last'
            oder None bei Fehler
        """
        block_data = bytearray()
        end_time = time.time() + timeout
        
        # Lese erste 3 Bytes um zu prüfen ob es ACK/GOO ist
        initial = bytearray()
        while len(initial) < 3 and time.time() < end_time:
            byte = self._read_byte(timeout=0.5)
            if byte is not None:
                initial.append(byte)
            else:
                break
        
        if len(initial) < 3:
            self.log("ERROR: Could not read initial bytes")
            return None
        
        # Prüfe ob es ACK oder GOO ist (überspringen)
        if bytes(initial) == b'ACK' or bytes(initial) == b'GOO':
            self.punter_log(f"    [SKIP] Skipped {bytes(initial)}")
            # Lese nächste 3 Bytes für Block-Header Start
            initial = bytearray()
            while len(initial) < 3 and time.time() < end_time:
                byte = self._read_byte(timeout=0.5)
                if byte is not None:
                    initial.append(byte)
                else:
                    break
        
        # initial enthält jetzt die ersten 3 Bytes des Blocks
        block_data.extend(initial)
        
        # Lese Rest des Headers (brauchen noch 4 bytes für total 7)
        while len(block_data) < 7 and time.time() < end_time:
            byte = self._read_byte(timeout=1)
            if byte is not None:
                block_data.append(byte)
        
        if len(block_data) < 7:
            self.log("ERROR: Could not read block header")
            if block_data:
                hex_str = ' '.join(f'{b:02X}' for b in block_data)
                self.punter_log(f"    Partial data received: {hex_str}")
            return None
        
        # Log Header
        hex_str = ' '.join(f'{b:02X}' for b in block_data[:7])
        self.punter_log(f"    [IN] Block header: {hex_str}")
        
        # Parse Header
        additive = block_data[0] | (block_data[1] << 8)
        cyclic = block_data[2] | (block_data[3] << 8)
        next_size = block_data[4]
        block_index = block_data[5] | (block_data[6] << 8)
        
        is_last = (block_index >= 0xFF00)  # Hi-byte >= 0xFF = letzter Block
        
        self.punter_log(f"    Block header parsed: add={additive:04X} cyc={cyclic:04X} next={next_size} idx={block_index:04X} last={is_last}")
        
        # Bestimme wie viele Bytes wir lesen müssen
        if expected_size is not None and expected_size > 7:
            # Verwende expected_size aus vorherigem Block
            target_size = expected_size
        else:
            # Standard: 255 bytes für Datenblöcke, 8 für Typ-Blöcke
            target_size = 255 if next_size > 0 else 8
        
        self.punter_log(f"    [BLOCK] Target size: {target_size}, already have: {len(block_data)}")
        
        # Lese Payload bis target_size erreicht
        read_start = time.time()
        while len(block_data) < target_size and time.time() < end_time:
            byte = self._read_byte(timeout=0.5)
            if byte is not None:
                block_data.append(byte)
            elif time.time() - read_start > 2.0:
                # Nach 2 Sekunden ohne Fortschritt -> Warnung aber weitermachen
                self.punter_log(f"    [WARN] Slow read: {len(block_data)}/{target_size} bytes after 2s")
                read_start = time.time()  # Reset für nächste Warnung
        
        # Warnung wenn nicht alle Bytes empfangen
        if len(block_data) < target_size:
            self.punter_log(f"    [WARN] Block incomplete: {len(block_data)}/{target_size} bytes")
        
        payload = bytes(block_data[7:])
        
        # Log kompletten Block
        hex_str = ' '.join(f'{b:02X}' for b in block_data[:32])
        if len(block_data) > 32:
            hex_str += f"... ({len(block_data)} total)"
        self.punter_log(f"    [IN] Block complete: {hex_str}")
        
        # Checksum verifizieren (über bytes [4..end])
        checksum_data = bytes(block_data[4:])
        calc_add, calc_cyc = self._punter_calc_checksums(checksum_data)
        
        if calc_add != additive or calc_cyc != cyclic:
            self.log(f"    CHECKSUM ERROR! " +
                    f"received: add={additive:04X} cyc={cyclic:04X}, " +
                    f"calc: add={calc_add:04X} cyc={calc_cyc:04X}")
            # Rückgabe None signalisiert dass BAD gesendet werden sollte
            return None
        
        return {
            'payload': payload,
            'next_size': next_size,
            'block_index': block_index,
            'is_last': is_last
        }
    
    def _punter_receive_end_off(self):
        """
        Empfängt die End-Off Sequenz (als Receiver)
        """
        # Warte auf ACK
        code = self._punter_wait_for_code([self.PUNTER_ACK], timeout=10)
        if code != self.PUNTER_ACK:
            return False
        
        # Sende S/B
        self._punter_send_code(self.PUNTER_SB)
        
        # Warte auf SYN
        code = self._punter_wait_for_code([self.PUNTER_SYN], timeout=10)
        if code != self.PUNTER_SYN:
            return False
        
        # Sende SYN
        self._punter_send_code(self.PUNTER_SYN)
        
        # Erwarte 3x S/B (mit Pausen) - wir ignorieren sie einfach
        # Warte 2 Sekunden wie in der Doku empfohlen
        time.sleep(2.0)
        
        # Flush buffer
        while self._read_byte(timeout=0.1) is not None:
            pass
        
        return True
    
    # ==================================================================================
    # MULTI-PUNTER SUPPORT
    # ==================================================================================
    
    def _punter_send_multi(self, filepaths, callback=None):
        """
        Punter Batch Send - Sendet mehrere Dateien
        
        Multi-File Flow:
        1. Für jede Datei:
           - Header senden: S/B filename,type CR
           - Datei mit Batch Flow senden
           - Nach Datei (außer letzte): S/B SYN / SYN S/B Handshake
        2. Am Ende: 5x $04$09 (End-Off)
        """
        import os
        
        self.log(f"\n{'='*60}")
        self.log(f"PUNTER BATCH SEND: {len(filepaths) if isinstance(filepaths, list) else 1} files")
        self.log(f"{'='*60}")
        
        if isinstance(filepaths, str):
            filepaths = [filepaths]
        
        total_files = len(filepaths)
        
        for idx, filepath in enumerate(filepaths):
            filename = os.path.basename(filepath)
            is_last_file = (idx == total_files - 1)
            
            # Datei-Typ bestimmen
            ext = os.path.splitext(filepath)[1].lower()
            ftype = 'S' if ext in ['.seq', '.txt', '.s'] else 'P'
            
            self.log(f"\n--- File {idx+1}/{total_files}: {filename} ---")
            
            # Header senden: S/B filename,type CR
            self._punter_send_file_header(filename, ftype)
            
            # Sende Datei
            if callback:
                def file_callback(sent, total, status):
                    callback(sent, total, f"[{idx+1}/{total_files}] {filename}: {status}")
            else:
                file_callback = None
            
            success = self._punter_send_after_header(filepath, file_callback)
            
            if not success:
                self.log(f"ERROR: Failed to send {filename}")
                return False
            
            self.log(f"File {idx+1}/{total_files} complete: {filename}")
            
            # Zwischen Files: S/B SYN / SYN S/B Handshake (NICHT am Ende!)
            if not is_last_file:
                self.log("\n--- Inter-file handshake: S/B SYN / SYN S/B ---")
                time.sleep(0.3)
                
                # Sende S/B
                self._punter_send_code(self.PUNTER_SB)
                
                # Warte auf SYN vom BBS
                code = self._punter_wait_for_code([self.PUNTER_SYN], timeout=10)
                if code != self.PUNTER_SYN:
                    self.log("WARNING: No SYN received, continuing anyway")
                else:
                    self.log("Got SYN from BBS")
                
                time.sleep(0.2)
                
                # Sende SYN
                self._punter_send_code(self.PUNTER_SYN)
                
                # Warte auf S/B vom BBS
                code = self._punter_wait_for_code([self.PUNTER_SB], timeout=10)
                if code == self.PUNTER_SB:
                    self.log("Got S/B from BBS - ready for next file")
                
                time.sleep(0.5)
        
        # ============================================================
        # END-OFF: 5x $04$09 senden (NUR am Ende des gesamten Batch!)
        # ============================================================
        self.log("\n--- Sending End-Off: 5x $04$09 ---")
        time.sleep(0.5)
        
        end_signal = bytes([0x04, 0x09])
        for i in range(5):
            self.send_raw(end_signal)
            self.log(f"    Sent $04$09 {i+1}/5")
            time.sleep(0.1)
        
        self.log(f"\n✓ PUNTER BATCH COMPLETE: {total_files} files sent")
        return True
    
    def _punter_send_file_header(self, filename, ftype='P'):
        """
        Sendet Punter Batch Datei-Header
        
        Format: 10× TAB + Filename + "," + Type + CR
        (Basierend auf tcpser Log: C64 sendet 0x09 0x09 ... + filename)
        
        Args:
            filename: Dateiname (max 16 Zeichen für C64)
            ftype: 'P' für PRG, 'S' für SEQ
        """
        # Dateiname auf 16 Zeichen begrenzen und bereinigen
        clean_name = filename[:16].upper()
        
        # Header bauen: 10× TAB (0x09) + Filename + "," + Type + CR (0x0D)
        header = bytearray()
        header.extend(b'\x09' * 10)  # 10× TAB
        header.extend(clean_name.encode('ascii', errors='replace'))
        header.append(ord(','))
        header.append(ord(ftype.upper()))
        header.append(0x0D)  # CR
        
        hex_str = ' '.join(f'{b:02X}' for b in header)
        self.punter_log(f"    [OUT] Header: {hex_str}")
        self.log(f"    [OUT] Header: 10xTAB + {clean_name},{ftype} + CR")
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
    
    def _punter_wait_for_file_header(self, timeout=60):
        """
        Wartet auf Multi-Punter Datei-Header vom BBS
        
        Returns:
            (filename, type) Tuple bei Erfolg
            'END' wenn End-Marker empfangen
            None bei Timeout
        """
        return self._punter_wait_for_header(timeout)
    
    def _punter_send_after_header(self, filepath, callback=None):
        """
        Punter Send NACH Header (S/B filename,type CR wurde bereits gesendet)
        
        Korrekter Flow laut Punter C1 Batch Schema:
        
        [Header bereits gesendet]
                                         <- GOO
        GOO ->
                                         <- GOO  
        ACK ->
                                         <- S/B
        8 Byte Block1 (ff 01 06 04 00 ff ff 01) ->
                                         <- GOO
        ACK ->
                                         <- S/B
        SYN ->
                                         <- SYN
        S/B ->
                                         <- GOO (Phase B)
        ACK ->
                                         <- S/B
        8 Byte Block2 (identisch: ff 01 06 04 00 ff ff 01) ->
                                         <- GOO
        [Loop für Datenblöcke]
        ACK ->
                                         <- S/B
        Datablock ->
                                         <- GOO
        [Nach letztem Block]
        ACK ->
                                         <- S/B
        SYN ->
                                         <- SYN
        S/B ->
        """
        import os
        
        self.log(f"\n--- PUNTER SEND (after header): {filepath} ---")
        
        try:
            # Datei laden
            with open(filepath, 'rb') as f:
                file_data = f.read()
            
            filesize = len(file_data)
            self.log(f"File size: {filesize} bytes")
            
            # Datei-Typ bestimmen
            ext = os.path.splitext(filepath)[1].lower()
            file_type = 1 if ext in ['.seq', '.txt', '.s'] else 0
            
            # Block-Größe: 255 Bytes total (7 Header + 248 Payload)
            block_size = 255
            payload_size = block_size - 7  # 248 bytes Nutzdaten pro Block
            
            # Standard 8-Byte Block für Phase A
            # Im Referenz-Log: c8 02 96 08 c9 ff ff 01
            # next_size = 0xC9 (201) wie im C64!
            standard_block = self._punter_make_block(
                bytes([0x01]),  # 1 Byte Payload
                0xC9,           # next_size = 201 (wie C64!)
                0xFFFF          # block_index = FFFF (last)
            )
            
            # ============================================================
            # PHASE A: File Type
            # ============================================================
            self.log("Phase A: File Type")
            
            # Korrekte Reihenfolge:
            # 1. Warte auf GOO vom BBS
            # 2. Sende GOO
            # 3. Warte auf GOO vom BBS
            # 4. Sende ACK
            
            # Warte auf GOO vom BBS (längerer Timeout)
            code = self._punter_wait_for_code([self.PUNTER_GOO], timeout=60)
            if code != self.PUNTER_GOO:
                self.log("ERROR: No GOO from BBS after header")
                return False
            
            time.sleep(0.2)  # Pause vor Antwort
            
            # Sende GOO als Antwort
            self._punter_send_code(self.PUNTER_GOO)
            
            # Warte auf zweites GOO vom BBS
            code = self._punter_wait_for_code([self.PUNTER_GOO], timeout=30)
            if code != self.PUNTER_GOO:
                self.log("ERROR: No second GOO from BBS")
                return False
            
            time.sleep(0.2)  # Pause vor Antwort
            
            # Sende ACK
            self._punter_send_code(self.PUNTER_ACK)
            
            # Warte auf S/B
            code = self._punter_wait_for_code([self.PUNTER_SB], timeout=15)
            if code != self.PUNTER_SB:
                self.log("ERROR: No S/B for Block1")
                return False
            
            time.sleep(0.2)  # Pause vor Block
            
            # Sende Block1 (8 Bytes) mit Retry bei BAD
            max_retries = 3
            for retry in range(max_retries):
                self._punter_send_block(standard_block)
                if retry == 0:
                    self.log(f"Sent Block1 (8 bytes)")
                else:
                    self.log(f"Sent Block1 (retry {retry})")
                
                # Warte auf GOO/BAD
                code = self._punter_wait_for_code([self.PUNTER_GOO, self.PUNTER_BAD], timeout=15)
                
                if code == self.PUNTER_GOO:
                    break  # Erfolg!
                elif code == self.PUNTER_BAD:
                    self.log(f"    BAD received for Block1 - resending ({retry+1}/{max_retries})")
                    time.sleep(0.2)
                    # Bei BAD: Versuche S/B oder ACK abzuwarten, dann erneut senden
                    code = self._punter_wait_for_code([self.PUNTER_SB, self.PUNTER_ACK], timeout=2)
                    if code:
                        self.log(f"    Got {code.decode()} after BAD")
                    continue
                else:
                    self.log("ERROR: No GOO/BAD for Block1 (timeout)")
                    return False
            else:
                # Alle Retries fehlgeschlagen
                self.log(f"ERROR: Max retries ({max_retries}) for Block1")
                return False
            
            time.sleep(0.2)  # Pause vor Antwort
            
            # End-Off Phase A
            self._punter_send_code(self.PUNTER_ACK)
            
            code = self._punter_wait_for_code([self.PUNTER_SB], timeout=15)
            if code != self.PUNTER_SB:
                self.log("WARNING: No S/B in end-off A")
            
            time.sleep(0.2)  # Pause vor Antwort
            
            self._punter_send_code(self.PUNTER_SYN)
            
            code = self._punter_wait_for_code([self.PUNTER_SYN], timeout=15)
            if code != self.PUNTER_SYN:
                self.log("WARNING: No SYN in end-off A")
            
            time.sleep(0.3)  # Pause vor Phase B
            
            # S/B senden um Phase B zu starten
            self._punter_send_code(self.PUNTER_SB)
            
            # ============================================================
            # PHASE B: File Data
            # ============================================================
            self.log("Phase B: File Data")
            
            # Phase B Handshake:
            # - BBS sollte jetzt mit GOO antworten
            # - Sender sammelt GOOs
            # - Nach mehreren GOOs sendet Sender ACK
            # - BBS sendet dann S/B für Block2
            
            self.log("Waiting for GOOs from BBS...")
            
            goo_count = 0
            for attempt in range(20):  # Max 20 Versuche
                # Cancel-Check
                if self.cancel_requested:
                    self.log("CANCELLED by user")
                    return False
                
                # DEBUG: Prüfe Queue und Socket bei jedem Versuch
                if hasattr(self.connection, 'receive_queue'):
                    qsize = self.connection.receive_queue.qsize()
                    if qsize > 0:
                        self.log(f"    [DEBUG] Queue has {qsize} items!")
                
                # Warte auf GOOs für 3 Sekunden
                collect_end = time.time() + 3.0
                while time.time() < collect_end:
                    if self.cancel_requested:
                        self.log("CANCELLED by user")
                        return False
                    
                    code = self._punter_wait_for_code([self.PUNTER_GOO], timeout=1.0)
                    if code == self.PUNTER_GOO:
                        goo_count += 1
                        self.log(f"    Got GOO #{goo_count}")
                
                # Nach genügend GOOs (mind. 3) können wir ACK senden
                if goo_count >= 3:
                    self.log(f"Got {goo_count} GOOs - sending ACK")
                    break
                
                # Keine GOOs bekommen - sende S/B erneut
                self.log(f"    No GOOs, sending S/B again (attempt {attempt+1}/20)")
                self._punter_send_code(self.PUNTER_SB)
                time.sleep(0.3)
            
            if goo_count < 1:
                self.log("ERROR: No GOO from BBS for Phase B")
                return False
            
            # Sende ACK
            self._punter_send_code(self.PUNTER_ACK)
            
            # Warte auf S/B
            code = self._punter_wait_for_code([self.PUNTER_SB], timeout=10)
            if code != self.PUNTER_SB:
                self.log("ERROR: No S/B for Block2")
                return False
            
            # Block2 ist NUR Header (7 Bytes), KEIN Payload!
            # Im Referenz-Log: ff 00 f8 07 ff 00 00
            # next_size = 255 (voller nächster Block), block_index = 0
            block2 = self._punter_make_block(
                b'',            # KEIN Payload!
                255,            # next_size = 255 (voller Block)
                0x0000          # block_index = 0
            )
            
            # Sende Block2 mit Retry bei BAD
            for retry in range(max_retries):
                self._punter_send_block(block2)
                if retry == 0:
                    self.log(f"Sent Block2 ({len(block2)} bytes - header only)")
                else:
                    self.log(f"Sent Block2 (retry {retry})")
                
                # Warte auf GOO/BAD
                code = self._punter_wait_for_code([self.PUNTER_GOO, self.PUNTER_BAD], timeout=10)
                
                if code == self.PUNTER_GOO:
                    break  # Erfolg!
                elif code == self.PUNTER_BAD:
                    self.log(f"    BAD received for Block2 - resending ({retry+1}/{max_retries})")
                    time.sleep(0.2)
                    # Bei BAD: Versuche S/B oder ACK abzuwarten, dann erneut senden
                    code = self._punter_wait_for_code([self.PUNTER_SB, self.PUNTER_ACK], timeout=2)
                    if code:
                        self.log(f"    Got {code.decode()} after BAD")
                    continue
                else:
                    self.log("ERROR: No GOO/BAD for Block2 (timeout)")
                    return False
            else:
                # Alle Retries fehlgeschlagen
                self.log(f"ERROR: Max retries ({max_retries}) for Block2")
                return False
            
            # Sende alle Datenblöcke
            bytes_sent = 0
            block_index = 1
            # max_retries ist bereits definiert (= 3)
            
            while bytes_sent < filesize:
                # Cancel-Check
                if self.cancel_requested:
                    self.log("CANCELLED by user")
                    return False
                
                # Sende ACK
                self._punter_send_code(self.PUNTER_ACK)
                
                time.sleep(0.1)  # Kleine Pause
                
                # Warte auf S/B
                code = self._punter_wait_for_code([self.PUNTER_SB], timeout=15)
                if code != self.PUNTER_SB:
                    self.log(f"ERROR: No S/B for datablock {block_index}")
                    return False
                
                time.sleep(0.1)  # Kleine Pause vor Block
                
                # Block-Daten
                chunk_start = bytes_sent
                chunk_end = min(bytes_sent + payload_size, filesize)
                chunk = file_data[chunk_start:chunk_end]
                
                is_last = (chunk_end >= filesize)
                
                if is_last:
                    # Letzter Block: next_size = tatsächliche Größe, index = 0xFFFF
                    next_size = len(chunk) + 7
                    blk_idx = 0xFFFF
                else:
                    # next_size für nächsten Block
                    remaining = filesize - chunk_end
                    if remaining >= payload_size:
                        next_size = 255  # Voller Block
                    else:
                        next_size = remaining + 7
                    blk_idx = block_index
                
                data_block = self._punter_make_block(chunk, next_size, blk_idx)
                
                # Sende Block mit Retry bei BAD
                for retry in range(max_retries):
                    self._punter_send_block(data_block)
                    
                    if retry == 0:
                        self.log(f"Datablock {block_index}: {len(chunk)} bytes, total {chunk_end}/{filesize}")
                    else:
                        self.log(f"Datablock {block_index}: {len(chunk)} bytes (retry {retry})")
                    
                    # Warte auf GOO/BAD
                    code = self._punter_wait_for_code([self.PUNTER_GOO, self.PUNTER_BAD], timeout=15)
                    
                    if code == self.PUNTER_GOO:
                        break  # Erfolg!
                    elif code == self.PUNTER_BAD:
                        self.log(f"    BAD received - resending block ({retry+1}/{max_retries})")
                        time.sleep(0.2)  # Pause vor Retry
                        # Bei BAD: Block direkt erneut senden (nächste Iteration)
                        # Manche BBS senden S/B, manche nicht - versuche beides
                        code = self._punter_wait_for_code([self.PUNTER_SB, self.PUNTER_ACK], timeout=2)
                        if code:
                            self.log(f"    Got {code.decode()} after BAD")
                        # Block wird in nächster Iteration erneut gesendet
                        continue
                    else:
                        self.log(f"ERROR: No GOO for datablock {block_index}")
                        return False
                else:
                    # Alle Retries fehlgeschlagen
                    self.log(f"ERROR: Max retries ({max_retries}) for block {block_index}")
                    return False
                
                bytes_sent = chunk_end
                block_index += 1
                
                if callback:
                    callback(bytes_sent, filesize, f"Block {block_index-1}")
            
            # End-Off Phase B
            time.sleep(0.2)  # Pause vor End-Off
            
            self._punter_send_code(self.PUNTER_ACK)
            
            code = self._punter_wait_for_code([self.PUNTER_SB], timeout=15)
            if code != self.PUNTER_SB:
                self.log("WARNING: No S/B in end-off B")
            
            time.sleep(0.2)  # Pause
            
            self._punter_send_code(self.PUNTER_SYN)
            
            code = self._punter_wait_for_code([self.PUNTER_SYN], timeout=15)
            if code != self.PUNTER_SYN:
                self.log("WARNING: No SYN in end-off B")
            
            time.sleep(0.2)  # Pause
            
            self._punter_send_code(self.PUNTER_SB)
            
            # KEIN End-of-Transfer Signal hier!
            # Das wird vom Aufrufer gemacht:
            # - Single File: 5x GOO in _punter_send()
            # - Multi-File: S/B SYN / SYN S/B zwischen Files, 5x GOO am Ende
            
            time.sleep(0.3)  # Pause nach End-Off
            
            self.log(f"✓ Sent {filesize} bytes")
            if callback:
                callback(filesize, filesize, "Complete!")
            
            return True
            
        except Exception as e:
            self.log(f"ERROR: {str(e)}")
            import traceback
            self.log(traceback.format_exc())
            return False
    
    def _punter_receive_multi(self, target_dir, callback=None):
        """
        Punter Batch Receive - Empfängt mehrere Dateien
        
        Batch Format:
        - Jede Datei: S/B filename,type CR
        - Kein expliziter End-Marker - Timeout = Ende
        
        Args:
            target_dir: Zielverzeichnis für empfangene Dateien
            callback: Optional callback
        
        Returns:
            Liste der empfangenen Dateipfade oder leere Liste bei Fehler
        """
        import os
        
        self.log(f"\n{'='*60}")
        self.log(f"PUNTER BATCH RECEIVE: target={target_dir}")
        self.log(f"{'='*60}")
        
        received_files = []
        file_count = 0
        
        while True:
            # Warte auf Punter Header (S/B filename,type CR)
            self.log("\nWaiting for next file header...")
            header_data = self._punter_wait_for_header(timeout=60)
            
            if header_data is None:
                self.log("No more files (timeout)")
                break
            
            filename, ftype = header_data
            self.log(f"Received header: S/B {filename},{ftype}")
            
            # Sanitize Filename - ersetze ungültige Zeichen
            safe_filename = filename.replace('/', '-').replace('\\', '-').replace(':', '-')
            safe_filename = safe_filename.replace('*', '-').replace('?', '-').replace('"', '-')
            safe_filename = safe_filename.replace('<', '-').replace('>', '-').replace('|', '-')
            if safe_filename != filename:
                self.log(f"    Sanitized filename: {filename} -> {safe_filename}")
                filename = safe_filename
            
            # Ziel-Pfad erstellen
            ext = '.SEQ' if ftype == 'S' else '.PRG'
            if not filename.upper().endswith(ext):
                if '.' not in filename:
                    filename = filename + ext
            
            filepath = os.path.join(target_dir, filename)
            
            file_count += 1
            self.log(f"\n--- File {file_count}: {filename} ---")
            
            if callback:
                def file_callback(recv, total, status):
                    callback(recv, total, f"[{file_count}] {filename}: {status}")
            else:
                file_callback = None
            
            success = self._punter_receive_after_header(filepath, file_callback)
            
            if success:
                received_files.append(filepath)
                self.log(f"File {file_count} complete: {filepath}")
            else:
                self.log(f"ERROR receiving file {file_count}: {filename}")
                # Bei Fehler abbrechen
                break
        
        self.log(f"\n✓ PUNTER BATCH RECEIVE COMPLETE: {len(received_files)} files")
        return received_files
    
    def _punter_wait_for_header(self, timeout=60):
        """
        Wartet auf Multi-Punter Header oder End-Marker
        ODER erkennt dass BBS schon im Transfer-Modus ist (GOO/ACK)
        
        Header Format: 10× TAB + Filename + "," + Type + CR
        End-Marker: 16× TAB + 16× EOT + CR
        
        Returns:
            ('filename', 'P'/'S') für Datei-Header
            'TRANSFER_MODE' wenn BBS schon im Transfer-Modus (GOO+ACK erkannt)
            'END' für End-Marker
            None bei Timeout
        """
        buffer = bytearray()
        raw_buffer = bytearray()
        tab_count = 0
        eot_count = 0
        end_time = time.time() + timeout
        
        self.log("Waiting for Multi-Punter header (10xTAB + filename,type + CR)...")
        self.log(f"    Timeout: {timeout}s")
        self.log(f"    byte_buffer already has {len(self.byte_buffer)} bytes")
        
        loop_count = 0
        last_log_time = time.time()
        
        # Für GOO/ACK Erkennung
        goo_seen = False
        ack_count = 0
        
        while time.time() < end_time:
            loop_count += 1
            
            # Log alle 2 Sekunden
            if time.time() - last_log_time >= 2.0:
                self.log(f"    [WAIT] Loop {loop_count}, raw={len(raw_buffer)}, tabs={tab_count}, goo={goo_seen}, acks={ack_count}")
                last_log_time = time.time()
            
            # Hole ein Byte - zuerst aus byte_buffer, dann aus Queue
            byte = None
            
            # Aus byte_buffer
            if len(self.byte_buffer) > 0:
                byte = self.byte_buffer.pop(0)
            else:
                # Aus Queue
                if hasattr(self.connection, 'get_received_data'):
                    data = self.connection.get_received_data(timeout=0.05)
                    if data:
                        if isinstance(data, str):
                            data = data.encode('latin-1')
                        if len(data) > 0:
                            byte = data[0]
                            if len(data) > 1:
                                self.byte_buffer.extend(data[1:])
            
            if byte is None:
                time.sleep(0.01)
                continue
            
            # Verarbeite das Byte
            raw_buffer.append(byte)
            
            # Prüfe auf GOO oder ACK (3-Byte Sequenzen)
            if len(raw_buffer) >= 3:
                last3 = bytes(raw_buffer[-3:])
                if last3 == b'GOO' and not goo_seen:
                    goo_seen = True
                    self.log(f"    [DETECTED] GOO from BBS!")
                elif last3 == b'ACK':
                    ack_count += 1
                    self.log(f"    [DETECTED] ACK #{ack_count} from BBS!")
                    
                    # Wenn GOO + ACK, ist BBS bereit für Block-Transfer!
                    if goo_seen and ack_count >= 1:
                        self.log(f"    >>> BBS is in TRANSFER_MODE (GOO + ACK received)")
                        self.log(f"    >>> Header was probably already sent before F3 was pressed")
                        return 'TRANSFER_MODE'
            
            # TAB zählen
            if byte == 0x09:
                tab_count += 1
                buffer = bytearray()  # Reset buffer nach TABs
                eot_count = 0
                continue
            
            # EOT zählen (End-Marker: 16× TAB + 16× EOT + CR)
            if byte == 0x04 and tab_count >= 10:
                eot_count += 1
                if eot_count >= 10:
                    self.log(f"    END MARKER detected!")
                    return 'END'
                continue
            
            # CR = Ende des Headers
            if byte == 0x0D and tab_count >= 10 and len(buffer) > 0:
                ascii_str = ''.join(chr(x) if 32 <= x < 127 else '.' for x in buffer)
                self.log(f"    [HEADER] Found after {tab_count} TABs!")
                self.log(f"    [HEADER] ascii: {ascii_str}")
                
                try:
                    # Verwende latin-1 statt ASCII um Decode-Fehler zu vermeiden
                    header_str = buffer.decode('latin-1')
                    
                    # Prüfe ob es ein gültiger Header ist (muss Komma enthalten)
                    if ',' in header_str:
                        parts = header_str.rsplit(',', 1)
                        filename = parts[0]
                        ftype = parts[1].upper() if len(parts) > 1 else 'P'
                        
                        # Prüfe ob Filename gültig aussieht (nicht nur Punkte/Sonderzeichen)
                        if any(c.isalnum() for c in filename):
                            self.log(f"    Header parsed: filename={filename}, type={ftype}")
                            return (filename, ftype)
                        else:
                            self.log(f"    Invalid filename (no alphanumeric chars): {filename}")
                    else:
                        self.log(f"    No comma in header - not a valid file header")
                        self.log(f"    This might be BBS screen output - assuming transfer complete")
                        return 'END'
                        
                except Exception as e:
                    self.log(f"    Header parse error: {e}")
                    self.log(f"    Assuming transfer complete (BBS returned to menu)")
                    return 'END'
                    
                buffer = bytearray()
                tab_count = 0
                continue
            
            # Normales Zeichen zum Buffer hinzufügen (nach TABs)
            if tab_count >= 10 and byte not in [0x09, 0x04, 0x0D]:
                buffer.append(byte)
                eot_count = 0
            else:
                # Noch keine 10 TABs - reset
                if byte != 0x09:
                    tab_count = 0
                    buffer = bytearray()
        
        self.log(f"    TIMEOUT waiting for header after {loop_count} loops")
        self.log(f"    Received {len(raw_buffer)} bytes total, {tab_count} consecutive TABs")
        if raw_buffer:
            hex_str = ' '.join(f'{b:02X}' for b in raw_buffer[:80])
            self.log(f"    [TIMEOUT] Buffer: {hex_str}")
        return None
    
    def _punter_receive_after_header(self, filepath, callback=None):
        """
        Punter Receive NACH Header (10×TAB + filename,type + CR wurde empfangen)
        
        Korrekter Flow laut tcpser Download-Log:
        
        [Header wurde empfangen: 10×TAB + filename,type + CR]
        GOO -> (mehrmals)                (Client initiiert!)
                                         <- GOO
        GOO ->
                                         <- ACK
        S/B ->
                                         <- Block (8 bytes)
        ...
        """
        self.log(f"\n--- PUNTER RECEIVE (after header): {filepath} ---")
        
        # Extrahiere Dateiname für Callback
        display_name = os.path.basename(filepath) if filepath else "download"
        
        try:
            # ============================================================
            # PHASE A: File Type (Block1)
            # ============================================================
            self.log("Phase A: Block1")
            
            # Sende GOOs bis BBS antwortet (wie am Transfer-Start)
            got_response = False
            for attempt in range(5):
                self._punter_send_code(self.PUNTER_GOO)
                time.sleep(0.15)
                
                # Prüfe ob Antwort da ist
                code = self._punter_wait_for_code([self.PUNTER_GOO, self.PUNTER_ACK], timeout=2)
                if code is not None:
                    self.log(f"    Got {code} after {attempt+1} GOOs")
                    got_response = True
                    break
                self.log(f"    GOO attempt {attempt+1} - no response")
            
            if not got_response:
                self.log("ERROR: No response to GOOs")
                return False
            
            if code == self.PUNTER_GOO:
                # Sende GOO zurück
                self._punter_send_code(self.PUNTER_GOO)
                
                # Warte auf ACK
                code = self._punter_wait_for_code([self.PUNTER_ACK], timeout=10)
                if code != self.PUNTER_ACK:
                    self.log("ERROR: No ACK from sender")
                    return False
            
            # Sende S/B
            self._punter_send_code(self.PUNTER_SB)
            
            # Empfange Block1 (8 Bytes) - mit Retry bei Checksum-Fehler
            max_retries = 3
            block1 = None
            for retry in range(max_retries):
                block1 = self._punter_receive_block(timeout=15)
                if block1 is not None:
                    break  # Erfolg!
                
                # Checksum-Fehler - sende BAD
                self.log(f"    BAD Block1 - sending BAD ({retry+1}/{max_retries})")
                self._punter_send_code(self.PUNTER_BAD)
                
                # Warte auf ACK und sende S/B für Retry
                code = self._punter_wait_for_code([self.PUNTER_ACK], timeout=10)
                if code == self.PUNTER_ACK:
                    self._punter_send_code(self.PUNTER_SB)
                else:
                    self.log("ERROR: No ACK after BAD for Block1")
                    break
            
            if block1 is None:
                self.log("ERROR: Failed to receive Block1 after retries")
                return False
            
            self.log(f"Block1 received: {len(block1.get('payload', b''))+7} bytes")
            
            # Sende GOO
            self._punter_send_code(self.PUNTER_GOO)
            
            # End-Off Phase A
            code = self._punter_wait_for_code([self.PUNTER_ACK], timeout=10)
            if code != self.PUNTER_ACK:
                self.log("WARNING: No ACK in end-off A")
            
            self._punter_send_code(self.PUNTER_SB)
            
            code = self._punter_wait_for_code([self.PUNTER_SYN], timeout=10)
            if code != self.PUNTER_SYN:
                self.log("WARNING: No SYN in end-off A")
            
            self._punter_send_code(self.PUNTER_SYN)
            
            code = self._punter_wait_for_code([self.PUNTER_SB], timeout=10)
            if code != self.PUNTER_SB:
                self.log("WARNING: No S/B in end-off A")
            
            # ============================================================
            # PHASE B: File Data
            # ============================================================
            self.log("Phase B: Data")
            
            # Nach S/B vom BBS: GOO-GOO-ACK Handshake
            # Sende GOOs bis Antwort
            got_response = False
            for attempt in range(5):
                self._punter_send_code(self.PUNTER_GOO)
                time.sleep(0.15)
                
                code = self._punter_wait_for_code([self.PUNTER_GOO, self.PUNTER_ACK], timeout=2)
                if code is not None:
                    self.log(f"    Phase B: Got {code} after {attempt+1} GOOs")
                    got_response = True
                    break
            
            if code == self.PUNTER_GOO:
                self._punter_send_code(self.PUNTER_GOO)
                code = self._punter_wait_for_code([self.PUNTER_ACK], timeout=10)
            
            if code != self.PUNTER_ACK:
                self.log(f"WARNING: No ACK for Phase B (got: {code})")
            
            # Sende S/B
            self._punter_send_code(self.PUNTER_SB)
            
            # Empfange Block2 (identisch: 8 Bytes) - mit Retry bei Checksum-Fehler
            block2 = None
            for retry in range(max_retries):
                block2 = self._punter_receive_block(timeout=15)
                if block2 is not None:
                    break  # Erfolg!
                
                # Checksum-Fehler - sende BAD
                self.log(f"    BAD Block2 - sending BAD ({retry+1}/{max_retries})")
                self._punter_send_code(self.PUNTER_BAD)
                
                # Warte auf ACK und sende S/B für Retry
                code = self._punter_wait_for_code([self.PUNTER_ACK], timeout=10)
                if code == self.PUNTER_ACK:
                    self._punter_send_code(self.PUNTER_SB)
                else:
                    self.log("ERROR: No ACK after BAD for Block2")
                    break
            
            if block2 is None:
                self.log("ERROR: Failed to receive Block2 after retries")
                return False
            
            self.log(f"Block2 received: {len(block2.get('payload', b''))+7} bytes, next_size={block2['next_size']}")
            
            # Sende GOO
            self._punter_send_code(self.PUNTER_GOO)
            
            # Empfange Datenblöcke
            file_data = bytearray()
            block_count = 0
            next_block_size = block2['next_size']  # Größe des ersten Datenblocks
            
            while True:
                # Warte auf ACK
                code = self._punter_wait_for_code([self.PUNTER_ACK], timeout=10)
                if code is None:
                    self.log("No ACK - checking for end-off")
                    break
                
                # Sende S/B
                self._punter_send_code(self.PUNTER_SB)
                
                # Empfange Block mit erwarteter Größe - mit Retry bei Checksum-Fehler
                max_retries = 3
                data_block = None
                for retry in range(max_retries):
                    data_block = self._punter_receive_block(timeout=20, expected_size=next_block_size)
                    if data_block is not None:
                        break  # Erfolg!
                    
                    # Checksum-Fehler - sende BAD und warte auf erneutes S/B
                    self.log(f"    BAD block - sending BAD ({retry+1}/{max_retries})")
                    self._punter_send_code(self.PUNTER_BAD)
                    
                    # Warte auf ACK + S/B für Retry
                    code = self._punter_wait_for_code([self.PUNTER_ACK], timeout=10)
                    if code == self.PUNTER_ACK:
                        self._punter_send_code(self.PUNTER_SB)
                    else:
                        self.log("ERROR: No ACK after BAD")
                        break
                
                if data_block is None:
                    self.log("ERROR: Failed to receive data block after retries")
                    break
                
                # Speichere next_size für nächsten Block
                next_block_size = data_block['next_size']
                
                if data_block['payload']:
                    file_data.extend(data_block['payload'])
                block_count += 1
                
                self.log(f"Datablock {block_count}: {len(data_block['payload']) if data_block['payload'] else 0} bytes, " +
                        f"total {len(file_data)}, is_last={data_block['is_last']}")
                
                if callback:
                    callback(len(file_data), 0, f"{display_name}: Block {block_count}")
                
                # Sende GOO
                self._punter_send_code(self.PUNTER_GOO)
                
                if data_block['is_last']:
                    break
            
            # End-Off Phase B
            code = self._punter_wait_for_code([self.PUNTER_ACK], timeout=10)
            if code == self.PUNTER_ACK:
                self._punter_send_code(self.PUNTER_SB)
                
                code = self._punter_wait_for_code([self.PUNTER_SYN], timeout=10)
                if code == self.PUNTER_SYN:
                    self._punter_send_code(self.PUNTER_SYN)
                    
                    code = self._punter_wait_for_code([self.PUNTER_SB], timeout=10)
            
            # Datei speichern
            if len(file_data) > 0:
                with open(filepath, 'wb') as f:
                    f.write(file_data)
                
                self.log(f"✓ Received {len(file_data)} bytes -> {filepath}")
                if callback:
                    # Sende FILE_COMPLETE Event für Dateiliste
                    callback(len(file_data), len(file_data), 
                            f"FILE_COMPLETE:{display_name}:{block_count}:{len(file_data)}")
                    callback(len(file_data), len(file_data), f"{display_name}: Complete!")
                return True
            else:
                self.log("ERROR: No data received")
                return False
            
        except Exception as e:
            self.log(f"ERROR: {str(e)}")
            import traceback
            self.log(traceback.format_exc())
            return False

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
                # Single-File: send_file() verwenden
                success = turbo.send_file(filepath, callback)
                
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
