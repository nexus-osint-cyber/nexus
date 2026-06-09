"""
NEXUS - Watchlist-System
Überwacht Regionen/Keywords im Hintergrund alle 15 Minuten.
Bei neuen Ereignissen erscheint ein Alert im NEXUS-Terminal.

Befehle (in main.py verdrahtet):
  W                   – Watchlist-Menü anzeigen
  W+ ukraine          – "ukraine" zur Watchlist hinzufügen
  W- ukraine          – "ukraine" entfernen
  W                   – Aktuelle Watchlist anzeigen
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

WATCH_INTERVAL_SEC = 15 * 60   # 15 Minuten zwischen Checks

_thread:   threading.Thread | None = None
_running   = False
_print_fn  = print   # wird von main.py mit nexus_print überschrieben


# ── Hintergrund-Loop ─────────────────────────────────────────────────────────

def _check_term(term: str) -> int:
    """Prüft einen Term, speichert neue Artikel, gibt Anzahl Neuer zurück."""
    try:
        from nexus_rss     import fetch_news      # type: ignore
        from nexus_memory  import store_articles, new_events_since, wl_mark_checked

        articles = fetch_news(fast=True, keyword_filter=term[:30]) or []
        if articles:
            store_articles(term, articles)

        new_count = new_events_since(term)
        wl_mark_checked(term, new_count)
        return new_count
    except Exception:
        return 0


def _watch_loop() -> None:
    """Läuft als Daemon-Thread. Prüft alle 15min alle Watchlist-Einträge."""
    global _running
    try:
        from nexus_memory import init_db, wl_list
        init_db()
    except Exception:
        pass

    while _running:
        try:
            from nexus_memory import wl_list
            watched = wl_list()
            for entry in watched:
                if not _running:
                    return
                term      = entry["term"]
                new_count = _check_term(term)
                if new_count > 0:
                    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
                    _print_fn(
                        f"\n\033[33m[NEXUS WATCH] ⚠  {new_count} neue Ereignisse "
                        f"zu '{term}' – {ts}\033[0m",
                        flush=True,
                    )
                    # Windows Toast + Sound
                    try:
                        from nexus_alert import alert_watchlist_hit  # type: ignore
                        alert_watchlist_hit(
                            keyword=term,
                            headline=f"{new_count} neue Ereignisse erkannt – {ts}",
                            source="NEXUS Watch",
                        )
                    except Exception:
                        pass
                time.sleep(2)   # kurze Pause zwischen Begriffen
        except Exception:
            pass

        # Warte bis zum nächsten Zyklus, aber reagiere sofort auf Stop
        for _ in range(WATCH_INTERVAL_SEC):
            if not _running:
                return
            time.sleep(1)


# ── Lifecycle ────────────────────────────────────────────────────────────────

def start(print_fn=None) -> bool:
    """Startet den Watchlist-Thread. Gibt True zurück wenn erfolgreich."""
    global _thread, _running, _print_fn
    if _running:
        return True
    if print_fn:
        _print_fn = print_fn
    _running = True
    _thread  = threading.Thread(target=_watch_loop, daemon=True, name="nexus-watchlist")
    _thread.start()
    return True


def stop() -> None:
    global _running
    _running = False


def is_running() -> bool:
    return _running


# ── Watchlist verwalten ──────────────────────────────────────────────────────

def add(term: str) -> str:
    """Fügt Term zur Watchlist hinzu. Gibt Status-String zurück."""
    try:
        from nexus_memory import wl_add
        ok = wl_add(term.strip())
        return f"✅ '{term}' zur Watchlist hinzugefügt." if ok else f"'{term}' ist bereits in der Watchlist."
    except Exception as e:
        return f"Fehler: {e}"


def remove(term: str) -> str:
    try:
        from nexus_memory import wl_remove
        wl_remove(term.strip())
        return f"🗑 '{term}' aus Watchlist entfernt."
    except Exception as e:
        return f"Fehler: {e}"


def show() -> str:
    """Gibt formatierte Watchlist zurück."""
    try:
        from nexus_memory import wl_list
        entries = wl_list()
    except Exception:
        entries = []

    if not entries:
        return (
            "📋 Watchlist ist leer.\n"
            "  Hinzufügen: W+ ukraine\n"
            "  Entfernen:  W- ukraine"
        )

    lines = [f"📋 NEXUS WATCHLIST ({len(entries)} Einträge):"]
    for e in entries:
        last = e.get("checked_ts", "noch nie")[:16] if e.get("checked_ts") else "noch nie"
        cnt  = e.get("alert_cnt", 0)
        lines.append(
            f"  • {e['term']:<25} "
            f"Alerts: {cnt:>4}  |  Zuletzt: {last}"
        )
    lines.append(f"\nIntervall: alle {WATCH_INTERVAL_SEC // 60} Minuten")
    lines.append("Entfernen: W- <begriff>")
    return "\n".join(lines)


# ── Direktaufruf zum Testen ──────────────────────────────────────────────────
if __name__ == "__main__":
    print(add("Ukraine"))
    print(add("Hormuz"))
    print(show())
    print("\nStarte 10s Test-Loop...")
    start()
    time.sleep(10)
    stop()
    print("Fertig.")
