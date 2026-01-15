# PYCGMS V1.0 Terminal Client
**by lA-sTYLe/Quantum (2026)**

Modern PETSCII-capable BBS terminal with multi-protocol file transfer support.

## 🚀 Features

### Display & Input
- **PETSCII Support** - Full C64 character set rendering
- **Custom Fonts** - C64 Pro Mono for authentic look
- **Color Mapping** - All 16 C64 colors
- **40/80 Column Mode** - Live switching with F6
  - 40 columns for authentic C64 look
  - 80 columns for modern readability
  - Instant toggle without reconnecting
- **Scrollback Buffer** - Configurable history
  - Default: 1000 lines
  - Range: 100-10000 lines
  - Scroll with PgUp/PgDn or mouse wheel
  - Search through history
- **Keyboard Mapping** - PETSCII character support
- **Auto-Login** - Save credentials and auto-connect
- **Traffic Logger** - F12 to capture all send/receive data

### File Transfers
- **XModem** - Classic protocol
- **XModem-CRC** - With error detection  
- **XModem-1K** - Faster 1K blocks
- **YModem** - Smart batch transfers
  - **Single file** → Auto XModem-1K (no header)
  - **Multiple files** → YModem Batch (with headers)
  - Automatic .prg extension handling
  - Progress display with filename and percentage
- **TurboModem** - Ultra-fast custom protocol
  - **4096-byte blocks** - Massive data chunks
  - **CRC-32 checksums** - Maximum data integrity
  - **20-25x faster** than XModem
  - Custom implementation by lA-sTYLe/Quantum
  - Compatible with PYCGMS BBS Server

### Tools (Alt+T)
- **ZIP Code Converter** - Convert ZIP codes to PETSCII format
- **ZIP to D64** - Extract ZIPs to C64 disk images
- **LNX to D64** - Convert Lynx archives to D64 format

### Connection
- **Telnet** - Standard BBS protocol
- **Multiple Profiles** - Save favorite BBSs
- **Auto-Reconnect** - Connection monitoring
- **Encoding** - PETSCII, Latin-1, UTF-8 support

## 📋 Requirements

### System Requirements
- **OS:** Windows, Linux, or macOS
- **Python:** 3.8 or higher
- **Display:** 800x600 minimum

### Python Packages
```bash
pip install pillow
```

### Font
- **C64 Pro Mono** (included in package)
- Automatically loaded from `fonts/` directory

## 🔧 Installation

### Windows

1. **Install Python**
   - Download from python.org
   - Check "Add Python to PATH" during installation

2. **Install Dependencies**
```cmd
pip install pillow
```

3. **Extract Package**
```cmd
# Unzip pycgms-client-v1.0.zip to C:\PYCGMS
cd C:\PYCGMS
```

4. **Run Terminal**
```cmd
python bbs_terminal.py
```

### Linux/macOS

1. **Install Python** (usually pre-installed)
```bash
python3 --version  # Should be 3.8+
```

2. **Install Dependencies**
```bash
pip3 install pillow
```

3. **Extract Package**
```bash
unzip pycgms-client-v1.0.zip
cd pycgms-client
```

4. **Run Terminal**
```bash
python3 bbs_terminal.py
```

## 📖 Usage

### First Run

1. **Launch Terminal**
   ```
   python bbs_terminal.py
   ```

2. **Add BBS**
   - Click "Add BBS" or press Alt+N
   - Enter BBS details:
     - Name: "My Favorite BBS"
     - Host: "bbs.example.com"
     - Port: 6400
     - Auto-login: Optional

3. **Connect**
   - Double-click BBS entry
   - Or select and click "Connect"

### Keyboard Shortcuts

| Key | Function |
|-----|----------|
| **F1** | Upload file |
| **F2** | Send text file (Latin-1) |
| **F3** | Disconnect |
| **F5** | Clear screen |
| **F6** | Toggle 40/80 column mode |
| **F7** | Change protocol |
| **F12** | Toggle traffic logger |
| **Alt+T** | Open Tools menu |
| **PgUp** | Scroll up |
| **PgDn** | Scroll down |
| **Ctrl+C** | Copy selection |
| **Ctrl+V** | Paste |
| **Ctrl+,** | Settings |

### File Uploads

#### Single File Upload
```
1. Press F1
2. Select file to upload
3. Transfer starts automatically
   - YModem selected? → Uses XModem-1K (no header)
   - XModem selected? → Uses XModem
```

