"""
C64 ROM Font Renderer
Verwendet die Original C64 ROM Character Bitmaps (upper.bmp/lower.bmp)
Genau wie CGTerm

Version 3.4:
- Globaler Screen-Hintergrund (screen_bg) für CCGMS/Novaterm CTRL-B Support
- Font-Format bleibt unverändert (RGB mit schwarzem Hintergrund)
"""

from PIL import Image, ImageDraw
import os
from petscii_charset import C64_COLORS

# Verbesserte C64-Farbpalette (aus CGTerm gfx.c)
C64_PALETTE_CGTERM = {
    0:  (0x00, 0x00, 0x00),  # schwarz
    1:  (0xFD, 0xFE, 0xFC),  # weiß
    2:  (0xBE, 0x1A, 0x24),  # rot
    3:  (0x30, 0xE6, 0xC6),  # cyan
    4:  (0xB4, 0x1A, 0xE2),  # violett
    5:  (0x1F, 0xD2, 0x1E),  # grün
    6:  (0x21, 0x1B, 0xAE),  # blau
    7:  (0xDF, 0xF6, 0x0A),  # gelb
    8:  (0xB8, 0x41, 0x04),  # orange
    9:  (0x6A, 0x33, 0x04),  # braun
    10: (0xFE, 0x4A, 0x57),  # hellrot
    11: (0x42, 0x45, 0x40),  # dunkelgrau
    12: (0x70, 0x74, 0x6F),  # mittelgrau
    13: (0x59, 0xFE, 0x59),  # hellgrün
    14: (0x5F, 0x53, 0xFE),  # hellblau
    15: (0xA4, 0xA7, 0xA2),  # hellgrau
}


