@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

REM ----------------------------------------------------------------------
REM  VidJuice UniTube - one-click build (double-click to run)
REM  Produces dist\VidJuiceUniTube.exe (standalone, no Python needed on
REM  the target machine). Run from a NORMAL Windows terminal.
REM
REM  IMPORTANT: some sandboxed / CI terminals block `pip install` with a
REM  "bulk-install guard" (the command exits non-zero even with network).
REM  If step 2/3 below fails, do NOT keep retrying here - open a plain
REM  CMD / PowerShell on your own PC and run the manual command shown,
REM  then double-click this script again.
REM ----------------------------------------------------------------------

REM Always work from the folder that contains this .bat, so double-click
REM works no matter where the file is launched from.
cd /d "%~dp0"

echo ============================================================
echo   VidJuice UniTube - One-click Build
echo ============================================================
echo.

REM 0) Python available on PATH?
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found on PATH.
    echo         Install Python 3.10+ and tick "Add python.exe to PATH",
    echo         then run this script again.
    goto :done
)

REM 1) Ensure PyInstaller is installed
python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [INFO] PyInstaller not found - installing...
    python -m pip install -U pyinstaller
    if errorlevel 1 (
        echo.
        echo [ERROR] Could not install PyInstaller (pip exited with an error).
        echo   This is usually one of:
        echo     - no network / behind a corporate proxy
        echo     - this terminal blocks pip installs (sandboxed environment)
        echo.
        echo   Fix - open a normal CMD/PowerShell on your PC and run:
        echo       python -m pip install -U pyinstaller PySide6-Addons
        echo   then double-click this script again.
        goto :done
    )
)

REM 2) Ensure PySide6-Addons (provides QtWebEngine for the login browser)
python -c "import PySide6.QtWebEngineWidgets" >nul 2>nul
if errorlevel 1 (
    echo [INFO] PySide6-Addons (QtWebEngine) not found - installing...
    python -m pip install -U PySide6-Addons
    if errorlevel 1 (
        echo.
        echo [ERROR] Could not install PySide6-Addons (see note above).
        echo   On your own PC run: python -m pip install -U PySide6-Addons
        goto :done
    )
)

REM 3) Run the build (any extra args you pass to build.bat are forwarded)
echo.
echo [BUILD] Running build.py ...
echo.
python build.py %*

:done
echo.
echo ============================================================
echo   Done. Press any key to close this window.
echo ============================================================
pause >nul
