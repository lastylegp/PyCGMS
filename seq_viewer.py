"""
PETSCII SEQ File Viewer
Lädt und zeigt PETSCII SEQ-Dateien (C64 Art)
"""

import sys
from petscii_parser import PETSCIIScreenBuffer, PETSCIIParser
from petscii_renderer import PETSCIIRenderer


def load_seq_file(filename):
    """
    Lädt eine PETSCII SEQ-Datei
    
    Args:
        filename: Pfad zur SEQ-Datei
        
    Returns:
        bytearray mit PETSCII-Daten
    """
    with open(filename, 'rb') as f:
        return bytearray(f.read())


def view_seq_file(filename, output_image=None):
    """
    Zeigt eine PETSCII SEQ-Datei an
    
    Args:
        filename: Pfad zur SEQ-Datei
        output_image: Optional - Pfad für Output-PNG
    """
    print(f"Lade PETSCII File: {filename}")
    
    # Lade SEQ-Daten
    seq_data = load_seq_file(filename)
    print(f"Geladen: {len(seq_data)} bytes")
    
    # Screen Buffer erstellen
    screen = PETSCIIScreenBuffer(40, 25)
    parser = PETSCIIParser(screen)
    
    # PETSCII parsen
    parser.parse_bytes(seq_data)
    
    # Text-Ausgabe
    print("\nScreen Content:")
    print("=" * 42)
    print(screen.get_screen_text())
    print("=" * 42)
    
    # Grafische Ausgabe
    if output_image:
        renderer = PETSCIIRenderer(screen, char_width=10, char_height=16)
        img = renderer.render()
        img.save(output_image)
        print(f"\nBild gespeichert: {output_image}")
        

def create_petscii_art_demo():
    """Erstellt eine Demo PETSCII Art SEQ-Datei"""
    
    seq_data = bytearray([
        0x93,  # CLR/HOME
        0x05,  # WHITE
    ])
    
    # Title
    title = "╔═══════════════════════════════════════╗"
    for char in title:
        # Mapping für Box-Zeichen
        if char == '╔':
            seq_data.append(0x79)
        elif char == '═':
            seq_data.append(0x75)
        elif char == '╗':
            seq_data.append(0x7A)
        else:
            seq_data.append(ord(char))
    seq_data.append(0x0D)
    
    # Content Line
    line1 = "║  PETSCII BBS TERMINAL v1.0            ║"
    for char in line1:
        if char == '║':
            seq_data.append(0x72)
        else:
            seq_data.append(ord(char))
    seq_data.append(0x0D)
    
    # Bottom
    bottom = "╚═══════════════════════════════════════╝"
    for char in bottom:
        if char == '╚':
            seq_data.append(0x7B)
        elif char == '═':
            seq_data.append(0x75)
        elif char == '╝':
            seq_data.append(0x7C)
        else:
            seq_data.append(ord(char))
    seq_data.append(0x0D)
    seq_data.append(0x0D)
    
    # Colored text
    colors = [
        (0x1C, "RED TEXT"),
        (0x1E, "GREEN TEXT"),
        (0x9F, "CYAN TEXT"),
        (0x9E, "YELLOW TEXT"),
        (0x9C, "PURPLE TEXT")
    ]
    
    for color_code, text in colors:
        seq_data.append(color_code)
        seq_data.append(0x12)  # RVS ON
        seq_data.append(0x20)  # Space
        for char in text:
            seq_data.append(ord(char))
        seq_data.append(0x20)  # Space
        seq_data.append(0x92)  # RVS OFF
        seq_data.append(0x0D)
        
    # Graphics demo
    seq_data.append(0x0D)
    seq_data.append(0x05)  # WHITE
    
    # Box drawing chars
    graphics = "┌─────────┐"
    for char in graphics:
        if char == '┌':
            seq_data.append(0x6C)
        elif char == '─':
            seq_data.append(0x60)
        elif char == '┐':
            seq_data.append(0x6B)
        else:
            seq_data.append(ord(char))
    seq_data.append(0x0D)
    
    for _ in range(3):
        seq_data.append(0x62)  # │
        for _ in range(9):
            seq_data.append(0x20)  # Space
        seq_data.append(0x62)  # │
        seq_data.append(0x0D)
        
    graphics2 = "└─────────┘"
    for char in graphics2:
        if char == '└':
            seq_data.append(0x6E)
        elif char == '─':
            seq_data.append(0x60)
        elif char == '┘':
            seq_data.append(0x6D)
        else:
            seq_data.append(ord(char))
    
    return seq_data


def main():
    """Hauptfunktion"""
    
    if len(sys.argv) > 1:
        # View existing file
        filename = sys.argv[1]
        output = sys.argv[2] if len(sys.argv) > 2 else "output.png"
        view_seq_file(filename, output)
    else:
        # Create and view demo
        print("Erstelle PETSCII Art Demo...")
        demo_data = create_petscii_art_demo()
        
        # Save demo
        demo_file = "petscii_demo.seq"
        with open(demo_file, 'wb') as f:
            f.write(demo_data)
        print(f"Demo gespeichert: {demo_file}")
        
        # View demo
        view_seq_file(demo_file, "petscii_demo.png")


if __name__ == "__main__":
    main()
