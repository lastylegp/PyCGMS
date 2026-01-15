"""
Tools Module für PYCGMS Terminal
Enthält Funktionen für D64/D71/D81 Disk Image Operationen
"""

import os
from pathlib import Path


class DiskImageViewer:
    """Liest und rendert D64/D71/D81 Disk Image Directories"""
    
    # Track Offset Tabellen für verschiedene Disk-Formate
    D64_TRACK_OFFSETS = [
        0,      # Track 1 starts at 0
        0x1500, 0x2A00, 0x3F00, 0x5400, 0x6900, 0x7E00, 0x9300, 0xA800,
        0xBD00, 0xD200, 0xE700, 0xFC00, 0x11100, 0x12600, 0x13B00, 0x15000,
        0x16500,  # Track 18 (Directory)
    ]
    
    def __init__(self, filepath):
        self.filepath = Path(filepath)
        self.disk_name = ""
        self.disk_id = ""
        self.entries = []
        self.blocks_free = 0
        
    def read_directory(self):
        """Liest Directory direkt aus dem Disk Image"""
        ext = self.filepath.suffix.lower()
        
        if ext == '.d64':
            return self._read_d64()
        elif ext == '.d71':
            return self._read_d71()
        elif ext == '.d81':
            return self._read_d81()
        elif ext in ('.d2m', '.d4m', '.dnp'):
            return self._read_cmd_native()
        else:
            raise ValueError(f"Unbekanntes Format: {ext}")
    
    def _read_d64(self):
        """Liest D64 Directory (Track 18)"""
        with open(self.filepath, 'rb') as f:
            # BAM ist Track 18, Sector 0
            # Bei D64: Track 18 beginnt bei Offset 0x16500
            bam_offset = 0x16500
            
            f.seek(bam_offset)
            bam = f.read(256)
            
            # Disk Name bei Offset 0x90 (16 bytes)
            self.disk_name = bam[0x90:0x90+16]
            # Disk ID bei Offset 0xA2 (5 bytes: ID + 0xA0 + DOS Type)
            self.disk_id = bam[0xA2:0xA7]
            
            # Blocks Free berechnen aus BAM
            self.blocks_free = self._count_free_blocks_d64(bam)
            
            # Directory Entries lesen (ab Sector 1)
            self.entries = []
            
            # Erste Directory-Zeile: Header mit Disk Name
            header_line = self._format_header_line()
            self.entries.append(header_line)
            
            # Directory Chain folgen
            next_track = bam[0]  # Normalerweise 18
            next_sector = bam[1]  # Normalerweise 1
            
            while next_track != 0:
                sector_offset = self._get_sector_offset_d64(next_track, next_sector)
                f.seek(sector_offset)
                sector_data = f.read(256)
                
                # Nächster Sector in Chain
                next_track = sector_data[0]
                next_sector = sector_data[1]
                
                # 8 Directory Entries pro Sector (je 32 bytes)
                for i in range(8):
                    entry_offset = i * 32
                    entry = sector_data[entry_offset:entry_offset+32]
                    
                    file_type = entry[2]
                    if file_type != 0:  # Gültiger Eintrag
                        line = self._format_dir_entry(entry)
                        self.entries.append(line)
            
            # Blocks Free Zeile
            free_line = self._format_blocks_free()
            self.entries.append(free_line)
            
        return self.entries
    
    def _read_d71(self):
        """Liest D71 Directory (doppelseitig)"""
        with open(self.filepath, 'rb') as f:
            # D71: 70 Tracks (35 + 35), wie D64 aber doppelseitig
            # BAM ist Track 18, Sector 0
            bam_offset = 0x16500
            
            f.seek(bam_offset)
            bam = f.read(256)
            
            # Disk Name bei Offset 0x90 (16 bytes)
            self.disk_name = bam[0x90:0x90+16]
            self.disk_id = bam[0xA2:0xA7]
            
            # Blocks Free für Side 1 (Tracks 1-35, ohne Track 18)
            free_side1 = 0
            for i in range(35):
                if i == 17:  # Track 18 = Directory
                    continue
                free_side1 += bam[4 + i*4]
            
            # Blocks Free für Side 2 (Tracks 36-70)
            # Bei D71 ist die Side 2 BAM ab Offset 0xDD (221) im gleichen Sector
            # 35 Bytes, je 1 Byte = freie Sectors pro Track
            free_side2 = 0
            for i in range(35):
                if i == 17:  # Track 53 = Directory Side 2 (optional)
                    continue
                free_side2 += bam[0xDD + i]
            
            self.blocks_free = free_side1 + free_side2
            
            # Directory Entries
            self.entries = []
            header_line = self._format_header_line()
            self.entries.append(header_line)
            
            # Directory Chain folgen (wie D64)
            next_track = bam[0]
            next_sector = bam[1]
            
            while next_track != 0:
                sector_offset = self._get_sector_offset_d71(next_track, next_sector)
                f.seek(sector_offset)
                sector_data = f.read(256)
                
                next_track = sector_data[0]
                next_sector = sector_data[1]
                
                for i in range(8):
                    entry_offset = i * 32
                    entry = sector_data[entry_offset:entry_offset+32]
                    
                    file_type = entry[2]
                    if file_type != 0:
                        line = self._format_dir_entry(entry)
                        self.entries.append(line)
            
            free_line = self._format_blocks_free()
            self.entries.append(free_line)
            
        return self.entries
    
    def _get_sector_offset_d71(self, track, sector):
        """Berechnet Byte-Offset für Track/Sector in D71"""
        # D71: 70 Tracks, gleiche Struktur wie D64 pro Seite
        if track <= 35:
            return self._get_sector_offset_d64(track, sector)
        else:
            # Side 2: Track 36-70
            # Offset = komplette Side 1 + Position auf Side 2
            side1_size = 174848  # D64 Größe
            track_on_side2 = track - 35
            return side1_size + self._get_sector_offset_d64(track_on_side2, sector)
    
    def _read_d81(self):
        """Liest D81 Directory (Track 40)"""
        with open(self.filepath, 'rb') as f:
            # D81: 80 Tracks, 40 Sectors pro Track, 256 bytes pro Sector
            # Header auf Track 40, Sector 0
            # BAM auf Track 40, Sectors 1 und 2
            
            header_offset = (40 - 1) * 40 * 256  # Track 40, Sector 0
            
            f.seek(header_offset)
            header = f.read(256)
            
            # Disk Name bei Offset 0x04
            self.disk_name = header[0x04:0x04+16]
            self.disk_id = header[0x16:0x1B]
            
            # BAM Sector 1 (Track 40, Sector 1) - Tracks 1-40
            f.seek(header_offset + 256)
            bam1 = f.read(256)
            
            # BAM Sector 2 (Track 40, Sector 2) - Tracks 41-80
            f.seek(header_offset + 512)
            bam2 = f.read(256)
            
            # Zähle freie Blocks
            # Format: 6 Bytes pro Track, Byte 0 = Anzahl freier Sectors
            self.blocks_free = 0
            
            # BAM 1: Tracks 1-40 (ab Offset 0x10)
            for i in range(40):
                if i == 39:  # Track 40 = Directory
                    continue
                offset = 0x10 + i * 6
                if offset < len(bam1):
                    self.blocks_free += bam1[offset]
            
            # BAM 2: Tracks 41-80 (ab Offset 0x10)
            for i in range(40):
                offset = 0x10 + i * 6
                if offset < len(bam2):
                    self.blocks_free += bam2[offset]
            
            self.entries = []
            header_line = self._format_header_line()
            self.entries.append(header_line)
            
            # Directory ab Track 40, Sector 3
            next_track = 40
            next_sector = 3
            
            while next_track != 0:
                sector_offset = (next_track - 1) * 40 * 256 + next_sector * 256
                f.seek(sector_offset)
                sector_data = f.read(256)
                
                next_track = sector_data[0]
                next_sector = sector_data[1]
                
                for i in range(8):
                    entry_offset = i * 32
                    entry = sector_data[entry_offset:entry_offset+32]
                    
                    file_type = entry[2]
                    if file_type != 0:
                        line = self._format_dir_entry(entry)
                        self.entries.append(line)
            
            free_line = self._format_blocks_free()
            self.entries.append(free_line)
            
        return self.entries
    
    def _read_cmd_native(self):
        """Liest CMD FD2000/FD4000 D2M/D4M/DNP Directory
        
        Format:
        - D2M: 81 Tracks × 80 Sectors = 1,658,880 bytes
        - D4M: 161 Tracks × 80 Sectors = 3,276,800 bytes  
        - DNP: Variable (Native Partition)
        - Header bei Sector 1
        - Directory als linked list
        """
        with open(self.filepath, 'rb') as f:
            data = f.read()
        
        sector_size = 256
        total_sectors = len(data) // sector_size
        
        def get_sector(sector_num):
            offset = sector_num * sector_size
            return data[offset:offset+sector_size] if offset + sector_size <= len(data) else None
        
        def is_valid_filename(fname):
            """Prüft ob Filename gültige PETSCII-Zeichen enthält"""
            for b in fname:
                if b == 0xA0 or b == 0x00:  # Shifted space / Null
                    continue
                if 0x20 <= b <= 0x5F:  # Standard printable
                    continue
                if 0xA1 <= b <= 0xBF:  # Shifted printable
                    continue
                if 0xC0 <= b <= 0xDF:  # Shifted graphics
                    continue
                return False
            return True
        
        # Header bei Sector 1
        header = get_sector(1)
        if not header:
            raise ValueError("Cannot read header")
        
        # Header Format:
        # Byte 0-1: Link zum ersten Directory-Sektor
        # Byte 2: 0x48 (Header Type)
        # Byte 4-19: Disk Name (16 bytes)
        # Byte 21-25: Disk ID
        self.disk_name = header[4:20]
        self.disk_id = header[21:26]
        
        # Gültige File Types
        valid_types = {0x80, 0x81, 0x82, 0x83, 0x84,  # Normal
                       0xC0, 0xC1, 0xC2, 0xC3, 0xC4}  # Locked
        
        # Scanne alle Sektoren nach Directory-Einträgen
        # (robuster als Chain-Following bei fragmentierten Images)
        all_raw_entries = []
        seen_names = set()
        
        for sector_num in range(total_sectors):
            sector_data = get_sector(sector_num)
            if not sector_data:
                continue
            
            # Zähle gültige Einträge im Sektor
            sector_entries = []
            for i in range(8):
                entry = sector_data[i*32:(i+1)*32]
                file_type = entry[2]
                
                if file_type in valid_types:
                    fname = entry[5:21]
                    if is_valid_filename(fname):
                        sector_entries.append(entry)
            
            # Mindestens 3 gültige Einträge = Directory-Sektor
            if len(sector_entries) >= 3:
                for entry in sector_entries:
                    # Deduplizierung basierend auf Dateiname
                    fname = bytes(entry[5:21])
                    if fname not in seen_names:
                        seen_names.add(fname)
                        all_raw_entries.append(entry)
        
        # BAM für Blocks Free (aus Header-Sektor 0)
        self.blocks_free = self._calc_blocks_free_cmd(data)
        
        # Directory Entries formatieren
        self.entries = []
        header_line = self._format_header_line()
        self.entries.append(header_line)
        
        for entry in all_raw_entries:
            line = self._format_dir_entry(entry)
            self.entries.append(line)
        
        # Blocks Free Zeile
        free_line = self._format_blocks_free()
        self.entries.append(free_line)
        
        return self.entries
    
    def _calc_blocks_free_cmd(self, data):
        """Berechnet Blocks Free für CMD Native Format aus BAM"""
        sector_size = 256
        total_sectors = len(data) // sector_size
        
        # BAM bei Sector 0 und weitere BAM-Sektoren
        # Vereinfachte Berechnung: Total Sektoren minus geschätzte benutzte
        # (echte BAM-Berechnung wäre komplex)
        
        # Zähle benutzte Sektoren (nicht-null)
        used = 0
        for i in range(total_sectors):
            sector = data[i*sector_size:(i+1)*sector_size]
            if any(b != 0 for b in sector):
                used += 1
        
        # Grobe Schätzung: Total - Used - BAM/System Overhead
        free = total_sectors - used
        if free < 0:
            free = 0
        
        return free
    
    
    def _get_sector_offset_d64(self, track, sector):
        """Berechnet Byte-Offset für Track/Sector in D64"""
        # Tracks 1-17: 21 Sectors
        # Tracks 18-24: 19 Sectors
        # Tracks 25-30: 18 Sectors
        # Tracks 31-35: 17 Sectors
        
        offset = 0
        for t in range(1, track):
            if t <= 17:
                offset += 21 * 256
            elif t <= 24:
                offset += 19 * 256
            elif t <= 30:
                offset += 18 * 256
            else:
                offset += 17 * 256
        
        offset += sector * 256
        return offset
    
    def _count_free_blocks_d64(self, bam):
        """Zählt freie Blocks aus BAM (ohne Track 18 = Directory)"""
        free = 0
        # BAM Entries für Tracks 1-35 ab Offset 4
        for i in range(35):
            if i == 17:  # Track 18 (Index 17) = Directory Track - nicht mitzählen
                continue
            free += bam[4 + i*4]  # Erstes Byte jedes 4-Byte Eintrags = freie Sectors
        return free
    
    def _format_header_line(self):
        """Formatiert die Header-Zeile mit Disk Name"""
        # Format: 0 "DISKNAME        " ID DOS
        # " startet an Position 2 (3. Zeichen)
        line = bytearray()
        
        # "0" am Anfang
        line.append(0x30)  # '0'
        
        # Padding bis Position 2
        while len(line) < 2:
            line.append(0x20)  # Space
        
        # Anführungszeichen an Position 2
        line.append(0x22)  # "
        
        # Disk Name (16 chars)
        for b in self.disk_name[:16]:
            line.append(self._normalize_petscii(b))
        
        # Anführungszeichen
        line.append(0x22)  # "
        line.append(0x20)  # Space
        
        # Disk ID (5 chars)
        for b in self.disk_id[:5]:
            line.append(self._normalize_petscii(b))
        
        return bytes(line)
    
    def _normalize_petscii(self, b):
        """Normalisiert ein Byte zu gültigem PETSCII für Uppercase Anzeige
        
        - ASCII lowercase ($61-$7A / a-z) -> PETSCII uppercase ($41-$5A / A-Z)
        - Shifted space ($A0) -> Space ($20)
        - Andere Bytes bleiben unverändert (inkl. Grafik-Zeichen)
        """
        if b == 0xA0:  # Shifted space
            return 0x20
        elif 0x61 <= b <= 0x7A:  # ASCII lowercase a-z
            return b - 0x20  # -> PETSCII uppercase A-Z ($41-$5A)
        else:
            return b
    
    def _format_dir_entry(self, entry):
        """Formatiert einen Directory-Eintrag"""
        # Format: BBB  "FILENAME        " TYP< (< = locked)
        # Blocks linksbündig, " startet an Position 5 (6. Zeichen)
        line = bytearray()
        
        raw_file_type = entry[2]
        file_type = raw_file_type & 0x0F
        locked = (raw_file_type & 0xC0) == 0xC0  # 0xC0-0xC4 = locked
        blocks = entry[0x1E] + entry[0x1F] * 256
        filename = entry[5:5+16]
        
        # Blocks (linksbündig)
        blocks_str = str(blocks)
        for ch in blocks_str:
            line.append(ord(ch))
        
        # Padding bis Position 5 (für ")
        while len(line) < 5:
            line.append(0x20)  # Space
        
        # Anführungszeichen an Position 5
        line.append(0x22)  # "
        
        # Filename (16 chars) - KEINE Normalisierung!
        # $60-$7F sind Grafik-Zeichen in echten C64 Directories
        for b in filename:
            if b == 0xA0:  # Nur Shifted Space -> Space
                line.append(0x20)
            else:
                line.append(b)
        
        # Anführungszeichen
        line.append(0x22)  # "
        
        # Space vor File Type
        line.append(0x20)
        
        # File Type
        type_names = {0: 'DEL', 1: 'SEQ', 2: 'PRG', 3: 'USR', 4: 'REL'}
        type_str = type_names.get(file_type, '???')
        for ch in type_str:
            line.append(ord(ch))
        
        # Locked Marker
        if locked:
            line.append(ord('<'))
        
        return bytes(line)
    
    def _format_blocks_free(self):
        """Formatiert die 'BLOCKS FREE' Zeile"""
        line = bytearray()
        
        blocks_str = str(self.blocks_free)
        for ch in blocks_str:
            line.append(ord(ch))
        
        line.append(0x20)  # Space
        
        for ch in "BLOCKS FREE.":
            line.append(ord(ch))
        
        return bytes(line)


