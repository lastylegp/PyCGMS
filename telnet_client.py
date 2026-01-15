"""
Telnet Client für BBS-Verbindungen
Unterstützt PETSCII-Encoding und asynchrone Kommunikation
"""

import socket
import threading
import time
from queue import Queue, Empty

# Global debug flag - can be set from outside
_TELNET_DEBUG = False

def set_telnet_debug(enabled):
    """Set debug mode for telnet client"""
    global _TELNET_DEBUG
    _TELNET_DEBUG = enabled

def _debug_print(*args, **kwargs):
    """Print only if debug mode is enabled"""
    if _TELNET_DEBUG:
        print(*args, **kwargs)


class BBSTelnetClient:
    """
    Telnet Client speziell für C64 BBS-Systeme
    """
    
    # Class-level traffic logger
    _traffic_log_file = None
    _traffic_logging = False
    
    @classmethod
    def start_raw_traffic_log(cls, filename=None):
        """Startet Raw Traffic Logging auf Class-Level"""
        import datetime
        if filename is None:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"raw_traffic_{timestamp}.log"
        try:
            cls._traffic_log_file = open(filename, 'w', encoding='utf-8', buffering=1)
            cls._traffic_logging = True
            cls._traffic_log_file.write(f"{'='*70}\n")
            cls._traffic_log_file.write(f"RAW TRAFFIC LOG - {datetime.datetime.now()}\n")
            cls._traffic_log_file.write(f"{'='*70}\n\n")
            _debug_print(f"[RAW TRAFFIC LOG] Started: {filename}")
            return True
        except Exception as e:
            _debug_print(f"[RAW TRAFFIC LOG] Failed to start: {e}")
            return False
    
    @classmethod
    def stop_raw_traffic_log(cls):
        """Stoppt Raw Traffic Logging"""
        if cls._traffic_log_file:
            try:
                cls._traffic_log_file.write(f"\n{'='*70}\n")
                cls._traffic_log_file.write(f"RAW TRAFFIC LOG STOPPED\n")
                cls._traffic_log_file.write(f"{'='*70}\n")
                cls._traffic_log_file.close()
                _debug_print(f"[RAW TRAFFIC LOG] Stopped")
            except:
                pass
            cls._traffic_log_file = None
        cls._traffic_logging = False
    
    @classmethod
    def _log_traffic(cls, direction, data):
        """Loggt Traffic"""
        if not cls._traffic_logging or not cls._traffic_log_file:
            return
        try:
            import datetime
            timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            
            if isinstance(data, (bytes, bytearray)):
                hex_str = ' '.join(f'{b:02X}' for b in data)
                ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data)
            else:
                hex_str = f'{data:02X}'
                ascii_str = chr(data) if 32 <= data < 127 else '.'
            
            arrow = ">>>" if direction == "SEND" else "<<<"
            cls._traffic_log_file.write(f"[{timestamp}] {direction} {arrow} {len(data) if isinstance(data, (bytes, bytearray)) else 1} bytes\n")
            cls._traffic_log_file.write(f"    HEX: {hex_str}\n")
            cls._traffic_log_file.write(f"    ASC: {ascii_str}\n\n")
            cls._traffic_log_file.flush()
        except Exception as e:
            _debug_print(f"[RAW TRAFFIC LOG] Error: {e}")
    
    def __init__(self, host, port, encoding='petscii'):
        self.host = host
        self.port = port
        self.encoding = encoding
        self.socket = None
        self.connected = False
        self.receive_thread = None
        self.receive_queue = Queue()
        self.read_buffer = bytearray()  # Buffer für partial reads (xmodem)
        self.running = False
        
    def connect(self):
        """Verbindet zum BBS"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            
            # === SOCKET OPTIMIERUNGEN (wichtig für Windows!) ===
            # TCP_NODELAY: Deaktiviert Nagle-Algorithm (verhindert Verzögerungen)
            # Besonders wichtig für kleine Pakete bei Protokoll-Handshakes
            try:
                self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except (AttributeError, OSError):
                pass  # Nicht auf allen Plattformen verfügbar
            
            # Größere Send/Receive Buffer (64KB statt Default ~8KB)
            # Hilft bei schnellen Transfers
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
            except (AttributeError, OSError):
                pass
            
            # SO_KEEPALIVE: Erkennt tote Verbindungen
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except (AttributeError, OSError):
                pass
            
            self.socket.settimeout(10.0)
            self.socket.connect((self.host, self.port))
            self.socket.settimeout(None)  # Blocking mode für receive
            self.connected = True
            self.running = True
            
            # Starte Empfangs-Thread
            self.receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self.receive_thread.start()
            
            return True
            
        except socket.timeout:
            raise ConnectionError(f"Timeout beim Verbinden zu {self.host}:{self.port}")
        except socket.error as e:
            raise ConnectionError(f"Verbindungsfehler: {e}")
            
    def disconnect(self):
        """Trennt die Verbindung"""
        self.running = False
        self.connected = False
        
        if self.socket:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except:
                pass
            try:
                self.socket.close()
            except:
                pass
            self.socket = None
            
        if self.receive_thread and self.receive_thread.is_alive():
            self.receive_thread.join(timeout=2.0)
            
    def send_byte(self, byte_val):
        """Sendet ein einzelnes Byte"""
        if not self.connected or not self.socket:
            return False
            
        try:
            data = bytes([byte_val])
            BBSTelnetClient._log_traffic("SEND", data)
            self.socket.send(data)
            return True
        except socket.error:
            self.connected = False
            return False
            
    def send_bytes(self, data):
        """Sendet mehrere Bytes"""
        if not self.connected or not self.socket:
            return False
            
        try:
            if isinstance(data, str):
                data = data.encode('latin-1')
            BBSTelnetClient._log_traffic("SEND", data)
            self.socket.sendall(data)
            return True
        except socket.error:
            self.connected = False
            return False
            
    def send_petscii_string(self, text):
        """
        Sendet einen Text-String als PETSCII
        Konvertiert ASCII -> PETSCII
        """
        petscii_data = bytearray()
        
        for char in text:
            code = ord(char)
            
            # ASCII -> PETSCII Konvertierung
            if char == '\n':
                petscii_data.append(0x0D)  # CR
            elif 0x20 <= code <= 0x5F:  # Druckbare ASCII
                petscii_data.append(code)
            elif 0x61 <= code <= 0x7A:  # Lowercase a-z
                petscii_data.append(code - 0x20)  # -> Uppercase in PETSCII upper mode
            else:
                petscii_data.append(0x3F)  # '?' für unbekannte Zeichen
                
        return self.send_bytes(petscii_data)
        
    def send_key(self, key_code):
        """
        Sendet einen Tastendruck als PETSCII
        
        Args:
            key_code: PETSCII key code (z.B. 0x0D für RETURN)
        """
        return self.send_byte(key_code)
    
    def send_raw(self, data):
        """
        Sendet rohe Bytes ohne jegliche Konvertierung
        Für File-Transfer (XModem etc.)
        
        Args:
            data: bytes oder bytearray
        """
        if not self.connected:
            _debug_print(f"[SEND_RAW] ERROR: Not connected!")
            return False
        try:
            data_bytes = bytes(data)
            
            # DEBUG: Zeige was gesendet wird
            if len(data_bytes) <= 20:
                hex_str = ' '.join(f'{b:02X}' for b in data_bytes)
                _debug_print(f"[SEND_RAW] {len(data_bytes)} bytes: {hex_str}")
            else:
                hex_str = ' '.join(f'{b:02X}' for b in data_bytes[:20])
                _debug_print(f"[SEND_RAW] {len(data_bytes)} bytes: {hex_str}...")
            
            # Log to raw traffic file
            BBSTelnetClient._log_traffic("SEND", data_bytes)
            
            self.socket.sendall(data_bytes)
            _debug_print(f"[SEND_RAW] OK - sent to socket")
            return True
        except Exception as e:
            _debug_print(f"[SEND_RAW] ERROR: {e}")
            return False
        
    def _receive_loop(self):
        """Thread-Loop zum Empfangen von Daten"""
        buffer = bytearray()
        
        while self.running and self.connected:
            try:
                # Empfange Daten (blockierend, aber mit timeout)
                self.socket.settimeout(0.5)
                # 16KB Buffer - Sweet Spot für XModem (12x 1K Blocks oder 125x 128B Blocks)
                data = self.socket.recv(16384)
                self.socket.settimeout(None)
                
                if not data:
                    # Verbindung geschlossen
                    self.connected = False
                    break
                
                # DEBUG: Log empfangene Daten
                if len(data) <= 20:
                    hex_str = ' '.join(f'{b:02X}' for b in data)
                    _debug_print(f"[RECV_LOOP] {len(data)} bytes: {hex_str}")
                else:
                    hex_str = ' '.join(f'{b:02X}' for b in data[:20])
                    _debug_print(f"[RECV_LOOP] {len(data)} bytes: {hex_str}...")
                
                # Log empfangene Daten
                BBSTelnetClient._log_traffic("RECV", data)
                
                # DEAKTIVIERT: IAC De-Escaping zum Testen
                # Die meisten BBS senden Raw-Daten ohne Telnet-Escaping
                # Falls nötig, kann es später wieder aktiviert werden
                
                # Daten in Queue für Verarbeitung (OHNE De-Escaping)
                buffer.extend(data)
                
                # Verarbeite komplette Buffer-Chunks
                if len(buffer) > 0:
                    self.receive_queue.put(bytes(buffer))
                    _debug_print(f"[RECV_LOOP] Put {len(buffer)} bytes in queue, queue size now: {self.receive_queue.qsize()}")
                    buffer.clear()
                    
            except socket.timeout:
                # Normal, einfach weiter
                continue
            except socket.error as e:
                self.connected = False
                break
                
    def get_received_data(self, timeout=0.01):
        """
        Holt empfangene Daten aus der Queue
        
        Args:
            timeout: Wartezeit in Sekunden
            
        Returns:
            bytes oder None
        """
        try:
            data = self.receive_queue.get(timeout=timeout)
            if data:
                # DEBUG: Zeige dass Daten aus Queue geholt wurden
                hex_str = ' '.join(f'{b:02X}' for b in data[:10]) if isinstance(data, bytes) else str(data)[:20]
                _debug_print(f"[GET_DATA] Got {len(data) if isinstance(data, bytes) else '?'} bytes from queue: {hex_str}")
            return data
        except Empty:
            return None
    
    def get_received_data_raw(self, size, timeout=3.0):
        """
        Holt exakt 'size' Bytes - für xmodem Library
        Blockiert bis alle Bytes da sind oder Timeout
        
        Args:
            size: Anzahl Bytes
            timeout: Timeout in Sekunden
            
        Returns:
            bytes oder None
        """
        import time
        start_time = time.time()
        
        # Fülle Buffer bis wir genug haben
        while len(self.read_buffer) < size:
            remaining_time = timeout - (time.time() - start_time)
            if remaining_time <= 0:
                # Timeout - gebe zurück was wir haben (oder None)
                if len(self.read_buffer) > 0:
                    result = bytes(self.read_buffer[:size])
                    self.read_buffer = self.read_buffer[size:]
                    return result
                return None
            
            try:
                # Hole Daten aus Queue mit kleinem Timeout
                data = self.receive_queue.get(timeout=min(0.01, remaining_time))
                if data:
                    self.read_buffer.extend(data)
            except Empty:
                # CRITICAL FIX für VM Performance!
                # Ohne sleep(): Busy-Wait = 100% CPU = VM Overhead
                # Mit sleep(): CPU wird freigegeben = viel schneller!
                time.sleep(0.001)  # 1ms sleep gibt CPU frei
                continue
        
        # Wir haben genug Bytes - gebe exakt 'size' zurück
        result = bytes(self.read_buffer[:size])
        self.read_buffer = self.read_buffer[size:]
        return result
            
    def has_received_data(self):
        """Prüft ob Daten empfangen wurden"""
        return not self.receive_queue.empty()
    
    def clear_receive_buffer(self):
        """Leert den Empfangsbuffer und read_buffer - wichtig vor Transfers!"""
        # DEBUG: Zeige was geleert wird
        queue_count = 0
        while not self.receive_queue.empty():
            try:
                data = self.receive_queue.get_nowait()
                queue_count += 1
                if data:
                    hex_str = ' '.join(f'{b:02X}' for b in data[:20]) if isinstance(data, bytes) else str(data)[:40]
                    _debug_print(f"[CLEAR] Discarding from queue: {hex_str}")
            except Empty:
                break
        
        # Leere read_buffer
        if len(self.read_buffer) > 0:
            hex_str = ' '.join(f'{b:02X}' for b in self.read_buffer[:20])
            _debug_print(f"[CLEAR] Discarding from read_buffer: {hex_str}")
        self.read_buffer.clear()
        
        _debug_print(f"[Client] Receive buffer cleared: {queue_count} queue items, read_buffer cleared")
    
    def settimeout(self, timeout):
        """Socket timeout setzen (für xmodem library Kompatibilität)"""
        # Wir nutzen Queue-based System, also ignorieren wir das
        # Die Timeouts werden in get_received_data_raw() gehandhabt
        pass
        
    def is_connected(self):
        """Prüft ob verbunden"""
        return self.connected


class BBSConnection:
    """
    High-level BBS Connection Handler
    Kombiniert Telnet Client mit PETSCII Parser
    """
    
    def __init__(self, config, parser):
        """
        Args:
            config: BBS config dict mit 'host', 'port', 'encoding'
            parser: PETSCIIParser Instanz
        """
        self.config = config
        self.parser = parser
        self.client = BBSTelnetClient(
            config['host'],
            config['port'],
            config.get('encoding', 'petscii')
        )
        
    def connect(self):
        """Verbindet zum BBS"""
        return self.client.connect()
        
    def disconnect(self):
        """Trennt Verbindung"""
        self.client.disconnect()
        
    def send_text(self, text):
        """Sendet Text zum BBS"""
        return self.client.send_petscii_string(text)
        
    def send_key(self, key_code):
        """Sendet Tastendruck"""
        return self.client.send_key(key_code)
    
    def send_raw(self, data):
        """Sendet rohe Bytes ohne PETSCII-Konvertierung (für File Transfer)"""
        if isinstance(data, int):
            data = bytes([data])
        elif isinstance(data, (list, bytearray)):
            data = bytes(data)
        return self.client.send_raw(data)
        
    def update(self):
        """
        Update-Loop: Verarbeitet empfangene Daten
        Sollte regelmäßig aufgerufen werden (z.B. in Main Loop)
        """
        while self.client.has_received_data():
            data = self.client.get_received_data()
            if data:
                self.parser.parse_bytes(data)
                
    def is_connected(self):
        """Prüft Verbindungsstatus"""
        return self.client.is_connected()


if __name__ == "__main__":
    # Test
    from petscii_parser import PETSCIIScreenBuffer, PETSCIIParser
    
    screen = PETSCIIScreenBuffer()
    parser = PETSCIIParser(screen)
    
    config = {
        'host': 'cottonwoodbbs.dyndns.org',
        'port': 6502,
        'encoding': 'petscii'
    }
    
    bbs = BBSConnection(config, parser)
    
    try:
        print(f"Verbinde zu {config['host']}:{config['port']}...")
        if bbs.connect():
            print("Verbunden!")
            
            # 5 Sekunden Daten empfangen
            for _ in range(50):
                bbs.update()
                time.sleep(0.1)
                
            print("\nEmpfangener Screen:")
            print(screen.get_screen_text())
            
    except Exception as e:
        print(f"Fehler: {e}")
    finally:
        bbs.disconnect()
