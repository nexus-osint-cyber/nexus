"""
nexus_push.py — NEXUS Push-Alerts ans Handy
=============================================
Sendet sofortige Handy-Benachrichtigungen via ntfy.sh.
Kein Account nötig — ntfy.sh ist kostenlos und open source.

Setup (einmalig, 2 Minuten):
  1. ntfy-App aufs Handy installieren:
     Android: https://play.google.com/store/apps/details?id=io.heckel.ntfy
     iOS:     https://apps.apple.com/app/ntfy/id1625396347
  2. In der App auf "+" klicken und Topic abonnieren:
     z.B. "nexus-alerts-abc123" (wird in config.py gespeichert)
  3. config.py: NTFY_TOPIC = "nexus-alerts-abc123"

Dann landet jeder Alert sofort auf deinem Handy.

Aufruf im Code:
  from nexus_push import push_alert
  push_alert("Iran", 55.2, "ROT", ["gps_jamming", "isr_aircraft"])
"""

from __future__ import annotations

import sys
import time
import requests
from datetime import datetime, timezone
from typing import Optional

NTFY_BASE_URL  = "https://ntfy.sh"
REQUEST_TIMEOUT = 8

# Modul-Level-Flag: NTFY-Warnung wird nur einmal pro Python-Session gedruckt.
# Verhindert, dass jede Score-Überschreitung dieselbe Meldung wiederholt.
_ntfy_topic_warned: bool = False

# ── Konfig laden ──────────────────────────────────────────────────────────────

def _get_topic() -> str:
    """Liest NTFY_TOPIC aus config.py."""
    try:
        import config  # type: ignore
        topic = getattr(config, "NTFY_TOPIC", "")
        return topic.strip() if topic else ""
    except ImportError:
        return ""


# ── Priority-Mapping ──────────────────────────────────────────────────────────

_LEVEL_TO_PRIORITY = {
    "KRITISCH": "urgent",   # ntfy: maximale Priorität, Durchdringen von DND
    "ROT":      "high",
    "ORANGE":   "high",
    "GELB":     "default",
    "GRUEN":    "low",
}

_LEVEL_TO_EMOJI = {
    "KRITISCH": "⛔",
    "ROT":      "🔴",
    "ORANGE":   "🟠",
    "GELB":     "🟡",
    "GRUEN":    "🟢",
}


# ── Kern-Funktion ─────────────────────────────────────────────────────────────

