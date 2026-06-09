#!/usr/bin/env bash
# ================================================================
# NEXUS - Linux-Installer
# Einfach auf dem Linux-Rechner ausfuehren:
#   chmod +x install_linux.sh && ./install_linux.sh
# ================================================================
set -euo pipefail
cd "$(dirname "$0")"

GREEN='\033[92m'; YELLOW='\033[93m'; RED='\033[91m'; RESET='\033[0m'
ok()   { echo -e "${GREEN}[OK]${RESET} $*"; }
warn() { echo -e "${YELLOW}[WARN]${RESET} $*"; }
err()  { echo -e "${RED}[FEHLER]${RESET} $*"; }
info() { echo -e "      $*"; }

echo ""
echo "========================================"
echo "  NEXUS - Linux Installation"
echo "========================================"
echo ""

# ---- 1) Python 3.10+ pruefen / installieren ----
echo "[1/6] Python pruefen ..."
PYTHON=""
for py in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$py" &>/dev/null; then
        VER=$("$py" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        MAJOR=${VER%%.*}; MINOR=${VER##*.}
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 10 ]; then
            PYTHON="$py"
            ok "Python $VER gefunden ($py)"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    warn "Python 3.10+ nicht gefunden. Versuche Installation via apt ..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get update -qq
        sudo apt-get install -y python3 python3-pip python3-venv python3-dev
        PYTHON=python3
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y python3 python3-pip python3-venv
        PYTHON=python3
    elif command -v pacman &>/dev/null; then
        sudo pacman -Sy --noconfirm python python-pip
        PYTHON=python3
    else
        err "Konnte Python nicht installieren. Bitte manuell installieren: https://python.org"
        exit 1
    fi
fi

# ---- 2) System-Abhaengigkeiten ----
echo ""
echo "[2/6] System-Pakete installieren ..."

if command -v apt-get &>/dev/null; then
    echo "      (Debian/Ubuntu erkannt - apt)"
    sudo apt-get update -qq
    sudo apt-get install -y \
        portaudio19-dev \
        python3-dev \
        espeak \
        espeak-data \
        libespeak-dev \
        mpg123 \
        ffmpeg \
        curl \
        build-essential \
        2>/dev/null || warn "Einige Pakete konnten nicht installiert werden (nicht kritisch)"
    ok "System-Pakete installiert"

elif command -v dnf &>/dev/null; then
    echo "      (Fedora/RHEL erkannt - dnf)"
    sudo dnf install -y \
        portaudio-devel \
        python3-devel \
        espeak \
        mpg123 \
        ffmpeg \
        curl \
        gcc \
        2>/dev/null || warn "Einige Pakete nicht verfuegbar"
    ok "System-Pakete installiert"

elif command -v pacman &>/dev/null; then
    echo "      (Arch Linux erkannt - pacman)"
    sudo pacman -Sy --noconfirm \
        portaudio \
        python \
        espeak-ng \
        mpg123 \
        ffmpeg \
        curl \
        2>/dev/null || warn "Einige Pakete nicht verfuegbar"
    ok "System-Pakete installiert"

else
    warn "Unbekannte Distribution. System-Pakete muessen manuell installiert werden:"
    info "  - portaudio / portaudio19-dev  (fuer Mikrofon)"
    info "  - espeak                        (fuer Sprachausgabe)"
    info "  - mpg123 oder ffmpeg            (fuer Audio-Wiedergabe)"
fi

# ---- 3) Virtuelle Umgebung ----
echo ""
echo "[3/6] Virtuelle Python-Umgebung ..."
if [ ! -f "venv/bin/python" ]; then
    "$PYTHON" -m venv venv
    ok "venv erstellt"
else
    ok "venv vorhanden"
fi

PY="venv/bin/python"
PIP="$PY -m pip"

$PIP install --upgrade pip wheel setuptools -q
ok "pip aktualisiert"

# ---- 4) Python-Pakete ----
echo ""
echo "[4/6] Python-Pakete installieren ..."

install_pkg() {
    local pkg="$1"
    local label="${2:-$1}"
    echo "      pip install $label ..."
    if $PIP install "$pkg" -q 2>/dev/null; then
        ok "$label"
    else
        warn "$label konnte nicht installiert werden"
    fi
}

install_pkg "requests"                    "requests"
install_pkg "SpeechRecognition>=3.10.0"  "SpeechRecognition"
install_pkg "sounddevice>=0.4.6"         "sounddevice"
install_pkg "numpy>=1.26.0"              "numpy"
install_pkg "pyttsx3>=2.90"             "pyttsx3"
install_pkg "ddgs"                        "ddgs"
install_pkg "edge-tts>=6.1.10"          "edge-tts"
install_pkg "colorama>=0.4.6"           "colorama"

# Optionale Pakete
echo ""
echo "      Optionale Pakete ..."
$PIP install "pygame>=2.5.2" -q 2>/dev/null && ok "pygame (Audio)" || info "pygame nicht verfuegbar (mpg123/ffplay wird verwendet)"
$PIP install "faster-whisper>=1.0.0" -q 2>/dev/null && \
    ok "faster-whisper (bessere Spracherkennung - beim ersten Start ~500 MB Download)" || \
    info "faster-whisper nicht installiert (Google-STT wird verwendet)"

# ---- 5) Ollama ----
echo ""
echo "[5/6] Ollama pruefen/installieren ..."
if command -v ollama &>/dev/null; then
    ok "Ollama bereits installiert"
    ollama list 2>/dev/null || true
else
    echo "      Ollama wird installiert (curl) ..."
    if command -v curl &>/dev/null; then
        curl -fsSL https://ollama.com/install.sh | sh
        ok "Ollama installiert"
    else
        warn "curl nicht gefunden. Ollama manuell installieren: https://ollama.com/download"
    fi
fi

# Modell pruefen/ziehen
echo ""
MODEL=$(python3 -c "import sys; sys.path.insert(0,''); import config; print(config.OLLAMA_MODEL)" 2>/dev/null || echo "llama3")
echo "      Pruefe Modell '$MODEL' ..."
if command -v ollama &>/dev/null; then
    # Ollama starten falls noetig
    if ! curl -s http://localhost:11434/api/tags &>/dev/null; then
        echo "      Starte Ollama ..."
        ollama serve &>/dev/null &
        sleep 4
    fi
    if ollama list 2>/dev/null | grep -q "$MODEL"; then
        ok "Modell '$MODEL' vorhanden"
    else
        echo "      Lade Modell '$MODEL' herunter (kann einige Minuten dauern) ..."
        ollama pull "$MODEL" && ok "Modell '$MODEL' bereit" || warn "Modell-Download fehlgeschlagen. Manuell: ollama pull $MODEL"
    fi
fi

# ---- 6) Verifikation ----
echo ""
echo "[6/6] Verifikation ..."
$PY -c "import requests; print('  OK requests', requests.__version__)" 2>/dev/null || warn "requests fehlt"
$PY -c "import speech_recognition as sr; print('  OK SpeechRecognition', sr.__version__)" 2>/dev/null || warn "SpeechRecognition fehlt"
$PY -c "import sounddevice as sd; print('  OK sounddevice', sd.__version__)" 2>/dev/null || warn "sounddevice fehlt"
$PY -c "import numpy as np; print('  OK numpy', np.__version__)" 2>/dev/null || warn "numpy fehlt"
$PY -c "import pyttsx3; print('  OK pyttsx3')" 2>/dev/null || warn "pyttsx3 fehlt"
$PY -c "import edge_tts; print('  OK edge-tts')" 2>/dev/null || warn "edge-tts fehlt"

echo ""
echo "  Verfuegbare Mikrofone:"
$PY -c "
import sounddevice as sd
devices = sd.query_devices()
mics = [(i,d) for i,d in enumerate(devices) if d.get('max_input_channels',0)>0]
if mics:
    for i,d in mics: print(f'    [{i}] {d[\"name\"]}')
else:
    print('    (keine Mikrofone gefunden)')
" 2>/dev/null || true

echo ""
echo "========================================"
ok "Installation abgeschlossen!"
echo ""
echo "  Starten mit:  ./start_linux.sh"
echo "  Textmodus:    ./start_linux.sh text"
echo "========================================"
echo ""