#### Multiple File Upload  
```
1. Press F1
2. Hold Ctrl and click multiple files
3. Transfer starts automatically
   - YModem selected? → Uses YModem Batch (with headers)
   - Shows: "📤 Batch upload: 3 files using YModem"
```

### File Downloads

Downloads are initiated from BBS side:
```
BBS: "Start your download now"
Terminal: Automatically receives file
Progress: "filename.prg: Block 5 (5120/43434 bytes, 11%)"
```

**Features:**
- Shows filename during download
- Real-time progress percentage
- Automatic .prg extension if missing
- Save dialog for XModem-1K (when BBS sends no filename)

### Traffic Logger (F12)

Press **F12** to capture all data:
```
======================================================================
TRAFFIC LOG STARTED: 2026-01-02 14:28:39
======================================================================

[14:28:41.896] SEND → | 4E
                  ASCII | N
                  LEN   | 1 bytes

[14:28:42.002] RECV ← | 93 0D 20 20
                  ASCII | ..  
                  LEN   | 4 bytes
```

**Saved to:** `traffic_log_YYYYMMDD_HHMMSS.txt`

### Protocol Selection

Press **F7** to change protocol:
```
Available Protocols:
1. XMODEM
2. XMODEMCRC
3. XMODEM1K
4. YMODEM      ← Recommended!
5. TURBOMODEM  ← Ultra-fast!

Select: 4
Protocol: YMODEM
```

### Tools Menu (Alt+T)

Access built-in conversion tools:

#### ZIP Code Converter
- Convert ZIP codes to C64 PETSCII format
- Generates SEQ files for BBS upload
- Proper PETSCII encoding

#### ZIP to D64 Converter
- Extract files from ZIP archives
- Convert to D64 disk images
- Preserve file structure
- C64-compatible output

#### LNX to D64 Converter
- Convert Lynx archives to D64 format
- Extract individual files
- Maintain directory structure
- Ready for C64 emulators

**Usage:**
```
1. Press Alt+T to open Tools menu
2. Select desired tool
3. Follow on-screen prompts
4. Output files ready to use!
```

### Display Modes

#### 40/80 Column Toggle (F6)

Switch display width on-the-fly:

**40 Column Mode:**
```
- Authentic C64 experience
- Matches original hardware
- Better for PETSCII graphics
- Ideal for classic BBSs
```

**80 Column Mode:**
```
- Modern readability
- More text visible
- Better for long messages
- Useful for file listings
```

**Toggle anytime:**
- Press F6 during session
- No reconnection needed
- Setting persists per BBS
- Instant visual update

### Scrollback Buffer

Review conversation history:

**Features:**
- **Default:** 1000 lines kept in memory
- **Range:** 100-10000 lines (configurable)
- **Navigation:** PgUp/PgDn or mouse wheel
- **Search:** Find text in history
- **Copy:** Select and copy any previous text

**Configure in Settings (Ctrl+,):**
```
Scrollback Buffer: [1000] lines
```

**Memory Usage:**
- 1000 lines ≈ 100-200 KB
- 10000 lines ≈ 1-2 MB
- Adjust based on your needs

## ⚙️ Settings

### Download/Upload Folders

Set default folders in Settings (Ctrl+,):
```
Download Folder: C:\Users\YourName\Downloads\BBS
Upload Folder: C:\Users\YourName\BBS\Uploads
```

### Display Settings

- **Font Size:** 8-16pt (default: 10pt)
- **Column Mode:** 40 or 80 columns (toggle with F6)
- **Encoding:** PETSCII, Latin-1, UTF-8
- **Colors:** Enable/Disable PETSCII colors
- **Scrollback:** 100-10000 lines (default: 1000)

### Auto-Login

Save login credentials in BBS entry:
```
Username: myhandle
Password: mypassword
Auto-login: ✓ Enabled
```

**Security Note:** Passwords stored in plain text locally!

## 🎯 Protocol Features

### YModem Smart Mode

**Single File:**
```
User selects: mygame.prg (1 file)
↓
Terminal uses: XModem-1K (no header)
↓
Fast transfer, BBS doesn't need YModem support
```

**Multiple Files:**
```
User selects: game1.prg, game2.prg, game3.prg (3 files)
↓
Terminal uses: YModem Batch (with headers)
↓
Each file sent with name and size
```

### Extension Handling

**Upload:**
```
File on disk: mygame.prg
Header sent: "mygame.prg" (preserved)

File on disk: data (no extension)
Header sent: "data.prg" (auto-added)
```

