"""
PETSCII BBS Terminal v3.3 - Starter
"""

import sys
import os

# Prüfe Python-Version
if sys.version_info < (3, 8):
    print("ERROR: Python 3.8 oder höher benötigt!")
    print(f"Aktuelle Version: {sys.version}")
    sys.exit(1)

# Prüfe Pillow
try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow nicht installiert!")
    print("Installiere mit: pip install pillow")
    sys.exit(1)

# Prüfe benötigte Dateien
required_files = [
    'bbs_terminal.py',
    'petscii_parser.py',
    'petscii_screencode.py',
    'petscii_charset.py',
    'c64_rom_renderer.py',
    'c64_keyboard.py',
    'telnet_client.py',
    'file_transfer.py',
    'terminal_extensions.py',
    'upper.bmp',
    'lower.bmp'
]

missing = [f for f in required_files if not os.path.exists(f)]
if missing:
    print("ERROR: Fehlende Dateien:")
    for f in missing:
        print(f"  - {f}")
    sys.exit(1)

# Starte Terminal
print("Starting PETSCII BBS Terminal v3.3...")
print()

from bbs_terminal import BBSTerminal

if __name__ == '__main__':
    try:
        app = BBSTerminal()
        app.mainloop()
    except KeyboardInterrupt:
        print("\nTerminal beendet.")
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
