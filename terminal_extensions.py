"""
Terminal Extensions - Neue Features für PETSCII BBS Terminal v3.3
Enthält: Upload/Download Dialoge, Settings, Scrollback Buffer
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
from file_transfer import FileTransfer, TransferProtocol


class SettingsDialog(tk.Toplevel):
    """Parameter-Einstellungen Dialog (F5)"""
    
    def __init__(self, parent, current_protocol, current_columns):
        super().__init__(parent)
        self.title("Terminal Parameter")
        self.geometry("400x350")
        self.resizable(False, False)
        self.result = None
        
        # Protokoll-Auswahl
        protocol_frame = ttk.LabelFrame(self, text="Transfer-Protokoll", padding=10)
        protocol_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.protocol_var = tk.StringVar(value=current_protocol.value)
        
        protocols = [
            (TransferProtocol.XMODEM_CRC, "XModem-CRC (empfohlen)"),
            (TransferProtocol.XMODEM, "XModem (Checksum)"),
            (TransferProtocol.XMODEM_1K, "XModem-1K"),
            (TransferProtocol.YMODEM, "YModem (noch nicht verfügbar)"),
            (TransferProtocol.ZMODEM, "ZModem (noch nicht verfügbar)"),
            (TransferProtocol.PUNTER, "Punter (noch nicht verfügbar)")
        ]
        
        for proto, label in protocols:
            state = 'normal' if proto in [TransferProtocol.XMODEM, TransferProtocol.XMODEM_CRC, TransferProtocol.XMODEM_1K] else 'disabled'
            rb = ttk.Radiobutton(protocol_frame, text=label, 
                                variable=self.protocol_var, value=proto.value,
                                state=state)
            rb.pack(anchor=tk.W, pady=2)
        
        # Zeichen-Breite
        columns_frame = ttk.LabelFrame(self, text="Zeichen pro Zeile", padding=10)
        columns_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.columns_var = tk.IntVar(value=current_columns)
        
        rb40 = ttk.Radiobutton(columns_frame, text="40 Zeichen (C64 Standard)", 
                               variable=self.columns_var, value=40)
        rb40.pack(anchor=tk.W, pady=2)
        
        rb80 = ttk.Radiobutton(columns_frame, text="80 Zeichen (erweitert)", 
                               variable=self.columns_var, value=80)
        rb80.pack(anchor=tk.W, pady=2)
        
        ttk.Label(columns_frame, text="⚠ Änderung benötigt Neustart", 
                 font=('Arial', 9, 'italic')).pack(anchor=tk.W, pady=5)
        
        # Buttons
        button_frame = ttk.Frame(self)
        button_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Button(button_frame, text="Speichern", command=self.ok, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Abbrechen", command=self.destroy, width=12).pack(side=tk.LEFT)
        
        # Center window
        self.transient(parent)
        self.grab_set()
        self.protocol = tk.ACTIVE
    
    def ok(self):
        # Finde gewähltes Protokoll
        for proto in TransferProtocol:
            if proto.value == self.protocol_var.get():
                self.result = {
                    'protocol': proto,
                    'columns': self.columns_var.get()
                }
                break
        self.destroy()


class UploadDialog(tk.Toplevel):
    """Upload File Dialog (F1) mit Progress"""
    
    def __init__(self, parent, transfer_obj):
        super().__init__(parent)
        self.title("Upload File")
        self.geometry("550x250")
        self.resizable(False, False)
        self.transfer = transfer_obj
        self.cancelled = False
        
        # Header
        header = ttk.Label(self, text="📤 File Upload", font=('Arial', 14, 'bold'))
        header.pack(pady=10)
        
        # File-Auswahl
        file_frame = ttk.LabelFrame(self, text="Datei auswählen", padding=10)
        file_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.filepath_var = tk.StringVar()
        entry_frame = ttk.Frame(file_frame)
        entry_frame.pack(fill=tk.X)
        
        ttk.Entry(entry_frame, textvariable=self.filepath_var, state='readonly', 
                 font=('Courier', 9)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(entry_frame, text="Browse...", command=self.browse_file, width=10).pack(side=tk.LEFT)
        
        # Progress
        progress_frame = ttk.LabelFrame(self, text="Status", padding=10)
        progress_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.progress_var = tk.StringVar(value="Datei wählen und Upload starten...")
        ttk.Label(progress_frame, textvariable=self.progress_var, font=('Arial', 9)).pack(anchor=tk.W)
        
        self.progress_bar = ttk.Progressbar(progress_frame, mode='determinate')
        self.progress_bar.pack(fill=tk.X, pady=5)
        
        self.bytes_var = tk.StringVar(value="")
        ttk.Label(progress_frame, textvariable=self.bytes_var, font=('Courier', 8)).pack(anchor=tk.W)
        
        # Buttons
        button_frame = ttk.Frame(self)
        button_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.upload_btn = ttk.Button(button_frame, text="Upload starten", 
                                     command=self.start_upload, state='disabled', width=15)
        self.upload_btn.pack(side=tk.LEFT, padx=5)
        
        self.cancel_btn = ttk.Button(button_frame, text="Abbrechen", 
                                     command=self.cancel, width=15)
        self.cancel_btn.pack(side=tk.LEFT)
        
        self.transient(parent)
        self.grab_set()
    
    def browse_file(self):
        filename = filedialog.askopenfilename(
            parent=self,
            title="Datei zum Hochladen wählen",
            filetypes=[
                ("All Files", "*.*"),
                ("Text Files", "*.txt"),
                ("SEQ Files", "*.seq"),
                ("PRG Files", "*.prg")
            ]
        )
        if filename:
            self.filepath_var.set(filename)
            self.upload_btn.config(state='normal')
            self.progress_var.set("Bereit zum Upload")
    
    def start_upload(self):
        filepath = self.filepath_var.get()
        if not filepath:
            return
        
        self.upload_btn.config(state='disabled')
        self.cancel_btn.config(text="Abbrechen")
        self.progress_var.set("Starte Upload...")
        self.progress_bar['value'] = 0
        
        # Starte Upload in Thread
        def upload_thread():
            def progress_callback(bytes_sent, total_bytes, status):
                def update_ui():
                    if total_bytes > 0:
                        percent = (bytes_sent / total_bytes) * 100
                        self.progress_bar['value'] = percent
                        self.bytes_var.set(f"{bytes_sent:,} / {total_bytes:,} bytes ({percent:.1f}%)")
                    self.progress_var.set(status)
                
                try:
                    self.after(0, update_ui)
                except:
                    pass
            
            try:
                success = self.transfer.send_file(filepath, progress_callback)
                
                def finish():
                    if success:
                        self.progress_var.set("✓ Upload erfolgreich!")
                        self.cancel_btn.config(text="Schließen")
                    else:
                        self.progress_var.set("✗ Upload fehlgeschlagen!")
                        self.upload_btn.config(state='normal')
                
                self.after(0, finish)
            except Exception as e:
                def show_error():
                    self.progress_var.set(f"✗ Fehler: {str(e)}")
                    self.upload_btn.config(state='normal')
                self.after(0, show_error)
        
        threading.Thread(target=upload_thread, daemon=True).start()
    
    def cancel(self):
        self.cancelled = True
        if hasattr(self, 'transfer'):
            self.transfer.cancel()
        self.destroy()


class DownloadDialog(tk.Toplevel):
    """Download File Dialog (F3) mit Progress"""
    
    def __init__(self, parent, transfer_obj):
        super().__init__(parent)
        self.title("Download File")
        self.geometry("550x300")
        self.resizable(False, False)
        self.transfer = transfer_obj
        self.cancelled = False
        self.download_started = False
        
        # Header
        header = ttk.Label(self, text="📥 File Download", font=('Arial', 14, 'bold'))
        header.pack(pady=10)
        
        # Info
        info_frame = ttk.Frame(self)
        info_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(info_frame, text="1. Download im BBS starten\n2. Hier auf 'Download starten' klicken\n3. Dateinamen eingeben",
                 font=('Arial', 9), justify=tk.LEFT).pack(anchor=tk.W)
        
        # Filename
        file_frame = ttk.LabelFrame(self, text="Speichern als", padding=10)
        file_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.filename_var = tk.StringVar(value="download.dat")
        entry_frame = ttk.Frame(file_frame)
        entry_frame.pack(fill=tk.X)
        
        ttk.Entry(entry_frame, textvariable=self.filename_var, 
                 font=('Courier', 9)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(entry_frame, text="Browse...", command=self.browse_save, width=10).pack(side=tk.LEFT)
        
        # Progress
        progress_frame = ttk.LabelFrame(self, text="Status", padding=10)
        progress_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.progress_var = tk.StringVar(value="Bereit zum Download...")
        ttk.Label(progress_frame, textvariable=self.progress_var, font=('Arial', 9)).pack(anchor=tk.W)
        
        self.progress_bar = ttk.Progressbar(progress_frame, mode='indeterminate')
        self.progress_bar.pack(fill=tk.X, pady=5)
        
        self.bytes_var = tk.StringVar(value="")
        ttk.Label(progress_frame, textvariable=self.bytes_var, font=('Courier', 8)).pack(anchor=tk.W)
        
        # Buttons
        button_frame = ttk.Frame(self)
        button_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.download_btn = ttk.Button(button_frame, text="Download starten", 
                                       command=self.start_download, width=15)
        self.download_btn.pack(side=tk.LEFT, padx=5)
        
        self.cancel_btn = ttk.Button(button_frame, text="Abbrechen", 
                                     command=self.cancel, width=15)
        self.cancel_btn.pack(side=tk.LEFT)
        
        self.transient(parent)
        self.grab_set()
    
    def browse_save(self):
        filename = filedialog.asksaveasfilename(
            parent=self,
            title="Datei speichern als",
            defaultextension=".*",
            initialfile=self.filename_var.get(),
            filetypes=[
                ("All Files", "*.*"),
                ("Text Files", "*.txt"),
                ("SEQ Files", "*.seq"),
                ("PRG Files", "*.prg")
            ]
        )
        if filename:
            self.filename_var.set(filename)
    
    def start_download(self):
        filepath = self.filename_var.get()
        if not filepath:
            messagebox.showwarning("Fehler", "Bitte Dateinamen eingeben!", parent=self)
            return
        
        self.download_started = True
        self.download_btn.config(state='disabled')
        self.cancel_btn.config(text="Abbrechen")
        self.progress_bar.start(10)
        self.progress_var.set("Warte auf Daten vom BBS...")
        
        # Starte Download in Thread
        def download_thread():
            def progress_callback(bytes_received, status):
                def update_ui():
                    self.bytes_var.set(f"{bytes_received:,} bytes empfangen")
                    self.progress_var.set(status)
                
                try:
                    self.after(0, update_ui)
                except:
                    pass
            
            try:
                success = self.transfer.receive_file(filepath, progress_callback)
                
                def finish():
                    self.progress_bar.stop()
                    if success:
                        self.progress_var.set("✓ Download erfolgreich!")
                        self.cancel_btn.config(text="Schließen")
                    else:
                        self.progress_var.set("✗ Download fehlgeschlagen!")
                        self.download_btn.config(state='normal')
                
                self.after(0, finish)
            except Exception as e:
                def show_error():
                    self.progress_bar.stop()
                    self.progress_var.set(f"✗ Fehler: {str(e)}")
                    self.download_btn.config(state='normal')
                self.after(0, show_error)
        
        threading.Thread(target=download_thread, daemon=True).start()
    
    def cancel(self):
        self.cancelled = True
        if self.download_started and hasattr(self, 'transfer'):
            self.transfer.cancel()
        self.destroy()


class ScrollbackBuffer:
    """
    Scrollback Buffer für Terminal-History
    Speichert alle empfangenen und gesendeten Zeichen
    UNLIMITED - kein Limit!
    """
    
    def __init__(self, max_lines=0):
        self.max_lines = 0  # 0 = UNLIMITED
        self.lines = []
        self.current_line = []
        self.raw_bytes = bytearray()  # RAW PETSCII bytes
        self.max_raw_bytes = 0  # 0 = UNLIMITED
    
    def add_char(self, char):
        """Fügt ein Zeichen zum Buffer hinzu"""
        if char == '\n' or char == '\r':
            self.lines.append(''.join(self.current_line))
            self.current_line = []
            
            # Limitiere Buffer-Größe nur wenn max_lines > 0
            if self.max_lines > 0 and len(self.lines) > self.max_lines:
                self.lines.pop(0)
        else:
            self.current_line.append(char)
    
    def add_bytes(self, data):
        """Fügt mehrere Bytes zum Buffer hinzu"""
        # Speichere RAW bytes (UNLIMITED!)
        if isinstance(data, (bytes, bytearray)):
            self.raw_bytes.extend(data)
        elif isinstance(data, int):
            self.raw_bytes.append(data)
        
        # KEIN Limit - unbegrenzt!
        
        # Text-Representation für get_all_text()
        for byte in data:
            # Speichere ALLE bytes für PETSCII (nicht nur ASCII printable)
            # PETSCII nutzt 0x20-0xFF
            if isinstance(byte, int):
                if byte >= 0x20 or byte in [0x0D, 0x0A]:  # Printable PETSCII + CR/LF
                    self.add_char(chr(byte))
                elif byte < 0x20:
                    # Control codes als Hex darstellen
                    self.add_char(f'[{byte:02X}]')
            else:
                # Falls char schon als string
                self.add_char(byte)
    
    def get_lines(self, start=0, count=None):
        """Holt Zeilen aus dem Buffer"""
        if count is None:
            return self.lines[start:]
        return self.lines[start:start+count]
    
    def get_all_text(self):
        """Gibt gesamten Buffer als Text zurück"""
        all_lines = self.lines + ([''.join(self.current_line)] if self.current_line else [])
        return '\n'.join(all_lines)
    
    def get_all_bytes(self):
        """Gibt alle RAW PETSCII bytes zurück"""
        return bytes(self.raw_bytes)
    
    def clear(self):
        """Löscht den Buffer"""
        self.lines = []
        self.current_line = []
        self.raw_bytes = bytearray()
    
    def get_line_count(self):
        """Gibt Anzahl der Zeilen zurück"""
        return len(self.lines)



class ScrollbackViewer(tk.Toplevel):
    """Viewer für Scrollback Buffer mit PETSCII Rendering
    
    - 2500 Zeilen pro Page
    - Virtuelles Scrolling (rendert nur sichtbare Zeilen)
    - Auto-Page-Wechsel am Ende der Page
    - Füllt das gesamte Fenster
    """
    
    def __init__(self, parent, scrollback_buffer, terminal_width=80):
        super().__init__(parent)
        self.title(f"Scrollback Buffer (PETSCII) - {terminal_width} Columns")
        self.geometry("1280x800")
        self.buffer = scrollback_buffer
        self.terminal_width = terminal_width
        
        # Page-System: 2500 Zeilen pro Page
        self.lines_per_page = 2500
        self.current_page = 0
        self.total_pages = 1
        
        # Viewport wird dynamisch berechnet
        self.scroll_offset = 0
        
        # Import für PIL
        from PIL import ImageTk
        self.ImageTk = ImageTk
        
        # PETSCII Screen + Parser für Scrollback
        from petscii_parser import PETSCIIScreenBuffer, PETSCIIParser
        from c64_rom_renderer import C64ROMFontRenderer
        
        # Screen für Parsing (wächst unbegrenzt)
        self.screen = PETSCIIScreenBuffer(width=terminal_width, height=50)
        self.screen.unlimited_growth = True
        self.parser = PETSCIIParser(self.screen, scrollback_mode=True)
        self.renderer = C64ROMFontRenderer(
            self.screen,
            font_upper_path="upper.bmp",
            font_lower_path="lower.bmp",
            zoom=2
        )
        
        # Toolbar
        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Button(toolbar, text="Load RAW", command=self.load_raw).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Refresh", command=self.refresh).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Clear Buffer", command=self.clear_buffer).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Save RAW", command=self.save_raw).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Save Text", command=self.save_text).pack(side=tk.LEFT, padx=2)
        
        # Page-Navigation
        nav_frame = ttk.Frame(toolbar)
        nav_frame.pack(side=tk.RIGHT, padx=10)
        
        ttk.Button(nav_frame, text="⏮", width=3, command=self.page_first).pack(side=tk.LEFT)
        ttk.Button(nav_frame, text="◀", width=3, command=self.page_prev).pack(side=tk.LEFT)
        
        self.page_var = tk.StringVar(value="Page 1/1")
        ttk.Label(nav_frame, textvariable=self.page_var, width=14).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(nav_frame, text="▶", width=3, command=self.page_next).pack(side=tk.LEFT)
        ttk.Button(nav_frame, text="⏭", width=3, command=self.page_last).pack(side=tk.LEFT)
        
        # Status
        self.status_var = tk.StringVar(value="0 lines")
        ttk.Label(toolbar, textvariable=self.status_var).pack(side=tk.RIGHT, padx=10)
        
        # Zeilen-Anzeige (welche Zeilen gerade sichtbar)
        self.lines_var = tk.StringVar(value="Lines: -")
        ttk.Label(toolbar, textvariable=self.lines_var).pack(side=tk.RIGHT, padx=10)
        
        # Main Frame mit Canvas und Scrollbar
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Scrollbar
        self.scrollbar = ttk.Scrollbar(main_frame)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Canvas
        self.canvas = tk.Canvas(main_frame, bg='black')
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Scrollbar Command
        self.scrollbar.config(command=self._on_scrollbar)
        
        # Mausrad
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)
        self.canvas.bind("<Button-5>", self._on_mousewheel)
        
        # Resize Event
        self.canvas.bind("<Configure>", self._on_resize)
        
        # Initial befüllen (nach kurzer Verzögerung damit Canvas-Größe bekannt)
        self.after(100, self.refresh)
        
        self.transient(parent)
    
    def _get_viewport_lines(self):
        """Berechnet wie viele Zeilen ins Fenster passen"""
        canvas_height = self.canvas.winfo_height()
        if canvas_height < 10:
            canvas_height = 600  # Default
        char_height = 8 * self.renderer.zoom
        return max(10, canvas_height // char_height)
    
    def _get_page_lines(self):
        """Anzahl Zeilen in aktueller Page"""
        start = self.current_page * self.lines_per_page
        end = min(start + self.lines_per_page, self.screen.height)
        return max(0, end - start)
    
    def _on_resize(self, event):
        """Fenster wurde vergrößert/verkleinert"""
        self.render_viewport()
    
    def _on_scrollbar(self, *args):
        """Scrollbar Kommando"""
        page_lines = self._get_page_lines()
        viewport_lines = self._get_viewport_lines()
        max_scroll = max(0, page_lines - viewport_lines)
        
        if args[0] == 'moveto':
            fraction = float(args[1])
            self.scroll_offset = int(fraction * max_scroll)
        elif args[0] == 'scroll':
            amount = int(args[1])
            if args[2] == 'units':
                self.scroll_offset += amount
            elif args[2] == 'pages':
                self.scroll_offset += amount * viewport_lines
        
        self._handle_scroll_bounds()
        self.render_viewport()
    
    def _on_mousewheel(self, event):
        """Mausrad Scrolling"""
        if event.num == 4 or event.delta > 0:
            self.scroll_offset -= 3
        elif event.num == 5 or event.delta < 0:
            self.scroll_offset += 3
        
        self._handle_scroll_bounds()
        self.render_viewport()
    
    def _handle_scroll_bounds(self):
        """Prüft Grenzen und wechselt Page bei Bedarf"""
        page_lines = self._get_page_lines()
        viewport_lines = self._get_viewport_lines()
        max_scroll = max(0, page_lines - viewport_lines)
        
        # Am Ende der Page -> nächste Page
        if self.scroll_offset > max_scroll:
            if self.current_page < self.total_pages - 1:
                self.current_page += 1
                self.scroll_offset = 0
                self._update_page_var()
            else:
                self.scroll_offset = max_scroll
        
        # Vor dem Anfang -> vorherige Page
        if self.scroll_offset < 0:
            if self.current_page > 0:
                self.current_page -= 1
                prev_lines = self._get_page_lines()
                self.scroll_offset = max(0, prev_lines - viewport_lines)
                self._update_page_var()
            else:
                self.scroll_offset = 0
    
    def _update_page_var(self):
        """Page-Anzeige aktualisieren"""
        self.page_var.set(f"Page {self.current_page + 1}/{self.total_pages}")
    
    def _update_scrollbar(self):
        """Scrollbar Position aktualisieren"""
        page_lines = self._get_page_lines()
        viewport_lines = self._get_viewport_lines()
        if page_lines <= viewport_lines:
            self.scrollbar.set(0, 1)
        else:
            start = self.scroll_offset / page_lines
            end = (self.scroll_offset + viewport_lines) / page_lines
            self.scrollbar.set(start, min(1, end))
    
    def page_first(self):
        """Erste Page, Anfang"""
        self.current_page = 0
        self.scroll_offset = 0
        self._update_page_var()
        self.render_viewport()
    
    def page_prev(self):
        """Vorherige Page"""
        if self.current_page > 0:
            self.current_page -= 1
            self.scroll_offset = 0
            self._update_page_var()
            self.render_viewport()
    
    def page_next(self):
        """Nächste Page"""
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.scroll_offset = 0
            self._update_page_var()
            self.render_viewport()
    
    def page_last(self):
        """Letzte Page, Ende"""
        self.current_page = max(0, self.total_pages - 1)
        page_lines = self._get_page_lines()
        viewport_lines = self._get_viewport_lines()
        self.scroll_offset = max(0, page_lines - viewport_lines)
        self._update_page_var()
        self.render_viewport()
    
    def refresh(self):
        """Buffer neu parsen und anzeigen"""
        all_bytes = self.buffer.get_all_bytes()
        
        print(f"Scrollback Refresh: {len(all_bytes)} bytes")
        
        # Reset screen
        self.screen.clear_screen()
        self.screen.cursor_x = 0
        self.screen.cursor_y = 0
        
        # Parse
        try:
            self.parser.parse_bytes(all_bytes)
            print(f"Parse OK - {self.screen.height} lines")
        except Exception as e:
            print(f"Parse Error: {e}")
            import traceback
            traceback.print_exc()
        
        # Berechne Pages
        self.total_pages = max(1, (self.screen.height + self.lines_per_page - 1) // self.lines_per_page)
        
        # Gehe zur letzten Page, ans Ende
        self.current_page = max(0, self.total_pages - 1)
        page_lines = self._get_page_lines()
        viewport_lines = self._get_viewport_lines()
        self.scroll_offset = max(0, page_lines - viewport_lines)
        
        # Status
        self.status_var.set(f"{self.screen.height} lines, {len(all_bytes)} bytes")
        self._update_page_var()
        
        self.render_viewport()
    
    def render_viewport(self):
        """Rendert den sichtbaren Bereich - füllt das ganze Fenster"""
        from PIL import Image
        
        # Canvas-Größe holen
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        
        if canvas_width < 10 or canvas_height < 10:
            return  # Canvas noch nicht initialisiert
        
        # Viewport berechnen
        char_width = 8 * self.renderer.zoom
        char_height = 8 * self.renderer.zoom
        viewport_lines = canvas_height // char_height
        
        # Absolute Zeilen-Position
        page_start = self.current_page * self.lines_per_page
        abs_start = page_start + self.scroll_offset
        abs_end = min(abs_start + viewport_lines, self.screen.height)
        
        # Bild in Canvas-Größe erstellen
        img_width = self.screen.width * char_width
        img_height = canvas_height
        
        bg_idx = self.screen.screen_bg if hasattr(self.screen, 'screen_bg') else 0
        bg_color = self.renderer.palette[bg_idx]
        
        img = Image.new('RGB', (img_width, img_height), bg_color)
        
        # Font
        current_font = self.renderer.font_lower if self.screen.charset_mode == 'lower' else self.renderer.font_upper
        
        # Rendere sichtbare Zeilen
        for y in range(abs_start, abs_end):
            render_y = y - abs_start
            for x in range(self.screen.width):
                if y < len(self.screen.buffer):
                    cell = self.screen.buffer[y][x]
                    self.renderer._render_cell(img, current_font, x, render_y, cell, bg_idx)
        
        # Anzeigen
        self.photo = self.ImageTk.PhotoImage(img)
        self.canvas.delete('all')
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.photo)
        
        # Zeilen-Anzeige aktualisieren
        self.lines_var.set(f"Lines {abs_start + 1}-{abs_end} / {self.screen.height}")
        
        self._update_scrollbar()
    
    def clear_buffer(self):
        """Buffer löschen"""
        if messagebox.askyesno("Confirm", "Scrollback Buffer löschen?", parent=self):
            self.buffer.clear()
            self.refresh()
    
    def load_raw(self):
        """RAW PETSCII Datei laden"""
        filename = filedialog.askopenfilename(
            parent=self,
            title="Load RAW PETSCII File",
            filetypes=[("PETSCII SEQ", "*.seq"), ("Binary", "*.bin"), ("All Files", "*.*")]
        )
        if filename:
            try:
                with open(filename, 'rb') as f:
                    raw_data = f.read()
                
                metadata = None
                petscii_data = raw_data
                
                if len(raw_data) >= 2:
                    header_len = int.from_bytes(raw_data[0:2], byteorder='big')
                    
                    if 0 < header_len < 1024 and len(raw_data) >= (2 + header_len):
                        try:
                            import json
                            header_bytes = raw_data[2:2+header_len]
                            metadata = json.loads(header_bytes.decode('utf-8'))
                            petscii_data = raw_data[2+header_len:]
                            
                            if 'width' in metadata:
                                new_width = metadata['width']
                                if new_width != self.screen.width:
                                    from petscii_parser import PETSCIIScreenBuffer, PETSCIIParser
                                    self.screen = PETSCIIScreenBuffer(width=new_width, height=50)
                                    self.screen.unlimited_growth = True
                                    self.parser = PETSCIIParser(self.screen, scrollback_mode=True)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            petscii_data = raw_data
                            metadata = None
                
                self.buffer.add_bytes(petscii_data)
                self.refresh()
                
                info_msg = f"Loaded {len(petscii_data)} bytes from {filename}"
                if metadata and 'width' in metadata:
                    info_msg += f"\nWidth: {metadata['width']} columns"
                
                messagebox.showinfo("Success", info_msg, parent=self)
            except Exception as e:
                messagebox.showerror("Error", f"Fehler beim Laden: {str(e)}", parent=self)
                import traceback
                traceback.print_exc()
    
    def save_raw(self):
        """Buffer als RAW speichern"""
        filename = filedialog.asksaveasfilename(
            parent=self,
            title="Scrollback als RAW speichern",
            defaultextension=".seq",
            filetypes=[("PETSCII SEQ", "*.seq"), ("Binary", "*.bin"), ("All Files", "*.*")]
        )
        if filename:
            try:
                all_bytes = self.buffer.get_all_bytes()
                
                import json
                metadata = {
                    "width": self.screen.width,
                    "height": self.screen.height,
                    "version": "3.3"
                }
                header = json.dumps(metadata).encode('utf-8')
                header_len = len(header)
                
                with open(filename, 'wb') as f:
                    f.write(header_len.to_bytes(2, byteorder='big'))
                    f.write(header)
                    f.write(all_bytes)
                
                messagebox.showinfo("Success", 
                    f"RAW gespeichert: {filename}\n"
                    f"Width: {self.screen.width} columns\n"
                    f"Size: {len(all_bytes)} bytes", 
                    parent=self)
            except Exception as e:
                messagebox.showerror("Error", f"Fehler: {str(e)}", parent=self)
    
    def save_text(self):
        """Buffer als Text speichern"""
        filename = filedialog.asksaveasfilename(
            parent=self,
            title="Scrollback als Text speichern",
            defaultextension=".txt",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")]
        )
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(self.buffer.get_all_text())
                messagebox.showinfo("Success", f"Text gespeichert: {filename}", parent=self)
            except Exception as e:
                messagebox.showerror("Error", f"Fehler: {str(e)}", parent=self)
