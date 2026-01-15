"""
PETSCII Parser und Screen Buffer
Verarbeitet PETSCII-Stream und verwaltet virtuellen C64-Screen
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
    Virtueller C64 Screen Buffer (40x25)
    Verwaltet Zeichen, Farben und Cursor-Position
    """
    
    def __init__(self, width=40, height=25):
        self.width = width
        self.height = height  # Initial height, kann wachsen!
        self.cursor_x = 0
        self.cursor_y = 0
        self.current_fg = 14  # Light Blue (default C64)
        self.current_bg = 0   # Black (schwarz statt blau)
        self.reverse_mode = False
        self.charset_mode = 'lower'  # DEFAULT: 'lower' für BBS (nicht 'upper')
        
        # Screen buffer als 2D-Array (dynamisch!)
        self.buffer = [[PETSCIIScreenCell(bg_color=self.current_bg) 
                       for _ in range(width)] 
                       for _ in range(height)]
        
        # Scrollback buffer für gescrollte Zeilen
        self.scrollback = []
        self.max_scrollback = 500
        
        # Flag für unbegrenztes Wachstum (Scrollback Mode)
        self.unlimited_growth = False
        
    def clear_screen(self):
        """Löscht den Screen"""
        # Im unlimited_growth Modus: NUR Zeilen leeren, height behalten!
        if hasattr(self, 'unlimited_growth') and self.unlimited_growth:
            # Leere nur die Zeilen, aber behalte height
            for y in range(len(self.buffer)):
                for x in range(self.width):
                    self.buffer[y][x] = PETSCIIScreenCell(bg_color=self.current_bg)
            # Cursor NUR bei echtem Clear Screen zurücksetzen
            # Im Scrollback wird das nie aufgerufen weil wir Text schreiben
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
        
        # Im unlimited_growth: Nicht clampen, buffer wächst!
        if hasattr(self, 'unlimited_growth') and self.unlimited_growth:
            self.cursor_y = max(0, self.cursor_y + dy)
        else:
            self.cursor_y = max(0, min(self.height - 1, self.cursor_y + dy))
        
    def set_cursor(self, x, y):
        """Setzt Cursor absolut"""
        self.cursor_x = max(0, min(self.width - 1, x))
        
        # Im unlimited_growth: Nicht clampen, buffer wächst!
        if hasattr(self, 'unlimited_growth') and self.unlimited_growth:
            self.cursor_y = max(0, y)
        else:
            self.cursor_y = max(0, min(self.height - 1, y))
        
    def write_char(self, char):
        """Schreibt ein Zeichen an der Cursor-Position"""
        if self.cursor_x >= self.width:
            self.newline()
        
        # Im unlimited_growth: Stelle sicher dass genug Zeilen existieren
        if hasattr(self, 'unlimited_growth') and self.unlimited_growth:
            while self.cursor_y >= len(self.buffer):
                # Füge neue Zeile hinzu
                new_line = [PETSCIIScreenCell(bg_color=self.current_bg) for _ in range(self.width)]
                self.buffer.append(new_line)
                self.height += 1
        
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
        """Führt einen Zeilenumbruch durch"""
        self.cursor_x = 0
        self.cursor_y += 1
        
        # RVS-Reset entfernt - verursacht Probleme mit startup.seq
        # In echtem C64 bleibt RVS an bis explizit ausgeschaltet
        # self.reverse_mode = False
        
        # Im unlimited_growth Modus: Füge neue Zeile hinzu statt scrollen
        if self.unlimited_growth and self.cursor_y >= self.height:
            # Füge neue Zeile hinzu
            new_line = [PETSCIIScreenCell(bg_color=self.current_bg) for _ in range(self.width)]
            self.buffer.append(new_line)
            self.height += 1
            # Cursor bleibt auf neuer Zeile
        # Normal: Scrollen wenn am unteren Rand
        elif self.cursor_y >= self.height:
            self.scroll_up()
            self.cursor_y = self.height - 1
            
    def scroll_up(self, lines=1):
        """Scrollt Screen nach oben"""
        for _ in range(lines):
            # Oberste Zeile in Scrollback speichern
            self.scrollback.append(self.buffer[0])
            if len(self.scrollback) > self.max_scrollback:
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


class PETSCIIParser:
    """
    Parser für PETSCII-Bytestream
    Verarbeitet Control Codes und schreibt in ScreenBuffer
    """
    
    def __init__(self, screen_buffer, scrollback_mode=False):
        """
        Args:
            screen_buffer: PETSCIIScreenBuffer Instanz
            scrollback_mode: True = Für Scrollback (Clear Screen → Text)
        """
        self.screen = screen_buffer
        self.scrollback_mode = scrollback_mode
        
    def parse_byte(self, byte_val):
        """
        Verarbeitet ein einzelnes PETSCII-Byte
        Logic wie in seqconvert.py: erst Control-Codes, dann direkt mappen
        
        Args:
            byte_val: Byte-Wert (0-255)
        """
        # Carriage Return / Line Feed
        if byte_val in (0x0D, 0x8D):
            self.screen.newline()
            return
            
        # HOME (0x13)
        if byte_val == 0x13:
            if self.scrollback_mode:
                # Im Scrollback: HOME ignorieren (würde alte Daten überschreiben!)
                # Alternativ: Behandle wie newline
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
                    # Konvertiere ASCII zu PETSCII/Screencode
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
            
        # Farbcodes
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
    
    # Test-Daten
    test_data = bytearray([
        0x93,  # CLR
        0x05,  # WHITE
        ord('H'), ord('E'), ord('L'), ord('L'), ord('O'),
        0x0D,  # CR
        0x1C,  # RED
        ord('W'), ord('O'), ord('R'), ord('L'), ord('D'),
    ])
    
    parser.parse_bytes(test_data)
    print(screen.get_screen_text())
