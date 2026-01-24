#!/usr/bin/env python3
"""
PETSCII BBS Telnet Client
Terminal-Emulator für C64 BBS-Systeme mit vollem PETSCII-Support
"""

import sys
import socket
import threading
import queue
import time
from pathlib import Path
from typing import Optional, Tuple
import json
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
from PIL import Image, ImageTk, ImageDraw, ImageFont
import io

# Importiere PETSCII-Funktionen
from petscii_lib import (
    parse_petscii_stream,
    render_petscii_to_image,
    PALETTE_ORIG,
    PALETTE_MID,
    PALETTE_BRIGHT,
    DEFAULT_BG,
    PETSCII_TO_ASCII,
)


class TelnetConnection:
    """Handles Telnet connection to BBS"""
    
    def __init__(self, host: str, port: int, callback):
        self.host = host
        self.port = port
        self.callback = callback
        self.socket: Optional[socket.socket] = None
        self.running = False
        self.recv_thread: Optional[threading.Thread] = None
        
    def connect(self) -> bool:
        """Establish connection to BBS"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(10)
            self.socket.connect((self.host, self.port))
            self.socket.settimeout(None)
            
            self.running = True
            self.recv_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self.recv_thread.start()
            
            return True
        except Exception as e:
            self.callback('error', f"Connection failed: {e}")
            return False
    
    def _receive_loop(self):
        """Receive data from BBS"""
        buffer = bytearray()
        
        while self.running:
            try:
                data = self.socket.recv(4096)
                if not data:
                    self.callback('disconnected', None)
                    break
                
                buffer.extend(data)
                # Send received data to callback
                self.callback('data', bytes(buffer))
                buffer.clear()
                
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    self.callback('error', f"Receive error: {e}")
                break
    
    def send(self, data: bytes):
        """Send data to BBS"""
        if self.socket and self.running:
            try:
                self.socket.sendall(data)
            except Exception as e:
                self.callback('error', f"Send error: {e}")
    
    def disconnect(self):
        """Close connection"""
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        self.socket = None


class BBSConfig:
    """Manages BBS configuration"""
    
    def __init__(self, config_file: str = "bbs_config.json"):
        self.config_file = Path(config_file)
        self.bbs_list = []
        self.load()
    
    def load(self):
        """Load BBS list from config file"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    data = json.load(f)
                    self.bbs_list = data.get('bbs_list', [])
            except Exception as e:
                print(f"Error loading config: {e}")
                self._create_default()
        else:
            self._create_default()
    
    def _create_default(self):
        """Create default BBS list"""
        self.bbs_list = [
            {
                'name': 'Cottonwood BBS',
                'host': 'cottonwoodbbs.dyndns.org',
                'port': 6502,
                'description': 'Classic C64 BBS'
            },
            {
                'name': 'Street Corner BBS',
                'host': 'bbs.fozztexx.com',
                'port': 6400,
                'description': 'Retro C64 BBS'
            },
            {
                'name': 'Particle BBS',
                'host': 'particlebbs.com',
                'port': 6400,
                'description': 'Active C64 Community'
            }
        ]
        self.save()
    
    def save(self):
        """Save BBS list to config file"""
        try:
            with open(self.config_file, 'w') as f:
                json.dump({'bbs_list': self.bbs_list}, f, indent=2)
        except Exception as e:
            print(f"Error saving config: {e}")
    
    def add_bbs(self, name: str, host: str, port: int, description: str = ""):
        """Add new BBS to list"""
        self.bbs_list.append({
            'name': name,
            'host': host,
            'port': port,
            'description': description
        })
        self.save()