**Download:**
```
BBS sends: "mygame" (no extension)
Saved as: "mygame.prg" (auto-added)

BBS sends: "archive.d64"  
Saved as: "archive.d64" (preserved)
```

## 📊 Transfer Progress

### During Upload
```
📤 Single file upload: Using XModem-1K
Protocol: XMODEM-1K
File: mygame.prg

Uploading... (Progress bar)
```

### During Download
```
mygame.prg: Block 15 (15360/43434 bytes, 35%)
mygame.prg: Block 16 (16384/43434 bytes, 37%)
mygame.prg: Block 17 (17408/43434 bytes, 40%)
```

## 🐛 Troubleshooting

### Display Issues

**Problem:** Characters look wrong/blocky

**Solution:** 
1. Check font installed: `fonts/C64_Pro_Mono-STYLE.ttf`
2. Restart terminal
3. Try different font size in Settings

### Connection Problems

**Problem:** Cannot connect to BBS

**Solution:**
1. Check host/port correct
2. Try Telnet manually: `telnet bbs.example.com 6400`
3. Check firewall settings
4. Some BBSs use different ports (23, 6400, 6502)

### Upload Fails

**Problem:** File upload starts but fails

**Solution:**
1. Check protocol matches BBS (use YModem for compatibility)
2. File size < 64KB? Try XModem
3. Check traffic log (F12) for errors
4. Some BBSs need specific protocol (ask sysop)

### Download Filename Wrong

**Problem:** Downloaded file has wrong name

**Solution:**
1. XModem-1K: Shows save dialog (no filename from BBS)
2. YModem: Uses filename from BBS header
3. If BBS uses XModem-1K, you'll always get save dialog

## 📁 Files Included

```
pycgms-client/
├── bbs_terminal.py        # Main terminal application
├── file_transfer.py       # YModem/XModem protocol
├── telnet_client.py       # Telnet connection handler
├── c64_keyboard.py        # PETSCII keyboard mapping
├── bbs_connection.py      # BBS connection manager
├── fonts/
│   └── C64_Pro_Mono-STYLE.ttf  # C64 font
├── README.md              # This file
└── LICENSE.txt            # License information
```

## 🎨 Color Scheme

PETSCII colors fully supported:
```
0 = Black          8 = Orange
1 = White          9 = Brown  
2 = Red            10 = Light Red
3 = Cyan           11 = Dark Gray
4 = Purple         12 = Gray
5 = Green          13 = Light Green
6 = Blue           14 = Light Blue
7 = Yellow         15 = Light Gray
```

## 📝 Version History

### v1.0 (January 2026)
- Initial release as PYCGMS
- Complete YModem implementation
- Smart protocol detection (single/batch)
- TurboModem support (ultra-fast transfers)
- Multi-file upload support
- Traffic logger (F12)
- Extension auto-handling
- Progress display with filename
- Save dialog for XModem-1K
- **40/80 column toggle (F6)**
- **Scrollback buffer (configurable)**
- **Tools menu (Alt+T):**
  - ZIP code converter
  - ZIP to D64 converter
  - LNX to D64 converter

### Previous (v3.3)
- PETSCII terminal with basic transfers
- XModem support
- Auto-login

## 👥 Credits

**Developer:** lA-sTYLe/Quantum  
**Year:** 2026  
**Font:** C64 Pro Mono by Style  
**License:** Free for personal use

**Special Thanks:**
- C64 scene for PETSCII specifications
- BBS community for protocol documentation
- Python xmodem library authors

## 📧 Support

For bugs or feature requests:
- Check GitHub for updates
- Contact: [your contact info]
- BBS: [your BBS info]

## 🎮 Tips & Tricks

### Fast Downloads
```
1. Use YModem protocol (F7)
2. BBS sends XModem-1K for single files
3. Much faster than standard XModem
```

### Batch Operations
```
1. Select multiple files (Ctrl+Click)
2. Upload sends all with YModem Batch
3. BBS receives with filenames intact
```

### Debugging
```
1. Press F12 before transfer
2. Traffic log captures all data
3. Check traffic_log_*.txt for issues
4. Send log to sysop if problems
```

### Auto-Login
```
1. Edit BBS entry
2. Enter username/password
3. Enable auto-login
4. Connect → Automatic login!
```

---

**PYCGMS V1.0** - The Modern PETSCII Terminal for Classic BBSs

**Enjoy connecting to the world of C64 BBSs!** 🎮📡