def petscii_to_screencode(petscii_byte):
    """
    Konvertiert ein PETSCII Byte zu Screen Code für C64 Font Rendering (Upper Font)
    
    PETSCII Bereiche:
    $00-$1F: Control codes -> Space
    $20-$3F: Zahlen, Sonderzeichen -> Screen $20-$3F
    $40-$5F: @ A-Z [ \\ ] ^ _ -> Screen $00-$1F
    $60-$7F: lowercase/graphics -> Screen $40-$5F
    $80-$9F: Control codes -> Space
    $A0-$BF: Shifted graphics -> Screen $60-$7F
    $C0-$DF: CBM Graphics -> Screen $40-$5F (Dir-Art!)
    $E0-$FF: Shifted graphics -> Screen $60-$7F
    """
    b = petscii_byte & 0xFF
    
    if b < 0x20:
        return 0x20  # Control -> Space
    elif b < 0x40:
        return b  # $20-$3F -> identisch
    elif b < 0x60:
        return b - 0x40  # $40-$5F -> $00-$1F (@ A-Z)
    elif b < 0x80:
        return b - 0x20  # $60-$7F -> $40-$5F
    elif b < 0xA0:
        return 0x20  # Control -> Space
    elif b < 0xC0:
        return b - 0x40  # $A0-$BF -> $60-$7F (shifted graphics)
    elif b < 0xE0:
        return b - 0x80  # $C0-$DF -> $40-$5F (CBM graphics)
    else:
        return b - 0x80  # $E0-$FF -> $60-$7F