class C64ROMFontRenderer:
    """
    Rendert mit Original C64 ROM Fonts (BMP)
    Wie CGTerm - verwendet SCREENCODES als Index
    """
    
    def __init__(self, screen_buffer, font_upper_path="upper.bmp", font_lower_path="lower.bmp", zoom=2):
        """
        Args:
            screen_buffer: PETSCIIScreenBuffer Instanz
            font_upper_path: Pfad zu upper.bmp (Upper/Graphics)
            font_lower_path: Pfad zu lower.bmp (Lower/Upper)
            zoom: Skalierungsfaktor (1, 2, 3, 4)
        """
        self.screen = screen_buffer
        self._zoom = zoom
        
        # char_width und char_height werden jetzt dynamisch berechnet
        # siehe Properties unten
        
        # Palette
        self.palette = C64_PALETTE_CGTERM
        
        # Cache-Verzeichnis für vorberechnete Font-Surfaces
        self.cache_dir = ".font_cache"
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)
        
        # Lade ROM Fonts
        self.font_upper_raw = self._load_bmp(font_upper_path)
        self.font_lower_raw = self._load_bmp(font_lower_path)
        
        # Cache für Font-Surfaces (key = zoom level)
        self.font_cache_upper = {}
        self.font_cache_lower = {}
        
        # Erstelle vorgerenderte Font-Surfaces (wie in CGTerm gfx_createfont)
        self.font_upper = self._get_or_create_font_surface(self.font_upper_raw, zoom, is_upper=True)
        self.font_lower = self._get_or_create_font_surface(self.font_lower_raw, zoom, is_upper=False)
        
        # Bild-Dimensionen
        self.img_width = screen_buffer.width * self.char_width
        self.img_height = screen_buffer.height * self.char_height
    
    def preload_common_zooms(self, zoom_levels=[1, 2, 3, 4, 5]):
        """
        Lädt häufig verwendete Zoom-Levels vor (beim ersten Start)
        Dies erstellt die Font-Surfaces und speichert sie im Disk-Cache
        """
        print(f"Preloading fonts: {zoom_levels}")
        for zoom in zoom_levels:
            if zoom != self._zoom:  # Aktueller Zoom ist schon geladen
                try:
                    # Einfach Surface anfordern - wird automatisch gecacht
                    self._get_or_create_font_surface(self.font_upper_raw, zoom, is_upper=True)
                    self._get_or_create_font_surface(self.font_lower_raw, zoom, is_upper=False)
                except Exception as e:
                    print(f"Error preloading zoom {zoom}x: {e}")
        print(f"Preload complete")
    
    @property
    def zoom(self):
        """Getter für zoom"""
        return self._zoom
    
    @zoom.setter
    def zoom(self, value):
        """Setter für zoom - Font-Surfaces aus Cache holen oder erstellen"""
        if value != self._zoom:
            self._zoom = value
            # Font-Surfaces aus Cache holen oder neu erstellen
            self.font_upper = self._get_or_create_font_surface(self.font_upper_raw, value, is_upper=True)
            self.font_lower = self._get_or_create_font_surface(self.font_lower_raw, value, is_upper=False)
            # Bild-Dimensionen neu berechnen
            self.img_width = self.screen.width * self.char_width
            self.img_height = self.screen.height * self.char_height
    
    @property
    def char_width(self):
        """Dynamisch berechnete char_width basierend auf zoom"""
        return 8 * self._zoom
    
    @property
    def char_height(self):
        """Dynamisch berechnete char_height basierend auf zoom"""
        return 8 * self._zoom
        
    def _load_bmp(self, path):
        """Lädt BMP Font"""
        if not os.path.exists(path):
            raise FileNotFoundError(f"Font nicht gefunden: {path}")
        
        img = Image.open(path)
        if img.mode != 'P':  # Palette mode
            img = img.convert('P')
        return img
    
    def _get_or_create_font_surface(self, raw_font, zoom, is_upper):
        """Holt Font-Surface aus RAM-Cache, Disk-Cache oder erstellt neu"""
        cache = self.font_cache_upper if is_upper else self.font_cache_lower
        font_name = 'upper' if is_upper else 'lower'
        
        # 1. Prüfe RAM-Cache
        if zoom in cache:
            return cache[zoom]
        
        # 2. Prüfe Disk-Cache
        cache_filename = f"font_{font_name}_zoom{zoom}.png"
        cache_path = os.path.join(self.cache_dir, cache_filename)
        
        if os.path.exists(cache_path):
            try:
                surface = Image.open(cache_path)
                # Konvertiere zu RGB wenn nötig
                if surface.mode != 'RGB':
                    surface = surface.convert('RGB')
                cache[zoom] = surface
                print(f"Font {font_name} zoom {zoom}x: loaded from cache")
                return surface
            except Exception as e:
                print(f"Error loading cached font: {e}")
        
        # 3. Erstelle neu und speichere in Disk-Cache
        print(f"Font {font_name} zoom {zoom}x: creating...")
        surface = self._create_font_surface(raw_font, zoom)
        cache[zoom] = surface
        
        # Speichere im Disk-Cache
        try:
            surface.save(cache_path, 'PNG')
            print(f"Font {font_name} zoom {zoom}x: saved to cache")
        except Exception as e:
            print(f"Warning: Could not save font to cache: {e}")
        
        return surface
    
    def _create_font_surface(self, raw_font, zoom):
        """
        Erstellt Font-Surface mit allen Farben
        Wie CGTerm gfx_createfont() - Zeile 69-116
        
        Font-Surface wird nach SCREENCODE indexiert!
        Font[screencode] = ROM[screencode & 0x7F]
        Wenn Bit 7 gesetzt: Farben vertauschen (RVS)
        
        Format: 256 SCREENCODES breit, 16 Farben hoch
        """
        # Berechne char_width/height explizit für diesen Zoom
        char_w = 8 * zoom
        char_h = 8 * zoom
        
        surface_width = 256 * char_w
        surface_height = 16 * char_h
        
        surface = Image.new('RGB', (surface_width, surface_height), (0, 0, 0))
        
        # Für jede Farbe
        for color_idx in range(16):
            fg_color = self.palette[color_idx]
            bg_color = self.palette[0]  # Schwarz
            
            # Für jeden SCREENCODE (0-255)
            for screencode in range(256):
                # ROM-Position (ignoriere Bit 7)
                rom_code = screencode & 0x7F
                src_x = (rom_code & 0x1F) * 8  # rom_code % 32
                src_y = (rom_code // 32) * 8
                
                # Position im Output
                dest_x = screencode * char_w
                dest_y = color_idx * char_h
                
                # Wenn Bit 7 gesetzt: Farben vertauschen (RVS)
                if screencode & 0x80:
                    # Invertiert: fg und bg tauschen
                    self._blit_char(surface, raw_font, src_x, src_y, dest_x, dest_y, bg_color, fg_color, zoom)
                else:
                    # Normal
                    self._blit_char(surface, raw_font, src_x, src_y, dest_x, dest_y, fg_color, bg_color, zoom)
        
        return surface
    
    def _blit_char(self, dest, src, src_x, src_y, dest_x, dest_y, fg_color, bg_color, zoom):
        """
        Kopiert ein 8x8 Zeichen und skaliert es
        Wie CGTerm - Zeile 96-112
        """
        # Hole 8x8 Pixel aus Source
        for y in range(8):
            for dy in range(zoom):  # Vertical zoom
                for x in range(8):
                    # Pixel aus Source-Font holen
                    pixel = src.getpixel((src_x + x, src_y + y))
                    
                    # Farbe bestimmen (0 = Hintergrund, sonst Vordergrund)
                    color = fg_color if pixel != 0 else bg_color
                    
                    # Horizontal zoom
                    for dx in range(zoom):
                        dest.putpixel((dest_x + x * zoom + dx, dest_y + y * zoom + dy), color)
    
    def render(self):
        """
        Rendert den kompletten Screen (normal 25 Zeilen)
        
        CCGMS/Novaterm kompatibel:
        - screen.screen_bg wird als globaler Hintergrund verwendet
        """
        # Bild-Größe (normal 40x25 oder 80x25)
        img_width = self.screen.width * self.char_width
        img_height = self.screen.height * self.char_height
        
        # Globaler Hintergrund aus screen.screen_bg (CTRL-B setzt diesen!)
        if hasattr(self.screen, 'screen_bg'):
            bg_color_idx = self.screen.screen_bg
        else:
            bg_color_idx = 0  # Fallback: Schwarz
        
        bg_color = self.palette[bg_color_idx]
        img = Image.new('RGB', (img_width, img_height), bg_color)
        
        # Welcher Font?
        current_font = self.font_lower if self.screen.charset_mode == 'lower' else self.font_upper
        
        # Rendere jede Zeile
        for y in range(self.screen.height):
            for x in range(self.screen.width):
                cell = self.screen.buffer[y][x]
                self._render_cell(img, current_font, x, y, cell, bg_color_idx)
        
        return img
    
    def _render_cell(self, dest, font_surface, x, y, cell, global_bg_idx):
        """
        Rendert eine Zelle
        Wie CGTerm gfx_draw_line() - Zeile 413-418
        
        Bei nicht-schwarzem Hintergrund: Schwarze Pixel durch Hintergrund ersetzen
        """
        # Hole SCREENCODE aus Cell
        if isinstance(cell.char, int):
            screencode = cell.char
        elif cell.char and len(cell.char) == 1:
            screencode = ord(cell.char)
        else:
            screencode = 0x20  # Space
        
        # RVS (Bit 7) - Font-Surface hat bereits invertierte Versionen
        if cell.reverse:
            screencode |= 0x80
        
        # Farbe
        fg_idx = cell.fg_color
        
        # Position im Font-Surface (SCREENCODE als Index!)
        src_x = screencode * self.char_width
        src_y = fg_idx * self.char_height
        
        # Position im Dest
        dest_x = x * self.char_width
        dest_y = y * self.char_height
        
        # Kopiere aus Font-Surface
        region = font_surface.crop((src_x, src_y, src_x + self.char_width, src_y + self.char_height))
        
        # Wenn globaler Hintergrund NICHT schwarz ist:
        # Ersetze schwarze Pixel (0,0,0) durch Hintergrundfarbe
        if global_bg_idx != 0:
            bg_color = self.palette[global_bg_idx]
            region = region.copy()  # Muss kopieren um Original nicht zu ändern
            pixels = region.load()
            for py in range(self.char_height):
                for px in range(self.char_width):
                    if pixels[px, py] == (0, 0, 0):
                        pixels[px, py] = bg_color
        
        dest.paste(region, (dest_x, dest_y))
    
    def render_with_cursor(self, cursor_char='█'):
        """Rendert mit Cursor"""
        img = self.render()
        draw = ImageDraw.Draw(img)
        
        # Cursor-Position
        cx = self.screen.cursor_x * self.char_width
        cy = self.screen.cursor_y * self.char_height
        
        # Cursor als Block am unteren Rand
        cell = self.screen.buffer[self.screen.cursor_y][self.screen.cursor_x]
        fg, bg = cell.get_display_colors()
        cursor_color = self.palette.get(fg, self.palette[14])
        
        cursor_height = 2 * self.zoom
        draw.rectangle(
            [cx, cy + self.char_height - cursor_height, 
             cx + self.char_width - 1, cy + self.char_height - 1],
            fill=cursor_color
        )
        
        return img


class AnimatedC64ROMFontRenderer(C64ROMFontRenderer):
    """Renderer mit animiertem Cursor"""
    
    def __init__(self, screen_buffer, font_upper_path="upper.bmp", font_lower_path="lower.bmp", zoom=2):
        super().__init__(screen_buffer, font_upper_path, font_lower_path, zoom)
        self.cursor_visible = True
        self.cursor_blink_time = 0
        self.cursor_blink_rate = 0.5
        
    def update_cursor_blink(self, delta_time):
        """Update Cursor-Blink"""
        self.cursor_blink_time += delta_time
        if self.cursor_blink_time >= self.cursor_blink_rate:
            self.cursor_blink_time = 0
            self.cursor_visible = not self.cursor_visible
            
    def render_with_cursor(self):
        """Rendert mit animiertem Cursor"""
        img = self.render()
        
        if self.cursor_visible:
            draw = ImageDraw.Draw(img)
            
            cx = self.screen.cursor_x * self.char_width
            cy = self.screen.cursor_y * self.char_height
            
            cell = self.screen.buffer[self.screen.cursor_y][self.screen.cursor_x]
            fg, bg = cell.get_display_colors()
            cursor_color = self.palette.get(fg, self.palette[14])
            
            cursor_height = 2 * self.zoom
            draw.rectangle(
                [cx, cy + self.char_height - cursor_height, 
                 cx + self.char_width - 1, cy + self.char_height - 1],
                fill=cursor_color
            )
        
        return img


if __name__ == "__main__":
    # Test
    from petscii_parser import PETSCIIScreenBuffer, PETSCIIParser
    
    screen = PETSCIIScreenBuffer(40, 25)
    parser = PETSCIIParser(screen)
    
    # Test-Daten
    test_data = bytearray([0x93, 0x05])  # CLR, WHITE
    for char in "quantum world":
        test_data.append(ord(char))
    
    parser.parse_bytes(test_data)
    
    try:
        renderer = C64ROMFontRenderer(screen, zoom=2)
        img = renderer.render_with_cursor()
        img.save("test_c64rom.png")
        print("✅ Test-Rendering gespeichert: test_c64rom.png")
    except FileNotFoundError as e:
        print(f"❌ {e}")
        print("Kopiere upper.bmp und lower.bmp ins Verzeichnis!")
