
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import json
import threading
import time
import os
import socket
import queue
from PIL import ImageTk

from petscii_parser import PETSCIIScreenBuffer, PETSCIIParser
from c64_rom_renderer import AnimatedC64ROMFontRenderer
from telnet_client import BBSConnection, set_telnet_debug
from c64_keyboard import get_petscii_for_key, is_printable_key
from file_transfer import FileTransfer, TransferProtocol
from terminal_extensions import ScrollbackBuffer, ScrollbackViewer

# Version (single source of truth)
PYCGMS_VERSION = "1.1"

# Global debug flag - set by BBSTerminal when settings are loaded
_TERMINAL_DEBUG = False

def debug_print(*args, **kwargs):
    """Print only if debug mode is enabled"""
    if _TERMINAL_DEBUG:
        print(*args, **kwargs)


class TransferProgressDialog(tk.Toplevel):
    """Transfer Progress mit LIVE Bytes, Geschwindigkeit und Dateiname"""
    
    def __init__(self, parent, title, is_upload=True, show_file_list=False, file_list=None, punter_debug=False, is_punter=False, bbs_connection=None):
        super().__init__(parent)
        self.title(title)
        self.resizable(True, True)
        
        self.cancelled = False
        self.start_time = time.time()
        self.last_bytes = 0
        self.last_time = time.time()
        self.show_file_list = show_file_list
        self.completed_files = []
        self.file_status = {}
        self.total_files = 0
        self.completed_count = 0
        self.punter_debug = punter_debug
        self.is_punter = is_punter or punter_debug
        self.file_transfer = None
        self.bbs_connection = bbs_connection
        
        # Für Geschwindigkeitsberechnung
        self.speed_samples = []  # Liste von (time, bytes) für gleitenden Durchschnitt
        self.current_file_start_bytes = 0
        self.current_filename = ""
        self.files_completed = 0
        self.total_files_count = 0
        
        # Throttling
        self.last_update_time = 0
        self.min_update_interval = 0.05
        self.pending_update = None
        
        # ===== UI AUFBAU =====
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Header mit Typ
        header_text = "📤 UPLOAD" if is_upload else "📥 DOWNLOAD"
        ttk.Label(main_frame, text=header_text, font=('Arial', 16, 'bold')).pack(pady=(0, 10))
        
        # === AKTUELLES FILE (groß und prominent) ===
        file_frame = ttk.LabelFrame(main_frame, text="Current File", padding=10)
        file_frame.pack(fill=tk.X, pady=5)
        
        self.current_file_var = tk.StringVar(value="Waiting...")
        self.current_file_label = ttk.Label(file_frame, textvariable=self.current_file_var, 
                                            font=('Consolas', 12, 'bold'), foreground='blue')
        self.current_file_label.pack(anchor=tk.W)
        
        # File Progress Bar
        self.file_progress = ttk.Progressbar(file_frame, mode='determinate', length=450)
        self.file_progress.pack(fill=tk.X, pady=(5, 0))
        
        # File Stats (Bytes + Prozent)
        file_stats_frame = ttk.Frame(file_frame)
        file_stats_frame.pack(fill=tk.X, pady=(5, 0))
        
        self.file_bytes_var = tk.StringVar(value="0 / 0 bytes")
        self.file_percent_var = tk.StringVar(value="0%")
        ttk.Label(file_stats_frame, textvariable=self.file_bytes_var, font=('Arial', 10)).pack(side=tk.LEFT)
        ttk.Label(file_stats_frame, textvariable=self.file_percent_var, font=('Arial', 10, 'bold')).pack(side=tk.RIGHT)
        
        # === TRANSFER STATS ===
        stats_frame = ttk.LabelFrame(main_frame, text="Transfer Statistics", padding=10)
        stats_frame.pack(fill=tk.X, pady=5)
        
        # Grid für Stats
        self.speed_var = tk.StringVar(value="Speed: -- KB/s")
        self.eta_var = tk.StringVar(value="ETA: --:--")
        self.elapsed_var = tk.StringVar(value="Elapsed: 0:00")
        self.total_bytes_var = tk.StringVar(value="Total: 0 bytes")
        
        ttk.Label(stats_frame, textvariable=self.speed_var, font=('Arial', 11, 'bold'), 
                  foreground='green').grid(row=0, column=0, sticky=tk.W, padx=5)
        ttk.Label(stats_frame, textvariable=self.eta_var, font=('Arial', 10)).grid(row=0, column=1, sticky=tk.W, padx=5)
        ttk.Label(stats_frame, textvariable=self.elapsed_var, font=('Arial', 10)).grid(row=1, column=0, sticky=tk.W, padx=5)
        ttk.Label(stats_frame, textvariable=self.total_bytes_var, font=('Arial', 10)).grid(row=1, column=1, sticky=tk.W, padx=5)
        
        # === MULTI-FILE COUNTER ===
        self.files_var = tk.StringVar(value="")
        self.files_label = ttk.Label(main_frame, textvariable=self.files_var, font=('Arial', 11))
        self.files_label.pack(pady=5)
        
        # === FILE LIST (wenn Multi-File) ===
        if show_file_list:
            list_frame = ttk.LabelFrame(main_frame, text="Files", padding=5)
            list_frame.pack(fill=tk.BOTH, expand=True, pady=5)
            
            self.file_listbox = tk.Listbox(list_frame, height=6, font=('Consolas', 9))
            scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.file_listbox.yview)
            self.file_listbox.configure(yscrollcommand=scrollbar.set)
            
            self.file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            
            if file_list:
                self.set_file_list(file_list)
        
        # ============================================================
        # PUNTER CONTROLS (immer bei Punter-Transfers)
        # ============================================================
        if self.is_punter:
            # Waiting For + CTRL+X immer anzeigen
            self._setup_punter_controls()
            
            # Activity Log + Manuelle Buttons nur wenn punter_debug
            if punter_debug:
                self._setup_punter_debug()
        
        # Cancel Button
        ttk.Button(self, text="Cancel", command=self.cancel).pack(pady=10)
        
        # Größe je nach Modus - größer für bessere Lesbarkeit
        if punter_debug:
            self.geometry("700x700")
        elif self.is_punter:
            self.geometry("520x500")
        elif show_file_list:
            self.geometry("520x550")
        else:
            self.geometry("520x380")  # Größer für alle Statistiken
        
        self.transient(parent)
        self.grab_set()
        
        # Zentriere Dialog
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")
    
    def _setup_punter_controls(self):
        """Setup Punter Controls - immer bei Punter-Transfers (Waiting + CTRL+X)"""
        # Separator
        ttk.Separator(self, orient='horizontal').pack(fill=tk.X, padx=20, pady=10)
        
        # Frame für Waiting + CTRL+X
        control_frame = ttk.Frame(self)
        control_frame.pack(fill=tk.X, padx=20, pady=5)
        
        # Waiting For Anzeige
        ttk.Label(control_frame, text="⏳ Waiting for:", font=('Arial', 10, 'bold')).pack(side=tk.LEFT)
        self.waiting_var = tk.StringVar(value="---")
        self.waiting_label = ttk.Label(control_frame, textvariable=self.waiting_var, 
                                        font=('Consolas', 11, 'bold'), foreground='blue')
        self.waiting_label.pack(side=tk.LEFT, padx=10)
        
        # CTRL+X Abbruch Button (immer verfügbar)
        self.btn_ctrlx = ttk.Button(control_frame, text="CTRL+X Abort", width=12, command=self._manual_ctrlx)
        self.btn_ctrlx.pack(side=tk.RIGHT, padx=5)
    
    def _setup_punter_debug(self):
        """Setup Punter Debug UI - Activity Log und manuelle Buttons"""
        import tkinter.scrolledtext as scrolledtext
        
        # Manual Send Buttons
        btn_frame = ttk.LabelFrame(self, text="Manual Send (Debug)", padding=5)
        btn_frame.pack(fill=tk.X, padx=20, pady=5)
        
        self.btn_goo = ttk.Button(btn_frame, text="GOO", width=8, command=self._manual_goo)
        self.btn_goo.pack(side=tk.LEFT, padx=5)
        
        self.btn_ack = ttk.Button(btn_frame, text="ACK", width=8, command=self._manual_ack)
        self.btn_ack.pack(side=tk.LEFT, padx=5)
        
        self.btn_sb = ttk.Button(btn_frame, text="S/B", width=8, command=self._manual_sb)
        self.btn_sb.pack(side=tk.LEFT, padx=5)
        
        self.btn_syn = ttk.Button(btn_frame, text="SYN", width=8, command=self._manual_syn)
        self.btn_syn.pack(side=tk.LEFT, padx=5)
        
        # Live Log
        log_frame = ttk.LabelFrame(self, text="Live Protocol Log", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)
        
        self.live_log = scrolledtext.ScrolledText(log_frame, height=12, width=70, 
                                                   font=('Consolas', 9), state='disabled')
        self.live_log.pack(fill=tk.BOTH, expand=True)
        
        # Tags für Farben
        self.live_log.tag_configure('IN', foreground='green')
        self.live_log.tag_configure('OUT', foreground='blue')
        self.live_log.tag_configure('WAIT', foreground='orange')
        self.live_log.tag_configure('STATUS', foreground='purple')
        self.live_log.tag_configure('MANUAL', foreground='red', font=('Consolas', 9, 'bold'))
    
    def set_file_transfer(self, ft):
        """Setzt FileTransfer Referenz für manuelle Sends"""
        self.file_transfer = ft
        if self.is_punter and ft:
            # Registriere Live-Callback (für Waiting-Anzeige auch wenn nicht debug)
            ft.set_live_callback(self.live_update)
    
    def live_update(self, direction, data, description=""):
        """Callback für Live IN/OUT Updates - threadsafe"""
        if not self.is_punter:
            return
        
        def do_update():
            try:
                if not self.winfo_exists():
                    return
                
                # Update Waiting Anzeige (immer wenn is_punter)
                if direction == 'WAIT':
                    if hasattr(self, 'waiting_var'):
                        self.waiting_var.set(description.replace("Waiting for: ", ""))
                elif direction == 'IN' and 'MATCHED' in str(description):
                    if hasattr(self, 'waiting_var'):
                        self.waiting_var.set("---")
                
                # Log nur wenn punter_debug
                if self.punter_debug and hasattr(self, 'live_log'):
                    # Timestamp
                    timestamp = time.strftime("%H:%M:%S")
                    
                    # Format message
                    if data and isinstance(data, bytes):
                        hex_str = ' '.join(f'{b:02X}' for b in data[:10])
                        msg = f"[{timestamp}] {direction}: {hex_str}"
                        if description:
                            msg += f" - {description}"
                    else:
                        msg = f"[{timestamp}] {direction}: {description}"
                    
                    # Update Log
                    self.live_log.configure(state='normal')
                    self.live_log.insert(tk.END, msg + "\n", direction)
                    self.live_log.see(tk.END)
                    self.live_log.configure(state='disabled')
                
            except:
                pass
        
        # Führe Update im Hauptthread aus (threadsafe)
        try:
            self.after(0, do_update)
        except:
            pass
    
    def _manual_goo(self):
        """Manuell GOO senden"""
        try:
            if self.file_transfer:
                self.file_transfer.manual_send_goo()
        except:
            pass
    
    def _manual_ack(self):
        """Manuell ACK senden"""
        try:
            if self.file_transfer:
                self.file_transfer.manual_send_ack()
        except:
            pass
    
    def _manual_sb(self):
        """Manuell S/B senden"""
        try:
            if self.file_transfer:
                self.file_transfer.manual_send_sb()
        except:
            pass
    
    def _manual_syn(self):
        """Manuell SYN senden"""
        try:
            if self.file_transfer:
                self.file_transfer.manual_send_syn()
        except:
            pass
    
    def _manual_ctrlx(self):
        """Manuell CTRL+X (Abbruch) senden und Transfer beenden"""
        debug_print("[CTRL+X] Button pressed")
        sent = False
        
        # Versuche zuerst über file_transfer zu senden
        try:
            if self.file_transfer:
                debug_print(f"[CTRL+X] Sending via file_transfer")
                self.file_transfer.send_raw(bytes([0x18]))
                self.file_transfer.cancel_requested = True
                sent = True
                debug_print("[CTRL+X] Sent via file_transfer")
        except Exception as e:
            debug_print(f"[CTRL+X] file_transfer error: {e}")
        
        # Fallback: Direkt über BBS-Connection senden
        if not sent and self.bbs_connection:
            try:
                debug_print(f"[CTRL+X] Sending via bbs_connection")
                if hasattr(self.bbs_connection, 'send_raw'):
                    self.bbs_connection.send_raw(bytes([0x18]))
                    sent = True
                    debug_print("[CTRL+X] Sent via bbs_connection.send_raw")
                elif hasattr(self.bbs_connection, 'client') and self.bbs_connection.client:
                    if hasattr(self.bbs_connection.client, 'send_raw'):
                        self.bbs_connection.client.send_raw(bytes([0x18]))
                        sent = True
                        debug_print("[CTRL+X] Sent via bbs_connection.client.send_raw")
            except Exception as e:
                debug_print(f"[CTRL+X] bbs_connection error: {e}")
        
        if not sent:
            debug_print("[CTRL+X] WARNING: Could not send CTRL+X!")
        
        # Dialog als abgebrochen markieren und schließen
        self.cancelled = True
        debug_print("[CTRL+X] Dialog cancelled, destroying...")
        try:
            self.destroy()
        except:
            pass
    
    def _log_manual(self, code):
        """Log manuellen Send - deaktiviert"""
        pass
    
    def set_file_list(self, files):
        """Setzt die initiale File-Liste (für Multi-File Transfers)"""
        self.total_files = len(files)
        self.total_files_count = len(files)  # Für update_progress
        self.completed_count = 0
        self.files_completed = 0  # Für update_progress
        self.file_status = {}
        
        # Setze Files Counter auch wenn keine Listbox
        self._update_files_counter()
        
        if not self.show_file_list or not hasattr(self, 'file_listbox'):
            return
        
        self.file_listbox.delete(0, tk.END)
        
        for filepath in files:
            import os
            filename = os.path.basename(filepath)
            self.file_status[filename] = 'waiting'
            # Format: "⏳ FILENAME.PRG"
            self.file_listbox.insert(tk.END, f"⏳ {filename}")
    
    def _update_files_counter(self):
        """Aktualisiert den Files x/y Counter"""
        if self.total_files > 0:
            self.files_var.set(f"Files: {self.completed_count}/{self.total_files}")
        else:
            self.files_var.set("")
    
    def set_file_active(self, filename):
        """Markiert ein File als aktiv (wird gerade übertragen)"""
        if not self.show_file_list or not hasattr(self, 'file_listbox'):
            return
        
        import os
        filename = os.path.basename(filename)
        
        def do_update():
            for i in range(self.file_listbox.size()):
                item = self.file_listbox.get(i)
                # Extrahiere Filename aus Item (nach dem Status-Icon)
                item_filename = item[2:].strip()  # Skip "⏳ " oder "✓ " etc.
                if ' - ' in item_filename:
                    item_filename = item_filename.split(' - ')[0]
                
                if item_filename == filename:
                    self.file_status[filename] = 'active'
                    self.file_listbox.delete(i)
                    self.file_listbox.insert(i, f"📤 {filename}")
                    self.file_listbox.see(i)
                    break
            self.update()
        
        self.after(0, do_update)
    
    def set_file_complete(self, filename, size_bytes=0):
        """Markiert ein File als erfolgreich abgeschlossen"""
        if not self.show_file_list or not hasattr(self, 'file_listbox'):
            return
        
        import os
        filename = os.path.basename(filename)
        
        def do_update():
            for i in range(self.file_listbox.size()):
                item = self.file_listbox.get(i)
                # Extrahiere Filename aus Item
                item_filename = item[2:].strip()
                if ' - ' in item_filename:
                    item_filename = item_filename.split(' - ')[0]
                
                if item_filename == filename:
                    self.file_status[filename] = 'done'
                    self.file_listbox.delete(i)
                    if size_bytes > 0:
                        self.file_listbox.insert(i, f"✓ {filename} - {size_bytes:,} bytes")
                    else:
                        self.file_listbox.insert(i, f"✓ {filename}")
                    self.file_listbox.itemconfig(i, fg='green')
                    self.file_listbox.see(i)
                    break
            
            self.completed_count += 1
            self._update_files_counter()
            self.update()
        
        self.after(0, do_update)
    
    def set_file_error(self, filename, error_msg=""):
        """Markiert ein File als fehlgeschlagen"""
        if not self.show_file_list or not hasattr(self, 'file_listbox'):
            return
        
        import os
        filename = os.path.basename(filename)
        
        def do_update():
            for i in range(self.file_listbox.size()):
                item = self.file_listbox.get(i)
                item_filename = item[2:].strip()
                if ' - ' in item_filename:
                    item_filename = item_filename.split(' - ')[0]
                
                if item_filename == filename:
                    self.file_status[filename] = 'error'
                    self.file_listbox.delete(i)
                    self.file_listbox.insert(i, f"✗ {filename}")
                    self.file_listbox.itemconfig(i, fg='red')
                    break
            self.update()
        
        self.after(0, do_update)
    
    def add_completed_file(self, filename, blocks, size_bytes):
        """Fügt eine abgeschlossene Datei zur Liste hinzu (für Download/Punter)"""
        if self.show_file_list and hasattr(self, 'file_listbox'):
            # Format: "✓ FILENAME.PRG - 65 blocks, 16,135 bytes"
            entry = f"✓ {filename} - {blocks} blocks, {size_bytes:,} bytes"
            self.completed_files.append((filename, blocks, size_bytes))
            
            def do_add():
                self.file_listbox.insert(tk.END, entry)
                self.file_listbox.itemconfig(self.file_listbox.size()-1, fg='green')
                self.file_listbox.see(tk.END)  # Scrolle zum Ende
                
                self.completed_count += 1
                self._update_files_counter()
                self.update()
            
            self.after(0, do_add)
    
    def update_progress(self, bytes_done, total_bytes, status, filename=None):
        """Update Progress mit LIVE Statistiken (mit Throttling für TurboModem)"""
        current_time = time.time()
        time_since_last = current_time - self.last_update_time
        
        # Immer beim ersten Update oder wenn genug Zeit vergangen ist
        if self.last_update_time == 0 or time_since_last >= self.min_update_interval:
            self._do_update(bytes_done, total_bytes, status, filename)
            self.last_update_time = current_time
            
            if self.pending_update:
                self.after_cancel(self.pending_update)
                self.pending_update = None
        else:
            if not self.pending_update:
                delay_ms = int((self.min_update_interval - time_since_last) * 1000)
                self.pending_update = self.after(delay_ms, 
                    lambda: self._do_update(bytes_done, total_bytes, status, filename))
    
    def _do_update(self, bytes_done, total_bytes, status, filename=None):
        """Actual update logic mit allen Live-Statistiken"""
        try:
            current_time = time.time()
            
            # === DATEINAME UPDATE ===
            if filename and filename != self.current_filename:
                self.current_filename = filename
                self.current_file_var.set(filename)
                self.current_file_start_bytes = self.last_bytes
                self.current_file_label.configure(foreground='blue')
            elif not filename and status:
                # Wenn kein filename, versuche aus status zu extrahieren
                # TurboModem Status: "Sent 123 KB"
                if not self.current_filename or self.current_filename == "Waiting...":
                    self.current_file_var.set(status)
            
            # === GESCHWINDIGKEIT (gleitender Durchschnitt) ===
            time_diff = current_time - self.last_time
            if time_diff >= 0.1:
                bytes_diff = bytes_done - self.last_bytes
                
                if bytes_diff < 0:
                    bytes_diff = bytes_done
                    self.last_bytes = 0
                    self.speed_samples = []
                
                if time_diff > 0:
                    instant_speed = bytes_diff / time_diff
                    self.speed_samples.append((current_time, instant_speed))
                    self.speed_samples = [(t, s) for t, s in self.speed_samples 
                                          if current_time - t < 2.0]
                    
                    if self.speed_samples:
                        avg_speed = sum(s for _, s in self.speed_samples) / len(self.speed_samples)
                        self.speed_var.set(f"Speed: {self._format_speed(avg_speed)}")
                        
                        if total_bytes > 0 and avg_speed > 0:
                            remaining = total_bytes - bytes_done
                            eta_seconds = remaining / avg_speed
                            self.eta_var.set(f"ETA: {self._format_time(eta_seconds)}")
                        else:
                            self.eta_var.set("ETA: --")
                
                self.last_bytes = bytes_done
                self.last_time = current_time
            
            # === ELAPSED TIME ===
            elapsed = current_time - self.start_time
            self.elapsed_var.set(f"Elapsed: {self._format_time(elapsed)}")
            
            # === TOTAL BYTES ===
            self.total_bytes_var.set(f"Total: {bytes_done:,} bytes")
            
            # === PROGRESS BAR ===
            if total_bytes > 0:
                if self.file_progress['mode'] == 'indeterminate':
                    self.file_progress.stop()
                    self.file_progress.configure(mode='determinate')
                percent = (bytes_done / total_bytes) * 100
                self.file_progress['value'] = percent
                self.file_bytes_var.set(f"{bytes_done:,} / {total_bytes:,} bytes")
                self.file_percent_var.set(f"{percent:.1f}%")
            else:
                if self.file_progress['mode'] == 'determinate':
                    self.file_progress.configure(mode='indeterminate')
                    self.file_progress.start(50)
                self.file_bytes_var.set(f"{bytes_done:,} bytes")
                self.file_percent_var.set("--")
            
            # === FILES COUNTER (Multi-File) ===
            if self.total_files_count > 0:
                self.files_var.set(f"Files: {self.files_completed}/{self.total_files_count}")
            
            self.update()
            
        except tk.TclError:
            # Dialog wurde geschlossen - ignorieren
            pass
        except Exception:
            # Fehler nicht propagieren
            pass
    
    def _format_speed(self, bytes_per_sec):
        """Formatiert Geschwindigkeit"""
        if bytes_per_sec < 1024:
            return f"{bytes_per_sec:.0f} B/s"
        elif bytes_per_sec < 1024 * 1024:
            return f"{bytes_per_sec/1024:.1f} KB/s"
        else:
            return f"{bytes_per_sec/(1024*1024):.1f} MB/s"
    
    def _format_time(self, seconds):
        """Formatiert Zeit"""
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            return f"{seconds//60:.0f}m {seconds%60:.0f}s"
        else:
            return f"{seconds//3600:.0f}h {(seconds%3600)//60:.0f}m"
    
    def cancel(self):
        self.cancelled = True
        self.destroy()