def petscii_to_screencode_lower(petscii_byte):
    """
    Konvertiert ein PETSCII Byte zu Screen Code für Lower Font
    
    Im Lower Font:
    - Screen $00-$1F: @ a-z (Kleinbuchstaben) 
    - Screen $40-$5F: Grafik-Zeichen
    - Screen $60-$7F: Grafik-Zeichen
    
    Transformation:
    - Buchstaben A-Z ($41-$5A) -> Screen $01-$1A (Kleinbuchstaben a-z)
    - Grafik ($60-$7F, $A0-$BF, $C0-$DF) -> Screen $60-$7F
    """
    b = petscii_byte & 0xFF
    
    if b < 0x20:
        return 0x20  # Control -> Space
    elif b < 0x40:
        return b  # $20-$3F -> identisch (Zahlen, Sonderzeichen)
    elif b < 0x60:
        # $40-$5F: @ A-Z -> Screen $00-$1F (Kleinbuchstaben im lower font!)
        return b - 0x40  # $41 (A) -> $01 (a), etc.
    elif b < 0x80:
        # $60-$7F: Grafik -> Screen $60-$7F
        return b  # Grafik bleibt bei $60-$7F
    elif b < 0xA0:
        return 0x20  # Control -> Space
    elif b < 0xC0:
        # $A0-$BF: Shifted graphics -> Screen $60-$7F
        return b - 0x40
    elif b < 0xE0:
        # $C0-$DF: CBM Graphics -> Screen $60-$7F
        return b - 0x60
    else:
        # $E0-$FF: Grafik
        return b - 0x80  # -> $60-$7F


