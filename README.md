# PYCGMS - PETSCII BBS Terminal

A modern Python-based terminal emulator for connecting to Commodore 64 BBS systems via Telnet. Features authentic PETSCII rendering using original C64 ROM fonts, file transfer support, and a full-featured macro/hotkey editor.

**Version:** 1.0  
**Author:** lA-sTYLe/Quantum (2026)

## Features

### 🖥️ Authentic C64 Display
- **Original C64 ROM fonts** (upper.bmp, lower.bmp) for pixel-perfect PETSCII rendering
- **Full 16-color C64 palette support** 
- **40/80 column modes live switchable** 
- **Auto-scaling zoom** (1x-6x) based on window size

### 📡 Connection
- **Telnet connections** to any C64 BBS
- **Phonebook** with saved BBS entries including auto-login credentials
- **Auto-login** (F9) sends username/password automatically
- **optional Connection with Protocol Detailed logging for debugging** 

### 📁 File Transfer Protocols
- **YModem Batch** - Multiple files with headers
- **XModem-1K** - Single file, 1024-byte blocks
- **XModem-CRC** - 128-byte blocks with CRC
- **XModem** - Basic 128-byte blocks with checksum
- **TurboModem** - High-speed custom protocol with batchmode
- **RAWTCP** - High-speed custom RAW Stream protocol with batchmode
- **Punter** - C64 native protocol

### ⌨️ Hotkey System
- **Ctrl+Alt+F1 to F10** (or AltGr+F1-F10) - Customizable macros
- **Full PETSCII support** 
- **Visual hotkey editor** with:
  - Clickable C64 color palette
  - Clickable character grid rendered from fonts
  - Live PETSCII preview
  - Cursor navigation with arrow keys

### 📜 Scrollback Buffer
- **F4** to view scrollback
- **Search functionality**
- **Copy to clipboard**

## Installation
- see seprate file

### Requirements
- Python 3.8+
- Pillow (PIL)
- Pygame
- tkinter (usually included with Python)

### Install Dependencies
```bash
pip install pillow
pip install pygame
```

### Required Font Files
Place these files in the same directory as the terminal:
- `upper.bmp` - C64 uppercase/graphics charset
- `lower.bmp` - C64 lowercase charset

## Usage

### Starting the Terminal
```bash
python bbs_terminal.py
```

Or use the launcher script:
```bash
python run_terminal.py
```

### Keyboard Shortcuts

| Key | Function |
|-----|----------|
| **F1** | Upload file(s) |
| **F2** | Send text file (SEQ or Text)|
| **F3** | Download file |
| **F5** | Settings dialog |
| **F7** | Dial/Connect to BBS |
| **F9** | Send auto-login |
| **F11** | Toggle fullscreen |
| **F12** | Enable/Disable Connection logging to File |
| **Alt+H** | Hotkey editor |
| **Alt+S** | Screenshot of current Terminal Window |
| **Alt+P** | Toggle protocol |
| **Ctrl+1-8** | C64 basic colors |
| **Alt+1-8** | C64 extended colors |
| **Ctrl+Alt+F1-F10** | Send hotkey macro |

### C64 Keyboard Mapping

The terminal maps your keyboard to C64 PETSCII codes:

| PC Key | C64 Function |
|--------|--------------|
| Ctrl+1-8 | Colors (Black, White, Red, Cyan, Purple, Green, Blue, Yellow) |
| Ctrl+9 | RVS ON (Reverse video) |
| Ctrl+0 | RVS OFF |
| Alt+1-8 | Extended colors (Orange, Brown, Lt Red, Dk Gray, Gray, Lt Green, Lt Blue, Lt Gray) |
| Shift+Letters | Uppercase / Graphics mode chars |

## Configuration

### Settings (F5)
- **Download folder** - Where to save downloaded files
- **Upload folder** - Default folder for uploads
- **Screen width** - 40 or 80 columns
- **Transfer protocol** - Select default protocol with optinal selectable Pause Delays
- **Telnet logging** - Enable/disable connection logging
- **Debug ** - Enable/disable connection Debug output

### Phonebook
Save frequently used BBS connections with:
- Host and port
- Username and password for auto-login
- Character delay settings

### Hotkeys File
Hotkeys are stored in `hotkeys.seq` - a binary file with PETSCII sequences separated by CR (0x0D).

## File Transfer

### Uploading Files
1. Press **F1** or use menu Transfer → Upload
2. Select file(s) - multiple selection supported
3. Single file uses XModem-1K, multiple files use YModem Batch
4. Progress shows: `File X/Y: filename (bytes/total bytes)`

### Downloading Files
1. Initiate download on BBS
2. Press **F3** or use menu Transfer → Download
3. Files are saved to configured download folder
4. YModem/TurboModem/Multi Punter automatically receive filename from sender
5. Files without extension get `.prg` added automatically

### Transfer Timing
The terminal includes configurable delays for compatibility with different BBS systems:
- `INTER_BLOCK_DELAY` - Pause between blocks (default: 150ms)
- Adjustable in `file_transfer.py` for local vs. internet connections
- Local connections may need higher delays (200-300ms)

### Debug Logs
Transfer operations create debug logs:
- `transfer_debug_YYYYMMDD_HHMMSS.log`
- Contains detailed block-by-block transfer information

