"""
NEXUS - Konfigurationsvorlage
===============================
ANLEITUNG:
1. Diese Datei kopieren: config.example.py -> config.py
2. In config.py die gewuenschten Werte anpassen
3. config.py NIEMALS auf GitHub hochladen (.gitignore ist bereits korrekt gesetzt)

Alle Einstellungen sind OPTIONAL - NEXUS laeuft auch ohne API-Keys (reduzierte Funktionen).
"""

# ====================================================
# PFLICHTFELD - NEXUS Persoenlichkeit
# ====================================================
SYSTEM_PROMPT = """Du bist NEXUS - ein praeziser KI-Assistent mit der Denkweise
eines Generalstabsoffiziers. Antworte immer auf Deutsch, direkt und faktenbasiert."""

BEGRUESSUNG = "NEXUS aktiv. Womit kann ich helfen?"
EXIT_WORDS  = ["tschuess", "beenden", "exit", "quit", "auf wiedersehen"]

# ====================================================
# OLLAMA - Lokale KI (kostenlos, laeuft auf deinem PC)
# ====================================================
# Installation: https://ollama.com -> dann: ollama pull llama3
OLLAMA_HOST       = "http://localhost:11434"
OLLAMA_MODEL      = "llama3"     # Alternativen: llama3.1, qwen2.5:7b, mistral
OLLAMA_TIMEOUT    = 120
OLLAMA_KEEP_ALIVE = "10m"        # Wie lange Modell im RAM bleibt ("10m", "1h", "-1" = dauerhaft)

OLLAMA_OPTIONS = {
    "temperature":    0.65,
    "top_p":          0.9,
    "num_ctx":        8192,
    "repeat_penalty": 1.1,
}

# ====================================================
# STIMME & SPRACHEINGABE (Voice / STT)
# ====================================================
# TTS-Engine: "edge-tts" (besser, braucht Internet) oder "pyttsx3" (offline)
TTS_ENGINE     = "edge-tts"
EDGE_TTS_VOICE  = "de-DE-KillianNeural"   # Stimme (de-DE-KillianNeural / de-DE-AmalaNeural)
EDGE_TTS_RATE   = "+0%"                   # Geschwindigkeit: "+20%" schneller, "-10%" langsamer
EDGE_TTS_VOLUME = "+0%"

# STT-Backend: "google" (Standard), "whisper" (offline, besser), "vosk" (offline, schnell)
STT_BACKEND           = "google"
STT_LANGUAGE          = "de-DE"
STT_ENERGY_THRESHOLD  = 400    # Mikrofon-Empfindlichkeit (300-600 typisch)
STT_PAUSE_THRESHOLD   = 0.8    # Sekunden Pause bis Aufnahme endet
STT_TIMEOUT           = 8.0    # Max. Wartezeit auf Sprache (Sekunden)
STT_PHRASE_TIME_LIMIT = 15.0   # Max. Aufnahmedauer (Sekunden)

# Whisper-Modell (nur wenn STT_BACKEND = "whisper")
# Modelle: tiny, base, small, medium, large (groesser = besser, langsamer)
WHISPER_MODEL = "base"

# Vosk-Modell (nur wenn STT_BACKEND = "vosk" oder als Offline-Fallback)
# Modell herunterladen: https://alphacephei.com/vosk/models -> vosk-model-small-de-0.15
VOSK_MODEL_PATH = ""   # z.B. "C:/nexus/vosk-model-small-de-0.15"

# ====================================================
# WAKE-WORD & HYBRID-EINGABE
# ====================================================
USE_WAKEWORD     = False         # True = NEXUS hoert passiv auf das Aktivierungswort
WAKE_WORD        = "nexus"       # Aktivierungswort (Kleinschreibung)
WAKEWORD         = "nexus"       # Alias (identisch mit WAKE_WORD)
WAKEWORD_ALIASES = ["hey nexus", "nex"]   # Weitere Aktivierungswoerter
WAKEWORD_ACK     = "Ja?"         # NEXUS-Antwort nach Wake-Word-Erkennung
WAKEWORD_LOG     = False         # Wake-Word-Erkennungen protokollieren
USE_HYBRID_INPUT = True          # True = Tastatur-Fallback wenn Mikrofon nicht verfuegbar
VERBOSE          = False         # Ausfuehrliche Debug-Ausgaben

