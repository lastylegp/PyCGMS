"""
C64 Keyboard Mapping
Basiert auf VICE Keymap - Vollständiges Tastatur-Mapping für C64 BBS
Mit deutschem Tastaturlayout (QWERTZ) und Z/Y Swap Option für US Layout
"""

# Globale Einstellung für Z/Y Swap (für US Tastatur)
_swap_zy = False

def set_swap_zy(swap: bool):
    """Aktiviert/Deaktiviert Z/Y Swap für US Tastaturlayout"""
    global _swap_zy
    _swap_zy = swap
    
def get_swap_zy() -> bool:
    """Gibt aktuellen Z/Y Swap Status zurück"""
    return _swap_zy

# C64 Keyboard Matrix (8x8)
# Row/Column Mapping wie im Original C64

# VICE Keymap zu PETSCII Control Code Mapping
# Format: (scancode/keysym) -> PETSCII byte

# Spezielle C64 Tasten
C64_KEYS = {
    # PETSCII Control Codes
    'RETURN': 0x0D,      # Carriage Return
    'DELETE': 0x14,      # DEL (Backspace)
    'INSERT': 0x94,      # INS
    'HOME': 0x13,        # HOME
    'CLR': 0x93,         # CLR/HOME (Shift+HOME)
    
    # Cursor Movement
    'CRSR_UP': 0x91,     # Cursor Up
    'CRSR_DOWN': 0x11,   # Cursor Down
    'CRSR_LEFT': 0x9D,   # Cursor Left
    'CRSR_RIGHT': 0x1D,  # Cursor Right
    
    # Function Keys
    'F1': 0x85,          # F1
    'F2': 0x89,          # F2 (Shift+F1)
    'F3': 0x86,          # F3
    'F4': 0x8A,          # F4 (Shift+F3)
    'F5': 0x87,          # F5
    'F6': 0x8B,          # F6 (Shift+F5)
    'F7': 0x88,          # F7
    'F8': 0x8C,          # F8 (Shift+F7)
    
    # Special Keys
    'STOP': 0x03,        # RUN/STOP
    'RESTORE': 0x00,     # RESTORE (special handling)
    
    # Shift Charset
    'CHARSET_LOWER': 0x0E,  # Switch to lowercase
    'CHARSET_UPPER': 0x8E,  # Switch to uppercase
    
    # Colors
    'BLACK': 0x90,
    'WHITE': 0x05,
    'RED': 0x1C,
    'CYAN': 0x9F,
    'PURPLE': 0x9C,
    'GREEN': 0x1E,
    'BLUE': 0x1F,
    'YELLOW': 0x9E,
    'ORANGE': 0x81,
    'BROWN': 0x95,
    'LT_RED': 0x96,
    'GREY1': 0x97,
    'GREY2': 0x98,
    'LT_GREEN': 0x99,
    'LT_BLUE': 0x9A,
    'GREY3': 0x9B,
    
    # Display Modes
    'RVS_ON': 0x12,      # Reverse On
    'RVS_OFF': 0x92,     # Reverse Off
}