class BBSDialDialog(tk.Toplevel):
    """BBS Dialer (F7) mit Editor und Preview-Bild"""
    
    def __init__(self, parent):
        super().__init__(parent)
        self.title("BBS Dialer")
        self.geometry("950x650")
        self.result = None
        self.current_photo = None  # Für Bildanzeige
        
        # Liste der BBS (aus Config oder hardcoded)
        self.bbs_list = [
            {
                "name": "The Hidden", 
                "host": "the-hidden.hopto.org", 
                "port": 64128,
                "username": "",
                "password": "",
                "send_delay": 100
            },
            {
                "name": "Cottonwood", 
                "host": "cottonwoodbbs.dyndns.org", 
                "port": 6502,
                "username": "",
                "password": "",
                "send_delay": 100
            }
        ]
        
        # Versuche BBS Liste aus Config zu laden
        self.load_bbs_list()
        
        # Header
        ttk.Label(self, text="📞 BBS Directory", font=('Arial', 14, 'bold')).pack(pady=10)
        
        # Hauptbereich: Links Liste, Rechts Bild+Details
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Linker Bereich: Toolbar + Listbox
        left_frame = ttk.Frame(main_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Toolbar über Listbox
        toolbar = ttk.Frame(left_frame)
        toolbar.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Button(toolbar, text="➕ New", command=self.new_entry, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="✏️ Edit", command=self.edit_entry, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="🗑️ Delete", command=self.delete_entry, width=10).pack(side=tk.LEFT, padx=2)
        
        # Listbox
        list_frame = ttk.Frame(left_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.listbox = tk.Listbox(list_frame, font=('Courier', 10), yscrollcommand=scrollbar.set,
                                   selectmode=tk.SINGLE, activestyle='none',
                                   selectbackground='#3399FF', selectforeground='white')
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.listbox.yview)
        
        self.refresh_listbox()
        
        # Bindings
        self.listbox.bind('<<ListboxSelect>>', self.on_select)
        self.listbox.bind('<Button-1>', self.on_click)  # Einzelklick
        self.listbox.bind('<Double-Button-1>', lambda e: self.connect())
        self.listbox.bind('<Button-3>', self.show_context_menu)  # Rechtsklick
        self.listbox.bind('<MouseWheel>', self.on_mousewheel)  # Windows
        self.listbox.bind('<Button-4>', self.on_mousewheel)  # Linux scroll up
        self.listbox.bind('<Button-5>', self.on_mousewheel)  # Linux scroll down
        self.listbox.bind('<Up>', self.on_arrow_key)
        self.listbox.bind('<Down>', self.on_arrow_key)
        self.listbox.bind('<Return>', lambda e: self.connect())  # Enter = Connect
        
        # Hotkeys: 1-9, A-Z
        for i in range(1, 10):  # 1-9
            self.bind(str(i), lambda e, idx=i-1: self.hotkey_connect(idx))
        for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':  # A-Z
            idx = 9 + (ord(c) - ord('A'))  # A=9, B=10, etc.
            self.bind(c.lower(), lambda e, idx=idx: self.hotkey_connect(idx))
            self.bind(c.upper(), lambda e, idx=idx: self.hotkey_connect(idx))
        
        # Context Menu
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="✏️ Edit Entry", command=self.edit_entry)
        self.context_menu.add_command(label="🗑️ Delete Entry", command=self.delete_entry)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="➕ New Entry", command=self.new_entry)
        
        # Rechter Bereich: Bild + Details
        right_frame = ttk.Frame(main_frame, width=400)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(10, 0))
        right_frame.pack_propagate(False)
        
        # Preview-Bild Frame
        preview_frame = ttk.LabelFrame(right_frame, text="BBS Preview", padding=5)
        preview_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Canvas für Bild (384x272 oder kleiner)
        self.preview_canvas = tk.Canvas(preview_frame, width=384, height=272, bg='#000000',
                                         highlightthickness=1, highlightbackground='#333333')
        self.preview_canvas.pack(pady=5)
        
        # Placeholder Text
        self.preview_canvas.create_text(192, 136, text="No Preview", fill='#666666', 
                                        font=('Arial', 12), tags='placeholder')
        
        # Details Frame (read-only anzeige)
        details_frame = ttk.LabelFrame(right_frame, text="Selected BBS Details", padding=10)
        details_frame.pack(fill=tk.X)
        
        # Grid für Details
        self.detail_labels = {}
        labels = [
            ('Host:', 'host_label'),
            ('Port:', 'port_label'),
            ('Username:', 'username_label'),
            ('Password:', 'password_label'),
            ('Send Delay:', 'delay_label'),
            ('Protocol:', 'protocol_label'),
            ('Speed:', 'speed_label')
        ]
        
        for row, (label_text, var_name) in enumerate(labels):
            ttk.Label(details_frame, text=label_text).grid(row=row, column=0, sticky=tk.W, padx=5, pady=2)
            label = ttk.Label(details_frame, text="-", foreground='blue')
            label.grid(row=row, column=1, sticky=tk.W, padx=5, pady=2)
            self.detail_labels[var_name] = label
        
        # Info Label
        info_frame = ttk.Frame(self)
        info_frame.pack(fill=tk.X, padx=10, pady=5)
        ttk.Label(info_frame, text="💡 Hotkeys: 1-9, A-Z | Double-click = Connect | Right-click = Edit/Delete | Scroll = Navigate", 
                 font=('Arial', 9, 'italic')).pack(anchor=tk.W)
        
        # Buttons
        button_frame = ttk.Frame(self)
        button_frame.pack(pady=10)
        
        ttk.Button(button_frame, text="Connect", command=self.connect, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.destroy, width=12).pack(side=tk.LEFT)
        
        self.transient(parent)
        self.grab_set()
        self.focus_set()  # WICHTIG: Setze Focus für Hotkeys!
        self.listbox.focus_set()  # Focus auf Listbox für Scrolling
        
        # Selektiere ersten Eintrag
        if self.bbs_list:
            self.listbox.selection_set(0)
            self.listbox.activate(0)
            self.on_select(None)
        
        # Zentriere Dialog auf Parent
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")
    
    def refresh_listbox(self):
        """Aktualisiert die Listbox mit Hotkey-Nummern"""
        self.listbox.delete(0, tk.END)
        for idx, bbs in enumerate(self.bbs_list):
            # Hotkey: 1-9, dann A-Z
            if idx < 9:
                hotkey = str(idx + 1)
            elif idx < 35:
                hotkey = chr(ord('A') + (idx - 9))
            else:
                hotkey = "-"
            
            username_info = f" [{bbs.get('username', '')}]" if bbs.get('username') else ""
            self.listbox.insert(tk.END, f"[{hotkey}] {bbs['name']:20s} {bbs['host']}:{bbs['port']}{username_info}")
    
    def on_click(self, event):
        """Einzelklick - Wählt Eintrag aus und zeigt Details"""
        index = self.listbox.nearest(event.y)
        if 0 <= index < len(self.bbs_list):
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(index)
            self.listbox.activate(index)
            self.on_select(None)
    
    def on_mousewheel(self, event):
        """Mausrad - Navigiert durch Einträge"""
        if not self.bbs_list:
            return
        
        # Bestimme Scroll-Richtung
        if event.num == 5 or event.delta < 0:  # Scroll down
            delta = 1
        elif event.num == 4 or event.delta > 0:  # Scroll up
            delta = -1
        else:
            return
        
        # Hole aktuelle Selektion
        selection = self.listbox.curselection()
        if selection:
            current_idx = selection[0]
        else:
            current_idx = 0
        
        # Berechne neuen Index
        new_idx = current_idx + delta
        new_idx = max(0, min(new_idx, len(self.bbs_list) - 1))
        
        # Setze neue Selektion
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(new_idx)
        self.listbox.activate(new_idx)
        self.listbox.see(new_idx)
        self.on_select(None)
        
        return "break"  # Verhindere Standard-Scroll
    
    def on_arrow_key(self, event):
        """Pfeiltasten - Navigiert durch Einträge"""
        if not self.bbs_list:
            return
        
        selection = self.listbox.curselection()
        if selection:
            current_idx = selection[0]
        else:
            current_idx = 0
        
        if event.keysym == 'Up':
            new_idx = max(0, current_idx - 1)
        elif event.keysym == 'Down':
            new_idx = min(len(self.bbs_list) - 1, current_idx + 1)
        else:
            return
        
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(new_idx)
        self.listbox.activate(new_idx)
        self.listbox.see(new_idx)
        self.on_select(None)
    
    def load_preview_image(self, index):
        """Lädt Preview-Bild basierend auf dem Hostnamen des BBS"""
        try:
            from PIL import Image, ImageTk
            import glob
            
            # Hole BBS-Eintrag
            if index < 0 or index >= len(self.bbs_list):
                return
            
            bbs = self.bbs_list[index]
            hostname = bbs.get('host', '').lower()
            
            # Extrahiere Keywords aus Hostname
            # z.B. "the-hidden.hopto.org" → ["the-hidden", "hidden", "the", "hopto"]
            # z.B. "cottonwoodbbs.dyndns.org" → ["cottonwoodbbs", "cottonwood", "dyndns"]
            keywords = []
            
            # Erste Subdomain (vor dem ersten Punkt)
            if '.' in hostname:
                first_part = hostname.split('.')[0]
            else:
                first_part = hostname
            
            keywords.append(first_part)
            
            # Wenn mit "the-" beginnt, auch ohne "the-" suchen
            if first_part.startswith('the-'):
                keywords.append(first_part[4:])
            
            # Wenn "-bbs" am Ende, auch ohne "-bbs" suchen
            if first_part.endswith('-bbs') or first_part.endswith('bbs'):
                if first_part.endswith('-bbs'):
                    keywords.append(first_part[:-4])
                elif first_part.endswith('bbs') and len(first_part) > 3:
                    keywords.append(first_part[:-3])
            
            # Teile durch Bindestriche
            for part in first_part.split('-'):
                if part and part not in ['the', 'bbs'] and len(part) > 2:
                    keywords.append(part)
            
            # Script-Verzeichnis
            script_dir = os.path.dirname(os.path.abspath(__file__))
            
            # Suche nach PNG-Dateien die einen Keyword enthalten
            found_image = None
            for keyword in keywords:
                if not keyword:
                    continue
                # Suche case-insensitive
                pattern = os.path.join(script_dir, f"*{keyword}*.png")
                matches = glob.glob(pattern, recursive=False)
                
                # Auch mit anderem Case probieren
                if not matches:
                    pattern = os.path.join(script_dir, f"*{keyword.lower()}*.png")
                    matches = glob.glob(pattern, recursive=False)
                
                if not matches:
                    pattern = os.path.join(script_dir, f"*{keyword.upper()}*.png")
                    matches = glob.glob(pattern, recursive=False)
                
                if matches:
                    # Nimm das erste Match
                    found_image = matches[0]
                    break
            
            if found_image and os.path.exists(found_image):
                img = Image.open(found_image)
                
                # Skaliere auf 384x272 wenn nötig
                img = img.resize((384, 272), Image.Resampling.LANCZOS)
                
                self.current_photo = ImageTk.PhotoImage(img)
                
                # Lösche altes Bild und Placeholder
                self.preview_canvas.delete('all')
                self.preview_canvas.create_image(192, 136, image=self.current_photo)
            else:
                # Kein Bild vorhanden - zeige Placeholder mit Hostname
                self.preview_canvas.delete('all')
                hint = f"({first_part}.png)" if first_part else "(no host)"
                self.preview_canvas.create_text(192, 136, text=f"No Preview\n{hint}", 
                                                fill='#666666', font=('Arial', 11), 
                                                justify='center', tags='placeholder')
                self.current_photo = None
        except Exception as e:
            debug_print(f"Error loading preview image: {e}")
            self.preview_canvas.delete('all')
            self.preview_canvas.create_text(192, 136, text="Error loading image", 
                                            fill='#FF6666', font=('Arial', 10), tags='placeholder')
            self.current_photo = None
    
    def hotkey_connect(self, index):
        """Verbindet mit BBS via Hotkey"""
        if 0 <= index < len(self.bbs_list):
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(index)
            self.listbox.see(index)
            self.connect()
    
    def show_context_menu(self, event):
        """Zeigt Context Menu bei Rechtsklick"""
        # Selektiere Item unter Maus
        index = self.listbox.nearest(event.y)
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(index)
        self.listbox.activate(index)
        
        # Zeige Menu
        self.context_menu.post(event.x_root, event.y_root)
    
    def new_entry(self):
        """Erstellt neuen BBS Eintrag"""
        dialog = BBSEditDialog(self, None)
        self.wait_window(dialog)
        
        if dialog.result:
            self.bbs_list.append(dialog.result)
            self.save_bbs_list()
            self.refresh_listbox()
            # Selektiere neuen Eintrag
            self.listbox.selection_set(len(self.bbs_list) - 1)
            self.on_select(None)
    
    def edit_entry(self):
        """Editiert ausgewählten BBS Eintrag"""
        selection = self.listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Please select an entry first!")
            return
        
        idx = selection[0]
        bbs = self.bbs_list[idx]
        
        dialog = BBSEditDialog(self, bbs)
        self.wait_window(dialog)
        
        if dialog.result:
            self.bbs_list[idx] = dialog.result
            self.save_bbs_list()
            self.refresh_listbox()
            # Selektiere editierten Eintrag
            self.listbox.selection_set(idx)
            self.on_select(None)
    
    def delete_entry(self):
        """Löscht ausgewählten BBS Eintrag"""
        selection = self.listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Please select an entry first!")
            return
        
        idx = selection[0]
        bbs = self.bbs_list[idx]
        
        if messagebox.askyesno("Confirm Delete", 
                               f"BBS '{bbs['name']}' wirklich löschen?"):
            del self.bbs_list[idx]
            self.save_bbs_list()
            self.refresh_listbox()
    
    def load_bbs_list(self):
        """Lädt BBS Liste aus bbs_config.json"""
        try:
            if os.path.exists('bbs_config.json'):
                with open('bbs_config.json', 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    if 'bbs_list' in config and isinstance(config['bbs_list'], list):
                        # Validiere jeden Eintrag
                        valid_list = []
                        for bbs in config['bbs_list']:
                            if isinstance(bbs, dict) and 'name' in bbs and 'host' in bbs:
                                # Stelle sicher dass alle Felder existieren
                                bbs.setdefault('description', '')
                                bbs.setdefault('port', 23)
                                bbs.setdefault('username', '')
                                bbs.setdefault('password', '')
                                bbs.setdefault('send_delay', 100)
                                valid_list.append(bbs)
                        if valid_list:
                            self.bbs_list = valid_list
                            debug_print(f"Loaded {len(valid_list)} BBS entries")
        except json.JSONDecodeError as e:
            print(f"JSON Error in bbs_config.json: {e}")
            print(f"Backing up corrupted file and using defaults")
            # Backup der kaputten Datei
            try:
                import shutil
                shutil.copy('bbs_config.json', 'bbs_config.json.bak')
            except:
                pass
        except Exception as e:
            print(f"Error loading BBS list: {e}")
    
    def save_bbs_list(self):
        """Speichert BBS Liste in bbs_config.json"""
        try:
            config = {}
            if os.path.exists('bbs_config.json'):
                try:
                    with open('bbs_config.json', 'r', encoding='utf-8') as f:
                        config = json.load(f)
                except json.JSONDecodeError:
                    print("Warning: Existing config was corrupted, creating new one")
                    config = {}
            
            config['bbs_list'] = self.bbs_list
            
            with open('bbs_config.json', 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            print(f"Saved {len(self.bbs_list)} BBS entries")
        except Exception as e:
            print(f"Error saving BBS list: {e}")
            import traceback
            traceback.print_exc()
    
    def on_select(self, event):
        """Wird aufgerufen wenn BBS ausgewählt wird"""
        selection = self.listbox.curselection()
        if selection:
            idx = selection[0]
            bbs = self.bbs_list[idx]
            self.detail_labels['host_label'].config(text=bbs.get('host', '-'))
            self.detail_labels['port_label'].config(text=str(bbs.get('port', '-')))
            self.detail_labels['username_label'].config(text=bbs.get('username', '-') or '(none)')
            
            # Password mit Sternchen anzeigen
            password = bbs.get('password', '')
            password_display = '*' * len(password) if password else '(none)'
            self.detail_labels['password_label'].config(text=password_display)
            
            self.detail_labels['delay_label'].config(text=f"{bbs.get('send_delay', 100)} ms")
            self.detail_labels['protocol_label'].config(text=bbs.get('protocol', 'TurboModem'))
            self.detail_labels['speed_label'].config(text=bbs.get('transfer_speed', 'normal'))
            
            # Lade Preview-Bild
            self.load_preview_image(idx)
    
    def connect(self):
        """Verbindet mit ausgewähltem BBS"""
        selection = self.listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Please select an entry first!")
            return
        
        bbs = self.bbs_list[selection[0]]
        if not bbs.get('host'):
            messagebox.showwarning("Invalid Entry", "This entry has no host!")
            return
        
        self.result = {
            'host': bbs['host'], 
            'port': bbs['port'],
            'username': bbs.get('username', ''),
            'password': bbs.get('password', ''),
            'send_delay': bbs.get('send_delay', 100),
            'protocol': bbs.get('protocol'),  # Protocol laden!
            'transfer_speed': bbs.get('transfer_speed', 'normal')  # Speed Profile laden!
        }
        self.destroy()


class BBSEditDialog(tk.Toplevel):
    """Dialog zum Editieren/Erstellen von BBS Einträgen"""
    
    def __init__(self, parent, bbs_data):
        super().__init__(parent)
        self.title("Edit BBS Entry" if bbs_data else "New BBS Entry")
        self.geometry("550x550")
        self.resizable(False, False)
        self.result = None
        
        # Header
        header_text = "✏️ Edit BBS Entry" if bbs_data else "➕ New BBS Entry"
        ttk.Label(self, text=header_text, font=('Arial', 14, 'bold')).pack(pady=10)
        
        # Form
        form_frame = ttk.Frame(self, padding=20)
        form_frame.pack(fill=tk.BOTH, expand=True)
        
        # Name
        row = 0
        ttk.Label(form_frame, text="Name:", font=('Arial', 10, 'bold')).grid(row=row, column=0, sticky=tk.W, pady=5)
        self.name_var = tk.StringVar(value=bbs_data.get('name', '') if bbs_data else '')
        ttk.Entry(form_frame, textvariable=self.name_var, width=40).grid(row=row, column=1, pady=5, sticky=tk.W)
        
        # Description
        row += 1
        ttk.Label(form_frame, text="Description:", font=('Arial', 10)).grid(row=row, column=0, sticky=tk.W, pady=5)
        self.description_var = tk.StringVar(value=bbs_data.get('description', '') if bbs_data else '')
        ttk.Entry(form_frame, textvariable=self.description_var, width=40).grid(row=row, column=1, pady=5, sticky=tk.W)
        
        # Host
        row += 1
        ttk.Label(form_frame, text="Host:", font=('Arial', 10, 'bold')).grid(row=row, column=0, sticky=tk.W, pady=5)
        self.host_var = tk.StringVar(value=bbs_data.get('host', '') if bbs_data else '')
        ttk.Entry(form_frame, textvariable=self.host_var, width=40).grid(row=row, column=1, pady=5, sticky=tk.W)
        
        # Port
        row += 1
        ttk.Label(form_frame, text="Port:", font=('Arial', 10, 'bold')).grid(row=row, column=0, sticky=tk.W, pady=5)
        self.port_var = tk.StringVar(value=str(bbs_data.get('port', 23)) if bbs_data else '23')
        ttk.Entry(form_frame, textvariable=self.port_var, width=10).grid(row=row, column=1, pady=5, sticky=tk.W)
        
        # Separator
        row += 1
        ttk.Separator(form_frame, orient='horizontal').grid(row=row, column=0, columnspan=2, sticky='ew', pady=10)
        
        # Username
        row += 1
        ttk.Label(form_frame, text="Username:", font=('Arial', 10)).grid(row=row, column=0, sticky=tk.W, pady=5)
        self.username_var = tk.StringVar(value=bbs_data.get('username', '') if bbs_data else '')
        ttk.Entry(form_frame, textvariable=self.username_var, width=40).grid(row=row, column=1, pady=5, sticky=tk.W)
        
        # Password
        row += 1
        ttk.Label(form_frame, text="Password:", font=('Arial', 10)).grid(row=row, column=0, sticky=tk.W, pady=5)
        self.password_var = tk.StringVar(value=bbs_data.get('password', '') if bbs_data else '')
        self.password_entry = ttk.Entry(form_frame, textvariable=self.password_var, width=40, show="*")
        self.password_entry.grid(row=row, column=1, pady=5, sticky=tk.W)
        
        # Show Password Checkbox
        row += 1
        self.show_password_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(form_frame, text="Show Password", variable=self.show_password_var,
                       command=self.toggle_password).grid(row=row, column=1, sticky=tk.W, pady=2)
        
        # Send Delay
        row += 1
        ttk.Label(form_frame, text="Send Delay (ms):", font=('Arial', 10)).grid(row=row, column=0, sticky=tk.W, pady=5)
        self.delay_var = tk.StringVar(value=str(bbs_data.get('send_delay', 100)) if bbs_data else '100')
        ttk.Entry(form_frame, textvariable=self.delay_var, width=10).grid(row=row, column=1, pady=5, sticky=tk.W)
        
        ttk.Label(form_frame, text="(Zeit zwischen Username und Password)", 
                 font=('Arial', 8, 'italic')).grid(row=row+1, column=0, columnspan=2, sticky=tk.W)
        
        # Transfer Protocol
        row += 2
        ttk.Separator(form_frame, orient='horizontal').grid(row=row, column=0, columnspan=2, sticky='ew', pady=10)
        
        row += 1
        ttk.Label(form_frame, text="Transfer Protocol:", font=('Arial', 10, 'bold')).grid(row=row, column=0, sticky=tk.W, pady=5)
        
        # Protocol Dropdown
        from file_transfer import TransferProtocol
        current_protocol = bbs_data.get('protocol', 'TurboModem') if bbs_data else 'TurboModem'
        self.protocol_var = tk.StringVar(value=current_protocol)
        
        protocol_options = [p.value for p in [TransferProtocol.RAWTCP,       # 🚀 MAX SPEED (LAN)
                                               TransferProtocol.TURBOMODEM,   # ⚡ ULTRA FAST!
                                               TransferProtocol.PUNTER,       # 📦 Multi-File
                                               TransferProtocol.XMODEM_1K,
                                               TransferProtocol.XMODEM_CRC, 
                                               TransferProtocol.XMODEM,
                                               TransferProtocol.YMODEM]]
        
        protocol_combo = ttk.Combobox(form_frame, textvariable=self.protocol_var, 
                                     values=protocol_options, state='readonly', width=18)
        protocol_combo.grid(row=row, column=1, pady=5, sticky=tk.W)
        
        ttk.Label(form_frame, text="🚀 RawTCP = MAX LAN Speed!", 
                 font=('Arial', 8, 'italic')).grid(row=row+1, column=0, columnspan=2, sticky=tk.W)
        
        # Transfer Speed
        row += 2
        ttk.Label(form_frame, text="Transfer Speed:", font=('Arial', 10)).grid(row=row, column=0, sticky=tk.W, pady=5)
        
        from file_transfer import TransferSpeed
        current_speed = bbs_data.get('transfer_speed', 'normal') if bbs_data else 'normal'
        self.speed_var = tk.StringVar(value=current_speed)
        
        speed_options = [s.value for s in TransferSpeed]
        speed_combo = ttk.Combobox(form_frame, textvariable=self.speed_var, 
                                  values=speed_options, state='readonly', width=18)
        speed_combo.grid(row=row, column=1, pady=5, sticky=tk.W)
        
        ttk.Label(form_frame, text="(Timing between ACK and next block)", 
                 font=('Arial', 8, 'italic')).grid(row=row+1, column=0, columnspan=2, sticky=tk.W)
        
        # Buttons
        button_frame = ttk.Frame(self)
        button_frame.pack(pady=10)
        
        ttk.Button(button_frame, text="Save", command=self.save, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.destroy, width=12).pack(side=tk.LEFT)
        
        # Enter key = Save
        self.bind('<Return>', lambda e: self.save())
        
        self.transient(parent)
        self.grab_set()
        
        # Zentriere Dialog
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")
        
        # Focus auf Name Field
        form_frame.after(100, lambda: self.name_var and self.focus())
    
    def toggle_password(self):
        """Zeigt/Versteckt Password"""
        if self.show_password_var.get():
            self.password_entry.config(show="")
        else:
            self.password_entry.config(show="*")
    
    def save(self):
        """Speichert Eintrag"""
        name = self.name_var.get().strip()
        host = self.host_var.get().strip()
        port_str = self.port_var.get().strip()
        
        # Validierung
        if not name:
            messagebox.showerror("Error", "Name cannot be empty!")
            return
        
        if not host:
            messagebox.showerror("Error", "Host cannot be empty!")
            return
        
        try:
            port = int(port_str)
            if port < 1 or port > 65535:
                raise ValueError()
        except:
            messagebox.showerror("Error", "Port muss zwischen 1 und 65535 sein!")
            return
        
        try:
            delay = int(self.delay_var.get())
            if delay < 0 or delay > 10000:
                raise ValueError()
        except:
            messagebox.showerror("Error", "Send Delay muss zwischen 0 und 10000 ms sein!")
            return
        
        # Speichere Ergebnis
        self.result = {
            'name': name,
            'description': self.description_var.get().strip(),
            'host': host,
            'port': port,
            'username': self.username_var.get().strip(),
            'password': self.password_var.get(),  # Nicht trimmen!
            'send_delay': delay,
            'protocol': self.protocol_var.get(),  # Transfer Protocol!
            'transfer_speed': self.speed_var.get()  # Transfer Speed Profile!
        }
        
        self.destroy()


class SettingsDialog(tk.Toplevel):
    """Settings Dialog (F5) - Two Column Layout"""
    
    def __init__(self, parent, current_protocol, current_width):
        super().__init__(parent)
        self.parent = parent
        self.title("Terminal Settings")
        self.resizable(True, True)
        self.result = None
        
        # Header
        ttk.Label(self, text="⚙️ Terminal Settings", font=('Arial', 14, 'bold')).pack(pady=10)
        
        # Main container with two columns
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # LEFT COLUMN
        left_col = ttk.Frame(main_frame)
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        
        # RIGHT COLUMN
        right_col = ttk.Frame(main_frame)
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))
        
        # ========== LEFT COLUMN ==========
        
        # Protocol
        proto_frame = ttk.LabelFrame(left_col, text="Transfer Protocol", padding=10)
        proto_frame.pack(fill=tk.X, pady=5)
        
        self.proto_var = tk.StringVar(value=current_protocol.value)
        
        for proto in [TransferProtocol.RAWTCP,
                      TransferProtocol.TURBOMODEM,
                      TransferProtocol.PUNTER,
                      TransferProtocol.XMODEM_1K,
                      TransferProtocol.XMODEM_CRC, 
                      TransferProtocol.XMODEM, 
                      TransferProtocol.YMODEM]:
            ttk.Radiobutton(proto_frame, text=proto.value, variable=self.proto_var, value=proto.value).pack(anchor=tk.W)
        
        ttk.Label(proto_frame, text="🚀 RawTCP: MAX LAN Speed (~12 MB/s)!", 
                 font=('Arial', 8, 'italic')).pack(anchor=tk.W, pady=(5, 0))
        ttk.Label(proto_frame, text="⚡ TurboModem: 10-20x faster than XModem!", 
                 font=('Arial', 8, 'italic')).pack(anchor=tk.W)
        ttk.Label(proto_frame, text="📦 Punter C1: Multi-File Downloads (C64 BBS)", 
                 font=('Arial', 8, 'italic')).pack(anchor=tk.W)
        ttk.Label(proto_frame, text="💡 YModem: Batch Transfer with filenames", 
                 font=('Arial', 8, 'italic')).pack(anchor=tk.W)
        
        # Transfer Speed Profile
        speed_frame = ttk.LabelFrame(left_col, text="Transfer Speed (XModem/YModem)", padding=10)
        speed_frame.pack(fill=tk.X, pady=5)
        
        from file_transfer import TransferSpeed
        current_speed = parent.settings.get('transfer_speed', 'normal')
        self.speed_var = tk.StringVar(value=current_speed)
        
        speed_options = [
            ("Turbo", "turbo", "20ms delay"),
            ("Fast", "fast", "50ms delay"),
            ("Normal", "normal", "150ms delay"),
            ("Slow", "slow", "300ms delay"),
            ("Local", "local", "500ms delay"),
        ]
        
        for label, value, desc in speed_options:
            frame = ttk.Frame(speed_frame)
            frame.pack(anchor=tk.W, fill=tk.X)
            ttk.Radiobutton(frame, text=label, variable=self.speed_var, value=value, width=8).pack(side=tk.LEFT)
            ttk.Label(frame, text=f"- {desc}", font=('Arial', 8)).pack(side=tk.LEFT, padx=5)
        
        ttk.Label(speed_frame, text="💡 Increase delay if transfers fail", 
                 font=('Arial', 8, 'italic')).pack(anchor=tk.W, pady=(5, 0))
        
        # Screen Width
        width_frame = ttk.LabelFrame(left_col, text="Screen Width", padding=10)
        width_frame.pack(fill=tk.X, pady=5)
        
        self.width_var = tk.IntVar(value=current_width)
        ttk.Radiobutton(width_frame, text="40 Columns (C64)", variable=self.width_var, value=40).pack(anchor=tk.W)
        ttk.Radiobutton(width_frame, text="80 Columns", variable=self.width_var, value=80).pack(anchor=tk.W)
        ttk.Label(width_frame, text="✨ Live switch - no restart needed", font=('Arial', 8, 'italic')).pack(anchor=tk.W, pady=5)
        
        # ========== RIGHT COLUMN ==========
        
        # Keyboard Layout
        keyboard_frame = ttk.LabelFrame(right_col, text="Keyboard Layout", padding=10)
        keyboard_frame.pack(fill=tk.X, pady=5)
        
        current_swap_zy = parent.settings.get('swap_zy', False)
        self.swap_zy_var = tk.BooleanVar(value=current_swap_zy)
        ttk.Checkbutton(keyboard_frame, text="Swap Z ↔ Y (US keyboard)", 
                       variable=self.swap_zy_var).pack(anchor=tk.W)
        
        ttk.Label(keyboard_frame, text="⌨️ German keyboard (QWERTZ) is default", 
                 font=('Arial', 8, 'italic')).pack(anchor=tk.W, pady=(5, 0))
        ttk.Label(keyboard_frame, text="💡 Enable for US/UK keyboard (QWERTY)", 
                 font=('Arial', 8, 'italic')).pack(anchor=tk.W)
        ttk.Label(keyboard_frame, text="📝 CTRL+A to Z sends Control Codes 0x01-0x1A", 
                 font=('Arial', 8, 'italic')).pack(anchor=tk.W, pady=(5, 0))
        
        # Transfer Debug
        debug_frame = ttk.LabelFrame(right_col, text="Transfer Debug", padding=10)
        debug_frame.pack(fill=tk.X, pady=5)
        
        current_transfer_debug = parent.settings.get('transfer_debug', False)
        self.transfer_debug_var = tk.BooleanVar(value=current_transfer_debug)
        ttk.Checkbutton(debug_frame, text="Enable Debug Mode", 
                       variable=self.transfer_debug_var).pack(anchor=tk.W)
        ttk.Label(debug_frame, text="(Activity Log + Manual Send Buttons)", 
                 font=('Arial', 8)).pack(anchor=tk.W)
        
        ttk.Label(debug_frame, text="💡 Waiting indicator and CTRL+X always available", 
                 font=('Arial', 8, 'italic')).pack(anchor=tk.W, pady=(5, 0))
        
        # Transfer Folders
        folders_frame = ttk.LabelFrame(right_col, text="Transfer Folders", padding=10)
        folders_frame.pack(fill=tk.X, pady=5)
        
        # Upload Folder
        ttk.Label(folders_frame, text="Upload:", font=('Arial', 9, 'bold')).grid(row=0, column=0, sticky=tk.W, pady=2)
        self.upload_folder_var = tk.StringVar(value=parent.settings.get('upload_folder', ''))
        upload_entry = ttk.Entry(folders_frame, textvariable=self.upload_folder_var, width=22)
        upload_entry.grid(row=0, column=1, padx=3, pady=2)
        ttk.Button(folders_frame, text="...", command=self.browse_upload_folder, width=3).grid(row=0, column=2, pady=2)
        
        # Download Folder
        ttk.Label(folders_frame, text="Download:", font=('Arial', 9, 'bold')).grid(row=1, column=0, sticky=tk.W, pady=2)
        self.download_folder_var = tk.StringVar(value=parent.settings.get('download_folder', ''))
        download_entry = ttk.Entry(folders_frame, textvariable=self.download_folder_var, width=22)
        download_entry.grid(row=1, column=1, padx=3, pady=2)
        ttk.Button(folders_frame, text="...", command=self.browse_download_folder, width=3).grid(row=1, column=2, pady=2)
        
        ttk.Label(folders_frame, text="💡 Leave empty to be asked each time", 
                 font=('Arial', 8, 'italic')).grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(5, 0))
        
        # Save Button
        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=15)
        ttk.Button(btn_frame, text="Save", command=self.save, width=12).pack()
        
        self.transient(parent)
        self.grab_set()
        
        # Calculate optimal size
        self.update_idletasks()
        
        # Set geometry - wider layout, adequate height
        self.geometry("620x680")
        
        # Center dialog
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")
    
    def browse_upload_folder(self):
        """Select Upload Folder"""
        folder = filedialog.askdirectory(
            parent=self,
            title="Select Upload Folder", 
            initialdir=self.upload_folder_var.get() or None
        )
        
        # Bring dialog to front (Linux fix)
        self.lift()
        self.focus_force()
        
        if folder:
            self.upload_folder_var.set(folder)
    
    def browse_download_folder(self):
        """Select Download Folder"""
        folder = filedialog.askdirectory(
            parent=self,
            title="Select Download Folder",
            initialdir=self.download_folder_var.get() or None
        )
        
        # Bring dialog to front (Linux fix)
        self.lift()
        self.focus_force()
        
        if folder:
            self.download_folder_var.set(folder)
    
    def save(self):
        for proto in TransferProtocol:
            if proto.value == self.proto_var.get():
                self.result = {
                    'protocol': proto, 
                    'width': self.width_var.get(),
                    'upload_folder': self.upload_folder_var.get(),
                    'download_folder': self.download_folder_var.get(),
                    'transfer_speed': self.speed_var.get(),
                    'swap_zy': self.swap_zy_var.get(),
                    'transfer_debug': self.transfer_debug_var.get()
                }
                break
        self.destroy()