def render_directory_to_image(entries, font_path, zoom=2, 
                              bg_color=(63, 63, 215),    # C64 Blau
                              fg_color=(255, 255, 255)): # Weiß
    """
    Rendert Directory-Einträge zu einem PIL Image mit C64 Font
    
    Die erste Zeile (Header) wird ab Position 2 (nach "0 ") invertiert gerendert.
    
    Args:
        entries: Liste von bytes (PETSCII Zeilen)
        font_path: Pfad zu upper.bmp oder lower.bmp
        zoom: Vergrößerungsfaktor (default 2)
        bg_color: Hintergrundfarbe
        fg_color: Textfarbe
    
    Returns:
        PIL.Image
    """
    from PIL import Image
    
    if not os.path.exists(font_path):
        raise FileNotFoundError(f"Font nicht gefunden: {font_path}")
    
    # Bestimme ob Lower Font verwendet wird
    use_lower_font = 'lower' in os.path.basename(font_path).lower()
    
    # Wähle die passende Konvertierungsfunktion
    convert_func = petscii_to_screencode_lower if use_lower_font else petscii_to_screencode
    
    # Lade Font
    font_img = Image.open(font_path).convert('L')
    
    # Font Parameter
    char_width = 8
    char_height = 8
    chars_per_row = 32
    
    # Berechne Bildgröße
    max_line_len = max(len(line) for line in entries) if entries else 40
    max_line_len = max(max_line_len, 40)
    num_lines = len(entries)
    
    img_width = max_line_len * char_width * zoom
    img_height = num_lines * char_height * zoom
    
    # Erstelle Bild
    screen_img = Image.new('RGB', (img_width, img_height), color=bg_color)
    
    # Rendere jede Zeile
    y_pos = 0
    line_index = 0
    
    for line_bytes in entries:
        x_pos = 0
        char_index = 0
        
        for petscii_byte in line_bytes:
            # PETSCII zu Screen Code konvertieren (abhängig vom Font)
            screen_code = convert_func(petscii_byte)
            
            # Position im Font
            font_col = screen_code % chars_per_row
            font_row = screen_code // chars_per_row
            
            # Extrahiere Zeichen aus Font
            left = font_col * char_width
            top = font_row * char_height
            char_img = font_img.crop((left, top, left + char_width, top + char_height))
            
            # Skaliere
            if zoom != 1:
                char_img = char_img.resize(
                    (char_width * zoom, char_height * zoom), 
                    Image.Resampling.NEAREST
                )
            
            # Header-Zeile (erste Zeile) ab Position 2 (dem ") invertiert rendern
            is_reversed = (line_index == 0 and char_index >= 2)
            
            # Farben für dieses Zeichen
            if is_reversed:
                char_fg = bg_color  # Invertiert
                char_bg = fg_color
            else:
                char_fg = fg_color
                char_bg = bg_color
            
            # Zeichne Pixel
            for py in range(char_img.height):
                for px in range(char_img.width):
                    pixel = char_img.getpixel((px, py))
                    if pixel > 128:  # Vordergrund
                        screen_img.putpixel((x_pos + px, y_pos + py), char_fg)
                    elif is_reversed:  # Hintergrund nur bei reversed zeichnen
                        screen_img.putpixel((x_pos + px, y_pos + py), char_bg)
            
            x_pos += char_width * zoom
            char_index += 1
        
        y_pos += char_height * zoom
        line_index += 1
    
    return screen_img
    
    return screen_img


