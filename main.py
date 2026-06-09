"""
NEXUS - Hauptprogramm
Start:
    python main.py              # Sprach- + Hybrid-Modus (Mikrofon + Tippen)
    python main.py --text       # Textmodus + NEXUS spricht trotzdem
    python main.py --no-hybrid  # Nur Spracheingabe (kein Tippen)
    python main.py --no-wake    # Kein Wakeword
    python main.py --no-preflight
"""

from __future__ import annotations

import argparse
import os
import queue as _queue
import sys
import threading as _threading
import time

import config
from nexus_brain import NexusBrain
from nexus_search import (
    needs_search, needs_news_search,
    web_search, news_search, multi_angle_search, clean_query,
    historical_search, historical_multi_search,
)

# OSINT-Module (lazy import – nur wenn nötig)
def _load_flights():
    try:
        from nexus_flights import flights_for_llm, get_flights, REGIONS
        return flights_for_llm, get_flights, REGIONS
    except ImportError:
        return None, None, {}

def _load_rss():
    try:
        from nexus_rss import news_for_llm, fetch_news
        return news_for_llm, fetch_news
    except Exception:
        return None, None

def _load_report():
    try:
        from nexus_report import generate_report
        return generate_report
    except ImportError:
        return None

def _load_imgcheck():
    try:
        from nexus_imgcheck import full_image_check, format_check_for_terminal
        return full_image_check, format_check_for_terminal
    except ImportError:
        return None, None

def _load_weather():
    try:
        from nexus_weather import weather_for_llm, weather_for_report
        return weather_for_llm, weather_for_report
    except ImportError:
        return None, None

def _load_maritime():
    try:
        from nexus_maritime import maritime_for_llm, get_maritime_situation
        return maritime_for_llm, get_maritime_situation
    except ImportError:
        return None, None


# ===================================================
# Lade-Animation (Spinner waehrend NEXUS denkt)
# ===================================================

class _Spinner:
    """
    Zeigt eine Animation in der aktuellen Zeile waehrend NEXUS recherchiert/denkt.
    Nutzung als Context Manager:  with _Spinner("NEXUS denkt"):  ...
    Oder direkt: s = _Spinner("Suche"); s.start(); ...; s.stop()
    """
    # Braille-Spinner (schoen, wenn Terminal Unicode kann)
    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    # ASCII-Fallback
    _FRAMES_ASCII = ["|", "/", "-", "\\"]

    def __init__(self, message: str = "NEXUS denkt") -> None:
        self._msg = message
        self._stop_evt = _threading.Event()
        self._thread: _threading.Thread | None = None

    def _run(self) -> None:
        color = getattr(config, "COLOR_DEBUG", "\033[32m")
        reset = getattr(config, "COLOR_RESET", "\033[0m")
        frames = self._FRAMES
        i = 0
        width = len(self._msg) + 6
        while not self._stop_evt.is_set():
            frame = frames[i % len(frames)]
            line = "{}{}  {}{}".format(color, self._msg, frame, reset)
            print("\r" + line, end="", flush=True)
            i += 1
            self._stop_evt.wait(timeout=0.1)
        # Zeile aufraeumen
        print("\r" + " " * (width + 10) + "\r", end="", flush=True)

    def start(self) -> "_Spinner":
        self._stop_evt.clear()
        self._thread = _threading.Thread(target=self._run, daemon=True, name="nexus-spin")
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self._thread = None

    def update(self, message: str) -> None:
        """Aendert den angezeigten Text waehrend der Spinner laeuft."""
        self._msg = message

    def __enter__(self) -> "_Spinner":
        return self.start()

    def __exit__(self, *_) -> None:
        self.stop()


# ===================================================
# ANSI-Farben
# ===================================================

def _enable_ansi_windows() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes, ctypes.wintypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.wintypes.DWORD()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        import colorama
        colorama.init(autoreset=False)
    except ImportError:
        os.system("")


def _c(color: str, text: str) -> str:
    if not getattr(config, "COLOR_ENABLED", False):
        return text
    return "{}{}{}".format(color, text, getattr(config, "COLOR_RESET", "\033[0m"))


def nexus_print(msg: str) -> None:
    print(_c(getattr(config, "COLOR_NEXUS", "\033[92m"), msg), flush=True)


def debug_print(msg: str) -> None:
    if config.VERBOSE:
        print(_c(getattr(config, "COLOR_DEBUG", "\033[32m"), msg), flush=True)


# ===================================================
# Keyboard-Thread (NUR fuer Hybrid-Sprachmodus)
# ===================================================
# WICHTIG: Dieser Thread darf NICHT laufen wenn input() genutzt wird
# (stdin kann nur von einem Leser gleichzeitig konsumiert werden).
# Im Textmodus wird ausschliesslich input() verwendet.

_kb_queue: _queue.Queue = _queue.Queue()
_kb_thread: _threading.Thread | None = None
_kb_thread_active = False


def _start_keyboard_thread() -> None:
    """Startet Keyboard-Thread - NUR im Hybrid-Sprachmodus aufrufen."""
    global _kb_thread, _kb_thread_active

    if _kb_thread is not None and _kb_thread.is_alive():
        return

    def _worker() -> None:
        while _kb_thread_active:
            try:
                line = sys.stdin.readline()
                if line:
                    text = line.strip()
                    if text:
                        _kb_queue.put(text)
                elif line == "":
                    # EOF - kurz warten und weiter versuchen
                    time.sleep(0.1)
            except Exception:  # noqa: BLE001
                # Fehler im stdin-Lesen: NICHT abbrechen, kurz pausieren
                time.sleep(0.2)

    _kb_thread_active = True
    _kb_thread = _threading.Thread(target=_worker, daemon=True, name="nexus-keyboard")
    _kb_thread.start()
    debug_print("[NEXUS] Keyboard-Thread gestartet (tippen + Enter moeglich).")


# ===================================================
# Hybrid-Eingabe (Mikrofon + Tastatur gleichzeitig)
# ===================================================

def _listen_hybrid(ears) -> tuple[str, str | None]:
    """
    Wartet gleichzeitig auf Sprache (Mikrofon) und Tastatureingabe.
    Gibt (source, text) zurueck, wobei source 'voice' oder 'keyboard' ist.
    Tastatureingabe hat Prioritaet: wird sofort verarbeitet.
    """
    voice_result: list[str | None] = [None]
    voice_done = _threading.Event()

    def _voice_worker() -> None:
        try:
            voice_result[0] = ears.listen()
        except Exception:  # noqa: BLE001
            voice_result[0] = None
        finally:
            voice_done.set()

    _threading.Thread(target=_voice_worker, daemon=True, name="nexus-v-listen").start()

    total_wait = config.STT_TIMEOUT + config.STT_PHRASE_TIME_LIMIT + 3.0
    start = time.monotonic()

    while time.monotonic() - start < total_wait:
        # Tastatur hat Prioritaet
        try:
            return ("keyboard", _kb_queue.get_nowait())
        except _queue.Empty:
            pass
        # Dann auf Sprache pruefen
        if voice_done.wait(timeout=0.1):
            return ("voice", voice_result[0])

    # Timeout - Ergebnis nehmen was da ist
    return ("voice", voice_result[0])


# ===================================================
# Sprechen mit Interrupt-Ueberwachung
# ===================================================

def _speak_with_interrupt(voice, text: str, monitor_keyboard: bool = False) -> None:
    """
    Spricht text aus.

    monitor_keyboard=True (Hybrid-Sprachmodus):
        Keyboard-Queue wird parallel ueberwacht.
        'stopp' + Enter unterbricht die Ausgabe sofort.

    monitor_keyboard=False (Textmodus / reiner Sprachmodus):
        Sprechen laeuft synchron durch - kein stdin-Konflikt.
    """
    if not monitor_keyboard:
        voice.speak(text)
        return

    # Hybrid: Thread + Keyboard-Monitor
    speak_thread = _threading.Thread(target=voice.speak, args=(text,), daemon=True)
    speak_thread.start()

    interrupt_words = getattr(config, "INTERRUPT_WORDS", ["stopp", "stop", "halt"])

    while speak_thread.is_alive():
        try:
            kb_input = _kb_queue.get_nowait()
            if any(w in kb_input.lower() for w in interrupt_words):
                debug_print("[NEXUS] Ausgabe unterbrochen.")
                voice.stop()
                speak_thread.join(timeout=2.0)
                # Queue leeren
                while True:
                    try:
                        _kb_queue.get_nowait()
                    except _queue.Empty:
                        break
                return
            else:
                # Kein Interrupt - Eingabe zurueck in Queue
                _kb_queue.put(kb_input)
        except _queue.Empty:
            pass
        time.sleep(0.04)

    speak_thread.join(timeout=1.0)


# ===================================================
# Hilfsfunktionen
# ===================================================

def is_exit_command(text: str) -> bool:
    if not text:
        return False
    return any(word in text.lower().strip() for word in config.EXIT_WORDS)


def is_interrupt_command(text: str) -> bool:
    if not text:
        return False
    words = getattr(config, "INTERRUPT_WORDS", ["stopp", "stop", "halt"])
    return any(w in text.lower().strip() for w in words)


def _needs_analysis(text: str) -> bool:
    words = getattr(config, "ANALYSIS_TRIGGER_WORDS", [])
    return any(w in text.lower() for w in words)


def handle_query(brain: NexusBrain, user_text: str,
                 spinner: "_Spinner | None" = None,
                 historical: bool = False) -> str:
    """
    Vier Modi:
      Historisch  -> historical_multi_search (kein Zeitfilter) + chat mit hist. System-Prompt
      Analyse     -> multi_angle_search + chat_analysis
      Nachrichten -> news_search + chat
      Normal      -> web_search (optional) + chat
    Wenn spinner uebergeben wird, zeigt er Recherche vs. Denkphase an.
    Folgefragen werden automatisch mit dem vorherigen Thema angereichert.
    """
    from nexus_search import is_followup_question, enrich_query_with_context

    query = clean_query(user_text) or user_text

    # Folgefrage? Vorheriges Thema aus Gespraechshistorie einbauen
    if is_followup_question(user_text) and brain.history:
        last_user = next(
            (m["content"] for m in reversed(brain.history) if m["role"] == "user"),
            None,
        )
        if last_user:
            enriched = enrich_query_with_context(query, last_user)
            if enriched != query:
                debug_print("[NEXUS] Folgefrage - erweiterter Query: {!r}".format(enriched))
                query = enriched

    # ── HISTORISCHER MODUS ──────────────────────────────────────────────────
    if historical:
        debug_print("[NEXUS][HIST] Historische Suche: {!r}".format(query))
        if spinner:
            spinner.update("NEXUS durchsucht die Geschichte")
        # Analyse-Wörter -> tiefe historische Multi-Suche
        if _needs_analysis(user_text):
            search_context = historical_multi_search(query)
        else:
            search_context = historical_search(query)
        if spinner:
            spinner.update("NEXUS analysiert historische Quellen")
        # System-Prompt temporaer um historischen Addon ergaenzen
        hist_addon = getattr(config, "SYSTEM_PROMPT_HISTORICAL_ADDON", "")
        original_prompt = brain.system_prompt
        if hist_addon and hist_addon not in original_prompt:
            brain.system_prompt = hist_addon + "\n" + original_prompt
        result = brain.chat(user_text, search_context=search_context)
        brain.system_prompt = original_prompt  # zuruecksetzen
        return result

    # ── ANALYSE-MODUS ───────────────────────────────────────────────────────
    if _needs_analysis(user_text) and needs_search(user_text):
        debug_print("[NEXUS] Analyse-Modus: {!r}".format(query))
        if spinner:
            spinner.update("NEXUS recherchiert")
        search_context = multi_angle_search(query)
        if spinner:
            spinner.update("NEXUS analysiert")
        return brain.chat_analysis(user_text, search_context=search_context)

    # ── NEWS / WEB ──────────────────────────────────────────────────────────
    if needs_search(user_text):
        if needs_news_search(user_text):
            debug_print("[NEXUS] Nachrichtensuche: {!r}".format(query))
            if spinner:
                spinner.update("NEXUS sucht Nachrichten")
            search_context = news_search(query)
        else:
            debug_print("[NEXUS] Websuche: {!r}".format(query))
            if spinner:
                spinner.update("NEXUS recherchiert")
            search_context = web_search(query)
        if spinner:
            spinner.update("NEXUS denkt")
        return brain.chat(user_text, search_context=search_context)

    return brain.chat(user_text)


def _strip_wakeword(text: str) -> str:
    if not text:
        return ""
    cleaned = text.strip().lower()
    aliases = sorted(
        {w.lower() for w in [config.WAKEWORD] + list(config.WAKEWORD_ALIASES)},
        key=len, reverse=True,
    )
    for alias in aliases:
        if cleaned.startswith(alias):
            cleaned = cleaned[len(alias):]
            break
        idx = cleaned.find(alias)
        if 0 <= idx <= 6:
            cleaned = cleaned[idx + len(alias):]
            break
    return cleaned.lstrip(" ,.!?:;-").strip()