class ToolsMenuDialog(tk.Toplevel):
    """F10 - Tools Menu für Disk/Archive Operationen"""
    
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Tools Menu")
        self.geometry("500x400")
        self.parent = parent
        
        # Header
        header = ttk.Label(self, text="🔧 Tools & Utilities", font=('Arial', 14, 'bold'))
        header.pack(pady=15)
        
        # Tools Frame
        tools_frame = ttk.LabelFrame(self, text="Disk & Archive Tools", padding=15)
        tools_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        
        # Buttons für Tools
        btn_width = 30
        
        ttk.Button(tools_frame, text="ZIP to D64", 
                  command=self.zip_to_d64, width=btn_width).pack(pady=5)
        
        ttk.Button(tools_frame, text="D64 to ZIP", 
                  command=self.d64_to_zip, width=btn_width).pack(pady=5)
        
        ttk.Button(tools_frame, text="LNX to D64", 
                  command=self.lnx_to_d64, width=btn_width).pack(pady=5)
        
        ttk.Button(tools_frame, text="📀 D64/D71/D81 Directory Viewer", 
                  command=self.dxx_display, width=btn_width).pack(pady=5)
        
        # Info Label
        info = ttk.Label(self, text="Converter & Viewer Tools for C64 Disk Images", 
                        font=('Arial', 9, 'italic'))
        info.pack(pady=10)
        
        # Close Button
        ttk.Button(self, text="Close", command=self.destroy, width=15).pack(pady=10)
        
        self.transient(parent)
        self.grab_set()
        
        # Zentriere Dialog auf Parent
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")
    
    def zip_to_d64(self):
        """ZIP to D64 Converter - Konvertiert ZipCode (1!xxx.prg etc.) zu D64"""
        filepath = filedialog.askopenfilename(
            parent=self,
            title="Select ZipCode File (1!*.prg)",
            filetypes=[
                ("ZipCode Files", "1!*"),
                ("PRG Files", "*.prg *.PRG"),
                ("All Files", "*.*")
            ]
        )
        
        if not filepath:
            return
        
        # Extrahiere base name aus "1!xxxxxx.prg" -> "xxxxxx.prg"
        import os
        dirname = os.path.dirname(filepath)
        filename = os.path.basename(filepath)
        
        if filename.startswith("1!"):
            base_name = filename[2:]  # Remove "1!"
        else:
            messagebox.showwarning("Warning", 
                "Filename should start with '1!'.\nTrying anyway...",
                parent=self)
            base_name = filename
        
        # Output D64 Name
        base_without_ext = os.path.splitext(base_name)[0]
        d64_name = base_without_ext + ".d64"
        d64_path = os.path.join(dirname, d64_name)
        
        # Konvertiere
        try:
            from tools import zipcode_to_d64
            result = zipcode_to_d64(base_name if not dirname else os.path.join(dirname, base_name), d64_path)
            
            if result == 0:
                messagebox.showinfo("Success", 
                    f"D64 Image erstellt:\n{d64_path}",
                    parent=self)
                # Öffne D64 Viewer
                self.destroy()
                D64ViewerDialog(self.parent, d64_path)
            else:
                messagebox.showerror("Error", 
                    "Conversion error.\nCheck if all 4 ZipCode files are present.",
                    parent=self)
        except Exception as e:
            messagebox.showerror("Error", f"Error: {e}", parent=self)
    
    def d64_to_zip(self):
        """D64 to ZIP Converter - Converts D64 to ZipCode Files"""
        filepath = filedialog.askopenfilename(
            parent=self,
            title="Select D64 Image",
            filetypes=[
                ("D64 Files", "*.d64 *.D64"),
                ("All Files", "*.*")
            ]
        )
        
        if not filepath:
            return
        
        import os
        dirname = os.path.dirname(filepath)
        filename = os.path.basename(filepath)
        base_without_ext = os.path.splitext(filename)[0]
        prg_name = base_without_ext + ".prg"
        prg_base = os.path.join(dirname, prg_name) if dirname else prg_name
        
        # Konvertiere
        try:
            from tools import d64_to_zipcode
            result, created_files = d64_to_zipcode(filepath, prg_base)
            
            if result == 0:
                files_str = "\n".join(os.path.basename(f) for f in created_files)
                messagebox.showinfo("Success", 
                    f"ZipCode Files created:\n{files_str}",
                    parent=self)
            else:
                messagebox.showerror("Error", 
                    "Conversion error.",
                    parent=self)
        except Exception as e:
            messagebox.showerror("Error", f"Error: {e}", parent=self)
    
    def lnx_to_d64(self):
        """LNX to D64 Converter - Converts Lynx Archive to D64"""
        filepath = filedialog.askopenfilename(
            parent=self,
            title="Select Lynx Archive",
            filetypes=[
                ("Lynx Files", "*lnx* *LNX*"),
                ("PRG Files", "*.prg *.PRG"),
                ("All Files", "*.*")
            ]
        )
        
        if not filepath:
            return
        
        import os
        dirname = os.path.dirname(filepath)
        filename = os.path.basename(filepath)
        
        # Remove .lnx or .LNX.prg extensions
        base = filename
        for ext in ['.lnx.prg', '.LNX.prg', '.lnx', '.LNX', '.prg', '.PRG']:
            if base.lower().endswith(ext.lower()):
                base = base[:-len(ext)]
                break
        
        d64_name = base + ".d64"
        d64_path = os.path.join(dirname, d64_name) if dirname else d64_name
        
        # Convert
        try:
            from tools import lnx_to_d64
            result = lnx_to_d64(filepath, d64_path)
            
            if result == 0:
                messagebox.showinfo("Success", 
                    f"D64 Image created:\n{d64_path}",
                    parent=self)
                # Open D64 Viewer
                self.destroy()
                D64ViewerDialog(self.parent, d64_path)
            else:
                messagebox.showerror("Error", 
                    "Conversion error.\nFile may not be a standard LNX.",
                    parent=self)
        except Exception as e:
            messagebox.showerror("Error", f"Error: {e}", parent=self)
    
    def dxx_display(self):
        """Dxx Display Viewer - shows D64/D71/D81 Directory with PETSCII Renderer"""
        # File Dialog für D64/D71/D81/D2M/D4M/DNP
        filepath = filedialog.askopenfilename(
            parent=self,
            title="Select Disk Image",
            filetypes=[
                ("All Disk Images", "*.d64 *.d71 *.d81 *.d2m *.d4m *.dnp *.D64 *.D71 *.D81 *.D2M *.D4M *.DNP"),
                ("D64 Files", "*.d64 *.D64"),
                ("D71 Files", "*.d71 *.D71"),
                ("D81 Files", "*.d81 *.D81"),
                ("D2M Files (CMD FD2000)", "*.d2m *.D2M"),
                ("D4M Files (CMD FD4000)", "*.d4m *.D4M"),
                ("DNP Files (CMD Native)", "*.dnp *.DNP"),
                ("All Files", "*.*")
            ]
        )
        
        if not filepath:
            return
        
        # Öffne D64 Viewer Dialog
        viewer = D64ViewerDialog(self.parent, filepath)


class D64ViewerDialog(tk.Toplevel):
    """Dialog zum Anzeigen von D64/D71/D81 Directory mit PETSCII Renderer"""
    
    def __init__(self, parent, filepath):
        super().__init__(parent)
        self.parent = parent
        self.filepath = filepath
        self.use_uppercase = True  # Toggle für Upper/Lower Charset
        self.entries = None  # Gespeicherte Directory-Einträge
        
        import os
        filename = os.path.basename(filepath)
        self.title(f"D64 Viewer - {filename}")
        self.geometry("700x500")
        
        # Header
        header = ttk.Label(self, text=f"📀 {filename}", font=('Arial', 14, 'bold'))
        header.pack(pady=10)
        
        # Canvas für PETSCII Rendering
        canvas_frame = ttk.Frame(self)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Scrollbar
        scrollbar_y = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL)
        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        
        scrollbar_x = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL)
        scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.canvas = tk.Canvas(canvas_frame, 
                               bg='#3F3FD7',  # C64 Blau
                               highlightthickness=0,
                               xscrollcommand=scrollbar_x.set,
                               yscrollcommand=scrollbar_y.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scrollbar_y.config(command=self.canvas.yview)
        scrollbar_x.config(command=self.canvas.xview)
        
        # Mausrad-Scrolling
        def on_mousewheel(event):
            # Windows/Mac: event.delta, Linux: event.num
            if event.delta:
                self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            elif event.num == 4:
                self.canvas.yview_scroll(-3, "units")
            elif event.num == 5:
                self.canvas.yview_scroll(3, "units")
        
        # Bind für verschiedene Plattformen
        self.canvas.bind("<MouseWheel>", on_mousewheel)  # Windows/Mac
        self.canvas.bind("<Button-4>", on_mousewheel)    # Linux scroll up
        self.canvas.bind("<Button-5>", on_mousewheel)    # Linux scroll down
        
        # Button Frame mit Toggle und Close
        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=10)
        
        # Toggle Button für Upper/Lower Charset
        self.charset_var = tk.StringVar(value="UPPERCASE")
        self.toggle_btn = ttk.Button(btn_frame, text="Toggle: UPPERCASE", 
                                      command=self.toggle_charset, width=20)
        self.toggle_btn.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(btn_frame, text="Close", command=self.destroy, width=15).pack(side=tk.LEFT, padx=5)
        
        # Lade und zeige Directory
        self.load_and_display()
        
        self.transient(parent)
        self.grab_set()
        
        # Zentriere Dialog
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")
    
    def toggle_charset(self):
        """Wechselt zwischen Upper und Lower Charset"""
        self.use_uppercase = not self.use_uppercase
        mode = "UPPERCASE" if self.use_uppercase else "lowercase"
        self.toggle_btn.config(text=f"Toggle: {mode}")
        
        # Neu rendern wenn Einträge vorhanden
        if self.entries:
            self.render_directory()
    
    def load_and_display(self):
        """Lädt D64 und zeigt Directory mit PETSCII Renderer"""
        try:
            from PIL import Image, ImageTk
            import os
            
            # Importiere tools.py
            try:
                from tools import DiskImageViewer, render_directory_to_image
            except ImportError:
                # Fallback: tools.py im gleichen Verzeichnis suchen
                import sys
                script_dir = os.path.dirname(os.path.abspath(__file__))
                if script_dir not in sys.path:
                    sys.path.insert(0, script_dir)
                from tools import DiskImageViewer, render_directory_to_image
            
            # Lese Directory direkt aus D64
            viewer = DiskImageViewer(self.filepath)
            self.entries = viewer.read_directory()
            
            if not self.entries:
                self.show_error("No directory entries found")
                return
            
            # Rendere Directory
            self.render_directory()
            
        except FileNotFoundError as e:
            self.show_error(f"File not found:\n{e}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.show_error(f"Error reading file:\n{e}")
    
    def render_directory(self):
        """Rendert das Directory mit dem aktuellen Charset"""
        try:
            from PIL import Image, ImageTk
            import os
            from tools import render_directory_to_image
            
            # Wähle Font basierend auf Toggle
            font_name = "upper.bmp" if self.use_uppercase else "lower.bmp"
            script_dir = os.path.dirname(os.path.abspath(__file__))
            font_path = os.path.join(script_dir, font_name)
            if not os.path.exists(font_path):
                font_path = font_name
            
            if not os.path.exists(font_path):
                self.show_error(f"{font_name} not found!")
                return
            
            # Rendere zu Bild (C64 Blau/Weiß)
            screen_img = render_directory_to_image(
                self.entries, 
                font_path, 
                zoom=2,
                bg_color=(63, 63, 215),     # C64 Blau
                fg_color=(255, 255, 255)    # Weiß
            )
            
            # Lösche alte Bilder
            self.canvas.delete("all")
            
            # Zeige Bild
            self.photo = ImageTk.PhotoImage(screen_img)
            self.canvas.create_image(0, 0, image=self.photo, anchor=tk.NW)
            self.canvas.config(scrollregion=(0, 0, screen_img.width, screen_img.height))
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.show_error(f"Render error:\n{e}")
    
    def show_error(self, message):
        """Zeigt Fehlermeldung im Canvas"""
        self.canvas.create_text(
            20, 20,
            text=message,
            fill='white',
            font=('Courier', 12),
            anchor=tk.NW
        )


class HotkeyEditorDialog(tk.Toplevel):
    """Alt+H - Hotkey Editor mit PETSCII Support"""
    
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Hotkey Editor - Ctrl+Alt+F1 bis Ctrl+Alt+F10 (AltGr+F1-F10)")
        self.geometry("1400x800")  # NOCH größer für sichtbares Preview!
        self.parent = parent
        
        # Lade aktuelle Hotkeys
        self.hotkeys = {}
        self.load_hotkeys()
        
        # Header
        header = ttk.Label(self, text="⌨️ Hotkey Editor", font=('Arial', 16, 'bold'))
        header.pack(pady=10)
        
        info = ttk.Label(self, text="Define PETSCII hotkeys for Ctrl+Alt+F1 to Ctrl+Alt+F10 (AltGr+F1 to AltGr+F10)", 
                        font=('Arial', 9, 'italic'))
        info.pack(pady=5)
        
        # Main Frame mit Scrollbar
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Canvas + Scrollbar
        canvas = tk.Canvas(main_frame)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Hotkey Rows
        self.hotkey_rows = []
        
        for i in range(1, 11):  # F1 bis F10
            row_frame = self.create_hotkey_row(scrollable_frame, i)
            row_frame.pack(fill=tk.X, padx=5, pady=3)
        
        # Button Frame
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Button(btn_frame, text="💾 Save All Hotkeys", 
                  command=self.save_all_hotkeys, 
                  width=25).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(btn_frame, text="🔄 Reload from File", 
                  command=self.reload_hotkeys, 
                  width=25).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(btn_frame, text="✖ Close", 
                  command=self.destroy, 
                  width=15).pack(side=tk.RIGHT, padx=5)
        
        self.transient(parent)
        self.grab_set()
        
        # Zentriere Dialog
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")
    
    def create_hotkey_row(self, parent, fkey_num):
        """Erstellt eine Zeile für einen Hotkey"""
        row = ttk.Frame(parent, relief=tk.RIDGE, borderwidth=1)
        row.pack(fill=tk.X, padx=2, pady=2)
        
        # Label: Ctrl+Alt+F1 etc
        label = ttk.Label(row, text=f"Ctrl+Alt+F{fkey_num}:", 
                         font=('Arial', 10, 'bold'), width=10)
        label.pack(side=tk.LEFT, padx=5, pady=5)
        
        # Preview Frame (schwarzer Hintergrund, C64 Font)
        preview_frame = tk.Frame(row, bg='black', relief=tk.SUNKEN, borderwidth=2)
        preview_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Preview Label mit C64 Pro Mono Font
        try:
            # Versuche C64 Font zu laden
            font_path = os.path.join("fonts", "C64_Pro_Mono-STYLE.ttf")
            if os.path.exists(font_path):
                from PIL import ImageFont
                # Font für Preview
                c64_font = ('C64 Pro Mono', 10)
            else:
                c64_font = ('Courier', 10)
        except:
            c64_font = ('Courier', 10)
        
        preview_label = tk.Label(preview_frame, 
                                text="(empty)", 
                                font=c64_font,
                                bg='black', 
                                fg='#40E0D0',  # Cyan
                                anchor=tk.W,
                                justify=tk.LEFT)
        preview_label.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)
        
        # Button Frame
        btn_frame = ttk.Frame(row)
        btn_frame.pack(side=tk.RIGHT, padx=5)
        
        # View Button - Zeigt gerenderten Hotkey
        view_btn = ttk.Button(btn_frame, text="👁️ View", width=8,
                             command=lambda: self.view_hotkey(fkey_num))
        view_btn.pack(side=tk.LEFT, padx=2)
        
        # Edit Button
        edit_btn = ttk.Button(btn_frame, text="✏️ Edit", width=8,
                             command=lambda: self.edit_hotkey(fkey_num))
        edit_btn.pack(side=tk.LEFT, padx=2)
        
        # Delete Button
        delete_btn = ttk.Button(btn_frame, text="🗑️ Delete", width=8,
                               command=lambda: self.delete_hotkey(fkey_num))
        delete_btn.pack(side=tk.LEFT, padx=2)
        
        # Speichere Referenzen
        self.hotkey_rows.append({
            'fkey_num': fkey_num,
            'frame': row,
            'preview': preview_label,
            'view_btn': view_btn,
            'edit_btn': edit_btn,
            'delete_btn': delete_btn
        })
        
        # Lade und zeige Hotkey
        self.update_hotkey_preview(fkey_num)
        
        return row
    
    def update_hotkey_preview(self, fkey_num):
        """Aktualisiert Preview eines Hotkeys"""
        row = self.hotkey_rows[fkey_num - 1]
        preview = row['preview']
        
        if fkey_num in self.hotkeys:
            hotkey_bytes = self.hotkeys[fkey_num]
            
            # Konvertiere zu anzeigbarem Text
            # PETSCII → Screen Code → Text
            display_text = self.bytes_to_display(hotkey_bytes)
            
            preview.config(text=display_text if display_text else "(empty)", 
                          fg='white')
        else:
            preview.config(text="(empty)", fg='gray')
    
    def bytes_to_display(self, data):
        """Konvertiert PETSCII bytes zu Display-String"""
        result = []
        
        for byte in data:
            # Printable ASCII
            if 0x20 <= byte <= 0x7E:
                result.append(chr(byte))
            # Basic Colors (Ctrl+1-8)
            elif byte == 0x90:
                result.append("[BLK]")
            elif byte == 0x05:
                result.append("[WHT]")
            elif byte == 0x1C:
                result.append("[RED]")
            elif byte == 0x9F:
                result.append("[CYN]")
            elif byte == 0x9C:
                result.append("[PUR]")
            elif byte == 0x1E:
                result.append("[GRN]")
            elif byte == 0x1F:
                result.append("[BLU]")
            elif byte == 0x9E:
                result.append("[YEL]")
            # Extended Colors (Cbm+1-8)
            elif byte == 0x81:
                result.append("[ORG]")
            elif byte == 0x95:
                result.append("[BRN]")
            elif byte == 0x96:
                result.append("[LRD]")
            elif byte == 0x97:
                result.append("[DGR]")
            elif byte == 0x98:
                result.append("[GRY]")
            elif byte == 0x99:
                result.append("[LGN]")
            elif byte == 0x9A:
                result.append("[LBL]")
            elif byte == 0x9B:
                result.append("[LGY]")
            # Graphics Characters (Cbm+A-Z) - 0xC1-0xDA
            elif 0xC1 <= byte <= 0xDA:
                # Zeige als Grafik-Symbol
                result.append(f"[G{byte:02X}]")
            # Control Graphics (Ctrl+A-Z) - 0x01-0x1A
            elif 0x01 <= byte <= 0x1A:
                # Zeige als Ctrl-Grafik
                result.append(f"[C{byte:02X}]")
            # Additional Graphics - 0xA0-0xBF
            elif 0xA0 <= byte <= 0xBF:
                result.append(f"[G{byte:02X}]")
            # Control Codes
            elif byte == 0x0D:
                result.append("↵")
            elif byte == 0x93:
                result.append("[CLR]")
            elif byte == 0x13:
                result.append("[HOME]")
            elif byte == 0x14:
                result.append("[DEL]")
            elif byte == 0x12:
                result.append("[RVS ON]")
            elif byte == 0x92:
                result.append("[RVS OFF]")
            elif byte == 0x0E:
                result.append("[LOWER]")
            elif byte == 0x8E:
                result.append("[UPPER]")
            else:
                result.append(f"[{byte:02X}]")
        
        return ''.join(result)[:80]  # Max 80 chars
    
    def edit_hotkey(self, fkey_num):
        """Öffnet Editor-Dialog für Hotkey"""
        # Hole aktuellen Wert
        current_bytes = self.hotkeys.get(fkey_num, b'')
        
        # Öffne Edit-Dialog
        editor = HotkeyEditDialog(self, fkey_num, current_bytes)
        self.wait_window(editor)
        
        # Aktualisiere Preview nach Edit
        if editor.result is not None:
            if len(editor.result) > 0:
                self.hotkeys[fkey_num] = editor.result
            else:
                # Leerer String = Delete
                if fkey_num in self.hotkeys:
                    del self.hotkeys[fkey_num]
            
            self.update_hotkey_preview(fkey_num)
    
    def view_hotkey(self, fkey_num):
        """Zeigt gerenderten Hotkey im PETSCII Preview"""
        if fkey_num not in self.hotkeys:
            messagebox.showinfo("No Hotkey", 
                              f"Ctrl+Alt+F{fkey_num} is empty",
                              parent=self)
            return
        
        # Öffne Preview Dialog
        HotkeyPreviewDialog(self, fkey_num, self.hotkeys[fkey_num])
    
    def delete_hotkey(self, fkey_num):
        """Löscht einen Hotkey"""
        if fkey_num in self.hotkeys:
            del self.hotkeys[fkey_num]
            self.update_hotkey_preview(fkey_num)
    
    def load_hotkeys(self):
        """Lädt Hotkeys aus Datei"""
        hotkey_file = "hotkeys.seq"
        
        if not os.path.exists(hotkey_file):
            return
        
        try:
            with open(hotkey_file, 'rb') as f:
                file_data = f.read()
            
            # Splitte an CR (0x0D) oder LF (0x0A)
            lines = []
            current_line = bytearray()
            
            for byte in file_data:
                if byte == 0x0D or byte == 0x0A:
                    if len(current_line) > 0:
                        lines.append(bytes(current_line))
                        current_line = bytearray()
                else:
                    current_line.append(byte)
            
            if len(current_line) > 0:
                lines.append(bytes(current_line))
            
            # Weise Hotkeys zu (Zeile 1 = F1, Zeile 2 = F2, ...)
            for i, line_bytes in enumerate(lines[:10]):
                fkey_num = i + 1
                self.hotkeys[fkey_num] = line_bytes
                
            debug_print(f"Loaded {len(lines)} hotkeys from {hotkey_file}")
            
        except Exception as e:
            print(f"Error loading hotkeys: {e}")
    
    def save_all_hotkeys(self):
        """Speichert alle Hotkeys in Datei"""
        hotkey_file = "hotkeys.seq"
        
        try:
            with open(hotkey_file, 'wb') as f:
                for i in range(1, 11):  # F1 bis F10
                    if i in self.hotkeys:
                        # Schreibe Hotkey
                        f.write(self.hotkeys[i])
                    # Else: Leere Zeile
                    
                    # Füge CR hinzu (außer nach letztem)
                    if i < 10:
                        f.write(b'\x0D')
            
            messagebox.showinfo("Success", 
                              f"Hotkeys saved to {hotkey_file}\n\n" +
                              "Reload terminal to apply changes.",
                              parent=self)
            
            # Lade Hotkeys im Parent Terminal neu
            if hasattr(self.parent, 'load_hotkeys'):
                self.parent.load_hotkeys()
                
        except Exception as e:
            messagebox.showerror("Error", 
                               f"Failed to save hotkeys:\n{e}",
                               parent=self)
    
    def reload_hotkeys(self):
        """Lädt Hotkeys neu aus Datei"""
        self.hotkeys.clear()
        self.load_hotkeys()
        
        # Aktualisiere alle Previews
        for i in range(1, 11):
            self.update_hotkey_preview(i)
        
        messagebox.showinfo("Reloaded", 
                          "Hotkeys reloaded from hotkeys.seq",
                          parent=self)