def push_alert(
    region:    str,
    score:     float,
    level:     str,
    signale:   list[str] | None = None,
    details:   str = "",
    topic:     str = "",
    click_url: str = "",
) -> bool:
    """
    Sendet einen Push-Alert ans Handy via ntfy.sh.

    region:    z.B. "Iran"
    score:     z.B. 55.2
    level:     z.B. "ROT"
    signale:   z.B. ["gps_jamming", "isr_aircraft"]
    details:   optionaler Freitext
    topic:     ntfy-Topic (überschreibt config.py)
    click_url: optionale URL — Tippen auf die Push-Nachricht öffnet diese
               Seite direkt (z.B. Link zum fertigen NEXUS-Bericht via
               nexus_live_server.py: "https://<server>:<port>/reports/<datei>.html?token=...")

    Gibt True zurück wenn erfolgreich.
    """
    global _ntfy_topic_warned
    t = topic or _get_topic()
    if not t:
        if not _ntfy_topic_warned:
            print("[Push] NTFY_TOPIC nicht konfiguriert — Push-Alerts deaktiviert. "
                  "Bitte NTFY_TOPIC = \"dein-topic\" in config.py eintragen.",
                  file=sys.stderr)
            _ntfy_topic_warned = True
        return False

    emoji  = _LEVEL_TO_EMOJI.get(level, "📡")
    prio   = _LEVEL_TO_PRIORITY.get(level, "default")
    jetzt  = datetime.now(timezone.utc).strftime("%H:%M UTC")

    titel  = f"{emoji} NEXUS: {region} — {level} ({score}/100)"

    if signale:
        sig_str = ", ".join(signale[:4])
        nachricht = f"Score: {score}/100 [{level}]\nSignale: {sig_str}\n{jetzt}"
    else:
        nachricht = f"Score: {score}/100 [{level}]\n{jetzt}"

    if details:
        nachricht += f"\n{details[:200]}"

    if click_url:
        nachricht += "\n(Tippen für vollen Bericht)"

    url = f"{NTFY_BASE_URL}/{t}"
    try:
        headers = {
            # Title als UTF-8-Bytes kodieren: HTTP-Header werden von requests
            # sonst als Latin-1 interpretiert, was bei Emojis (z.B. 'ORANGE') zu
            # "ordinal not in range(256)" fuehrt.
            "Title":    titel.encode("utf-8"),
            "Priority": prio,
            "Tags":     f"warning,{region.lower()}",
        }
        if click_url:
            # ntfy "Click"-Header: Tippen auf die Notification öffnet die URL
            # direkt im Browser des Handys (keine separate Action-Syntax nötig).
            headers["Click"] = click_url.encode("utf-8")

        r = requests.post(
            url,
            data=nachricht.encode("utf-8"),
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code in (200, 201, 204):
            print(f"[Push] ✓ Alert gesendet: {titel}", file=sys.stderr)
            return True
        else:
            print(f"[Push] HTTP {r.status_code} für Topic '{t}'", file=sys.stderr)
            return False
    except Exception as e:
        print(f"[Push] Fehler: {e}", file=sys.stderr)
        return False


def push_report(
    title:     str,
    summary:   str,
    report_url: str,
    topic:     str = "",
    level:     str = "GELB",
) -> bool:
    """
    Sendet eine Push-Nachricht mit Link zu einem fertigen NEXUS-Bericht
    (z.B. der HTML-Tagesbericht aus nexus_daily.py / nexus_selftest.py).

    title:      z.B. "NEXUS Selbsttest: Iran + Ukraine"
    summary:    kurze Zusammenfassung (max. ~200 Zeichen werden angezeigt)
    report_url: vollständige URL zum Bericht
                (https://<server>:<port>/reports/<datei>.html?token=...)
    topic:      ntfy-Topic (überschreibt config.py)
    level:      nur für Icon/Priorität, z.B. "GELB"/"ORANGE"/"ROT"

    Gibt True zurück wenn erfolgreich.
    """
    global _ntfy_topic_warned
    t = topic or _get_topic()
    if not t:
        if not _ntfy_topic_warned:
            print("[Push] NTFY_TOPIC nicht konfiguriert — Push-Alerts deaktiviert.",
                  file=sys.stderr)
            _ntfy_topic_warned = True
        return False

    emoji = _LEVEL_TO_EMOJI.get(level, "📄")
    prio  = _LEVEL_TO_PRIORITY.get(level, "default")
    jetzt = datetime.now(timezone.utc).strftime("%H:%M UTC")

    nachricht = f"{summary[:200]}\n{jetzt}\n(Tippen für vollen Bericht)"

    url = f"{NTFY_BASE_URL}/{t}"
    try:
        r = requests.post(
            url,
            data=nachricht.encode("utf-8"),
            headers={
                "Title":    f"{emoji} {title}".encode("utf-8"),
                "Priority": prio,
                "Tags":     "page_facing_up,report",
                "Click":    report_url.encode("utf-8"),
            },
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code in (200, 201, 204):
            print(f"[Push] ✓ Bericht gesendet: {title}", file=sys.stderr)
            return True
        else:
            print(f"[Push] HTTP {r.status_code} für Topic '{t}'", file=sys.stderr)
            return False
    except Exception as e:
        print(f"[Push] Fehler: {e}", file=sys.stderr)
        return False


def push_score_change(
    region:     str,
    score_alt:  float,
    score_neu:  float,
    level_neu:  str,
    signale:    list[str] | None = None,
) -> bool:
    """
    Sendet Alert bei signifikanter Score-Änderung (±10 Punkte oder Level-Wechsel).
    Wird aus nexus_escalation_watchlist.py aufgerufen.
    """
    delta = score_neu - score_alt
    if abs(delta) < 5:
        return False  # Zu klein, kein Alert

    pfeil = "↑" if delta > 0 else "↓"
    details = f"Score: {score_alt} → {score_neu} ({pfeil}{abs(delta):.1f})"
    return push_alert(region, score_neu, level_neu, signale, details)


# ── Schwellenwert-Monitor ─────────────────────────────────────────────────────

class PushMonitor:
    """
    Überwacht Score-Änderungen und sendet Alerts bei Überschreitung.
    Verhindert Spam durch Cooldown (default: 30 Minuten pro Region).
    """
    def __init__(self, schwellenwert: float = 50.0, cooldown_min: int = 30):
        self.schwellenwert  = schwellenwert
        self.cooldown_sek   = cooldown_min * 60
        self._letzter_alert: dict[str, float] = {}  # region → timestamp
        self._letzter_score: dict[str, float] = {}

    def check_and_alert(
        self,
        region:  str,
        score:   float,
        level:   str,
        signale: list[str] | None = None,
    ) -> bool:
        """
        Prüft ob Alert gesendet werden soll und tut es ggf.
        Gibt True zurück wenn Alert gesendet.
        """
        jetzt = time.monotonic()
        letzter = self._letzter_alert.get(region, 0)
        score_alt = self._letzter_score.get(region, 0)

        # Cooldown prüfen
        if jetzt - letzter < self.cooldown_sek:
            return False

        # Schwellenwert überschritten?
        schwelle_ueberschritten = score >= self.schwellenwert
        # Signifikante Änderung?
        signifikante_aenderung = abs(score - score_alt) >= 10

        if schwelle_ueberschritten or signifikante_aenderung:
            ok = push_alert(region, score, level, signale)
            # Cooldown immer setzen – auch bei fehlgeschlagenem Push (kein Topic).
            # Sonst fehlt der Cooldown und die Meldung feuert jeden Lauf erneut.
            self._letzter_alert[region] = jetzt
            self._letzter_score[region] = score
            return ok

        self._letzter_score[region] = score
        return False


# Globale Instanz
_monitor = PushMonitor(schwellenwert=50.0, cooldown_min=30)


def monitor_check(region: str, score: float, level: str,
                  signale: list[str] | None = None) -> bool:
    """Kurzform für den globalen Monitor."""
    return _monitor.check_and_alert(region, score, level, signale)


# ── Setup-Hilfe ───────────────────────────────────────────────────────────────

def setup_anleitung() -> str:
    """Gibt Setup-Anleitung zurück."""
    return """
╔══════════════════════════════════════════════════════════════╗
║          NEXUS Push-Alerts — Setup in 2 Minuten             ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  1. ntfy-App installieren:                                   ║
║     Android: Play Store → "ntfy"                            ║
║     iOS:     App Store → "ntfy"                             ║
║                                                              ║
║  2. Eigenes Topic wählen (wie ein Passwort):                 ║
║     Beispiel: nexus-alerts-2847                             ║
║     (zufällig, damit niemand anders deine Alerts bekommt)   ║
║                                                              ║
║  3. In der ntfy-App: "+" → Topic eingeben → Abonnieren      ║
║                                                              ║
║  4. In config.py eintragen:                                  ║
║     NTFY_TOPIC = "nexus-alerts-2847"                        ║
║                                                              ║
║  5. Test:                                                     ║
║     python nexus_push.py --test                             ║
║                                                              ║
║  Fertig! Du bekommst jetzt Alerts wenn Score > 50.          ║
╚══════════════════════════════════════════════════════════════╝
"""


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NEXUS Push-Alert Test")
    parser.add_argument("--test",   action="store_true", help="Test-Alert senden")
    parser.add_argument("--setup",  action="store_true", help="Setup-Anleitung anzeigen")
    parser.add_argument("--topic",  type=str, default="", help="ntfy-Topic (überschreibt config.py)")
    args = parser.parse_args()

    if args.setup:
        print(setup_anleitung())
    elif args.test:
        topic = args.topic or _get_topic()
        if not topic:
            print("Kein Topic konfiguriert!")
            print(setup_anleitung())
        else:
            print(f"[Push] Sende Test-Alert an Topic: {topic}")
            ok = push_alert(
                region="Test",
                score=55.0,
                level="ORANGE",
                signale=["gps_jamming", "isr_aircraft"],
                details="Das ist ein Test-Alert von NEXUS.",
                topic=topic,
            )
            print(f"[Push] {'✓ Erfolgreich! Schau aufs Handy.' if ok else '✗ Fehlgeschlagen.'}")
    else:
        print(setup_anleitung())
