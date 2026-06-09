@echo off
REM ================================================================
REM NEXUS - Robuste Installation unter Windows
REM Mikrofon-Backend: sounddevice (funktioniert auch auf Python 3.14)
REM ================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ========================================
echo  NEXUS - Installation
echo ========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [FEHLER] Python ist nicht im PATH. Bitte Python 3.10+ installieren:
    echo          https://www.python.org/downloads/
    pause
    exit /b 1
)
python --version

if not exist "venv\Scripts\python.exe" (
    echo.
    echo [1/3] Erstelle virtuelle Umgebung ...
    python -m venv venv
    if errorlevel 1 (
        echo [FEHLER] venv konnte nicht erstellt werden.
        pause
        exit /b 1
    )
) else (
    echo [1/3] venv vorhanden.
)

set "PY=venv\Scripts\python.exe"
set "PIP=%PY% -m pip"

echo.
echo [2/3] Aktualisiere pip ...
%PIP% install --upgrade pip wheel setuptools

echo.
echo [3/3] Installiere Pakete einzeln ...
echo.

set "FAILED="

call :INSTALL requests
call :INSTALL "SpeechRecognition>=3.10.0"
call :INSTALL "sounddevice>=0.4.6"
call :INSTALL "numpy>=1.26.0"
call :INSTALL "pyttsx3>=2.90"
call :INSTALL "ddgs"
call :INSTALL "edge-tts>=6.1.10"

echo.
echo --- pip install pygame (optional - nur fuer Edge-TTS benoetigt) ---
%PIP% install "pygame>=2.5.2"
if errorlevel 1 (
    echo [INFO] pygame nicht installiert. Standard-TTS (pyttsx3) laeuft ohne pygame.
    echo        Fuer die Online-Stimme (Edge-TTS) spaeter nachholen: pip install pygame
) else (
    echo  OK pygame
)

echo.
echo --- pip install colorama (optional - fuer gruene Konsolenfarben) ---
%PIP% install "colorama>=0.4.6"
if errorlevel 1 (
    echo [INFO] colorama nicht installiert. Farben deaktiviert.
) else (
    echo  OK colorama
)

echo.
echo --- pip install playsound (optional - Fallback-Audioplayer fuer Edge-TTS) ---
%PIP% install "playsound==1.2.2"
if errorlevel 1 (
    echo [INFO] playsound nicht installiert. pygame wird als primaerer Player verwendet.
) else (
    echo  OK playsound
echo.
echo --- pip install faster-whisper (optional - bessere Spracherkennung, ~500 MB) ---
%PIP% install "faster-whisper>=1.0.0"
if errorlevel 1 (
    echo [INFO] faster-whisper nicht installiert. Standard-Erkennung (Google) wird verwendet.
    echo        Zum Nachinstallieren: pip install faster-whisper
) else (
    echo  OK faster-whisper
    echo [INFO] Beim ersten Start wird das Whisper-Modell heruntergeladen (~500 MB^).
    echo [INFO] Zum Aktivieren: in config.py STT_BACKEND = "whisper" setzen.
)
)

echo.
echo ========================================
echo  Verifikation der wichtigsten Module
echo ========================================
%PY% -c "import requests; print(' OK requests', requests.__version__)" 2>&1
%PY% -c "import speech_recognition as sr; print(' OK SpeechRecognition', sr.__version__)" 2>&1
%PY% -c "import sounddevice as sd; print(' OK sounddevice', sd.__version__)" 2>&1
%PY% -c "import numpy as np; print(' OK numpy', np.__version__)" 2>&1
%PY% -c "import pyttsx3; print(' OK pyttsx3')" 2>&1
%PY% -c "from ddgs import DDGS; print(' OK ddgs')" 2>&1 || %PY% -c "from duckduckgo_search import DDGS; print(' OK duckduckgo_search')" 2>&1
%PY% -c "import edge_tts; print(' OK edge-tts')" 2>&1
%PY% -c "import pygame; print(' OK pygame (optional)')" 2>&1 || echo  -- pygame nicht installiert (optional, nicht benoetigt)

echo.
echo ----------------------------------------
echo Verfuegbare Mikrofone:
%PY% -c "import sounddevice as sd; ds=sd.query_devices(); [print('  -', i, d['name']) for i,d in enumerate(ds) if d.get('max_input_channels',0)>0]" 2>&1

echo.
where ollama >nul 2>nul
if errorlevel 1 (
    echo [WARNUNG] Ollama ist nicht im PATH. Bitte installieren:
    echo           https://ollama.com/download
    echo Danach in einer neuen PowerShell:
    echo     ollama pull llama3
) else (
    echo Ollama gefunden. Verfuegbare Modelle:
    ollama list 2>&1
    echo.
    echo Falls llama3 fehlt:
    echo     ollama pull llama3
)

echo.
echo ========================================
if defined FAILED (
    echo  Installation fertig - aber fehlgeschlagen:!FAILED!
) else (
    echo  Installation erfolgreich.
)
echo  Start mit: start.bat        (Sprachmodus)
echo  oder mit:  start.bat text   (Textmodus, ohne Mikrofon)
echo ========================================
echo.
pause
endlocal
exit /b 0


:INSTALL
echo.
echo --- pip install %~1 ---
REM %1 behaelt die Anfuehrungszeichen -> CMD interpretiert >= NICHT als Redirect-Operator
%PIP% install %1
if errorlevel 1 (
    echo [WARN] %~1 konnte nicht installiert werden.
    set "FAILED=!FAILED! %~1"
)
goto :eof