class PETSCIITerminal:
    """Main PETSCII Terminal Window"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("PETSCII BBS Terminal")
        self.root.geometry("840x650")
        
        # Configuration
        self.config = BBSConfig()
        self.connection: Optional[TelnetConnection] = None
        self.connected = False
        
        # PETSCII buffer
        self.petscii_buffer = bytearray()
        self.screen_lines = [[]]  # Parsed PETSCII screen
        
        # Palette selection
        self.current_palette = PALETTE_MID
        self.palette_index = 1  # 0=orig, 1=mid, 2=bright
        
        # Font setup
        self.setup_font()
        
        # UI setup
        self.setup_ui()
        
        # Keyboard bindings
        self.setup_keybindings()
        
        # Update timer
        self.update_screen()
    
    def setup_font(self):
        """Setup C64 Pro Mono font"""
        script_dir = Path(__file__).parent
        font_path = script_dir / "C64_Pro_Mono-STYLE.ttf"
        
        if not font_path.exists():
            # Try current directory
            font_path = Path("C64_Pro_Mono-STYLE.ttf")
        
        if not font_path.exists():
            messagebox.showerror("Font Error", 
                "C64_Pro_Mono-STYLE.ttf not found!\nPlease place it in the application directory.")
            sys.exit(1)
        
        self.font_path = font_path
        self.font_size = 16
    
    def setup_ui(self):
        """Setup user interface"""
        # Menu bar
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # BBS Menu
        bbs_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="BBS", menu=bbs_menu)
        bbs_menu.add_command(label="Connect (F1)", command=self.show_dial_menu, accelerator="F1")
        bbs_menu.add_command(label="Disconnect (F2)", command=self.disconnect, accelerator="F2")
        bbs_menu.add_separator()
        bbs_menu.add_command(label="Exit", command=self.quit_app)
        
        # Transfer Menu
        transfer_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Transfer", menu=transfer_menu)
        transfer_menu.add_command(label="Upload File (F3)", command=self.upload_file, accelerator="F3")
        transfer_menu.add_command(label="Download File (F4)", command=self.download_file, accelerator="F4")
        
        # Settings Menu
        settings_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Settings", menu=settings_menu)
        settings_menu.add_command(label="Palette: Original", command=lambda: self.set_palette(0))
        settings_menu.add_command(label="Palette: Medium", command=lambda: self.set_palette(1))
        settings_menu.add_command(label="Palette: Bright", command=lambda: self.set_palette(2))
        settings_menu.add_separator()
        settings_menu.add_command(label="Manage BBS List", command=self.manage_bbs_list)
        
        # Status bar
        self.status_bar = ttk.Frame(self.root)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.status_label = ttk.Label(self.status_bar, text="Not Connected", relief=tk.SUNKEN)
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # Terminal display (Canvas for PETSCII rendering)
        self.terminal_frame = ttk.Frame(self.root)
        self.terminal_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Canvas with scrollbar
        self.canvas = tk.Canvas(self.terminal_frame, bg='black', 
                                width=800, height=500)
        self.scrollbar = ttk.Scrollbar(self.terminal_frame, orient=tk.VERTICAL,
                                       command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Image placeholder
        self.canvas_image = None
        self.photo_image = None
        
        # Input field
        input_frame = ttk.Frame(self.root)
        input_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)
        
        ttk.Label(input_frame, text="Input:").pack(side=tk.LEFT)
        
        self.input_field = ttk.Entry(input_frame)
        self.input_field.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.input_field.bind('<Return>', self.send_input)
        
        send_btn = ttk.Button(input_frame, text="Send", command=self.send_input)
        send_btn.pack(side=tk.LEFT)
    
    def setup_keybindings(self):
        """Setup keyboard shortcuts"""
        self.root.bind('<F1>', lambda e: self.show_dial_menu())
        self.root.bind('<F2>', lambda e: self.disconnect())
        self.root.bind('<F3>', lambda e: self.upload_file())
        self.root.bind('<F4>', lambda e: self.download_file())
        
        # Focus input field on any key
        self.root.bind('<Key>', self.focus_input)
    
    def focus_input(self, event):
        """Focus input field when typing"""
        if event.widget != self.input_field:
            self.input_field.focus_set()
    
    def show_dial_menu(self):
        """Show BBS connection dialog"""
        if self.connected:
            messagebox.showinfo("Already Connected", 
                "Please disconnect before connecting to another BBS.")
            return
        
        # Create dialog
        dialog = tk.Toplevel(self.root)
        dialog.title("Connect to BBS")
        dialog.geometry("500x300")
        dialog.transient(self.root)
        dialog.grab_set()
        
        # BBS List
        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="Select BBS:", font=('Arial', 12, 'bold')).pack()
        
        listbox = tk.Listbox(frame, height=10)
        listbox.pack(fill=tk.BOTH, expand=True, pady=10)
        
        for bbs in self.config.bbs_list:
            listbox.insert(tk.END, f"{bbs['name']} - {bbs['host']}:{bbs['port']}")
        
        def connect_selected():
            selection = listbox.curselection()
            if selection:
                idx = selection[0]
                bbs = self.config.bbs_list[idx]
                dialog.destroy()
                self.connect_to_bbs(bbs['host'], bbs['port'], bbs['name'])
        
        listbox.bind('<Double-Button-1>', lambda e: connect_selected())
        
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X)
        
        ttk.Button(btn_frame, text="Connect", command=connect_selected).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT)
    
    def connect_to_bbs(self, host: str, port: int, name: str):
        """Connect to a BBS"""
        self.status_label.config(text=f"Connecting to {name}...")
        self.root.update()
        
        self.connection = TelnetConnection(host, port, self.on_telnet_event)
        
        if self.connection.connect():
            self.connected = True
            self.status_label.config(text=f"Connected to {name} ({host}:{port})")
            self.petscii_buffer.clear()
            self.screen_lines = [[]]
        else:
            self.status_label.config(text="Connection failed")
    
    def disconnect(self):
        """Disconnect from BBS"""
        if self.connection:
            self.connection.disconnect()
            self.connection = None
        self.connected = False
        self.status_label.config(text="Disconnected")
    
    def on_telnet_event(self, event_type: str, data):
        """Handle telnet events"""
        if event_type == 'data':
            # Add to buffer
            self.petscii_buffer.extend(data)
            
        elif event_type == 'disconnected':
            self.root.after(0, lambda: self.status_label.config(text="Disconnected by remote"))
            self.connected = False
            
        elif event_type == 'error':
            self.root.after(0, lambda: messagebox.showerror("Connection Error", data))
    
    def send_input(self, event=None):
        """Send input to BBS"""
        if not self.connected:
            messagebox.showwarning("Not Connected", "Please connect to a BBS first.")
            return
        
        text = self.input_field.get()
        if text:
            # Convert ASCII to PETSCII
            petscii = self.ascii_to_petscii(text)
            petscii += b'\r'  # Add RETURN
            
            self.connection.send(petscii)
            self.input_field.delete(0, tk.END)
    
    def ascii_to_petscii(self, text: str) -> bytes:
        """Convert ASCII string to PETSCII bytes"""
        result = bytearray()
        for ch in text:
            if 'a' <= ch <= 'z':
                # lowercase -> PETSCII uppercase (0x41-0x5A)
                result.append(ord(ch.upper()))
            elif 'A' <= ch <= 'Z':
                # uppercase -> PETSCII graphics (0xC1-0xDA)
                result.append(0xC1 + (ord(ch) - ord('A')))
            elif ch in PETSCII_TO_ASCII:
                # Find reverse mapping
                for petscii, ascii_ch in PETSCII_TO_ASCII.items():
                    if ascii_ch == ch:
                        result.append(petscii)
                        break
                else:
                    result.append(ord(ch))
            else:
                result.append(ord(ch) if ord(ch) < 256 else ord('?'))
        return bytes(result)
    
    def update_screen(self):
        """Update terminal display"""
        if self.petscii_buffer:
            # Parse PETSCII buffer
            self.screen_lines = parse_petscii_stream(bytes(self.petscii_buffer))
            
            # Render to image
            if self.screen_lines:
                img = render_petscii_to_image(
                    self.screen_lines,
                    self.font_path,
                    self.font_size,
                    self.current_palette
                )
                
                # Convert to PhotoImage
                self.photo_image = ImageTk.PhotoImage(img)
                
                # Update canvas
                if self.canvas_image:
                    self.canvas.delete(self.canvas_image)
                
                self.canvas_image = self.canvas.create_image(0, 0, anchor=tk.NW, 
                                                             image=self.photo_image)
                
                # Update scroll region
                self.canvas.configure(scrollregion=self.canvas.bbox(tk.ALL))
                
                # Auto-scroll to bottom
                self.canvas.yview_moveto(1.0)
        
        # Schedule next update
        self.root.after(100, self.update_screen)
    
    def set_palette(self, index: int):
        """Change color palette"""
        palettes = [PALETTE_ORIG, PALETTE_MID, PALETTE_BRIGHT]
        self.palette_index = index
        self.current_palette = palettes[index]
    
    def upload_file(self):
        """Upload file to BBS (placeholder)"""
        if not self.connected:
            messagebox.showwarning("Not Connected", "Please connect to a BBS first.")
            return
        
        filename = filedialog.askopenfilename(
            title="Select file to upload",
            filetypes=[("All files", "*.*"), ("SEQ files", "*.seq"), ("PRG files", "*.prg")]
        )
        
        if filename:
            messagebox.showinfo("Upload", 
                f"Upload functionality coming soon!\nFile: {filename}")
    
    def download_file(self):
        """Download file from BBS (placeholder)"""
        if not self.connected:
            messagebox.showwarning("Not Connected", "Please connect to a BBS first.")
            return
        
        messagebox.showinfo("Download", "Download functionality coming soon!")
    
    def manage_bbs_list(self):
        """Manage BBS list"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Manage BBS List")
        dialog.geometry("600x400")
        dialog.transient(self.root)
        
        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        
        # List
        listbox = tk.Listbox(frame)
        listbox.pack(fill=tk.BOTH, expand=True, pady=5)
        
        def refresh_list():
            listbox.delete(0, tk.END)
            for bbs in self.config.bbs_list:
                listbox.insert(tk.END, 
                    f"{bbs['name']} - {bbs['host']}:{bbs['port']}")
        
        refresh_list()
        
        # Buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=5)
        
        def add_bbs():
            # Simple add dialog
            add_dialog = tk.Toplevel(dialog)
            add_dialog.title("Add BBS")
            add_dialog.geometry("400x200")
            
            ttk.Label(add_dialog, text="Name:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
            name_entry = ttk.Entry(add_dialog, width=30)
            name_entry.grid(row=0, column=1, padx=5, pady=5)
            
            ttk.Label(add_dialog, text="Host:").grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)
            host_entry = ttk.Entry(add_dialog, width=30)
            host_entry.grid(row=1, column=1, padx=5, pady=5)
            
            ttk.Label(add_dialog, text="Port:").grid(row=2, column=0, padx=5, pady=5, sticky=tk.W)
            port_entry = ttk.Entry(add_dialog, width=30)
            port_entry.grid(row=2, column=1, padx=5, pady=5)
            port_entry.insert(0, "6400")
            
            def save_bbs():
                name = name_entry.get()
                host = host_entry.get()
                try:
                    port = int(port_entry.get())
                    if name and host:
                        self.config.add_bbs(name, host, port)
                        refresh_list()
                        add_dialog.destroy()
                except ValueError:
                    messagebox.showerror("Error", "Port must be a number")
            
            ttk.Button(add_dialog, text="Add", command=save_bbs).grid(row=3, column=0, 
                                                                       columnspan=2, pady=10)
        
        ttk.Button(btn_frame, text="Add BBS", command=add_bbs).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Close", command=dialog.destroy).pack(side=tk.LEFT)
    
    def quit_app(self):
        """Quit application"""
        if self.connected:
            self.disconnect()
        self.root.quit()


def main():
    """Main entry point"""
    root = tk.Tk()
    app = PETSCIITerminal(root)
    
    # Handle window close
    root.protocol("WM_DELETE_WINDOW", app.quit_app)
    
    root.mainloop()


if __name__ == "__main__":
    main()
