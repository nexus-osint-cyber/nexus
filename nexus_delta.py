"""
NEXUS – Delta-Analyse / Persistentes Weltmodell (Stufe 3)
"Gestern 3 Explosionen, heute 12 → +300% Spike"

NEXUS merkt sich pro Region tagesaktuell:
  - Artikelanzahl nach Typ
  - Flugzeug-Alerts
  - Maritime Alerts
  - Erdbeben (Anzahl + max. Magnitude)
  - ACLED Konfliktereignisse
  - NASA FIRMS Feuerpunkte (FRP-Summe)

Vergleicht aktuellen Stand mit 7-Tage-Durchschnitt.
Gibt Spike-Alerts aus wenn ein Wert > 2x Durchschnitt steigt.
Schreibt Zusammenfassung in SQLite über nexus_memory.py Erweiterung.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "nexus_world.db"

# ── Datenbankschema ────────────────────────────────────────────────────────────

def init_delta_db() -> None:
    """Initialisiert die Delta-Datenbank."""
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS region_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                region      TEXT NOT NULL,
                date        TEXT NOT NULL,          -- YYYY-MM-DD
                hour        INTEGER NOT NULL,        -- 0-23
                ts          REAL NOT NULL,           -- Unix-Timestamp
                metrics     TEXT NOT NULL            -- JSON
            );
            CREATE INDEX IF NOT EXISTS idx_region_date
                ON region_snapshots(region, date);

            CREATE TABLE IF NOT EXISTS delta_alerts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          REAL NOT NULL,
                region      TEXT NOT NULL,
                metric      TEXT NOT NULL,
                value_now   REAL NOT NULL,
                value_avg   REAL NOT NULL,
                change_pct  REAL NOT NULL,
                severity    TEXT NOT NULL,
                message     TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_delta_region
                ON delta_alerts(region, ts);
        """)


# ── Snapshot speichern ─────────────────────────────────────────────────────────

def save_snapshot(
    region: str,
    articles: list = None,
    flight_data: dict = None,
    maritime_data: dict = None,
    earthquakes: list = None,
    fires: list = None,
    acled_events: list = None,
) -> dict:
    """
    Speichert einen Daten-Snapshot für eine Region.
    Gibt die gespeicherten Metriken zurück.
    """
    init_delta_db()

    now   = datetime.now(timezone.utc)
    date  = now.strftime("%Y-%m-%d")
    hour  = now.hour

    # Artikelmetriken
    arts = articles or []
    art_total    = len(arts)
    art_high_cred = sum(1 for a in arts if (a.get("credibility_score") or 0) >= 7)
    art_keywords  = _count_conflict_keywords(arts)

    # Flugdaten
    fl = flight_data or {}
    susp_flights = len(fl.get("suspicious", [])) if fl else 0

    # Maritime
    ma = maritime_data or {}
    maritime_alerts = ma.get("alert_count", 0) if ma else 0

    # Erdbeben
    quakes = earthquakes or []
    quake_count = len(quakes)
    quake_max_mag = max((q.get("mag", 0) for q in quakes), default=0)

    # Brände (FIRMS)
    fire_list = fires or []
    fire_count = len(fire_list)
    fire_frp_sum = sum(f.get("frp", 0) for f in fire_list)

    # ACLED
    acled = acled_events or []
    acled_count      = len(acled)
    acled_fatalities = sum(e.get("fatalities", 0) for e in acled)
    acled_critical   = sum(1 for e in acled if e.get("priority") == "KRITISCH")

    metrics = {
        "art_total":        art_total,
        "art_high_cred":    art_high_cred,
        "art_conflict_kw":  art_keywords,
        "susp_flights":     susp_flights,
        "maritime_alerts":  maritime_alerts,
        "quake_count":      quake_count,
        "quake_max_mag":    round(quake_max_mag, 1),
        "fire_count":       fire_count,
        "fire_frp_sum":     round(fire_frp_sum, 1),
        "acled_count":      acled_count,
        "acled_fatalities": acled_fatalities,
        "acled_critical":   acled_critical,
    }

    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO region_snapshots (region, date, hour, ts, metrics) VALUES (?,?,?,?,?)",
            (region.lower(), date, hour, time.time(), json.dumps(metrics))
        )

    return metrics


