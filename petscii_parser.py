"""
PETSCII Parser und Screen Buffer
Verarbeitet PETSCII-Stream und verwaltet virtuellen C64-Screen

Version 3.5:
- CTRL-B ($02) + Farbcode = Hintergrundfarbe ändern (CCGMS/Novaterm kompatibel)
- Unbegrenzter Screen-Buffer (Renderer zeigt nur Viewport)
- Bell-Sequenz £B1 ($5C $42 $31) und $07
"""

from petscii_charset import (
    get_petscii_char, is_control_code, get_control_name,
    is_color_code, get_color_number, C64_COLORS
)
from petscii_screencode import petscii_to_screencode

# Screen Buffer speichert SCREENCODES (nicht PETSCII!)


class PETSCIIScreenCell:
    """Eine einzelne Bildschirmzelle mit Zeichen und Attributen"""
    
    def __init__(self, char=' ', fg_color=14, bg_color=0, reverse=False):
        self.char = char
        self.fg_color = fg_color  # Foreground color (0-15)
        self.bg_color = bg_color  # Background color (0-15) - 0=schwarz
        self.reverse = reverse     # Reverse video flag
        
    def copy(self):
        """Erstellt eine Kopie der Zelle"""
        return PETSCIIScreenCell(self.char, self.fg_color, self.bg_color, self.reverse)
    
    def get_display_colors(self):
        """Gibt die anzuzeigenden Farben zurück (berücksichtigt Reverse)"""
        if self.reverse:
            return self.bg_color, self.fg_color
        return self.fg_color, self.bg_color