# PC Keyboard zu C64 PETSCII Mapping
# Erweiterte Mapping-Tabelle mit deutschem Layout
KEYBOARD_MAPPING = {
    # Haupttastatur - Zahlen
    '1': 0x31, '2': 0x32, '3': 0x33, '4': 0x34, '5': 0x35,
    '6': 0x36, '7': 0x37, '8': 0x38, '9': 0x39, '0': 0x30,
    
    # Haupttastatur - Buchstaben (Uppercase in PETSCII Upper Mode)
    'a': 0x41, 'b': 0x42, 'c': 0x43, 'd': 0x44, 'e': 0x45,
    'f': 0x46, 'g': 0x47, 'h': 0x48, 'i': 0x49, 'j': 0x4A,
    'k': 0x4B, 'l': 0x4C, 'm': 0x4D, 'n': 0x4E, 'o': 0x4F,
    'p': 0x50, 'q': 0x51, 'r': 0x52, 's': 0x53, 't': 0x54,
    'u': 0x55, 'v': 0x56, 'w': 0x57, 'x': 0x58, 'y': 0x59,
    'z': 0x5A,
    
    # Shift+Buchstaben = Lowercase/Graphics (C64 Upper Mode)
    'A': 0xC1, 'B': 0xC2, 'C': 0xC3, 'D': 0xC4, 'E': 0xC5,
    'F': 0xC6, 'G': 0xC7, 'H': 0xC8, 'I': 0xC9, 'J': 0xCA,
    'K': 0xCB, 'L': 0xCC, 'M': 0xCD, 'N': 0xCE, 'O': 0xCF,
    'P': 0xD0, 'Q': 0xD1, 'R': 0xD2, 'S': 0xD3, 'T': 0xD4,
    'U': 0xD5, 'V': 0xD6, 'W': 0xD7, 'X': 0xD8, 'Y': 0xD9,
    'Z': 0xDA,
    
    # Sonderzeichen
    ' ': 0x20,  # Space
    '!': 0x21, '"': 0x22, '#': 0x23, '$': 0x24, '%': 0x25,
    '&': 0x26, "'": 0x27, '(': 0x28, ')': 0x29, '*': 0x2A,
    '+': 0x2B, ',': 0x2C, '-': 0x2D, '.': 0x2E, '/': 0x2F,
    ':': 0x3A, ';': 0x3B, '<': 0x3C, '=': 0x3D, '>': 0x3E,
    '?': 0x3F, '@': 0x40, '[': 0x5B, '\\': 0x5C, ']': 0x5D,
    '^': 0x5E, '_': 0x5F,
    
    # Deutsche Sonderzeichen - PETSCII Ersetzungen
    'ä': 0x7B,  # { auf C64 (oder alternativ 'ae' senden)
    'ö': 0x7C,  # | auf C64 (oder alternativ 'oe' senden)
    'ü': 0x7D,  # } auf C64 (oder alternativ 'ue' senden)
    'Ä': 0x5B,  # [ auf C64
    'Ö': 0x5C,  # \ auf C64
    'Ü': 0x5D,  # ] auf C64
    'ß': 0x7E,  # ~ auf C64 (oder alternativ 'ss' senden)
    
    # Weitere deutsche Zeichen
    '§': 0x40,  # @ als Ersatz
    '€': 0x24,  # $ als Ersatz für Euro
    '°': 0x5E,  # ^ als Ersatz für Grad
    '´': 0x27,  # ' als Ersatz für Akzent
    '`': 0x27,  # ' als Ersatz für Gravis
}

# Tkinter KeySyms zu PETSCII Mapping
KEYSYM_TO_PETSCII = {
    # Control Keys
    'Return': C64_KEYS['RETURN'],
    'BackSpace': C64_KEYS['DELETE'],
    'Delete': C64_KEYS['DELETE'],
    'Insert': C64_KEYS['INSERT'],
    'Home': C64_KEYS['HOME'],
    'End': C64_KEYS['CLR'],  # End = CLR
    
    # Cursor Keys
    'Up': C64_KEYS['CRSR_UP'],
    'Down': C64_KEYS['CRSR_DOWN'],
    'Left': C64_KEYS['CRSR_LEFT'],
    'Right': C64_KEYS['CRSR_RIGHT'],
    
    # Function Keys (F1-F8)
    'F1': C64_KEYS['F1'],
    'F2': C64_KEYS['F2'],
    'F3': C64_KEYS['F3'],
    'F4': C64_KEYS['F4'],
    'F5': C64_KEYS['F5'],
    'F6': C64_KEYS['F6'],
    'F7': C64_KEYS['F7'],
    'F8': C64_KEYS['F8'],
    
    # Special Keys
    'Escape': C64_KEYS['STOP'],  # ESC = RUN/STOP
    'Tab': 0x09,                 # Tab
    'space': 0x20,               # Space
    
    # Numpad
    'KP_0': 0x30, 'KP_1': 0x31, 'KP_2': 0x32, 'KP_3': 0x33,
    'KP_4': 0x34, 'KP_5': 0x35, 'KP_6': 0x36, 'KP_7': 0x37,
    'KP_8': 0x38, 'KP_9': 0x39,
    'KP_Add': 0x2B,      # +
    'KP_Subtract': 0x2D, # -
    'KP_Multiply': 0x2A, # *
    'KP_Divide': 0x2F,   # /
    'KP_Enter': 0x0D,    # Enter
    'KP_Decimal': 0x2E,  # .
    
    # Deutsche Tastatur KeySyms
    'adiaeresis': 0x7B,    # ä
    'odiaeresis': 0x7C,    # ö
    'udiaeresis': 0x7D,    # ü
    'Adiaeresis': 0x5B,    # Ä
    'Odiaeresis': 0x5C,    # Ö
    'Udiaeresis': 0x5D,    # Ü
    'ssharp': 0x7E,        # ß
    'section': 0x40,       # §
    'degree': 0x5E,        # °
    'EuroSign': 0x24,      # €
}