class HotkeyPreviewDialog(tk.Toplevel):
    """Preview Dialog - Zeigt gerenderten Hotkey wie im Terminal"""
    
    def __init__(self, parent, fkey_num, hotkey_bytes):
        super().__init__(parent)
        self.title(f"Hotkey Preview: Ctrl+Alt+F{fkey_num}")
        self.geometry("800x400")
        self.parent = parent
        
        # Header
        header = ttk.Label(self, text=f"👁️ Preview: Ctrl+Alt+F{fkey_num}", 
                          font=('Arial', 14, 'bold'))
        header.pack(pady=10)
        
        info = ttk.Label(self, text="Rendered PETSCII output as it appears in terminal", 
                        font=('Arial', 9, 'italic'))
        info.pack(pady=5)
        
        # Canvas Frame (schwarzer Hintergrund wie im Terminal)
        canvas_frame = tk.Frame(self, bg='black', relief=tk.SUNKEN, borderwidth=3)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        
        # Canvas für PETSCII Rendering
        self.canvas = tk.Canvas(canvas_frame, 
                               bg='black',
                               highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Info Label
        hex_dump = ' '.join(f'{b:02X}' for b in hotkey_bytes)
        info_label = ttk.Label(self, 
                              text=f"Bytes ({len(hotkey_bytes)}): {hex_dump[:80]}...", 
                              font=('Courier', 8))
        info_label.pack(pady=5)
        
        # Close Button
        ttk.Button(self, text="✖ Close", 
                  command=self.destroy, 
                  width=15).pack(pady=10)
        
        self.transient(parent)
        self.grab_set()
        
        # Zentriere Dialog
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")
        
        # Rendere PETSCII
        self.after(100, lambda: self.render_petscii(hotkey_bytes))
    
    def render_petscii(self, data):
        """Rendert PETSCII bytes mit echtem C64 Font Renderer wie im Terminal"""
        try:
            # Importiere PETSCII Module
            from petscii_parser import PETSCIIScreenBuffer, PETSCIIParser
            from c64_rom_renderer import AnimatedC64ROMFontRenderer
            from PIL import ImageTk
            
            # Erstelle Buffer und Parser
            screen = PETSCIIScreenBuffer(40, 25)  # 40x25 wie C64
            parser = PETSCIIParser(screen)
            
            # Parse PETSCII bytes
            parser.parse_bytes(data)
            
            # Erstelle Renderer GENAU wie im Terminal
            renderer = AnimatedC64ROMFontRenderer(
                screen,  # Screen buffer als erstes Argument!
                font_upper_path="upper.bmp",
                font_lower_path="lower.bmp",
                zoom=2
            )
            
            # Rendere zu PIL Image
            img = renderer.render()
            
            # Konvertiere zu PhotoImage
            photo = ImageTk.PhotoImage(img)
            
            # Zeige auf Canvas
            self.canvas.create_image(0, 0, image=photo, anchor=tk.NW)
            
            # WICHTIG: Referenz speichern damit GC nicht löscht!
            self.canvas.image = photo
            
        except Exception as e:
            # Fallback bei Fehler
            import traceback
            error_text = f"Preview error:\n{str(e)}\n\n{traceback.format_exc()[:300]}"
            self.canvas.create_text(10, 10, 
                                   text=error_text, 
                                   fill='red',
                                   anchor=tk.NW,
                                   font=('Courier', 8))
    
    def bytes_to_display_text(self, data):
        """Konvertiert PETSCII bytes zu Unicode-Zeichen für echte Darstellung"""
        # PETSCII zu Unicode Mapping
        petscii_map = {
            # Graphics Characters (0xC1-0xDA) - Box Drawing
            0xC1: '├', 0xC2: '┤', 0xC3: '┬', 0xC4: '┴', 0xC5: '┼',
            0xC6: '╮', 0xC7: '╰', 0xC8: '╯', 0xC9: '╱', 0xCA: '╲',
            0xCB: '○', 0xCC: '●', 0xCD: '◆', 0xCE: '┃', 0xCF: '╭',
            0xD0: '─', 0xD1: '╳', 0xD2: '♠', 0xD3: '♣', 0xD4: '♥',
            0xD5: '♦', 0xD6: '▌', 0xD7: '▐', 0xD8: '▀', 0xD9: '▄',
            0xDA: '█',
            # Additional Graphics (0xA0-0xBF)
            0xA0: '▁', 0xA1: '▂', 0xA2: '▃', 0xA3: '▄', 0xA4: '▅',
            0xA5: '▆', 0xA6: '▇', 0xA7: '█', 0xA8: '▏', 0xA9: '▎',
            0xAA: '▍', 0xAB: '▌', 0xAC: '▋', 0xAD: '▊', 0xAE: '▉',
            0xAF: '█', 0xB0: '░', 0xB1: '▒', 0xB2: '▓', 0xB3: '│',
            0xB4: '┤', 0xB5: '╣', 0xB6: '║', 0xB7: '╗', 0xB8: '╝',
            0xB9: '┐', 0xBA: '└', 0xBB: '┴', 0xBC: '┬', 0xBD: '├',
            0xBE: '─', 0xBF: '┼',
            # Control codes als Tags
            0x05: '[WHT]', 0x1C: '[RED]', 0x1E: '[GRN]', 0x1F: '[BLU]',
            0x90: '[BLK]', 0x9E: '[YEL]', 0x81: '[ORG]', 0x9F: '[CYN]',
            0x9C: '[PUR]', 0x95: '[BRN]', 0x96: '[LRD]', 0x97: '[DGR]',
            0x98: '[GRY]', 0x99: '[LGN]', 0x9A: '[LBL]', 0x9B: '[LGY]',
            0x0D: '\n', 0x93: '[CLR]', 0x13: '[HOME]', 0x14: '[DEL]',
            0x12: '[RVS]', 0x92: '[RVS]', 0x0E: '[LOW]', 0x8E: '[UP]'
        }
        
        result = []
        for byte in data:
            if byte in petscii_map:
                result.append(petscii_map[byte])
            elif 0x20 <= byte <= 0x7E:  # Printable ASCII
                result.append(chr(byte))
            else:
                result.append(f'[{byte:02X}]')
        
        return ''.join(result)


class HotkeyEditDialog(tk.Toplevel):
    """Edit-Dialog für einzelnen Hotkey mit PETSCII Support"""
    
    def __init__(self, parent, fkey_num, current_bytes):
        super().__init__(parent)
        self.title(f"Edit Hotkey: Ctrl+Alt+F{fkey_num}")
        self.geometry("1800x900")  # Breiter für Paletten-Sidebar!
        self.parent = parent
        self.fkey_num = fkey_num
        self.result = None
        
        # Byte Buffer für Hotkey
        self.hotkey_buffer = bytearray(current_bytes)
        
        # Byte-Position Cursor (wo im Buffer eingefügt wird)
        self.byte_cursor_pos = len(self.hotkey_buffer)  # Start am Ende
        
        # Cursor Position im Preview
        self.cursor_x = 0
        self.cursor_y = 0
        
        # Header
        header = ttk.Label(self, text=f"✏️ Edit Ctrl+Alt+F{fkey_num}", 
                          font=('Arial', 14, 'bold'))
        header.pack(pady=10)
        
        info = ttk.Label(self, text="Click colors or graphics to insert | Use C64 keyboard mapping | Cursor shows position in preview", 
                        font=('Arial', 9, 'italic'))
        info.pack(pady=5)
        
        # Main Container: Paletten-Sidebar links, Editor+Preview rechts
        container = ttk.Frame(self)
        container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # LEFT SIDEBAR: Farbpalette + Grafikpalette
        sidebar = ttk.Frame(container)
        sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        
        # Farbpalette
        self.color_palette = self.create_color_palette(sidebar)
        self.color_palette.pack(pady=(0, 10))
        
        # Grafikpalette (in try-Block damit Fehler den Dialog nicht zerstören)
        try:
            self.graphics_palette = self.create_graphics_palette(sidebar)
            self.graphics_palette.pack(fill=tk.BOTH, expand=True)
        except Exception as e:
            print(f"Error creating graphics palette: {e}")
            import traceback
            traceback.print_exc()
            # Fallback: Zeige Fehlermeldung
            error_label = ttk.Label(sidebar, text=f"Graphics palette error:\n{e}", 
                                   foreground='red', wraplength=200)
            error_label.pack(pady=10)
        
        # RIGHT: Text Editor + Live Preview (wie vorher)
        main_frame = ttk.Frame(container)
        main_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Left: Text Editor
        editor_frame = ttk.LabelFrame(main_frame, text="Editor (Type here)", padding=5)
        editor_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        
        # Text Widget mit C64 Font
        try:
            c64_font = ('C64 Pro Mono', 10)
        except:
            c64_font = ('Courier', 10)
        
        self.text_widget = tk.Text(editor_frame, 
                                   font=c64_font,
                                   bg='black',
                                   fg='white',
                                   insertbackground='white',
                                   wrap=tk.CHAR,
                                   height=8)
        self.text_widget.pack(fill=tk.BOTH, expand=True)
        
        # Right: Live Preview mit Cursor
        preview_frame_outer = ttk.LabelFrame(main_frame, text="Live Preview (PETSCII Rendered + Cursor)", padding=5)
        preview_frame_outer.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))
        
        # Canvas für Live PETSCII Rendering (größer!)
        self.preview_canvas = tk.Canvas(preview_frame_outer, 
                                       bg='black',
                                       highlightthickness=0,
                                       width=640,   # 40 chars * 8px * 2 zoom
                                       height=400)  # 25 lines * 8px * 2 zoom
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        
        # Füge aktuellen Text ein
        self.update_text_display()
        
        # Start cursor animation
        self.animate_cursor()
        
        # Bind Keyboard mit C64 Mapping
        self.text_widget.bind("<Key>", self.on_key_press)
        self.text_widget.focus_set()
        
        # Info Label
        info_text = ("💡 Complete C64 Charset Support:\n" +
                    "Ctrl+1-8: Basic Colors | Ctrl+9: RVS ON | Ctrl+0: RVS OFF\n" +
                    "Cbm+1-8 (Alt+1-8): Extended Colors | Cbm+9: Lower | Cbm+0: Upper\n" +
                    "Ctrl+A-Z: Control Graphics (0x01-0x1A)\n" +
                    "Cbm+A-Z (Alt+A-Z): Graphics Characters (Box drawing, symbols)\n" +
                    "Shift+Letters: Uppercase (Graphics in Graphics Mode)\n" +
                    "Enter: RETURN | Backspace: Delete | All PETSCII codes supported!")
        
        info_label = ttk.Label(self, text=info_text, 
                              font=('Arial', 8), justify=tk.LEFT)
        info_label.pack(pady=5)
        
        # Buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, padx=20, pady=10)
        
        ttk.Button(btn_frame, text="✅ Save", 
                  command=self.save_hotkey, 
                  width=15).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(btn_frame, text="🗑️ Clear All", 
                  command=self.clear_all, 
                  width=15).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(btn_frame, text="✖ Cancel", 
                  command=self.cancel, 
                  width=15).pack(side=tk.RIGHT, padx=5)
        
        self.transient(parent)
        self.grab_set()
        
        # Zentriere Dialog
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")
    
    def create_color_palette(self, parent):
        """Erstellt clickbare Farbpalette"""
        import os
        
        frame = ttk.LabelFrame(parent, text="🎨 C64 Colors", padding=5)
        
        # Color codes mapping (image has 2 rows x 8 cols)
        color_codes = [
            # Row 1: Basic colors (Ctrl+1-8)
            [0x90, 0x05, 0x1C, 0x9F, 0x9C, 0x1E, 0x1F, 0x9E],
            # Row 2: Extended colors (Cbm+1-8)
            [0x81, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0x9B]
        ]
        
        try:
            from PIL import Image, ImageTk
            
            # Suche c64_colors.png im Skript-Verzeichnis
            script_dir = os.path.dirname(os.path.abspath(__file__))
            img_path = os.path.join(script_dir, "c64_colors.png")
            if not os.path.exists(img_path):
                img_path = "c64_colors.png"
            
            # Load color palette image
            img = Image.open(img_path)
            photo = ImageTk.PhotoImage(img)
            
            # Create canvas
            canvas = tk.Canvas(frame, width=img.width, height=img.height, 
                              highlightthickness=0, cursor="hand2")
            canvas.pack()
            
            # Display image
            canvas.create_image(0, 0, image=photo, anchor=tk.NW)
            canvas.image = photo  # Keep reference!
            
            # Calculate click regions
            color_width = img.width / 8
            color_height = img.height / 2
            
            def on_click(event):
                col = int(event.x / color_width)
                row = int(event.y / color_height)
                
                if 0 <= row < 2 and 0 <= col < 8:
                    code = color_codes[row][col]
                    self.insert_byte(code)
            
            canvas.bind("<Button-1>", on_click)
            
        except Exception as e:
            print(f"Could not load color palette: {e}")
            tk.Label(frame, text="Color palette not available", 
                    fg='red').pack()
        
        return frame
    
    def load_petscii_map(self):
        """Lädt das PETSCII Mapping aus petscii.map"""
        import os
        self.petscii_map = {}
        
        # Suche petscii.map im Skript-Verzeichnis
        script_dir = os.path.dirname(os.path.abspath(__file__))
        map_file = os.path.join(script_dir, "petscii.map")
        
        # Fallback: aktuelles Verzeichnis
        if not os.path.exists(map_file):
            map_file = "petscii.map"
        
        try:
            with open(map_file, 'r', encoding='latin-1') as f:
                for line in f:
                    line = line.strip()
                    # Kommentare und Leerzeilen überspringen
                    if not line or line.startswith('#'):
                        continue
                    
                    # Format: XX = YY oder XX = RVS:YY
                    if '=' in line:
                        parts = line.split('=')
                        if len(parts) >= 2:
                            screen_hex = parts[0].strip()
                            value = parts[1].split('#')[0].strip()  # Kommentar entfernen
                            
                            try:
                                screen_code = int(screen_hex, 16)
                                
                                if value.startswith('RVS:'):
                                    # Reversed: RVS ON + PETSCII + RVS OFF
                                    petscii_hex = value[4:].strip()
                                    petscii_code = int(petscii_hex, 16)
                                    self.petscii_map[screen_code] = ('rvs', petscii_code)
                                else:
                                    # Normal: nur PETSCII Code
                                    petscii_code = int(value, 16)
                                    self.petscii_map[screen_code] = petscii_code
                            except ValueError:
                                continue
                                
            debug_print(f"Loaded {len(self.petscii_map)} mappings from {map_file}")
            
        except FileNotFoundError:
            print(f"Warning: {map_file} not found, using default mapping")
            # Fallback: Standard-Mapping
            for sc in range(128):
                if sc < 32:
                    self.petscii_map[sc] = sc + 0x40
                elif sc < 64:
                    self.petscii_map[sc] = sc
                elif sc < 96:
                    self.petscii_map[sc] = sc + 0x80
                else:
                    self.petscii_map[sc] = sc + 0x40
            # Reversed
            for sc in range(128, 256):
                base = sc & 0x7F
                if base in self.petscii_map:
                    p = self.petscii_map[base]
                    if isinstance(p, int):
                        self.petscii_map[sc] = ('rvs', p)
    
    def create_graphics_palette(self, parent):
        """Erstellt clickbare Grafikzeichen-Palette mit Upper/Lower Font Tabs"""
        import os
        
        frame = ttk.LabelFrame(parent, text="📐 C64 Characters (Click to insert)", padding=5)
        
        try:
            from PIL import Image, ImageTk, ImageDraw
            
            # Lade Mapping aus petscii.map
            self.load_petscii_map()
            
            # Suche Font-Dateien im Skript-Verzeichnis
            script_dir = os.path.dirname(os.path.abspath(__file__))
            
            upper_path = os.path.join(script_dir, "upper.bmp")
            if not os.path.exists(upper_path):
                upper_path = "upper.bmp"
            
            lower_path = os.path.join(script_dir, "lower.bmp")
            if not os.path.exists(lower_path):
                lower_path = "lower.bmp"
            
            # Notebook für Tabs
            notebook = ttk.Notebook(frame)
            notebook.pack(fill=tk.BOTH, expand=True)
            
            # Speichere Referenzen für beide Tabs
            self.graphics_char_map = {}  # Wird je nach Tab aktualisiert
            self.font_images = {}  # Speichert PhotoImage Referenzen
            
            # Erstelle beide Tabs
            fonts = [
                ("Upper/Graphics", upper_path, "upper"),
                ("Lower/Text", lower_path, "lower")
            ]
            
            for tab_name, font_path, font_key in fonts:
                tab_frame = ttk.Frame(notebook)
                notebook.add(tab_frame, text=tab_name)
                
                # Erstelle Font-Grid für diesen Tab
                self._create_font_grid(tab_frame, font_path, font_key)
            
            # Info Labels
            info1 = ttk.Label(frame, text="Row 0-7: Normal | Row 8-15: Reversed", 
                           font=('Arial', 8))
            info1.pack(pady=1)
            
            info2 = ttk.Label(frame, text="Upper=Graphics mode | Lower=Text mode", 
                           font=('Arial', 8))
            info2.pack(pady=1)
            
            # Char Info Label (wird bei Mouse-Over aktualisiert)
            # Feste Breite damit kein Window-Resize passiert
            self.char_info_label = ttk.Label(frame, text="Char: -", 
                           font=('Courier', 9, 'bold'), foreground='#00AA00',
                           width=45, anchor='w')
            self.char_info_label.pack(pady=2)
            
        except Exception as e:
            print(f"Could not create graphics palette: {e}")
            import traceback
            traceback.print_exc()
            tk.Label(frame, text=f"Graphics palette error:\n{e}", 
                    fg='red', wraplength=250).pack()
        
        return frame
    
    def _create_font_grid(self, parent, font_path, font_key):
        """Erstellt ein Font-Grid für einen Tab"""
        from PIL import Image, ImageTk, ImageDraw
        
        try:
            # Lade Font-Bitmap
            font_img = Image.open(font_path).convert('L')
            
            # Font ist 8x8 pro Zeichen, 32 Zeichen pro Reihe, 8 Reihen = 256 Zeichen
            src_char_width = 8
            src_char_height = 8
            src_chars_per_row = 32
            
            # Grid: 16x16 für alle 256 Screen Codes
            grid_cols = 16
            grid_rows = 16
            
            zoom = 2
            cell_size = src_char_width * zoom  # 16 pixels per cell
            grid_width = grid_cols * cell_size
            grid_height = grid_rows * cell_size
            
            # Erstelle Grid-Image
            grid_img = Image.new('RGB', (grid_width, grid_height), color='#202020')
            draw = ImageDraw.Draw(grid_img)
            
            # Char Map für diesen Font
            char_map = {}
            
            # Fülle Grid mit Screen Codes 0x00-0xFF
            for screen_code in range(256):
                grid_x = screen_code % grid_cols
                grid_y = screen_code // grid_cols
                
                # PETSCII Code aus petscii.map
                if screen_code in self.petscii_map:
                    char_map[(grid_x, grid_y)] = self.petscii_map[screen_code]
                else:
                    char_map[(grid_x, grid_y)] = screen_code
                
                # Position im Font-Bitmap
                font_col = screen_code % src_chars_per_row
                font_row = screen_code // src_chars_per_row
                
                # Extrahiere Zeichen
                left = font_col * src_char_width
                top = font_row * src_char_height
                char_img = font_img.crop((left, top, left + src_char_width, top + src_char_height))
                
                # Skaliere
                char_img = char_img.resize((src_char_width * zoom, src_char_height * zoom), Image.Resampling.NEAREST)
                
                # Konvertiere zu RGB (weiß auf schwarz)
                char_rgb = Image.new('RGB', char_img.size, color='black')
                for py in range(char_img.height):
                    for px in range(char_img.width):
                        pixel = char_img.getpixel((px, py))
                        if pixel > 128:
                            char_rgb.putpixel((px, py), (255, 255, 255))
                        else:
                            char_rgb.putpixel((px, py), (0, 0, 0))
                
                # Füge in Grid ein
                dest_x = grid_x * cell_size
                dest_y = grid_y * cell_size
                grid_img.paste(char_rgb, (dest_x, dest_y))
            
            # Zeichne Gitternetz
            grid_color = '#404040'
            for col in range(grid_cols + 1):
                x = col * cell_size
                draw.line([(x, 0), (x, grid_height - 1)], fill=grid_color, width=1)
            for row in range(grid_rows + 1):
                y = row * cell_size
                draw.line([(0, y), (grid_width - 1, y)], fill=grid_color, width=1)
            
            # Trennlinie zwischen normal/reversed
            separator_y = 8 * cell_size
            draw.line([(0, separator_y), (grid_width - 1, separator_y)], fill='#808080', width=2)
            
            # PhotoImage
            photo = ImageTk.PhotoImage(grid_img)
            self.font_images[font_key] = photo  # Keep reference!
            
            # Scrollbarer Canvas
            canvas_frame = tk.Frame(parent)
            canvas_frame.pack(fill=tk.BOTH, expand=True)
            
            scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            
            canvas = tk.Canvas(canvas_frame, 
                              width=grid_width, 
                              height=min(200, grid_height),
                              yscrollcommand=scrollbar.set,
                              highlightthickness=1,
                              highlightbackground='gray',
                              cursor="crosshair",
                              bg='#202020')
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            
            scrollbar.config(command=canvas.yview)
            
            canvas.create_image(0, 0, image=photo, anchor=tk.NW)
            canvas.config(scrollregion=(0, 0, grid_width, grid_height))
            
            # Click Handler
            def on_click(event):
                x = canvas.canvasx(event.x)
                y = canvas.canvasy(event.y)
                
                grid_x = int(x / cell_size)
                grid_y = int(y / cell_size)
                
                if grid_x < 0 or grid_x >= grid_cols or grid_y < 0 or grid_y >= grid_rows:
                    return
                
                if (grid_x, grid_y) in char_map:
                    entry = char_map[(grid_x, grid_y)]
                    screen_code = grid_y * grid_cols + grid_x
                    
                    if isinstance(entry, tuple) and entry[0] == 'rvs':
                        petscii = entry[1]
                        self.insert_byte(0x12)  # RVS ON
                        self.insert_byte(petscii)
                        self.insert_byte(0x92)  # RVS OFF
                        debug_print(f"{font_key}: Screen 0x{screen_code:02X} → [RVS] PETSCII 0x{petscii:02X}")
                    else:
                        petscii = entry
                        self.insert_byte(petscii)
                        debug_print(f"{font_key}: Screen 0x{screen_code:02X} → PETSCII 0x{petscii:02X}")
            
            canvas.bind("<Button-1>", on_click)
            
            # Mouse wheel scrolling
            def on_mousewheel(event):
                canvas.yview_scroll(int(-1*(event.delta/120)), "units")
            canvas.bind("<MouseWheel>", on_mousewheel)
            
            # Mouse motion für Char Info
            def on_motion(event):
                x = canvas.canvasx(event.x)
                y = canvas.canvasy(event.y)
                
                grid_x = int(x / cell_size)
                grid_y = int(y / cell_size)
                
                if grid_x < 0 or grid_x >= grid_cols or grid_y < 0 or grid_y >= grid_rows:
                    if hasattr(self, 'char_info_label'):
                        self.char_info_label.config(text="Char: -")
                    return
                
                screen_code = grid_y * grid_cols + grid_x
                
                # Hole PETSCII Code
                petscii_info = ""
                if (grid_x, grid_y) in char_map:
                    entry = char_map[(grid_x, grid_y)]
                    if isinstance(entry, tuple) and entry[0] == 'rvs':
                        petscii = entry[1]
                        petscii_info = f" → PETSCII ${petscii:02X} (RVS)"
                    else:
                        petscii_info = f" → PETSCII ${entry:02X}"
                
                # Update Label
                if hasattr(self, 'char_info_label'):
                    self.char_info_label.config(
                        text=f"Screen: ${screen_code:02X} / Dec {screen_code}{petscii_info}"
                    )
            
            canvas.bind("<Motion>", on_motion)
            
            # Mouse leave - reset label
            def on_leave(event):
                if hasattr(self, 'char_info_label'):
                    self.char_info_label.config(text="Char: -")
            canvas.bind("<Leave>", on_leave)
            
        except Exception as e:
            print(f"Could not create font grid for {font_path}: {e}")
            import traceback
            traceback.print_exc()
            tk.Label(parent, text=f"Font error: {e}", fg='red').pack()
    
    def insert_byte(self, byte_val):
        """Fügt PETSCII byte an Cursor-Position zum Buffer hinzu"""
        # Füge an byte_cursor_pos ein
        if self.byte_cursor_pos > len(self.hotkey_buffer):
            self.byte_cursor_pos = len(self.hotkey_buffer)
        
        self.hotkey_buffer.insert(self.byte_cursor_pos, byte_val)
        self.byte_cursor_pos += 1  # Cursor vorwärts bewegen
        
        self.update_text_display()
        self.text_widget.focus_set()
    
    def animate_cursor(self):
        """Animiert blinkenden Cursor im Preview"""
        try:
            # Toggle cursor visibility
            if not hasattr(self, 'cursor_visible'):
                self.cursor_visible = True
            
            self.cursor_visible = not self.cursor_visible
            
            # Update cursor in preview
            items = self.preview_canvas.find_withtag('cursor')
            if items:
                state = tk.NORMAL if self.cursor_visible else tk.HIDDEN
                self.preview_canvas.itemconfig('cursor', state=state)
            
            # Schedule next blink (500ms)
            self.after(500, self.animate_cursor)
            
        except:
            pass  # Window was destroyed
    
    def on_key_press(self, event):
        """Handle Keyboard mit C64 Mapping + Arrow Keys"""
        
        # Modifier-Bits erkennen
        state = event.state
        shift = bool(state & 0x0001)
        ctrl = bool(state & 0x0004)
        # Alt: Bit 0x20000 oder 0x40000 (NICHT 0x0008, das ist Num Lock!)
        alt = bool(state & 0x20000) or bool(state & 0x40000)
        
        # Arrow Keys für Navigation
        if event.keysym == "Left":
            if self.byte_cursor_pos > 0:
                self.byte_cursor_pos -= 1
                self.update_text_display()
            return "break"
        
        if event.keysym == "Right":
            if self.byte_cursor_pos < len(self.hotkey_buffer):
                self.byte_cursor_pos += 1
                self.update_text_display()
            return "break"
        
        if event.keysym == "Home":
            self.byte_cursor_pos = 0
            self.update_text_display()
            return "break"
        
        if event.keysym == "End":
            self.byte_cursor_pos = len(self.hotkey_buffer)
            self.update_text_display()
            return "break"
        
        # Backspace - lösche byte VOR cursor
        if event.keysym == "BackSpace":
            if self.byte_cursor_pos > 0:
                self.hotkey_buffer.pop(self.byte_cursor_pos - 1)
                self.byte_cursor_pos -= 1
                self.update_text_display()
            return "break"
        
        # Delete - lösche byte AN cursor
        if event.keysym == "Delete":
            if self.byte_cursor_pos < len(self.hotkey_buffer):
                self.hotkey_buffer.pop(self.byte_cursor_pos)
                self.update_text_display()
            return "break"
        
        # Importiere C64 Keyboard Funktion
        from c64_keyboard import get_petscii_for_key
        
        # Hole PETSCII byte
        # WICHTIG: Reihenfolge ist (char, keysym, shift, ctrl, alt)
        petscii_byte = get_petscii_for_key(
            event.char,                   # char (z.B. '1', 'a')
            event.keysym,                 # keysym (z.B. 'Return', 'F1')
            shift,                        # Shift
            ctrl,                         # Control
            alt                           # Alt (Commodore)
        )
        
        if petscii_byte is not None:
            # Füge PETSCII byte an Cursor-Position ein
            self.insert_byte(petscii_byte)
            # Verhindere normale Text-Eingabe
            return "break"
        
        # Erlaube normale Eingabe für printable chars
        if len(event.char) == 1 and event.char.isprintable():
            byte_val = ord(event.char)
            self.insert_byte(byte_val)
            return "break"
        
        return None
    
    def update_text_display(self):
        """Aktualisiert Text-Anzeige und Live Preview"""
        # Update Text Editor
        self.text_widget.delete('1.0', tk.END)
        
        # Konvertiere Buffer zu Display-String mit Cursor-Marker
        display_parts = []
        for i, byte_val in enumerate(self.hotkey_buffer):
            # Cursor-Marker vor diesem Byte?
            if i == self.byte_cursor_pos:
                display_parts.append('|')  # Cursor-Marker
            
            # Byte anzeigen
            if 0x20 <= byte_val <= 0x7E:
                display_parts.append(chr(byte_val))
            elif byte_val == 0x90:
                display_parts.append("[BLK]")
            elif byte_val == 0x05:
                display_parts.append("[WHT]")
            elif byte_val == 0x1C:
                display_parts.append("[RED]")
            elif byte_val == 0x9F:
                display_parts.append("[CYN]")
            elif byte_val == 0x9C:
                display_parts.append("[PUR]")
            elif byte_val == 0x1E:
                display_parts.append("[GRN]")
            elif byte_val == 0x1F:
                display_parts.append("[BLU]")
            elif byte_val == 0x9E:
                display_parts.append("[YEL]")
            elif byte_val == 0x81:
                display_parts.append("[ORG]")
            elif byte_val == 0x95:
                display_parts.append("[BRN]")
            elif byte_val == 0x96:
                display_parts.append("[LRD]")
            elif byte_val == 0x97:
                display_parts.append("[DGR]")
            elif byte_val == 0x98:
                display_parts.append("[GRY]")
            elif byte_val == 0x99:
                display_parts.append("[LGN]")
            elif byte_val == 0x9A:
                display_parts.append("[LBL]")
            elif byte_val == 0x9B:
                display_parts.append("[LGY]")
            elif 0xC1 <= byte_val <= 0xDA:
                display_parts.append(f"[G{byte_val:02X}]")
            else:
                display_parts.append(f"[{byte_val:02X}]")
        
        # Cursor am Ende?
        if self.byte_cursor_pos >= len(self.hotkey_buffer):
            display_parts.append('|')
        
        display_text = ''.join(display_parts)
        self.text_widget.insert('1.0', display_text)
        
        # Zeige auch Hex-Dump
        hex_dump = ' '.join(f'{b:02X}' for b in self.hotkey_buffer)
        self.text_widget.insert(tk.END, f"\n\n[Hex: {hex_dump}]")
        self.text_widget.insert(tk.END, f"\n[Cursor at byte {self.byte_cursor_pos}/{len(self.hotkey_buffer)}]")
        
        # Update Live Preview Canvas
        self.update_live_preview()
    
    def update_live_preview(self):
        """Aktualisiert Live PETSCII Preview mit echtem C64 Renderer + Cursor"""
        try:
            # Clear Canvas
            self.preview_canvas.delete('all')
            
            if len(self.hotkey_buffer) == 0:
                # Zeige Placeholder
                self.preview_canvas.create_text(10, 10, 
                                               text="(empty - start typing or click palettes)", 
                                               fill='gray',
                                               anchor=tk.NW,
                                               font=('Arial', 10, 'italic'))
                
                # Draw cursor at 0,0 in white
                self.cursor_x = 0
                self.cursor_y = 0
                self.current_fg_color = 1  # White
                self.draw_cursor()
                return
            
            # Importiere Module - verwende normalen Parser!
            from petscii_parser import PETSCIIScreenBuffer, PETSCIIParser
            from c64_rom_renderer import AnimatedC64ROMFontRenderer
            from PIL import ImageTk
            
            # Erstelle Screen Buffer und Parser
            screen = PETSCIIScreenBuffer(40, 25)
            parser = PETSCIIParser(screen)
            
            # Parse ALLE PETSCII bytes für vollständiges Bild
            parser.parse_bytes(bytes(self.hotkey_buffer))
            
            # Erstelle Renderer
            renderer = AnimatedC64ROMFontRenderer(
                screen,
                font_upper_path="upper.bmp",
                font_lower_path="lower.bmp",
                zoom=2
            )
            
            # Rendere zu PIL Image
            img = renderer.render()
            
            # Konvertiere zu PhotoImage
            photo = ImageTk.PhotoImage(img)
            
            # Zeige auf Canvas
            self.preview_canvas.create_image(0, 0, image=photo, anchor=tk.NW)
            
            # WICHTIG: Referenz speichern!
            self.preview_canvas.image = photo
            
            # Jetzt: Parse NUR BIS byte_cursor_pos um Cursor-Position und Farbe zu bekommen
            cursor_screen = PETSCIIScreenBuffer(40, 25)
            cursor_parser = PETSCIIParser(cursor_screen)
            
            # Parse nur bis zur Cursor-Position
            if self.byte_cursor_pos > 0:
                cursor_parser.parse_bytes(bytes(self.hotkey_buffer[:self.byte_cursor_pos]))
            
            # Hole Cursor-Position und Farbe an dieser Stelle
            self.cursor_x = cursor_screen.cursor_x
            self.cursor_y = cursor_screen.cursor_y
            self.current_fg_color = cursor_screen.current_fg
            
            # Draw cursor
            self.draw_cursor()
            
        except Exception as e:
            # Fallback bei Fehler
            import traceback
            self.preview_canvas.create_text(10, 10, 
                                           text=f"Preview error:\n{str(e)[:100]}", 
                                           fill='red',
                                           anchor=tk.NW,
                                           font=('Courier', 9))
    
    def draw_cursor(self):
        """Zeichnet ausgefüllten Cursor im Preview Canvas in aktueller Farbe"""
        # Character size in pixels (8x8 at zoom=2)
        char_width = 8 * 2
        char_height = 8 * 2
        
        # Calculate pixel position
        x = self.cursor_x * char_width
        y = self.cursor_y * char_height
        
        # C64 colors to RGB mapping
        color_map = {
            0: '#000000',  # BLACK
            1: '#FFFFFF',  # WHITE
            2: '#880000',  # RED
            3: '#AAFFEE',  # CYAN
            4: '#CC44CC',  # PURPLE
            5: '#00CC55',  # GREEN
            6: '#0000AA',  # BLUE
            7: '#EEEE77',  # YELLOW
            8: '#DD8855',  # ORANGE
            9: '#664400',  # BROWN
            10: '#FF7777', # LIGHT_RED
            11: '#333333', # DARK_GRAY
            12: '#777777', # GRAY
            13: '#AAFF66', # LIGHT_GREEN
            14: '#0088FF', # LIGHT_BLUE
            15: '#BBBBBB', # LIGHT_GRAY
        }
        
        # Get current color (use self.current_fg_color, default white)
        fg_color = getattr(self, 'current_fg_color', 1)
        cursor_rgb = color_map.get(fg_color, '#FFFFFF')
        
        # Draw FILLED cursor rectangle
        self.preview_canvas.create_rectangle(
            x, y, x + char_width, y + char_height,
            fill=cursor_rgb,
            outline='',  # No outline
            tags='cursor'
        )
    
    def bytes_to_display_simple(self, data):
        """Konvertiert bytes zu Unicode-Zeichen für Live Preview"""
        # PETSCII zu Unicode Mapping
        petscii_map = {
            # Graphics Characters (0xC1-0xDA)
            0xC1: '├', 0xC2: '┤', 0xC3: '┬', 0xC4: '┴', 0xC5: '┼',
            0xC6: '╮', 0xC7: '╰', 0xC8: '╯', 0xC9: '╱', 0xCA: '╲',
            0xCB: '○', 0xCC: '●', 0xCD: '◆', 0xCE: '┃', 0xCF: '╭',
            0xD0: '─', 0xD1: '╳', 0xD2: '♠', 0xD3: '♣', 0xD4: '♥',
            0xD5: '♦', 0xD6: '▌', 0xD7: '▐', 0xD8: '▀', 0xD9: '▄',
            0xDA: '█',
            # Additional Graphics (0xA0-0xBF)
            0xA0: '▁', 0xA1: '▂', 0xA2: '▃', 0xA3: '▄', 0xA4: '▅',
            0xA5: '▆', 0xA6: '▇', 0xA7: '█', 0xA8: '▏', 0xA9: '▎',
            0xAA: '▍', 0xAB: '▌', 0xAC: '▋', 0xAD: '▊', 0xAE: '▉',
            0xAF: '█', 0xB0: '░', 0xB1: '▒', 0xB2: '▓', 0xB3: '│',
            0xB4: '┤', 0xB5: '╣', 0xB6: '║', 0xB7: '╗', 0xB8: '╝',
            0xB9: '┐', 0xBA: '└', 0xBB: '┴', 0xBC: '┬', 0xBD: '├',
            0xBE: '─', 0xBF: '┼',
            # Colors & Control
            0x05: '[WHT]', 0x1C: '[RED]', 0x1E: '[GRN]', 0x1F: '[BLU]',
            0x90: '[BLK]', 0x9E: '[YEL]', 0x81: '[ORG]', 0x9F: '[CYN]',
            0x0D: '\n'
        }
        
        result = []
        for byte in data:
            if byte in petscii_map:
                result.append(petscii_map[byte])
            elif 0x20 <= byte <= 0x7E:
                result.append(chr(byte))
            else:
                result.append(f'[{byte:02X}]')
        
        return ''.join(result)
    
    def bytes_to_display(self, data):
        """Konvertiert bytes zu Display-String mit Graphics Support"""
        result = []
        
        for byte in data:
            # Printable ASCII
            if 0x20 <= byte <= 0x7E:
                result.append(chr(byte))
            # Graphics Characters (Cbm+A-Z) - 0xC1-0xDA
            elif 0xC1 <= byte <= 0xDA:
                result.append(f"[G{byte:02X}]")
            # Control Graphics (Ctrl+A-Z) - 0x01-0x1A  
            elif 0x01 <= byte <= 0x1A and byte != 0x0D:
                result.append(f"[C{byte:02X}]")
            # Additional Graphics - 0xA0-0xBF
            elif 0xA0 <= byte <= 0xBF:
                result.append(f"[G{byte:02X}]")
            # RETURN
            elif byte == 0x0D:
                result.append("↵\n")
            # All other special codes
            else:
                result.append(f"[{byte:02X}]")
        
        return ''.join(result)
    
    def clear_all(self):
        """Löscht kompletten Buffer"""
        self.hotkey_buffer.clear()
        self.update_text_display()
    
    def save_hotkey(self):
        """Speichert Hotkey und schließt Dialog"""
        self.result = bytes(self.hotkey_buffer)
        self.destroy()
    
    def cancel(self):
        """Abbrechen ohne Speichern"""
        self.result = None
        self.destroy()


