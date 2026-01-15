#!/bin/bash
####################################################################
# PYCGMS V1.0 Terminal Client - Linux Installer
# by lA-sTYLe/Quantum (2026)
####################################################################

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo ""
echo "===================================================================="
echo "PYCGMS V1.0 Terminal Client - Linux Installer"
echo "by lA-sTYLe/Quantum (2026)"
echo "===================================================================="
echo ""

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Function to check command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# [1/6] Check Python installation
echo -e "${BLUE}[1/6] Checking Python installation...${NC}"

if command_exists python3; then
    PYTHON_CMD="python3"
elif command_exists python; then
    PYTHON_CMD="python"
else
    echo -e "${RED}ERROR: Python is not installed!${NC}"
    echo ""
    echo "Please install Python 3.8 or higher:"
    echo ""
    echo "Ubuntu/Debian:"
    echo "  sudo apt update"
    echo "  sudo apt install python3 python3-pip python3-tk"
    echo ""
    echo "Fedora/RHEL:"
    echo "  sudo dnf install python3 python3-pip python3-tkinter"
    echo ""
    echo "Arch:"
    echo "  sudo pacman -S python python-pip tk"
    echo ""
    exit 1
fi

# Get Python version
PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}')
echo -e "${GREEN}Found Python $PYTHON_VERSION${NC}"

# Check version (needs 3.8+)
PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]); then
    echo -e "${RED}ERROR: Python 3.8+ required, found $PYTHON_VERSION${NC}"
    exit 1
fi

echo -e "${GREEN}Python version OK!${NC}"
echo ""

# [2/6] Check pip
echo -e "${BLUE}[2/6] Checking pip installation...${NC}"

if command_exists pip3; then
    PIP_CMD="pip3"
elif command_exists pip; then
    PIP_CMD="pip"
else
    echo -e "${YELLOW}WARNING: pip not found!${NC}"
    echo "Installing pip..."
    
    if command_exists apt-get; then
        sudo apt-get update
        sudo apt-get install -y python3-pip
    elif command_exists dnf; then
        sudo dnf install -y python3-pip
    elif command_exists pacman; then
        sudo pacman -S --noconfirm python-pip
    else
        echo -e "${RED}ERROR: Cannot install pip automatically${NC}"
        echo "Please install pip manually and run this script again"
        exit 1
    fi
    
    PIP_CMD="pip3"
fi

echo -e "${GREEN}pip found!${NC}"
echo ""

# [3/6] Install Python packages
echo -e "${BLUE}[3/6] Installing required Python packages...${NC}"
echo "Installing Pillow..."

$PIP_CMD install --user pillow

if [ $? -ne 0 ]; then
    echo -e "${YELLOW}WARNING: User install failed, trying with sudo...${NC}"
    sudo $PIP_CMD install pillow
    
    if [ $? -ne 0 ]; then
        echo -e "${RED}ERROR: Package installation failed!${NC}"
        echo ""
        echo "Try manually:"
        echo "  $PIP_CMD install --user pillow"
        echo ""
        exit 1
    fi
fi

echo -e "${GREEN}Pillow installed successfully!${NC}"
echo ""

# [4/6] Check Tkinter
echo -e "${BLUE}[4/6] Checking Tkinter installation...${NC}"

$PYTHON_CMD -c "import tkinter" 2>/dev/null

if [ $? -ne 0 ]; then
    echo -e "${YELLOW}WARNING: Tkinter not found!${NC}"
    echo "Tkinter is required for the GUI."
    echo ""
    echo "Installing Tkinter..."
    
    if command_exists apt-get; then
        sudo apt-get install -y python3-tk
    elif command_exists dnf; then
        sudo dnf install -y python3-tkinter
    elif command_exists pacman; then
        sudo pacman -S --noconfirm tk
    else
        echo -e "${RED}ERROR: Cannot install Tkinter automatically${NC}"
        echo "Please install python3-tk package manually"
        exit 1
    fi
    
    # Check again
    $PYTHON_CMD -c "import tkinter" 2>/dev/null
    if [ $? -ne 0 ]; then
        echo -e "${RED}ERROR: Tkinter installation failed!${NC}"
        exit 1
    fi
fi

echo -e "${GREEN}Tkinter OK!${NC}"
echo ""