## Hotkey Editor

Access with **Alt+H** to create powerful PETSCII macros:

### Color Palette
Click any of the 16 C64 colors to insert the corresponding PETSCII color code at cursor position.

### Character Grid
Shows all 224 printable characters (0x20-0xFF) rendered from the actual C64 font. Click to insert at cursor position.

### Editor Features
- **Arrow keys** - Navigate cursor position in buffer
- **Home/End** - Jump to start/end of buffer
- **Backspace** - Delete byte before cursor
- **Delete** - Delete byte at cursor
- **Live preview** - See rendered output in real-time
- **Cursor shows current color** - Filled block in current foreground color
- **Position indicator** - `[Cursor at byte X/Y]`

### Example Hotkey: Colored Login
```
[WHT]myusername[CR][GRN]mypassword[CR]
```
This sends white username, press Enter, green password, press Enter.

## Troubleshooting

### Transfer Issues

**Bad blocks on local connection:**
- Increase `INTER_BLOCK_DELAY` in `file_transfer.py` from 0.15 to 0.2 or 0.3
- Local connections are often too fast for the BBS to process

**Transfer fails at first block:**
- Try XModem-CRC instead of YModem
- Some BBS don't support YModem batch mode

**Multiple NAKs in log:**
- BBS may need hardware flow control (RTS/CTS) 
- Try smaller files or different protocol

**"No CRC request" error:**
- BBS took too long to respond
- Connection may have dropped

### Display Issues
- **Wrong characters** - Ensure `upper.bmp` and `lower.bmp` are in terminal directory
- **Colors wrong** - Check your system's color depth
- **Blurry display** - Try fullscreen (F11) for pixel-perfect rendering
- **Cursor wrong color** - Update to latest version

### Connection Issues
- **Can't connect** - Verify host:port, check firewall
- **Connection drops** - Some BBS have idle timeouts
- **Garbled text** - Try 80-column mode, or check BBS settings

## File Structure

```
terminal/
├── bbs_terminal.py       # Main terminal application
├── petscii_parser.py     # PETSCII code parser
├── c64_rom_renderer.py   # Font rendering engine with cache
├── c64_keyboard.py       # Keyboard to PETSCII mapping
├── telnet_client.py      # Telnet connection handler
├── file_transfer.py      # File transfer protocols (XModem, YModem, etc.)
├── terminal_extensions.py # Scrollback buffer
├── run_terminal.py       # Launcher script
├── upper.bmp             # C64 uppercase font (required)
├── lower.bmp             # C64 lowercase font (required)
├── c64_colors.png        # Color palette for hotkey editor
├── phonebook.json        # Saved BBS entries (created automatically)
├── hotkeys.seq           # Saved hotkey macros (created automatically)
├── settings.json         # Application settings (created automatically)
└── README.md             # This file
```

## Technical Details

### PETSCII Handling
- Full PETSCII code support (0x00-0xFF)
- Screen codes converted for font rendering
- Color codes tracked per-character
- Reverse video state maintained per-character

### Screen Buffer
- 40x25 or 80x25 character buffer
- Per-character color attributes (foreground)
- Per-character reverse video flag
- Cursor position tracking (x, y)
- Current foreground color state

### Font Cache
- Pre-rendered characters cached by (screen_code, color, reverse) tuple
- Fast O(1) lookup for real-time display
- Supports all 16 colors × 256 characters × 2 (normal/reverse)
- Cache persists across frames for smooth rendering

### Transfer Protocol Details

**YModem Batch:**
- Block 0: Header with filename and size
- Block 1+: 1024-byte data blocks with CRC-16
- EOT acknowledgment sequence
- NULL header for end-of-batch

**XModem-1K:**
- 1024-byte blocks with CRC-16
- No filename header (single file only)
- Simpler handshake than YModem

**TurboModem:**
- Custom high-speed protocol
- Automatic filename transfer
- Optimized for C64 serial speed

**Punter:**
- Native Üunter C1 implementation for up and Download

## Known Limitations

- Punter protocol is experimental
- 80-column mode may not work correctly with all BBS
- Some PETSCII control codes not fully implemented

## Contributing

This is a hobby project. Feel free to fork and improve!

Potential improvements:
- ZModem protocol
- ANSI escape code support for modern BBS
- SSH connections

## License

This software is provided as-is for connecting to Commodore 64 BBS systems. The C64 fonts are based on the original Commodore 64 character ROM.

## Version History

### v1.0 (2026)
- Initial release
- Full PETSCII support with C64 ROM fonts
- YModem, XModem, TurboModem, Punter protocols
- Hotkey editor with visual palettes
- Scrollback buffer
- Phonebook with auto-login
- Transfer progress with file/byte counters

## Credits

- C64 Font rendering based on original Commodore 64 character ROM
- PETSCII tables from various C64 documentation sources
- File transfer protocols based on original specifications
- Inspired by CGTerm, SyncTerm, CCGMSx and other classic terminals

**Code:** lA-sTYLe/Quantum (2026)
**Test:** Larry/Role (2026) thanks a lot for Patience and Bug Reports :-)

**Tested on the following Boards:**

- The Hiden
- Raveolution
- Rapidfire
- Friabad

**Happy BBS'ing!** 🖥️📞

*"The BBS is dead, long live the BBS!"*