def _count_conflict_keywords(articles: list) -> int:
    """Zählt Konflikt-Keywords in Artikeln."""
    KEYWORDS = {
        "attack", "explosion", "strike", "missile", "rocket", "drone", "killed",
        "angriff", "explosion", "rakete", "drohne", "getötet", "beschuss",
        "обстрел", "взрыв", "ракета", "удар",   # Russisch
    }
    count = 0
    for a in articles:
        text = (a.get("title", "") + " " + a.get("summary", "")).lower()
        count += sum(1 for kw in KEYWORDS if kw in text)
    return count


# ── Delta-Berechnung ───────────────────────────────────────────────────────────

def get_7day_average(region: str, metric: str) -> Optional[float]:
    """Berechnet 7-Tage-Durchschnitt einer Metrik für eine Region."""
    since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            rows = conn.execute(
                "SELECT metrics FROM region_snapshots WHERE region=? AND date>=? ORDER BY ts DESC",
                (region.lower(), since)
            ).fetchall()
        if not rows:
            return None
        values = []
        for (m_json,) in rows:
            try:
                m = json.loads(m_json)
                v = m.get(metric)
                if v is not None:
                    values.append(float(v))
            except Exception:
                pass
        return sum(values) / len(values) if values else None
    except Exception:
        return None


def get_latest_snapshot(region: str) -> Optional[dict]:
    """Gibt den letzten gespeicherten Snapshot für eine Region zurück."""
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            row = conn.execute(
                "SELECT metrics, ts FROM region_snapshots WHERE region=? ORDER BY ts DESC LIMIT 1",
                (region.lower(),)
            ).fetchone()
        if row:
            return {"metrics": json.loads(row[0]), "ts": row[1]}
    except Exception:
        pass
    return None


def compute_delta(region: str, current_metrics: dict) -> list[dict]:
    """
    Vergleicht aktuelle Metriken mit 7-Tage-Durchschnitt.
    Gibt Liste von signifikanten Delta-Ereignissen zurück.
    """
    SPIKE_THRESHOLD = 2.0    # 200% des Durchschnitts → Spike
    MIN_ABSOLUTE = {         # Mindest-Absolutwert damit Spike relevant ist
        "art_total":       3,
        "art_conflict_kw": 5,
        "susp_flights":    1,
        "maritime_alerts": 1,
        "quake_count":     1,
        "fire_count":      2,
        "acled_count":     1,
        "acled_critical":  1,
    }
    METRIC_LABELS = {
        "art_total":        "Artikel gesamt",
        "art_conflict_kw":  "Konflikt-Keywords in Artikeln",
        "susp_flights":     "auffällige Flugzeuge",
        "maritime_alerts":  "Maritime Alerts",
        "quake_count":      "Erdbeben",
        "fire_count":       "NASA Brandmeldungen",
        "fire_frp_sum":     "Feuerradianz (FRP MW)",
        "acled_count":      "ACLED Konfliktereignisse",
        "acled_critical":   "Kritische Konfliktereignisse",
        "acled_fatalities": "Bestätigte Todesopfer (ACLED)",
    }

    alerts = []
    for metric, label in METRIC_LABELS.items():
        current = current_metrics.get(metric)
        if current is None:
            continue
        avg = get_7day_average(region, metric)
        if avg is None or avg < 0.1:
            continue   # Kein Historien-Durchschnitt

        change_pct = ((current - avg) / avg) * 100
        min_abs = MIN_ABSOLUTE.get(metric, 1)

        if current >= min_abs and change_pct >= (SPIKE_THRESHOLD - 1) * 100:
            severity = "KRITISCH" if change_pct >= 400 else "HOCH" if change_pct >= 200 else "MITTEL"
            message  = (
                f"{label}: {current:.0f} (Ø 7d: {avg:.1f}) "
                f"→ +{change_pct:.0f}%"
            )
            alerts.append({
                "metric":     metric,
                "label":      label,
                "value_now":  current,
                "value_avg":  avg,
                "change_pct": change_pct,
                "severity":   severity,
                "message":    message,
            })

    # Auch starke Rückgänge bei ACLED melden
    for metric in ("acled_count", "acled_critical"):
        current = current_metrics.get(metric, 0)
        avg = get_7day_average(region, metric)
        if avg and avg > 5 and current < avg * 0.3:
            alerts.append({
                "metric":     metric,
                "label":      METRIC_LABELS.get(metric, metric),
                "value_now":  current,
                "value_avg":  avg,
                "change_pct": ((current - avg) / avg) * 100,
                "severity":   "INFO",
                "message":    f"{METRIC_LABELS.get(metric, metric)}: stark zurückgegangen ({current:.0f} vs Ø {avg:.1f})",
            })

    # Sortieren nach Schwere
    sev_order = {"KRITISCH": 0, "HOCH": 1, "MITTEL": 2, "INFO": 3}
    alerts.sort(key=lambda x: sev_order.get(x["severity"], 9))

    # In DB speichern
    if alerts:
        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                for al in alerts:
                    conn.execute(
                        "INSERT INTO delta_alerts (ts,region,metric,value_now,value_avg,change_pct,severity,message) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (time.time(), region.lower(), al["metric"],
                         al["value_now"], al["value_avg"], al["change_pct"],
                         al["severity"], al["message"])
                    )
        except Exception:
            pass

    return alerts