class ServerPortDialog(tk.Toplevel):
    """Simple dialog asking for the server listen port"""
    
    def __init__(self, parent, default_port=64128):
        super().__init__(parent)
        self.title("Server Mode")
        self.geometry("340x150")
        self.resizable(False, False)
        self.result = None
        
        # Content
        frame = ttk.Frame(self, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="Listen Port:", font=('Arial', 11)).pack(anchor=tk.W)
        
        self.port_var = tk.StringVar(value=str(default_port))
        self.port_entry = ttk.Entry(frame, textvariable=self.port_var, font=('Arial', 12), width=10)
        self.port_entry.pack(anchor=tk.W, pady=(5, 15))
        self.port_entry.select_range(0, tk.END)
        self.port_entry.focus_set()
        
        # Buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="Start", command=self.on_ok, width=10).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="Cancel", command=self.destroy, width=10).pack(side=tk.LEFT)
        
        self.bind('<Return>', lambda e: self.on_ok())
        self.bind('<Escape>', lambda e: self.destroy())
        
        self.transient(parent)
        self.grab_set()
        
        # Center on parent
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")
    
    def on_ok(self):
        try:
            port = int(self.port_var.get().strip())
            if not (1 <= port <= 65535):
                raise ValueError
            self.result = port
            self.destroy()
        except ValueError:
            messagebox.showwarning("Invalid Port", 
                "Please enter a valid port number (1-65535).", parent=self)


class ServerClientAdapter:
    """Adapts a raw socket to the interface expected by update_loop and FileTransfer.
    Mimics the telnet_client interface completely:
    - has_received_data(), get_received_data(timeout), get_received_data_raw(size, timeout)
    - send_raw(data) -> returns True on success
    - send_bytes(data), send_key(byte_val)
    - clear_receive_buffer()
    - settimeout(timeout)
    - connected, receive_queue, socket (alias for sock)
    
    The socket stays in BLOCKING mode so that FileTransfer can do direct
    blocking reads via get_received_data_raw(). The background recv thread
    uses select() and pauses during transfers (_transfer_mode)."""
    
    def __init__(self, sock):
        self.sock = sock
        self.socket = sock  # Alias - FileTransfer uses self.connection.socket
        self.sock.setblocking(True)
        self.sock.settimeout(None)
        self.connected = True
        self.receive_queue = queue.Queue()
        self._recv_lock = threading.Lock()
        self._transfer_mode = False
        
        # Start receive thread
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()
    
    def _recv_loop(self):
        """Background thread: read from socket into queue using select()"""
        import select
        while self.connected:
            if self._transfer_mode:
                time.sleep(0.05)
                continue
            try:
                readable, _, _ = select.select([self.sock], [], [], 0.05)
                if readable:
                    data = self.sock.recv(4096)
                    if data:
                        self.receive_queue.put(data)
                    else:
                        self.connected = False
                        break
            except (ValueError, OSError):
                self.connected = False
                break
    
    def set_transfer_mode(self, active):
        """Pause/resume the recv thread so FileTransfer can read the socket directly"""
        self._transfer_mode = active
        if active:
            # Give recv thread time to actually pause
            time.sleep(0.1)
    
    def settimeout(self, timeout):
        """Set socket timeout - used by FileTransfer for blocking reads"""
        try:
            self.sock.settimeout(timeout)
        except OSError:
            pass
    
    def has_received_data(self):
        return not self.receive_queue.empty()
    
    def get_received_data(self, timeout=0.1):
        """Get data from queue with optional timeout. Returns bytes or None."""
        try:
            return self.receive_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def get_received_data_raw(self, size, timeout=3):
        """Blocking read of exactly size bytes from socket (used by FileTransfer).
        Reads directly from socket, not from queue."""
        result = bytearray()
        end_time = time.time() + timeout
        old_timeout = self.sock.gettimeout()
        
        try:
            while len(result) < size and time.time() < end_time:
                remaining_time = end_time - time.time()
                if remaining_time <= 0:
                    break
                self.sock.settimeout(min(remaining_time, 1.0))
                try:
                    chunk = self.sock.recv(size - len(result))
                    if chunk:
                        result.extend(chunk)
                    else:
                        self.connected = False
                        break
                except socket.timeout:
                    continue
                except OSError:
                    self.connected = False
                    break
        finally:
            try:
                self.sock.settimeout(old_timeout)
            except OSError:
                pass
        
        return bytes(result) if result else None
    
    def clear_receive_buffer(self):
        """Discard all queued received data"""
        while not self.receive_queue.empty():
            try:
                self.receive_queue.get_nowait()
            except queue.Empty:
                break
    
    def send_raw(self, data):
        """Send raw bytes to the client. Returns True on success (FileTransfer checks this!)."""
        if not self.connected:
            return False
        try:
            if isinstance(data, (bytes, bytearray)):
                self.sock.sendall(data)
            else:
                self.sock.sendall(data.encode('latin-1'))
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.connected = False
            return False
    
    def send_bytes(self, data):
        """Send multiple bytes (alias for send_raw, used by hotkey sender)"""
        return self.send_raw(data)
    
    def send_key(self, byte_val):
        """Send a single byte"""
        return self.send_raw(bytes([byte_val]))
    
    def close(self):
        self.connected = False
        try:
            self.sock.close()
        except Exception:
            pass


class ServerConnectionWrapper:
    """Lightweight wrapper that mimics BBSConnection for server mode"""
    
    def __init__(self, client_adapter):
        self.client = client_adapter
        self.config = {'host': 'server', 'port': 0}
    
    def send_key(self, petscii_byte):
        """Send a single PETSCII byte"""
        self.client.send_raw(bytes([petscii_byte]))
    
    def send_raw(self, data):
        """Send raw bytes"""
        self.client.send_raw(data)
    
    def send_bytes(self, data):
        """Send multiple bytes"""
        self.client.send_bytes(data)
    
    def disconnect(self):
        """Disconnect the client"""
        self.client.close()


