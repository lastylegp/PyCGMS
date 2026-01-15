@echo off
REM ====================================================================
REM PYCGMS V1.0 Terminal Client - Windows Installer
REM by lA-sTYLe/Quantum (2026)
REM ====================================================================

echo.
echo ====================================================================
echo PYCGMS V1.0 Terminal Client - Windows Installer
echo by lA-sTYLe/Quantum (2026)
echo ====================================================================
echo.

REM Check if Python is installed
echo [1/5] Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Python is not installed or not in PATH!
    echo.
    echo Please install Python 3.8 or higher from:
    echo https://www.python.org/downloads/
    echo.
    echo Make sure to check "Add Python to PATH" during installation!
    echo.
    pause
    exit /b 1
)

REM Get Python version
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo Found Python %PYVER%

REM Check Python version (needs 3.8+)
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do (
    set PYMAJOR=%%a
    set PYMINOR=%%b
)

if %PYMAJOR% LSS 3 (
    echo ERROR: Python 3.8+ required, found %PYVER%
    pause
    exit /b 1
)

if %PYMAJOR% EQU 3 (
    if %PYMINOR% LSS 8 (
        echo ERROR: Python 3.8+ required, found %PYVER%
        pause
        exit /b 1
    )
)

echo Python version OK!
echo.

REM Install pip packages
echo [2/5] Installing required Python packages...
echo Installing Pillow...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install pillow

if errorlevel 1 (
    echo.
    echo WARNING: Package installation failed!
    echo You may need to run this script as Administrator.
    echo.
    echo Try running: python -m pip install pillow
    echo.
    pause
    exit /b 1
)

echo Pillow installed successfully!
echo.

REM Check if font exists
echo [3/5] Checking font installation...
if exist "fonts\C64_Pro_Mono-STYLE.ttf" (
    echo Font found: fonts\C64_Pro_Mono-STYLE.ttf
) else (
    echo WARNING: Font not found!
    echo Please make sure fonts\C64_Pro_Mono-STYLE.ttf exists.
    echo The terminal will use system font as fallback.
)
echo.

REM Create desktop shortcut (optional)
echo [4/5] Creating desktop shortcut...
set SCRIPT_DIR=%~dp0
set SHORTCUT_PATH=%USERPROFILE%\Desktop\PYCGMS.lnk

REM Use PowerShell to create shortcut
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = 'python'; $s.Arguments = '\"%SCRIPT_DIR%bbs_terminal.py\"'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = 'PYCGMS V1.0 Terminal'; $s.Save()" >nul 2>&1

if exist "%SHORTCUT_PATH%" (
    echo Desktop shortcut created: PYCGMS.lnk
) else (
    echo Shortcut creation skipped (optional)
)
echo.

REM Create start menu entry (optional)
echo [5/5] Creating Start Menu entry...
set STARTMENU_PATH=%APPDATA%\Microsoft\Windows\Start Menu\Programs\PYCGMS.lnk

powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%STARTMENU_PATH%'); $s.TargetPath = 'python'; $s.Arguments = '\"%SCRIPT_DIR%bbs_terminal.py\"'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = 'PYCGMS V1.0 Terminal'; $s.Save()" >nul 2>&1

if exist "%STARTMENU_PATH%" (
    echo Start Menu entry created: PYCGMS
) else (
    echo Start Menu entry skipped (optional)
)
echo.

REM Test if terminal can be launched
echo ====================================================================
echo Installation Complete!
echo ====================================================================
echo.
echo PYCGMS V1.0 Terminal is ready to use!
echo.
echo You can start it by:
echo   1. Double-clicking the Desktop shortcut "PYCGMS"
echo   2. Running: python bbs_terminal.py
echo   3. From Start Menu: Search for "PYCGMS"
echo.
echo Press any key to launch PYCGMS now...
pause >nul

REM Launch terminal
echo.
echo Launching PYCGMS Terminal...
start python bbs_terminal.py

echo.
echo If the terminal doesn't start, try running manually:
echo python bbs_terminal.py
echo.
pause