# [5/6] Check font
echo -e "${BLUE}[5/6] Checking font installation...${NC}"

if [ -f "fonts/C64_Pro_Mono-STYLE.ttf" ]; then
    echo -e "${GREEN}Font found: fonts/C64_Pro_Mono-STYLE.ttf${NC}"
    
    # Install font to user directory (optional)
    FONT_DIR="$HOME/.local/share/fonts"
    mkdir -p "$FONT_DIR"
    cp -f "fonts/C64_Pro_Mono-STYLE.ttf" "$FONT_DIR/"
    
    # Update font cache
    if command_exists fc-cache; then
        fc-cache -f "$FONT_DIR" 2>/dev/null
        echo -e "${GREEN}Font installed to $FONT_DIR${NC}"
    fi
else
    echo -e "${YELLOW}WARNING: Font not found!${NC}"
    echo "Please make sure fonts/C64_Pro_Mono-STYLE.ttf exists."
    echo "The terminal will use system font as fallback."
fi
echo ""

# [6/6] Create launcher
echo -e "${BLUE}[6/6] Creating application launcher...${NC}"

# Create desktop entry
DESKTOP_FILE="$HOME/.local/share/applications/pycgms.desktop"
mkdir -p "$HOME/.local/share/applications"

cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=PYCGMS Terminal
Comment=PETSCII BBS Terminal Client
Exec=$PYTHON_CMD "$SCRIPT_DIR/bbs_terminal.py"
Path=$SCRIPT_DIR
Icon=utilities-terminal
Terminal=false
Categories=Network;
Keywords=BBS;Terminal;PETSCII;C64;
EOF

if [ -f "$DESKTOP_FILE" ]; then
    chmod +x "$DESKTOP_FILE"
    echo -e "${GREEN}Desktop entry created: pycgms.desktop${NC}"
    echo "You can find PYCGMS in your application menu"
else
    echo -e "${YELLOW}Desktop entry creation skipped${NC}"
fi

# Create command-line launcher script
LAUNCHER_SCRIPT="$HOME/.local/bin/pycgms"
mkdir -p "$HOME/.local/bin"

cat > "$LAUNCHER_SCRIPT" << EOF
#!/bin/bash
cd "$SCRIPT_DIR"
exec $PYTHON_CMD "$SCRIPT_DIR/bbs_terminal.py" "\$@"
EOF

chmod +x "$LAUNCHER_SCRIPT"

if [ -f "$LAUNCHER_SCRIPT" ]; then
    echo -e "${GREEN}Command-line launcher created: $HOME/.local/bin/pycgms${NC}"
    
    # Check if ~/.local/bin is in PATH
    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        echo -e "${YELLOW}NOTE: $HOME/.local/bin is not in your PATH${NC}"
        echo "Add this to your ~/.bashrc or ~/.zshrc:"
        echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
    else
        echo "You can run 'pycgms' from anywhere now!"
    fi
fi

echo ""

# Create run script in current directory
cat > "$SCRIPT_DIR/run.sh" << 'EOF'
#!/bin/bash
cd "$(dirname "$0")"
python3 bbs_terminal.py
EOF

chmod +x "$SCRIPT_DIR/run.sh"
echo -e "${GREEN}Local launcher created: ./run.sh${NC}"
echo ""

# Installation complete
echo "===================================================================="
echo -e "${GREEN}Installation Complete!${NC}"
echo "===================================================================="
echo ""
echo "PYCGMS V1.0 Terminal is ready to use!"
echo ""
echo "You can start it by:"
echo "  1. From application menu: Search for 'PYCGMS'"
echo "  2. Command line: pycgms (if ~/.local/bin in PATH)"
echo "  3. This directory: ./run.sh"
echo "  4. Direct: $PYTHON_CMD bbs_terminal.py"
echo ""
read -p "Press ENTER to launch PYCGMS now, or Ctrl+C to exit... "

# Launch terminal
echo ""
echo "Launching PYCGMS Terminal..."
$PYTHON_CMD bbs_terminal.py &

echo ""
echo "PYCGMS launched in background!"
echo ""
echo "If the terminal doesn't start, check dependencies:"
echo "  $PYTHON_CMD bbs_terminal.py"
echo ""
