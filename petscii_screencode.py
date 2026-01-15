"""
PETSCII zu SCREENCODE Konvertierung
EXAKT aus CGTerm kernal.c Zeile 9-30
"""

# CGTerm scconv Tabelle (kernal.c Zeile 10-18)
SCCONV = [
    128,   # Range 0: 0x00-0x1F
    0,     # Range 1: 0x20-0x3F  
    -64,   # Range 2: 0x40-0x5F
    -32,   # Range 3: 0x60-0x7F
    64,    # Range 4: 0x80-0x9F
    -64,   # Range 5: 0xA0-0xBF
    -128,  # Range 6: 0xC0-0xDF
    -128   # Range 7: 0xE0-0xFF
]

# Pre-compute screencode table (wie CGTerm kernal_init)
SCREENCODE_TABLE = []
for c in range(256):
    sc = (c + SCCONV[c // 32]) & 0xFF
    SCREENCODE_TABLE.append(sc)

# Special case (kernal.c Zeile 30)
SCREENCODE_TABLE[255] = 94


def petscii_to_screencode(petscii_byte):
    """
    Konvertiert PETSCII-Byte zu C64 SCREENCODE
    EXAKT wie CGTerm: screencode[c] = c + scconv[c / 32]
    
    Args:
        petscii_byte: PETSCII byte (0-255)
        
    Returns:
        screencode byte (0-255)
    """
    return SCREENCODE_TABLE[petscii_byte & 0xFF]


def screencode_to_petscii(screencode, reverse=False):
    """
    Konvertiert SCREENCODE zurück zu PETSCII
    Basierend auf C64-Wiki PETSCII-Tabelle:
    https://www.c64-wiki.de/wiki/PETSCII-Tabelle
    
    Screen Code → PETSCII Mapping:
    - Screen 0-31   → PETSCII $40-$5F (64-95)  = @, A-Z, etc.
    - Screen 32-63  → PETSCII $20-$3F (32-63)  = Space, Zahlen, Sonderzeichen
    - Screen 64-95  → PETSCII $C0-$DF (192-223) = CBM Graphics (Cbm+Taste)
    - Screen 96-127 → PETSCII $A0-$BF (160-191) = Shifted Graphics
    
    Note: Screen 64-95 könnte auch $60-$7F sein, aber $C0-$DF ist gebräuchlicher
    Note: Screen 96-127 könnte auch $E0-$FE sein, aber $A0-$BF ist gebräuchlicher
    
    Args:
        screencode: Screencode byte (0-127, Bit 7 = reverse)
        reverse: Reverse-Flag (für RVS ON/OFF)
        
    Returns:
        petscii byte (0-255)
    """
    # Maske für 0-127 range (Bit 7 ist reverse im Font)
    sc = screencode & 0x7F
    
    if sc < 32:
        # Screen 0-31 → PETSCII $40-$5F (64-95) = @, A-Z, etc.
        petscii = sc + 0x40
    elif sc < 64:
        # Screen 32-63 → PETSCII $20-$3F (32-63) = Space, Zahlen, etc.
        petscii = sc
    elif sc < 96:
        # Screen 64-95 → PETSCII $C0-$DF (192-223) = CBM Graphics
        # Dies sind die gebräuchlicheren Codes für Grafik in BBS-Software
        petscii = sc + 0x80  # +128 = $C0-$DF
    else:
        # Screen 96-127 → PETSCII $A0-$BF (160-191) = Shifted Graphics  
        petscii = sc + 0x40  # +64 = $A0-$BF
    
    return petscii


# Test-Tabelle für wichtige Zeichen
PETSCII_TO_SCREENCODE_TABLE = {
    # Control Codes (bleiben meist)
    0x0D: 0x0D,  # RETURN
    0x14: 0x14,  # DEL
    0x20: 0x20,  # Space
    
    # Zahlen (0x30-0x39 -> 0x30-0x39)
    0x30: 0x30, 0x31: 0x31, 0x32: 0x32, 0x33: 0x33, 0x34: 0x34,
    0x35: 0x35, 0x36: 0x36, 0x37: 0x37, 0x38: 0x38, 0x39: 0x39,
    
    # Uppercase (0x41-0x5A -> 0x01-0x1A)
    0x41: 0x01,  # A
    0x42: 0x02,  # B
    0x43: 0x03,  # C
    0x44: 0x04,  # D
    0x45: 0x05,  # E
    0x46: 0x06,  # F
    0x47: 0x07,  # G
    0x48: 0x08,  # H
    0x49: 0x09,  # I
    0x4A: 0x0A,  # J
    0x4B: 0x0B,  # K
    0x4C: 0x0C,  # L
    0x4D: 0x0D,  # M (ACHTUNG: Konflikt mit RETURN!)
    0x4E: 0x0E,  # N
    0x4F: 0x0F,  # O
    0x50: 0x10,  # P
    0x51: 0x11,  # Q
    0x52: 0x12,  # R
    0x53: 0x13,  # S
    0x54: 0x14,  # T
    0x55: 0x15,  # U
    0x56: 0x16,  # V
    0x57: 0x17,  # W
    0x58: 0x18,  # X
    0x59: 0x19,  # Y
    0x5A: 0x1A,  # Z
    
    # Lowercase (0x61-0x7A -> 0x41-0x5A)
    0x61: 0x41,  # a
    0x62: 0x42,  # b
    0x63: 0x43,  # c
    0x64: 0x44,  # d
    0x65: 0x45,  # e
    0x66: 0x46,  # f
    0x67: 0x47,  # g
    0x68: 0x48,  # h
    0x69: 0x49,  # i
    0x6A: 0x4A,  # j
    0x6B: 0x4B,  # k
    0x6C: 0x4C,  # l
    0x6D: 0x4D,  # m
    0x6E: 0x4E,  # n
    0x6F: 0x4F,  # o
    0x70: 0x50,  # p
    0x71: 0x51,  # q
    0x72: 0x52,  # r
    0x73: 0x53,  # s
    0x74: 0x54,  # t
    0x75: 0x55,  # u
    0x76: 0x56,  # v
    0x77: 0x57,  # w
    0x78: 0x58,  # x
    0x79: 0x59,  # y
    0x7A: 0x5A,  # z
    
    # Grafik-Zeichen (Box-Drawing)
    0x60: 0x40,  # ─ (horizontal line)
    0x6D: 0x4D,  # └
    0x6E: 0x4E,  # ┘
    0x6C: 0x4C,  # ┌
    0x6B: 0x4B,  # ┐
    0x62: 0x42,  # │ (vertical line)
}


if __name__ == "__main__":
    # Test
    print("PETSCII zu SCREENCODE Konvertierung")
    print("=" * 60)
    print()
    
    # Test "quantum"
    word = "quantum"
    print(f"Test: '{word}'")
    print("-" * 40)
    for char in word:
        petscii = ord(char)
        screencode = petscii_to_screencode(petscii)
        print(f"  '{char}' PETSCII 0x{petscii:02X} -> SCREENCODE 0x{screencode:02X}")
    
    print()
    
    # Test "THE HIDDEN"
    word2 = "THE HIDDEN"
    print(f"Test: '{word2}'")
    print("-" * 40)
    for char in word2:
        petscii = ord(char)
        screencode = petscii_to_screencode(petscii)
        print(f"  '{char}' PETSCII 0x{petscii:02X} -> SCREENCODE 0x{screencode:02X}")
    
    print()
    print("=" * 60)
    print("Wichtig:")
    print("  PETSCII 0x41 'A' -> SCREENCODE 0x01 (nicht 0x41!)")
    print("  PETSCII 0x61 'a' -> SCREENCODE 0x41 (nicht 0x61!)")
    print()
    print("Das ist der Unterschied zu vorher!")
    print("CGTerm verwendet SCREENCODES als Font-Index!")