# Spezial-Kombinationen (mit Shift/Control/Commodore)
# Shift+Key Kombinationen
SHIFT_COMBINATIONS = {
    'Home': C64_KEYS['CLR'],         # Shift+Home = CLR
    'Insert': C64_KEYS['INSERT'],     # Shift+Delete = INSERT
    'F1': C64_KEYS['F2'],             # Shift+F1 = F2
    'F3': C64_KEYS['F4'],             # Shift+F3 = F4
    'F5': C64_KEYS['F6'],             # Shift+F5 = F6
    'F7': C64_KEYS['F8'],             # Shift+F7 = F8
}

# Control+Key Kombinationen (STRG-Taste)
# STRG+A bis STRG+Z sendet Control Codes 0x01-0x1A
CONTROL_COMBINATIONS = {
    # Control+Zahl = Farben
    '1': C64_KEYS['BLACK'],
    '2': C64_KEYS['WHITE'],
    '3': C64_KEYS['RED'],
    '4': C64_KEYS['CYAN'],
    '5': C64_KEYS['PURPLE'],
    '6': C64_KEYS['GREEN'],
    '7': C64_KEYS['BLUE'],
    '8': C64_KEYS['YELLOW'],
    '9': C64_KEYS['RVS_ON'],    # Control+9 = RVS ON
    '0': C64_KEYS['RVS_OFF'],   # Control+0 = RVS OFF
    
    # Control+Buchstaben = Control Codes (0x01-0x1A)
    # STRG+A = 0x01, STRG+B = 0x02, ... STRG+Z = 0x1A
    'a': 0x01, 'A': 0x01,  # Ctrl+A
    'b': 0x02, 'B': 0x02,  # Ctrl+B  
    'c': 0x03, 'C': 0x03,  # Ctrl+C (STOP auf C64)
    'd': 0x04, 'D': 0x04,  # Ctrl+D
    'e': 0x05, 'E': 0x05,  # Ctrl+E (auch WHITE)
    'f': 0x06, 'F': 0x06,  # Ctrl+F
    'g': 0x07, 'G': 0x07,  # Ctrl+G (BELL)
    'h': 0x08, 'H': 0x08,  # Ctrl+H
    'i': 0x09, 'I': 0x09,  # Ctrl+I (TAB)
    'j': 0x0A, 'J': 0x0A,  # Ctrl+J (LF)
    'k': 0x0B, 'K': 0x0B,  # Ctrl+K
    'l': 0x0C, 'L': 0x0C,  # Ctrl+L
    'm': 0x0D, 'M': 0x0D,  # Ctrl+M (RETURN)
    'n': 0x0E, 'N': 0x0E,  # Ctrl+N (Charset Lower)
    'o': 0x0F, 'O': 0x0F,  # Ctrl+O
    'p': 0x10, 'P': 0x10,  # Ctrl+P
    'q': 0x11, 'Q': 0x11,  # Ctrl+Q (Cursor Down)
    'r': 0x12, 'R': 0x12,  # Ctrl+R (RVS ON)
    's': 0x13, 'S': 0x13,  # Ctrl+S (HOME)
    't': 0x14, 'T': 0x14,  # Ctrl+T (DEL)
    'u': 0x15, 'U': 0x15,  # Ctrl+U
    'v': 0x16, 'V': 0x16,  # Ctrl+V
    'w': 0x17, 'W': 0x17,  # Ctrl+W
    'x': 0x18, 'X': 0x18,  # Ctrl+X
    'y': 0x19, 'Y': 0x19,  # Ctrl+Y
    'z': 0x1A, 'Z': 0x1A,  # Ctrl+Z
}

