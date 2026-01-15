# PYCGMS V1.0 - Installation Guide

Quick installation instructions for both Windows and Linux.

## Windows Installation

### Automatic Installation (Recommended)

1. **Extract ZIP file**
   ```
   Right-click pycgms-client-v1.0.zip → Extract All
   ```

2. **Run Installer**
   ```
   Double-click: install-windows.bat
   ```

3. **Follow prompts**
   - Installer checks Python
   - Installs required packages
   - Creates desktop shortcut
   - Launches PYCGMS

### Manual Installation

If automatic installer doesn't work:

1. **Install Python 3.8+**
   - Download from: https://python.org/downloads/
   - Check "Add Python to PATH" during installation!

2. **Install Pillow**
   ```cmd
   python -m pip install pillow
   ```

3. **Run Terminal**
   ```cmd
   python bbs_terminal.py
   ```

## Linux Installation

### Automatic Installation (Recommended)

1. **Extract ZIP file**
   ```bash
   unzip pycgms-client-v1.0.zip
   cd pycgms-client
   ```

2. **Run Installer**
   ```bash
   chmod +x install-linux.sh
   ./install-linux.sh
   ```

3. **Follow prompts**
   - Installer checks Python/pip
   - Installs required packages
   - Installs Tkinter if needed
   - Creates application launcher
   - Launches PYCGMS

### Manual Installation

If automatic installer doesn't work:

1. **Install Python 3.8+** (usually pre-installed)
   ```bash
   # Ubuntu/Debian
   sudo apt install python3 python3-pip python3-tk

   # Fedora/RHEL
   sudo dnf install python3 python3-pip python3-tkinter

   # Arch
   sudo pacman -S python python-pip tk
   ```

2. **Install Pillow**
   ```bash
   pip3 install --user pillow
   ```

3. **Run Terminal**
   ```bash
   python3 bbs_terminal.py
   ```

## macOS Installation

macOS is similar to Linux:

1. **Install Homebrew** (if not already)
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

2. **Install Python**
   ```bash
   brew install python-tk@3.11
   ```

3. **Install Pillow**
   ```bash
   pip3 install pillow
   ```

4. **Run Terminal**
   ```bash
   python3 bbs_terminal.py
   ```

## Troubleshooting

### "Python not found"

**Windows:**
- Reinstall Python from python.org
- Check "Add Python to PATH"
- Restart Command Prompt

**Linux:**
- Install python3 package
- Use `python3` instead of `python`

### "pip not found"

**Windows:**
```cmd
python -m ensurepip
python -m pip install --upgrade pip
```

**Linux:**
```bash
sudo apt install python3-pip
```

### "Tkinter not found" (Linux)

```bash
# Ubuntu/Debian
sudo apt install python3-tk

# Fedora
sudo dnf install python3-tkinter

# Arch
sudo pacman -S tk
```

### "Pillow installation failed"

Try with --user flag:
```bash
pip install --user pillow
```

Or use system package manager:
```bash
# Ubuntu/Debian
sudo apt install python3-pil python3-pil.imagetk

# Fedora
sudo dnf install python3-pillow python3-pillow-tk
```

### Font not displaying correctly

1. Check font file exists:
   ```
   fonts/C64_Pro_Mono-STYLE.ttf
   ```

2. Terminal will use fallback font if missing
3. Manually install font to system (optional)

### Permission denied (Linux)

Make scripts executable:
```bash
chmod +x install-linux.sh
chmod +x run.sh
```

## Verifying Installation

After installation, verify everything works:

1. **Launch terminal**
   - Windows: Desktop shortcut or `python bbs_terminal.py`
   - Linux: Application menu or `./run.sh`

2. **Check window title**
   - Should say: "PYCGMS V1.0 by lA-sTYLe/Quantum (2026)"

3. **Test basic functions**
   - Press F7 → Protocol selection appears
   - Press F1 → File dialog appears
   - Type text → Shows in terminal

4. **Test connection** (optional)
   - Click "Add BBS"
   - Enter test BBS details
   - Try connecting

If all works → Installation successful! ✓

## Uninstallation

### Windows

1. Delete extracted folder
2. Delete desktop shortcut (if created)
3. Delete Start Menu entry:
   ```
   %APPDATA%\Microsoft\Windows\Start Menu\Programs\PYCGMS.lnk
   ```

### Linux

1. Delete extracted folder
2. Delete application entry:
   ```bash
   rm ~/.local/share/applications/pycgms.desktop
   rm ~/.local/bin/pycgms
   ```

3. Delete font (optional):
   ```bash
   rm ~/.local/share/fonts/C64_Pro_Mono-STYLE.ttf
   fc-cache -f
   ```

## Getting Help

If you still have problems:

1. **Check documentation**
   - CLIENT-README.md (complete manual)
   - CLIENT-FILELIST.txt (file descriptions)

2. **Check Python version**
   ```
   python --version
   ```
   Must be 3.8 or higher!

3. **Check package installation**
   ```
   python -m pip list | grep -i pillow
   ```
   Should show: `Pillow x.x.x`

4. **Run with debug output**
   ```
   python bbs_terminal.py
   ```
   Check error messages in console

5. **Contact**
   - BBS community forums
   - GitHub issues (if available)
   - Developer contact info

## Next Steps

After successful installation:

1. **Read the manual**
   - CLIENT-README.md has complete documentation
   - Keyboard shortcuts
   - Protocol guide
   - Usage examples

2. **Configure terminal**
   - Press Ctrl+, for Settings
   - Set download/upload folders
   - Choose preferred protocol (YMODEM recommended)

3. **Add BBSs**
   - Click "Add BBS" or Alt+N
   - Enter BBS details
   - Test connection

4. **Enjoy!**
   - Connect to classic C64 BBSs
   - Upload/download files
   - Experience PETSCII graphics

---

**Welcome to PYCGMS V1.0!**

The modern way to experience classic BBS culture.

by lA-sTYLe/Quantum (2026)
