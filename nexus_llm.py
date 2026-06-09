"""
NEXUS – LLM-Bridge (Ebene 4 / Modul 4.1)
=========================================
Verbindet Ollama mit der NEXUS-Eskalations-Pipeline.

Unterschied zu nexus_brain.py:
  nexus_brain.py  = konversationeller Chat-Assistent (Gesprächshistorie)
  nexus_llm.py    = analytische Einzel-Abfragen für die OSINT-Pipeline
                    (kein Gedächtnis, spezialisierte Prompts, kurze Antworten)

Öffentliche API:
  llm_available()           → bool
  get_model_info()          → dict
  explain_escalation(...)   → str   (2 Sätze: Warum dieser Score?)
  generate_region_brief(...)→ str   (kompakter Lage-Text für HTML-Report)
  classify_signal(...)      → str   ("bestätigt" / "fraglich" / "falsch_alarm")
  quick_translate(text, target_lang) → str

Alle Funktionen haben einen Fallback-Wert, wenn Ollama offline ist –
der Rest des NEXUS-Systems läuft dadurch niemals in einen Fehler.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Interne Konstanten
# ─────────────────────────────────────────────────────────────────────────────

_OLLAMA_CHAT_URL   = f"{config.OLLAMA_HOST.rstrip('/')}/api/chat"
_OLLAMA_TAGS_URL   = f"{config.OLLAMA_HOST.rstrip('/')}/api/tags"
_DEFAULT_TIMEOUT   = 45          # Sekunden – kürzer als Brain (kein langer Dialog)
_CACHE_TTL         = 120         # Sekunden – identische Abfragen cachen
_MAX_SIGNAL_LABELS = 8           # Nur Top-N Signale an LLM übergeben

_SYSTEM_ANALYST = """Du bist NEXUS-Analyst, ein präziser geheimdienstlicher Lagebewerter.
Regeln:
- Antworte IMMER auf Deutsch.
- Antworte NUR mit dem angeforderten Text – keine Einleitung, kein Schlusskommentar.
- Maximal 2 Sätze pro Anfrage, außer explizit anders angegeben.
- Keine Wertung, keine Empfehlungen, keine Spekulation über Ursachen die nicht in den Daten stehen.
- Verwende militärischen/nachrichtendienstlichen Stil: sachlich, direkt, prägnant.
- Signalstärken sind objektive Messwerte – behandle sie als Fakten."""

_CACHE: dict[str, tuple[str, float]] = {}

# Claude API Endpunkt
_CLAUDE_API_URL  = "https://api.anthropic.com/v1/messages"
_CLAUDE_API_VER  = "2023-06-01"


# ─────────────────────────────────────────────────────────────────────────────
# Provider-Erkennung
# ─────────────────────────────────────────────────────────────────────────────

def _get_provider() -> str:
    """
    Bestimmt welchen LLM-Provider NEXUS nutzen soll.
    Reihenfolge: config.LLM_PROVIDER → auto-detect
    """
    provider = getattr(config, "LLM_PROVIDER", "auto").lower()
    if provider == "claude":
        return "claude"
    if provider == "ollama":
        return "ollama"
    # "auto": Claude wenn Key gesetzt, sonst Ollama
    key = getattr(config, "CLAUDE_API_KEY", "").strip()
    if key and key.startswith("sk-ant-"):
        return "claude"
    return "ollama"


def get_active_provider() -> str:
    """Gibt den aktiven Provider zurück (für UI/Logging)."""
    p = _get_provider()
    if p == "claude":
        model = getattr(config, "CLAUDE_MODEL", "claude-haiku-4-5-20251001")
        return f"Claude ({model})"
    return f"Ollama ({config.OLLAMA_MODEL})"


# ─────────────────────────────────────────────────────────────────────────────
# Hilfsfunktionen
# ─────────────────────────────────────────────────────────────────────────────

def _claude_call(prompt: str, timeout: int = 30) -> str:
    """
    Sendet einen Prompt an die Claude API (Anthropic).
    Nutzt direkte HTTP-Requests – kein anthropic-SDK nötig.
    Modell und Tokens aus config.CLAUDE_MODEL / config.CLAUDE_MAX_TOKENS.
    """
    api_key   = getattr(config, "CLAUDE_API_KEY", "").strip()
    model     = getattr(config, "CLAUDE_MODEL", "claude-haiku-4-5-20251001")
    max_tokens = getattr(config, "CLAUDE_MAX_TOKENS", 1500)

    if not api_key:
        return ""

    headers = {
        "x-api-key":         api_key,
        "anthropic-version": _CLAUDE_API_VER,
        "content-type":      "application/json",
    }
    payload = {
        "model":      model,
        "max_tokens": max_tokens,
        "system":     _SYSTEM_ANALYST,
        "messages": [
            {"role": "user", "content": prompt},
        ],
    }
    try:
        r = requests.post(_CLAUDE_API_URL, json=payload,
                          headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        # Claude API gibt {"content": [{"type": "text", "text": "..."}]}
        content_blocks = data.get("content", [])
        text = " ".join(
            b.get("text", "") for b in content_blocks if b.get("type") == "text"
        ).strip()
        return text
    except requests.exceptions.Timeout:
        logger.warning("[LLM/Claude] Timeout nach %ds", timeout)
        return ""
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response else "?"
        if status == 401:
            logger.error("[LLM/Claude] Ungültiger API-Key (401)")
        elif status == 429:
            logger.warning("[LLM/Claude] Rate-Limit erreicht (429)")
        else:
            logger.warning("[LLM/Claude] HTTP %s: %s", status, exc)
        return ""
    except Exception as exc:
        logger.warning("[LLM/Claude] Fehler: %s", exc)
        return ""


def _ollama_call(prompt: str, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """Sendet einen einzelnen User-Turn an Ollama (kein Gesprächsverlauf)."""
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": [
            {"role": "system",  "content": _SYSTEM_ANALYST},
            {"role": "user",    "content": prompt},
        ],
        "stream": False,
        "options": {
            "temperature":    0.3,
            "top_p":          0.85,
            "num_ctx":        4096,
            "repeat_penalty": 1.1,
        },
        "keep_alive": "5m",
    }
    try:
        r = requests.post(_OLLAMA_CHAT_URL, json=payload, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        content = data.get("message", {}).get("content", "").strip()
        return content
    except requests.exceptions.ConnectionError:
        logger.debug("[LLM] Ollama nicht erreichbar (ConnectionError)")
        return ""
    except requests.exceptions.Timeout:
        logger.warning("[LLM] Ollama Timeout nach %ds", timeout)
        return ""
    except Exception as exc:
        logger.warning("[LLM] Fehler: %s", exc)
        return ""


def _raw_call(prompt: str, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """
    Universeller LLM-Aufruf: wählt automatisch Claude oder Ollama.
    Claude → Fallback Ollama → Fallback "" (niemals Exception).
    """
    provider = _get_provider()
    if provider == "claude":
        result = _claude_call(prompt, timeout=min(timeout, 30))
        if result:
            return result
        # Fallback auf Ollama wenn Claude fehlschlägt
        logger.debug("[LLM] Claude fehlgeschlagen → Ollama-Fallback")
        return _ollama_call(prompt, timeout=timeout)
    return _ollama_call(prompt, timeout=timeout)


def _cached_call(cache_key: str, prompt: str, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """Interne Hilfsfunktion: Abfrage mit TTL-Cache."""
    now = time.monotonic()
    if cache_key in _CACHE:
        cached_val, cached_ts = _CACHE[cache_key]
        if now - cached_ts < _CACHE_TTL:
            return cached_val

    result = _raw_call(prompt, timeout)
    _CACHE[cache_key] = (result, now)
    return result


def _signal_text(signal_details: list[dict]) -> str:
    """Komprimiert Signal-Details zu einem lesbaren Text für den Prompt."""
    top = signal_details[:_MAX_SIGNAL_LABELS]
    lines = []
    for d in top:
        pts  = d.get("points", 0)
        conf = d.get("conf",   "?")
        lbl  = d.get("label",  d.get("signal", "?"))
        lines.append(f"  • {lbl} [{pts:.0f}Pkt, Konfidenz:{conf}]")
    return "\n".join(lines) if lines else "  (keine aktiven Signale)"


# ─────────────────────────────────────────────────────────────────────────────
# Öffentliche API
# ─────────────────────────────────────────────────────────────────────────────

def llm_available() -> bool:
    """
    Prüft schnell, ob Ollama erreichbar ist.
    Nutzt einen kurzen Timeout (3s) damit das UI nicht hängt.
    """
    try:
        r = requests.get(_OLLAMA_TAGS_URL, timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def get_model_info() -> dict:
    """
    Gibt Infos über das konfigurierte Modell zurück.
    Gibt leeres Dict zurück wenn Ollama offline.
    """
    try:
        r = requests.get(_OLLAMA_TAGS_URL, timeout=5)
        if r.status_code != 200:
            return {}
        models = r.json().get("models", [])
        for m in models:
            name = m.get("name", "")
            if name == config.OLLAMA_MODEL or name.startswith(config.OLLAMA_MODEL + ":"):
                return {
                    "name":     name,
                    "size_gb":  round(m.get("size", 0) / 1e9, 1),
                    "modified": m.get("modified_at", "")[:10],
                    "available": True,
                }
        return {"name": config.OLLAMA_MODEL, "available": False}
    except Exception:
        return {}


def explain_escalation(
    score:          float,
    level:          str,
    region:         str,
    signal_details: list[dict],
    coinc_note:     str = "",
) -> str:
    """
    Erklärt in 2 prägnanten Sätzen, warum der Eskalationsscore so hoch/niedrig ist.

    Rückgabe: Leerer String wenn Ollama offline (Fallback im Report zeigen).

    Beispiel-Output:
      "Der Score von 67/100 (ROT) wird primär durch zwei gleichzeitig aktive
       Aufklärungs­flugzeuge und einen bestätigten GPS-Jammer im Raum Donbass
       getrieben. Die Koinzidenz dreier Signalquellen innerhalb von 30 Minuten
       erhöht die Einschätzung auf koordinierte Aktivität."
    """
    if not signal_details:
        return ""

    sig_text = _signal_text(signal_details)
    today    = datetime.now().strftime("%d.%m.%Y %H:%M")
    coinc    = f"\nKoinzidenz-Hinweis: {coinc_note}" if coinc_note else ""

    prompt = (
        f"Aktueller Zeitpunkt: {today}\n"
        f"Region: {region or 'unbekannt'}\n"
        f"Eskalationsscore: {score}/100 – Stufe: {level}\n"
        f"Aktive Signale:\n{sig_text}{coinc}\n\n"
        "Erkläre in GENAU 2 Sätzen, warum dieser Score plausibel ist. "
        "Beziehe dich auf die stärksten Signale. "
        "Keine Einleitung, keine Quellenangabe."
    )

    cache_key = f"esc|{score}|{level}|{region}|{len(signal_details)}"
    result = _cached_call(cache_key, prompt)

    # Fallback: Automatisch generierter Text
    if not result and signal_details:
        top = signal_details[0]
        result = (
            f"Score {score}/100 ({level}) primär durch {top.get('label','aktive Signale')} "
            f"[{top.get('points',0):.0f} Pkt]. "
            f"{'Koinzidenz-Boost aktiv. ' if coinc_note else ''}"
            f"Insgesamt {len(signal_details)} Signalquelle(n) aktiv."
        )
    return result


def generate_region_brief(
    region:         str,
    score:          float,
    level:          str,
    signal_details: list[dict],
    articles:       Optional[list[dict]] = None,
    telegram_hint:  str = "",
    acled_hint:     str = "",
) -> str:
    """
    Erstellt einen kompakten Lage-Text (3–5 Sätze) für die "NEXUS Analysis"-Box
    im HTML-Report. Fasst OSINT-Signale + Artikel-Schlagzeilen zusammen.

    Rückgabe: Leerer String wenn Ollama offline.
    """
    sig_text  = _signal_text(signal_details)
    today     = datetime.now().strftime("%d.%m.%Y %H:%M UTC")

    # Top-5 Artikel-Titel für Kontext
    art_text = ""
    if articles:
        titles = [a.get("title", "")[:80] for a in articles[:5] if a.get("title")]
        if titles:
            art_text = "\nAktuelle Meldungen:\n" + "\n".join(f"  – {t}" for t in titles)

    # Telegram + ACLED Hinweise
    hints = ""
    if telegram_hint:
        hints += f"\nTelegram-Signal: {telegram_hint}"
    if acled_hint:
        hints += f"\nACLED-Signal: {acled_hint}"

    prompt = (
        f"Zeitpunkt: {today}\n"
        f"Region: {region or 'unbekannt'}\n"
        f"Eskalationsscore: {score}/100 – Stufe: {level}\n"
        f"Aktive OSINT-Signale:\n{sig_text}{hints}{art_text}\n\n"
        "Erstelle ein kurzes Lagebriefing in GENAU 3 Sätzen. "
        "Satz 1: Gesamtlage. Satz 2: Wichtigste Einzelsignale. Satz 3: Trend-Einschätzung. "
        "Kein Titel, keine Einleitung, keine Spiegelstriche."
    )

    cache_key = f"brief|{score}|{region}|{len(signal_details)}|{len(articles or [])}"
    result = _cached_call(cache_key, prompt)

    if not result:
        # Minimaler Fallback
        sig_labels = [d.get("label", "") for d in signal_details[:3]]
        result = (
            f"Lage in {region or 'der Region'}: Score {score}/100 ({level}). "
            + (f"Dominante Signale: {', '.join(sig_labels)}. " if sig_labels else "Keine aktiven Signale. ")
            + "KI-Analyse nicht verfügbar (Ollama offline)."
        )
    return result


def classify_signal(
    signal_type:  str,
    signal_label: str,
    context:      str = "",
) -> str:
    """
    Klassifiziert ein einzelnes OSINT-Signal als 'bestätigt' / 'fraglich' / 'falsch_alarm'.
    Kurze Begründung in einem Satz.

    Rückgabe: Leerer String wenn Ollama offline.
    """
    prompt = (
        f"Signal-Typ: {signal_type}\n"
        f"Signal-Beschreibung: {signal_label}\n"
        + (f"Kontext: {context}\n" if context else "")
        + "\nKlassifiziere dieses OSINT-Signal in EINER ZEILE:\n"
        "Format: [BESTÄTIGT|FRAGLICH|FALSCH_ALARM] – <Begründung in max. 10 Wörtern>"
    )
    cache_key = f"cls|{signal_type}|{signal_label[:40]}"
    return _cached_call(cache_key, prompt, timeout=20)


def quick_translate(text: str, target_lang: str = "de") -> str:
    """
    Schnelle Übersetzung kurzer Texte (max. 300 Zeichen).
    Primär für Telegram-Posts / ACLED-Beschreibungen aus Englisch/Ukrainisch.

    Rückgabe: Original-Text wenn Ollama offline oder Text bereits in Zielsprache.
    """
    if not text or len(text) > 300:
        return text

    lang_names = {"de": "Deutsch", "en": "Englisch", "uk": "Ukrainisch", "ru": "Russisch"}
    lang_name  = lang_names.get(target_lang, target_lang)

    prompt = (
        f"Übersetze exakt diesen Text ins {lang_name}. "
        f"Gib NUR die Übersetzung zurück, keine Erklärung:\n\n{text}"
    )
    cache_key = f"tr|{target_lang}|{hash(text)}"
    result = _cached_call(cache_key, prompt, timeout=20)
    return result if result else text


# ─────────────────────────────────────────────────────────────────────────────
# Batch-Funktion für den Live-Server / Report
# ─────────────────────────────────────────────────────────────────────────────

def enrich_escalation_result(esc_result: dict) -> dict:
    """
    Nimmt das dict von nexus_escalation.compute_escalation() und ergänzt es
    um LLM-generierte Felder:
      llm_explanation  – 2-Satz-Erklärung des Scores
      llm_brief        – 3-Satz-Lagebriefing
      llm_available    – bool

    Gibt das original dict zurück wenn Ollama offline (keine Felder geändert).
    """
    esc_result = dict(esc_result)  # Kopie, Original nicht ändern

    available = llm_available()
    esc_result["llm_available"] = available

    if not available:
        esc_result["llm_explanation"] = ""
        esc_result["llm_brief"]       = ""
        return esc_result

    score   = esc_result.get("score", 0)
    level   = esc_result.get("level", "GRUEN")
    region  = esc_result.get("region", "")
    details = esc_result.get("signal_details", [])
    coinc   = esc_result.get("coinc_note", "")

    esc_result["llm_explanation"] = explain_escalation(
        score, level, region, details, coinc
    )
    esc_result["llm_brief"] = generate_region_brief(
        region, score, level, details
    )
    return esc_result


# ─────────────────────────────────────────────────────────────────────────────
# CLI-Test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    print("=" * 60)
    print("NEXUS LLM-Bridge – Selbsttest")
    print("=" * 60)

    # Verfügbarkeit
    avail = llm_available()
    print(f"Ollama erreichbar : {'✓ JA' if avail else '✗ NEIN (starte: ollama serve)'}")

    if avail:
        info = get_model_info()
        print(f"Modell            : {info.get('name', config.OLLAMA_MODEL)}")
        print(f"Modell verfügbar  : {'✓' if info.get('available') else '✗ (ollama pull ' + config.OLLAMA_MODEL + ')'}")
        print(f"Modell-Größe      : {info.get('size_gb', '?')} GB")
    print()

    # Eskalations-Test mit Dummy-Daten
    test_signals = [
        {"signal": "isr_aircraft",    "label": "ISR-Aufklärer: RC-135 [SIGINT]",           "points": 20.0, "conf": "high"},
        {"signal": "gps_jamming",     "label": "GPS-Jamming HOCH (Donbass-Ost)",            "points": 10.0, "conf": "high"},
        {"signal": "transponder_off", "label": "2 Transponder AUS (Ghost-Track)",           "points": 7.5,  "conf": "medium"},
        {"signal": "telegram_surge",  "label": "Telegram Surge x7.2 (3 Kanäle)",           "points": 5.8,  "conf": "medium"},
        {"signal": "acled_events",    "label": "4 ACLED-Hochprioritäts-Ereignisse",         "points": 6.0,  "conf": "high"},
    ]

    print("── Test: explain_escalation ──")
    print("Score: 67/100 (ROT), Region: Ukraine")
    t0 = time.time()
    expl = explain_escalation(67, "ROT", "Ukraine", test_signals, "Koinzidenz x1.35 (5 Signale)")
    dt = time.time() - t0
    print(f"[{dt:.1f}s] {expl or '(kein Ergebnis – Ollama offline?)'}")
    print()

    print("── Test: generate_region_brief ──")
    test_articles = [
        {"title": "Ukraine meldet intensiven Beschuss im Raum Cherson"},
        {"title": "NATO aktiviert zusätzliche Überwachungsflüge"},
    ]
    t0 = time.time()
    brief = generate_region_brief(
        "Ukraine", 67, "ROT", test_signals,
        articles=test_articles,
        telegram_hint="Starke Kanal-Aktivität, Top-Score 8.5",
        acled_hint="4 Ereignisse in 24h, Surge-Ratio 2.1x"
    )
    dt = time.time() - t0
    print(f"[{dt:.1f}s] {brief or '(kein Ergebnis – Ollama offline?)'}")
    print()

    print("── Test: classify_signal ──")
    t0 = time.time()
    cls = classify_signal(
        "isr_aircraft",
        "RC-135 Rivet Joint über Schwarzem Meer, Transponder an",
        context="Bekannte NATO-Überwachungsroute"
    )
    dt = time.time() - t0
    print(f"[{dt:.1f}s] {cls or '(kein Ergebnis – Ollama offline?)'}")
    print()

    print("── Test: quick_translate ──")
    t0 = time.time()
    tr = quick_translate("Heavy artillery fire reported near Kherson", "de")
    dt = time.time() - t0
    print(f"[{dt:.1f}s] {tr}")
    print()

    print("=" * 60)
    print("Selbsttest abgeschlossen.")
    if not avail:
        print("Hinweis: Starte Ollama mit 'ollama serve' und führe den Test erneut aus.")
        sys.exit(1)