def delta_text_summary(region: str, alerts: list[dict]) -> str:
    """Gibt formatierte Delta-Zusammenfassung für LLM-Kontext zurück."""
    if not alerts:
        return ""
    lines = [f"\n[NEXUS DELTA-ANALYSE: {region} — Veränderungen vs. 7-Tage-Durchschnitt]"]
    for al in alerts:
        icon = {"KRITISCH": "🔴", "HOCH": "🟠", "MITTEL": "🟡", "INFO": "🔵"}.get(al["severity"], "⚪")
        lines.append(f"  {icon} [{al['severity']}] {al['message']}")
    return "\n".join(lines)


def delta_terminal_output(region: str, alerts: list[dict]) -> None:
    """Gibt Delta-Alerts farbig im Terminal aus."""
    if not alerts:
        return
    colors = {"KRITISCH": "\033[91m", "HOCH": "\033[93m", "MITTEL": "\033[96m", "INFO": "\033[94m"}
    reset = "\033[0m"
    print(f"\n\033[95m[NEXUS DELTA] {region.upper()} — Signifikante Veränderungen:{reset}", flush=True)
    for al in alerts:
        c = colors.get(al["severity"], "")
        print(f"  {c}[{al['severity']}] {al['message']}{reset}", flush=True)


def get_region_history(region: str, days: int = 14) -> list[dict]:
    """Gibt historische Snapshots einer Region zurück (für Trendanzeige)."""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            rows = conn.execute(
                "SELECT date, hour, metrics, ts FROM region_snapshots "
                "WHERE region=? AND date>=? ORDER BY ts ASC",
                (region.lower(), since)
            ).fetchall()
        result = []
        for (date, hour, m_json, ts) in rows:
            try:
                result.append({
                    "date":    date,
                    "hour":    hour,
                    "ts":      ts,
                    "metrics": json.loads(m_json),
                })
            except Exception:
                pass
        return result
    except Exception:
        return []


def cleanup_old_snapshots(days: int = 30) -> None:
    """Löscht Snapshots die älter als N Tage sind."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("DELETE FROM region_snapshots WHERE date<?", (cutoff,))
            conn.execute("DELETE FROM delta_alerts WHERE ts<?", (time.time() - days * 86400,))
    except Exception:
        pass


# ── Direktaufruf zum Testen ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("NEXUS Delta-Analyse Test")
    print("─" * 50)
    init_delta_db()

    # Test-Snapshot speichern
    test_metrics = save_snapshot(
        region="Ukraine",
        articles=[{"title": "Explosion in Kharkiv kills 5", "credibility_score": 8}] * 12,
        flight_data={"suspicious": [{"callsign": "TEST1"}, {"callsign": "TEST2"}]},
        maritime_data={"alert_count": 3},
    )
    print("Gespeicherte Metriken:", test_metrics)

    # Delta berechnen (braucht Historien-Daten)
    history = get_region_history("Ukraine")
    print(f"Historische Snapshots Ukraine: {len(history)}")
    print("\n✅ Delta-Datenbank initialisiert.")
