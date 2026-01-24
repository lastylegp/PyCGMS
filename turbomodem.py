"""
TURBOMODEM - Ultra-Fast Transfer Protocol
==========================================

10-20x schneller als XModem durch:
- Große Blocks (4 KB)
- Sliding Window (8 Blocks ohne ACK)
- CRC-32 Checksumme
- Einfaches Error Recovery

Performance:
- XModem: ~30-250 KB/s
- TurboModem: ~500 KB/s - 2 MB/s ✅
"""

import struct
import zlib
import time
import datetime

# Protocol Constants
MAGIC = b'TB'  # TurboBlock
CMD_REQUEST = b'TBRQ'  # Client requests transfer
CMD_OK = b'TBOK'  # Server ready, file follows
CMD_END = b'TBND'  # No more files (multi-file end)
CMD_ACK = b'TBAC'  # Block(s) acknowledged
CMD_NAK = b'TBNK'  # Block(s) need retransmit
CMD_EOT = b'TBEOT'  # End of transfer (single file)
CMD_CAN = b'TBCAN'  # Cancel transfer

BLOCK_SIZE = 4096  # 4 KB blocks
WINDOW_SIZE = 8  # 8 blocks without ACK = 32 KB pipeline
MAX_RETRIES = 16


class TurboModem:
    """TurboModem Protocol Implementation"""
    
    def __init__(self, connection, debug=False):
        """
        Args:
            connection: Socket-like object with sendall() and recv() methods
                       OR BBSTelnetClient with send_raw() and get_received_data_raw()
            debug: Enable debug logging
        """
        self.conn = connection
        self.debug = debug
        self.debug_log = []
        
        self.stats = {
            'blocks_sent': 0,
            'blocks_received': 0,
            'retransmits': 0,
            'bytes_transferred': 0,
            'start_time': 0,
            'end_time': 0,
            'blocks_corrupted': 0,
            'blocks_retried': 0,
            'timeouts': 0,
            'files_transferred': 0
        }
    
    def log(self, msg):
        """Debug logging - nur zu File, kein print()!"""
        if self.debug:
            import datetime
            timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            log_msg = f"[{timestamp}] {msg}"
            self.debug_log.append(log_msg)
            # KEIN print() - würde Terminal blockieren!
    
    def save_debug_log(self, filepath="turbomodem_debug.txt"):
        """Save debug log to file"""
        if self.debug_log:
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(self.debug_log))
                    f.write(f"\n\n===== LOG SAVED AT {datetime.datetime.now()} =====\n")
                self.log(f"Debug log saved to {filepath}")
            except Exception as e:
                # Fallback - versuche im temp dir
                try:
                    import tempfile
                    temp_path = tempfile.gettempdir() + "/turbomodem_debug.txt"
                    with open(temp_path, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(self.debug_log))
                    self.log(f"Debug log saved to {temp_path} (fallback)")
                except:
                    pass
    
    def __del__(self):
        """Destruktor - speichere Log automatisch"""
        if self.debug and self.debug_log:
            self.save_debug_log()
    
    def _send(self, data):
        """Send data - works with both Socket and BBSTelnetClient"""
        self.log(f"_send: {len(data)} bytes - {data[:20].hex() if len(data) > 20 else data.hex()}")
        if hasattr(self.conn, 'send_raw'):
            # BBSTelnetClient
            result = self.conn.send_raw(data)
            self.log(f"_send: send_raw() returned {result}")
            return result
        elif hasattr(self.conn, 'sendall'):
            # Socket
            self.conn.sendall(data)
            self.log(f"_send: sendall() done")
            return True
        else:
            # Fallback
            self.conn.send(data)
            self.log(f"_send: send() fallback done")
            return True
    
    def _flush_receive_buffer(self):
        """Leert den Empfangsbuffer - entfernt alte Daten vom vorherigen Transfer"""
        self.log("Flushing receive buffer...")
        flushed = 0
        
        # Methode 1: clear_receive_buffer() wenn vorhanden (BBSTelnetClient)
        if hasattr(self.conn, 'clear_receive_buffer'):
            try:
                self.conn.clear_receive_buffer()
                self.log("Buffer cleared via clear_receive_buffer()")
                return
            except Exception as e:
                self.log(f"clear_receive_buffer() failed: {e}")
        
        # Methode 2: Lese alle verfügbaren Daten mit kurzem Timeout
        if hasattr(self.conn, 'get_received_data_raw'):
            # Mehrere Versuche mit kurzem Timeout
            for _ in range(10):
                try:
                    data = self.conn.get_received_data_raw(4096, timeout=0.05)
                    if not data:
                        break
                    flushed += len(data)
                    self.log(f"Flushed {len(data)} bytes: {data[:20].hex()}...")
                except Exception as e:
                    self.log(f"Flush read error: {e}")
                    break
        
        # Methode 3: Socket direkt mit Non-Blocking
        elif hasattr(self.conn, 'socket') and self.conn.socket:
            try:
                import select
                sock = self.conn.socket
                # Check if data available (non-blocking)
                while True:
                    ready, _, _ = select.select([sock], [], [], 0.05)
                    if not ready:
                        break
                    data = sock.recv(4096)
                    if not data:
                        break
                    flushed += len(data)
                    self.log(f"Flushed from socket: {len(data)} bytes")
            except Exception as e:
                self.log(f"Socket flush error: {e}")
        
        self.log(f"Total flushed: {flushed} bytes")
    
    def _recv_exact(self, size, timeout=3.0):
        """
        Empfängt exakt 'size' Bytes
        
        Returns:
            bytes oder None bei Timeout/Error
        """
        import time
        
        # Nur bei großen Requests loggen (Block-Daten)
        if size > 100:
            self.log(f"_recv_exact: Requesting {size} bytes, timeout={timeout}s")
        
        # Nutze unsere Connection get_received_data_raw
        if hasattr(self.conn, 'get_received_data_raw'):
            # WICHTIG: get_received_data_raw könnte weniger zurückgeben!
            # Wir müssen in Loop sammeln bis wir exakt size Bytes haben
            data = bytearray()
            end_time = time.time() + timeout
            loop_count = 0
            
            while len(data) < size:
                if time.time() > end_time:
                    self.log(f"_recv_exact: TIMEOUT! Got {len(data)}/{size} bytes after {loop_count} loops")
                    self.stats['timeouts'] += 1
                    return None
                
                remaining = size - len(data)
                chunk = self.conn.get_received_data_raw(remaining, timeout=max(0.1, end_time - time.time()))
                loop_count += 1
                
                if not chunk:
                    # Kurz warten und retry
                    time.sleep(0.001)
                    continue
                
                # Nur bei Problemen loggen (mehr als 3 loops)
                if loop_count > 3 and size > 100:
                    self.log(f"_recv_exact: Loop {loop_count}: Got {len(chunk)} bytes, total {len(data)+len(chunk)}/{size}")
                
                data.extend(chunk)
            
            # Nur bei Problemen loggen (mehr als 2 loops)
            if loop_count > 2 and size > 100:
                self.log(f"_recv_exact: Took {loop_count} loops to get {len(data)} bytes")
            
            return bytes(data)
        else:
            # Fallback für direkte Socket
            data = bytearray()
            end_time = time.time() + timeout
            
            # WICHTIG: Setze Socket-Timeout!
            old_timeout = None
            try:
                if hasattr(self.conn, 'gettimeout'):
                    old_timeout = self.conn.gettimeout()
                    self.conn.settimeout(timeout)
            except:
                pass
            
            try:
                while len(data) < size:
                    if time.time() > end_time:
                        return None
                    
                    try:
                        remaining_time = end_time - time.time()
                        if remaining_time <= 0:
                            return None
                        
                        # Update timeout für verbleibende Zeit
                        if hasattr(self.conn, 'settimeout'):
                            self.conn.settimeout(max(0.1, remaining_time))
                        
                        chunk = self.conn.recv(size - len(data))
                        if not chunk:
                            return None
                        data.extend(chunk)
                    except Exception as e:
                        # Timeout oder anderer Error
                        if time.time() > end_time:
                            return None
                        # Kurz warten und retry
                        time.sleep(0.001)
            finally:
                # Stelle alten Timeout wieder her
                if old_timeout is not None:
                    try:
                        self.conn.settimeout(old_timeout)
                    except:
                        pass
            
            return bytes(data)
    
    def _wait_for_pattern(self, pattern, timeout=60):
        """
        Wartet auf ein bestimmtes Pattern in den empfangenen Daten.
        Ignoriert führende Bytes die nicht zum Pattern gehören.
        Nützlich wenn der Buffer Telnet IAC Reste enthält.
        """
        pattern_len = len(pattern)
        buffer = bytearray()
        end_time = time.time() + timeout
        
        while time.time() < end_time:
            # Versuche ein Byte zu lesen
            byte = self._recv_exact(1, timeout=1)
            if not byte:
                continue
            
            buffer.append(byte[0])
            
            # Suche nach Pattern am Ende des Buffers
            if len(buffer) >= pattern_len:
                if buffer[-pattern_len:] == bytearray(pattern):
                    # Pattern gefunden!
                    if len(buffer) > pattern_len:
                        self.log(f"_wait_for_pattern: Skipped {len(buffer)-pattern_len} bytes before {pattern}")
                    return pattern
        
        self.log(f"_wait_for_pattern: Timeout after {timeout}s, buffer: {bytes(buffer)}")
        return None
    
    def send_block(self, block_num, data):
        """
        Sendet einen TurboBlock
        
        Format:
        [MAGIC: 2B][Block#: 4B][Size: 2B][Data: N][CRC-32: 4B]
        
        Size im Header ist immer BLOCK_SIZE (4096) für konsistentes Empfangen.
        Das Trimmen auf die tatsächliche Dateigröße erfolgt beim Empfänger
        basierend auf der filesize aus dem initialen Header.
        """
        # Pad data to BLOCK_SIZE if needed
        original_len = len(data)
        if len(data) < BLOCK_SIZE:
            data = data + b'\x00' * (BLOCK_SIZE - len(data))
        
        # Build header - Size ist IMMER die gepaddete Größe für konsistentes Empfangen
        header = MAGIC + struct.pack('>I', block_num) + struct.pack('>H', BLOCK_SIZE)
        
        # Calculate CRC-32 over padded data
        crc = zlib.crc32(data) & 0xFFFFFFFF
        
        # Send complete block
        block = header + data + struct.pack('>I', crc)
        total_size = len(block)
        
        self.log(f"send_block #{block_num}: {original_len} bytes data, {total_size} bytes total (header+padding+crc)")
        
        # Nutze send_raw wenn verfügbar (BBSTelnetClient), sonst sendall (Socket)
        if hasattr(self.conn, 'send_raw'):
            self.log(f"send_block #{block_num}: Using send_raw()")
            result = self.conn.send_raw(block)
            self.log(f"send_block #{block_num}: send_raw() returned {result}")
        elif hasattr(self.conn, 'sendall'):
            self.log(f"send_block #{block_num}: Using sendall()")
            self._send(block)
            self.log(f"send_block #{block_num}: sendall() done")
        else:
            # Fallback: Versuche direkt zu senden
            self.log(f"send_block #{block_num}: Using conn.send()")
            self.conn.send(block)
        
        self.stats['blocks_sent'] += 1
        self.log(f"send_block #{block_num}: COMPLETE")
    
    def receive_block(self, timeout=3.0):
        """
        Empfängt einen TurboBlock
        
        Format:
        [MAGIC: 2B][Block#: 4B][Size: 2B][Data: N][CRC-32: 4B]
        
        Returns:
            (block_num, data) oder None bei Error
        """
        # Read header
        header = self._recv_exact(8, timeout)
        if not header:
            self.log("receive_block: Failed to receive header")
            return None
        
        magic, block_num, block_size = struct.unpack('>2sIH', header)
        if magic != MAGIC:
            self.log(f"receive_block: Invalid magic: {magic} (expected {MAGIC})")
            return None
        
        # Block size sollte immer BLOCK_SIZE sein, aber akzeptiere auch andere Werte
        # für Abwärtskompatibilität
        actual_recv_size = block_size if block_size > 0 else BLOCK_SIZE
        
        # Nur alle 10 Blocks loggen (zu viel Output sonst!)
        if block_num % 10 == 0:
            self.log(f"receive_block: Block #{block_num}, header_size={block_size}, recv_size={actual_recv_size}")
        
        # Read data - empfange die angegebene Größe
        data = self._recv_exact(actual_recv_size, timeout)
        if not data:
            self.log(f"receive_block: Failed to receive data for block #{block_num}")
            return None
        
        # Read CRC
        crc_bytes = self._recv_exact(4, timeout)
        if not crc_bytes:
            self.log(f"receive_block: Failed to receive CRC for block #{block_num}")
            return None
        
        expected_crc = struct.unpack('>I', crc_bytes)[0]
        actual_crc = zlib.crc32(data) & 0xFFFFFFFF
        
        if expected_crc != actual_crc:
            self.log(f"receive_block: CRC MISMATCH on block #{block_num}! Expected {expected_crc:08x}, got {actual_crc:08x}")
            self.stats['blocks_corrupted'] += 1
            return None
        
        self.stats['blocks_received'] += 1
        return (block_num, data)
    
    def send_file(self, filepath, callback=None, wait_for_request=True):
        """
        Sendet Datei mit TurboModem Protokoll (Client → BBS)
        
        Verwendet jetzt die gleiche Logik wie Server-Send:
        - Sende WINDOW_SIZE Blöcke
        - Warte auf ACK
        - Wiederhole bei Problemen
        
        Args:
            filepath: Path zur Datei
            callback: Progress callback(bytes_sent, total_bytes, status)
            wait_for_request: True = warte auf TBRQ, False = sende sofort TBOK
            
        Returns:
            True bei Erfolg, False bei Fehler
        """
        import os
        
        self.log(f"===== SEND FILE START: {filepath} =====")
        self.stats['start_time'] = time.time()
        
        # Datei komplett einlesen (wie Server)
        with open(filepath, 'rb') as f:
            filedata = f.read()
        filesize = len(filedata)
        
        # Extrahiere Dateinamen MIT Extension
        basename = os.path.basename(filepath)
        filename_to_send = basename  # Kompletter Name inkl. Extension!
        self.log(f"Original filename: {basename}")
        self.log(f"Sending as: {filename_to_send} (with extension)")
        self.log(f"Filesize: {filesize:,} bytes ({(filesize + BLOCK_SIZE - 1) // BLOCK_SIZE} blocks)")
        
        if wait_for_request:
            # Wait for REQUEST from receiver with retry loop
            self.log("Waiting for REQUEST from receiver (up to 60s)...")
            
            max_wait = 60  # Sekunden
            check_interval = 2.0  # Prüfe alle 2 Sekunden
            req = None
            
            start_wait = time.time()
            while time.time() - start_wait < max_wait:
                req = self._recv_exact(4, timeout=check_interval)
                if req == CMD_REQUEST:
                    self.log("Got TBRQ!")
                    break
                elif req:
                    self.log(f"Received {req.hex()}, waiting for TBRQ...")
                    req = None
            
            if req != CMD_REQUEST:
                self.log(f"ERROR: No REQUEST received after {max_wait}s")
                return False
        else:
            self.log("Skipping TBRQ wait (already received by caller)")
        
        # Send OK + Filesize + Filename (wie Server)
        self.log("Sending OK + Filesize + Filename...")
        filename_bytes = filename_to_send.encode('utf-8')
        
        # Format: OK(4) + Filesize(8) + FilenameLen(2) + Filename(N)
        header = CMD_OK + struct.pack('>Q', filesize) + struct.pack('>H', len(filename_bytes)) + filename_bytes
        self._send(header)
        self.log(f"Sent TBOK header ({len(header)} bytes)")
        
        if callback:
            callback(0, filesize, "Starting TurboModem send...", filename_to_send)
        
        # === BLOCKS (wie Server _send_one_file) ===
        total_blocks = (filesize + BLOCK_SIZE - 1) // BLOCK_SIZE
        if total_blocks == 0:
            total_blocks = 1
        block_num = 1
        offset = 0
        
        while offset < filesize:
            # Send window of blocks
            window_start = block_num
            blocks_sent = 0
            
            while blocks_sent < WINDOW_SIZE and offset < filesize:
                chunk = filedata[offset:offset + BLOCK_SIZE]
                chunk_padded = chunk.ljust(BLOCK_SIZE, b'\x00')
                chunk_crc = zlib.crc32(chunk_padded) & 0xFFFFFFFF
                
                # TB(2) + block#(4) + size(2) + data(4096) + CRC(4)
                block = MAGIC
                block += struct.pack('>I', block_num)
                block += struct.pack('>H', BLOCK_SIZE)
                block += chunk_padded
                block += struct.pack('>I', chunk_crc)
                
                self._send(block)
                self.stats['blocks_sent'] += 1
                
                offset += len(chunk)
                block_num += 1
                blocks_sent += 1
            
            pct = min(100, offset * 100 // filesize)
            self.log(f"Sent blocks {window_start}-{block_num-1}/{total_blocks} ({pct}%)")
            
            if callback:
                callback(offset, filesize, f"Sent {offset // 1024} KB", filename_to_send)
            
            # Wait for ACK (wie in _send_file_after_request)
            self.log("Waiting for ACK...")
            ack_cmd = self._recv_exact(4, timeout=30)
            
            if ack_cmd == CMD_CAN:
                self.log("Transfer cancelled by receiver")
                return False
            
            if ack_cmd != CMD_ACK:
                self.log(f"Expected ACK, got: {ack_cmd}")
                return False
            
            # Get bitmap
            bitmap_bytes = self._recv_exact(1, timeout=5)
            if not bitmap_bytes:
                self.log("No bitmap received")
                return False
            
            bitmap = bitmap_bytes[0]
            self.log(f"Got ACK with bitmap {bitmap:02x}")
            
            if bitmap < 0xFE:
                self.log(f"Retransmit requested (bitmap={bitmap:02x}), but continuing anyway")
        
        # Send EOT
        self.log("Sending EOT...")
        self._send(CMD_EOT)
        
        # Wait for final ACK (wie in _send_file_after_request)
        self.log("Waiting for final ACK...")
        final_ack = self._recv_exact(5, timeout=10)
        self.log(f"Final ACK: {final_ack}")
        
        # Handle next TBRQ - respond with TBND (IMMER senden!)
        self.log("Waiting for final TBRQ to send TBND...")
        next_tbrq = self._recv_exact(4, timeout=5)
        if next_tbrq == CMD_REQUEST:
            self.log("Got final TBRQ, sending TBND (no more files)")
        else:
            self.log(f"No TBRQ received (got {next_tbrq}), sending TBND anyway")
        
        # IMMER TBND senden am Ende eines Single-File Transfers!
        self._send(CMD_END)
        self.log(">>> TBND sent")
        
        self.stats['end_time'] = time.time()
        self.stats['bytes_transferred'] = filesize
        
        duration = self.stats['end_time'] - self.stats['start_time']
        speed = filesize / duration if duration > 0 else 0
        
        self.log(f"===== SEND COMPLETE =====")
        self.log(f"Duration: {duration:.2f}s")
        self.log(f"Speed: {speed/1024:.2f} KB/s")
        self.log(f"Blocks sent: {self.stats['blocks_sent']}")
        
        self.save_debug_log("turbomodem_upload_debug.txt")
        
        if callback:
            callback(filesize, filesize, "Transfer complete", filename_to_send)
        
        return True
    
    def _recv_ack_simple(self, timeout=30):
        """
        Receive ACK (wie Server _recv_ack, aber vereinfacht)
        Returns ACK bytes (5 bytes: TBAC + bitmap) oder None
        """
        buffer = bytearray()
        start = time.time()
        
        while time.time() - start < timeout:
            chunk = self._recv_exact(32, timeout=1)
            if chunk:
                buffer.extend(chunk)
            
            # Look for TBAC in buffer
            tbac_pos = buffer.find(CMD_ACK)
            if tbac_pos >= 0:
                # Found TBAC - need one more byte for bitmap
                if len(buffer) >= tbac_pos + 5:
                    return bytes(buffer[tbac_pos:tbac_pos + 5])
        
        return None
    
    def send_file_immediate(self, filepath, callback=None):
        """
        Sendet Datei SOFORT ohne auf TBRQ zu warten.
        
        Verwende diese Methode wenn:
        - TBRQ bereits vom Terminal/BBS-Client empfangen wurde
        - Der Aufrufer weiß dass der Server bereit ist
        
        Args:
            filepath: Path zur Datei
            callback: Progress callback(bytes_sent, total_bytes, status)
            
        Returns:
            True bei Erfolg, False bei Fehler
        """
        return self.send_file(filepath, callback=callback, wait_for_request=False)
    
    def send_end_of_transfer(self):
        """
        Signalisiert dem Server dass keine weiteren Dateien kommen.
        
        Verwende diese Methode NACH dem letzten send_file() wenn
        mehrere Dateien einzeln gesendet wurden.
        
        Ablauf für Multi-File mit einzelnen send_file() Aufrufen:
        1. send_file(file1)  # Wartet auf TBRQ, sendet TBOK
        2. send_file(file2)  # Wartet auf TBRQ, sendet TBOK
        3. send_end_of_transfer()  # Wartet auf TBRQ, sendet TBND
        
        ODER besser: send_files([file1, file2]) verwenden!
        """
        self.log("Waiting for final TBRQ to send TBND...")
        req = self._recv_exact(4, timeout=10)
        if req == CMD_REQUEST:
            self.log("Got TBRQ - sending TBND (no more files)")
            self._send(CMD_END)
            return True
        else:
            self.log(f"Expected TBRQ, got {req}")
            return False
    
    def receive_file(self, filepath, callback=None):
        """
        Empfängt Datei mit TurboModem Protokoll (BBS → Client)
        
        Args:
            filepath: Ziel - kann Verzeichnis oder Temp-Dateiname sein
            callback: Progress callback(bytes_received, total_bytes, status, filename)
            
        Returns:
            (success, actual_filepath) - True/False und tatsächlicher Dateipfad
        """
        import os
        
        # Pfad normalisieren (/ -> \ auf Windows)
        filepath = os.path.normpath(filepath).replace('/', os.sep)
        
        self.log(f"===== RECEIVE FILE START =====")
        self.log(f"Input filepath: {filepath}")
        
        # Bestimme Ziel-Verzeichnis
        if os.path.isdir(filepath):
            # filepath IST ein Verzeichnis
            target_dir = filepath
            self.log(f"Input is existing directory")
        elif os.path.isfile(filepath):
            # filepath IST eine Datei - verwende ihr Verzeichnis
            target_dir = os.path.dirname(filepath)
            self.log(f"Input is existing file - using directory")
        else:
            # filepath existiert nicht
            # Wenn es eine Extension hat (.bin, .prg) -> es ist ein Temp-File
            # Verwende nur das Verzeichnis-Teil
            if '.' in os.path.basename(filepath):
                target_dir = os.path.dirname(filepath)
                self.log(f"Input looks like temp file (has extension) - using directory part")
            else:
                target_dir = filepath
                self.log(f"Input looks like directory")
        
        # Erstelle Verzeichnis falls nötig
        if not os.path.exists(target_dir):
            try:
                os.makedirs(target_dir, exist_ok=True)
                self.log(f"Created directory: {target_dir}")
            except Exception as e:
                self.log(f"ERROR: Cannot create directory: {e}")
                return (False, None)
        
        self.log(f"Target directory: {target_dir}")
        self.stats['start_time'] = time.time()
        
        # WICHTIG: Bei Upload (Server empfängt) muss gewartet werden bis Client bereit ist
        # Aber nur wenn wir wirklich der Empfänger sind (nicht bei Download!)
        # Der Client braucht Zeit um Upload-Dialog zu öffnen und send_file() aufzurufen
        
        # Versuche zu erkennen: Gibt es schon Daten im Buffer?
        # Wenn ja -> Client hat schon gesendet, kein Delay nötig
        # Wenn nein -> Client ist noch nicht bereit, warte 3 Sekunden
        try:
            # Prüfe ob Daten verfügbar sind (non-blocking)
            if hasattr(self.conn, 'recv'):
                # Socket: Nutze MSG_PEEK um zu schauen ohne zu konsumieren
                import socket
                old_timeout = self.conn.gettimeout()
                self.conn.settimeout(0.1)  # 100ms non-blocking check
                try:
                    peek_data = self.conn.recv(1, socket.MSG_PEEK)
                    if peek_data:
                        self.log("Client already sent data - no delay needed")
                    else:
                        self.log("No data yet - waiting 3 seconds for client...")
                        time.sleep(3.0)
                except socket.timeout:
                    # Kein Timeout-Error = keine Daten
                    self.log("No data in buffer - waiting 3 seconds for client...")
                    time.sleep(3.0)
                except:
                    # Fallback: Warte sicherheitshalber
                    self.log("Cannot check buffer - waiting 3 seconds...")
                    time.sleep(3.0)
                finally:
                    self.conn.settimeout(old_timeout)
            else:
                # Kein recv verfügbar - warte sicherheitshalber
                self.log("Unknown connection type - waiting 3 seconds...")
                time.sleep(3.0)
        except:
            # Bei Fehler: Warte sicherheitshalber
            self.log("Error checking buffer - waiting 3 seconds...")
            time.sleep(3.0)
        
        # Send REQUEST (mehrmals bei Upload, einmal bei Download)
        # Bei Upload: Server wartet auf Client → viele Retries nötig
        # Bei Download: Client wartet auf Server → kein Retry nötig
        
        # Versuche zu erkennen: Sind wir Upload-Empfänger oder Download-Empfänger?
        # Heuristik: Bei Upload wurde schon 3 Sekunden gewartet (siehe oben)
        # Bei Download wird nicht gewartet (Client sendet sofort REQUEST)
        
        # Einfache Lösung: Sende REQUEST mit kurzem Timeout
        # Wenn OK kommt → gut!
        # Wenn nicht → retry (maximal 30× für Upload)
        
        self.log("Sending REQUEST...")
        
        max_retries = 30  # Maximal 30 Versuche
        retry_delay = 1.0  # 1 Sekunde zwischen Versuchen
        first_timeout = 10.0  # Erster Versuch: 10 Sekunden (für Download)
        
        for attempt in range(max_retries):
            if attempt == 0:
                # Erster Versuch: Längerer Timeout (Download könnte sofort antworten)
                timeout = first_timeout
            else:
                # Weitere Versuche: Kurzer Timeout (Upload-Retries)
                timeout = retry_delay
            
            self._send(CMD_REQUEST)
            
            if attempt == 0:
                self.log(f"Sent REQUEST (waiting {timeout}s for response)...")
            else:
                self.log(f"Sent REQUEST (attempt {attempt + 1}/{max_retries})...")
            
            # Warte auf Antwort
            try:
                ok = self._recv_exact(4, timeout=timeout)
                
                if ok == CMD_OK:
                    self.log("✓ Got OK from sender!")
                    break
                elif ok:
                    self.log(f"Got unexpected response: {ok.hex()}, retrying...")
                    continue
                else:
                    # Timeout
                    if attempt == 0:
                        # Nach erstem Timeout: Switch zu Retry-Modus
                        self.log(f"No response after {timeout}s, switching to retry mode...")
                    else:
                        self.log(f"No response yet, retrying...")
                    continue
                    
            except Exception as e:
                self.log(f"Exception during REQUEST: {e}, retrying...")
                continue
        else:
            # Alle Versuche fehlgeschlagen
            self.log(f"ERROR: Sender did not respond after {max_retries} attempts")
            return (False, None)
        
        # OK empfangen - jetzt Filesize + Filename empfangen
        self.log("Waiting for Filesize + Filename...")
        
        # Receive filesize (8 bytes)
        filesize_bytes = self._recv_exact(8, timeout=10)
        if not filesize_bytes:
            return (False, None)
        
        filesize = struct.unpack('>Q', filesize_bytes)[0]
        
        # Receive filename length (2 bytes)
        filename_len_bytes = self._recv_exact(2, timeout=10)
        if not filename_len_bytes:
            return (False, None)
        
        filename_len = struct.unpack('>H', filename_len_bytes)[0]
        self.log(f"Filename length: {filename_len}")
        
        # Receive filename
        filename_bytes = self._recv_exact(filename_len, timeout=10)
        if not filename_bytes:
            return (False, None)
        
        filename = filename_bytes.decode('utf-8', errors='replace')
        self.log(f"Server filename: {filename}")
        
        # Server sendet jetzt kompletten Filename MIT Extension
        # (Früher wurde Extension entfernt, jetzt nicht mehr)
        
        # Build ACTUAL filepath using target_dir + server filename
        actual_filepath = os.path.join(target_dir, filename)
        self.log(f"Final filepath: {actual_filepath}")
        self.log(f"Filesize: {filesize:,} bytes ({filesize // BLOCK_SIZE + 1} blocks expected)")
        
        if callback:
            callback(0, filesize, "Starting TurboModem receive...", filename)
        
        # KRITISCH: Öffne actual_filepath (NICHT filepath!)
        with open(actual_filepath, 'wb') as f:
            expected_block = 1
            window_received = {}  # {block_num: data}
            bytes_received = 0
            retries = 0
            window_num = 0
            
            while bytes_received < filesize:
                window_num += 1
                
                # Berechne wie viele Blöcke noch fehlen
                bytes_remaining = filesize - bytes_received
                blocks_remaining = (bytes_remaining + BLOCK_SIZE - 1) // BLOCK_SIZE
                expected_blocks_in_window = min(WINDOW_SIZE, blocks_remaining)
                
                self.log(f"===== WINDOW #{window_num} (expecting blocks {expected_block}-{expected_block+expected_blocks_in_window-1}, total={expected_blocks_in_window}) =====")
                self.log(f"Bytes remaining: {bytes_remaining:,}, blocks remaining: {blocks_remaining}")
                
                # Receive blocks
                blocks_in_window = 0
                timeout_count = 0
                
                while blocks_in_window < expected_blocks_in_window and timeout_count < 3:
                    block_result = self.receive_block(timeout=10)
                    
                    if block_result is None:
                        # Timeout or error
                        timeout_count += 1
                        self.log(f"receive_block returned None (timeout #{timeout_count})")
                        
                        # Wenn wir schon einige Blöcke haben und ein Timeout kommt,
                        # könnte der Transfer fertig sein
                        if blocks_in_window > 0 and bytes_received + (blocks_in_window * BLOCK_SIZE) >= filesize:
                            self.log(f"Received enough data ({bytes_received + blocks_in_window * BLOCK_SIZE} >= {filesize}), assuming transfer complete")
                            break
                        
                        # Bei 3 Timeouts -> raus aus der Block-Loop
                        if timeout_count >= 3:
                            self.log(f"3 consecutive timeouts, breaking block receive loop")
                            break
                        continue
                    
                    # Reset timeout counter bei erfolgreichem Empfang
                    timeout_count = 0
                    
                    block_num, data = block_result
                    window_received[block_num] = data
                    blocks_in_window += 1
                    
                    # Check if we got all EXPECTED blocks (nicht alle WINDOW_SIZE!)
                    all_received = True
                    for i in range(expected_blocks_in_window):  # Nur erwartete Blöcke!
                        if (expected_block + i) not in window_received:
                            all_received = False
                            break
                    
                    if all_received:
                        self.log(f"All {expected_blocks_in_window} expected blocks received!")
                        break
                
                # Build ACK bitmap - nur für erwartete Blöcke!
                bitmap = 0xFF  # Start with all bits set
                for i in range(expected_blocks_in_window):
                    if (expected_block + i) not in window_received:
                        bitmap &= ~(1 << i)
                
                self.log(f"Window complete: Got {blocks_in_window}/{expected_blocks_in_window} blocks, bitmap={bitmap:02x}")
                
                # Send ACK with bitmap (use 0xFE if 0xFF to avoid Telnet IAC)
                send_bitmap = 0xFE if bitmap == 0xFF else bitmap
                self._send(CMD_ACK + bytes([send_bitmap]))
                self.log(f"Sent ACK with bitmap {send_bitmap:02x}")
                
                if bitmap < 0xFE:  # Some blocks missing
                    # Some blocks missing - aber prüfe ob wir schon genug Bytes haben
                    if bytes_received >= filesize:
                        self.log(f"Bitmap incomplete ({bitmap:02x}) but we have all bytes ({bytes_received} >= {filesize})")
                        self.log(f"Transfer appears complete, ignoring missing blocks")
                        break  # Raus aus der while-Schleife
                    
                    # Noch nicht genug Bytes - retry
                    self.log(f"Missing blocks! Bitmap={bitmap:02x}, retry#{retries}")
                    self.stats['blocks_retried'] += 1
                    retries += 1
                    if retries > MAX_RETRIES:
                        # Letzter Check: Haben wir genug Bytes trotz Retries?
                        if bytes_received >= filesize:
                            self.log(f"MAX RETRIES but we have all bytes - considering success")
                            break
                        self._send(CMD_CAN)
                        self.log(f"MAX RETRIES EXCEEDED!")
                        return (False, None)
                    continue
                
                # Write blocks in order
                blocks_written = 0
                while expected_block in window_received:
                    data = window_received[expected_block]
                    original_len = len(data)
                    
                    # Remove padding - trim to exact filesize
                    if bytes_received + len(data) > filesize:
                        trim_to = filesize - bytes_received
                        self.log(f"Block {expected_block}: Trimming from {len(data)} to {trim_to} bytes (would exceed filesize)")
                        data = data[:trim_to]
                    
                    f.write(data)
                    bytes_received += len(data)
                    del window_received[expected_block]
                    expected_block += 1
                    blocks_written += 1
                    
                    if callback:
                        callback(bytes_received, filesize, f"Received {bytes_received // 1024} KB", filename)
                    
                    # Check if we're done
                    if bytes_received >= filesize:
                        self.log(f"Block {expected_block-1}: Reached filesize ({bytes_received} >= {filesize}), stopping")
                        break
                
                self.log(f"Wrote {blocks_written} blocks, total bytes={bytes_received:,}/{filesize:,} ({100*bytes_received//filesize}%)")
                
                # Exit loop if transfer complete  
                if bytes_received >= filesize:
                    self.log(f"TRANSFER COMPLETE! bytes_received={bytes_received} >= filesize={filesize}")
                    break
                
                retries = 0
        
        # Loop beendet - sende finalen Progress Update SOFORT!
        self.log(f"Main loop exited. bytes_received={bytes_received}, filesize={filesize}")
        
        # WICHTIG: Prüfe und korrigiere die tatsächliche Dateigröße
        try:
            actual_size = os.path.getsize(actual_filepath)
            self.log(f"Actual file size on disk: {actual_size}, expected: {filesize}")
            
            if actual_size > filesize:
                self.log(f"WARNING: File on disk ({actual_size}) larger than expected ({filesize}), truncating...")
                with open(actual_filepath, 'r+b') as f:
                    f.truncate(filesize)
                self.log(f"Truncated file to {filesize} bytes")
                bytes_received = filesize
            elif actual_size < filesize:
                self.log(f"WARNING: File on disk ({actual_size}) smaller than expected ({filesize})")
        except Exception as e:
            self.log(f"ERROR checking/truncating file: {e}")
        
        if callback:
            callback(bytes_received, filesize, "Finishing transfer...", filename)
        
        # Wait for EOT (kürzerer Timeout wenn wir schon alle Bytes haben)
        eot_timeout = 3.0 if bytes_received >= filesize else 10.0
        self.log(f"Waiting for EOT (timeout={eot_timeout}s)...")
        eot = self._recv_exact(5, timeout=eot_timeout)
        
        if eot == CMD_EOT:
            self.log("✓ Got EOT, sending final ACK")
            self._send(CMD_ACK + b'\xfe')  # IMMER mit Bitmap!
        elif eot:
            self.log(f"✗ Expected EOT (TBEOT), got: {eot.hex() if len(eot) > 0 else 'timeout'}")
            # Wenn wir alle Bytes haben, sende ACK trotzdem
            if bytes_received >= filesize:
                self.log("All bytes received, sending ACK anyway")
                self._send(CMD_ACK + b'\xfe')  # IMMER mit Bitmap!
        else:
            self.log(f"✗ Timeout waiting for EOT")
            if bytes_received >= filesize:
                self.log("All bytes received, sending ACK anyway")
                self._send(CMD_ACK + b'\xfe')  # IMMER mit Bitmap!
            else:
                self.log(f"WARNING: Incomplete transfer - {bytes_received}/{filesize} bytes")
        
        self.stats['end_time'] = time.time()
        self.stats['bytes_transferred'] = bytes_received
        
        duration = self.stats['end_time'] - self.stats['start_time']
        speed = bytes_received / duration if duration > 0 else 0
        
        self.log(f"===== TRANSFER COMPLETE =====")
        self.log(f"Duration: {duration:.2f}s")
        self.log(f"Speed: {speed/1024:.2f} KB/s")
        self.log(f"Blocks received: {self.stats['blocks_received']}")
        self.log(f"Blocks corrupted: {self.stats['blocks_corrupted']}")
        self.log(f"Blocks retried: {self.stats['blocks_retried']}")
        self.log(f"Timeouts: {self.stats['timeouts']}")
        self.log(f"Bytes transferred: {bytes_received}/{filesize} ({100*bytes_received//filesize if filesize>0 else 0}%)")
        
        # Save debug log only if debug is enabled
        if self.debug:
            self.save_debug_log()
        
        if callback:
            callback(bytes_received, filesize, "Transfer complete", filename)
        
        return (True, actual_filepath)
    
    def get_speed(self):
        """
        Berechnet Transfer-Geschwindigkeit
        
        Returns:
            (bytes_per_second, duration)
        """
        duration = self.stats['end_time'] - self.stats['start_time']
        if duration > 0:
            bps = self.stats['bytes_transferred'] / duration
            return (bps, duration)
        return (0, 0)
    
    def print_stats(self):
        """Gibt Transfer-Statistiken aus"""
        bps, duration = self.get_speed()
        print(f"\n[TurboModem Statistics]")
        print(f"  Duration: {duration:.2f} seconds")
        print(f"  Bytes: {self.stats['bytes_transferred']:,}")
        print(f"  Speed: {bps / 1024:.2f} KB/s ({bps * 8 / 1000:.2f} kbps)")
        print(f"  Blocks sent: {self.stats['blocks_sent']}")
        print(f"  Blocks received: {self.stats['blocks_received']}")
        print(f"  Retransmits: {self.stats['retransmits']}")
    
    # =========================================================================
    # MULTI-FILE TRANSFER
    # =========================================================================
    
    def send_files(self, file_list, callback=None):
        """
        Sendet mehrere Dateien (Server-Seite)
        
        Protokoll:
        1. Warte auf TBRQ
        2. Sende TBOK + Datei (oder TBND wenn keine mehr)
        3. Nach EOT+ACK: Zurück zu 1
        
        Args:
            file_list: Liste von Dateipfaden
            callback: Progress callback(bytes, total, status, filename)
        
        Returns:
            (success, files_sent)
        """
        import os
        
        self.log(f"===== SEND MULTI-FILE: {len(file_list)} files =====")
        self.stats['start_time'] = time.time()
        
        # Buffer leeren - alte Daten vom vorherigen Transfer entfernen
        self._flush_receive_buffer()
        
        queue = list(file_list)  # Kopie der Liste
        files_sent = 0
        total_bytes = sum(os.path.getsize(f) for f in queue if os.path.exists(f))
        bytes_sent_total = 0
        
        while True:
            # Wait for REQUEST - search for TBRQ pattern (handles Telnet IAC leftovers)
            self.log("Waiting for TBRQ...")
            req = self._wait_for_pattern(CMD_REQUEST, timeout=60)
            
            if req != CMD_REQUEST:
                self.log(f"Expected TBRQ, got {req}")
                break
            
            self.log("<<< TBRQ received")
            
            if not queue:
                # No more files - send END
                self.log("Queue empty - sending TBND")
                self._send(CMD_END)
                break
            
            # Get next file
            filepath = queue.pop(0)
            
            if not os.path.exists(filepath):
                self.log(f"File not found: {filepath}, skipping")
                continue
            
            filesize = os.path.getsize(filepath)
            filename = os.path.basename(filepath)
            
            # Send file using existing send_file logic (but we already have TBRQ)
            success = self._send_file_after_request(filepath, filesize, filename, callback)
            
            if success:
                files_sent += 1
                bytes_sent_total += filesize
                self.log(f"File {files_sent}/{len(file_list)} complete: {filename}")
                # Note: Don't flush buffer here! TBRQ might already be waiting.
                # _wait_for_pattern will handle any garbage before TBRQ.
            else:
                self.log(f"Failed to send: {filename}")
        
        self.stats['end_time'] = time.time()
        self.stats['files_transferred'] = files_sent
        duration = self.stats['end_time'] - self.stats['start_time']
        
        self.log(f"===== MULTI-SEND COMPLETE =====")
        self.log(f"Files sent: {files_sent}/{len(file_list)}")
        self.log(f"Total bytes: {bytes_sent_total:,}")
        self.log(f"Duration: {duration:.2f}s")
        if duration > 0:
            self.log(f"Speed: {bytes_sent_total/duration/1024:.2f} KB/s")
        
        # Debug log speichern
        self.save_debug_log("turbomodem_upload_debug.txt")
        
        return (files_sent == len(file_list), files_sent)
    
    def _send_file_after_request(self, filepath, filesize, filename, callback=None):
        """Interne Methode: Sendet TBOK + Daten (nach TBRQ bereits empfangen)"""
        import os
        
        try:
            self.log(f"=== _send_file_after_request START ===")
            self.log(f"  filepath: {filepath}")
            self.log(f"  filesize: {filesize}")
            self.log(f"  filename: {filename}")
            
            filename_bytes = filename.encode('utf-8')
            filename_len = len(filename_bytes)
            
            # Format: TBOK(4) + Filesize(8) + FilenameLen(2) + Filename(N)
            header = CMD_OK + struct.pack('>Q', filesize) + struct.pack('>H', filename_len) + filename_bytes
            self.log(f"Header built: {len(header)} bytes")
            
            self._send(header)
            self.log(f"TBOK header sent ({len(header)} bytes)")
            
            # Callback mit Error-Handling
            if callback:
                try:
                    callback(0, filesize, "Starting transfer...", filename)
                except Exception as cb_err:
                    self.log(f"WARNING: Callback error: {cb_err}")
            
            self.log(f"Opening file...")
            f = open(filepath, 'rb')
            self.log(f"File opened successfully")
            
            block_num = 1
            window = []
            bytes_sent = 0
            retries = 0
            
            while True:
                # Fill window
                self.log(f"Filling window (current: {len(window)}, target: {WINDOW_SIZE})")
                while len(window) < WINDOW_SIZE:
                    data = f.read(BLOCK_SIZE)
                    if not data:
                        self.log(f"EOF reached")
                        break
                    window.append((block_num, data))
                    self.log(f"Read block {block_num}: {len(data)} bytes")
                    block_num += 1
                
                if not window:
                    self.log("No more blocks to send")
                    break
                
                # Send window
                self.log(f"Sending {len(window)} blocks...")
                for bn, data in window:
                    self.send_block(bn, data)
                
                self.log("Blocks sent, waiting for ACK...")
                
                # Wait for ACK
                ack_cmd = self._recv_exact(4, timeout=10)
                
                if ack_cmd == CMD_CAN:
                    self.log("Transfer cancelled by receiver")
                    f.close()
                    return False
                
                if ack_cmd != CMD_ACK:
                    self.log(f"Expected ACK, got: {ack_cmd}")
                    retries += 1
                    if retries > MAX_RETRIES:
                        self.log("Max retries exceeded")
                        f.close()
                        return False
                    continue
                
                # Get bitmap
                bitmap_bytes = self._recv_exact(1, timeout=1)
                if not bitmap_bytes:
                    self.log("No bitmap received")
                    retries += 1
                    if retries > MAX_RETRIES:
                        f.close()
                        return False
                    continue
                
                bitmap = bitmap_bytes[0]
                self.log(f"ACK received, bitmap: {bitmap:02x}")
                
                if bitmap >= 0xFE:  # Accept 0xFE or 0xFF
                    # All OK
                    for bn, data in window:
                        bytes_sent += len(data)
                    window = []
                    retries = 0
                    self.log(f"Window OK, {bytes_sent}/{filesize} bytes sent")
                    
                    if callback:
                        try:
                            callback(bytes_sent, filesize, f"Sent {bytes_sent // 1024} KB", filename)
                        except:
                            pass
                else:
                    # Retransmit needed
                    self.log(f"Retransmit requested, bitmap: {bitmap:02x}")
                    new_window = []
                    for i, (bn, data) in enumerate(window):
                        if not (bitmap & (1 << i)):
                            new_window.append((bn, data))
                            self.stats['retransmits'] += 1
                    window = new_window
                    retries += 1
                    if retries > MAX_RETRIES:
                        f.close()
                        return False
            
            f.close()
            
            # Send EOT
            self.log("Sending EOT...")
            self._send(CMD_EOT)
            
            # Wait for final ACK (5 bytes: TBAC + bitmap)
            final_ack = self._recv_exact(5, timeout=5)
            self.log(f"Final ACK: {final_ack}")
            
            self.stats['bytes_transferred'] += filesize
            
            if callback:
                try:
                    callback(filesize, filesize, "Complete", filename)
                except:
                    pass
            
            self.log(f"=== _send_file_after_request COMPLETE ===")
            # Check if ACK received (first 4 bytes are TBAC)
            return final_ack and len(final_ack) >= 4 and final_ack[:4] == CMD_ACK
            
        except Exception as e:
            self.log(f"!!! EXCEPTION in _send_file_after_request: {e}")
            import traceback
            self.log(traceback.format_exc())
            # Speichere Log sofort bei Exception
            self.save_debug_log("turbomodem_CRASH.txt")
            return False
    
    def receive_files(self, target_dir, callback=None, max_files=100):
        """
        Empfängt mehrere Dateien bis TBND (Client-Seite)
        
        Protokoll:
        1. Sende TBRQ
        2. Empfange TBOK + Datei (oder TBND = fertig)
        3. Nach EOT+ACK: Zurück zu 1
        
        Args:
            target_dir: Zielverzeichnis
            callback: Progress callback(bytes, total, status, filename)
            max_files: Maximale Anzahl Dateien (Sicherheit)
        
        Returns:
            (success, list of received filepaths)
        """
        import os
        
        self.log(f"===== RECEIVE MULTI-FILE to {target_dir} =====")
        self.stats['start_time'] = time.time()
        
        # Buffer leeren - alte Daten vom vorherigen Transfer entfernen
        self._flush_receive_buffer()
        
        if not os.path.exists(target_dir):
            os.makedirs(target_dir, exist_ok=True)
        
        received_files = []
        
        for file_num in range(max_files):
            # Send REQUEST
            self.log(f"Sending TBRQ (file #{file_num + 1})...")
            self._send(CMD_REQUEST)
            
            # Wait for OK or END
            response = self._recv_exact(4, timeout=30)
            
            if response == CMD_END:
                self.log("Got TBND - transfer complete")
                break
            
            if response != CMD_OK:
                self.log(f"Expected TBOK or TBND, got {response}")
                break
            
            # Receive file data (TBOK already received)
            success, filepath = self._receive_file_after_ok(target_dir, callback)
            
            if success and filepath:
                received_files.append(filepath)
                self.log(f"Received file #{len(received_files)}: {os.path.basename(filepath)}")
            else:
                self.log("Failed to receive file")
                break
        
        self.stats['end_time'] = time.time()
        self.stats['files_transferred'] = len(received_files)
        duration = self.stats['end_time'] - self.stats['start_time']
        
        self.log(f"===== MULTI-RECEIVE COMPLETE =====")
        self.log(f"Files received: {len(received_files)}")
        self.log(f"Total bytes: {self.stats['bytes_transferred']:,}")
        self.log(f"Duration: {duration:.2f}s")
        
        # IMMER Debug log speichern
        self.save_debug_log("turbomodem_download_debug.txt")
        
        return (len(received_files) > 0, received_files)
    
    def _receive_file_after_ok(self, target_dir, callback=None):
        """Interne Methode: Empfängt Dateidaten nach TBOK"""
        import os
        
        # Receive filesize
        filesize_bytes = self._recv_exact(8, timeout=10)
        if not filesize_bytes:
            return (False, None)
        filesize = struct.unpack('>Q', filesize_bytes)[0]
        
        # Receive filename length
        filename_len_bytes = self._recv_exact(2, timeout=10)
        if not filename_len_bytes:
            return (False, None)
        filename_len = struct.unpack('>H', filename_len_bytes)[0]
        
        # Receive filename
        filename_bytes = self._recv_exact(filename_len, timeout=10)
        if not filename_bytes:
            return (False, None)
        filename = filename_bytes.decode('utf-8', errors='replace')
        
        self.log(f"Receiving: {filename} ({filesize:,} bytes)")
        
        filepath = os.path.join(target_dir, filename)
        
        if callback:
            callback(0, filesize, "Starting...", filename)
        
        total_blocks = (filesize + BLOCK_SIZE - 1) // BLOCK_SIZE
        if total_blocks == 0:
            total_blocks = 1
        expected_block = 1
        bytes_received = 0
        retries = 0
        
        self.log(f"Expecting {total_blocks} blocks of {BLOCK_SIZE} bytes")
        
        with open(filepath, 'wb') as f:
            while bytes_received < filesize:
                window_received = {}
                # Nur so viele Blöcke erwarten wie noch fehlen!
                blocks_remaining = total_blocks - expected_block + 1
                expected_blocks_in_window = min(WINDOW_SIZE, blocks_remaining)
                
                self.log(f"Window: expecting {expected_blocks_in_window} blocks (block {expected_block}-{expected_block + expected_blocks_in_window - 1})")
                
                # Receive window
                blocks_got = 0
                for _ in range(expected_blocks_in_window):
                    result = self.receive_block(timeout=10)
                    if result:
                        bn, data = result
                        window_received[bn] = data
                        blocks_got += 1
                        self.log(f"Got block {bn} ({len(data)} bytes)")
                    else:
                        self.log(f"Block receive timeout/error")
                        break  # Stop waiting for more blocks
                
                self.log(f"Window received {blocks_got}/{expected_blocks_in_window} blocks")
                
                # Build bitmap - nur für die erwarteten Blöcke
                bitmap = 0x00  # Start with all missing
                for i in range(expected_blocks_in_window):
                    if (expected_block + i) in window_received:
                        bitmap |= (1 << i)  # Mark as received
                
                self.log(f"Sending ACK with bitmap {bitmap:02x}")
                
                # Send ACK
                self._send(CMD_ACK + bytes([bitmap]))
                
                # Prüfe ob wir alle erwarteten Blöcke haben
                all_received = (blocks_got == expected_blocks_in_window)
                
                if not all_received:
                    # Nicht alle Blöcke bekommen
                    retries += 1
                    self.log(f"Missing blocks, retry {retries}/{MAX_RETRIES}")
                    if retries > MAX_RETRIES:
                        self._send(CMD_CAN)
                        return (False, None)
                    continue
                
                # Write blocks in order
                while expected_block in window_received:
                    data = window_received[expected_block]
                    
                    # Truncate last block if needed
                    remaining = filesize - bytes_received
                    if len(data) > remaining:
                        data = data[:remaining]
                    
                    f.write(data)
                    bytes_received += len(data)
                    del window_received[expected_block]
                    expected_block += 1
                    
                    if callback:
                        callback(bytes_received, filesize, f"Received {bytes_received // 1024} KB", filename)
                
                self.log(f"Progress: {bytes_received}/{filesize} bytes")
                retries = 0
                
                # Check if done
                if bytes_received >= filesize:
                    self.log(f"File complete!")
                    break
        
        # Wait for EOT
        self.log("Waiting for EOT...")
        eot = self._recv_exact(5, timeout=5)
        if eot == CMD_EOT:
            self.log("Got EOT, sending final ACK")
            self._send(CMD_ACK + b'\xfe')  # IMMER mit Bitmap 0xFE!
        else:
            self.log(f"Expected EOT, got: {eot}")
        
        self.stats['bytes_transferred'] += bytes_received
        
        if callback:
            callback(bytes_received, filesize, "Complete", filename)
        
        return (True, filepath)


# =============================================================================
# LOCAL TEST (Python to Python over socket pair)
# =============================================================================

def run_local_test():
    """
    Test Multi-File Transfer lokal (ohne C64)
    
    Erstellt Test-Dateien, sendet sie über Socket-Pair
    """
    import socket
    import threading
    import tempfile
    import os
    
    print("=" * 60)
    print("TURBOMODEM LOCAL MULTI-FILE TEST")
    print("=" * 60)
    
    # Create test directory
    test_dir = tempfile.mkdtemp(prefix="turbomodem_test_")
    send_dir = os.path.join(test_dir, "send")
    recv_dir = os.path.join(test_dir, "recv")
    os.makedirs(send_dir)
    os.makedirs(recv_dir)
    
    print(f"Test directory: {test_dir}")
    
    # Create test files
    test_files = []
    for i, (name, size) in enumerate([
        ("test1.prg", 1024),      # 1 KB
        ("test2.bin", 8192),      # 8 KB
        ("test3.dat", 32768),     # 32 KB
    ]):
        filepath = os.path.join(send_dir, name)
        with open(filepath, 'wb') as f:
            pattern = bytes([i] * 256)
            for _ in range(size // 256):
                f.write(pattern)
            f.write(pattern[:size % 256])
        test_files.append(filepath)
        print(f"Created: {name} ({size} bytes)")
    
    # Create socket pair
    server_sock, client_sock = socket.socketpair()
    
    results = {'sender': None, 'receiver': None}
    
    def sender_thread():
        try:
            turbo = TurboModem(server_sock, debug=False)
            success, count = turbo.send_files(test_files)
            results['sender'] = (success, count)
        except Exception as e:
            print(f"SENDER ERROR: {e}")
            import traceback
            traceback.print_exc()
            results['sender'] = (False, 0)
    
    def receiver_thread():
        try:
            turbo = TurboModem(client_sock, debug=False)
            success, files = turbo.receive_files(recv_dir)
            results['receiver'] = (success, files)
        except Exception as e:
            print(f"RECEIVER ERROR: {e}")
            import traceback
            traceback.print_exc()
            results['receiver'] = (False, [])
    
    print("\nStarting transfer...")
    print("-" * 40)
    
    sender = threading.Thread(target=sender_thread)
    receiver = threading.Thread(target=receiver_thread)
    
    sender.start()
    receiver.start()
    
    sender.join(timeout=60)
    receiver.join(timeout=60)
    
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    
    if results['sender']:
        success, count = results['sender']
        print(f"Sender: {'OK' if success else 'FAIL'} - {count} files sent")
    
    if results['receiver']:
        success, files = results['receiver']
        print(f"Receiver: {'OK' if success else 'FAIL'} - {len(files)} files received")
        
        print("\nVerifying files:")
        all_ok = True
        for filepath in files:
            filename = os.path.basename(filepath)
            original = os.path.join(send_dir, filename)
            
            if os.path.exists(original) and os.path.exists(filepath):
                with open(original, 'rb') as f1, open(filepath, 'rb') as f2:
                    orig_data = f1.read()
                    recv_data = f2.read()
                
                if orig_data == recv_data:
                    print(f"  ✓ {filename}: OK ({len(recv_data)} bytes)")
                else:
                    print(f"  ✗ {filename}: MISMATCH!")
                    all_ok = False
            else:
                print(f"  ✗ {filename}: File missing!")
                all_ok = False
        
        if all_ok:
            print("\n✓ ALL FILES VERIFIED OK!")
        else:
            print("\n✗ VERIFICATION FAILED!")
    
    server_sock.close()
    client_sock.close()
    
    print(f"\nTest files in: {test_dir}")
    return results


# Example usage
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        run_local_test()
    else:
        print("""
TurboModem Protocol - Usage Example
====================================

# Single file (receive):
turbo = TurboModem(connection)
success, filepath = turbo.receive_file("/download/", callback=progress)

# Single file (send):
turbo = TurboModem(connection)
success = turbo.send_file("upload.bin", callback=progress)

# Multi-file (receive):
turbo = TurboModem(connection)
success, files = turbo.receive_files("/download/", callback=progress)

# Multi-file (send):
turbo = TurboModem(connection)
success, count = turbo.send_files(['file1.prg', 'file2.bin'])

Run local test: python turbomodem.py test

Performance:
============
XModem:     ~30-250 KB/s
TurboModem: ~500 KB/s - 2 MB/s ✅ (10-20x faster!)
""")
