"""
nexus_selftest.py — Automatisierter NEXUS-Selbsttest (Cron)
=============================================================
Laeuft 1-2x/Tag per Cron. Reines Python, KEIN "claude -p"-Aufruf —
verbraucht also kein Claude-Pro/Max-Kontingent. Erstellt einen
NEXUS-Tagesbericht fuer feste Zielregionen (Standard: Iran, Ukraine)
und schickt eine NTFY-Push-Nachricht mit Klick-Link zum fertigen
Bericht (ueber nexus_live_server.py's token-geschuetzte /reports/-Route).

API-Limits: Ein Lauf nutzt nur kostenlose/quotenfreie Datenquellen
(OpenSky, Open-Meteo, AISStream, USGS, GDELT [gecacht], RSS, ACLED/UCDP).
2x taeglich fuer 2 Regionen bleibt weit unter allen dokumentierten
Free-Tier-Limits (siehe NEXUS_RESEARCH_LOG.md / Projekt-Notizen).
Die optionale KI-Kommentierung (nexus_brain.py) ist standardmaessig
deaktiviert (CLAUDE_API_KEY leer) und nutzt ohnehin einen separaten,
nach Tokens abgerechneten Claude-API-Key — NICHT das Pro/Max-Abo.

Cron-Beispiel (08:00 und 20:00 UTC, ca. 12h Abstand):
  0 8,20 * * * cd /opt/nexus && venv/bin/python nexus_selftest.py \
      >> .loop_prompts/logs/selftest_$(date +%Y%m%d_%H%M%S).log 2>&1

Voraussetzung fuer den Klick-Link im Push: nexus_live_server.py laeuft
dauerhaft (z.B. als systemd-Service) mit NEXUS_HOST=0.0.0.0 und einem
gesetzten NEXUS_TOKEN. Laeuft kein Live-Server, wird der Bericht trotzdem
lokal erstellt — der Push enthaelt dann nur Text, ohne Klick-Link.

Aufruf von Hand:
  python nexus_selftest.py                 # Standard: Iran, Ukraine
  python nexus_selftest.py "Iran" "Taiwan"  # eigene Ziele
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_TARGETS = ["Iran", "Ukraine"]


def _cfg_attr(name: str, env_name: str | None = None) -> str:
    """Liest eine Einstellung zuerst aus der Umgebungsvariable, dann aus
    config.py — exakt das gleiche Schema wie nexus_live_server.py."""
    val = os.environ.get(env_name or name, "").strip()
    if val:
        return val
    try:
        import config  # type: ignore
        return str(getattr(config, name, "")).strip()
    except ImportError:
        return ""


def _server_base_url() -> str:
    """Baut die Basis-URL zum Live-Server (Schema, Host, Port)."""
    host   = _cfg_attr("NEXUS_HOST") or "localhost"
    port   = _cfg_attr("NEXUS_PORT") or "11430"
    scheme = _cfg_attr("NEXUS_SCHEME") or "http"
    return f"{scheme}://{host}:{port}"


def _log_for_backtest(targets: list[str]) -> None:
    """
    Fuehrt pro Ziel zusaetzlich nexus_longtest.ein_lauf() aus.

    Das schreibt ein lauf_<timestamp>.json nach nexus_longtest_daten/ —
    genau das Format, das nexus_backtest.py fuer die woechentliche
    Auswertung (nexus_weekly_eval.py) braucht. create_daily_brief() allein
    speichert keine fuer den Backtest auswertbaren Rohdaten, nur fertiges
    HTML — deshalb der zusaetzliche, bewusst in Kauf genommene zweite
    Sammeldurchlauf. Laut API-Limit-Pruefung (siehe Projektnotizen) ist das
    bei 1-2 Laeufen/Tag unkritisch: GDELT ist 4h gecacht, alle anderen
    Quellen haben weit groessere Freikontingente als hier gebraucht.
    Fehler hier sind nicht fatal fuer den eigentlichen Bericht/Push.
    """
    try:
        from nexus_longtest import ein_lauf  # type: ignore
    except Exception as e:
        print(f"[Selftest] nexus_longtest nicht verfuegbar, kein Backtest-Log: {e}",
              file=sys.stderr)
        return
    for ziel in targets:
        try:
            ein_lauf(ziel, 1, 1)
        except Exception as e:
            print(f"[Selftest] Backtest-Log fuer '{ziel}' fehlgeschlagen: {e}",
                  file=sys.stderr)


def run_selftest(targets: list[str] | None = None) -> str:
    """
    Erstellt den NEXUS-Bericht fuer die Zielregionen, verschickt den
    NTFY-Push mit Klick-Link, und schreibt zusaetzlich die Rohdaten-Logs
    fuer die woechentliche Backtest-Auswertung (nexus_weekly_eval.py).

    Gibt den Dateipfad zur erzeugten HTML-Datei zurueck.
    """
    targets = targets or DEFAULT_TARGETS

    from nexus_daily import create_daily_brief  # type: ignore
    path = create_daily_brief(
        regions=targets, auto_open=False, pdf=False, send_email=False
    )
    filename = Path(path).name

    _log_for_backtest(targets)

    # Live-Server (falls im selben Prozess/Host erreichbar) ueber den
    # neuen Bericht informieren, damit auch die /report-Kurzroute aktuell ist.
    try:
        import nexus_live_server  # type: ignore
        nexus_live_server.set_last_report(path)
    except Exception:
        pass

    token      = _cfg_attr("NEXUS_TOKEN")
    report_url = f"{_server_base_url()}/reports/{filename}"
    if token:
        report_url += f"?token={token}"

    try:
        from nexus_push import push_report  # type: ignore
        ziel_str = " + ".join(targets)
        push_report(
            title=f"NEXUS Selbsttest: {ziel_str}",
            summary=f"Bericht fuer {ziel_str} fertig.",
            report_url=report_url,
        )
    except Exception as e:
        print(f"[Selftest] Push fehlgeschlagen: {e}", file=sys.stderr)

    print(f"[Selftest] Bericht erstellt: {path}")
    print(f"[Selftest] Link: {report_url}")
    return path


if __name__ == "__main__":
    _targets = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_TARGETS
    run_selftest(_targets)
