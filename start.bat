@echo off
REM ================================================================
REM NEXUS - Start
REM   ohne Argument        -> Textmodus + Stimme + v=Sprache (EMPFOHLEN)
REM   start.bat voice      -> Reiner Sprachmodus (nur Mikrofon)
REM   start.bat nopre      -> ohne Ollama-Vorabpruefung
REM   start.bat textnopre  -> Textmodus + ohne Preflight
REM   start.bat nowake     -> Wakeword deaktivieren (nur Sprachmodus)
REM   start.bat wake       -> Wakeword erzwingen (nur Sprachmodus)
REM ================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [FEHLER] Keine venv gefunden. Bitte erst install.bat ausfuehren.
    echo.
    pause
    exit /b 1
)

REM --- Ollama-Server bei Bedarf starten ---
where ollama >nul 2>nul
if not errorlevel 1 (
    tasklist /FI "IMAGENAME eq ollama.exe" 2>nul | find /I "ollama.exe" >nul
    if errorlevel 1 (
        echo Starte Ollama im Hintergrund ...
        start "" /B ollama serve
        timeout /t 3 >nul
    )
) else (
    echo [WARNUNG] Ollama nicht im PATH. Bitte zuerst installieren:
    echo           https://ollama.com/download
    echo.
)

REM Standard: Textmodus (zuverlaessig, v+Enter fuer Sprache)
REM Netzwerk-Zugriff: automatisch Tailscale-IP erkennen
REM (bindet NUR auf Tailscale-Interface, nicht auf offenem 0.0.0.0)
for /f "tokens=*" %%i in ('venv\Scripts\python.exe nexus_tailscale_ip.py') do set "NEXUS_HOST=%%i"
if "%NEXUS_HOST%"=="localhost" (
    echo [NEXUS] Tailscale nicht aktiv - Server nur lokal erreichbar.
) else (
    echo [NEXUS] Tailscale erkannt - Server bindet auf %NEXUS_HOST%
    echo [NEXUS] Handy-URL: http://%NEXUS_HOST%:11430/livemap
)

set "MODE=--text"
if /I "%~1"=="voice"     set "MODE="
if /I "%~1"=="nopre"     set "MODE=--text --no-preflight"
if /I "%~1"=="textnopre" set "MODE=--text --no-preflight"
if /I "%~1"=="nowake"    set "MODE=--no-wake"
if /I "%~1"=="wake"      set "MODE=--wake"

echo.
echo ========================================
echo  Starte NEXUS %MODE%
echo ========================================
echo.

call "venv\Scripts\python.exe" main.py %MODE%
set "RC=%ERRORLEVEL%"

echo.
echo ========================================
echo  NEXUS hat sich beendet (Exit-Code %RC%).
echo ========================================
echo.
pause
endlocal