def _contains_wakeword(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    aliases = [config.WAKEWORD.lower()] + [a.lower() for a in config.WAKEWORD_ALIASES]
    return any(a in lower for a in aliases)


# ===================================================
# Laufmodi
# ===================================================

def run_voice_mode(brain: NexusBrain) -> int:
    try:
        from nexus_voice import NexusEars, NexusVoice
    except RuntimeError as exc:
        print("[NEXUS] Sprach-Module nicht verfuegbar: {}".format(exc), file=sys.stderr, flush=True)
        return run_text_mode(brain, voice=None)

    try:
        voice = NexusVoice()
    except Exception as exc:
        print("[NEXUS] TTS-Init fehlgeschlagen: {}".format(exc), file=sys.stderr, flush=True)
        return run_text_mode(brain, voice=None)

    try:
        ears = NexusEars()
    except RuntimeError as exc:
        print("[NEXUS] Mikrofon-Init fehlgeschlagen: {}".format(exc), file=sys.stderr, flush=True)
        return run_text_mode(brain, voice=voice)

    if not ears.initialize_microphone():
        print("[NEXUS] Kein Mikrofon.", file=sys.stderr, flush=True)
        return run_text_mode(brain, voice=voice)

    use_hybrid = getattr(config, "USE_HYBRID_INPUT", False)
    if use_hybrid:
        _start_keyboard_thread()
        keyboard_hint = " (oder tippen + Enter)"
    else:
        keyboard_hint = ""

    def get_input() -> tuple[str, str | None]:
        if use_hybrid:
            return _listen_hybrid(ears)
        return ("voice", ears.listen())

    nexus_print("NEXUS: {}".format(config.BEGRUESSUNG))
    _speak_with_interrupt(voice, config.BEGRUESSUNG, monitor_keyboard=use_hybrid)

    while True:
        if config.USE_WAKEWORD:
            if config.WAKEWORD_LOG:
                hint_msg = "[NEXUS] (Standby - sage '{}'{})" .format(
                    config.WAKEWORD, keyboard_hint)
                if config.VERBOSE:
                    debug_print(hint_msg)

            source, heard = get_input()
            if not heard:
                continue

            if source == "keyboard":
                # Direkte Tastatureingabe: kein Wakeword noetig
                if is_interrupt_command(heard) and not is_exit_command(heard):
                    continue
                user_text = heard
            elif _contains_wakeword(heard):
                command = _strip_wakeword(heard)
                if command:
                    user_text = command
                else:
                    _speak_with_interrupt(voice, config.WAKEWORD_ACK, monitor_keyboard=use_hybrid)
                    _, user_text = get_input()
                    if not user_text:
                        _speak_with_interrupt(voice, "Keine Eingabe.", monitor_keyboard=use_hybrid)
                        continue
            else:
                continue
        else:
            if use_hybrid and config.VERBOSE:
                debug_print("[NEXUS] Hoere zu{}...".format(keyboard_hint))
            source, user_text = get_input()
            if not user_text:
                continue

        debug_print("[NEXUS] << {}".format(user_text))

        if is_exit_command(user_text):
            _speak_with_interrupt(voice, "Verstanden. NEXUS faehrt herunter.", monitor_keyboard=use_hybrid)
            nexus_print("NEXUS: Verstanden. NEXUS faehrt herunter.")
            return 0

        try:
            answer = handle_query(brain, user_text)
        except Exception as exc:  # noqa: BLE001
            answer = "Fehler: {}".format(exc)

        nexus_print("NEXUS: {}\n".format(answer))
        print(_c(getattr(config, "COLOR_DEBUG", "\033[32m"), "─" * 48 + " ✓"), flush=True)
        print()
        _speak_with_interrupt(voice, answer, monitor_keyboard=use_hybrid)


def _speak_text_mode(voice, text: str, timeout: float = 60.0) -> None:
    """
    TTS im Textmodus: laeuft im Hintergrund-Thread mit Timeout.
    Blockiert den Hauptthread maximal 'timeout' Sekunden.
    Danach wird TTS abgebrochen und input() kann aufgerufen werden.
    """
    if voice is None:
        return
    t = _threading.Thread(target=voice.speak, args=(text,), daemon=True, name="nexus-tts-text")
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        try:
            voice.stop()
        except Exception:  # noqa: BLE001
            pass


def run_text_mode(brain: NexusBrain, voice=None, ears=None) -> int:
    """
    Textmodus: Eingabe per Tastatur (input()) als Standard.
    Sprache optional: 'v' + Enter aktiviert Mikrofon fuer EINE Frage.
    Historischer Modus: 'h' + Enter togglet zeitunbegrenzte Suche.
    Kein Keyboard-Thread, kein Conflict, kein ueberlappendes Ausgabe-Chaos.
    """
    # Sprachaktivierungs-Woerter
    voice_triggers = {"v", "voice", "sprache", "mikro", "hoer zu", "hoere zu"}

    # Modi-Flags
    historical_mode = False
    lagebild_mode   = False

    def _mode_label() -> str:
        """Gibt den aktuellen Modus-Label fuer den Input-Prompt zurueck."""
        labels = []
        if historical_mode:
            labels.append("\033[33m[HIST]\033[0m")
        if lagebild_mode:
            labels.append("\033[36m[LAGE]\033[0m")
        return " " + " ".join(labels) if labels else ""

    # Prompt-Hinweis je nach verfuegbaren Modi
    if ears is not None:
        base_hint = " (h=Hist | l=Lage | d=Dash | m=Karte | delta | r <Ziel> | wl | i=Bild | v=Sprache | exit=Ende)"
    else:
        base_hint = " (h=Hist | l=Lage | d=Dash | m=Karte | delta | r <Ziel> | wl | i=Bild | exit=Ende)"

    nexus_print("NEXUS: {}".format(config.BEGRUESSUNG))
    _speak_text_mode(voice, config.BEGRUESSUNG, timeout=15.0)

    print("(Textmodus{}  |  Tippen + Enter)\n".format(base_hint), flush=True)

    # ── Angepinnte Region: automatisch beim Start laden ──────────────────────
    _pending_inputs: list = []
    _pinned_region = getattr(config, "PINNED_REGION", "").strip()
    if _pinned_region:
        lagebild_mode = True
        _pending_inputs.append(_pinned_region)
        print(
            "\n\033[36m[NEXUS] Angepinnte Region: '{}'\033[0m\n"
            "  Lagebild wird automatisch geladen...\n".format(_pinned_region),
            flush=True,
        )

    while True:
        try:
            prompt = "Sie{}{}: ".format(_mode_label(), base_hint)
            if _pending_inputs:
                user_input = _pending_inputs.pop(0)
                print("{}{}{}".format(prompt, user_input, ""), flush=True)
            else:
                user_input = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not user_input:
            continue

        # ── H-Toggle: Historischer Modus ────────────────────────────────────
        if user_input.lower() in {"h", "hist", "historisch", "history"}:
            historical_mode = not historical_mode
            if historical_mode:
                print(
                    "\n\033[33m[HISTORISCHER MODUS AKTIV]\033[0m\n"
                    "  Suche ohne Zeitfilter - alle Quellen, alle Jahrzehnte.\n"
                    "  Alte Dokumente, Archive, historische Berichte werden einbezogen.\n"
                    "  → H erneut druecken zum Deaktivieren.\n",
                    flush=True
                )
            else:
                print("\n[Historischer Modus deaktiviert] → Zurueck zur aktuellen Suche.\n", flush=True)
            continue

        # ── L-Toggle: Lagebild-Modus ─────────────────────────────────────────
        if user_input.lower() in {"l", "lage", "lagebild", "lagebericht"}:
            lagebild_mode = not lagebild_mode
            if lagebild_mode:
                print(
                    "\n\033[36m[LAGEBILD-MODUS AKTIV]\033[0m\n"
                    "  Naechste Anfrage kombiniert: Flugdaten + RSS-News + Web-Analyse.\n"
                    "  Ergebnis wird als HTML-Report im Browser geoeffnet.\n"
                    "  Beispiel: 'Lage Iran' oder 'Ukraine aktuell'\n"
                    "  → L erneut druecken zum Deaktivieren.\n",
                    flush=True
                )
            else:
                print("\n[Lagebild-Modus deaktiviert] → Normalmodus.\n", flush=True)
            continue

        # ── W-Befehl: Watchlist ──────────────────────────────────────────────
        if user_input.lower() in {"w", "watch", "watchlist"}:
            try:
                from nexus_watchlist import show as _wl_show
                nexus_print("NEXUS: {}\n".format(_wl_show()))
            except Exception as _we:
                nexus_print("NEXUS: Watchlist-Fehler: {}\n".format(_we))
            continue

        if user_input.lower().startswith("w+ ") or user_input.lower().startswith("watch+ "):
            term = user_input.split(" ", 1)[1].strip()
            try:
                from nexus_watchlist import add as _wl_add
                nexus_print("NEXUS: {}\n".format(_wl_add(term)))
            except Exception as _we:
                nexus_print("NEXUS: Fehler: {}\n".format(_we))
            continue

        if user_input.lower().startswith("w- ") or user_input.lower().startswith("watch- "):
            term = user_input.split(" ", 1)[1].strip()
            try:
                from nexus_watchlist import remove as _wl_remove
                nexus_print("NEXUS: {}\n".format(_wl_remove(term)))
            except Exception as _we:
                nexus_print("NEXUS: Fehler: {}\n".format(_we))
            continue

        # ── M-Befehl: Persistentes Lagebild (Live-Map) ──────────────────────
        if user_input.lower() in {"m", "map", "livemap", "live map", "karte", "live karte"}:
            try:
                import webbrowser as _wb
                _wb.open("http://localhost:11430/livemap")
                nexus_print("NEXUS: 🗺 Persistentes Lagebild geöffnet → http://localhost:11430/livemap\n"
                            "  Alle Layer (Flugzeuge, HUMINT, Fusion, Brände, Konflikte) auto-refresh.\n"
                            "  Seite offen lassen – aktualisiert sich alle 3 Minuten selbst.\n")
            except Exception as _me:
                nexus_print(f"NEXUS: Fehler: {_me}\n")
            continue

        # ── D-Befehl: Multi-Region Dashboard ────────────────────────────────
        if user_input.lower() in {"d", "dash", "dashboard", "übersicht", "uebersicht"}:
            try:
                import webbrowser as _wb
                _wb.open("http://localhost:11430/dashboard")
                nexus_print("NEXUS: ✅ Dashboard geöffnet → http://localhost:11430/dashboard\n"
                            "  Zeigt alle Watchlist-Regionen + Wirtschaftsindikatoren live.\n"
                            "  Auto-Refresh alle 5 Minuten.\n")
            except Exception as _de:
                nexus_print(f"NEXUS: Fehler beim Öffnen des Dashboards: {_de}\n")
            continue

        if user_input.lower() in {"tagesbericht", "brief", "daily", "morgenlagebild"}:
            try:
                from nexus_daily import create_daily_brief as _daily
                nexus_print("NEXUS: Erstelle Tagesbericht – bitte warten...\n")
                path = _daily()
                nexus_print("NEXUS: ✅ Tagesbericht fertig → {}\n".format(
                    __import__("os").path.basename(path)))
            except Exception as _de:
                nexus_print("NEXUS: Fehler: {}\n".format(_de))
            continue

        if user_input.lower() in {"tagesbericht einrichten", "brief einrichten", "schedule brief"}:
            try:
                from nexus_daily import setup_scheduler as _sched
                ok = _sched(hour=7, minute=0)
                nexus_print("NEXUS: {}\n".format(
                    "✅ Tagesbericht täglich 07:00 Uhr eingerichtet." if ok
                    else "❌ Fehler. Bitte als Administrator ausführen."))
            except Exception as _de:
                nexus_print("NEXUS: Fehler: {}\n".format(_de))
            continue

        if user_input.lower() in {"trend", "trends", "memory", "speicher"}:
            try:
                from nexus_memory import get_trend_text
                nexus_print("NEXUS: {}\n".format(get_trend_text(days=7)))
            except Exception as _me:
                nexus_print("NEXUS: Speicher-Fehler: {}\n".format(_me))
            continue

        # ── E-Befehl: Entity-Tracking (Palantir-Kern) ────────────────────────
        if user_input.lower() in {"e", "entities", "akteure", "entity"}:
            try:
                from nexus_entities import cli_summary, get_tracker  # type: ignore
                nexus_print("NEXUS: Entity-Tracking\n" + cli_summary() + "\n")
                nexus_print("  Befehle: 'e list', 'e search [name]', 'e graph', 'e show [id]'\n")
            except Exception as _ee:
                nexus_print("NEXUS: Entity-Fehler: {}\n".format(_ee))
            continue

        if user_input.lower() in {"e list", "entities list", "akteure liste"}:
            try:
                from nexus_entities import get_tracker  # type: ignore
                ents = get_tracker().get_all_entities(limit=25)
                if not ents:
                    nexus_print("NEXUS: Noch keine Entitäten getrackt.\n"
                                "  Tipp: Lagebild erstellen (L-Modus) → Entitäten werden automatisch extrahiert.\n")
                else:
                    lines = ["NEXUS: Getrackte Entitäten:"]
                    for e in ents:
                        last = (e.get("last_seen") or "")[:10]
                        lines.append(
                            "  {} {} ({}) – {}× gesehen | zuletzt: {}".format(
                                e["icon"], e["name"], e["type"], e["mentions"], last
                            )
                        )
                    nexus_print("\n".join(lines) + "\n")
            except Exception as _ee:
                nexus_print("NEXUS: Fehler: {}\n".format(_ee))
            continue

        if user_input.lower().startswith("e search ") or user_input.lower().startswith("entity search "):
            query = user_input.split(" ", 2)[-1].strip()
            try:
                from nexus_entities import get_tracker  # type: ignore
                results = get_tracker().search_entities(query, limit=10)
                if not results:
                    nexus_print("NEXUS: Keine Entitäten für '{}\' gefunden.\n".format(query))
                else:
                    lines = ["NEXUS: Suchergebnisse für '{}':".format(query)]
                    for e in results:
                        lines.append("  {} {} ({}) ID:{} – {}×".format(
                            e["icon"], e["name"], e["type"], e["id"], e["mentions"]))
                    nexus_print("\n".join(lines) + "\n")
            except Exception as _ee:
                nexus_print("NEXUS: Fehler: {}\n".format(_ee))
            continue

        if user_input.lower().startswith("e show "):
            eid = user_input.split(" ", 2)[-1].strip()
            try:
                from nexus_graph import get_graph  # type: ignore
                analysis = get_graph().analyze_entity(eid)
                if analysis.get("error"):
                    nexus_print("NEXUS: {}\n".format(analysis["error"]))
                else:
                    e   = analysis["entity"]
                    pol = analysis.get("pattern_of_life", {})
                    lines = [
                        "NEXUS: {} {} ({})".format(e["icon"], e["name"], e["type"]),
                        "  Sichtungen: {} | Konfidenz: {:.0%}".format(e["mentions"], e["confidence"]),
                        "  Erste Sichtung: {} | Letzte: {}".format(
                            (e.get("first_seen") or "?")[:10],
                            (e.get("last_seen")  or "?")[:10]),
                    ]
                    if pol.get("peak_hour_utc") is not None:
                        lines.append("  Pattern: Peak {}:00 UTC, {}".format(
                            pol["peak_hour_utc"], pol.get("peak_weekday", "?")))
                    if pol.get("frequent_associates"):
                        assoc = ", ".join(a["name"] for a in pol["frequent_associates"][:3])
                        lines.append("  Häufige Begleiter: {}".format(assoc))
                    if analysis.get("connections"):
                        c = analysis["connections"][0]
                        lines.append("  Nächste Verbindung: {} ({} Hop)".format(
                            c["target"], c["hops"]))
                    nexus_print("\n".join(lines) + "\n")
            except Exception as _ee:
                nexus_print("NEXUS: Fehler: {}\n".format(_ee))
            continue

        if user_input.lower() in {"e graph", "entity graph", "linkmap", "link analyse", "netzwerk"}:
            try:
                import webbrowser as _wb
                _wb.open("http://localhost:11430/linkmap")
                nexus_print("NEXUS: Maltego-Style Link-Analyse geöffnet\n"
                            "  → http://localhost:11430/linkmap\n"
                            "  Zeigt alle getrackte Akteure, Organisationen, Orte als Netzwerk.\n"
                            "  Klick auf Knoten → Pattern-of-Life + Timeline.\n")
            except Exception as _ee:
                nexus_print("NEXUS: Fehler: {}\n".format(_ee))
            continue

        # ── I-Befehl: Bild-Verifikation ──────────────────────────────────────
        lower_input = user_input.lower().strip()
        if lower_input.startswith(("i ", "img ", "bild ", "check ")) or lower_input in {"i", "img"}:
            # Pfad extrahieren
            parts = user_input.split(None, 1)
            img_path = parts[1].strip().strip('"\'') if len(parts) > 1 else ""

            if not img_path:
                print(
                    "\n[BILD-CHECK] Bitte Bildpfad angeben:\n"
                    "  Beispiel: i C:\\Users\\Du\\Downloads\\bild.jpg\n",
                    flush=True
                )
                continue

            full_check, fmt_terminal = _load_imgcheck()
            if full_check is None:
                print("[BILD-CHECK] nexus_imgcheck.py nicht gefunden.", flush=True)
                continue

            print(f"\n\033[36m[NEXUS] Prüfe Bild: {img_path}\033[0m", flush=True)
            with _Spinner("NEXUS analysiert Bild") as spin:
                try:
                    result = full_check(img_path)
                except Exception as exc:
                    result = {"error": str(exc)}

            output = fmt_terminal(result)
            print("\n" + output, flush=True)

            # LLM-Einschätzung dazu holen?
            try:
                from nexus_imgcheck import format_check_for_llm
                llm_ctx = format_check_for_llm(result)
                with _Spinner("NEXUS bewertet") as spin:
                    llm_answer = brain.chat(
                        "Bewerte diese Bild-Verifikationsdaten. Ist das Bild vertrauenswürdig? "
                        "Gibt es Red Flags?",
                        search_context=llm_ctx
                    )
                nexus_print("\nNEXUS-Einschätzung: {}\n".format(llm_answer))
            except Exception:
                pass

            print(_c(getattr(config, "COLOR_DEBUG", "\033[32m"), "─" * 48 + " ✓"), flush=True)
            print()
            continue

        # ── R-Befehl: Reisesicherheits-Analyse (T91) ─────────────────────────
        if user_input.lower().startswith("r "):
            dest = user_input[2:].strip()
            if dest:
                nexus_print(f"NEXUS: ✈ Reisesicherheits-Analyse für \033[96m{dest}\033[0m – bitte warten...\n")
                with _Spinner(f"NEXUS analysiert {dest}") as spin:
                    try:
                        from nexus_travel_safety import travel_safety_report, format_travel_brief  # type: ignore
                        report = travel_safety_report(dest)
                        brief  = format_travel_brief(report)
                    except Exception as _te:
                        brief = f"\n[FEHLER] Reisesicherheits-Analyse: {_te}\n"
                print(brief, flush=True)
                continue

        # ── Delta-Karte öffnen (T92) ─────────────────────────────────────────
        if user_input.lower() in {"delta", "vergleich", "diff", "delta-karte"}:
            try:
                import webbrowser as _wb
                _wb.open("http://localhost:11430/delta")
                nexus_print("NEXUS: 🔴🔵 Delta-Karte geöffnet → http://localhost:11430/delta\n"
                            "  Vergleicht aktuelle vs. früherer Lage – neue, verschwundene und\n"
                            "  verschärfte Ereignisse werden farblich markiert.\n")
            except Exception as _dte:
                nexus_print(f"NEXUS: Fehler: {_dte}\n")
            continue

        # ── Watchlist-UI öffnen (T93) ────────────────────────────────────────
        if user_input.lower() in {"wl", "wl-ui", "watchlist-ui", "watchlist ui", "wui"}:
            try:
                import webbrowser as _wb
                _wb.open("http://localhost:11430/watchlist")
                nexus_print("NEXUS: 📋 Watchlist-Manager geöffnet → http://localhost:11430/watchlist\n"
                            "  Begriffe + Regionen verwalten, Schwellenwerte setzen, Export als JSON.\n")
            except Exception as _wue:
                nexus_print(f"NEXUS: Fehler: {_wue}\n")
            continue

        # ── SAR-Befehl: Sentinel-1 Radar-Schiffsdetektion ───────────────────
        # Syntax:  sar                    → Hilfe + verfügbare Regionen
        #          sar hormuz             → Übersicht  870 m/px
        #          sar hormuz zoom        → Zoom       65 m/px  (0.15°)
        #          sar hormuz fine        → Fein       22 m/px  (0.05°)
        #          sar zoom:26.45,56.25   → Freie GPS-Koordinate
        _sar_lo = user_input.lower().strip()
        if _sar_lo == "sar" or _sar_lo.startswith("sar "):
            # ── Region + Auflösungsstufe parsen ──────────────────────────────
            _sar_parts = user_input.strip().split()           # ["sar", "hormuz", "fine"]
            _sar_region_raw = _sar_parts[1] if len(_sar_parts) > 1 else ""
            _sar_level      = _sar_parts[2].lower() if len(_sar_parts) > 2 else ""

            # Auflösungsstufe in Suffix übersetzen
            _sar_suffix_map = {"zoom": "-zoom", "fine": "-fine", "fein": "-fine",
                               "grob": "", "overview": "", "übersicht": ""}
            _sar_suffix = _sar_suffix_map.get(_sar_level, "")

            # Endgültige Region bestimmen
            # ── sar status → Lernstatus anzeigen ─────────────────────────
            if _sar_region_raw.lower() in ("status", "learn", "lernen", "lern"):
                try:
                    from nexus_sar_learner import format_stats_terminal  # type: ignore
                    print("\n" + format_stats_terminal() + "\n", flush=True)
                except ImportError:
                    nexus_print("NEXUS: nexus_sar_learner.py nicht gefunden.\n")
                continue

            if not _sar_region_raw:
                # Keine Region angegeben → Hilfe zeigen
                print(
                    "\n\033[36m╔══ NEXUS SAR – Sentinel-1 Radar-Schiffsdetektion ══════════════╗\033[0m\n"
                    "\033[36m║\033[0m  Syntax:  sar [region] [zoom|fine]                              \033[36m║\033[0m\n"
                    "\033[36m╠══ Voreingestellte Regionen ════════════════════════════════════╣\033[0m\n"
                    "\033[36m║\033[0m  sar hormuz          Straße von Hormuz   870 m/px  (2°×2°)      \033[36m║\033[0m\n"
                    "\033[36m║\033[0m  sar hormuz zoom     Hormuz-Zoom          65 m/px  (0.15°)       \033[36m║\033[0m\n"
                    "\033[36m║\033[0m  sar hormuz fine     Hormuz-Fein          22 m/px  (0.05°)       \033[36m║\033[0m\n"
                    "\033[36m║\033[0m  sar suez [zoom|fine]  Suez-Kanal                                \033[36m║\033[0m\n"
                    "\033[36m║\033[0m  sar taiwan [zoom|fine] Taiwan-Strait                            \033[36m║\033[0m\n"
                    "\033[36m║\033[0m  sar ostsee [zoom|fine] Ostsee                                   \033[36m║\033[0m\n"
                    "\033[36m║\033[0m  sar nordsee          Nordsee                                    \033[36m║\033[0m\n"
                    "\033[36m╠══ Freie GPS-Koordinate ════════════════════════════════════════╣\033[0m\n"
                    "\033[36m║\033[0m  sar zoom:26.45,56.25        0.1°-Box um diese Position          \033[36m║\033[0m\n"
                    "\033[36m║\033[0m  sar zoom:26.45,56.25,0.05   0.05°-Box (22 m/px)                 \033[36m║\033[0m\n"
                    "\033[36m╠══ Auflösungsstufen ════════════════════════════════════════════╣\033[0m\n"
                    "\033[36m║\033[0m  (kein Suffix)  870 m/px → RCS-Klasse, kein Schiffstyp möglich  \033[36m║\033[0m\n"
                    "\033[36m║\033[0m  zoom           65 m/px  → Typ-Klasse (Frachter/Kriegsschiff)   \033[36m║\033[0m\n"
                    "\033[36m║\033[0m  fine           22 m/px  → Schiffsklasse + DB-Abgleich           \033[36m║\033[0m\n"
                    "\033[36m╚════════════════════════════════════════════════════════════════╝\033[0m\n",
                    flush=True
                )
                continue

            # Region zusammenbauen: "hormuz" + "-zoom" → "hormuz-zoom"
            # Aber: "zoom:lat,lon" bleibt unverändert
            if _sar_region_raw.startswith("zoom:"):
                _sar_region = _sar_region_raw   # GPS-Koordinate, kein Suffix anhängen
            else:
                _sar_region = _sar_region_raw + _sar_suffix

            # ── SAR ausführen ─────────────────────────────────────────────────
            try:
                from nexus_sar import detect_ships, _region_bbox  # type: ignore
            except ImportError as _sie:
                nexus_print(f"NEXUS: nexus_sar.py nicht gefunden oder Fehler: {_sie}\n")
                continue

            # Auflösung berechnen für Anzeige
            try:
                _bbox = _region_bbox(_sar_region)
                _span = max(_bbox[2] - _bbox[0], _bbox[3] - _bbox[1])
                _mpp  = round(_span * 111_320 / 256)
            except Exception:
                _bbox = None
                _mpp  = 0

            _sar_level_label = (
                f"Fein ({_mpp} m/px)" if _sar_suffix == "-fine" else
                f"Zoom ({_mpp} m/px)" if _sar_suffix == "-zoom" else
                f"Übersicht ({_mpp} m/px)"
            )

            print(
                f"\n\033[36m╔══ NEXUS SAR  ·  {_sar_region.upper()}  ·  {_sar_level_label} ══\033[0m",
                flush=True
            )
            if _mpp > 200:
                print(
                    "\033[33m║  Hinweis: Bei >200 m/px sind Schiffe sub-pixel.\033[0m\n"
                    "\033[33m║  Typ-Klassifikation nicht möglich – nur RCS-Helligkeit.\033[0m\n"
                    "\033[33m║  Für Schiffstypen:  sar {} zoom  oder  sar {} fine\033[0m".format(
                        _sar_region_raw, _sar_region_raw),
                    flush=True
                )
            print("\033[36m╚" + "═" * 60 + "\033[0m", flush=True)

            with _Spinner(f"NEXUS scannt {_sar_region} via Sentinel-1"):
                try:
                    _sar_result = detect_ships(_sar_region, max_ships=30)
                except Exception as _se:
                    _sar_result = None
                    _sar_err    = str(_se)

            if _sar_result is None:
                nexus_print(f"NEXUS: SAR-Fehler: {_sar_err}\n")
                continue

            # ── Ergebnis formatieren ──────────────────────────────────────────
            ships = _sar_result.ships
            desc  = getattr(_sar_result, "description", "")

            if not ships:
                print(
                    f"\n  Keine Schiffe detektiert ({desc})\n"
                    f"  → Falls unerwartet: andere Auflösung oder Region versuchen.\n",
                    flush=True
                )
            else:
                print(f"\n  \033[92m{len(ships)} Schiff/e detektiert\033[0m  ·  {desc}\n",
                      flush=True)

                for _si, _sh in enumerate(ships, 1):
                    _cat  = _sh.get("category", "?")
                    _sub  = _sh.get("subcategory", "")
                    _lat  = _sh.get("lat", 0)
                    _lon  = _sh.get("lon", 0)
                    _conf = _sh.get("confidence", 0)
                    _len  = _sh.get("length_m", 0)
                    _wid  = _sh.get("width_m", 0)
                    _rcs  = _sh.get("rcs_class", "")
                    _br   = _sh.get("brightness", 0)
                    _poss = _sh.get("possible_classes", [])

                    # Konfidenz-Farbe
                    _conf_color = (
                        "\033[92m" if _conf >= 0.7 else
                        "\033[93m" if _conf >= 0.5 else
                        "\033[91m"
                    )

                    print(
                        f"  \033[36m┌─ Ziel #{_si}\033[0m  "
                        f"{_lat:.3f}°N  {_lon:.3f}°E\n"
                        f"  \033[36m│\033[0m  Typ:       \033[97m{_cat}\033[0m  /  {_sub}\n"
                        f"  \033[36m│\033[0m  Maße:      {_len:.0f}m × {_wid:.0f}m  ·  "
                        f"RCS {_rcs}  ·  Helligkeit {_br:.0f}/255\n"
                        f"  \033[36m│\033[0m  Konfidenz: {_conf_color}{_conf:.0%}\033[0m",
                        flush=True
                    )

                    if _poss and _mpp <= 200:
                        print(f"  \033[36m│\033[0m  DB-Abgleich:", flush=True)
                        for _ph in _poss[:3]:
                            _bar = "█" * int(_ph["match"] * 10)
                            _col = "\033[92m" if _ph["match"] >= 0.8 else \
                                   "\033[93m" if _ph["match"] >= 0.6 else "\033[90m"
                            print(
                                f"  \033[36m│\033[0m    {_col}{_bar:<10}\033[0m "
                                f"{_ph['match']:.0%}  {_ph['class']:<38}  "
                                f"\033[90m{_ph['note']}\033[0m",
                                flush=True
                            )
                    elif _mpp > 200 and _poss:
                        # Bei grober Auflösung: kein DB-Abgleich sinnvoll
                        print(
                            f"  \033[36m│\033[0m  \033[33m"
                            f"Typ-Bestimmung: 'sar {_sar_region_raw} zoom' für Details\033[0m",
                            flush=True
                        )

                    print(f"  \033[36m└{'─'*52}\033[0m", flush=True)

            # Tipp für nächste Auflösungsstufe
            if _sar_suffix == "" and ships:
                print(
                    f"\n  \033[33m💡 Tipp: 'sar {_sar_region_raw} zoom' für Schiffstypen  "
                    f"(65 m/px)\033[0m\n"
                    f"  \033[33m         'sar {_sar_region_raw} fine' für Klassen-DB-Abgleich  "
                    f"(22 m/px)\033[0m\n",
                    flush=True
                )
            elif _sar_suffix == "-zoom" and ships:
                print(
                    f"\n  \033[33m💡 Tipp: 'sar {_sar_region_raw} fine' für DB-Abgleich  "
                    f"(22 m/px)\033[0m\n",
                    flush=True
                )

            # ── SAR-Learner: AIS-Kreuzvalidierung im Hintergrund ─────────────
            try:
                from nexus_sar_learner import (               # type: ignore
                    run_learning_cycle, classify_dark_ship,
                    get_stats as _sar_stats,
                )
                _learn_sum = run_learning_cycle(_sar_region)
                if _learn_sum.get("matches", 0) > 0:
                    print(
                        f"  \033[32m🧠 SAR-Learner: {_learn_sum['matches']} neue AIS-SAR-Paare gespeichert "
                        f"(Gesamt: {_learn_sum['total_examples']})\033[0m",
                        flush=True
                    )
                    if _learn_sum.get("retrained"):
                        print(
                            f"  \033[32m   Modell neu trainiert – CV-Accuracy: "
                            f"{_learn_sum['accuracy']:.1%}\033[0m",
                            flush=True
                        )
                else:
                    _st = _sar_stats()
                    if _st["examples_needed"] > 0:
                        print(
                            f"  \033[90m🧠 SAR-Learner: {_st['total_examples']}/{50} Paare gesammelt "
                            f"– noch {_st['examples_needed']} bis erstes Training\033[0m",
                            flush=True
                        )
                # Lernmodell-Einschätzung für dunkle Schiffe anzeigen
                if ships and _sar_stats().get("model_ready"):
                    print(f"  \033[90m   Gelernte 2. Meinung:\033[0m", flush=True)
                    for _sh in ships[:5]:
                        _pred = classify_dark_ship(
                            size_px      = _sh.get("size_px", 1),
                            brightness   = _sh.get("brightness", 128),
                            aspect_ratio = _sh.get("aspect_ratio", 1.0),
                            elongation   = _sh.get("elongation", 0.0),
                            compactness  = _sh.get("compactness", 0.5),
                        )
                        if _pred:
                            print(
                                f"  \033[90m   Ziel {ships.index(_sh)+1}: "
                                f"[Learner] {_pred['ship_type']} "
                                f"({_pred['confidence']:.0%})\033[0m",
                                flush=True
                            )
            except ImportError:
                pass  # nexus_sar_learner optional
            except Exception as _sle:
                log.debug(f"SAR-Learner Fehler: {_sle}")

            print(_c(getattr(config, "COLOR_DEBUG", "\033[32m"), "─" * 56 + " ✓"),
                  flush=True)
            print(flush=True)
            continue

        # ── Gesundheits-Frühwarnung: health [region] ────────────────────────
        _h_lo = user_input.lower().strip()
        if _h_lo == "health" or _h_lo.startswith("health ") or _h_lo == "seuche" or _h_lo.startswith("seuche "):
            _h_region = user_input.strip().split(None, 1)[1] if " " in user_input.strip() else None
            try:
                from nexus_health import get_health_alerts, format_health_terminal  # type: ignore
                with _Spinner("NEXUS: WHO/ProMED Feeds abrufen"):
                    _h_alerts = get_health_alerts(region=_h_region, max_age_days=14)
                print(format_health_terminal(_h_alerts), flush=True)
            except ImportError as _hie:
                nexus_print(f"NEXUS: nexus_health.py nicht gefunden: {_hie}\n")
            continue

        # ── Sanktionen: sanktionen [name/MMSI/IMO] ──────────────────────────
        _s_lo = user_input.lower().strip()
        if _s_lo.startswith("sanktion") or _s_lo.startswith("ofac ") or _s_lo.startswith("sdn "):
            _s_query = user_input.strip().split(None, 1)[1] if " " in user_input.strip() else ""
            try:
                from nexus_sanctions import check_vessel, check_entity, get_stats as _san_stats, refresh_all as _san_refresh  # type: ignore
                if not _s_query or _s_query.lower() in ("status", "liste", "laden"):
                    with _Spinner("NEXUS: Sanktionslisten laden/prüfen"):
                        _san_refresh()
                        _ss = _san_stats()
                    print(
                        f"\n\033[31m╔══ Sanktionslisten-Status ══════════════════════╗\033[0m\n"
                        f"  Gesamt: {_ss['total']:,} Einträge ({_ss['vessels']:,} Schiffe)\n"
                        + "".join(f"  {k}: {v:,}\n" for k, v in _ss["by_source"].items())
                        + "\033[31m╚════════════════════════════════════════════════╝\033[0m\n",
                        flush=True
                    )
                else:
                    with _Spinner(f"NEXUS: Abgleich '{_s_query}'"):
                        _match = check_vessel(name=_s_query) or check_entity(_s_query)
                    if _match:
                        print(
                            f"\n\033[91m⚖️  SANKTIONSTREFFER: '{_s_query}'\033[0m\n"
                            f"  Liste:     {_match['source']}\n"
                            f"  Eintrag:   {_match['matched_name']}\n"
                            f"  Ähnlichkeit: {_match['similarity']:.0%}\n"
                            f"  Grund:     {_match.get('reason','?')[:80]}\n"
                            f"  Gelistet:  {_match.get('listed_on','?')}\n",
                            flush=True
                        )
                    else:
                        print(f"\n\033[32m✓ '{_s_query}' – kein Sanktionstreffer\033[0m\n", flush=True)
            except ImportError as _sie2:
                nexus_print(f"NEXUS: nexus_sanctions.py nicht gefunden: {_sie2}\n")
            continue

        # ── BGP-Monitor: bgp [land] ──────────────────────────────────────────
        _b_lo = user_input.lower().strip()
        if _b_lo == "bgp" or _b_lo.startswith("bgp ") or _b_lo == "internet" or _b_lo.startswith("internet "):
            _b_regions = user_input.strip().split()[1:] or None
            try:
                from nexus_bgp import get_bgp_summary, format_bgp_terminal  # type: ignore
                with _Spinner("NEXUS: BGP-Routing prüfen"):
                    _bgp_sum = get_bgp_summary(_b_regions)
                print(format_bgp_terminal(_bgp_sum), flush=True)
            except ImportError as _bie:
                nexus_print(f"NEXUS: nexus_bgp.py nicht gefunden: {_bie}\n")
            continue

        # ── Nachtlichter: dunkel [region] ────────────────────────────────────
        _d_lo = user_input.lower().strip()
        if _d_lo == "dunkel" or _d_lo.startswith("dunkel ") or _d_lo.startswith("viirs ") or _d_lo == "viirs":
            _d_region = user_input.strip().split(None, 1)[1] if " " in user_input.strip() else "ukraine"
            try:
                from nexus_viirs import check_darkness  # type: ignore
                with _Spinner(f"NEXUS: VIIRS Nachtlichter für {_d_region}"):
                    _v_res = check_darkness(_d_region)
                if _v_res["status"] == "unknown_region":
                    nexus_print(f"NEXUS: Unbekannte Region '{_d_region}'. Bekannte Regionen: ukraine, gaza, syrien, hormuz, taiwan, kharkiv, jemen\n")
                elif _v_res["status"] in ("no_data", "new_baseline"):
                    nexus_print(f"NEXUS: Neue Region '{_d_region}' – Baseline wird aufgebaut ({_v_res.get('current_score','?')} Helligkeit gespeichert). Morgen ist Vergleich möglich.\n")
                elif _v_res["alert"]:
                    print(
                        f"\n\033[91m⬛ VERDUNKELUNG DETEKTIERT: {_d_region.title()}\033[0m\n"
                        f"  Aktuelle Helligkeit: {_v_res['current_score']:.1f}  "
                        f"(Baseline: {_v_res['baseline_score']:.1f})\n"
                        f"  Abfall:              {_v_res['drop_pct']:.0%}\n"
                        f"  Datum:               {_v_res['date_checked']}\n"
                        f"  → Möglicher Infrastrukturausfall oder Konflikt\n",
                        flush=True
                    )
                else:
                    print(
                        f"\n\033[32m✓ {_d_region.title()}: Normal  "
                        f"(Score {_v_res.get('current_score','?'):.1f}, "
                        f"Baseline {_v_res.get('baseline_score','?')})\033[0m\n",
                        flush=True
                    )
            except ImportError as _vie:
                nexus_print(f"NEXUS: nexus_viirs.py nicht gefunden: {_vie}\n")
            continue

        # ── Vertreibung: vertreibung [land] / unhcr [land] ───────────────────
        _u_lo = user_input.lower().strip()
        if _u_lo in ("vertreibung", "unhcr", "flüchtlinge", "idp") or \
           any(_u_lo.startswith(p + " ") for p in ("vertreibung", "unhcr", "flüchtlinge", "idp")):
            _u_country = user_input.strip().split(None, 1)[1] if " " in user_input.strip() else None
            try:
                from nexus_displacement import get_displacement_data, format_displacement_terminal  # type: ignore
                with _Spinner("NEXUS: UNHCR/IOM Vertreibungsdaten"):
                    _u_data = get_displacement_data(_u_country)
                print(format_displacement_terminal(_u_data), flush=True)
            except ImportError as _uie:
                nexus_print(f"NEXUS: nexus_displacement.py nicht gefunden: {_uie}\n")
            continue

        # ── Demo-Modus: demo / demo schnell / demo [modul] ──────────────────
        _demo_lo = user_input.lower().strip()
        if _demo_lo == "demo" or _demo_lo.startswith("demo "):
            _demo_arg = user_input.strip().split(None, 1)[1] if " " in user_input.strip() else ""
            try:
                from nexus_demo import run_demo  # type: ignore
                run_demo(_demo_arg)
            except ImportError as _die:
                nexus_print(f"NEXUS: nexus_demo.py nicht gefunden: {_die}\n")
            except KeyboardInterrupt:
                nexus_print("\nNEXUS: Demo unterbrochen.\n")
            continue

        # ── Lokal-OSINT: @ Adresse [Radius] ─────────────────────────────────
        # Syntax:  @ Berlin Mitte              → 25km Radius
        #          @ Hauptstraße 1, München    → 25km Radius
        #          @ Kölner Dom, 5km           → 5km Radius
        #          @ 48.137,11.576, 10         → GPS-Koordinaten, 10km
        _loc_lo = user_input.strip()
        if _loc_lo.startswith("@"):
            try:
                from nexus_local import parse_local_query, local_osint, format_local_terminal  # type: ignore
            except ImportError as _lie:
                nexus_print(f"NEXUS: nexus_local.py nicht gefunden: {_lie}\n")
                continue

            _loc_addr, _loc_radius = parse_local_query(_loc_lo)
            if not _loc_addr:
                print(
                    "\n\033[36m╔══ NEXUS Lokal-OSINT ════════════════════════════════╗\033[0m\n"
                    "\033[36m║\033[0m  Syntax:  @ Ort/Adresse [Radius km]                  \033[36m║\033[0m\n"
                    "\033[36m║\033[0m  Beispiele:                                           \033[36m║\033[0m\n"
                    "\033[36m║\033[0m    @ Berlin Mitte              (25km Radius)          \033[36m║\033[0m\n"
                    "\033[36m║\033[0m    @ Hauptstraße 1, München    (25km Radius)          \033[36m║\033[0m\n"
                    "\033[36m║\033[0m    @ Kölner Dom, 5km           (5km Radius)           \033[36m║\033[0m\n"
                    "\033[36m║\033[0m    @ 48.137,11.576, 10         (GPS, 10km)            \033[36m║\033[0m\n"
                    "\033[36m╚═════════════════════════════════════════════════════╝\033[0m\n",
                    flush=True
                )
                continue

            with _Spinner(f"NEXUS scannt {_loc_addr} (±{_loc_radius:.0f}km)"):
                try:
                    _loc_result = local_osint(_loc_addr, _loc_radius)
                except Exception as _loe:
                    _loc_result = None
                    _loc_err    = str(_loe)

            if _loc_result is None:
                nexus_print(f"NEXUS: Lokal-Fehler: {_loc_err}\n")
            else:
                print(format_local_terminal(_loc_result), flush=True)
            continue

        # ── Sprach-Trigger ───────────────────────────────────────────────────
        if ears is not None and user_input.lower() in voice_triggers:
            print("[NEXUS] Hoere zu ...", flush=True)
            heard = ears.listen()
            if not heard:
                print("[NEXUS] Nichts verstanden. Bitte erneut versuchen.", flush=True)
                continue
            print("Sie (Sprache): {}".format(heard), flush=True)
            user_text = heard
        else:
            user_text = user_input

        if is_exit_command(user_text) or user_text.lower() in {"exit", "quit"}:
            nexus_print("NEXUS: Verstanden. NEXUS faehrt herunter.")
            _speak_text_mode(voice, "Verstanden. NEXUS faehrt herunter.", timeout=10.0)
            return 0

        # ── Lagebild-Modus: alle Quellen kombinieren ────────────────────────
        if lagebild_mode:
            query = clean_query(user_text) or user_text
            # Lagebild-Präfixe rausschneiden: "lagebild sinzig" → "sinzig"
            for _lp in ("lagebild ", "lagebericht ", "lage ", "lagbild "):
                if query.lower().startswith(_lp):
                    query = query[len(_lp):].strip()
                    break
            sources_used = []   # Protokoll welche Quellen klappten

            # ── Geo-Erkennung: nur echte geografische Anfragen bekommen Flug-/Wetterdaten ──
            def _detect_geo_region(q: str):
                """Gibt den besten REGIONS-Schlüssel zurück, oder None wenn keine Geo-Relevanz."""
                q_low = q.lower()
                # 1. Direkte REGIONS-Namen
                try:
                    _, _, _REGS = _load_flights()
                    if _REGS:
                        direct = next((r for r in _REGS if r.lower() in q_low), None)
                        if direct:
                            return direct
                except Exception:
                    pass
                # 2. Erweiterte Keyword→Region-Tabelle
                _KW_MAP = {
                    # Naher Osten / Persischer Golf
                    "iran": "Persischer Golf", "irak": "Naher Osten",
                    "israel": "Naher Osten", "gaza": "Naher Osten",
                    "libanon": "Naher Osten", "syrien": "Naher Osten",
                    "jemen": "Rotes Meer", "houthi": "Rotes Meer",
                    "saudi": "Persischer Golf", "persisch": "Persischer Golf",
                    "hormuz": "Hormuz-Strasse", "oman": "Hormuz-Strasse",
                    # Ukraine / Russland
                    "ukraine": "Ukraine", "russland": "Schwarzes Meer",
                    "krim": "Schwarzes Meer", "donbas": "Ukraine",
                    "kiew": "Ukraine", "odessa": "Schwarzes Meer",
                    "bosporus": "Schwarzes Meer", "ostsee": "Ostsee",
                    # Asien
                    "taiwan": "Taiwan-Strasse", "china": "Taiwan-Strasse",
                    "korea": "Korea-Halbinsel", "nordkorea": "Korea-Halbinsel",
                    # Afrika / Sahel
                    "mali": "Sahel", "niger": "Sahel", "sahel": "Sahel",
                    "sudan": "Sahel",
                    # Suez / Rotes Meer
                    "suez": "Rotes Meer", "rotes meer": "Rotes Meer",
                    "aden": "Rotes Meer",
                }
                for kw, region in _KW_MAP.items():
                    if kw in q_low:
                        return region
                # 3. Kein Preset-Match → Query selbst zurückgeben (Geocoding-Fallback)
                # Nur überspringen wenn Query eindeutig nicht geografisch ist
                _NON_GEO = {"was", "wer", "wie", "warum", "erkläre", "definiere",
                            "was ist", "what is", "explain", "define", "timmy",
                            "wal", "whale", "rezept", "wetter heute"}
                if any(ng in q_low for ng in _NON_GEO):
                    return None
                # Mindestlänge: zu kurze Queries überspringen
                if len(q.strip()) < 4:
                    return None
                return q.strip()  # Raw query → Geocoding in nexus_flights/weather

            # ── Geo-Region ermitteln (vor Stadtfokus benötigt) ──────────────────
            best_region = _detect_geo_region(query)

            # ── Stadtfokus: Nominatim-Geocoding für präzise Stadtkoordinaten ──
            city_focus: dict | None = None
            # Stadt-Level Typen (Nominatim OSM-Klassen)
            _CITY_CLASSES = {"place", "amenity"}
            _CITY_TYPES   = {"city", "town", "village", "municipality",
                             "quarter", "suburb", "district", "borough"}
            _REGION_TYPES = {"country", "state", "county", "province",
                             "administrative", "region"}

            _geo_for_focus = best_region or query
            if _geo_for_focus:
                try:
                    import requests as _req_city
                    _nom_r = _req_city.get(
                        "https://nominatim.openstreetmap.org/search",
                        params={"q": _geo_for_focus, "format": "json", "limit": 1,
                                "accept-language": "de"},
                        headers={"User-Agent": "NEXUS-OSINT/3.0"},
                        timeout=5,
                    )
                    if _nom_r.status_code == 200:
                        _nom_data = _nom_r.json()
                        if _nom_data:
                            _city = _nom_data[0]
                            _osm_class = _city.get("class", "")
                            _osm_type  = _city.get("type", "")
                            _imp       = float(_city.get("importance", 0))
                            # Zoom je nach Ort-Typ bestimmen
                            if _osm_class in _CITY_CLASSES or _osm_type in _CITY_TYPES:
                                _zoom = 11   # Stadt/Ort: sehr nah
                            elif _osm_type in _REGION_TYPES and _imp > 0.8:
                                _zoom = 5    # Großes Land/Region: weit
                            elif _osm_type in _REGION_TYPES:
                                _zoom = 7    # Mittlere Region: mittel
                            else:
                                _zoom = 9    # Default

                            _clat = float(_city.get("lat", 0))
                            _clon = float(_city.get("lon", 0))
                            if _clat and _clon:
                                city_focus = {
                                    "name":       _city.get("display_name",
                                                            _geo_for_focus)[:60],
                                    "lat":        _clat,
                                    "lon":        _clon,
                                    "type":       _osm_type,
                                    "osm_class":  _osm_class,
                                    "importance": _imp,
                                    "zoom":       _zoom,
                                }
                                debug_print(
                                    "[LAGE] Stadtfokus: {} ({:.4f},{:.4f}) "
                                    "class={} type={} zoom={}".format(
                                        city_focus["name"], _clat, _clon,
                                        _osm_class, _osm_type, _zoom))
                except Exception:
                    pass

            with _Spinner("NEXUS erstellt Lagebild") as spin:
                # Jede Quelle einzeln abgesichert - ein Fehler stoppt nicht den Rest

                # 1. Flugdaten – immer versuchen (Geocoding-Fallback in nexus_flights.py)
                flight_data = None
                flight_context = ""
                try:
                    spin.update("NEXUS: Flugdaten abrufen ({})".format(best_region or query))
                    if best_region:
                        _, get_flights_fn, _ = _load_flights()
                        if get_flights_fn:
                            flight_data = get_flights_fn(best_region)
                            if flight_data and "error" not in flight_data:
                                flight_context = flight_data.get("summary", "")
                                sources_used.append("Flugdaten ({})".format(
                                    flight_data.get("region", best_region)))
                            elif flight_data and "error" in flight_data:
                                debug_print("[LAGE] Flugdaten: {}".format(flight_data["error"]))
                    else:
                        debug_print("[LAGE] Kein Geo-Kontext – Flugdaten übersprungen")
                except Exception as _e:
                    debug_print("[LAGE] Flugdaten-Fehler: {}".format(_e))

                # 2. RSS-News (feedparser optional)
                articles = []
                rss_context = ""
                try:
                    spin.update("NEXUS: Nachrichten aggregieren")
                    _, fetch_news_fn = _load_rss()
                    if fetch_news_fn:
                        articles = fetch_news_fn(fast=False, keyword_filter=query[:30])
                        if not articles:
                            articles = fetch_news_fn(fast=True)
                        if articles:
                            sources_used.append("RSS ({} Artikel)".format(len(articles)))
                except Exception as _e:
                    debug_print("[LAGE] RSS-Fehler (feedparser installiert?): {}".format(_e))

                # 3. Wetterdaten – immer versuchen (Geocoding-Fallback in nexus_weather.py)
                weather_data = None
                weather_context = ""
                _geo_query = best_region or query
                try:
                    spin.update("NEXUS: Wetterdaten ({})".format(_geo_query[:20]))
                    _, weather_report_fn = _load_weather()
                    weather_llm_fn, _ = _load_weather()
                    if weather_report_fn:
                        weather_data = weather_report_fn(_geo_query)
                        if weather_data and "error" not in weather_data:
                            weather_context = weather_llm_fn(_geo_query) if weather_llm_fn else ""
                            sources_used.append("Wetter ({})".format(
                                weather_data.get("location", _geo_query)))
                        else:
                            debug_print("[LAGE] Wetter: {}".format(
                                weather_data.get("error","?") if weather_data else "keine Daten"))
                except Exception as _e:
                    debug_print("[LAGE] Wetter-Fehler: {}".format(_e))

                # 4. Maritime Lage – immer versuchen (DDG-Fallback für beliebige Regionen)
                maritime_data = None
                maritime_context = ""
                try:
                    spin.update("NEXUS: Maritime Lage ({})".format(_geo_query[:20]))
                    _, get_maritime_fn = _load_maritime()
                    maritime_llm_fn, _ = _load_maritime()
                    if get_maritime_fn:
                        maritime_data = get_maritime_fn(_geo_query)
                        if maritime_data and "error" not in maritime_data:
                            maritime_context = maritime_llm_fn(_geo_query) if maritime_llm_fn else ""
                            sources_used.append("Maritime ({})".format(
                                maritime_data.get("region", _geo_query)))
                except Exception as _e:
                    debug_print("[LAGE] Maritime-Fehler: {}".format(_e))

                # 5. GDELT-Artikel (geocodierte Weltnachrichten)
                gdelt_ctx = ""
                try:
                    spin.update("NEXUS: GDELT-Datenbank abfragen")
                    from nexus_gdelt import fetch_gdelt_articles  # type: ignore
                    gdelt_arts = fetch_gdelt_articles(query, hours=48, max_records=15)
                    if gdelt_arts:
                        # Mit RSS-Artikeln zusammenführen (Duplikate durch Titel filtern)
                        existing_titles = {a.get("title","") for a in articles}
                        for ga in gdelt_arts:
                            if ga.get("title","") not in existing_titles:
                                articles.append(ga)
                                existing_titles.add(ga["title"])
                        sources_used.append("GDELT ({})".format(len(gdelt_arts)))
                except Exception as _e:
                    debug_print("[LAGE] GDELT-Fehler: {}".format(_e))

                # 5b. Telegram OSINT-Kanäle
                try:
                    spin.update("NEXUS: Telegram-Kanäle durchsuchen")
                    from nexus_telegram import fetch_osint_channels as _tg_fetch  # type: ignore
                    tg_arts = _tg_fetch(keyword_filter=query, limit_per_channel=8,
                                        max_channels=5)
                    if tg_arts:
                        existing_titles = {a.get("title", "") for a in articles}
                        added = 0
                        for ta in tg_arts:
                            if ta.get("title", "") not in existing_titles:
                                articles.append(ta)
                                existing_titles.add(ta["title"])
                                added += 1
                        if added:
                            sources_used.append("Telegram ({} Posts)".format(added))
                except Exception as _e:
                    debug_print("[LAGE] Telegram-Fehler: {}".format(_e))

                # 5c. Reddit OSINT-Subreddits
                try:
                    spin.update("NEXUS: Reddit durchsuchen")
                    from nexus_reddit import fetch_osint_reddit as _reddit_fetch  # type: ignore
                    reddit_arts = _reddit_fetch(keyword_filter=query, limit_per_sub=15,
                                                max_subs=4)
                    if reddit_arts:
                        existing_titles = {a.get("title", "") for a in articles}
                        added = 0
                        for ra in reddit_arts:
                            if ra.get("title", "") not in existing_titles:
                                articles.append(ra)
                                existing_titles.add(ra["title"])
                                added += 1
                        if added:
                            sources_used.append("Reddit ({} Posts)".format(added))
                except Exception as _e:
                    debug_print("[LAGE] Reddit-Fehler: {}".format(_e))

                # 5d. Social Media (Bluesky, Mastodon, VK, Wikipedia Recent Changes)
                try:
                    spin.update("NEXUS: Social Media durchsuchen")
                    from nexus_social import fetch_social_media  # type: ignore
                    social_arts = fetch_social_media(query, limit_per_platform=8)
                    if social_arts:
                        existing_titles = {a.get("title", "") for a in articles}
                        added = 0
                        for sa in social_arts:
                            if sa.get("title", "") not in existing_titles:
                                articles.append(sa)
                                existing_titles.add(sa["title"])
                                added += 1
                        if added:
                            sources_used.append("Social ({} Posts)".format(added))
                except Exception as _e:
                    debug_print("[LAGE] Social-Fehler: {}".format(_e))

                # 5e. ACLED Konfliktdaten mit GPS-Koordinaten
                acled_arts: list = []
                acled_ctx = ""
                try:
                    spin.update("NEXUS: ACLED Konfliktereignisse abrufen")
                    from nexus_acled import fetch_acled_events, acled_for_llm  # type: ignore
                    _acled_region = best_region or query
                    acled_arts = fetch_acled_events(_acled_region, days=7, limit=30)
                    if acled_arts:
                        # Mit Artikel-Liste zusammenführen (für Karte + Korrelation)
                        existing_titles = {a.get("title", "") for a in articles}
                        added = 0
                        for ae in acled_arts:
                            if ae.get("title", "") not in existing_titles:
                                articles.append(ae)
                                existing_titles.add(ae["title"])
                                added += 1
                        acled_ctx = acled_for_llm(_acled_region, days=7)
                        sources_used.append("ACLED ({} Ereignisse)".format(len(acled_arts)))
                        debug_print("[LAGE] ACLED: {} Konfliktereignisse geladen".format(
                            len(acled_arts)))
                except Exception as _e:
                    debug_print("[LAGE] ACLED-Fehler: {}".format(_e))

                # 6. Web-Analyse (immer verfuegbar)
                web_ctx = ""
                try:
                    spin.update("NEXUS: Webrecherche")
                    web_ctx = multi_angle_search(query)
                    if web_ctx:
                        sources_used.append("Websuche")
                except Exception as _e:
                    debug_print("[LAGE] Websuche-Fehler: {}".format(_e))

                # 7. Erdbeben (USGS, kostenlos)
                seismic_ctx = ""
                try:
                    spin.update("NEXUS: USGS Erdbeben-Daten")
                    from nexus_seismic import seismic_summary  # type: ignore
                    seismic_ctx = seismic_summary(_geo_query, hours=48)
                    if seismic_ctx and "Keine" not in seismic_ctx:
                        sources_used.append("USGS Seismik")
                        full_context_extra = getattr(locals(), "full_context_extra", "") + "\n\n" + seismic_ctx
                except Exception as _e:
                    debug_print("[LAGE] Seismik-Fehler: {}".format(_e))

                # 8. NOTAMs
                notam_ctx = ""
                try:
                    spin.update("NEXUS: NOTAM Luftsperren prüfen")
                    from nexus_notam import notam_summary  # type: ignore
                    notam_ctx = notam_summary(_geo_query)
                    if notam_ctx and "Keine" not in notam_ctx:
                        sources_used.append("NOTAM")
                except Exception as _e:
                    debug_print("[LAGE] NOTAM-Fehler: {}".format(_e))

                # 8b. Sentinel-2 Satellitenszene (Copernicus)
                try:
                    spin.update("NEXUS: Copernicus Satellitenszene prüfen")
                    from nexus_sentinel import sentinel_summary as _sent_sum  # type: ignore
                    from nexus_flights import REGIONS as _SENT_REGS  # type: ignore
                    # Koordinaten: city_focus > REGIONS-Zentrum > Fallback
                    if city_focus and city_focus.get("lat"):
                        _slat, _slon = city_focus["lat"], city_focus["lon"]
                    elif best_region and best_region in _SENT_REGS:
                        _b = _SENT_REGS[best_region]
                        _slat = (_b[0] + _b[2]) / 2.0
                        _slon = (_b[1] + _b[3]) / 2.0
                    else:
                        _slat, _slon = 0.0, 0.0
                    if _slat or _slon:
                        try:
                            import config as _sc  # type: ignore
                            _cid  = getattr(_sc, "COPERNICUS_CLIENT_ID",     "")
                            _csec = getattr(_sc, "COPERNICUS_CLIENT_SECRET", "")
                        except ImportError:
                            _cid, _csec = "", ""
                        _sent_ctx = _sent_sum(
                            region=best_region or query[:30],
                            lat=_slat, lon=_slon,
                            client_id=_cid, client_secret=_csec,
                        )
                        if _sent_ctx:
                            _sentinel_context = _sent_ctx
                            sources_used.append("Sentinel-2")
                except Exception as _se:
                    debug_print("[NEXUS] Sentinel-Fehler: {}".format(_se))

                # 9. Wirtschaftsindikatoren (Stufe 3)
                econ_ctx = ""
                try:
                    spin.update("NEXUS: Wirtschaftsindikatoren abrufen")
                    from nexus_economics import economics_for_llm, economics_summary_line  # type: ignore
                    econ_ctx = economics_for_llm()
                    if econ_ctx:
                        sources_used.append("Wirtschaft")
                        # Kurze Zusammenfassung im Terminal
                        debug_print("[LAGE] " + economics_summary_line())
                except Exception as _e:
                    debug_print("[LAGE] Wirtschaft-Fehler: {}".format(_e))

                # 10. Wikipedia-Hintergrundkontext (Stufe 3)
                wiki_ctx = ""
                try:
                    spin.update("NEXUS: Wikipedia-Kontext laden")
                    from nexus_wiki import wiki_inject_for_query  # type: ignore
                    wiki_ctx = wiki_inject_for_query(query)
                    if wiki_ctx:
                        sources_used.append("Wikipedia")
                except Exception as _e:
                    debug_print("[LAGE] Wiki-Fehler: {}".format(_e))

                # Auto-Übersetzung (Russisch/Arabisch/Englisch → Deutsch)
                try:
                    spin.update("NEXUS: Artikel übersetzen")
                    from nexus_translate import enrich_articles_with_translation  # type: ignore
                    if articles:
                        articles = enrich_articles_with_translation(articles)
                        translated = sum(1 for a in articles if a.get("translated"))
                        if translated:
                            debug_print("[LAGE] {} Artikel übersetzt".format(translated))
                except Exception as _e:
                    debug_print("[LAGE] Übersetzungs-Fehler (kein Problem): {}".format(_e))

                # Credibility-Bewertung der Artikel
                credibility_ctx = ""
                try:
                    spin.update("NEXUS: Quellen bewerten")
                    from nexus_credibility import enrich_articles, credibility_context  # type: ignore
                    if articles:
                        articles = enrich_articles(articles)
                        credibility_ctx = credibility_context(articles)
                except Exception as _e:
                    debug_print("[LAGE] Credibility-Fehler: {}".format(_e))

                # NER – Named Entity Recognition (Personen, Orte, Waffen, Akteure)
                ner_ctx = ""
                try:
                    spin.update("NEXUS: Entitäten extrahieren (NER)")
                    from nexus_ner import enrich_articles_with_ner, ner_context_for_llm  # type: ignore
                    if articles:
                        articles = enrich_articles_with_ner(articles)
                        ner_ctx = ner_context_for_llm(articles)
                        if ner_ctx:
                            sources_used.append("NER")
                            debug_print("[LAGE] NER: Entitäten aus {} Artikeln extrahiert".format(
                                len(articles)))
                except Exception as _e:
                    debug_print("[LAGE] NER-Fehler: {}".format(_e))

                # Korrelations-Analyse
                correlation_ctx = ""
                try:
                    spin.update("NEXUS: Ereignisse korrelieren")
                    from nexus_correlate import correlate_events, correlation_text_summary  # type: ignore
                    geo_arts  = [a for a in (articles or []) if a.get("lat") and a.get("lon")]
                    susp_ac   = (flight_data.get("suspicious", []) if flight_data else [])
                    # Toast-Alerts für auffällige Flugzeuge
                    if susp_ac:
                        try:
                            from nexus_alert import alert_suspicious_flight as _aflight  # type: ignore
                            for ac in susp_ac[:3]:
                                _aflight(
                                    callsign=ac.get("callsign", "?"),
                                    reason=(ac.get("suspicious") or ac.get("osint") or "Auffälliges Muster")[:80],
                                    region=(best_region or "")[:30],
                                )
                        except Exception:
                            pass
                    alerts    = correlate_events(articles=geo_arts, aircraft=susp_ac)
                    if alerts:
                        correlation_ctx = correlation_text_summary(alerts)
                        sources_used.append("Korrelation ({} Cluster)".format(len(alerts)))
                        # Terminal-Hinweis + Toast-Alert bei hoher Konfidenz
                        high = [a for a in alerts if a["confidence"] == "HOCH"]
                        if high:
                            nexus_print("\n⚡ KORRELATION ERKANNT: {} Cluster mit hoher Konfidenz!".format(len(high)))
                            for h in high[:2]:
                                nexus_print("   {} – {} Quellen".format(h["title"], h["n_sources"]))
                            # Windows-Benachrichtigung
                            try:
                                from nexus_alert import alert_correlation as _acorr  # type: ignore
                                for h in high[:2]:
                                    _acorr(
                                        topic=h.get("topic_str", "Unbekannt")[:50],
                                        confidence="HOCH",
                                        n_sources=h.get("n_sources", 0),
                                        location=(best_region or "")[:30],
                                    )
                            except Exception:
                                pass
                except Exception as _e:
                    debug_print("[LAGE] Korrelation-Fehler: {}".format(_e))

                # Ereignisse in Speicher schreiben
                try:
                    from nexus_memory import (  # type: ignore
                        init_db, store_articles, store_flight_alerts, store_maritime_alerts
                    )
                    init_db()
                    if articles:
                        store_articles(query, articles[:20])
                    if flight_data and "suspicious" in flight_data:
                        store_flight_alerts(query, flight_data)
                    if maritime_data and "alerts" in maritime_data:
                        store_maritime_alerts(query, maritime_data)
                except Exception:
                    pass

                # Delta-Analyse – Weltmodell (Vergleich mit 7-Tage-Durchschnitt)
                delta_ctx = ""
                try:
                    spin.update("NEXUS: Delta-Analyse (Weltmodell)")
                    from nexus_delta import (  # type: ignore
                        save_snapshot as _save_snap,
                        compute_delta as _comp_delta,
                        delta_text_summary as _delta_txt,
                        delta_terminal_output as _delta_term,
                    )
                    # Erdbeben für Snapshot laden
                    _quakes_snap: list = []
                    try:
                        from nexus_seismic import get_earthquakes_for_region as _get_quakes  # type: ignore
                        _quakes_snap = _get_quakes(best_region or query) or []
                    except Exception:
                        pass
                    # Snapshot speichern
                    _snap_metrics = _save_snap(
                        region=(best_region or query)[:40],
                        articles=articles,
                        flight_data=flight_data,
                        maritime_data=maritime_data,
                        earthquakes=_quakes_snap,
                        acled_events=acled_arts,
                    )
                    # Delta berechnen
                    _delta_alerts = _comp_delta(best_region or query, _snap_metrics)
                    if _delta_alerts:
                        delta_ctx = _delta_txt(best_region or query, _delta_alerts)
                        sources_used.append("Delta ({} Alerts)".format(len(_delta_alerts)))
                        _delta_term(best_region or query, _delta_alerts)
                except Exception as _e:
                    debug_print("[LAGE] Delta-Fehler: {}".format(_e))

                # Pruefen ob ueberhaupt Quellen vorhanden
                if not flight_context and not articles and not web_ctx:
                    answer = (
                        "Lagebild konnte nicht erstellt werden - keine Quellen erreichbar.\n"
                        "Pruefe Internetverbindung und ob feedparser installiert ist:\n"
                        "  venv\\Scripts\\pip install feedparser Pillow"
                    )
                else:
                    # 6. Kontext zusammenbauen
                    spin.update("NEXUS: Analysiert {} Quellen".format(len(sources_used)))
                    full_context = "[LAGEBILD-QUELLEN: {}]\n".format(", ".join(sources_used))
                    # Stadtfokus-Kontext
                    if city_focus:
                        full_context += (
                            "\n[STADTFOKUS: {} | GPS: {:.4f},{:.4f}]\n"
                            "Analyse auf Stadtebene – Radius ca. 50 km.\n"
                        ).format(city_focus["name"], city_focus["lat"], city_focus["lon"])
                    if flight_context:
                        full_context += "\n\n" + flight_context
                    if weather_context:
                        full_context += "\n\n" + weather_context
                    if maritime_context:
                        full_context += "\n\n" + maritime_context
                    if locals().get("_sentinel_context"):
                        full_context += "\n\n" + _sentinel_context
                    # ── Alle gesammelten Artikel direkt formatieren ──────────────────────
                    # -- Dedup + Konfidenz-Scoring fur LLM formatieren --
                    # 1. nexus_dedup: gleiche Ereignisse aus mehreren Quellen clustern
                    # 2. nexus_confidence: pro Aussage BESTAETIGT/WAHRSCHEINLICH/etc.
                    if articles:
                        try:
                            from nexus_dedup import deduplicate as _dedup  # type: ignore
                            from nexus_confidence import score_articles as _score, confidence_for_llm as _conf_fmt  # type: ignore
                            _deduped = _dedup(articles)
                            _canonical = [a for a in _deduped if a.get("is_canonical", True)]
                            _scored = _score(_canonical)
                            _dedup_ctx = _conf_fmt(_scored, max_articles=50)
                            full_context += "\n\n" + _dedup_ctx
                        except Exception:
                            # Fallback: rohe Artikel-Liste
                            _arts_ctx_lines = [
                                "[NACHRICHTEN / POSTS / EREIGNISSE – {} Einträge]".format(
                                    min(len(articles), 50)
                                )
                            ]
                            _sorted_arts = sorted(
                                articles[:50],
                                key=lambda a: (
                                    -(a.get("credibility_score", 0.5) or 0.5),
                                    a.get("published", ""),
                                ),
                                reverse=False,
                            )
                            for _a in _sorted_arts:
                                _src  = _a.get("source", "?")
                                _tit  = (_a.get("title") or "–")[:130]
                                _pub  = _a.get("published", "")
                                _txt  = (_a.get("text") or _a.get("summary") or "")[:300].strip()
                                _cred = _a.get("credibility_label", "")
                                _lat  = _a.get("lat")
                                _lon  = _a.get("lon")
                                _line = f"• [{_src}] {_tit}"
                                if _pub:
                                    _line += f"  ({_pub})"
                                if _cred:
                                    _line += f"  [Quelle: {_cred}]"
                                if _lat and _lon:
                                    _line += f"  [GPS: {_lat:.3f},{_lon:.3f}]"
                                if _txt and _txt.strip()[:60] not in _tit:
                                    _line += f"\n  {_txt}"
                                _arts_ctx_lines.append(_line)
                            full_context += "\n\n" + "\n".join(_arts_ctx_lines)
                    if web_ctx:
                        full_context += "\n\n" + web_ctx
                    if correlation_ctx:
                        full_context += "\n\n" + correlation_ctx
                    if credibility_ctx:
                        full_context += "\n\n" + credibility_ctx
                    if econ_ctx:
                        full_context += "\n\n" + econ_ctx
                    if wiki_ctx:
                        full_context += "\n\n" + wiki_ctx
                    if acled_ctx:
                        full_context += "\n\n" + acled_ctx
                    if ner_ctx:
                        full_context += "\n\n" + ner_ctx
                    if delta_ctx:
                        full_context += "\n\n" + delta_ctx
                    # ── Bug-Fix: seismic_ctx + notam_ctx waren nie im LLM-Kontext ──────
                    if seismic_ctx and "Keine" not in seismic_ctx:
                        full_context += "\n\n" + seismic_ctx
                    if notam_ctx and "Keine" not in notam_ctx:
                        full_context += "\n\n" + notam_ctx
                    # ── Wissens-Graph: Persistente Entitaeten + Bewegungen ──────────────
                    try:
                        from nexus_knowledge import knowledge_for_llm as _know_llm, ingest_articles as _know_ingest  # type: ignore
                        if articles:
                            _know_ingest(articles)
                        _know_ctx = _know_llm()
                        if _know_ctx:
                            full_context += "\n\n" + _know_ctx
                            sources_used.append("Wissens-Graph")
                    except Exception as _ke:
                        debug_print("[NEXUS] Wissens-Graph-Fehler: {}".format(_ke))

                    # ── Netzwerk-Propagations-Analyse (T130) ─────────────────────────────
                    try:
                        spin.update("NEXUS: Netzwerk-Propagation analysieren")
                        from nexus_netprop import analyze_articles_propagation as _netprop, netprop_for_llm as _netprop_fmt  # type: ignore
                        if articles:
                            _prop_result = _netprop(articles)
                            _netprop_ctx = _netprop_fmt(_prop_result)
                            if _netprop_ctx:
                                full_context += "\n\n" + _netprop_ctx
                                _coord_alerts = _prop_result.get("coordination_alerts", [])
                                _state_amps   = _prop_result.get("state_amplification_events", [])
                                _np_label = "Netprop"
                                if _coord_alerts:
                                    _np_label += " ({}x Koordination)".format(len(_coord_alerts))
                                if _state_amps:
                                    _np_label += " +Staatl.Amp."
                                sources_used.append(_np_label)
                                # Terminal-Hinweis bei Koordinations-Alarm
                                if _coord_alerts:
                                    nexus_print("\n⚠️  PROPAGATIONS-ALARM: {} koordinierte Themen!".format(
                                        len(_coord_alerts)))
                                    for _ca in _coord_alerts[:2]:
                                        nexus_print("   [{}] {} — Score {:.0%}".format(
                                            _ca["verdict"], _ca["topic"][:50], _ca["score"]))
                    except Exception as _npe:
                        debug_print("[NEXUS] Netprop-Fehler: {}".format(_npe))

                    # ── Video-Analyse (T128) — Telegram Video-URLs ───────────────────────
                    try:
                        spin.update("NEXUS: Video-Inhalte analysieren")
                        from nexus_video import videos_for_llm as _vid_llm, analyze_video_url as _vid_url  # type: ignore
                        _video_analyses = []
                        for _art in (articles or [])[:20]:
                            _art_url = _art.get("url", "")
                            _art_src = _art.get("source", "")
                            # Nur Telegram-Posts mit Video-Indikatoren
                            if ("t.me" in _art_url or "telegram" in _art_src.lower()) and _art_url:
                                _vid_res = _vid_url(_art_url)
                                if _vid_res.get("analysis") and not _vid_res.get("error"):
                                    _video_analyses.append(_vid_res["analysis"])
                        if _video_analyses:
                            _video_ctx = _vid_llm(_video_analyses)
                            if _video_ctx:
                                full_context += "\n\n" + _video_ctx
                                sources_used.append("Video ({} Clips)".format(len(_video_analyses)))
                    except Exception as _ve:
                        debug_print("[NEXUS] Video-Fehler: {}".format(_ve))

                    # ── Bild-Metadaten OSINT (T129) — EXIF + Schatten + Terrain ─────────────
                    try:
                        spin.update("NEXUS: Bild-Metadaten analysieren")
                        from nexus_imgmeta import analyze_image_url as _img_url, imgmeta_for_llm as _img_fmt  # type: ignore
                        _img_analyses = []
                        for _art in (articles or [])[:25]:
                            _aurl = _art.get("image_url") or _art.get("url", "")
                            if _aurl and any(_aurl.lower().endswith(ext)
                                             for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
                                _ir = _img_url(_aurl)
                                if not _ir.get("error") and _ir.get("verdict"):
                                    _img_analyses.append(_ir)
                        if _img_analyses:
                            _img_ctx = _img_fmt(_img_analyses)
                            if _img_ctx:
                                full_context += "\n\n" + _img_ctx
                                _suspicious = sum(1 for _i in _img_analyses
                                                  if _i.get("verdict") in ("VERDÄCHTIG", "WAHRSCHEINLICH_GEFÄLSCHT"))
                                _im_label = "ImgMeta ({} Bilder)".format(len(_img_analyses))
                                if _suspicious:
                                    _im_label += " ⚠️ {}x Verdächtig".format(_suspicious)
                                sources_used.append(_im_label)
                                if _suspicious:
                                    nexus_print("\n⚠️  BILD-ALARM: {} verdächtige Bilder!".format(_suspicious))
                    except Exception as _ime:
                        debug_print("[NEXUS] ImgMeta-Fehler: {}".format(_ime))

                    # ── Timeline-Analyse (T141) — Automatische Ereignis-Chronologie ────────
                    try:
                        spin.update("NEXUS: Ereignis-Chronologie")
                        from nexus_timeline import timeline_for_llm as _tl_llm  # type: ignore
                        if articles:
                            _tl_ctx = _tl_llm(articles, max_events=20)
                            if _tl_ctx:
                                full_context += "\n\n" + _tl_ctx
                                sources_used.append("Timeline")
                    except Exception as _tle:
                        debug_print("[NEXUS] Timeline-Fehler: {}".format(_tle))

                    # ── WHOIS / Domain-Attribution (T141) ────────────────────────────────
                    try:
                        spin.update("NEXUS: Domain-Attribution analysieren")
                        from nexus_whois import analyze_article_sources as _whois_src, whois_for_llm as _whois_fmt  # type: ignore
                        if articles:
                            _whois_result = _whois_src(articles, max_domains=8)
                            _whois_ctx = _whois_fmt(_whois_result)
                            if _whois_ctx:
                                full_context += "\n\n" + _whois_ctx
                                _disinfo_count = len([
                                    d for d in _whois_result.get("domain_analyses", [])
                                    if d.get("risk_score", 0) >= 50
                                ])
                                _w_label = "WHOIS"
                                if _disinfo_count:
                                    _w_label += " ⚠️ {}x Disinfo-Verdacht".format(_disinfo_count)
                                sources_used.append(_w_label)
                                if _disinfo_count:
                                    nexus_print("\n⚠️  DISINFO-ALARM: {} Quellen mit hohem Risiko-Score!".format(
                                        _disinfo_count))
                    except Exception as _we:
                        debug_print("[NEXUS] WHOIS-Fehler: {}".format(_we))

                    # ── Dokument-Metadaten OSINT (T141) — PDFs/DOCX aus Artikeln ─────────
                    try:
                        spin.update("NEXUS: Dokument-Metadaten analysieren")
                        from nexus_docmeta import analyze_document_url as _doc_url, docmeta_for_llm as _doc_fmt  # type: ignore
                        _doc_analyses = []
                        for _art in (articles or [])[:30]:
                            _art_url = _art.get("url", "")
                            if _art_url and any(_art_url.lower().endswith(ext)
                                                for ext in (".pdf", ".docx", ".xlsx", ".pptx")):
                                _doc_res = _doc_url(_art_url)
                                if not _doc_res.get("error"):
                                    _doc_analyses.append(_doc_res)
                        if _doc_analyses:
                            _doc_ctx = _doc_fmt(_doc_analyses)
                            if _doc_ctx:
                                full_context += "\n\n" + _doc_ctx
                                sources_used.append("DocMeta ({} Docs)".format(len(_doc_analyses)))
                    except Exception as _dme:
                        debug_print("[NEXUS] DocMeta-Fehler: {}".format(_dme))

                    # ── T149: Fehlende Signal-Quellen für LLM-Kontext ────────────────────

                    # FIRMS — NASA Brände / Thermische Anomalien
                    try:
                        from nexus_firms import firms_summary as _firms_sum  # type: ignore
                        _firms_ctx = _firms_sum(best_region or query)
                        if _firms_ctx and "keine" not in _firms_ctx.lower()[:50]:
                            full_context += "\n\n" + _firms_ctx
                            sources_used.append("FIRMS-Brände")
                    except Exception as _ferr:
                        debug_print("[NEXUS] FIRMS-Fehler: {}".format(_ferr))

                    # Frontlinien-Zusammenfassung
                    try:
                        from nexus_frontline import frontline_summary as _fl_sum  # type: ignore
                        _fl_ctx = _fl_sum()
                        if _fl_ctx and len(_fl_ctx) > 20:
                            full_context += "\n\n" + _fl_ctx
                            sources_used.append("Frontlinie")
                    except Exception as _fle:
                        debug_print("[NEXUS] Frontlinie-Fehler: {}".format(_fle))

                    # GPS-Jamming (Elektronische Kriegsführung)
                    try:
                        from nexus_gpsjam import gpsjam_summary as _gpsjam_sum  # type: ignore
                        _gps_ctx = _gpsjam_sum(best_region or query)
                        if _gps_ctx and "keine" not in _gps_ctx.lower()[:50]:
                            full_context += "\n\n" + _gps_ctx
                            sources_used.append("GPS-Jamming")
                    except Exception as _gje:
                        debug_print("[NEXUS] GPSJam-Fehler: {}".format(_gje))

                    # Blitzortung / Artillerie-Flashes
                    try:
                        from nexus_lightning import lightning_summary as _light_sum  # type: ignore
                        _light_ctx = _light_sum(best_region or query)
                        if _light_ctx and "keine" not in _light_ctx.lower()[:50]:
                            full_context += "\n\n" + _light_ctx
                            sources_used.append("Blitzortung")
                    except Exception as _le:
                        debug_print("[NEXUS] Lightning-Fehler: {}".format(_le))

                    # NASA EONET — Naturereignisse
                    try:
                        from nexus_eonet import eonet_summary as _eonet_sum  # type: ignore
                        _eonet_ctx = _eonet_sum(best_region or query)
                        if _eonet_ctx and "keine" not in _eonet_ctx.lower()[:50]:
                            full_context += "\n\n" + _eonet_ctx
                            sources_used.append("EONET")
                    except Exception as _ee2:
                        debug_print("[NEXUS] EONET-Fehler: {}".format(_ee2))

                    # Strahlungs-Monitoring (EPA + IAEA)
                    try:
                        from nexus_radnet import radiation_for_map as _rad_pts, radiation_summary as _rad_sum  # type: ignore
                        _rad_points = _rad_pts(best_region or "Europa")
                        if _rad_points:
                            _rad_ctx = _rad_sum(_rad_points)
                            if _rad_ctx and len(_rad_ctx) > 20:
                                full_context += "\n\n" + _rad_ctx
                                sources_used.append("Radnet")
                    except Exception as _re2:
                        debug_print("[NEXUS] Radnet-Fehler: {}".format(_re2))

                    # Satelliten-Überflüge (n2yo)
                    try:
                        from nexus_satellite_timing import passes_summary as _sat_sum  # type: ignore
                        import config as _cfg_sat  # type: ignore
                        _sat_ctx = _sat_sum(best_region or query,
                                           api_key=getattr(_cfg_sat, "N2YO_API_KEY", ""))
                        if _sat_ctx and "keine" not in _sat_ctx.lower()[:50]:
                            full_context += "\n\n" + _sat_ctx
                            sources_used.append("Satelliten-Timing")
                    except Exception as _ste:
                        debug_print("[NEXUS] SatTiming-Fehler: {}".format(_ste))

                    # Schiffs-Tiefgang Delta (AIS Ladungsveränderung)
                    try:
                        from nexus_draught import draught_summary as _drau_sum  # type: ignore
                        _drau_ctx = _drau_sum(best_region or query)
                        if _drau_ctx and "keine" not in _drau_ctx.lower()[:50]:
                            full_context += "\n\n" + _drau_ctx
                            sources_used.append("Tiefgang-Delta")
                    except Exception as _dre2:
                        debug_print("[NEXUS] Draught-Fehler: {}".format(_dre2))

                    # AIS Schiffslagen (Kontext-Text)
                    try:
                        from nexus_ais import vessels_for_map as _ais_vessels  # type: ignore
                        _ais_data = _ais_vessels(best_region or query)
                        if _ais_data:
                            _ais_lines = ["AIS Schiffe ({} Meldungen):".format(len(_ais_data))]
                            for _v in _ais_data[:8]:
                                _vname = _v.get("name", "?") or str(_v.get("mmsi", "?"))
                                _vtype = _v.get("type", "")
                                _vspd  = float(_v.get("speed", 0) or 0)
                                _vstat = _v.get("status", "")
                                _ais_lines.append("  • {} ({}) — {:.1f} kn {}".format(
                                    _vname, _vtype, _vspd, _vstat).strip())
                            full_context += "\n\n" + "\n".join(_ais_lines)
                            sources_used.append("AIS ({} Schiffe)".format(len(_ais_data)))
                    except Exception as _aise:
                        debug_print("[NEXUS] AIS-Fehler: {}".format(_aise))

                    # Multi-Signal Fusion (Angriffs-Detektion)
                    try:
                        from nexus_fusion import fusion_summary as _fus_sum, fusion_for_map as _fus_map  # type: ignore
                        _fus_pipeline = {
                            "fires": [], "flights": flight_data or {},
                            "maritime": maritime_data or {},
                            "seismic": [], "acled": [],
                        }
                        _fus_threats = _fus_map(_fus_pipeline, best_region or query)
                        if _fus_threats:
                            _fus_ctx = _fus_sum(_fus_threats)
                            if _fus_ctx:
                                full_context += "\n\n" + _fus_ctx
                                sources_used.append("Fusion ({} Threats)".format(
                                    len(_fus_threats)))
                    except Exception as _fue:
                        debug_print("[NEXUS] Fusion-Fehler: {}".format(_fue))

                    # HUMINT — Koordinaten + Einheiten aus Artikel-Texten
                    try:
                        from nexus_humint import humint_for_map as _hum_map, humint_summary as _hum_sum  # type: ignore
                        _hum_hits = _hum_map(articles or [], region=best_region or query)
                        if _hum_hits:
                            _hum_ctx = _hum_sum(_hum_hits)
                            if _hum_ctx:
                                full_context += "\n\n" + _hum_ctx
                                sources_used.append("HUMINT ({} Hits)".format(len(_hum_hits)))
                    except Exception as _hume:
                        debug_print("[NEXUS] HUMINT-Fehler: {}".format(_hume))

                    # Bewegungs-Anomalien / Konvoi-Detektion
                    try:
                        from nexus_movement import get_traffic_anomalies as _mov_get, movement_summary as _mov_sum  # type: ignore
                        _mov_anomalies = _mov_get(best_region or query)
                        if _mov_anomalies:
                            _mov_ctx = _mov_sum(_mov_anomalies)
                            if _mov_ctx:
                                full_context += "\n\n" + _mov_ctx
                                sources_used.append("Bewegungs-Anomalien ({})".format(
                                    len(_mov_anomalies)))
                    except Exception as _mve:
                        debug_print("[NEXUS] Movement-Fehler: {}".format(_mve))

                    # WebSDR — HF-Radio-Aktivität
                    try:
                        from nexus_websdr import websdr_for_map as _sdr_map  # type: ignore
                        _sdr_data = _sdr_map(best_region or query)
                        if _sdr_data:
                            _sdr_lines = ["HF-Radio-Aktivität ({} Signale):".format(
                                len(_sdr_data))]
                            for _s in _sdr_data[:5]:
                                _sdr_lines.append("  • {} MHz — {} ({})".format(
                                    _s.get("freq", "?"), _s.get("label", "?"),
                                    _s.get("status", "?")))
                            full_context += "\n\n" + "\n".join(_sdr_lines)
                            sources_used.append("WebSDR")
                    except Exception as _sdre:
                        debug_print("[NEXUS] WebSDR-Fehler: {}".format(_sdre))

                    # HF-Maritime Radio (Kurzwellen-Schiffsfrequenzen)
                    try:
                        from nexus_hf_maritime import hf_activity_for_region as _hfm_fn  # type: ignore
                        _hfm = _hfm_fn(best_region or query)
                        if _hfm and _hfm.get("signals"):
                            _hfm_ctx = "HF-Maritime Aktivität: {} Signale, Priorität {}".format(
                                len(_hfm["signals"]), _hfm.get("priority", "?"))
                            full_context += "\n\n" + _hfm_ctx
                            sources_used.append("HF-Maritime")
                    except Exception as _hfme:
                        debug_print("[NEXUS] HFMaritime-Fehler: {}".format(_hfme))

                    # Iridium/Inmarsat Satelliten-Kommunikations-Monitoring
                    try:
                        from nexus_iridium import satellite_comms_for_region as _irid_fn  # type: ignore
                        _irid = _irid_fn(best_region or query)
                        if _irid and _irid.get("total_pings", 0) > 0:
                            _irid_ctx = "Satelliten-Komms (Iridium/Inmarsat): {} Aktivierungen in der Region".format(
                                _irid.get("total_pings", 0))
                            full_context += "\n\n" + _irid_ctx
                            sources_used.append("Iridium/Inmarsat")
                    except Exception as _iride:
                        debug_print("[NEXUS] Iridium-Fehler: {}".format(_iride))

                    # Maritime Anomalie-Detektion (AIS → Chokepoints + STS)
                    try:
                        from nexus_ais import vessels_for_map as _mav_fn  # type: ignore
                        from nexus_maritime_anomaly import analyse_vessels as _maa_fn, anomaly_text_summary as _maa_txt  # type: ignore
                        _mav = _mav_fn(best_region or query)
                        if _mav:
                            _maa_result = _maa_fn(_mav)
                            _maa_ctx = _maa_txt(_maa_result)
                            if _maa_ctx and len(_maa_ctx) > 20:
                                full_context += "\n\n" + _maa_ctx
                                sources_used.append("Maritime-Anomalie")
                    except Exception as _mae:
                        debug_print("[NEXUS] MaritimeAnomalie-Fehler: {}".format(_mae))

                    # ── SitRep-Kontext (T147) — BLUF + Threat Assessment ──────────────────
                    try:
                        from nexus_sitrep import sitrep_for_llm as _sr_llm  # type: ignore
                        _sr_pipeline = {
                            "articles":       articles or [],
                            "escalation":     _pipeline_esc if "_pipeline_esc" in dir() else {},
                            "flights":        flight_data or {},
                            "maritime":       maritime_data or {},
                            "acled":          [], "netprop": {}, "whois": {},
                            "seismic":        [], "firms":   [], "lightning": [],
                            "gpsjam":         [], "bgp_anomalies": [],
                            "displacement":   [], "health_alerts": [],
                            "economics":      {}, "sanctions_hits": [],
                            "timeline_context": "", "prediction": {},
                        }
                        _sr_ctx = _sr_llm(_sr_pipeline, query)
                        if _sr_ctx:
                            full_context = _sr_ctx + "\n\n" + full_context
                            sources_used.append("SitRep")
                    except Exception as _sre:
                        debug_print("[NEXUS] SitRep-Fehler: {}".format(_sre))

                    # 7. LLM-Analyse
                    spin.update("NEXUS: KI-Synthese")
                    try:
                        answer = brain.chat_analysis(user_text, search_context=full_context)
                    except Exception as exc:
                        answer = "LLM-Fehler: {}".format(exc)

                    # 7b. Eskalations-Score vorberechnen + in History vorseeden
                    _pipeline_esc: dict = {}
                    try:
                        spin.update("NEXUS: Eskalations-Score")
                        from nexus_escalation import compute_escalation  # type: ignore
                        _pipeline_result = {
                            "articles": articles or [],
                            "flight_data": flight_data or {},
                            "maritime_data": maritime_data or {},
                        }
                        _pipeline_esc = compute_escalation(_pipeline_result, query)
                        _pipeline_esc["region"] = query
                        # Score in Predict-History eintragen (Sparkline-Seed)
                        try:
                            from nexus_predict import record_score as _rec_score  # type: ignore
                            _rec_score(
                                region=query,
                                score=_pipeline_esc.get("score", 0),
                                level=_pipeline_esc.get("level", "GRUEN"),
                                signal_details=_pipeline_esc.get("signal_details", []),
                                source="pipeline",
                            )
                        except Exception:
                            pass
                    except Exception as _ee:
                        debug_print("[LAGE] Eskalations-Score-Fehler: {}".format(_ee))

                    # 8. HTML-Report
                    try:
                        spin.update("NEXUS: HTML-Report")
                        generate_report_fn = _load_report()
                        if generate_report_fn:
                            import os as _os
                            save_dir = _os.path.dirname(_os.path.abspath(__file__))
                            _report_kwargs: dict = dict(
                                topic=query[:30],
                                analysis_text=answer,
                                articles=articles,
                                flight_data=flight_data,
                                weather_data=weather_data,
                                maritime_data=maritime_data,
                                escalation_data=_pipeline_esc or None,
                                auto_open=True,
                                save_dir=save_dir,
                                query=query,
                            )
                            # Stadtfokus: Karte auf Stadt-Koordinaten zentrieren
                            if city_focus:
                                _report_kwargs["map_center"] = [
                                    city_focus["lat"], city_focus["lon"]]
                                _report_kwargs["map_zoom"] = city_focus.get("zoom", 10)
                            report_path = generate_report_fn(**_report_kwargs)
                            answer += "\n\n[HTML-Report: {}]".format(
                                _os.path.basename(report_path))
                    except Exception as _e:
                        debug_print("[LAGE] HTML-Fehler: {}".format(_e))

                    # 9. PDF-Export (Stufe 2)
                    try:
                        spin.update("NEXUS: PDF-Export")
                        from nexus_pdf_export import export_report_pdf as _pdf  # type: ignore
                        import os as _os
                        save_dir = _os.path.dirname(_os.path.abspath(__file__))
                        pdf_path = _pdf(
                            topic=query[:30],
                            analysis_text=answer.split("[HTML-Report")[0],
                            articles=articles,
                            flight_data=flight_data,
                            weather_data=weather_data,
                            maritime_data=maritime_data,
                            correlation_alerts=(alerts if alerts else None),
                            auto_open=False,
                            save_dir=save_dir,
                        )
                        answer += "\n[PDF: {}]".format(_os.path.basename(pdf_path))
                        debug_print("[LAGE] PDF erstellt: {}".format(pdf_path))
                    except Exception as _pe:
                        debug_print("[LAGE] PDF-Fehler (kein Problem): {}".format(_pe))

            nexus_print("NEXUS: {}\n".format(answer))
            print("\033[36m" + "─" * 48 + " ◈\033[0m", flush=True)
            print()
            _speak_text_mode(voice, answer.split("[HTML")[0], timeout=120.0)
            continue

        # ── Spinner-Nachricht je nach Modus ─────
        spinner_msg = "NEXUS durchsucht die Geschichte" if historical_mode else "NEXUS denkt"

        with _Spinner(spinner_msg) as spin:
            try:
                answer = handle_query(brain, user_text, spinner=spin, historical=historical_mode)
            except Exception as exc:  # noqa: BLE001
                answer = "Fehler: {}".format(exc)

        # Abschlusszeile: im hist. Modus in Gelb
        if historical_mode:
            done_line = "\033[33m" + "─" * 48 + " 📜\033[0m"
        else:
            done_line = _c(getattr(config, "COLOR_DEBUG", "\033[32m"), "─" * 48 + " ✓")

        nexus_print("NEXUS: {}\n".format(answer))
        print(done_line, flush=True)
        print()
        _speak_text_mode(voice, answer, timeout=120.0)


def preflight(brain: NexusBrain) -> bool:
    # LLM-Provider erkennen
    try:
        from nexus_llm import get_active_provider  # type: ignore
        from nexus_brain import _use_claude        # type: ignore
        provider_name = get_active_provider()
        use_claude    = _use_claude()
    except Exception:
        provider_name = "Ollama ({})".format(config.OLLAMA_MODEL)
        use_claude    = False

    print("[NEXUS] LLM-Provider: {}".format(provider_name), flush=True)

    if use_claude:
        print("[NEXUS] Claude API aktiv - kein lokales Ollama noetig.", flush=True)
        return True

    # Ollama-Pruefung (nur wenn kein Claude konfiguriert)
    debug_print("[NEXUS] Pruefe Ollama-Server ...")
    if not brain.is_available():
        print("[NEXUS] FEHLER: Ollama nicht erreichbar unter {}.".format(config.OLLAMA_HOST),
              file=sys.stderr, flush=True)
        print("        Bitte starten: ollama serve", file=sys.stderr)
        print("        TIPP: CLAUDE_API_KEY in config.py setzen um Ollama zu umgehen.",
              file=sys.stderr)
        return False

    debug_print("[NEXUS] Pruefe Modell '{}'  ...".format(config.OLLAMA_MODEL))
    if not brain.model_available():
        print("[NEXUS] FEHLER: Modell '{}'  nicht installiert.".format(config.OLLAMA_MODEL),
              file=sys.stderr, flush=True)
        print("        Bitte ausfuehren: ollama pull {}".format(config.OLLAMA_MODEL),
              file=sys.stderr)
        return False

    debug_print("[NEXUS] Ollama bereit.")
    return True


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    _enable_ansi_windows()

    parser = argparse.ArgumentParser(description="NEXUS - persoenlicher KI-Assistent")
    parser.add_argument("--text", action="store_true",
                        help="Textmodus: tippen statt sprechen, NEXUS antwortet trotzdem per Stimme")
    parser.add_argument("--no-preflight", action="store_true")
    parser.add_argument("--wake", dest="wake", action="store_true", default=None)
    parser.add_argument("--no-wake", dest="wake", action="store_false")
    parser.add_argument("--no-hybrid", dest="hybrid", action="store_false", default=None)
    args = parser.parse_args()

    if args.wake is True:
        config.USE_WAKEWORD = True
    elif args.wake is False:
        config.USE_WAKEWORD = False
    if args.hybrid is False:
        config.USE_HYBRID_INPUT = False

    brain = NexusBrain()

    if not args.no_preflight:
        if not preflight(brain):
            return 1
        time.sleep(0.2)

    if args.text:
        # Textmodus: TTS + optionales Mikrofon initialisieren
        voice = None
        ears = None
        try:
            from nexus_voice import NexusVoice
            voice = NexusVoice()
        except Exception:  # noqa: BLE001
            pass  # Kein TTS - weiter ohne Stimme

        try:
            from nexus_voice import NexusEars
            _ears = NexusEars()
            if _ears.initialize_microphone():
                ears = _ears
                debug_print("[NEXUS] Mikrofon bereit (Trigger: 'v' + Enter).")
            else:
                debug_print("[NEXUS] Kein Mikrofon - nur Texteingabe.")
        except Exception:  # noqa: BLE001
            pass  # Kein Mikrofon - nur Texteingabe

        return run_text_mode(brain, voice=voice, ears=ears)

    return run_voice_mode(brain)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[NEXUS] Beendet.", flush=True)
        sys.exit(0)