# Legacy Funktion für Kompatibilität mit showd64.py
def show_d64_directory(filepath):
    """
    Zeigt D64 Directory (Kompatibilität mit showd64.py)
    
    Returns:
        Liste von Strings für Konsolen-Ausgabe
    """
    try:
        from d64 import DiskImage
        
        lines = []
        with DiskImage(str(filepath)) as image:
            for entry in image.directory():
                lines.append(str(entry))
        return lines
    except ImportError:
        # Fallback auf eigene Implementation
        viewer = DiskImageViewer(filepath)
        entries = viewer.read_directory()
        return [entry.decode('latin-1', errors='replace') for entry in entries]


# ============================================================================
# ZipCode <-> D64 Konverter Funktionen
# ============================================================================

# Track ranges for each of the 4 ZipCode files
ZIPCODE_TRACK_RANGES = (
    (1, 8),    # File 1
    (9, 16),   # File 2
    (17, 25),  # File 3
    (26, 35),  # File 4
)

# Sectors per track zone: (num_sectors, (start_track, end_track))
ZIPCODE_TRACK_SECTOR_MAX = (
    (21, (1, 17)),   # Tracks 1-17: 21 sectors (0-20)
    (19, (18, 24)),  # Tracks 18-24: 19 sectors (0-18)
    (18, (25, 30)),  # Tracks 25-30: 18 sectors (0-17)
    (17, (31, 35)),  # Tracks 31-35: 17 sectors (0-16)
)

# End marker for ZipCode files
ZIPCODE_END_MARKER_TRACK = 26
ZIPCODE_END_MARKER_SECTOR = 26

# Final track/sector for each ZipCode file
ZIPCODE_FINAL_TS = ((8, 10), (16, 10), (25, 17), (35, 8))


def _zipcode_get_sectors_for_track(track):
    """Return number of sectors for a given track."""
    for sectors, track_range in ZIPCODE_TRACK_SECTOR_MAX:
        if track_range[0] <= track <= track_range[1]:
            return sectors
    return 0


def _zipcode_get_interleave_for_track(track):
    """Return interleave value for a given track."""
    num_sectors = _zipcode_get_sectors_for_track(track)
    return (num_sectors + 1) // 2


def _zipcode_get_sector_order(track):
    """Return sector order with interleave for a given track."""
    num_sectors = _zipcode_get_sectors_for_track(track)
    interleave = _zipcode_get_interleave_for_track(track)
    
    order = []
    for i in range(interleave):
        order.append(i)
        if i + interleave < num_sectors:
            order.append(i + interleave)
    
    return order


def _zipcode_block_start(track, sector):
    """Calculate byte offset in D64 for given track/sector."""
    import struct
    sector_start = 0
    for sectors, track_range in ZIPCODE_TRACK_SECTOR_MAX:
        if track > track_range[1]:
            sector_start += (track_range[1] - track_range[0] + 1) * sectors
        else:
            sector_start += (track - track_range[0]) * sectors
            sector_start += sector
            break
    return sector_start * 0x100


def _zipcode_read_block(image_fp, track, sector):
    """Read a 256-byte block from the D64 image."""
    pos = _zipcode_block_start(track, sector)
    image_fp.seek(pos)
    data = image_fp.read(0x100)
    if len(data) != 0x100:
        raise IOError(f"Could not read full block at track {track}, sector {sector}")
    return data


def _zipcode_compress_block_rle(data, rep_byte):
    """Compress a 256-byte block using RLE."""
    result = bytearray()
    i = 0
    
    while i < len(data):
        run_byte = data[i]
        run_length = 1
        while i + run_length < len(data) and data[i + run_length] == run_byte and run_length < 255:
            run_length += 1
        
        if run_length >= 4 or run_byte == rep_byte:
            result.append(rep_byte)
            result.append(run_length)
            result.append(run_byte)
            i += run_length
        else:
            result.append(run_byte)
            i += 1
    
    return bytes(result)


def _zipcode_find_best_rep_byte(data):
    """Find the best repeat marker byte (one that appears least in data)."""
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    
    min_count = min(counts)
    for i, c in enumerate(counts):
        if c == min_count:
            return i
    return 0


def _zipcode_compress_block(data):
    """Compress a 256-byte block and return (flags, compressed_data)."""
    import struct
    
    # Check for fill block
    if all(b == data[0] for b in data):
        return 1, bytes([data[0]])
    
    # Try RLE compression
    rep_byte = _zipcode_find_best_rep_byte(data)
    compressed = _zipcode_compress_block_rle(data, rep_byte)
    
    rle_size = 2 + len(compressed)
    
    if len(compressed) <= 255 and rle_size < 256:
        chunk = struct.pack('BB', len(compressed), rep_byte) + compressed
        return 2, chunk
    
    return 0, data


