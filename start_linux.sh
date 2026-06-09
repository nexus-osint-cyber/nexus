#!/usr/bin/env bash
# ================================================================
# NEXUS - Linux Start
#   ./start_linux.sh             -> Sprachmodus
#   ./start_linux.sh text        -> Textmodus (ohne Mikrofon)
#   ./start_linux.sh nopre       -> ohne Ollama-Vorabpruefung
#   ./start_linux.sh textnopre   -> Textmodus + kein Preflight
#   ./start_linux.sh nowake      -> Wakeword deaktivieren
#   ./start_linux.sh nohybrid    -> nur Spracheingabe
# ================================================================
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f "venv/bin/python" ]; then
    echo "[FEHLER] Keine venv gefunden. Bitte erst ./install_linux.sh ausfuehren."
    exit 1
fi

# Ollama starten falls noetig
if command -v ollama &>/dev/null; then
    if ! curl -s --max-time 2 http://localhost:11434/api/tags &>/dev/null; then
        echo "Starte Ollama im Hintergrund ..."
        ollama serve &>/dev/null &
        sleep 3
    fi
else
    echo "[WARNUNG] Ollama nicht gefunden. Bitte installieren: https://ollama.com/download"
fi

# Modus auswerten
MODE=""
case "${1:-}" in
    text)       MODE="--text" ;;
    nopre)      MODE="--no-preflight" ;;
    textnopre)  MODE="--text --no-preflight" ;;
    nowake)     MODE="--no-wake" ;;
    wake)       MODE="--wake" ;;
    nohybrid)   MODE="--no-hybrid" ;;
esac

echo ""
echo "========================================"
echo "  Starte NEXUS $MODE"
echo "========================================"
echo ""

venv/bin/python main.py $MODE
RC=$?

echo ""
echo "========================================"
echo "  NEXUS beendet (Exit-Code $RC)"
echo "========================================"
