@echo off
REM ================================================================
REM NEXUS - Diagnose
REM Sammelt Infos und laesst das Fenster offen.
REM ================================================================
setlocal
cd /d "%~dp0"

echo ========================================
echo  NEXUS - DIAGNOSE
echo ========================================
echo.

echo [1] Aktueller Ordner:
cd
echo.

echo [2] Vorhandene Dateien:
dir /b
echo.

echo [3] Python Version:
where python
python --version 2>&1
echo.

echo [4] venv vorhanden?
if exist "venv\Scripts\python.exe" (
    echo    JA
    "venv\Scripts\python.exe" --version
) else (
    echo    NEIN - bitte install.bat zuerst ausfuehren.
)
echo.

echo [5] Installierte Pakete in venv:
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" -m pip list 2>&1 | findstr /I "requests SpeechRecognition sounddevice numpy pyaudio pyttsx3 edge-tts ddgs duckduckgo pygame"
)
echo.

echo [6] Ollama im PATH?
where ollama
echo.

echo [7] Ollama erreichbar?
curl -s -o nul -w "HTTP %%{http_code}" http://localhost:11434/api/tags
echo.
echo.

echo [8] Installierte Ollama-Modelle:
ollama list 2>&1
echo.

echo [9] Mikrofontest (sounddevice):
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" -u -c "import sounddevice as sd; ds=sd.query_devices(); mics=[d for d in ds if d.get('max_input_channels',0)>0]; print('Mikrofone gefunden:', len(mics)); [print(' ', i, d['name']) for i,d in enumerate(ds) if d.get('max_input_channels',0)>0]" 2>&1
)
echo.

echo [10] pyttsx3-Test (Subprocess-Modus):
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" -u -c "import subprocess,sys; r=subprocess.run([sys.executable,'-c','import pyttsx3; e=pyttsx3.init(); print(\"pyttsx3 OK, Stimmen:\", len(e.getProperty(\"voices\"))); e.stop()'],capture_output=True,text=True,timeout=15); print(r.stdout.strip() or r.stderr.strip() or 'Kein Output')" 2>&1
)
echo.

echo [11] Trockenstart von main.py (Textmodus, ohne Preflight):
if exist "venv\Scripts\python.exe" (
    echo exit | "venv\Scripts\python.exe" -u main.py --no-preflight --text 2>&1
)
echo.

echo ========================================
echo  Diagnose abgeschlossen.
echo  Bitte schicken Sie diese Ausgabe.
echo ========================================
pause
endlocal