# ====================================================
# SUCHE
# ====================================================
SEARCH_MAX_RESULTS   = 8
SEARCH_REGION        = "de-de"
SEARCH_TRIGGER_WORDS = ["suche", "such", "finde", "recherche", "google", "web"]

# ====================================================
# KOSTENLOSE KEYS (empfohlen - sofort verfuegbar)
# ====================================================

# NASA FIRMS - Braende & Hitzepunkte (sofort kostenlos)
# Registrierung: https://firms.modaps.eosdis.nasa.gov/api/map_key/
FIRMS_MAP_KEY = ""   # z.B. "abc123def456"

# AISStream.io - Echtzeit-Schiffspositionen (kostenlos)
# Registrierung: https://aisstream.io/account
AISSTREAM_KEY = ""   # z.B. "88bf75d0b152..."

# ====================================================
# ACLED - Konfliktereignisse mit GPS (Research-Zugang)
# ====================================================
# Registrierung: https://acleddata.com/register/
# NEXUS holt OAuth-Token automatisch - nur Email + Passwort eintragen:
ACLED_EMAIL    = ""   # z.B. "deine@email.com"
ACLED_PASSWORD = ""   # dein ACLED-Login-Passwort

# ====================================================
# DEEPL - Uebersetzung (500.000 Zeichen/Monat kostenlos)
# ====================================================
# Registrierung: https://www.deepl.com/pro-api
DEEPL_API_KEY = ""   # z.B. "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:fx"

# ====================================================
# N2YO - Satellit-Ueberflug-Timer (kostenlos)
# ====================================================
# Registrierung: https://www.n2yo.com/api/
N2YO_API_KEY = ""   # z.B. "XXXXXX-XXXXXX-XXXXXX-XXXXXX"

# ====================================================
# MARINETRAFFIC - Schiffsdaten (optional, kostenpflichtig)
# ====================================================
MARINETRAFFIC_KEY = ""
VESSELFINDER_KEY  = ""

# ====================================================
# EMAIL-ALERTS - SMTP Konfiguration (optional)
# ====================================================
# Gmail: 16-stelliges App-Passwort unter myaccount.google.com/apppasswords
SMTP_HOST     = "smtp.gmail.com"
SMTP_PORT     = 587
SMTP_USER     = ""   # deine@gmail.com
SMTP_PASSWORD = ""   # 16-stelliges App-Passwort
SMTP_FROM     = ""   # leer = SMTP_USER
SMTP_TO       = ""   # Empfaenger, mehrere: "a@b.com,c@d.com"

# ====================================================
# DISCORD / TELEGRAM WEBHOOKS (optional)
# ====================================================
DISCORD_WEBHOOK_URL = ""   # https://discord.com/api/webhooks/...
TELEGRAM_BOT_TOKEN  = ""
TELEGRAM_CHAT_ID    = ""

# ====================================================
# WATCHLIST - Regionen die staendig beobachtet werden
# ====================================================
WATCHLIST_REGIONS = [
    "Ukraine",
    "Naher Osten",
    "Taiwan-Strasse",
]
WATCHLIST_INTERVAL_MIN = 30    # Pruefintervall in Minuten
WATCHLIST_ALERT_SCORE  = 65    # Eskalations-Score ab dem ein Alert ausgeloest wird

# ====================================================
# PINNED REGION - Standard-Lagebild beim Start
# ====================================================
PINNED_REGION          = "Ukraine"
AUTO_LAGE_ON_START     = True
REPORT_REFRESH_MINUTES = 15

# ====================================================
# NEXUS LLM-PROVIDER (NEU: Claude API Support)
# ====================================================
# LLM_PROVIDER = "auto"   -> Claude wenn Key gesetzt, sonst Ollama
# LLM_PROVIDER = "claude" -> Immer Claude (braucht CLAUDE_API_KEY)
# LLM_PROVIDER = "ollama" -> Immer lokales Modell
LLM_PROVIDER = "auto"

# Anthropic Claude API-Key (optional, stark empfohlen fuer bessere Analyse)
# Kostenlos registrieren: https://console.anthropic.com
# claude-haiku-4-5-20251001 = guenstig + schnell | claude-sonnet-4-6 = staerker
CLAUDE_API_KEY    = ""   # "sk-ant-api03-..."
CLAUDE_MODEL      = "claude-haiku-4-5-20251001"
CLAUDE_MAX_TOKENS = 1500