def d64_to_zipcode(image_path, out_base, disk_id=None):
    """
    Convert a D64 image to 4 ZipCode files.
    
    image_path: path to input .d64 file
    out_base: base name for output (e.g., "GAME.prg" creates 1!GAME.prg, etc.)
    disk_id: optional 2-byte disk ID
    
    Returns: 0 on success, 1 on error
    """
    import struct
    
    base_dir, base_name = os.path.split(out_base)
    
    try:
        image_fp = open(image_path, 'rb')
    except:
        print("ERROR: Cannot open input image:", image_path)
        return 1
    
    # Read disk ID from BAM if not provided
    if disk_id is None:
        image_fp.seek(_zipcode_block_start(18, 0) + 0xA2)
        disk_id = image_fp.read(2)
        if len(disk_id) != 2:
            disk_id = b'00'
    
    error_found = False
    created_files = []
    
    with image_fp:
        for file_num, (start_track, end_track) in enumerate(ZIPCODE_TRACK_RANGES, 1):
            part_name = "%d!%s" % (file_num, base_name)
            fname = os.path.join(base_dir, part_name) if base_dir else part_name
            
            try:
                _zipcode_write_file(image_fp, fname, start_track, end_track, 
                                   disk_id if file_num == 1 else None)
                created_files.append(fname)
            except Exception as e:
                print(f"ERROR creating {fname}: {e}")
                error_found = True
    
    return (1 if error_found else 0), created_files


def _zipcode_write_file(image_fp, out_path, start_track, end_track, disk_id=None):
    """Write a single ZipCode file covering the specified track range."""
    import struct
    
    with open(out_path, 'wb') as zip_fp:
        if disk_id is not None:
            zip_fp.write(struct.pack('<H', 0x03FE))
            zip_fp.write(disk_id)
        else:
            zip_fp.write(struct.pack('<H', 0x0400))
        
        for track in range(start_track, end_track + 1):
            sector_order = _zipcode_get_sector_order(track)
            
            for sector in sector_order:
                data = _zipcode_read_block(image_fp, track, sector)
                flags, compressed = _zipcode_compress_block(data)
                
                track_flags = track | (flags << 6)
                zip_fp.write(struct.pack('BB', track_flags, sector))
                zip_fp.write(compressed)
        
        # Write end marker
        track_flags = ZIPCODE_END_MARKER_TRACK | (1 << 6)
        zip_fp.write(struct.pack('BB', track_flags, ZIPCODE_END_MARKER_SECTOR))


def zipcode_to_d64(in_base, out_path):
    """
    Convert 4 ZipCode files to a D64 image.
    
    in_base: base name (e.g., "GAME.prg" reads 1!GAME.prg, 2!GAME.prg, etc.)
    out_path: output D64 file path
    
    Returns: 0 on success, 1 on error
    """
    import struct
    
    error_found = False
    base_dir, base_name = os.path.split(in_base)
    
    try:
        out_fp = open(out_path, 'wb')
    except:
        print("ERROR: Cannot create output image:", out_path)
        return 1
    
    with out_fp as image_fp:
        for n in range(1, 5):
            part_name = "%d!%s" % (n, base_name)
            fname = os.path.join(base_dir, part_name) if base_dir else part_name
            
            try:
                _zipcode_convert_file(fname, image_fp)
            except Exception as e:
                print(f"{fname} is missing or invalid: {e}")
                error_found = True
    
    return 1 if error_found else 0


def _zipcode_convert_file(fname, image_fp):
    """Convert a single ZipCode file to D64 blocks."""
    import struct
    
    with open(fname, 'rb') as zip_fp:
        (start_addr,) = struct.unpack('<H', zip_fp.read(2))
        if start_addr == 0x03FE:
            _ = zip_fp.read(2)  # disk ID
        
        while True:
            val = zip_fp.read(2)
            if len(val) == 0:
                return
            
            data = bytes()
            track_flags, sector = struct.unpack('BB', val)
            flags = (track_flags & 0xC0) >> 6
            track = track_flags & 0x3F
            
            if track > 35:
                return  # End marker or invalid
            
            if flags == 0:
                data = zip_fp.read(0x100)
            
            elif flags == 1:
                fill_byte = zip_fp.read(1)
                data = fill_byte * 0x100
            
            elif flags == 2:
                while len(data) < 0x100:
                    dlen, rep = struct.unpack('BB', zip_fp.read(2))
                    zdata = zip_fp.read(dlen)
                    
                    while zdata:
                        b = zdata[0]
                        if b == rep:
                            data += bytes([zdata[2]]) * zdata[1]
                            zdata = zdata[3:]
                        else:
                            data += bytes([b])
                            zdata = zdata[1:]
            
            if len(data) == 256:
                pos = _zipcode_block_start(track, sector)
                image_fp.seek(pos)
                image_fp.write(data)
            
            if (track, sector) in ZIPCODE_FINAL_TS:
                break


# ============================================================================
# LNX -> D64 Konverter Funktionen
# ============================================================================

# D64 Track/Sector Daten
LNX_TRACK_SECTORS = [0]  # Index 0 unbenutzt
for _t in range(1, 36):
    if 1 <= _t <= 17:
        LNX_TRACK_SECTORS.append(21)
    elif 18 <= _t <= 24:
        LNX_TRACK_SECTORS.append(19)
    elif 25 <= _t <= 30:
        LNX_TRACK_SECTORS.append(18)
    else:
        LNX_TRACK_SECTORS.append(17)

