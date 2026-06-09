@echo off
REM ================================================================
REM NEXUS - Desktop-Verknuepfung anlegen
REM Einfach doppelklicken - fertig.
REM ================================================================
cd /d "%~dp0"

echo.
echo  Erstelle NEXUS-Verknuepfung auf dem Desktop ...
echo.

powershell -ExecutionPolicy Bypass -File "%~dp0create_shortcut.ps1"

if errorlevel 1 (
    echo.
    echo  [FEHLER] Verknuepfung konnte nicht erstellt werden.
    echo  Versuchen Sie, dieses Skript als Administrator auszufuehren:
    echo  Rechtsklick auf create_shortcut.bat -^> "Als Administrator ausfuehren"
    echo.
)

pause
