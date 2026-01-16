#!/bin/bash
####################################################################
# PYCGMS V1.0 Terminal Client by lA-sTYLe/Quantum (2026)
# macOS Installer by The Codeblasters (2026)
####################################################################

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo ""
echo "===================================================================="
echo "PYCGMS V1.0 Terminal Client by lA-sTYLe/Quantum (2026)"
echo "macOS Installer by The Codeblasters (2026)"
echo "===================================================================="
echo ""

# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# -------------------------------------------------------------------
# [1/6] Check Python
# -------------------------------------------------------------------
echo -e "${BLUE}[1/6] Checking Python installation...${NC}"

if command_exists python3; then
    PYTHON_CMD="python3"
else
    echo -e "${RED}ERROR: Python 3 not found${NC}"
    echo ""
    echo "Install Python 3 using one of the following methods:"
    echo ""
    echo "Official installer:"
    echo "  https://www.python.org/downloads/macos/"
    echo ""
    echo "Or Homebrew:"
    echo "  brew install python"
    echo ""
    exit 1
fi

PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}')
echo -e "${GREEN}Found Python $PYTHON_VERSION${NC}"

PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]; }; then
    echo -e "${RED}ERROR: Python 3.8+ required${NC}"
    exit 1
fi

echo -e "${GREEN}Python version OK${NC}"
echo ""

# -------------------------------------------------------------------
# [2/6] Check pip
# -------------------------------------------------------------------
echo -e "${BLUE}[2/6] Checking pip...${NC}"

if command_exists pip3; then
    PIP_CMD="pip3"
else
    echo -e "${YELLOW}pip not found, attempting bootstrap...${NC}"
    $PYTHON_CMD -m ensurepip --user
    PIP_CMD="pip3"
fi

echo -e "${GREEN}pip available${NC}"
echo ""

# -------------------------------------------------------------------
# [3/6] Install Python packages
# -------------------------------------------------------------------
echo -e "${BLUE}[3/6] Installing Python dependencies...${NC}"

$PIP_CMD install --user pillow

if [ $? -ne 0 ]; then
    echo -e "${RED}ERROR: Failed to install Pillow${NC}"
    echo "Try manually:"
    echo "  $PIP_CMD install --user pillow"
    exit 1
fi

echo -e "${GREEN}Pillow installed${NC}"
echo ""

# -------------------------------------------------------------------
# [4/6] Check Tkinter
# -------------------------------------------------------------------
echo -e "${BLUE}[4/6] Checking Tkinter...${NC}"

$PYTHON_CMD - <<EOF
import tkinter
EOF

if [ $? -ne 0 ]; then
    echo -e "${RED}ERROR: Tkinter not available${NC}"
    echo ""
    echo "Install a Python version that includes Tk support:"
    echo "  https://www.python.org/downloads/macos/"
    exit 1
fi

echo -e "${GREEN}Tkinter OK${NC}"
echo ""

# -------------------------------------------------------------------
# [5/6] Install Font
# -------------------------------------------------------------------
echo -e "${BLUE}[5/6] Installing font...${NC}"

FONT_SRC="fonts/C64_Pro_Mono-STYLE.ttf"
FONT_DIR="$HOME/Library/Fonts"

if [ -f "$FONT_SRC" ]; then
    cp -f "$FONT_SRC" "$FONT_DIR/"
    echo -e "${GREEN}Font installed to $FONT_DIR${NC}"
else
    echo -e "${YELLOW}Font not found, using system fallback${NC}"
fi

echo ""

# -------------------------------------------------------------------
# [6/6] Create launchers
# -------------------------------------------------------------------
echo -e "${BLUE}[6/6] Creating launchers...${NC}"

# CLI launcher
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"

cat > "$BIN_DIR/pycgms" << EOF
#!/bin/bash
cd "$SCRIPT_DIR"
exec $PYTHON_CMD "$SCRIPT_DIR/bbs_terminal.py" "\$@"
EOF

chmod +x "$BIN_DIR/pycgms"
echo -e "${GREEN}CLI launcher created: pycgms${NC}"

# PATH notice
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo -e "${YELLOW}NOTE: $BIN_DIR not in PATH${NC}"
    echo "Add this to ~/.zshrc:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# Local run script
cat > "$SCRIPT_DIR/run.sh" << 'EOF'
#!/bin/bash
cd "$(dirname "$0")"
python3 bbs_terminal.py
EOF

chmod +x "$SCRIPT_DIR/run.sh"
echo -e "${GREEN}Local launcher created: ./run.sh${NC}"

echo ""
echo "===================================================================="
echo -e "${GREEN}Installation Complete!${NC}"
echo "===================================================================="
echo ""
echo "You can start PYCGMS using:"
echo "  1. Command line: pycgms"
echo "  2. Local script: ./run.sh"
echo "  3. Direct: python3 bbs_terminal.py"
echo ""

read -p "Press ENTER to launch PYCGMS now, or Ctrl+C to exit... "

echo "Launching PYCGMS..."
$PYTHON_CMD bbs_terminal.py &
