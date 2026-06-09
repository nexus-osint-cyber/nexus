"""
nexus_calibration.py  — T178
Source Calibration + Feedback Loop für NEXUS.

Macht den Eskalations-Score überprüfbar und kalibrierbar.

Was es tut:
  1. Loggt jeden Alert mit Timestamp, Score, Region, Kontext
  2. Analyst trägt per CLI Outcome ein (eskaliert/nicht eskaliert)
  3. Berechnet Precision/Recall pro Score-Schwelle
  4. Gibt kalibrierten Multiplikator zurück (Score × Faktor = kalibrierter Score)
  5. Zeigt Reliability-Bericht pro Quellen-Typ

Baut auf nexus_timeseries.py (T173) auf — nutzt dessen alert-Tabelle.

Verwendung:
  from nexus_calibration import get_calibrated_score, calibration_report
  python nexus_calibration.py --log-alert --score 75 --region hormuz --context "P-8A + USNS"
  python nexus_calibration.py --show-open
  python nexus_calibration.py --record-outcome 42 --outcome eskaliert
  python nexus_calibration.py --report
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ─── Abhängigkeit: nexus_timeseries ──────────────────────────────────────────

try:
    from nexus_timeseries import (
        record_alert as _ts_record_alert,
        record_outcome as _ts_record_outcome,
        open_alerts as _ts_open_alerts,
        escalation_history as _ts_history,
        DB_PATH as _TS_DB_PATH,
    )
    _TS_AVAILABLE = True
except ImportError:
    _TS_AVAILABLE = False
    _TS_DB_PATH   = Path(__file__).parent / "nexus_data" / "nexus_timeseries.db"

# ─── Konfiguration ────────────────────────────────────────────────────────────

# Score-Schwellen für Level-Klassifikation (müssen mit nexus_escalation.py übereinstimmen)
_SCORE_THRESHOLDS = {
    "KRITISCH": 85.0,
    "HOCH":     65.0,
    "MITTEL":   40.0,
    "NIEDRIG":  20.0,
    "NORMAL":    0.0,
}

# Mindest-Outcomes für valide Kalibrierung
_MIN_OUTCOMES = 5

# Wie viele Tage Vergangenheit für Kalibrierung
_CALIBRATION_WINDOW_DAYS = 90

# ─── Datentypen ───────────────────────────────────────────────────────────────

@dataclass
class CalibrationStats:
    """Statistiken für eine Score-Schwelle."""
    threshold:    float
    n_alerts:     int = 0
    n_outcomes:   int = 0
    n_correct:    int = 0      # Tatsächlich eskaliert wenn Score >= threshold
    n_false_pos:  int = 0      # Nicht eskaliert obwohl Score >= threshold
    n_false_neg:  int = 0      # Eskaliert obwohl Score < threshold
    precision:    float = 0.0  # TP / (TP + FP)
    recall:       float = 0.0  # TP / (TP + FN)
    f1:           float = 0.0
    calibration_factor: float = 1.0  # Multiplikator für Score-Anpassung

    def to_dict(self) -> dict:
        return {
            "threshold":   self.threshold,
            "n_alerts":    self.n_alerts,
            "n_outcomes":  self.n_outcomes,
            "precision":   round(self.precision, 3),
            "recall":      round(self.recall, 3),
            "f1":          round(self.f1, 3),
            "cal_factor":  round(self.calibration_factor, 3),
        }


@dataclass
class SourceReliability:
    """Zuverlässigkeit einer einzelnen Datenquelle."""
    source:           str
    n_contributions:  int = 0
    n_verified:       int = 0
    reliability_pct:  float = 0.0
    avg_contribution: float = 0.0  # Durchschnittlicher Score-Beitrag wenn aktiv
    worth_monitoring: bool = True

    def to_dict(self) -> dict:
        return {
            "source":          self.source,
            "n_contributions": self.n_contributions,
            "reliability_pct": round(self.reliability_pct, 1),
            "worth_monitoring": self.worth_monitoring,
        }


@dataclass
class CalibrationReport:
    """Vollständiger Kalibrierungsbericht."""
    n_total_alerts:    int = 0
    n_with_outcomes:   int = 0
    n_open:            int = 0
    overall_precision: float = 0.0
    overall_recall:    float = 0.0
    best_threshold:    float = 65.0
    calibration_factor: float = 1.0
    threshold_stats:   list[CalibrationStats] = field(default_factory=list)
    source_reliability: list[SourceReliability] = field(default_factory=list)
    recommendation:    str = ""
    generated:         str = ""

    def to_dict(self) -> dict:
        return {
            "n_total_alerts":    self.n_total_alerts,
            "n_with_outcomes":   self.n_with_outcomes,
            "n_open":            self.n_open,
            "overall_precision": round(self.overall_precision, 3),
            "overall_recall":    round(self.overall_recall, 3),
            "best_threshold":    self.best_threshold,
            "calibration_factor": round(self.calibration_factor, 3),
            "threshold_stats":   [s.to_dict() for s in self.threshold_stats],
            "source_reliability": [s.to_dict() for s in self.source_reliability],
            "recommendation":    self.recommendation,
            "generated":         self.generated,
        }


# ─── Alert-Logging ────────────────────────────────────────────────────────────

def log_alert(
    score:   float,
    region:  str = "",
    context: str = "",
    source:  str = "nexus_escalation",
    level:   str = "",
) -> int:
    """
    Loggt einen Alert.
    Gibt alert_id zurück (für record_outcome() benötigt).

    Beispiel:
        aid = log_alert(78.5, "hormuz", "P-8A + USNS + ACLED convergence")
        print(f"Alert #{aid} geloggt. Outcome später eintragen.")
    """
    if not level:
        for lv, thr in sorted(_SCORE_THRESHOLDS.items(), key=lambda x: -x[1]):
            if score >= thr:
                level = lv
                break
        level = level or "NORMAL"

    if _TS_AVAILABLE:
        return _ts_record_alert(source, level, score, region, context)

    # Direkter Fallback falls nexus_timeseries nicht verfügbar
    return _direct_log_alert(source, level, score, region, context)


def _direct_log_alert(source, level, score, region, context) -> int:
    """Direktes Alert-Logging ohne nexus_timeseries."""
    db = _ensure_db()
    con = sqlite3.connect(str(db))
    cur = con.execute(
        "INSERT INTO alerts(ts, source, level, score, region, context) VALUES(?,?,?,?,?,?)",
        (time.time(), source, level, score, region, context),
    )
    aid = cur.lastrowid
    con.commit()
    con.close()
    return aid


def record_outcome(
    alert_id: int,
    outcome: str,
    note: str = "",
) -> bool:
    """
    Trägt Outcome für einen Alert ein.
    outcome: "eskaliert" | "nicht_eskaliert" | "unklar"

    Beispiel:
        record_outcome(42, "eskaliert",
                       "Hormuz-Durchfahrt blockiert am 2024-03-15 laut Reuters")
    """
    allowed = {"eskaliert", "nicht_eskaliert", "unklar"}
    if outcome not in allowed:
        raise ValueError(f"outcome muss einer von {allowed} sein")

    if _TS_AVAILABLE:
        return _ts_record_outcome(alert_id, outcome, note)

    db = _ensure_db()
    con = sqlite3.connect(str(db))
    con.execute(
        "UPDATE alerts SET outcome=?, outcome_ts=?, outcome_note=? WHERE id=?",
        (outcome, time.time(), note, alert_id),
    )
    con.commit()
    con.close()
    return True


def show_open_alerts(hours: float = 168) -> list[dict]:
    """Zeigt Alerts ohne Outcome der letzten N Stunden."""
    if _TS_AVAILABLE:
        return _ts_open_alerts(hours)
    return _direct_open_alerts(hours)


def _direct_open_alerts(hours: float) -> list[dict]:
    db = _ensure_db()
    cutoff = time.time() - hours * 3600
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM alerts WHERE outcome IS NULL AND ts>=? ORDER BY ts DESC",
        (cutoff,),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ─── Datenbank-Zugriff ────────────────────────────────────────────────────────

def _ensure_db() -> Path:
    """Stellt sicher dass die Datenbank existiert."""
    db_path = _TS_DB_PATH
    if not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.executescript("""
        CREATE TABLE IF NOT EXISTS alerts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          REAL    NOT NULL,
            source      TEXT    NOT NULL,
            level       TEXT    NOT NULL,
            score       REAL    DEFAULT 0.0,
            region      TEXT    DEFAULT '',
            context     TEXT    DEFAULT '',
            outcome     TEXT    DEFAULT NULL,
            outcome_ts  REAL    DEFAULT NULL,
            outcome_note TEXT   DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts DESC);
    """)
    con.commit()
    con.close()
    return db_path


def _load_alerts_with_outcomes(days: int = _CALIBRATION_WINDOW_DAYS) -> list[dict]:
    """Lädt alle Alerts mit Outcomes für Kalibrierung."""
    db = _ensure_db()
    cutoff = time.time() - days * 86400
    try:
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM alerts WHERE ts>=? ORDER BY ts DESC",
            (cutoff,),
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ─── Kalibrierungs-Berechnung ─────────────────────────────────────────────────

def _compute_threshold_stats(
    alerts: list[dict],
    threshold: float,
) -> CalibrationStats:
    """Berechnet Precision/Recall für eine Score-Schwelle."""
    stats = CalibrationStats(threshold=threshold)

    with_outcomes = [a for a in alerts if a.get("outcome") in
                     ("eskaliert", "nicht_eskaliert")]
    stats.n_alerts   = len(alerts)
    stats.n_outcomes = len(with_outcomes)

    if not with_outcomes:
        return stats

    # True Positives: Score >= threshold UND outcome=eskaliert
    tp = sum(1 for a in with_outcomes
             if (a.get("score") or 0) >= threshold and a["outcome"] == "eskaliert")
    # False Positives: Score >= threshold ABER outcome=nicht_eskaliert
    fp = sum(1 for a in with_outcomes
             if (a.get("score") or 0) >= threshold and a["outcome"] == "nicht_eskaliert")
    # False Negatives: Score < threshold ABER outcome=eskaliert
    fn = sum(1 for a in with_outcomes
             if (a.get("score") or 0) < threshold and a["outcome"] == "eskaliert")

    stats.n_correct   = tp
    stats.n_false_pos = fp
    stats.n_false_neg = fn

    stats.precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    stats.recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    stats.f1 = (
        2 * stats.precision * stats.recall / (stats.precision + stats.recall)
        if (stats.precision + stats.recall) > 0 else 0.0
    )

    # Kalibrierungsfaktor: wenn Precision niedrig → Score nach unten korrigieren
    # Wenn Recall niedrig → Score nach oben (um mehr zu fangen)
    # Ziel: Precision ≥ 0.7 UND Recall ≥ 0.6
    if stats.precision < 0.5 and stats.n_outcomes >= _MIN_OUTCOMES:
        stats.calibration_factor = 0.85  # Zu viele False Positives → Score reduzieren
    elif stats.precision > 0.9 and stats.recall < 0.5 and stats.n_outcomes >= _MIN_OUTCOMES:
        stats.calibration_factor = 1.15  # Zu viele verpasste Events → Score erhöhen
    else:
        stats.calibration_factor = 1.0

    return stats


def calibration_report(
    days: int = _CALIBRATION_WINDOW_DAYS,
) -> CalibrationReport:
    """
    Berechnet vollständigen Kalibrierungsbericht.
    Braucht mindestens 5 Alerts mit eingetragenen Outcomes.

    Gibt CalibrationReport zurück.
    """
    alerts = _load_alerts_with_outcomes(days)
    report = CalibrationReport(
        generated=datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    )
    report.n_total_alerts  = len(alerts)
    report.n_with_outcomes = sum(1 for a in alerts
                                  if a.get("outcome") in ("eskaliert", "nicht_eskaliert"))
    report.n_open          = sum(1 for a in alerts if a.get("outcome") is None)

    if report.n_with_outcomes < _MIN_OUTCOMES:
        report.recommendation = (
            f"Zu wenig Outcomes für Kalibrierung ({report.n_with_outcomes}/{_MIN_OUTCOMES} nötig). "
            f"Bitte Outcomes für offene Alerts eintragen: "
            f"python nexus_calibration.py --show-open --record-outcome <ID> --outcome eskaliert"
        )
        return report

    # Threshold-Stats für verschiedene Schwellen berechnen
    thresholds = [20.0, 40.0, 55.0, 65.0, 75.0, 85.0]
    best_f1    = -1.0
    best_thr   = 65.0
    best_stats = None

    for thr in thresholds:
        stats = _compute_threshold_stats(alerts, thr)
        report.threshold_stats.append(stats)
        if stats.f1 > best_f1 and stats.n_outcomes >= _MIN_OUTCOMES:
            best_f1    = stats.f1
            best_thr   = thr
            best_stats = stats

    report.best_threshold       = best_thr
    report.calibration_factor   = best_stats.calibration_factor if best_stats else 1.0
    report.overall_precision    = best_stats.precision if best_stats else 0.0
    report.overall_recall       = best_stats.recall if best_stats else 0.0

    # Source-Reliability: Welche Quellen korrelierten mit echten Eskalationen
    source_counts: dict[str, dict] = {}
    for a in alerts:
        src = a.get("source", "unknown")
        if src not in source_counts:
            source_counts[src] = {"total": 0, "correct": 0}
        source_counts[src]["total"] += 1
        if a.get("outcome") == "eskaliert" and (a.get("score") or 0) >= best_thr:
            source_counts[src]["correct"] += 1

    for src, counts in source_counts.items():
        rel = counts["correct"] / counts["total"] if counts["total"] > 0 else 0.0
        report.source_reliability.append(SourceReliability(
            source=src,
            n_contributions=counts["total"],
            n_verified=counts["correct"],
            reliability_pct=rel * 100,
            worth_monitoring=counts["total"] >= 3 or rel > 0.3,
        ))
    report.source_reliability.sort(key=lambda s: -s.reliability_pct)

    # Empfehlung
    if report.overall_precision < 0.5:
        report.recommendation = (
            f"⚠️ Niedrige Precision ({report.overall_precision:.0%}): "
            f"Score-Schwelle von {best_thr:.0f} zu niedrig. "
            f"Empfehle Erhöhung auf {min(best_thr + 10, 90):.0f}."
        )
    elif report.overall_recall < 0.5:
        report.recommendation = (
            f"⚠️ Niedrige Recall ({report.overall_recall:.0%}): "
            f"NEXUS verpasst reale Events. "
            f"Empfehle Score-Schwelle auf {max(best_thr - 10, 20):.0f} senken."
        )
    elif report.overall_precision >= 0.7 and report.overall_recall >= 0.6:
        report.recommendation = (
            f"✅ Gute Kalibrierung: Precision={report.overall_precision:.0%}, "
            f"Recall={report.overall_recall:.0%}, F1={best_f1:.2f}. "
            f"Aktuelle Schwelle {best_thr:.0f} ist optimal."
        )
    else:
        report.recommendation = (
            f"Kalibrierung ausbaufähig: P={report.overall_precision:.0%}, "
            f"R={report.overall_recall:.0%}. Mehr Outcomes eintr..."
        )

    return report


def get_calibrated_score(raw_score: float, region: str = "") -> dict:
    """
    Gibt kalibrierten Score zurück.
    Wendet den aus historischen Daten berechneten Kalibrierungsfaktor an.

    Gibt zurück:
        {"raw": 75.0, "calibrated": 63.75, "factor": 0.85,
         "level": "HOCH", "reliable": True}

    Beispiel:
        result = get_calibrated_score(78.5, "hormuz")
        print(f"Kalibrierter Score: {result['calibrated']}")
    """
    report = calibration_report(days=30)  # Nur letzte 30 Tage
    factor = report.calibration_factor if report.n_with_outcomes >= _MIN_OUTCOMES else 1.0

    calibrated = raw_score * factor

    # Level aus kalibriertem Score
    level = "NORMAL"
    for lv, thr in sorted(_SCORE_THRESHOLDS.items(), key=lambda x: -x[1]):
        if calibrated >= thr:
            level = lv
            break

    return {
        "raw":        round(raw_score, 1),
        "calibrated": round(calibrated, 1),
        "factor":     round(factor, 3),
        "level":      level,
        "reliable":   report.n_with_outcomes >= _MIN_OUTCOMES,
        "n_outcomes": report.n_with_outcomes,
    }


# ─── Integration: Auto-Logging in nexus_escalation.py ────────────────────────

def auto_log_from_escalation(escalation_data: dict) -> Optional[int]:
    """
    Wird von nexus_live_server.py nach Escalation-Berechnung aufgerufen.
    Loggt automatisch wenn Score >= MITTEL-Schwelle.

    Gibt alert_id zurück oder None.
    """
    score  = float(escalation_data.get("combined_score") or
                   escalation_data.get("score") or 0)
    region = escalation_data.get("region") or escalation_data.get("geo_region") or ""
    level  = escalation_data.get("level") or ""
    reason = escalation_data.get("reason") or escalation_data.get("summary") or ""

    if score < _SCORE_THRESHOLDS.get("MITTEL", 40):
        return None  # Kein Alert nötig

    return log_alert(score=score, region=region, context=reason[:300],
                     source="nexus_escalation", level=level)


# ─── Datenbank-Hilfsfunktionen ────────────────────────────────────────────────

def calibration_db_stats() -> dict:
    """Gibt DB-Statistiken zurück."""
    try:
        db = _ensure_db()
        con = sqlite3.connect(str(db))
        n_total   = con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        n_open    = con.execute("SELECT COUNT(*) FROM alerts WHERE outcome IS NULL").fetchone()[0]
        n_esc     = con.execute("SELECT COUNT(*) FROM alerts WHERE outcome='eskaliert'").fetchone()[0]
        n_no_esc  = con.execute("SELECT COUNT(*) FROM alerts WHERE outcome='nicht_eskaliert'").fetchone()[0]
        con.close()
        coverage = round((n_esc + n_no_esc) / n_total * 100, 1) if n_total > 0 else 0.0
        return {
            "total_alerts":         n_total,
            "open_alerts":          n_open,
            "outcome_eskaliert":    n_esc,
            "outcome_nicht":        n_no_esc,
            "outcome_coverage_pct": coverage,
            "calibration_viable":   (n_esc + n_no_esc) >= _MIN_OUTCOMES,
        }
    except Exception:
        return {}


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="NEXUS Source Calibration + Feedback Loop",
        epilog=(
            "Workflow:\n"
            "  1. python nexus_calibration.py --show-open\n"
            "  2. python nexus_calibration.py --record-outcome 42 --outcome eskaliert\n"
            "  3. python nexus_calibration.py --report\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--log-alert",       action="store_true", help="Neuen Alert loggen")
    parser.add_argument("--score",           type=float, default=0.0)
    parser.add_argument("--region",          default="")
    parser.add_argument("--context",         default="")
    parser.add_argument("--show-open",       action="store_true", help="Offene Alerts anzeigen")
    parser.add_argument("--record-outcome",  type=int,  metavar="ALERT_ID")
    parser.add_argument("--outcome",         choices=["eskaliert","nicht_eskaliert","unklar"])
    parser.add_argument("--note",            default="", help="Notiz zum Outcome")
    parser.add_argument("--report",          action="store_true", help="Kalibrierungsbericht")
    parser.add_argument("--stats",           action="store_true", help="DB-Statistiken")
    parser.add_argument("--calibrate",       type=float, metavar="SCORE",
                        help="Score kalibrieren")
    parser.add_argument("--days",            type=int, default=_CALIBRATION_WINDOW_DAYS)
    parser.add_argument("--json",            action="store_true")
    args = parser.parse_args()

    if args.log_alert:
        if not args.score:
            print("Fehler: --score ist erforderlich")
        else:
            aid = log_alert(args.score, args.region, args.context)
            print(f"✅ Alert #{aid} geloggt. Score={args.score}, Region='{args.region}'")
            print(f"   Outcome später eintragen: --record-outcome {aid} --outcome eskaliert")

    elif args.show_open:
        alerts = show_open_alerts()
        print(f"\n=== Offene Alerts (ohne Outcome) ===")
        if not alerts:
            print("  ✅ Alle Alerts haben Outcomes.")
            print("  Tipp: --log-alert --score 75 --region hormuz um Test-Alert zu erstellen.")
        else:
            print(f"  {len(alerts)} Alerts warten auf Outcome-Eintragung:\n")
            for a in alerts[:20]:
                dt = datetime.fromtimestamp(a["ts"], tz=timezone.utc).strftime("%m-%d %H:%M")
                print(f"  [{a['id']:4d}] {dt}  {a['level']:8s}  "
                      f"Score={a['score']:.0f}  [{a.get('region','?')}]  "
                      f"{(a.get('context','') or '')[:50]}")

    elif args.record_outcome:
        if not args.outcome:
            parser.error("--outcome ist erforderlich")
        record_outcome(args.record_outcome, args.outcome, args.note)
        print(f"✅ Outcome '{args.outcome}' für Alert #{args.record_outcome} gespeichert.")

    elif args.report:
        report = calibration_report(args.days)
        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"\n=== NEXUS Kalibrierungsbericht ===")
            print(f"  Zeitraum:    letzte {args.days} Tage")
            print(f"  Alerts:      {report.n_total_alerts} gesamt, "
                  f"{report.n_with_outcomes} mit Outcome, "
                  f"{report.n_open} offen")
            print(f"  Precision:   {report.overall_precision:.0%}")
            print(f"  Recall:      {report.overall_recall:.0%}")
            print(f"  Beste Schw.: {report.best_threshold:.0f}")
            print(f"  Cal-Faktor:  {report.calibration_factor:.3f}")
            print(f"\n  {report.recommendation}")

            if report.threshold_stats:
                print(f"\n  Threshold-Analyse:")
                print(f"  {'Schwelle':>8}  {'Prec':>6}  {'Recall':>6}  {'F1':>6}  "
                      f"{'Faktor':>6}  {'N-Out':>6}")
                print(f"  {'─'*52}")
                for s in report.threshold_stats:
                    marker = " ← BEST" if s.threshold == report.best_threshold else ""
                    print(f"  {s.threshold:8.0f}  {s.precision:6.0%}  "
                          f"{s.recall:6.0%}  {s.f1:6.2f}  "
                          f"{s.calibration_factor:6.3f}  {s.n_outcomes:6d}{marker}")

            if report.source_reliability:
                print(f"\n  Quellen-Zuverlässigkeit:")
                for sr in report.source_reliability[:8]:
                    bar = "█" * int(sr.reliability_pct / 10)
                    ok  = "✅" if sr.reliability_pct >= 50 else "⚠️"
                    print(f"  {ok} {sr.source:30s}  "
                          f"{sr.reliability_pct:5.1f}%  {bar}")

    elif args.calibrate is not None:
        result = get_calibrated_score(args.calibrate, args.region)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"\n=== Score-Kalibrierung ===")
            print(f"  Roh-Score:        {result['raw']}")
            print(f"  Kalibriert:       {result['calibrated']}")
            print(f"  Faktor:           {result['factor']}")
            print(f"  Level:            {result['level']}")
            print(f"  Zuverlässig:      {'Ja' if result['reliable'] else 'Nein (zu wenig Outcomes)'}")
            print(f"  Outcomes vorhanden: {result['n_outcomes']}/{_MIN_OUTCOMES} Minimum")

    elif args.stats:
        s = calibration_db_stats()
        print("\n=== Calibration DB ===")
        for k, v in s.items():
            print(f"  {k:30s}: {v}")
    else:
        parser.print_help()