LNX_TRACK_OFFSETS = [0]
_cum = 0
for _t in range(1, 36):
    LNX_TRACK_OFFSETS.append(_cum)
    _cum += LNX_TRACK_SECTORS[_t] * 256

LNX_D64_SIZE = _cum  # 174848 Bytes


def _lnx_ts_to_offset(track, sector):
    """Track/Sektor -> Byteoffset im D64."""
    if not (1 <= track <= 35):
        raise ValueError(f"Ungültiger Track: {track}")
    if not (0 <= sector < LNX_TRACK_SECTORS[track]):
        raise ValueError(f"Ungültiger Sektor {sector} auf Track {track}")
    return LNX_TRACK_OFFSETS[track] + sector * 256


def _lnx_ascii_to_petscii_name(name):
    """Namen für das Directory in PETSCII wandeln (16 Bytes, mit $A0 gepaddet)."""
    name = name[:16]
    out = bytearray()
    for ch in name:
        c = ord(ch)
        if 97 <= c <= 122:
            c -= 32
        if 32 <= c < 128:
            out.append(c)
        else:
            out.append(32)
    while len(out) < 16:
        out.append(0xA0)
    return bytes(out)


def _lnx_parse(buf):
    """
    Parst ein Lynx-Archiv.
    
    Returns: (dir_blocks, num_files, signature, entries_list)
    """
    from dataclasses import dataclass
    
    @dataclass
    class LynxEntry:
        name: str
        blocks: int
        ftype: str
        last_bytes: int
        data_offset: int
        total_bytes: int
    
    v = buf
    vlen = len(v)
    
    # DirBlocks-Zeile finden
    dir_pos = None
    for i in range(0x20, min(vlen - 3, 0x200)):
        if v[i] == 0x0D and v[i + 1] == 0x20 and 0x30 <= v[i + 2] <= 0x39:
            dir_pos = i
            break
    if dir_pos is None:
        raise ValueError("DirBlocks-Zeile im LNX-Header nicht gefunden")
    
    i = dir_pos + 1
    while i < vlen and v[i] == 0x20:
        i += 1
    dstart = i
    while i < vlen and 0x30 <= v[i] <= 0x39:
        i += 1
    if dstart == i:
        raise ValueError("DirBlocks-Zahl nicht gefunden")
    dir_blocks = int(v[dstart:i].decode("ascii"))
    
    while i < vlen and v[i] == 0x20:
        i += 1
    sig_start = i
    while i < vlen and v[i] != 0x0D:
        i += 1
    sig = v[sig_start:i].decode("ascii", errors="replace")
    numline_cr = i
    
    # NumFiles lesen
    j = numline_cr + 1
    while j < vlen and v[j] == 0x20:
        j += 1
    nf_start = j
    while j < vlen and 0x30 <= v[j] <= 0x39:
        j += 1
    if nf_start == j:
        raise ValueError("NumFiles-Zahl nicht gefunden")
    num_files = int(v[nf_start:j].decode("ascii"))
    
    while j < vlen and v[j] != 0x0D:
        j += 1
    pos = j + 1
    
    data_start = dir_blocks * 254
    entries = []
    cur_block = 0
    
    for fi in range(num_files):
        # Dateiname
        name_start = pos
        while pos < vlen and v[pos] != 0x0D:
            pos += 1
        name_bytes = v[name_start:pos]
        name = name_bytes.decode("ascii", errors="replace")
        pos += 1
        
        # Blocks
        k = pos
        while k < vlen and v[k] == 0x20:
            k += 1
        bstart = k
        while k < vlen and 0x30 <= v[k] <= 0x39:
            k += 1
        blocks = int(v[bstart:k].decode("ascii"))
        while k < vlen and v[k] != 0x0D:
            k += 1
        pos = k + 1
        
        # Typ
        ftype_ch = chr(v[pos])
        pos += 2
        
        # LastBytes
        k = pos
        while k < vlen and v[k] == 0x20:
            k += 1
        lbstart = k
        while k < vlen and 0x30 <= v[k] <= 0x39:
            k += 1
        last_bytes = int(v[lbstart:k].decode("ascii"))
        while k < vlen and v[k] != 0x0D:
            k += 1
        pos = k + 1
        
        total_bytes = (blocks - 1) * 254 + last_bytes
        data_offset = data_start + cur_block * 254
        cur_block += blocks
        
        entries.append(LynxEntry(
            name=name,
            blocks=blocks,
            ftype=ftype_ch.upper(),
            last_bytes=last_bytes,
            data_offset=data_offset,
            total_bytes=total_bytes,
        ))
    
    return dir_blocks, num_files, sig, entries