class PETSCIIScreenBuffer:
    """
    Virtueller C64 Screen Buffer (40x25 oder 80x25)
    Verwaltet Zeichen, Farben und Cursor-Position
    
    Hintergrund-System (CCGMS/Novaterm kompatibel):
    - screen_bg: Globaler Screen-Hintergrund ($D021) - CTRL-B ändert diesen
    - border_color: Border-Farbe ($D020)
    - Zellen haben KEINEN individuellen Hintergrund (außer bei Reverse)
    """
    
    def __init__(self, width=40, height=25):
        self.width = width
        self.height = height  # Normal 25 Zeilen
        self.cursor_x = 0
        self.cursor_y = 0
        self.current_fg = 14  # Light Blue (default C64)
        
        # Globaler Screen-Hintergrund (wie $D021) - CTRL-B ändert diesen!
        self.screen_bg = 0    # Schwarz - Standard für BBS
        self.current_bg = 0   # Für Zellen (wird von screen_bg übernommen)
        
        self.border_color = 0  # Schwarz - Standard für BBS
        self.reverse_mode = False
        self.charset_mode = 'lower'  # DEFAULT: 'lower' für BBS (nicht 'upper')
        
        # Screen buffer als 2D-Array (normal 25 Zeilen)
        self.buffer = [[PETSCIIScreenCell(bg_color=self.current_bg) 
                       for _ in range(width)] 
                       for _ in range(height)]
        
        # Scrollback buffer für History (2500 Zeilen)
        self.scrollback = []
        self.max_scrollback = 0  # 0 = UNLIMITED
        
        # Flag für dynamisches Wachstum (AUS)
        self.unlimited_growth = False
        
    def clear_screen(self):
        """Löscht den Screen"""
        # Im unlimited_growth Modus: NUR Zeilen leeren, height behalten!
        if hasattr(self, 'unlimited_growth') and self.unlimited_growth:
            # Leere nur die Zeilen, aber behalte height
            for y in range(len(self.buffer)):
                for x in range(self.width):
                    self.buffer[y][x] = PETSCIIScreenCell(bg_color=self.current_bg)
        else:
            # Normal: Leere fixe Anzahl Zeilen
            for y in range(self.height):
                for x in range(self.width):
                    self.buffer[y][x] = PETSCIIScreenCell(bg_color=self.current_bg)
            self.cursor_x = 0
            self.cursor_y = 0
        
    def home_cursor(self):
        """Setzt Cursor auf Home-Position"""
        self.cursor_x = 0
        self.cursor_y = 0
        
    def move_cursor(self, dx=0, dy=0):
        """Bewegt Cursor relativ"""
        self.cursor_x = max(0, min(self.width - 1, self.cursor_x + dx))
        
        # Im unlimited_growth: Wachsen erlaubt (UNBEGRENZT)
        if hasattr(self, 'unlimited_growth') and self.unlimited_growth:
            new_y = self.cursor_y + dy
            if new_y >= 0:
                self.cursor_y = new_y
                # Stelle sicher dass Buffer groß genug ist
                self._ensure_height(self.cursor_y + 1)
            else:
                self.cursor_y = 0
        else:
            self.cursor_y = max(0, min(self.height - 1, self.cursor_y + dy))
    
    def _ensure_height(self, needed_height):
        """Stellt sicher dass Buffer mindestens needed_height Zeilen hat.
           UNBEGRENZT - Renderer kümmert sich um Viewport!"""
        
        # Füge neue Zeilen hinzu wenn nötig
        while len(self.buffer) < needed_height:
            new_line = [PETSCIIScreenCell(bg_color=self.current_bg) for _ in range(self.width)]
            self.buffer.append(new_line)
            self.height = len(self.buffer)
        
    def set_cursor(self, x, y):
        """Setzt Cursor absolut"""
        self.cursor_x = max(0, min(self.width - 1, x))
        
        # Im unlimited_growth: Wachsen erlaubt (UNBEGRENZT)
        if hasattr(self, 'unlimited_growth') and self.unlimited_growth:
            self.cursor_y = max(0, y)
            self._ensure_height(self.cursor_y + 1)
        else:
            self.cursor_y = max(0, min(self.height - 1, y))
        
    def write_char(self, char):
        """Schreibt ein Zeichen an der Cursor-Position"""
        if self.cursor_x >= self.width:
            self.newline()
        
        # Im unlimited_growth: Stelle sicher dass genug Zeilen existieren
        if hasattr(self, 'unlimited_growth') and self.unlimited_growth:
            self._ensure_height(self.cursor_y + 1)
        
        # Zeichen ist SCREENCODE (konvertiert von PETSCII im Parser)
        screencode_char = char
            
        cell = self.buffer[self.cursor_y][self.cursor_x]
        cell.char = screencode_char
        cell.fg_color = self.current_fg
        cell.bg_color = self.current_bg
        cell.reverse = self.reverse_mode
        
        self.cursor_x += 1
        
        if self.cursor_x >= self.width:
            self.newline()
            
    def newline(self):
        """Führt einen Zeilenumbruch durch (auto-wrap)"""
        self.cursor_x = 0
        self.cursor_y += 1
        
        # KEIN RVS-Reset hier! newline() wird auch bei auto-wrap aufgerufen,
        # und da soll RVS aktiv bleiben (z.B. startup.seq).
        # RVS wird nur bei explizitem CR (0x0D) im Parser zurückgesetzt.
        
        # Im unlimited_growth Modus: Wachsen MIT Limit
        if self.unlimited_growth:
            self._ensure_height(self.cursor_y + 1)
        # Normal: Scrollen wenn am unteren Rand
        elif self.cursor_y >= self.height:
            self.scroll_up()
            self.cursor_y = self.height - 1
            
    def scroll_up(self, lines=1):
        """Scrollt Screen nach oben"""
        for _ in range(lines):
            # Oberste Zeile in Scrollback speichern
            self.scrollback.append(self.buffer[0])
            # Nur limitieren wenn max_scrollback > 0
            if self.max_scrollback > 0 and len(self.scrollback) > self.max_scrollback:
                self.scrollback.pop(0)
                
            # Screen nach oben verschieben
            for y in range(self.height - 1):
                self.buffer[y] = self.buffer[y + 1]
                
            # Neue leere Zeile unten
            self.buffer[self.height - 1] = [
                PETSCIIScreenCell(bg_color=self.current_bg) 
                for _ in range(self.width)
            ]
            
    def delete_char(self):
        """Löscht Zeichen vor Cursor (Backspace)"""
        if self.cursor_x > 0:
            self.cursor_x -= 1
            cell = self.buffer[self.cursor_y][self.cursor_x]
            cell.char = ' '
            cell.fg_color = self.current_fg
            cell.bg_color = self.current_bg
            cell.reverse = False
            
    def insert_char(self):
        """Fügt Leerzeichen an Cursor-Position ein"""
        # Verschiebe restliche Zeile nach rechts
        line = self.buffer[self.cursor_y]
        for x in range(self.width - 1, self.cursor_x, -1):
            line[x] = line[x - 1].copy()
        line[self.cursor_x] = PETSCIIScreenCell(bg_color=self.current_bg)
        
    def get_line(self, y):
        """Gibt eine Zeile als String zurück"""
        if y < 0 or y >= self.height:
            return ""
        return "".join(cell.char for cell in self.buffer[y])
    
    def get_screen_text(self):
        """Gibt kompletten Screen als Text zurück"""
        return "\n".join(self.get_line(y) for y in range(self.height))
    
    def set_background_color(self, color):
        """Setzt die GLOBALE Hintergrundfarbe (wie $D021 beim C64)
        
        CCGMS/Novaterm: CTRL-B + Farbcode ändert den gesamten Screen-Hintergrund!
        """
        if 0 <= color <= 15:
            self.screen_bg = color
            self.current_bg = color  # Auch für neue Zellen
            print(f"[BG] Screen background set to color {color}")
    
    def set_border_color(self, color):
        """Setzt die Border-Farbe (wie $D020 beim C64)"""
        if 0 <= color <= 15:
            self.border_color = color
            print(f"[BORDER] Border color set to {color}")