class BBSTerminal(tk.Tk):
    """Hauptanwendung mit allen Features"""
    
    def __init__(self):
        super().__init__()
        
        self.title(f"PYCGMS V{PYCGMS_VERSION} by lA-sTYLe/Quantum (2026)")
        # 1320x880 garantiert Zoom 4x (1280x800) + Menubar/Statusbar
        self.geometry("1320x880")
        
        # Config laden
        self.settings = self.load_config()
        
        # Set global debug flags
        global _TERMINAL_DEBUG
        _TERMINAL_DEBUG = self.settings.get('transfer_debug', False)
        set_telnet_debug(_TERMINAL_DEBUG)  # Also set telnet client debug
        
        # State
        self.connected = False
        self.bbs_connection = None
        
        # Protocol aus Config laden (Default: TurboModem)
        saved_protocol = self.settings.get('transfer_protocol', 'TurboModem')
        self.current_protocol = TransferProtocol.TURBOMODEM  # Default
        for proto in TransferProtocol:
            if proto.value == saved_protocol:
                self.current_protocol = proto
                break
        
        self.screen_width = self.settings.get('screen_width', 40)
        self.screen_height = 25
        self._transfer_active = False
        self.current_zoom = 4  # Starte mit höherem Zoom
        self.fullscreen = False  # Fullscreen-Status
        
        # Keyboard Layout Einstellung laden und aktivieren
        swap_zy = self.settings.get('swap_zy', False)
        from c64_keyboard import set_swap_zy
        set_swap_zy(swap_zy)
        if swap_zy:
            debug_print("Keyboard layout: US (QWERTY) - Z/Y swapped")
        else:
            debug_print("Keyboard layout: German (QWERTZ)")
        
        # Resize throttling
        self.resize_pending = False
        self.last_canvas_width = 0
        self.last_canvas_height = 0
        
        # Login-Daten für F9
        self.current_bbs_username = ""
        self.current_bbs_password = ""
        self.current_bbs_delay = 100
        self.current_bbs_host = ""  # BBS Host
        self.current_bbs_port = 0   # BBS Port
        
        # Hotkeys (Ctrl+Alt+F1-F10 = AltGr+F1-F10)
        self.hotkeys = {}  # Dictionary: F-Key Nummer → PETSCII Bytes
        self.load_hotkeys()
        
        # Terminal Keyboard Map (aus terminal.map)
        self.terminal_keymap = {}  # Dictionary: keysym → PETSCII Code(s)
        self.load_terminal_keymap()
        
        # Alt-Taste Tracking (Workaround für Num Lock Problem)
        self.alt_pressed = False
        
        # CTRL+B Modus für lokale Hintergrundfarbe (CCGMS/Novaterm kompatibel)
        self.awaiting_bg_color = False
        
        # Scrollback Buffer
        self.scrollback = ScrollbackBuffer(max_lines=10000)
        
        # Server Mode State
        self.server_mode = False
        self.server_socket = None
        self.server_thread = None
        self.server_port = 64128  # Default port
        
        # Screen Buffer
        self.screen = PETSCIIScreenBuffer(self.screen_width, self.screen_height)
        self.parser = PETSCIIParser(self.screen)
        
        # Renderer
        self.renderer = AnimatedC64ROMFontRenderer(
            self.screen,
            font_upper_path="upper.bmp",
            font_lower_path="lower.bmp",
            zoom=self.current_zoom
        )
        
        # UI erstellen
        self.create_ui()
        self.create_menu()
        self.bind_keys()
        
        # Resize Handler - auf Canvas binden statt Window
        self.canvas.bind("<Configure>", self.on_canvas_resize)
        
        # Initial Zoom berechnen - NUR EINMAL nach kurzer Verzögerung
        self.after(200, self.update_zoom)
        
        # Preload häufige Zoom-Levels im Hintergrund (nach 1 Sekunde)
        self.after(1000, self.preload_fonts)
        
        # Lade Startup Screen (nach 300ms, nach zoom)
        self.after(300, self.load_startup_screen)
        
        # Cursor Animation starten
        self.cursor_visible = True
        self.after(500, self.animate_terminal_cursor)
        
        # Update Loop
        self.after(50, self.update_loop)
    
    @property
    def transfer_active(self):
        return self._transfer_active
    
    @transfer_active.setter
    def transfer_active(self, value):
        self._transfer_active = value
        # In server mode, pause/resume the recv thread so FileTransfer
        # can read the socket directly for protocol handshakes
        if self.server_mode and self.bbs_connection and hasattr(self.bbs_connection, 'client'):
            client = self.bbs_connection.client
            if hasattr(client, 'set_transfer_mode'):
                client.set_transfer_mode(value)
    
    def create_ui(self):
        """Erstellt UI"""
        # Terminal Display - Canvas expandiert mit Fenster
        self.canvas = tk.Canvas(self, bg='black', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, side=tk.TOP)
        
        # Status Bar
        status_frame = ttk.Frame(self)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM)
        
        # Status Text (links)
        self.status_var = tk.StringVar(value="Not connected | F7=Dial F9=Login F1=Upload F3=Download F5=Settings")
        ttk.Label(status_frame, textvariable=self.status_var, relief=tk.SUNKEN).pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # Column Mode (rechts)
        self.column_var = tk.StringVar(value=f"{self.screen_width} COL")
        ttk.Label(status_frame, textvariable=self.column_var, relief=tk.SUNKEN, width=8).pack(side=tk.RIGHT)
    
    def create_menu(self):
        """Erstellt Menü"""
        menubar = tk.Menu(self)
        self.config(menu=menubar)
        
        # File
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Connect (F7)", command=self.show_dial_dialog)
        file_menu.add_command(label="Auto-Login (F9)", command=self.send_auto_login)
        file_menu.add_command(label="Disconnect", command=self.disconnect)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.quit)
        
        # Transfer
        transfer_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Transfer", menu=transfer_menu)
        transfer_menu.add_command(label="Upload (F1)", command=self.show_upload)
        transfer_menu.add_command(label="Send File (F2)", command=self.send_file)
        transfer_menu.add_command(label="Download (F3)", command=self.show_download)
        transfer_menu.add_separator()
        transfer_menu.add_command(label="Cycle Protocol (Alt+P)", command=self.cycle_protocol)
        transfer_menu.add_command(label="Settings (F5)", command=self.show_settings)
        
        # Server
        self.server_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Server", menu=self.server_menu)
        self.server_menu.add_command(label="Start Server Mode...", command=self.start_server_mode)
        self.server_menu.add_command(label="Stop Server Mode", command=self.stop_server_mode, state=tk.DISABLED)
        
        # View
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Fullscreen (F6)", command=self.toggle_fullscreen)
        view_menu.add_command(label="Toggle Charset (F8)", command=self.toggle_charset)
        view_menu.add_command(label="Buffer (F4)", command=self.show_scrollback)
        view_menu.add_command(label="📸 Screenshot (Alt+S)", command=self.take_screenshot)
        view_menu.add_separator()
        view_menu.add_command(label="Tools Menu (F10)", command=self.show_tools_menu)
        view_menu.add_separator()
        view_menu.add_command(label="⌨️ Hotkey Editor (Alt+H)", command=self.show_hotkey_editor)
    
    def bind_keys(self):
        """Bind Tastatur"""
        # Alt-Taste Tracking (Workaround für Num Lock = 0x0008 Problem)
        self.bind("<KeyPress-Alt_L>", self.on_alt_press)
        self.bind("<KeyPress-Alt_R>", self.on_alt_press)
        self.bind("<KeyRelease-Alt_L>", self.on_alt_release)
        self.bind("<KeyRelease-Alt_R>", self.on_alt_release)
        
        # Normale F-Keys (ohne Ctrl)
        self.bind("<F1>", lambda e: self.show_upload())
        self.bind("<F2>", lambda e: self.send_file())  # ← NEU: Send Latin-1 File
        self.bind("<F3>", lambda e: self.show_download())
        self.bind("<F4>", lambda e: self.show_scrollback())
        self.bind("<F5>", lambda e: self.show_settings())
        self.bind("<F6>", lambda e: self.toggle_fullscreen())
        self.bind("<F7>", lambda e: self.show_dial_dialog())
        self.bind("<F8>", lambda e: self.toggle_charset())
        self.bind("<F9>", lambda e: self.send_auto_login())
        self.bind("<F10>", lambda e: self.show_tools_menu())
        self.bind("<Alt-h>", lambda e: self.show_hotkey_editor())  # Hotkey Editor!
        self.bind("<Alt-H>", lambda e: self.show_hotkey_editor())  # Hotkey Editor (Shift)
        self.bind("<Alt-p>", lambda e: self.cycle_protocol())  # Protocol wechseln
        self.bind("<Alt-P>", lambda e: self.cycle_protocol())  # Protocol wechseln (Shift)
        self.bind("<Alt-s>", lambda e: self.take_screenshot())  # Screenshot
        self.bind("<Alt-S>", lambda e: self.take_screenshot())  # Screenshot (Shift)
        self.bind("<F12>", lambda e: self.toggle_traffic_logger())
        
        # Traffic Logger State
        self.traffic_logger_active = False
        self.traffic_log_file = None
        self.traffic_log_count = 0
        
        # AltGr+F-Keys für Hotkeys
        # Auf Windows: AltGr = Control-Alt Kombination
        # Auf Linux: AltGr = ISO_Level3_Shift
        for i in range(1, 11):
            fkey = f"F{i}"
            # Windows: Control+Alt gleichzeitig
            self.bind(f"<Control-Alt-{fkey}>", lambda e, num=i: self.send_hotkey(num))
        
        self.bind("<Escape>", lambda e: self.exit_fullscreen() if self.fullscreen else None)
        self.bind("<Key>", self.on_key_press)
    
    def on_alt_press(self, event):
        """Alt-Taste gedrückt (Backup für Systeme wo Bit-Erkennung nicht funktioniert)"""
        self.alt_pressed = True
    
    def on_alt_release(self, event):
        """Alt-Taste losgelassen"""
        self.alt_pressed = False
    
    def send_hotkey(self, fkey_num):
        """Sendet Hotkey F1-F10 als raw bytes direkt zum Socket (PETSCII Grafik + Farbcodes)"""
        if not self.connected:
            return
        
        if fkey_num in self.hotkeys:
            hotkey_bytes = self.hotkeys[fkey_num]
            
            debug_print(f"Sending hotkey F{fkey_num}: {len(hotkey_bytes)} bytes")
            debug_print(f"  Bytes: {' '.join(f'{b:02X}' for b in hotkey_bytes)}")
            
            # Log outgoing traffic
            self.log_traffic("SEND", hotkey_bytes)
            
            # Sende DIREKT zum Socket als raw bytes
            # WICHTIG: Keine PETSCII-Konvertierung, keine Verzögerung
            # Genau wie: conn.sendall(b"\x05\x1f\xc2...")
            if hasattr(self.bbs_connection, 'client'):
                # BBSConnection wrapper
                self.bbs_connection.client.send_bytes(hotkey_bytes)
            else:
                # Direct client
                self.bbs_connection.send_bytes(hotkey_bytes)
            
            # Füge zu Scrollback hinzu für lokale Anzeige
            if not self.transfer_active:
                self.scrollback.add_bytes(hotkey_bytes)
        else:
            debug_print(f"No hotkey defined for F{fkey_num}")
    
    def send_file(self):
        """F2 - Send File (Latin-1 encoded text file)"""
        if not self.connected:
            messagebox.showwarning("Not Connected", "Connect to BBS first!")
            return
        
        # Nutze Upload Ordner aus Settings (falls gesetzt)
        initial_dir = self.settings.get('upload_folder', None)
        
        filepath = filedialog.askopenfilename(
            parent=self,  # self IST root (BBSTerminal erbt von tk.Tk)
            title="Select file to send (Latin-1 text)",
            initialdir=initial_dir,
            filetypes=[
                ("Text files", "*.txt"),
                ("SEQ files", "*.seq"),
                ("All files", "*.*")
            ]
        )
        if not filepath:
            return
        
        try:
            # Lade Datei als Latin-1
            with open(filepath, 'r', encoding='latin-1') as f:
                content = f.read()
            
            # Konvertiere zu Bytes
            data = content.encode('latin-1')
            
            # Zeige Info
            filename = os.path.basename(filepath)
            result = messagebox.askokcancel(
                "Send File",
                f"Send file: {filename}\n"
                f"Size: {len(data):,} bytes\n"
                f"Encoding: Latin-1\n\n"
                f"The file will be sent byte-by-byte to the BBS.\n"
                f"Continue?"
            )
            
            if not result:
                return
            
            # Progress Dialog
            self.transfer_active = True
            progress = TransferProgressDialog(self, "Send File", is_upload=True, 
                                              is_punter=True, bbs_connection=self.bbs_connection)
            
            # Sende in separatem Thread
            def send_thread():
                import time
                
                debug_print(f"Sending file: {filename} ({len(data)} bytes)")
                
                # Sende Byte für Byte mit kleiner Verzögerung
                for i, byte in enumerate(data):
                    # Check Cancel
                    if progress.cancelled:
                        debug_print("Send cancelled by user")
                        break
                    
                    if not self.connected:
                        debug_print("Connection lost - stopping send")
                        break
                    
                    self.bbs_connection.send_key(byte)
                    if not self.transfer_active:
                        self.scrollback.add_bytes([byte])
                    
                    # Update Progress alle 100 Bytes
                    if i % 100 == 0:
                        status = f"Sending {filename}"
                        progress.after(0, lambda d=i+1, t=len(data), s=status: 
                                     progress.update_progress(d, t, s))
                    
                    # Kleine Verzögerung alle 10 Bytes um BBS nicht zu überlasten
                    if i % 10 == 0:
                        time.sleep(0.01)  # 10ms
                
                # Finale Update
                def finish():
                    self.transfer_active = False
                    try:
                        if not progress.cancelled:
                            progress.destroy()
                            
                            if i == len(data) - 1:  # Komplett gesendet
                                debug_print(f"File sent: {len(data)} bytes")
                                messagebox.showinfo("Send Complete", 
                                    f"File sent successfully!\n"
                                    f"File: {filename}\n"
                                    f"Size: {len(data):,} bytes")
                            else:
                                debug_print(f"Send incomplete: {i+1}/{len(data)} bytes")
                        else:
                            progress.destroy()
                            debug_print("Send cancelled")
                    except tk.TclError:
                        pass  # Dialog bereits geschlossen
                
                self.after(0, finish)
            
            import threading
            threading.Thread(target=send_thread, daemon=True).start()
            
        except Exception as e:
            messagebox.showerror("Error", f"Could not read file:\n{e}")
            import traceback
            traceback.print_exc()
    
    def on_key_press(self, event):
        """Tastatur-Handler mit terminal.map Unterstützung"""
        if not self.connected:
            return
        
        # WICHTIG: Während Transfer KEINE Tastatur-Eingaben senden!
        if self.transfer_active:
            return "break"
        
        shift = (event.state & 0x1) != 0
        ctrl = (event.state & 0x4) != 0
        # Alt: Bit 0x20000 oder 0x40000 (NICHT 0x0008, das ist Num Lock!)
        alt = bool(event.state & 0x20000) or bool(event.state & 0x40000)
        
        # Debug: Zeige event.state um Modifier-Bits zu analysieren
        debug_print(f"Key: keysym='{event.keysym}' state=0x{event.state:08X} shift={shift} ctrl={ctrl} alt={alt} awaiting_bg={self.awaiting_bg_color}")
        
        # ============================================================
        # CTRL+B Modus: Lokale Hintergrundfarbe ändern
        # MUSS VOR terminal.map kommen!
        # ============================================================
        
        # CTRL+B erkennen - Modus aktivieren
        if ctrl and event.keysym.lower() == 'b':
            self.awaiting_bg_color = True
            debug_print(f"[LOCAL BG] *** CTRL+B pressed, waiting for color (1-8) ***")
            # Sende AUCH an BBS (für BBSe die das unterstützen)
            self.log_traffic("SEND", 0x02)
            self.bbs_connection.send_key(0x02)
            return "break"
        
        # CTRL+N = Hintergrund auf Schwarz zurücksetzen
        if ctrl and event.keysym.lower() == 'n':
            self.screen.screen_bg = 0
            self.awaiting_bg_color = False
            debug_print(f"[LOCAL BG] Reset to black (CTRL+N)")
            # Sende AUCH an BBS
            self.log_traffic("SEND", 0x0E)
            self.bbs_connection.send_key(0x0E)
            return "break"
        
        # Wenn awaiting_bg_color aktiv ist, Zahlentasten abfangen
        if self.awaiting_bg_color:
            debug_print(f"[LOCAL BG] Awaiting color, got key: {event.keysym}")
            
            # Farb-Mapping: 1-8 = Farben 0-7 (mit oder ohne CTRL)
            color_map = {
                '1': 0,  # Schwarz
                '2': 1,  # Weiß
                '3': 2,  # Rot
                '4': 3,  # Cyan
                '5': 4,  # Lila
                '6': 5,  # Grün
                '7': 6,  # Blau
                '8': 7,  # Gelb
                '9': 8,  # Orange
                '0': 9,  # Braun
            }
            
            key = event.keysym
            
            if key in color_map:
                color = color_map[key]
                # NICHT beenden! Modus bleibt aktiv für weitere Farbwechsel
                self.screen.screen_bg = color
                debug_print(f"[LOCAL BG] *** Background color set to {color} ***")
                return "break"
            elif key == 'b' and ctrl:
                # CTRL+B nochmal gedrückt - ignorieren
                return "break"
            elif key in ['Control_L', 'Control_R', 'Alt_L', 'Alt_R', 'Shift_L', 'Shift_R']:
                # Modifier-Tasten ignorieren
                return "break"
            else:
                # Andere Taste gedrückt - Modus beenden und normal weitermachen
                self.awaiting_bg_color = False
                debug_print(f"[LOCAL BG] Mode ended (key={key})")
                # NICHT return - Taste normal verarbeiten
        
        # Debug: Zeige was gedrückt wurde (nur bei Sondertasten oder wenn alt)
        if ctrl or alt or event.keysym in ['F1','F2','F3','F4','F5','F6','F7','F8','F9','F10','F11','F12',
                                           'Up','Down','Left','Right','Home','End','Insert','Delete',
                                           'Prior','Next','Escape','Pause','Scroll_Lock','Alt_L','Alt_R']:
            debug_print(f"Key: char='{event.char}' keysym='{event.keysym}' shift={shift} ctrl={ctrl} alt={alt}")
        
        # 1. ZUERST: Prüfe terminal.map
        mapped = self.get_mapped_key(event.keysym, shift, ctrl, alt)
        if mapped:
            debug_print(f"  → terminal.map: {[hex(b) for b in mapped]}")
            for petscii_byte in mapped:
                self.log_traffic("SEND", petscii_byte)
                self.bbs_connection.send_key(petscii_byte)
                self.scrollback.add_bytes([petscii_byte])
                # Server Mode: Local Echo
                if self.server_mode:
                    self.parser.parse_bytes(bytes([petscii_byte]))
            return "break"
        
        # 2. DANN: Standard C64 Keyboard Mapping
        # Ctrl+Nummer: Nutze keysym wenn char leer ist
        if ctrl and not event.char and event.keysym in '0123456789':
            debug_print(f"  Using keysym for Ctrl+{event.keysym}")
            petscii_code = get_petscii_for_key(event.keysym, event.keysym, shift, ctrl, False)
        else:
            petscii_code = get_petscii_for_key(event.char, event.keysym, shift, ctrl, False)
        
        if petscii_code is not None:
            if ctrl or alt:
                debug_print(f"  → PETSCII: 0x{petscii_code:02X}")
            
            # Log outgoing traffic
            self.log_traffic("SEND", petscii_code)
            
            self.bbs_connection.send_key(petscii_code)
            self.scrollback.add_bytes([petscii_code])
            
            # Server Mode: Local Echo
            if self.server_mode:
                self.parser.parse_bytes(bytes([petscii_code]))
            
            return "break"
    
    def show_dial_dialog(self):
        """F7 - Dialer"""
        dialog = BBSDialDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            self.connect_bbs(
                dialog.result['host'], 
                dialog.result['port'],
                dialog.result.get('username', ''),
                dialog.result.get('password', ''),
                dialog.result.get('send_delay', 100),
                dialog.result.get('protocol'),  # Protocol laden!
                dialog.result.get('transfer_speed')  # Transfer Speed laden!
            )
    
    def show_upload(self):
        """F1 - Upload"""
        if not self.connected:
            messagebox.showwarning("Not Connected", "Connect to BBS first!")
            return
        
        # Nutze Upload Ordner aus Settings (falls gesetzt)
        initial_dir = self.settings.get('upload_folder', None)
        
        # MULTI-FILE SELECTION aktiviert!
        filepaths = filedialog.askopenfilenames(  # ← "askopenfilenames" mit 's'!
            parent=self,  # self IST root (BBSTerminal erbt von tk.Tk)
            title="Select file(s) to upload (multiple selection allowed)",
            initialdir=initial_dir,
            filetypes=[
                ("C64 Programs", "*.prg"),
                ("Sequential Files", "*.seq"),
                ("All Files", "*.*")
            ]
        )
        
        if not filepaths:
            return
        
        # Konvertiere tuple zu list
        filepaths = list(filepaths)
        num_files = len(filepaths)
        
        import os  # Import hier für beide Pfade!
        
        # Smart Protocol Detection:
        # - 1 File + YModem selected → Use XModem-1K (no header)
        # - Multiple Files + YModem → Use YModem Batch (with headers)
        from file_transfer import TransferProtocol
        
        original_protocol = self.current_protocol
        
        if num_files == 1 and self.current_protocol == TransferProtocol.YMODEM:
            debug_print(f"\n📤 Single file upload: Using XModem-1K (no filename header)")
            upload_protocol = TransferProtocol.XMODEM_1K  # Use Enum direkt!
            filepath = filepaths[0]  # Single file
            is_multi = False
        elif num_files > 1 and self.current_protocol == TransferProtocol.YMODEM:
            debug_print(f"\n📤 Batch upload: {num_files} files using YModem")
            upload_protocol = TransferProtocol.YMODEM
            filepath = filepaths  # List of files
            is_multi = True
        else:
            # Use selected protocol as-is
            upload_protocol = self.current_protocol
            filepath = filepaths[0] if num_files == 1 else filepaths
            is_multi = (num_files > 1)
        
        debug_print(f"Protocol: {upload_protocol}")
        if num_files == 1:
            debug_print(f"File: {os.path.basename(filepath)}")
        else:
            debug_print(f"Files: {', '.join([os.path.basename(f) for f in filepaths])}")
        
        # WICHTIG: Leere empfangene Daten bevor Upload startet
        # BBS sendet oft Text/Prompts die nicht Teil des Protocols sind
        # ABER: Bei RAWTCP liegt das READY Signal schon im Buffer - NICHT leeren!
        if upload_protocol != TransferProtocol.RAWTCP:
            self.bbs_connection.client.clear_receive_buffer()
        
        self.transfer_active = True
        
        # Für Punter Multi-File: Zeige File-Liste
        # Alle Transfers bekommen Waiting + CTRL+X
        # Debug (Activity Log + Buttons) aus Settings
        is_punter = upload_protocol == TransferProtocol.PUNTER
        show_file_list = is_multi and is_punter
        transfer_debug = self.settings.get('transfer_debug', False)
        progress = TransferProgressDialog(
            self, "Upload File", is_upload=True, 
            show_file_list=show_file_list,
            file_list=filepaths if show_file_list else None,
            punter_debug=transfer_debug,
            is_punter=True,  # Alle Transfers bekommen Waiting + CTRL+X
            bbs_connection=self.bbs_connection  # Für CTRL+X
        )
        
        def upload_thread():
            import time
            import os
            from file_transfer import TransferSpeed
            
            # Hole Speed Profile aus Settings
            speed_name = self.settings.get('transfer_speed', 'normal')
            try:
                speed_profile = TransferSpeed(speed_name)
            except ValueError:
                speed_profile = TransferSpeed.NORMAL
            
            # Log-Verzeichnis = Download-Verzeichnis (auch für Upload-Logs)
            log_dir = self.settings.get('download_folder', os.path.dirname(os.path.abspath(__file__)))
            
            transfer = FileTransfer(self.bbs_connection.client, upload_protocol, speed_profile, log_dir=log_dir, debug=transfer_debug)
            
            # Setze FileTransfer-Referenz für Live-Updates (alle Transfers)
            progress.file_transfer = transfer
            transfer.set_live_callback(progress.live_update)
            
            start_time = time.time()
            
            # Filesize und Filename
            if is_multi:
                filesize = sum(os.path.getsize(f) for f in filepath)  # Total size
                filename = f"{len(filepath)} files"  # Display name
            else:
                filesize = os.path.getsize(filepath)
                filename = os.path.basename(filepath)
            
            def callback(done, total, status, filename=None, **kwargs):
                # Prüfe ob User Cancel gedrückt hat
                if progress.cancelled:
                    transfer.cancel()
                    return
                
                # Dateiname: direkt als Parameter oder aus kwargs
                current_filename = filename or kwargs.get('filename') or kwargs.get('event_filename')
                
                # Handle File-Events für Multi-File
                event = kwargs.get('event')
                event_filename = kwargs.get('filename') or filename
                event_size = kwargs.get('size', 0)
                
                if event == 'file_start' and event_filename:
                    progress.after(0, lambda fn=event_filename: progress.set_file_active(fn))
                    progress.files_completed = kwargs.get('file_num', progress.files_completed)
                elif event == 'file_complete' and event_filename:
                    progress.after(0, lambda fn=event_filename, sz=event_size: progress.set_file_complete(fn, sz))
                    progress.files_completed += 1
                elif event == 'file_error' and event_filename:
                    progress.after(0, lambda fn=event_filename: progress.set_file_error(fn))
                
                # WICHTIG: Rate-Limiting für GUI Updates!
                # TurboModem ist so schnell dass ohne Throttling Tkinter abstürzt
                current_time = time.time()
                if not hasattr(callback, 'last_update'):
                    callback.last_update = 0
                
                # Update nur alle 100ms (= max 10 Updates/Sekunde)
                if current_time - callback.last_update >= 0.1:
                    callback.last_update = current_time
                    progress.after(0, lambda d=done, t=total, s=status, fn=current_filename: 
                                  progress.update_progress(d, t, s, fn))
            
            success = transfer.send_file(filepath, callback)
            
            def finish():
                # Zeige Debug-Log Pfad
                if transfer.debug_file:
                    debug_print(f"📄 Upload debug log: {transfer.debug_file}")
                
                self.transfer_active = False
                if not progress.cancelled:
                    try:
                        progress.destroy()
                    except tk.TclError:
                        pass  # Dialog bereits geschlossen
                    if success:
                        # Berechne Transfer-Zeit
                        end_time = time.time()
                        duration = end_time - start_time
                        
                        # Berechne Geschwindigkeit
                        bytes_per_sec = filesize / duration if duration > 0 else 0
                        
                        # Formatiere Zeit
                        if duration < 60:
                            time_str = f"{duration:.1f} seconds"
                        else:
                            mins = int(duration // 60)
                            secs = duration % 60
                            time_str = f"{mins} minute{'s' if mins != 1 else ''}, {secs:.1f} seconds"
                        
                        # Formatiere Geschwindigkeit
                        if bytes_per_sec < 1024:
                            speed_str = f"{bytes_per_sec:.0f} bytes/sec"
                        elif bytes_per_sec < 1024 * 1024:
                            speed_str = f"{bytes_per_sec/1024:.1f} KB/sec"
                        else:
                            speed_str = f"{bytes_per_sec/(1024*1024):.1f} MB/sec"
                        
                        if is_multi:
                            messagebox.showinfo("Upload Complete", 
                                f"Files: {num_files}\n"
                                f"Total Size: {filesize:,} bytes\n"
                                f"Time: {time_str}\n"
                                f"Speed: {speed_str}")
                        else:
                            messagebox.showinfo("Upload Complete", 
                                f"File: {filename}\n"
                                f"Size: {filesize:,} bytes\n"
                                f"Time: {time_str}\n"
                                f"Speed: {speed_str}")
                    else:
                        messagebox.showerror("Error", "Upload failed!")
            
            self.after(0, finish)
        
        threading.Thread(target=upload_thread, daemon=True).start()
    
    def show_download(self):
        """F3 - Download"""
        if not self.connected:
            messagebox.showwarning("Not Connected", "Connect to BBS first!")
            return
        
        # Nutze Download Ordner aus Settings, falls gesetzt
        # Sonst: Script-Root Verzeichnis
        download_dir = self.settings.get('download_folder', None)
        if not download_dir:
            import os
            # Script-Root (wo run_terminal.py liegt)
            download_dir = os.path.dirname(os.path.abspath(__file__))
        
        # YModem, TurboModem, Punter, High-Speed oder XModem
        if self.current_protocol in [TransferProtocol.YMODEM, TransferProtocol.TURBOMODEM, TransferProtocol.PUNTER,
                                     TransferProtocol.RAWTCP]:
            # Diese Protokolle verwalten Dateinamen selbst
            filepath = download_dir
            temp_filepath = None  # Kein temp file - Protokoll setzt finalen Namen
        else:
            # XModem: Download zu temporärer Datei
            import os
            temp_filepath = os.path.join(download_dir, "tmpdown.bin")
            filepath = temp_filepath
        
        self.transfer_active = True
        # Alle Transfers bekommen Waiting + CTRL+X
        # Debug (Activity Log + Buttons) aus Settings
        is_punter = self.current_protocol == TransferProtocol.PUNTER
        show_file_list = is_punter
        transfer_debug = self.settings.get('transfer_debug', False)
        progress = TransferProgressDialog(self, "Download File", is_upload=False, 
                                          show_file_list=show_file_list, punter_debug=transfer_debug,
                                          is_punter=True,  # Alle Transfers bekommen Waiting + CTRL+X
                                          bbs_connection=self.bbs_connection)  # Für CTRL+X
        
        def download_thread():
            import time
            import os
            from file_transfer import TransferSpeed
            
            # Hole Speed Profile aus Settings
            speed_name = self.settings.get('transfer_speed', 'normal')
            try:
                speed_profile = TransferSpeed(speed_name)
            except ValueError:
                speed_profile = TransferSpeed.NORMAL
            
            # Log-Verzeichnis = Download-Verzeichnis
            log_dir = self.settings.get('download_folder', os.path.dirname(os.path.abspath(__file__)))
            
            transfer = FileTransfer(self.bbs_connection.client, self.current_protocol, speed_profile, log_dir=log_dir, debug=transfer_debug)
            
            # Setze FileTransfer-Referenz für Live-Updates (alle Transfers)
            progress.file_transfer = transfer
            transfer.set_live_callback(progress.live_update)
            
            start_time = time.time()
            final_bytes = 0
            final_status = ""
            received_filename = None  # YModem Filename wenn vorhanden
            received_header_names = []  # Liste der vom BBS empfangenen Dateinamen
            
            def callback(done, total, status, filename=None, **kwargs):
                nonlocal final_bytes, final_status, received_filename, received_header_names
                
                final_bytes = done if done > final_bytes else final_bytes
                final_status = status
                
                # Handle File-Events (RAWTCP Batch, Punter Multi)
                event = kwargs.get('event')
                event_filename = kwargs.get('filename') or filename
                event_size = kwargs.get('size', 0)
                
                if event == 'file_start' and event_filename:
                    if event_filename not in received_header_names:
                        received_header_names.append(event_filename)
                    received_filename = event_filename
                elif event == 'file_complete' and event_filename:
                    debug_print(f"[RAWTCP] File complete: {event_filename} ({event_size} bytes)")
                
                # TurboModem sendet filename direkt als Parameter!
                if filename:
                    received_filename = filename
                    if filename not in received_header_names:
                        received_header_names.append(filename)
                
                # Punter: Prüfe auf FILE_COMPLETE Event
                if status and status.startswith("FILE_COMPLETE:"):
                    parts = status.split(":")
                    if len(parts) >= 4:
                        file_name = parts[1]
                        blocks = int(parts[2])
                        size_bytes = int(parts[3])
                        if not file_name.startswith('download_') and file_name not in received_header_names:
                            received_header_names.append(file_name)
                        progress.after(0, lambda f=file_name, b=blocks, s=size_bytes: 
                                      progress.add_completed_file(f, b, s))
                    return
                
                # Prüfe ob User Cancel gedrückt hat
                if progress.cancelled:
                    transfer.cancel()
                    return
                
                # WICHTIG: Rate-Limiting für GUI Updates!
                # TurboModem ist so schnell dass tausende Updates pro Sekunde kommen
                # Das führt zu RecursionError in Tkinter
                current_time = time.time()
                if not hasattr(callback, 'last_update'):
                    callback.last_update = 0
                
                # Update nur alle 100ms (= max 10 Updates/Sekunde)
                if current_time - callback.last_update >= 0.1:
                    callback.last_update = current_time
                    current_fn = received_filename or event_filename or filename
                    progress.after(0, lambda d=done, t=total, s=status, fn=current_fn: 
                                  progress.update_progress(d, t, s, fn))
            
            try:
                success = transfer.receive_file(filepath, callback)
            except Exception as e:
                transfer.log(f"EXCEPTION in receive_file: {e}")
                import traceback
                transfer.log(traceback.format_exc())
                success = False
            
            def finish():
                # Zeige Debug-Log Pfad
                if transfer.debug_file:
                    debug_print(f"📄 Download debug log: {transfer.debug_file}")
                
                self.transfer_active = False
                if not progress.cancelled:
                    try:
                        progress.destroy()
                    except tk.TclError:
                        pass  # Dialog bereits geschlossen
                    if success:
                        # Berechne Transfer-Zeit
                        end_time = time.time()
                        duration = end_time - start_time
                        
                        # XModem: Frage nach finalem Dateinamen
                        # (YModem, TurboModem, Punter und High-Speed-Protokolle setzen Namen selbst)
                        if self.current_protocol not in [TransferProtocol.YMODEM, TransferProtocol.TURBOMODEM, TransferProtocol.PUNTER,
                                                         TransferProtocol.RAWTCP] and temp_filepath:
                            # Hole Dateigröße von temp file
                            temp_filesize = os.path.getsize(temp_filepath)
                            
                            # Frage User nach finalem Dateinamen
                            final_filepath = filedialog.asksaveasfilename(
                                parent=self,  # self IST das root window (BBSTerminal erbt von tk.Tk)
                                title="Save downloaded file as",
                                defaultextension=".dat",
                                initialdir=download_dir,
                                initialfile="download.dat"
                            )
                            
                            if final_filepath:
                                # Rename temp file zu finalem Namen
                                import shutil
                                try:
                                    shutil.move(temp_filepath, final_filepath)
                                    final_filename = os.path.basename(final_filepath)
                                    
                                    # Berechne Geschwindigkeit
                                    bytes_per_sec = temp_filesize / duration if duration > 0 else 0
                                    
                                    # Formatiere Zeit
                                    if duration < 60:
                                        time_str = f"{duration:.1f} seconds"
                                    else:
                                        mins = int(duration // 60)
                                        secs = duration % 60
                                        time_str = f"{mins} minute{'s' if mins != 1 else ''}, {secs:.1f} seconds"
                                    
                                    # Formatiere Geschwindigkeit
                                    if bytes_per_sec < 1024:
                                        speed_str = f"{bytes_per_sec:.0f} bytes/sec"
                                    elif bytes_per_sec < 1024 * 1024:
                                        speed_str = f"{bytes_per_sec/1024:.1f} KB/sec"
                                    else:
                                        speed_str = f"{bytes_per_sec/(1024*1024):.1f} MB/sec"
                                    
                                    messagebox.showinfo("Download Complete", 
                                        f"File: {final_filename}\n"
                                        f"Saved to: {os.path.dirname(final_filepath)}\n"
                                        f"Size: {temp_filesize:,} bytes\n"
                                        f"Time: {time_str}\n"
                                        f"Speed: {speed_str}")
                                except Exception as e:
                                    messagebox.showerror("Error", f"Could not rename file: {e}")
                                    # Cleanup temp file
                                    try:
                                        os.remove(temp_filepath)
                                    except:
                                        pass
                            else:
                                # User cancelled - lösche temp file
                                try:
                                    os.remove(temp_filepath)
                                except:
                                    pass
                                messagebox.showinfo("Cancelled", "Download cancelled - temp file deleted")
                        
                        # YModem/TurboModem: Zeige Statistiken
                        elif self.current_protocol == TransferProtocol.TURBOMODEM:
                            # TurboModem Multi-File: Prüfe ob mehrere Dateien empfangen wurden
                            turbo_files = getattr(transfer, 'turbomodem_received_files', [])
                            
                            if turbo_files and len(turbo_files) > 1:
                                # MULTI-FILE: Zeige alle empfangenen Dateien
                                total_size = sum(os.path.getsize(f) for f in turbo_files if os.path.exists(f))
                                bytes_per_sec = total_size / duration if duration > 0 else 0
                                
                                # Formatiere Zeit
                                if duration < 60:
                                    time_str = f"{duration:.1f} seconds"
                                else:
                                    mins = int(duration // 60)
                                    secs = duration % 60
                                    time_str = f"{mins} minute{'s' if mins != 1 else ''}, {secs:.1f} seconds"
                                
                                # Formatiere Geschwindigkeit
                                if bytes_per_sec < 1024:
                                    speed_str = f"{bytes_per_sec:.0f} bytes/sec"
                                elif bytes_per_sec < 1024 * 1024:
                                    speed_str = f"{bytes_per_sec/1024:.1f} KB/sec"
                                else:
                                    speed_str = f"{bytes_per_sec/(1024*1024):.1f} MB/sec"
                                
                                # Dateiliste erstellen
                                file_list = "\n".join([f"  • {os.path.basename(f)} ({os.path.getsize(f):,} bytes)" 
                                                      for f in turbo_files if os.path.exists(f)])
                                
                                messagebox.showinfo("TurboModem Multi-File Download Complete", 
                                    f"Files received: {len(turbo_files)}\n"
                                    f"Saved to: {download_dir}\n\n"
                                    f"{file_list}\n\n"
                                    f"Total: {total_size:,} bytes\n"
                                    f"Time: {time_str}\n"
                                    f"Speed: {speed_str}")
                            
                            elif turbo_files and len(turbo_files) == 1:
                                # Single file
                                downloaded_file = turbo_files[0]
                                if os.path.exists(downloaded_file):
                                    file_size = os.path.getsize(downloaded_file)
                                    bytes_per_sec = file_size / duration if duration > 0 else 0
                                    
                                    if duration < 60:
                                        time_str = f"{duration:.1f} seconds"
                                    else:
                                        mins = int(duration // 60)
                                        secs = duration % 60
                                        time_str = f"{mins} minute{'s' if mins != 1 else ''}, {secs:.1f} seconds"
                                    
                                    if bytes_per_sec < 1024:
                                        speed_str = f"{bytes_per_sec:.0f} bytes/sec"
                                    elif bytes_per_sec < 1024 * 1024:
                                        speed_str = f"{bytes_per_sec/1024:.1f} KB/sec"
                                    else:
                                        speed_str = f"{bytes_per_sec/(1024*1024):.1f} MB/sec"
                                    
                                    messagebox.showinfo("TurboModem Download Complete", 
                                        f"File: {os.path.basename(downloaded_file)}\n"
                                        f"Saved to: {download_dir}\n"
                                        f"Size: {file_size:,} bytes\n"
                                        f"Time: {time_str}\n"
                                        f"Speed: {speed_str}")
                                else:
                                    messagebox.showinfo("Download Complete", 
                                        f"File downloaded successfully!\n"
                                        f"Saved to: {download_dir}")
                            
                            elif received_filename:
                                # Fallback: Einzelne Datei mit bekanntem Namen (alte Methode)
                                downloaded_file = os.path.join(download_dir, received_filename)
                                
                                if os.path.exists(downloaded_file):
                                    file_size = os.path.getsize(downloaded_file)
                                    bytes_per_sec = file_size / duration if duration > 0 else 0
                                    
                                    if duration < 60:
                                        time_str = f"{duration:.1f} seconds"
                                    else:
                                        mins = int(duration // 60)
                                        secs = duration % 60
                                        time_str = f"{mins} minute{'s' if mins != 1 else ''}, {secs:.1f} seconds"
                                    
                                    if bytes_per_sec < 1024:
                                        speed_str = f"{bytes_per_sec:.0f} bytes/sec"
                                    elif bytes_per_sec < 1024 * 1024:
                                        speed_str = f"{bytes_per_sec/1024:.1f} KB/sec"
                                    else:
                                        speed_str = f"{bytes_per_sec/(1024*1024):.1f} MB/sec"
                                    
                                    messagebox.showinfo("TurboModem Download Complete", 
                                        f"File: {received_filename}\n"
                                        f"Saved to: {download_dir}\n"
                                        f"Size: {file_size:,} bytes\n"
                                        f"Time: {time_str}\n"
                                        f"Speed: {speed_str}")
                                else:
                                    messagebox.showinfo("Download Complete", 
                                        f"File downloaded successfully!\n"
                                        f"Saved to: {download_dir}")
                            else:
                                messagebox.showinfo("Download Complete", 
                                    f"TurboModem transfer complete!\n"
                                    f"Saved to: {download_dir}")
                        
                        # Punter C1: Zeige Statistiken
                        elif self.current_protocol == TransferProtocol.PUNTER:
                            # Punter: Datei wurde in download_dir gespeichert
                            # Berechne Geschwindigkeit
                            bytes_per_sec = final_bytes / duration if duration > 0 else 0
                            
                            # Formatiere Zeit
                            if duration < 60:
                                time_str = f"{duration:.1f} seconds"
                            else:
                                mins = int(duration // 60)
                                secs = duration % 60
                                time_str = f"{mins} minute{'s' if mins != 1 else ''}, {secs:.1f} seconds"
                            
                            # Formatiere Geschwindigkeit
                            if bytes_per_sec < 1024:
                                speed_str = f"{bytes_per_sec:.0f} bytes/sec"
                            elif bytes_per_sec < 1024 * 1024:
                                speed_str = f"{bytes_per_sec/1024:.1f} KB/sec"
                            else:
                                speed_str = f"{bytes_per_sec/(1024*1024):.1f} MB/sec"
                            
                            # Prüfe ob Dateinamen vom BBS empfangen wurden
                            # Wenn ja -> kein Rename-Dialog nötig
                            debug_print(f"[DEBUG] received_header_names: {received_header_names}")
                            debug_print(f"[DEBUG] progress.completed_files: {progress.completed_files}")
                            
                            if len(received_header_names) > 0:
                                # Download mit Header-Namen - kein Rename nötig
                                total_files = len(progress.completed_files) if progress.completed_files else len(received_header_names)
                                
                                if total_files > 1:
                                    # Multi-File Download
                                    total_bytes = sum(f[2] for f in progress.completed_files) if progress.completed_files else final_bytes
                                    total_blocks = sum(f[1] for f in progress.completed_files) if progress.completed_files else 0
                                    
                                    messagebox.showinfo("Punter C1 Download Complete", 
                                        f"Files: {total_files}\n"
                                        f"Total: {total_bytes:,} bytes ({total_blocks} blocks)\n"
                                        f"Saved to: {download_dir}\n"
                                        f"Time: {time_str}\n"
                                        f"Speed: {speed_str}")
                                else:
                                    # Single-File mit Header-Name
                                    file_name = received_header_names[0] if received_header_names else "unknown"
                                    messagebox.showinfo("Punter C1 Download Complete", 
                                        f"File: {file_name}\n"
                                        f"Size: {final_bytes:,} bytes\n"
                                        f"Saved to: {download_dir}\n"
                                        f"Time: {time_str}\n"
                                        f"Speed: {speed_str}")
                            else:
                                # Single-Download OHNE Header - Rename anbieten
                                downloaded_files = [f for f in os.listdir(download_dir) 
                                                  if f.startswith('download_') and f.upper().endswith('.PRG')]
                                
                                if downloaded_files:
                                    # Sortiere nach Änderungsdatum, neueste zuerst
                                    downloaded_files.sort(key=lambda f: os.path.getmtime(os.path.join(download_dir, f)), reverse=True)
                                    newest_file = downloaded_files[0]
                                    old_path = os.path.join(download_dir, newest_file)
                                    
                                    # Dialog zum Umbenennen
                                    new_filepath = filedialog.asksaveasfilename(
                                        parent=self,
                                        title="Save downloaded file as",
                                        defaultextension=".PRG",
                                        initialdir=download_dir,
                                        initialfile=newest_file,
                                        filetypes=[("PRG files", "*.PRG"), ("All files", "*.*")]
                                    )
                                    
                                    if new_filepath:
                                        # Umbenennen
                                        import shutil
                                        try:
                                            shutil.move(old_path, new_filepath)
                                            final_filename = os.path.basename(new_filepath)
                                            
                                            messagebox.showinfo("Punter C1 Download Complete", 
                                                f"File: {final_filename}\n"
                                                f"Saved to: {os.path.dirname(new_filepath)}\n"
                                                f"Size: {final_bytes:,} bytes\n"
                                                f"Time: {time_str}\n"
                                                f"Speed: {speed_str}")
                                        except Exception as e:
                                            messagebox.showerror("Error", f"Could not rename file: {e}")
                                    else:
                                        # User hat abgebrochen - Datei bleibt mit generischem Namen
                                        messagebox.showinfo("Punter C1 Download Complete", 
                                            f"File: {newest_file}\n"
                                            f"Saved to: {download_dir}\n"
                                            f"Size: {final_bytes:,} bytes\n"
                                            f"Time: {time_str}\n"
                                            f"Speed: {speed_str}")
                                else:
                                    # Keine download_* Dateien gefunden - normaler Abschluss
                                    messagebox.showinfo("Punter C1 Download Complete", 
                                        f"Saved to: {download_dir}\n"
                                        f"Size: {final_bytes:,} bytes\n"
                                        f"Time: {time_str}\n"
                                        f"Speed: {speed_str}")
                        
                        # HIGH-SPEED PROTOCOLS (RAWTCP): Zeige Statistiken
                        elif self.current_protocol == TransferProtocol.RAWTCP:
                            # Hole den tatsächlichen Dateipfad vom Transfer-Objekt
                            actual_path = getattr(transfer, 'last_received_filepath', None)
                            
                            if actual_path and os.path.exists(actual_path):
                                file_size = os.path.getsize(actual_path)
                                file_name = os.path.basename(actual_path)
                                
                                # Berechne Geschwindigkeit
                                bytes_per_sec = file_size / duration if duration > 0 else 0
                                
                                # Formatiere Zeit
                                if duration < 60:
                                    time_str = f"{duration:.1f} seconds"
                                else:
                                    mins = int(duration // 60)
                                    secs = duration % 60
                                    time_str = f"{mins} minute{'s' if mins != 1 else ''}, {secs:.1f} seconds"
                                
                                # Formatiere Geschwindigkeit
                                if bytes_per_sec < 1024:
                                    speed_str = f"{bytes_per_sec:.0f} bytes/sec"
                                elif bytes_per_sec < 1024 * 1024:
                                    speed_str = f"{bytes_per_sec/1024:.1f} KB/sec"
                                else:
                                    speed_str = f"{bytes_per_sec/(1024*1024):.2f} MB/sec"
                                
                                proto_name = self.current_protocol.value
                                messagebox.showinfo(f"{proto_name} Download Complete", 
                                    f"File: {file_name}\n"
                                    f"Saved to: {os.path.dirname(actual_path)}\n"
                                    f"Size: {file_size:,} bytes\n"
                                    f"Time: {time_str}\n"
                                    f"Speed: {speed_str}")
                            else:
                                proto_name = self.current_protocol.value
                                messagebox.showinfo(f"{proto_name} Download Complete", 
                                    f"Transfer complete!\n"
                                    f"Saved to: {download_dir}")
                        
                        # YModem: Zeige Statistiken
                        elif os.path.isdir(filepath):
                            # YModem Batch - zeige Statistiken für alle Dateien
                            if "Batch complete:" in final_status:
                                # Parse Statistiken aus Status
                                parts = final_status.split(", ")
                                num_files = parts[0].split(": ")[1].split(" ")[0]
                                total_bytes = final_bytes
                                
                                # Berechne Geschwindigkeit
                                bytes_per_sec = total_bytes / duration if duration > 0 else 0
                                
                                # Formatiere Zeit
                                if duration < 60:
                                    time_str = f"{duration:.1f} seconds"
                                else:
                                    mins = int(duration // 60)
                                    secs = duration % 60
                                    time_str = f"{mins} minute{'s' if mins != 1 else ''}, {secs:.1f} seconds"
                                
                                # Formatiere Geschwindigkeit
                                if bytes_per_sec < 1024:
                                    speed_str = f"{bytes_per_sec:.0f} bytes/sec"
                                elif bytes_per_sec < 1024 * 1024:
                                    speed_str = f"{bytes_per_sec/1024:.1f} KB/sec"
                                else:
                                    speed_str = f"{bytes_per_sec/(1024*1024):.1f} MB/sec"
                                
                                messagebox.showinfo("Batch Download Complete", 
                                    f"Files: {num_files}\n"
                                    f"Saved to: {filepath}\n"
                                    f"Total Size: {total_bytes:,} bytes\n"
                                    f"Time: {time_str}\n"
                                    f"Speed: {speed_str}")
                            else:
                                messagebox.showinfo("Success", 
                                    f"Download complete!\nSaved to: {filepath}")
                    else:
                        # Transfer fehlgeschlagen - cleanup temp file
                        if temp_filepath and os.path.exists(temp_filepath):
                            try:
                                os.remove(temp_filepath)
                            except:
                                pass
                        messagebox.showerror("Error", "Download failed!")
            
            self.after(0, finish)
        
        threading.Thread(target=download_thread, daemon=True).start()
    
    def show_default_welcome(self):
        """Zeigt Default Welcome Screen wenn keine startup.seq vorhanden"""
        # PETSCII-Codes für einen einfachen Welcome Screen
        welcome = bytearray()
        
        # Clear screen
        welcome.append(0x93)
        
        # Cursor auf Position 0,0
        welcome.append(0x13)
        
        # Farbe: Hellblau
        welcome.append(0x9F)
        
        # Text zentriert
        lines = [
            "",
            "",
            "    PETSCII BBS TERMINAL v3.3",
            "",
            "           **** READY ****",
            "",
            "",
            "  F7 = BBS DIALER   F9 = AUTO-LOGIN   F12 = TRAFFIC LOG",
            "",
            "  F1 = UPLOAD       F3 = DOWNLOAD",
            "",
            "  F5 = SETTINGS     ALT+S = SCREENSHOT",
            "",
            "  ALT+P = CYCLE PROTOCOL",
            "",
            "  WAITING FOR CONNECTION...",
            "",
        ]
        
        for line in lines:
            # PETSCII String (ASCII + 0x80 für lowercase falls nötig)
            for char in line:
                if char.islower():
                    welcome.append(ord(char.upper()))
                else:
                    welcome.append(ord(char))
            # Return
            welcome.append(0x0D)
        
        # Parse und render
        self.parser.parse_bytes(welcome)
        
        # Setze Cursor-Position (wie bei startup.seq)
        # x=1 damit Cursor nicht ganz links ist
        self.screen.cursor_x = 1
        self.screen.cursor_y = 14  # Unter "WAITING FOR CONNECTION..."
        
        self.render_display()
    
    def show_settings(self):
        """F5 - Settings"""
        dialog = SettingsDialog(self, self.current_protocol, self.screen_width)
        self.wait_window(dialog)
        
        if dialog.result:
            # Protocol ändern
            old_protocol = self.current_protocol
            self.current_protocol = dialog.result['protocol']
            
            if old_protocol != self.current_protocol:
                debug_print(f"Protocol changed: {old_protocol.value} → {self.current_protocol.value}")
                
                # Speichere in globalem Config
                self.settings['transfer_protocol'] = self.current_protocol.value
                self.save_config()
                
                # Wenn mit BBS verbunden: Speichere auch im BBS-Config
                if self.connected and self.current_bbs_host:
                    # Finde BBS in Liste und update Protocol
                    for bbs in self.settings.get('bbs_list', []):
                        if (bbs.get('host') == self.current_bbs_host and 
                            bbs.get('port') == self.current_bbs_port):
                            bbs['protocol'] = self.current_protocol.value
                            self.save_config()
                            debug_print(f"Protocol saved to BBS config: {self.current_bbs_host}:{self.current_bbs_port}")
                            break
                
                # Update Statusbar wenn connected
                if self.connected:
                    self.update_status_connected(f"Protocol changed to {self.current_protocol.value}")
            
            # Upload/Download Ordner speichern
            if 'upload_folder' in dialog.result:
                self.settings['upload_folder'] = dialog.result['upload_folder']
                self.save_config()
                debug_print(f"Upload folder: {dialog.result['upload_folder']}")
            
            if 'download_folder' in dialog.result:
                self.settings['download_folder'] = dialog.result['download_folder']
                self.save_config()
                debug_print(f"Download folder: {dialog.result['download_folder']}")
            
            # Transfer Speed Profile speichern
            if 'transfer_speed' in dialog.result:
                old_speed = self.settings.get('transfer_speed', 'normal')
                new_speed = dialog.result['transfer_speed']
                if old_speed != new_speed:
                    self.settings['transfer_speed'] = new_speed
                    self.save_config()
                    debug_print(f"Transfer speed changed: {old_speed} → {new_speed}")
                    
                    # Wenn mit BBS verbunden: Speichere auch im BBS-Config
                    if self.connected and self.current_bbs_host:
                        for bbs in self.settings.get('bbs_list', []):
                            if (bbs.get('host') == self.current_bbs_host and 
                                bbs.get('port') == self.current_bbs_port):
                                bbs['transfer_speed'] = new_speed
                                self.save_config()
                                debug_print(f"Transfer speed saved to BBS config: {self.current_bbs_host}:{self.current_bbs_port}")
                                break
            
            # Z/Y Swap für Keyboard Layout speichern
            if 'swap_zy' in dialog.result:
                old_swap = self.settings.get('swap_zy', False)
                new_swap = dialog.result['swap_zy']
                if old_swap != new_swap:
                    self.settings['swap_zy'] = new_swap
                    self.save_config()
                    # Aktiviere/Deaktiviere Z/Y Swap im Keyboard Modul
                    from c64_keyboard import set_swap_zy
                    set_swap_zy(new_swap)
                    layout = "US (QWERTY)" if new_swap else "German (QWERTZ)"
                    debug_print(f"Keyboard layout changed: {layout}")
            
            # Punter Debug speichern
            if 'transfer_debug' in dialog.result:
                old_debug = self.settings.get('transfer_debug', False)
                new_debug = dialog.result['transfer_debug']
                if old_debug != new_debug:
                    self.settings['transfer_debug'] = new_debug
                    self.save_config()
                    # Update global debug flags
                    global _TERMINAL_DEBUG
                    _TERMINAL_DEBUG = new_debug
                    set_telnet_debug(new_debug)  # Also update telnet client debug
                    state = "enabled" if new_debug else "disabled"
                    print(f"Transfer debug mode {state}")
            
            # Width ändern - DYNAMISCH ohne Neustart!
            new_width = dialog.result['width']
            if new_width != self.screen_width:
                # Speichere in Config
                self.settings['screen_width'] = new_width
                self.save_config()
                
                # Wechsle Width dynamisch
                self.switch_column_mode(new_width)
    
    def show_scrollback(self):
        """F4 - Buffer Viewer"""
        ScrollbackViewer(self, self.scrollback, self.screen_width)
    
    def switch_column_mode(self, new_width):
        """Wechselt Column-Mode dynamisch ohne Neustart"""
        old_width = self.screen_width
        
        debug_print(f"Switching column mode: {old_width} → {new_width}")
        
        # Update Width
        self.screen_width = new_width
        
        # Erstelle neuen Screen Buffer mit neuer Width
        from petscii_parser import PETSCIIScreenBuffer, PETSCIIParser
        self.screen = PETSCIIScreenBuffer(self.screen_width, self.screen_height)
        self.parser = PETSCIIParser(self.screen)
        
        # Erstelle neuen Renderer mit neuer Width
        from c64_rom_renderer import AnimatedC64ROMFontRenderer
        self.renderer = AnimatedC64ROMFontRenderer(
            self.screen,
            font_upper_path="upper.bmp",
            font_lower_path="lower.bmp",
            zoom=self.current_zoom
        )
        
        # Update Column-Anzeige in Statusbar
        self.column_var.set(f"{new_width} COL")
        
        # Lade Startup Screen NUR wenn NICHT verbunden
        if not self.connected:
            self.load_startup_screen()
        else:
            # Bei aktiver Verbindung: Leeren Screen zeigen
            self.screen.clear_screen()
            debug_print("Column switch during active connection - screen cleared")
        
        # Render
        self.render_display()
        
        # Update Zoom für neue Width
        self.update_zoom()
        
        # Zeige Info
        if self.connected:
            messagebox.showinfo("Column Mode Changed", 
                f"Switched from {old_width} to {new_width} columns.\n"
                f"BBS connection active - screen cleared.\n"
                f"Continue session in {new_width} column mode.")
        else:
            messagebox.showinfo("Column Mode Changed", 
                f"Switched from {old_width} to {new_width} columns.\n"
                f"Screen buffer reset.")
        
        debug_print(f"Column mode switched successfully to {new_width}")
    
    def show_tools_menu(self):
        """F10 - Öffnet Tools Menu"""
        ToolsMenuDialog(self)
    
    def show_hotkey_editor(self):
        """Alt+H - Öffnet Hotkey Editor"""
        HotkeyEditorDialog(self)
    
    # ================================================================
    # SERVER MODE
    # ================================================================
    
    def start_server_mode(self):
        """Start Server Mode - ask for port and listen for incoming connections"""
        if self.server_mode:
            messagebox.showinfo("Server Mode", "Server is already running!", parent=self)
            return
        
        if self.connected:
            messagebox.showwarning("Server Mode", 
                "Please disconnect from the current BBS first.", parent=self)
            return
        
        # Ask for listen port
        dialog = ServerPortDialog(self, self.server_port)
        self.wait_window(dialog)
        
        if dialog.result is None:
            return
        
        self.server_port = dialog.result
        
        # Start listening
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.settimeout(1.0)  # Timeout for clean shutdown
            self.server_socket.bind(('0.0.0.0', self.server_port))
            self.server_socket.listen(1)
        except OSError as e:
            messagebox.showerror("Server Mode", 
                f"Cannot bind to port {self.server_port}:\n{e}", parent=self)
            self.server_socket = None
            return
        
        self.server_mode = True
        
        # Update menu state
        self.server_menu.entryconfig("Start Server Mode...", state=tk.DISABLED)
        self.server_menu.entryconfig("Stop Server Mode", state=tk.NORMAL)
        
        self.status_var.set(f"Server Mode | Listening on port {self.server_port} ...")
        debug_print(f"[SERVER] Listening on port {self.server_port}")
        
        # Show message on terminal screen (UC/LC switched)
        # $0E = switch to lowercase mode, $0D = carriage return
        # PETSCII LC mode has swapped case: upper<->lower
        msg = f"\x0esERVER mODE ACTIVATED\rLISTENING ON PORT {self.server_port} ...\r\r"
        self.parser.parse_bytes(msg.encode('latin-1'))
        
        # Start accept thread
        self.server_thread = threading.Thread(target=self._server_accept_loop, daemon=True)
        self.server_thread.start()
    
    def stop_server_mode(self):
        """Stop Server Mode"""
        if not self.server_mode:
            return
        
        self.server_mode = False
        
        # Close server socket (will unblock accept)
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
            self.server_socket = None
        
        # Disconnect any active server connection
        if self.connected:
            self.disconnect()
        
        # Update menu state
        self.server_menu.entryconfig("Start Server Mode...", state=tk.NORMAL)
        self.server_menu.entryconfig("Stop Server Mode", state=tk.DISABLED)
        
        self.status_var.set("Server Mode stopped")
        debug_print("[SERVER] Server mode stopped")
    
    def _server_accept_loop(self):
        """Background thread: wait for incoming connections"""
        while self.server_mode and self.server_socket:
            try:
                client_sock, client_addr = self.server_socket.accept()
                debug_print(f"[SERVER] Connection from {client_addr[0]}:{client_addr[1]}")
                
                # Hand off to main thread via after()
                self.after(0, lambda s=client_sock, a=client_addr: self._server_on_connect(s, a))
                
                # Wait until this connection ends before accepting another
                while self.server_mode and self.connected:
                    time.sleep(0.2)
                
                # Back to listening
                if self.server_mode:
                    self.after(0, lambda: self.status_var.set(
                        f"Server Mode | Listening on port {self.server_port} ..."))
                
            except socket.timeout:
                continue
            except OSError:
                break  # Socket closed
        
        debug_print("[SERVER] Accept loop ended")
    
    def _server_on_connect(self, client_sock, client_addr):
        """Called on main thread when a client connects"""
        if self.connected:
            # Already connected, reject
            try:
                client_sock.close()
            except Exception:
                pass
            return
        
        try:
            # Wrap the raw socket in a ServerClientAdapter that mimics
            # the interface expected by update_loop (has_received_data, etc.)
            adapter = ServerClientAdapter(client_sock)
            
            # Create a lightweight wrapper that looks like BBSConnection
            self.bbs_connection = ServerConnectionWrapper(adapter)
            self.connected = True
            
            self.current_bbs_host = client_addr[0]
            self.current_bbs_port = client_addr[1]
            
            self.status_var.set(
                f"Server Mode | Client connected: {client_addr[0]}:{client_addr[1]}")
            debug_print(f"[SERVER] Client active: {client_addr[0]}:{client_addr[1]}")
            
            # Send welcome message with UC/LC switch ($0E)
            # PETSCII LC mode has swapped case: upper<->lower
            welcome = f"\x0ewELCOME TO pycgms v{PYCGMS_VERSION}\r\n"
            adapter.send_raw(welcome.encode('latin-1'))
            debug_print(f"[SERVER] Sent welcome: Welcome to PYCGMS V{PYCGMS_VERSION}")
            
        except Exception as e:
            debug_print(f"[SERVER] Error accepting client: {e}")
            try:
                client_sock.close()
            except Exception:
                pass


    def update_status(self, text):
        """Aktualisiert Statusbar"""
        self.status_var.set(text)
    
    def update_status_connected(self, extra_info=""):
        """Aktualisiert Statusbar für Connected State mit Protocol und Speed"""
        host = self.current_bbs_host
        port = self.current_bbs_port
        protocol = self.current_protocol.value
        speed = self.settings.get('transfer_speed', 'normal')
        
        if extra_info:
            self.status_var.set(f"Connected to {host}:{port} | {extra_info} | Protocol: {protocol} | Speed: {speed}")
        else:
            self.status_var.set(f"Connected to {host}:{port} | Protocol: {protocol} | Speed: {speed}")
    
    def toggle_charset(self):
        """F8 - Togglet zwischen UPPER/LOWER Charset"""
        if self.screen.charset_mode == 'upper':
            self.screen.charset_mode = 'lower'
            self.renderer.charset = 'lower'
            debug_print("Charset: LOWERCASE")
        else:
            self.screen.charset_mode = 'upper'
            self.renderer.charset = 'upper'
            debug_print("Charset: UPPERCASE")
        
        # Neu rendern
        self.render_display()
    
    def toggle_fullscreen(self):
        """F6 - Toggle Fullscreen Mode"""
        self.fullscreen = not self.fullscreen
        self.attributes("-fullscreen", self.fullscreen)
        
        if self.fullscreen:
            debug_print("Fullscreen: ON (ESC or F6 to exit)")
            # Update Statusbar
            self.update_status("FULLSCREEN MODE | ESC=Exit | F6=Toggle")
        else:
            debug_print("Fullscreen: OFF")
            # Restore original status
            if self.connected:
                self.update_status_connected()
            else:
                self.update_status("Not connected | F7=Dial F9=Login F1=Upload F3=Download F5=Settings")
        
        # Zoom neu berechnen nach Fullscreen-Wechsel
        self.after(100, self.update_zoom)
    
    def exit_fullscreen(self):
        """ESC - Verlässt Fullscreen-Modus"""
        if self.fullscreen:
            self.fullscreen = False
            self.attributes("-fullscreen", False)
            debug_print("Fullscreen: OFF")
            # Restore original status
            if self.connected:
                self.update_status_connected()
            else:
                self.update_status("Not connected | F7=Dial F9=Login F1=Upload F3=Download F5=Settings")
            self.after(100, self.update_zoom)
    
    def cycle_protocol(self):
        """Alt+P - Wechselt zum nächsten Transfer-Protokoll (zyklisch)"""
        from file_transfer import TransferProtocol
        
        # Definiere die Protokoll-Reihenfolge (gleiche Reihenfolge wie in Settings)
        protocol_order = [
            TransferProtocol.RAWTCP,       # 🚀 MAX SPEED (LAN)
            TransferProtocol.TURBOMODEM,   # ⚡ ULTRA FAST!
            TransferProtocol.PUNTER,       # 📦 Multi-File
            TransferProtocol.XMODEM_1K,
            TransferProtocol.XMODEM_CRC,
            TransferProtocol.XMODEM,
            TransferProtocol.YMODEM
        ]
        
        # Finde aktuelles Protokoll in der Liste
        try:
            current_index = protocol_order.index(self.current_protocol)
        except ValueError:
            current_index = 0
        
        # Wechsle zum nächsten (zyklisch)
        next_index = (current_index + 1) % len(protocol_order)
        self.current_protocol = protocol_order[next_index]
        
        # Speichere in Config
        self.settings['transfer_protocol'] = self.current_protocol.value
        self.save_config()
        
        # Status aktualisieren
        debug_print(f"Protocol changed to {self.current_protocol.value}")
        
        if self.connected:
            self.update_status_connected()
        else:
            self.update_status(f"Protocol: {self.current_protocol.value} | F7=Dial F9=Login F1=Upload F3=Download")
    
    def take_screenshot(self):
        """Alt+S - Macht Screenshot vom BBS Screen (384x272 PNG)"""
        try:
            from PIL import Image
            from tkinter import filedialog
            
            # Rendere aktuellen Screen
            pil_image = self.renderer.render()
            
            # Skaliere auf exakt 384x272
            screenshot = pil_image.resize((384, 272), Image.Resampling.LANCZOS)
            
            # Öffne Save-Dialog
            filename = filedialog.asksaveasfilename(
                title="Save Screenshot",
                initialdir=os.path.dirname(os.path.abspath(__file__)),
                defaultextension=".png",
                filetypes=[("PNG files", "*.png"), ("All files", "*.*")],
                initialfile=f"screenshot_{int(time.time())}.png"
            )
            
            if filename:
                screenshot.save(filename, 'PNG')
                debug_print(f"Screenshot saved: {filename}")
                
                # Kurze Bestätigung in Statusbar
                if self.connected:
                    self.update_status_connected(f"📸 Screenshot saved!")
                else:
                    self.update_status(f"📸 Screenshot saved: {os.path.basename(filename)}")
                
                # Nach 3 Sekunden Status zurücksetzen
                self.after(3000, lambda: self.update_status_connected() if self.connected else 
                          self.update_status("Not connected | F7=Dial F9=Login F1=Upload F3=Download F5=Settings"))
        except Exception as e:
            debug_print(f"Screenshot error: {e}")
            messagebox.showerror("Screenshot Error", f"Could not save screenshot:\n{e}")
    
    def toggle_traffic_logger(self):
        """F12 - Toggle Traffic Logger (loggt allen ein/ausgehenden Traffic)"""
        import datetime
        import os
        
        if not self.traffic_logger_active:
            # STARTE Logger
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"traffic_log_{timestamp}.txt"
            
            try:
                # Öffne Datei zum Schreiben
                self.traffic_log_file = open(filename, 'w', encoding='utf-8', buffering=1)  # Line buffered
                self.traffic_log_count = 0
                self._log_error_count = 0
                self.traffic_logger_active = True
                
                # Header schreiben
                self.traffic_log_file.write("="*70 + "\n")
                self.traffic_log_file.write(f"TRAFFIC LOG STARTED: {datetime.datetime.now()}\n")
                self.traffic_log_file.write("="*70 + "\n")
                self.traffic_log_file.write("Format: [Timestamp] DIRECTION | HEX | ASCII\n")
                self.traffic_log_file.write("  SEND → = Outgoing (Client → BBS)\n")
                self.traffic_log_file.write("  RECV ← = Incoming (BBS → Client)\n")
                self.traffic_log_file.write("="*70 + "\n\n")
                self.traffic_log_file.flush()
                
                # Hole absoluten Pfad
                abs_path = os.path.abspath(filename)
                
                debug_print(f"\n{'='*70}")
                debug_print(f"✓ TRAFFIC LOGGER STARTED")
                debug_print(f"  File: {abs_path}")
                debug_print(f"  Press F12 again to stop logging")
                print(f"{'='*70}\n")
                
                self.update_status_connected(f"📝 LOGGING to {filename}")
                
                # Test-Write
                self.traffic_log_file.write(f"[TEST] Logger initialized successfully\n\n")
                self.traffic_log_file.flush()
                
            except Exception as e:
                print(f"✗ Failed to start traffic logger: {e}")
                import traceback
                traceback.print_exc()
                
                self.traffic_logger_active = False
                if self.traffic_log_file:
                    try:
                        self.traffic_log_file.close()
                    except:
                        pass
                    self.traffic_log_file = None
        
        else:
            # STOPPE Logger
            if self.traffic_log_file:
                try:
                    # Footer schreiben
                    self.traffic_log_file.write("\n" + "="*70 + "\n")
                    self.traffic_log_file.write(f"TRAFFIC LOG STOPPED: {datetime.datetime.now()}\n")
                    self.traffic_log_file.write(f"Total packets logged: {self.traffic_log_count}\n")
                    self.traffic_log_file.write("="*70 + "\n")
                    self.traffic_log_file.flush()
                    
                    filename = self.traffic_log_file.name
                    abs_path = os.path.abspath(filename)
                    
                    self.traffic_log_file.close()
                    
                    debug_print(f"\n{'='*70}")
                    debug_print(f"✓ TRAFFIC LOGGER STOPPED")
                    debug_print(f"  File: {abs_path}")
                    debug_print(f"  Packets logged: {self.traffic_log_count}")
                    print(f"{'='*70}\n")
                    
                except Exception as e:
                    print(f"⚠ Error closing log file: {e}")
                
                self.traffic_log_file = None
            
            self.traffic_logger_active = False
            self.traffic_log_count = 0
            self.update_status_connected("Traffic logging stopped")
    
    def log_traffic(self, direction, data):
        """Loggt Traffic wenn Logger aktiv ist
        
        Args:
            direction: "SEND" oder "RECV"
            data: bytes, int, oder str
        """
        # Quick exit wenn Logger nicht aktiv
        if not self.traffic_logger_active or not self.traffic_log_file:
            return
        
        if not data:
            return
        
        import datetime
        
        try:
            timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            
            # Konvertiere zu bytes falls nötig
            if isinstance(data, int):
                data = bytes([data])
            elif isinstance(data, str):
                data = data.encode('latin-1', errors='replace')
            elif not isinstance(data, bytes):
                # Unbekannter Typ
                print(f"⚠ log_traffic: Unknown data type {type(data)}")
                return
            
            # Limitiere Hex-Ausgabe auf max 80 Bytes pro Zeile
            # Bei großen Transfers sonst zu viel Output
            if len(data) > 80:
                hex_str = ' '.join(f'{b:02X}' for b in data[:80]) + f' ... ({len(data)-80} more bytes)'
                ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[:80]) + '...'
            else:
                hex_str = ' '.join(f'{b:02X}' for b in data)
                ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data)
            
            # Schreibe Log Entry
            arrow = "→" if direction == "SEND" else "←"
            self.traffic_log_file.write(f"[{timestamp}] {direction} {arrow} | {hex_str}\n")
            self.traffic_log_file.write(f"{'':17} ASCII | {ascii_str}\n")
            self.traffic_log_file.write(f"{'':17} LEN   | {len(data)} bytes\n\n")
            
            # WICHTIG: Flush nach jedem Write damit Daten sofort sichtbar sind
            self.traffic_log_file.flush()
            
            self.traffic_log_count += 1
            
            # Update Statusbar alle 100 Pakete
            if self.traffic_log_count % 100 == 0:
                self.update_status_connected(f"📝 Logged {self.traffic_log_count} packets...")
            
        except Exception as e:
            print(f"⚠ Traffic log write error: {e}")
            import traceback
            traceback.print_exc()
            # Deaktiviere Logger bei wiederholten Fehlern
            if not hasattr(self, '_log_error_count'):
                self._log_error_count = 0
            self._log_error_count += 1
            if self._log_error_count > 10:
                print(f"✗ Too many log errors, disabling traffic logger")
                self.traffic_logger_active = False
    
    def send_auto_login(self):
        """F9 - Sends Username and Password automatically with delay"""
        if not self.connected or not self.bbs_connection:
            messagebox.showwarning("Not Connected", "Please connect to BBS first!")
            return
        
        if not self.current_bbs_username and not self.current_bbs_password:
            messagebox.showinfo("No Login Data", 
                "No login data saved.\n" +
                "Please enter Username/Password in Dialer (F7).")
            return
        
        # Hole aktuellen Host für Statusbar
        current_host = ""
        if self.bbs_connection and hasattr(self.bbs_connection, 'config'):
            current_host = f"{self.bbs_connection.config['host']}:{self.bbs_connection.config['port']}"
        
        # DEBUG: Zeige was gesendet wird
        debug_print(f"\n{'='*60}")
        debug_print(f"AUTO-LOGIN DEBUG")
        debug_print(f"{'='*60}")
        debug_print(f"Username (raw): {repr(self.current_bbs_username)}")
        debug_print(f"Password (raw): {repr(self.current_bbs_password)}")
        debug_print(f"Delay: {self.current_bbs_delay}ms")
        
        # WICHTIG: Sende Zeichen mit kleinem Delay (10ms)
        # Manche BBS haben Buffer-Probleme bei zu schnellem Input
        def send_string_slowly(text, final_callback=None, debug_label=""):
            """Sendet String Zeichen für Zeichen mit 10ms Delay"""
            if not text:
                if final_callback:
                    final_callback()
                return
            
            # Konvertiere String zu PETSCII Bytes
            # Wichtig: Nicht einfach Latin-1 encoding, sondern echte PETSCII-Konvertierung!
            from c64_keyboard import get_petscii_for_key
            
            text_bytes = []
            for char in text:
                # Hole PETSCII Code für dieses Zeichen
                # (shift=False, ctrl=False für normale Zeichen)
                petscii = get_petscii_for_key(char, char, False, False, False)
                
                if petscii is not None:
                    text_bytes.append(petscii)
                else:
                    # Fallback: Nutze ASCII-Wert direkt (für Zahlen/Buchstaben meist OK)
                    text_bytes.append(ord(char) if ord(char) < 128 else ord('?'))
            
            debug_print(f"{debug_label} converted to PETSCII:")
            debug_print(f"  Text: {repr(text)}")
            debug_print(f"  PETSCII Bytes: {' '.join(f'{b:02X}' for b in text_bytes)}")
            debug_print(f"  ASCII equiv:   {' '.join(chr(b) if 32 <= b < 127 else '.' for b in text_bytes)}")
            
            index = 0
            def send_next_char():
                nonlocal index
                if index < len(text_bytes):
                    # Sende ein Zeichen
                    byte_val = text_bytes[index]
                    debug_print(f"  [{index}] Sending: 0x{byte_val:02X} ('{text[index]}' → PETSCII)")
                    
                    # Log outgoing traffic
                    self.log_traffic("SEND", byte_val)
                    
                    self.bbs_connection.send_key(byte_val)
                    index += 1
                    # Nächstes Zeichen nach 10ms
                    self.after(10, send_next_char)
                else:
                    # Fertig - sende RETURN (PETSCII 0x0D = ASCII CR)
                    debug_print(f"  Sending RETURN: 0x0D")
                    
                    # Log outgoing traffic
                    self.log_traffic("SEND", 0x0D)
                    
                    self.bbs_connection.send_key(0x0D)
                    if final_callback:
                        self.after(10, final_callback)
            
            send_next_char()
        
        # SCHRITT 1: Sende Username
        if self.current_bbs_username:
            debug_print(f"\nSending Username...")
            self.update_status_connected(f"Sending username...")
            
            def after_username():
                debug_print(f"\n✓ Username sent")
                debug_print(f"Waiting {self.current_bbs_delay}ms for password prompt...")
                self.update_status_connected(f"Username sent, waiting {self.current_bbs_delay}ms...")
                
                # SCHRITT 2: Warte, dann sende Password
                if self.current_bbs_password:
                    def send_password():
                        debug_print(f"\nSending Password...")
                        self.update_status_connected("Sending password...")
                        
                        def after_password():
                            debug_print(f"\n✓ Password sent")
                            print(f"{'='*60}\n")
                            self.update_status_connected("Login complete")
                        
                        send_string_slowly(self.current_bbs_password, after_password, "Password")
                    
                    # Verzögere Password um X ms
                    self.after(self.current_bbs_delay, send_password)
                else:
                    debug_print(f"\n✓ Username sent (no password configured)")
                    print(f"{'='*60}\n")
                    self.update_status_connected("Username sent (no password)")
            
            send_string_slowly(self.current_bbs_username, after_username, "Username")
        else:
            # Nur Password (kein Username)
            if self.current_bbs_password:
                debug_print(f"\nSending Password only (no username)...")
                
                def after_password():
                    debug_print(f"\n✓ Password sent")
                    print(f"{'='*60}\n")
                    self.update_status_connected("Password sent")
                
                send_string_slowly(self.current_bbs_password, after_password, "Password")
    
    def load_config(self):
        """Lädt Config aus bbs_config.json"""
        try:
            if os.path.exists('bbs_config.json'):
                with open('bbs_config.json', 'r') as f:
                    config = json.load(f)
                    # Defaults für fehlende Keys
                    config.setdefault('screen_width', 40)
                    config.setdefault('transfer_debug', False)
                    return config
        except:
            pass
        return {
            'screen_width': 40,
            'default_host': 'the-hidden.hopto.org',
            'default_port': 64128,
            'transfer_debug': False
        }
    
    def save_config(self):
        """Speichert Config"""
        try:
            with open('bbs_config.json', 'w') as f:
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            print(f"Config save error: {e}")
    
    def on_canvas_resize(self, event):
        """Wird aufgerufen wenn Canvas größe sich ändert"""
        # Throttle - nur wenn Größe wirklich geändert
        if event.width == self.last_canvas_width and event.height == self.last_canvas_height:
            return
        
        self.last_canvas_width = event.width
        self.last_canvas_height = event.height
        
        # Throttle - warte 100ms nach letztem Event
        if self.resize_pending:
            return
        
        self.resize_pending = True
        
        def do_resize():
            self.resize_pending = False
            # Nutze event.width/height direkt
            self.update_zoom(event.width, event.height)
        
        self.after(100, do_resize)
    
    def update_zoom(self, force_width=None, force_height=None):
        """Berechnet und setzt optimalen Zoom"""
        try:
            # Canvas Größe holen
            if force_width and force_height:
                canvas_width = force_width
                canvas_height = force_height
            else:
                self.canvas.update_idletasks()
                canvas_width = self.canvas.winfo_width()
                canvas_height = self.canvas.winfo_height()
            
            # Mindestgröße prüfen
            if canvas_width <= 1 or canvas_height <= 1:
                # Noch nicht initialisiert - versuche Window-Größe
                canvas_width = self.winfo_width()
                # Abzug für Menubar (~25px) + Statusbar (~25px) = ~50px
                canvas_height = self.winfo_height() - 50
            
            if canvas_width < 100 or canvas_height < 100:
                return
            
            # C64 char size: 8x8 pixels
            char_width = 8
            char_height = 8
            
            # Berechne maximalen Zoom der reinpasst
            # Nutze GANZE Canvas-Größe (100%)
            zoom_x = canvas_width // (self.screen_width * char_width)
            zoom_y = canvas_height // (self.screen_height * char_height)
            
            # Nimm kleineren Wert, mindestens 1, maximal 6
            new_zoom = max(1, min(6, min(zoom_x, zoom_y)))
            
            # Berechne tatsächliche Display-Größe
            display_width = self.screen_width * char_width * new_zoom
            display_height = self.screen_height * char_height * new_zoom
            
            # Nur loggen wenn Zoom sich ändert
            if new_zoom != self.current_zoom:
                debug_print(f"Zoom: {new_zoom}x ({display_width}x{display_height})")
            
            # IMMER Zoom setzen und neu rendern (auch wenn gleich)
            # weil Canvas-Größe sich geändert hat
            self.current_zoom = new_zoom
            self.renderer.zoom = new_zoom
            
            # Force immediate re-render um Zentrierung zu aktualisieren
            self.render_display()
            
        except Exception as e:
            print(f"Zoom update error: {e}")
            import traceback
            traceback.print_exc()
    
    def preload_fonts(self):
        """Lädt häufige Zoom-Levels im Hintergrund vor"""
        def do_preload():
            try:
                # Preload Zoom 1-6 (alle möglichen Levels)
                self.renderer.preload_common_zooms([1, 2, 3, 4, 5, 6])
            except Exception as e:
                print(f"Font preload error: {e}")
        
        # In separatem Thread ausführen um UI nicht zu blockieren
        import threading
        threading.Thread(target=do_preload, daemon=True).start()
    
    def load_startup_screen(self):
        """Lädt und rendert Startup Screen (.seq Datei)"""
        # WICHTIG: Leere Screen ZUERST (vor Dateisuche)
        self.screen.clear_screen()
        
        # Suche zuerst nach width-spezifischen Startup-Screens
        if self.screen_width == 80:
            startup_files = ['startup_80.seq', 'welcome_80.seq', 'startup.seq', 'welcome.seq', 'ccgms.seq']
        else:  # 40 columns
            startup_files = ['startup_40.seq', 'startup.seq', 'welcome.seq', 'ccgms.seq']
        
        for filename in startup_files:
            if os.path.exists(filename):
                try:
                    debug_print(f"Loading startup screen: {filename} ({self.screen_width} columns)")
                    
                    # Lade SEQ-Datei
                    with open(filename, 'rb') as f:
                        seq_data = f.read()
                    
                    # Prüfe ob Datei mit CLR (0x93) oder HOME (0x13) startet
                    # Wenn nicht: Füge CLR + HOME am Anfang hinzu
                    if len(seq_data) >= 2:
                        has_clr = (seq_data[0] == 0x93)
                        has_home = (seq_data[0] == 0x13 or (len(seq_data) > 1 and seq_data[1] == 0x13))
                        
                        if not has_clr:
                            debug_print(f"  → Adding CLR + HOME to {filename}")
                            # Füge CLR (0x93) + HOME (0x13) am Anfang hinzu
                            seq_data = bytes([0x93, 0x13]) + seq_data
                    
                    # Parse SEQ-Daten (Screen ist schon geleert)
                    self.parser.parse_bytes(seq_data)
                    
                    # Setze Cursor auf Zeile 13, Position 3 (0-basiert: y=12, x=2)
                    self.screen.cursor_x = 2
                    self.screen.cursor_y = 12
                    
                    # Render
                    self.render_display()
                    
                    debug_print(f"Startup screen loaded: {filename}")
                    return
                    
                except Exception as e:
                    print(f"Error loading {filename}: {e}")
                    import traceback
                    traceback.print_exc()
        
        # Kein Startup Screen gefunden - zeige Default
        self.show_default_welcome()
    
    def load_hotkeys(self):
        """
        Lädt Hotkeys aus hotkeys.seq Datei
        
        Format: Bis zu 10 Zeilen, jede Zeile = eine Hotkey
        Zeile 1 = Ctrl+Alt+F1 (AltGr+F1)
        Zeile 2 = Ctrl+Alt+F2 (AltGr+F2)
        ...
        Zeile 10 = Ctrl+Alt+F10 (AltGr+F10)
        
        WICHTIG: Datei wird als BINÄR geladen (PETSCII Grafik + Farbcodes!)
        Jede Zeile wird automatisch mit RETURN (0x0D) abgeschlossen.
        """
        hotkey_file = "hotkeys.seq"
        
        if not os.path.exists(hotkey_file):
            debug_print(f"No hotkey file found: {hotkey_file}")
            return
        
        try:
            # WICHTIG: Lade als BINÄR (nicht Text!)
            # PETSCII Grafik und Farbcodes würden bei Latin-1 Text-Decode kaputt gehen!
            with open(hotkey_file, 'rb') as f:
                file_data = f.read()
            
            # Splitte an CR (0x0D) oder LF (0x0A)
            lines = []
            current_line = bytearray()
            
            for byte in file_data:
                if byte == 0x0D or byte == 0x0A:
                    # Zeilen-Ende
                    if len(current_line) > 0:
                        lines.append(bytes(current_line))
                        current_line = bytearray()
                else:
                    current_line.append(byte)
            
            # Letzte Zeile (falls keine CR/LF am Ende)
            if len(current_line) > 0:
                lines.append(bytes(current_line))
            
            # Lade bis zu 10 Zeilen
            for i, line_bytes in enumerate(lines[:10], start=1):
                if line_bytes:
                    # Füge RETURN (0x0D) hinzu
                    line_data = line_bytes + b'\r'
                    
                    self.hotkeys[i] = line_data
                    
                    # Zeige Hex-Vorschau (erste 20 Bytes)
                    hex_preview = ' '.join(f'{b:02X}' for b in line_bytes[:20])
                    if len(line_bytes) > 20:
                        hex_preview += '...'
                    debug_print(f"Hotkey F{i}: {hex_preview} ({len(line_data)} bytes)")
            
            debug_print(f"Loaded {len(self.hotkeys)} hotkeys from {hotkey_file}")
            
        except Exception as e:
            print(f"Error loading hotkeys: {e}")
            import traceback
            traceback.print_exc()
    
    def load_terminal_keymap(self):
        """
        Lädt Keyboard Mapping aus terminal.map
        
        Format: PC_KEY = PETSCII_CODE
        - Hex: $XX oder 0xXX
        - Dezimal: NNN
        - Sequenz: $XX,$YY,$ZZ (mehrere Bytes)
        - Modifier: Control+KEY, Shift+KEY, Alt+KEY
        """
        map_file = "terminal.map"
        self.terminal_keymap = {}
        
        if not os.path.exists(map_file):
            print(f"No terminal keymap found: {map_file} (using defaults)")
            return
        
        try:
            with open(map_file, 'r', encoding='latin-1') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    
                    # Kommentare und Leerzeilen überspringen
                    if not line or line.startswith('#'):
                        continue
                    
                    # Format: KEY = VALUE
                    if '=' not in line:
                        continue
                    
                    parts = line.split('=', 1)
                    if len(parts) != 2:
                        continue
                    
                    key_part = parts[0].strip()
                    value_part = parts[1].split('#')[0].strip()  # Kommentar entfernen
                    
                    if not key_part or not value_part:
                        continue
                    
                    # Parse Value (PETSCII Code(s))
                    try:
                        petscii_bytes = self._parse_petscii_value(value_part)
                        if petscii_bytes:
                            self.terminal_keymap[key_part] = petscii_bytes
                    except Exception as e:
                        print(f"Warning: terminal.map line {line_num}: {e}")
            
            debug_print(f"Loaded {len(self.terminal_keymap)} key mappings from {map_file}")
            
        except Exception as e:
            print(f"Error loading terminal keymap: {e}")
    
    def _parse_petscii_value(self, value):
        """
        Parst PETSCII Wert aus String
        
        Formate:
        - $XX oder 0xXX (Hex)
        - NNN (Dezimal)
        - $XX,$YY,$ZZ (Sequenz)
        - {NAME} (Spezial-Befehle)
        """
        result = []
        
        # Spezial-Befehle
        if value.startswith('{') and value.endswith('}'):
            cmd = value[1:-1].upper()
            special_codes = {
                'RESTORE': [0x00],  # NMI (speziell behandelt)
                'BREAK': [0x03],
                'NUL': [0x00],
                'CLR': [0x93],
                'HOME': [0x13],
                'RVS_ON': [0x12],
                'RVS_OFF': [0x92],
            }
            return special_codes.get(cmd, [])
        
        # Sequenz (kommagetrennt)
        if ',' in value:
            parts = value.split(',')
            for part in parts:
                part = part.strip()
                byte_val = self._parse_single_byte(part)
                if byte_val is not None:
                    result.append(byte_val)
            return result
        
        # Einzelner Wert
        byte_val = self._parse_single_byte(value)
        if byte_val is not None:
            return [byte_val]
        
        return []
    
    def _parse_single_byte(self, value):
        """Parst einen einzelnen Byte-Wert"""
        value = value.strip()
        
        try:
            if value.startswith('$'):
                return int(value[1:], 16)
            elif value.startswith('0x') or value.startswith('0X'):
                return int(value, 16)
            else:
                return int(value)
        except ValueError:
            return None
    
    def get_mapped_key(self, keysym, shift, ctrl, alt):
        """
        Sucht Key in terminal.map
        
        Returns:
            List of PETSCII bytes oder None wenn nicht gefunden
        """
        # Baue Key-String mit Modifiern
        modifiers = []
        if ctrl:
            modifiers.append('Control')
        if shift:
            modifiers.append('Shift')
        if alt:
            modifiers.append('Alt')
        
        # Versuche mit allen Modifiern
        if modifiers:
            full_key = '+'.join(modifiers) + '+' + keysym
            if full_key in self.terminal_keymap:
                return self.terminal_keymap[full_key]
        
        # Versuche nur mit Keysym
        if keysym in self.terminal_keymap:
            return self.terminal_keymap[keysym]
        
        return None
    
    def connect_bbs(self, host, port, username="", password="", send_delay=100, protocol=None, transfer_speed=None):
        """Verbindet mit BBS"""
        try:
            config = {
                'host': host,
                'port': port,
                'encoding': 'petscii'
            }
            
            # Speichere Login-Daten für F9
            self.current_bbs_username = username
            self.current_bbs_password = password
            self.current_bbs_delay = send_delay
            self.current_bbs_host = host  # Speichere Host
            self.current_bbs_port = port  # Speichere Port
            
            # Setze Protocol wenn angegeben
            if protocol:
                # Konvertiere String zu TransferProtocol Enum
                for proto in TransferProtocol:
                    if proto.value == protocol:
                        self.current_protocol = proto
                        debug_print(f"Protocol für {host}: {self.current_protocol.value}")
                        break
            
            # Setze Transfer Speed wenn angegeben
            if transfer_speed:
                self.settings['transfer_speed'] = transfer_speed
                debug_print(f"Transfer Speed für {host}: {transfer_speed}")
            
            self.bbs_connection = BBSConnection(config, self.parser)
            if self.bbs_connection.connect():
                self.connected = True
                
                # Sende Client-Identifikation an Server
                try:
                    time.sleep(0.1)  # Kurz warten bis Verbindung stabil
                    self.bbs_connection.send_raw(f"PYCGMS {PYCGMS_VERSION}\r\n".encode())
                    debug_print(f"[Connect] Sent PYCGMS client identification")
                except Exception as e:
                    debug_print(f"[Connect] Could not send PYCGMS: {e}")
                
                login_info = f" (Login: {username})" if username else ""
                protocol_info = f" | Protocol: {self.current_protocol.value}"
                speed_info = f" | Speed: {self.settings.get('transfer_speed', 'normal')}"
                self.status_var.set(f"Connected to {host}:{port}{login_info}{protocol_info}{speed_info}")
            else:
                messagebox.showerror("Error", "Connection failed!")
        except Exception as e:
            messagebox.showerror("Error", str(e))
    
    def disconnect(self):
        """Disconnect from BBS"""
        if self.bbs_connection:
            self.bbs_connection.disconnect()
        self.connected = False
        if self.server_mode:
            self.status_var.set(f"Server Mode | Listening on port {self.server_port} ...")
        else:
            self.status_var.set("Disconnected")
    
    def render_display(self):
        """Rendert und zeigt das Display mit Cursor"""
        try:
            # Rendering
            pil_image = self.renderer.render()
            
            # Konvertiere PIL.Image zu PhotoImage
            self.photo = ImageTk.PhotoImage(pil_image)
            
            # Hole aktuelle Canvas-Größe
            canvas_width = self.last_canvas_width if self.last_canvas_width > 0 else self.canvas.winfo_width()
            canvas_height = self.last_canvas_height if self.last_canvas_height > 0 else self.canvas.winfo_height()
            
            img_width = pil_image.width
            img_height = pil_image.height
            
            # Berechne Position zum Zentrieren
            x = max(0, (canvas_width - img_width) // 2)
            y = max(0, (canvas_height - img_height) // 2)
            
            self.canvas.delete("all")
            self.canvas.create_image(x, y, anchor=tk.NW, image=self.photo)
            
            # Zeichne Cursor an aktueller Position
            self.draw_terminal_cursor(x, y)
            
        except Exception as e:
            print(f"Render error: {e}")
    
    def draw_terminal_cursor(self, offset_x, offset_y):
        """Zeichnet ausgefüllten Cursor im Terminal (schwarz bei Transfer)"""
        # Hole Cursor-Position vom Screen
        cursor_x = self.screen.cursor_x
        cursor_y = self.screen.cursor_y
        
        # Berechne Pixel-Position (8x8 chars * zoom)
        zoom = self.renderer.zoom
        char_width = 8 * zoom
        char_height = 8 * zoom
        
        x = offset_x + (cursor_x * char_width)
        y = offset_y + (cursor_y * char_height)
        
        # Bei Transfer: Cursor schwarz (unsichtbar)
        if self.transfer_active:
            cursor_color = 'black'
        else:
            # Sonst: Aktuelle Vordergrundfarbe
            color_map = {
                0: '#000000',  # BLACK
                1: '#FFFFFF',  # WHITE
                2: '#880000',  # RED
                3: '#AAFFEE',  # CYAN
                4: '#CC44CC',  # PURPLE
                5: '#00CC55',  # GREEN
                6: '#0000AA',  # BLUE
                7: '#EEEE77',  # YELLOW
                8: '#DD8855',  # ORANGE
                9: '#664400',  # BROWN
                10: '#FF7777', # LIGHT_RED
                11: '#333333', # DARK_GRAY
                12: '#777777', # GRAY
                13: '#AAFF66', # LIGHT_GREEN
                14: '#0088FF', # LIGHT_BLUE
                15: '#BBBBBB', # LIGHT_GRAY
            }
            
            try:
                fg_color = self.screen.current_fg  # current_fg nicht fg_color!
                cursor_color = color_map.get(fg_color, '#FFFFFF')
            except:
                cursor_color = '#FFFFFF'
        
        # Zeichne AUSGEFÜLLTEN Cursor (wenn sichtbar)
        if hasattr(self, 'cursor_visible') and self.cursor_visible:
            self.canvas.create_rectangle(
                x, y, x + char_width, y + char_height,
                fill=cursor_color,
                outline='',  # No outline
                tags='cursor'
            )
    
    def animate_terminal_cursor(self):
        """Animiert blinkenden Terminal-Cursor"""
        try:
            # Toggle cursor visibility
            if not hasattr(self, 'cursor_visible'):
                self.cursor_visible = True
            
            self.cursor_visible = not self.cursor_visible
            
            # Cursor wird beim nächsten render_display() neu gezeichnet
            # Kein extra Rendering nötig, da update_loop() bereits 20x/Sek rendert
            
            # Schedule next blink (500ms)
            self.after(500, self.animate_terminal_cursor)
            
        except:
            pass  # Window was destroyed
    
    def update_loop(self):
        """Main Update Loop"""
        try:
            # BBS Daten verarbeiten
            if self.connected and self.bbs_connection:
                # Während Transfer: KEINE Daten vom receive_buffer holen!
                # FileTransfer._read_byte() holt sie direkt
                if not self.transfer_active:
                    # Hole ALLE Daten vom BBS (auch wenn Verbindung schon getrennt!)
                    # WICHTIG: Erst alle Daten verarbeiten, dann Disconnect prüfen
                    try:
                        data_processed = True
                        while data_processed:
                            data_processed = False
                            if self.bbs_connection.client.has_received_data():
                                data = self.bbs_connection.client.get_received_data()
                                if data:
                                    data_processed = True
                                    # Log incoming traffic
                                    self.log_traffic("RECV", data)
                                    
                                    # Scrollback Buffer updaten
                                    if isinstance(data, bytes):
                                        self.scrollback.add_bytes(data)
                                    elif isinstance(data, str):
                                        self.scrollback.add_bytes(data.encode('latin-1'))
                                    
                                    # Parser verarbeitet die Daten → PETSCII Display
                                    self.parser.parse_bytes(data)
                    except Exception as e:
                        debug_print(f"[UPDATE_LOOP] Error reading data: {e}")
                    
                    # NACH Queue-Verarbeitung: Prüfe ob Verbindung getrennt
                    # Nur wenn KEINE Daten mehr da sind!
                    try:
                        if hasattr(self.bbs_connection, 'client') and self.bbs_connection.client:
                            client = self.bbs_connection.client
                            # Noch Daten in der Queue?
                            has_more_data = client.has_received_data()
                            if hasattr(client, 'receive_queue') and not client.receive_queue.empty():
                                has_more_data = True
                            
                            # Nur Disconnect wenn Queue WIRKLICH leer UND Verbindung getrennt
                            if not has_more_data and not client.connected:
                                self.connected = False
                                if self.server_mode:
                                    self.status_var.set(f"Server Mode | Client disconnected | Listening on port {self.server_port} ...")
                                else:
                                    self.status_var.set("Disconnected (BBS closed connection)")
                                debug_print("[UPDATE_LOOP] Connection closed and queue completely empty")
                    except Exception:
                        pass
                else:
                    # Transfer aktiv
                    try:
                        if self.bbs_connection.client.has_received_data():
                            debug_print(f"[UPDATE_LOOP] transfer_active=True but queue has data!")
                    except Exception:
                        pass
            
            # Rendering
            self.render_display()
            
        except Exception as e:
            print(f"Update error: {e}")
            import traceback
            traceback.print_exc()
        
        self.after(50, self.update_loop)


if __name__ == '__main__':
    app = BBSTerminal()
    app.mainloop()