def _lnx_build_d64(entries, buf, diskname="LYNX-DISK"):
    """Erzeugt ein D64-Image aus Lynx-Einträgen."""
    
    image = bytearray(LNX_D64_SIZE)
    used_ts = set()
    used_ts.add((18, 0))
    
    free_ts = []
    for t in range(1, 36):
        if t == 18:
            continue
        for s in range(LNX_TRACK_SECTORS[t]):
            free_ts.append((t, s))
    
    file_count = len(entries)
    dir_sectors_needed = (file_count + 7) // 8 or 1
    dir_sectors = list(range(1, 1 + dir_sectors_needed))
    
    for i, sec in enumerate(dir_sectors):
        off = _lnx_ts_to_offset(18, sec)
        if i < len(dir_sectors) - 1:
            image[off + 0] = 18
            image[off + 1] = dir_sectors[i + 1]
        else:
            image[off + 0] = 0
            image[off + 1] = 0xFF
        used_ts.add((18, sec))
    
    bam_off = _lnx_ts_to_offset(18, 0)
    image[bam_off + 0] = 18
    image[bam_off + 1] = dir_sectors[0]
    image[bam_off + 2] = 0x41  # DOS Version 'A'
    image[bam_off + 3] = 0x00  # Single-sided (Standard 1541)
    
    # Disk Name bei $90-$9F
    dn_bytes = _lnx_ascii_to_petscii_name(diskname)
    image[bam_off + 0x90: bam_off + 0x90 + 16] = dn_bytes
    
    # Padding bei $A0-$A1
    image[bam_off + 0xA0] = 0xA0
    image[bam_off + 0xA1] = 0xA0
    
    # Disk ID bei $A2-$A3
    image[bam_off + 0xA2] = 0x30  # '0'
    image[bam_off + 0xA3] = 0x31  # '1'
    
    # Padding bei $A4
    image[bam_off + 0xA4] = 0xA0
    
    # DOS Type bei $A5-$A6 = "2A"
    image[bam_off + 0xA5] = 0x32  # '2'
    image[bam_off + 0xA6] = 0x41  # 'A'
    
    # Padding bei $A7-$AA
    image[bam_off + 0xA7] = 0xA0
    image[bam_off + 0xA8] = 0xA0
    image[bam_off + 0xA9] = 0xA0
    image[bam_off + 0xAA] = 0xA0
    
    for idx, e in enumerate(entries):
        start = e.data_offset
        end = start + e.total_bytes
        file_data = buf[start:end]
        
        n_sectors = (len(file_data) + 253) // 254
        if len(free_ts) < n_sectors:
            raise ValueError("Nicht genug Platz im D64-Image.")
        
        allocated = free_ts[:n_sectors]
        free_ts = free_ts[n_sectors:]
        
        for si in range(n_sectors):
            t, s = allocated[si]
            used_ts.add((t, s))
            off = _lnx_ts_to_offset(t, s)
            chunk_start = si * 254
            chunk_end = min(chunk_start + 254, len(file_data))
            chunk = file_data[chunk_start:chunk_end]
            
            if si < n_sectors - 1:
                nt, ns = allocated[si + 1]
                image[off + 0] = nt
                image[off + 1] = ns
                image[off + 2:off + 2 + len(chunk)] = chunk
            else:
                image[off + 0] = 0
                used = len(chunk)
                eof_pos = 1 + used
                if eof_pos > 255:
                    eof_pos = 255
                image[off + 1] = eof_pos
                image[off + 2:off + 2 + used] = chunk
        
        dir_sec_index = idx // 8
        entry_index = idx % 8
        dtrack = 18
        dsector = dir_sectors[dir_sec_index]
        d_off = _lnx_ts_to_offset(dtrack, dsector)
        
        entry_off = d_off + entry_index * 32
        file_type_byte = 0x80 | 0x20 | 0x02  # PRG
        image[entry_off + 2] = file_type_byte
        
        first_t, first_s = allocated[0]
        image[entry_off + 3] = first_t
        image[entry_off + 4] = first_s
        
        name_bytes = _lnx_ascii_to_petscii_name(e.name)
        image[entry_off + 5: entry_off + 21] = name_bytes
        
        image[entry_off + 30] = n_sectors & 0xFF
        image[entry_off + 31] = (n_sectors >> 8) & 0xFF
    
    # BAM füllen
    bam_ptr = bam_off + 0x04
    for track in range(1, 36):
        sectors = LNX_TRACK_SECTORS[track]
        free_count = 0
        b0 = b1 = b2 = 0
        for sec in range(sectors):
            if (track, sec) not in used_ts:
                free_count += 1
                bit = 1 << (sec & 7)
                if sec < 8:
                    b0 |= bit
                elif sec < 16:
                    b1 |= bit
                else:
                    b2 |= bit
        
        image[bam_ptr + 0] = free_count
        image[bam_ptr + 1] = b0
        image[bam_ptr + 2] = b1
        image[bam_ptr + 3] = b2
        bam_ptr += 4
    
    return bytes(image)


def lnx_to_d64(lnx_path, out_path):
    """
    Convert Lynx archive to D64 image.
    
    lnx_path: path to .lnx or .LNX.prg file
    out_path: output D64 file path
    
    Returns: 0 on success, 1 on error
    """
    import sys
    
    if not os.path.isfile(lnx_path):
        candidates = [
            lnx_path + ".lnx",
            lnx_path + ".LNX",
            lnx_path + ".LNX.prg",
            lnx_path + ".lnx.prg",
        ]
        for c in candidates:
            if os.path.isfile(c):
                lnx_path = c
                break
    
    if not os.path.isfile(lnx_path):
        print("LNX-Archiv nicht gefunden:", lnx_path)
        return 1
    
    with open(lnx_path, "rb") as f:
        buf = f.read()
    
    try:
        dir_blocks, num_files, sig, entries = _lnx_parse(buf)
    except Exception as e:
        print("Fehler beim Parsen des LNX-Archivs:", e)
        return 1
    
    if not entries:
        print("Keine Dateien im LNX-Archiv gefunden.")
        return 1
    
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    
    diskname = os.path.splitext(os.path.basename(out_path))[0][:16]
    
    try:
        image = _lnx_build_d64(entries, buf, diskname=diskname)
    except Exception as e:
        print("Fehler beim Erzeugen des D64-Images:", e)
        return 1
    
    with open(out_path, "wb") as f:
        f.write(image)
    
    return 0


if __name__ == "__main__":
    # Test
    import sys
    if len(sys.argv) > 1:
        viewer = DiskImageViewer(sys.argv[1])
        entries = viewer.read_directory()
        for entry in entries:
            print(entry)
