"""
nexus_weekly_eval.py — Woechentliche Selbsttest-Auswertung (Cron)
====================================================================
Laeuft 1x/Woche per Cron. Wertet die von nexus_selftest.py gesammelten
lauf_*.json-Logs (nexus_longtest_daten/) mit der bestehenden
nexus_backtest.py-Engine aus — gegen die in nexus_groundtruth.json
gepflegten echten Ereignisse — und verschickt eine kurze NTFY-
Zusammenfassung mit Klick-Link zum vollen HTML-Bericht.

Kein "claude -p"-Aufruf, kein Pro/Max-Kontingent — reine Python-Auswertung
bereits gesammelter Daten.

Ground-Truth pflegen: nexus_groundtruth.json von Hand um echte Ereignisse
der Woche ergaenzen (Datum, Label, erwartetes Eskalations-Level — siehe
Format/Beispiele in nexus_backtest.py GROUND_TRUTH_EVENTS). Ohne aktuelle
Ground-Truth-Eintraege liefert der Bericht trotzdem Kennzahlen
(Score-Mittelwert, Volatilitaet, Quellen-Uptime), nur keine Trefferquote.

Cron-Beispiel (jeden Montag 09:00 UTC):
  0 9 * * 1 cd /opt/nexus && venv/bin/python nexus_weekly_eval.py \
      >> .loop_prompts/logs/weekly_eval_$(date +%Y%m%d_%H%M%S).log 2>&1
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPORT_DIR = SCRIPT_DIR / "nexus_reports"
LOGDIR     = SCRIPT_DIR / "nexus_longtest_daten"
GT_FILE    = SCRIPT_DIR / "nexus_groundtruth.json"


def run_weekly_eval() -> str:
    """
    Erstellt den woechentlichen Backtest-HTML-Bericht und verschickt eine
    NTFY-Zusammenfassung mit Klick-Link. Gibt den Dateipfad zurueck
    (leerer String, falls keine Logs vorhanden sind).
    """
    from nexus_backtest import build_html, load_logs, run_backtest  # type: ignore

    if not LOGDIR.exists():
        print(f"[WeeklyEval] Kein Log-Verzeichnis: {LOGDIR} — "
              f"laeuft nexus_selftest.py schon per Cron?")
        return ""

    logs = load_logs(LOGDIR)
    if not logs:
        print(f"[WeeklyEval] Keine Logs in {LOGDIR} gefunden.")
        return ""

    extra_gt = None
    if GT_FILE.exists():
        try:
            with open(GT_FILE, encoding="utf-8") as f:
                extra_gt = json.load(f).get("events", [])
        except Exception as e:
            print(f"[WeeklyEval] Ground-Truth-Datei fehlerhaft: {e}", file=sys.stderr)

    result = run_backtest(logs, extra_gt=extra_gt)
    html   = build_html(result)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts_fn   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    outfile = REPORT_DIR / f"nexus_weekly_eval_{ts_fn}.html"
    outfile.write_text(html, encoding="utf-8")

    m  = result["metrics"]
    gt = result["ground_truth"]
    summary = f"{m['total_runs']} Laeufe, Ø Score {m['score_mean']:.1f}"
    if gt:
        correct = sum(1 for g in gt if g["verdict"] == "KORREKT")
        summary += f", GT {correct}/{len(gt)} korrekt"
    else:
        summary += " (keine Ground-Truth-Ereignisse hinterlegt)"

    try:
        from nexus_selftest import _cfg_attr, _server_base_url  # type: ignore
        base  = _server_base_url()
        token = _cfg_attr("NEXUS_TOKEN")
    except Exception:
        base, token = "http://localhost:11430", ""

    report_url = f"{base}/reports/{outfile.name}"
    if token:
        report_url += f"?token={token}"

    try:
        from nexus_push import push_report  # type: ignore
        push_report(
            title="NEXUS Wochenauswertung",
            summary=summary,
            report_url=report_url,
        )
    except Exception as e:
        print(f"[WeeklyEval] Push fehlgeschlagen: {e}", file=sys.stderr)

    print(f"[WeeklyEval] Bericht gespeichert: {outfile}")
    print(f"[WeeklyEval] Link: {report_url}")
    return str(outfile)


if __name__ == "__main__":
    run_weekly_eval()