# Commodore+Key Kombinationen (Alt-Taste = Commodore auf PC)
# Extended Colors (Cbm+1 bis Cbm+8)
COMMODORE_COMBINATIONS = {
    # Colors
    '1': 0x81,  # Cbm+1 = Orange
    '2': 0x95,  # Cbm+2 = Brown
    '3': 0x96,  # Cbm+3 = Light Red
    '4': 0x97,  # Cbm+4 = Dark Gray
    '5': 0x98,  # Cbm+5 = Gray
    '6': 0x99,  # Cbm+6 = Light Green
    '7': 0x9A,  # Cbm+7 = Light Blue
    '8': 0x9B,  # Cbm+8 = Light Gray
    '9': 0x0E,  # Cbm+9 = Charset Lower
    '0': 0x8E,  # Cbm+0 = Charset Upper
    
    # Graphics Characters (Cbm+A-Z) - PETSCII 0xC1-0xDA
    # In Uppercase Mode: Shows graphic symbols
    'a': 0xC1, 'A': 0xC1,  # ├
    'b': 0xC2, 'B': 0xC2,  # ┤
    'c': 0xC3, 'C': 0xC3,  # ┬
    'd': 0xC4, 'D': 0xC4,  # ┴
    'e': 0xC5, 'E': 0xC5,  # ┼
    'f': 0xC6, 'F': 0xC6,  # ╮
    'g': 0xC7, 'G': 0xC7,  # ╰
    'h': 0xC8, 'H': 0xC8,  # ╯
    'i': 0xC9, 'I': 0xC9,  # ╲
    'j': 0xCA, 'J': 0xCA,  # ╱
    'k': 0xCB, 'K': 0xCB,  # ○
    'l': 0xCC, 'L': 0xCC,  # ●
    'm': 0xCD, 'M': 0xCD,  # ◆
    'n': 0xCE, 'N': 0xCE,  # ┃
    'o': 0xCF, 'O': 0xCF,  # ╭
    'p': 0xD0, 'P': 0xD0,  # ─
    'q': 0xD1, 'Q': 0xD1,  # ╳
    'r': 0xD2, 'R': 0xD2,  # ♠
    's': 0xD3, 'S': 0xD3,  # ♣
    't': 0xD4, 'T': 0xD4,  # ♥
    'u': 0xD5, 'U': 0xD5,  # ♦
    'v': 0xD6, 'V': 0xD6,  # ▌
    'w': 0xD7, 'W': 0xD7,  # ▐
    'x': 0xD8, 'X': 0xD8,  # ▀
    'y': 0xD9, 'Y': 0xD9,  # ▄
    'z': 0xDA, 'Z': 0xDA,  # █
    
    # Graphics Symbols (Cbm+Shift+Keys)
    '-': 0xDD,  # ▔
    '+': 0xDB,  # ▁
    '*': 0xC0,  # ─ (horizontal line)
    '@': 0xBA,  # ▌ (left half block)
    '[': 0xBB,  # ▐ (right half block)  
    ']': 0xAE,  # ▀ (upper half block)
    ';': 0xBC,  # ▄ (lower half block)
    ':': 0xBD,  # ▌ (left half)
}

# Shift+Buchstaben = Graphics Characters (PETSCII 0x41-0x5A sind in Shifted Mode grafisch!)
# Nur wenn im Graphics Charset Mode (Shift+C= aktiviert)
# Diese werden automatisch von KEYBOARD_MAPPING behandelt als uppercase
# Zusätzliche Shift-Graphics für Sonderzeichen:
SHIFT_GRAPHICS = {
    # Shifted Graphics-Zeichen auf Sondertasten
    # Diese erzeugen Box-Drawing Zeichen im C64
    '!': 0x21,  # !
    '"': 0x22,  # "
    '#': 0x23,  # #
    '$': 0x24,  # $
    '%': 0x25,  # %
    '&': 0x26,  # &
    "'": 0x27,  # '
    '(': 0x28,  # (
    ')': 0x29,  # )
}