class PETSCIIParser:
    """
    Parser für PETSCII-Bytestream
    Verarbeitet Control Codes und schreibt in ScreenBuffer
    
    CCGMS/Novaterm kompatibel:
    - CTRL-B ($02) + Farbcode = Hintergrundfarbe ändern
    - CTRL-N ($0E) = Hintergrund auf Schwarz (wird hier als Lowercase/Uppercase verwendet)
    
    Hinweis: Manche BBSe verwenden $03 für BG-Reset statt $0E
    """
    
    def __init__(self, screen_buffer, scrollback_mode=False):
        """
        Args:
            screen_buffer: PETSCIIScreenBuffer Instanz
            scrollback_mode: True = Für Scrollback (Clear Screen → Text)
        """
        self.screen = screen_buffer
        self.scrollback_mode = scrollback_mode
        
        # State für mehrbyte Sequenzen
        self.awaiting_bg_color = False  # Warte auf Hintergrund-Farbcode nach CTRL-B
        
        # Bell-Sequenz Detection: $5C $42 $31 (£B1)
        self.bell_sequence = [0x5C, 0x42, 0x31]
        self.bell_buffer = []
        
        # Callback für Bell - wird von Terminal gesetzt
        self.bell_callback = None
    
    def set_bell_callback(self, callback):
        """Setzt Callback-Funktion für Bell-Sound"""
        self.bell_callback = callback
    
    def _check_bell_sequence(self, byte_val):
        """Prüft ob Bell-Sequenz $03 $07 $08 $21 empfangen wurde.
        
        Returns:
            True wenn Sequenz komplett und Bell ausgelöst wurde
            False wenn Byte normal verarbeitet werden soll
        """
        expected_idx = len(self.bell_buffer)
        
        # Passt das Byte zur erwarteten Position in der Sequenz?
        if expected_idx < len(self.bell_sequence) and byte_val == self.bell_sequence[expected_idx]:
            self.bell_buffer.append(byte_val)
            
            # Sequenz komplett?
            if len(self.bell_buffer) == len(self.bell_sequence):
                print(f"[BELL] Sequence £B1 ($5C $42 $31) detected - playing sound!")
                self.bell_buffer = []  # Reset
                if self.bell_callback:
                    self.bell_callback()
                return True
            return True  # Byte ist Teil der Sequenz, nicht normal verarbeiten
        
        # Byte passt nicht - Buffer verarbeiten und zurücksetzen
        if self.bell_buffer:
            # Die gepufferten Bytes müssen normal verarbeitet werden
            buffered = self.bell_buffer[:]
            self.bell_buffer = []
            print(f"[BELL] Sequence broken, processing buffered bytes: {[hex(b) for b in buffered]}")
            
            # Verarbeite gepufferte Bytes (ohne erneute Sequenz-Prüfung)
            for b in buffered:
                self._parse_byte_internal(b)
        
        # Prüfe ob aktuelles Byte neue Sequenz startet
        if byte_val == self.bell_sequence[0]:
            self.bell_buffer = [byte_val]
            return True
        
        return False  # Byte normal verarbeiten
        
    def parse_byte(self, byte_val):
        """
        Verarbeitet ein einzelnes PETSCII-Byte
        """
        # Zuerst: Bell-Sequenz prüfen ($03 $07 $08 $21)
        if self._check_bell_sequence(byte_val):
            return  # Byte ist Teil der Bell-Sequenz
        
        # Normale Verarbeitung
        self._parse_byte_internal(byte_val)
    
    def _parse_byte_internal(self, byte_val):
        """
        Interne Byte-Verarbeitung (ohne Bell-Sequenz-Check)
        Logic wie in seqconvert.py: erst Control-Codes, dann direkt mappen
        
        Args:
            byte_val: Byte-Wert (0-255)
        """
        
        # ============================================================
        # CTRL-B ($02) Hintergrundfarbe - zweites Byte verarbeiten
        # ============================================================
        if self.awaiting_bg_color:
            self.awaiting_bg_color = False
            
            # Prüfe ob es ein gültiger Farbcode ist
            if is_color_code(byte_val):
                color = get_color_number(byte_val)
                print(f"[CTRL-B] Background color code 0x{byte_val:02X} -> color {color}")
                self.screen.set_background_color(color)
                return
            else:
                # Kein Farbcode - verarbeite Byte normal weiter
                print(f"[CTRL-B] 0x{byte_val:02X} is NOT a color code, processing normally")
                # Fall-through zur normalen Verarbeitung
        
        # ============================================================
        # CTRL-B ($02) - Hintergrundfarbe folgt als nächstes Byte
        # ============================================================
        if byte_val == 0x02:
            print(f"[CTRL-B] Received, waiting for color code...")
            self.awaiting_bg_color = True
            return
        
        # ============================================================
        # CTRL-N ($0E) - Lowercase charset (original)
        # Manche BBSe nutzen $03 für BG-Reset, wir unterstützen beides
        # ============================================================
        # $0E ist original für Lowercase - bleibt so!
        # Siehe unten bei 0x0E
        
        # ============================================================
        # CTRL-C ($03) - Hintergrund auf Schwarz (manche BBSe)
        # ============================================================
        if byte_val == 0x03:
            self.screen.set_background_color(0)  # Schwarz
            return
        
        # ============================================================
        # CTRL-G ($07) - Bell / Beep - Sound abspielen
        # ============================================================
        if byte_val == 0x07:
            if self.bell_callback:
                self.bell_callback()
            return
        
        # Carriage Return / Line Feed
        if byte_val in (0x0D, 0x8D):
            # RVS bei CR zurücksetzen - Standard für BBS-Terminals!
            # Wichtig: NUR bei explizitem CR, NICHT bei auto-wrap!
            self.screen.reverse_mode = False
            self.screen.newline()
            return
            
        # HOME (0x13)
        if byte_val == 0x13:
            if self.scrollback_mode:
                # Im Scrollback: HOME ignorieren (würde alte Daten überschreiben!)
                pass
            else:
                self.screen.home_cursor()
            return
            
        # CLEAR SCREEN (0x93)
        if byte_val == 0x93:
            if self.scrollback_mode:
                # Im Scrollback: Zeige Text statt Clear
                self.screen.newline()
                
                # Setze Farbe auf Weiß
                old_fg = self.screen.current_fg
                self.screen.current_fg = 5  # Weiß
                
                # Schreibe Text
                separator = "---- CLR ----"
                for char in separator:
                    self.screen.write_char(ord(char))
                
                # Restore Farbe
                self.screen.current_fg = old_fg
                self.screen.newline()
            else:
                # Normal: Clear Screen
                self.screen.clear_screen()
            return
            
        # Cursor Movement
        if byte_val == 0x11:  # CRSR DOWN
            self.screen.move_cursor(dy=1)
            return
        if byte_val == 0x91:  # CRSR UP
            self.screen.move_cursor(dy=-1)
            return
        if byte_val == 0x1D:  # CRSR RIGHT
            self.screen.move_cursor(dx=1)
            return
        if byte_val == 0x9D:  # CRSR LEFT
            self.screen.move_cursor(dx=-1)
            return
            
        # DELETE / INSERT
        if byte_val == 0x14:  # DEL
            self.screen.delete_char()
            return
        if byte_val == 0x94:  # INS
            self.screen.insert_char()
            return
            
        # Charset-Umschaltung
        if byte_val == 0x0E:  # CHR$(14): lower/upper
            self.screen.charset_mode = 'lower'
            return
        if byte_val == 0x8E:  # CHR$(142): upper/graphics
            self.screen.charset_mode = 'upper'
            return
            
        # Reverse Video
        if byte_val == 0x12:  # RVS ON
            self.screen.reverse_mode = True
            return
        if byte_val == 0x92:  # RVS OFF
            self.screen.reverse_mode = False
            return
            
        # Farbcodes (Vordergrund)
        if is_color_code(byte_val):
            self.screen.current_fg = get_color_number(byte_val)
            return
            
        # Andere Steuerzeichen (<0x20) ignorieren (außer CR/LF)
        if byte_val < 0x20 and byte_val not in (0x0D, 0x8D):
            return
            
        # DRUCKBARE Zeichen (wie CGTerm kernal.c Zeile 204):
        # (a >= 32 && a <= 127) || (a >= 160)
        # = 0x20-0x7F oder 0xA0-0xFF
        if (0x20 <= byte_val <= 0x7F) or (byte_val >= 0xA0):
            # PETSCII -> SCREENCODE konvertieren, dann speichern
            screencode = petscii_to_screencode(byte_val)
            self.screen.write_char(chr(screencode))
        # ALLE anderen Bytes (0x00-0x1F, 0x80-0x9F) sind Control-Codes
        # und werden oben bereits behandelt
            
    def parse_bytes(self, data):
        """
        Verarbeitet mehrere Bytes
        
        Args:
            data: bytes oder bytearray
        """
        for byte_val in data:
            self.parse_byte(byte_val)


if __name__ == "__main__":
    # Test
    screen = PETSCIIScreenBuffer(40, 25)
    parser = PETSCIIParser(screen)
    
    # Test-Daten mit Hintergrundfarbe
    test_data = bytearray([
        0x93,  # CLR
        0x05,  # WHITE (Vordergrund)
        0x02, 0x1C,  # CTRL-B + RED = Roter Hintergrund
        ord('H'), ord('E'), ord('L'), ord('L'), ord('O'),
        0x0D,  # CR
        0x03,  # CTRL-C = Hintergrund auf Schwarz
        0x1C,  # RED (Vordergrund)
        ord('W'), ord('O'), ord('R'), ord('L'), ord('D'),
    ])
    
    parser.parse_bytes(test_data)
    print(screen.get_screen_text())
    print(f"\nScreen size: {screen.width}x{screen.height}")
    print(f"Max height: {screen.max_height}")