def get_petscii_for_key(char, keysym, shift=False, ctrl=False, alt=False):
    """
    Konvertiert Tastendruck zu PETSCII Code
    
    Args:
        char: Character string (z.B. 'a', '1', etc.)
        keysym: Tkinter KeySym (z.B. 'Return', 'F1', 'a', 'A', etc.)
        shift: Shift-Taste gedrückt
        ctrl: Control-Taste gedrückt (= STRG auf PC)
        alt: Alt-Taste gedrückt (= Commodore-Taste auf C64)
        
    Returns:
        PETSCII byte code oder None
    """
    
    # Bei STRG/Alt ist char oft leer, nutze keysym als Fallback
    key_char = char if char else keysym
    
    # Z/Y Swap für US Tastaturlayout - auf BEIDE anwenden
    if _swap_zy:
        if key_char:
            if key_char.lower() == 'z':
                key_char = 'y' if key_char.islower() else 'Y'
            elif key_char.lower() == 'y':
                key_char = 'z' if key_char.islower() else 'Z'
        # Auch keysym swappen für STRG-Kombinationen
        if keysym:
            if keysym.lower() == 'z':
                keysym = 'y' if keysym.islower() else 'Y'
            elif keysym.lower() == 'y':
                keysym = 'z' if keysym.islower() else 'Z'
    
    # Control-Kombinationen (STRG-Taste) - STRG+A bis STRG+Z
    if ctrl:
        # Prüfe keysym für Buchstaben (bei STRG ist char oft leer!)
        ctrl_key = keysym.lower() if keysym else ''
        if ctrl_key in CONTROL_COMBINATIONS:
            return CONTROL_COMBINATIONS[ctrl_key]
        # Auch char prüfen falls vorhanden
        if key_char and key_char.lower() in CONTROL_COMBINATIONS:
            return CONTROL_COMBINATIONS[key_char.lower()]
    
    # Commodore-Kombinationen (Alt = Commodore auf PC)
    if alt:
        if key_char in COMMODORE_COMBINATIONS:
            return COMMODORE_COMBINATIONS[key_char]
        # Auch keysym prüfen
        if keysym and keysym in COMMODORE_COMBINATIONS:
            return COMMODORE_COMBINATIONS[keysym]
    
    # Shift-Kombinationen für Sondertasten
    if shift and keysym in SHIFT_COMBINATIONS:
        return SHIFT_COMBINATIONS[keysym]
    
    # KeySym-Mapping (Sondertasten + deutsche Umlaute)
    if keysym in KEYSYM_TO_PETSCII:
        return KEYSYM_TO_PETSCII[keysym]
    
    # Character-Mapping (normale Zeichen)
    if key_char and key_char in KEYBOARD_MAPPING:
        return KEYBOARD_MAPPING[key_char]
    
    # Fallback für unbekannte Tasten
    return None


def is_printable_key(keysym):
    """Prüft ob Taste ein druckbares Zeichen ist"""
    non_printable = [
        'Shift_L', 'Shift_R', 'Control_L', 'Control_R',
        'Alt_L', 'Alt_R', 'Super_L', 'Super_R',
        'Caps_Lock', 'Num_Lock', 'Scroll_Lock',
        'Print', 'Pause', 'Break',
        'F9', 'F10', 'F11', 'F12',  # F9-F12 nicht auf C64
        'Menu', 'Win_L', 'Win_R'
    ]
    return keysym not in non_printable


if __name__ == "__main__":
    # Test
    print("C64 Keyboard Mapping Test")
    print("=" * 60)
    
    # Test normale Zeichen
    print("\nNormale Zeichen:")
    for char in "HELLO WORLD!":
        code = get_petscii_for_key(char, '', False, False, False)
        print(f"  '{char}' -> 0x{code:02X}")
    
    # Test Sondertasten
    print("\nSondertasten:")
    for key in ['Return', 'Home', 'Up', 'F1', 'F3']:
        code = get_petscii_for_key('', key, False, False, False)
        print(f"  {key} -> 0x{code:02X}")
    
    # Test Shift-Kombinationen
    print("\nShift-Kombinationen:")
    for key in ['Home', 'F1']:
        code = get_petscii_for_key('', key, True, False, False)
        print(f"  Shift+{key} -> 0x{code:02X}")
    
    # Test Control-Kombinationen (Farben)
    print("\nControl-Kombinationen (Farben):")
    for num in ['1', '2', '3', '4', '5', '6', '7', '8']:
        code = get_petscii_for_key(num, '', False, True, False)
        print(f"  Ctrl+{num} -> 0x{code:02X}")
    
    # Test Commodore-Kombinationen (Extended Colors)
    print("\nCommodore-Kombinationen (Extended Colors):")
    for num in ['1', '2', '3', '4', '5', '6', '7', '8']:
        code = get_petscii_for_key(num, '', False, False, True)
        if code:
            print(f"  Cbm+{num} -> 0x{code:02X}")
