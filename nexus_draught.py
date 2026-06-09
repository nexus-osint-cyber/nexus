"""
NEXUS - Schiffs-Tiefgang Delta Detektor
Verfolgt AIS-Tiefgangsaenderungen per MMSI.
Delta-Tiefgang = moeglicherweise Ladungstransfer, Bunkering oder Ship-to-Ship.

Relevanz:
  - Tiefgang sinkt um >2m in offenem Meer:  STS-Transfer (Ship-to-Ship) moeglich
  - Tiefgang sinkt in Konflikthafen:        Entladung/Munitionslieferung
  - Tiefgang steigt bei bekanntem Sanktionsschiff: verdaechtige Beladung

Datenquelle: AIS-Meldungen via nexus_ais.py / nexus_maritime.py
Persistence:  SQLite (nexus_draught.db)
"""

from __future__ import annotations

import sqlite3
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

# Datenbankpfad (im gleichen Verzeichnis wie nexus_*)
_DB_PATH = os.path.join(os.path.dirname(__file__), "nexus_draught.db")

# Schwellenwerte
DELTA_ALERT_M      = 2.0   # Tiefgangsaenderung >= 2m = Alert
DELTA_SUSPECT_M    = 1.0   # >= 1m = verdaechtig
STS_ZONE_KM        = 50    # Kein Hafen im Umkreis = offenes Meer -> STS-Verdacht
MAX_SNAPSHOT_AGE_H = 72    # Nur Vergleiche innerhalb 72 Stunden

# OSINT-Hochrisiko-Flaggen (Schiffe die Sanktionslisten oder Greylists zugeordnet sind)
# Wird idealerweise aus nexus_watchlist.py gespeist
_WATCHLIST_MMSI: set[str] = set()


def _init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draught_snapshots (
            mmsi        TEXT NOT NULL,
            name        TEXT,
            lat         REAL,
            lon         REAL,
            draught_m   REAL NOT NULL,
            timestamp   REAL NOT NULL,   -- Unix timestamp
            source      TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draught_alerts (
            mmsi        TEXT NOT NULL,
            name        TEXT,
            lat         REAL,
            lon         REAL,
            draught_old REAL,
            draught_new REAL,
            delta_m     REAL,
            event_type  TEXT,   -- 'STS', 'ENTLADUNG', 'BELADUNG', 'BUNKER_VERDACHT'
            confidence  TEXT,   -- 'high'/'medium'/'low'
            hint        TEXT,
            timestamp   REAL NOT NULL,
            acknowledged INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mmsi ON draught_snapshots(mmsi)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ts   ON draught_snapshots(timestamp)")
    conn.commit()
    return conn


def _get_db() -> sqlite3.Connection:
    return _init_db()


def _nearest_port(lat: float, lon: float) -> Optional[str]:
    """
    Prueft ob (lat, lon) nahe einem bekannten Hafen liegt (< 30km).
    Grobe Heuristik fuer STS-Erkennung.
    """
    import math

    PORTS = [
        # (lat, lon, name)
        (29.87, 48.47, "Kuwait City"),
        (26.21, 50.58, "Bahrain"),
        (25.28, 55.30, "Dubai/Jebel Ali"),
        (23.62, 58.59, "Maskat (Oman)"),
        (27.10, 49.61, "Dammam (Saudi-Arabien)"),
        (30.06, 31.25, "Alexandria"),
        (29.97, 32.57, "Suez"),
        (32.08, 34.77, "Ashdod"),
        (33.90, 35.49, "Beirut"),
        (37.02, 22.11, "Kalamata"),
        (37.94, 23.63, "Piräus"),
        (51.90,  4.47, "Rotterdam"),
        (53.55,  9.99, "Hamburg"),
        (37.50, 126.60, "Incheon"),
        (31.23, 121.47, "Shanghai"),
        (22.28, 114.17, "Hongkong"),
        (1.26,  103.82, "Singapur"),
        (13.09,  80.29, "Chennai"),
        (18.96,  72.82, "Mumbai"),
        (6.45,   3.39, "Lagos"),
        (-33.92, 18.42, "Kapstadt"),
        (3.14,  101.69, "Klang (Malaysia)"),
    ]

    def haversine(la1, lo1, la2, lo2):
        R = 6371
        dlat = math.radians(la2 - la1)
        dlon = math.radians(lo2 - lo1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(la1)) * math.cos(math.radians(la2)) * math.sin(dlon/2)**2
        return R * 2 * math.asin(math.sqrt(a))

    for plat, plon, pname in PORTS:
        if haversine(lat, lon, plat, plon) < 35:
            return pname
    return None


def record_draught(vessels: list[dict]) -> list[dict]:
    """
    Nimmt Liste von Schiffs-Dicts (mit mmsi, draught_m, lat, lon, name)
    und schreibt Snapshots in DB. Gibt neue Alerts zurueck.
    """
    conn = _get_db()
    now  = time.time()
    alerts: list[dict] = []
    cutoff = now - MAX_SNAPSHOT_AGE_H * 3600

    for v in vessels:
        mmsi     = str(v.get("mmsi") or "").strip()
        draught  = v.get("draught_m") or v.get("draught") or 0.0
        lat      = v.get("lat")
        lon      = v.get("lon")
        name     = v.get("name") or v.get("vessel_name") or "?"

        if not mmsi or not draught or draught < 0.5:
            continue  # Kein MMSI oder kein gueltiger Tiefgang

        # Letzten Snapshot fuer diese MMSI holen
        row = conn.execute(
            "SELECT draught_m, timestamp, lat, lon FROM draught_snapshots "
            "WHERE mmsi=? AND timestamp>=? ORDER BY timestamp DESC LIMIT 1",
            (mmsi, cutoff)
        ).fetchone()

        if row:
            old_draught, old_ts, old_lat, old_lon = row
            delta = draught - old_draught  # positiv = schwerer, negativ = leichter

            if abs(delta) >= DELTA_SUSPECT_M:
                # Alert erzeugen
                conf = "high" if abs(delta) >= DELTA_ALERT_M else "medium"

                # Ereignistyp bestimmen
                near_port = _nearest_port(lat, lon) if lat and lon else None
                if delta < -DELTA_ALERT_M and not near_port:
                    event_type = "STS"  # Ship-to-Ship in offenem Meer
                    hint = (
                        f"Tiefgang von {old_draught:.1f}m auf {draught:.1f}m gesunken "
                        f"({delta:.1f}m) – kein Hafen in der Naehe. "
                        f"Moeglicher Ship-to-Ship Transfer (STS)."
                    )
                elif delta < 0:
                    event_type = "ENTLADUNG"
                    hint = (
                        f"Tiefgang um {abs(delta):.1f}m gesunken "
                        f"({old_draught:.1f}m -> {draught:.1f}m)"
                        + (f" nahe {near_port}" if near_port else "")
                        + ". Ladung wurde geloescht."
                    )
                elif delta > DELTA_ALERT_M:
                    event_type = "BELADUNG"
                    hint = (
                        f"Tiefgang um {delta:.1f}m gestiegen "
                        f"({old_draught:.1f}m -> {draught:.1f}m)"
                        + (f" nahe {near_port}" if near_port else "")
                        + ". Schwere Beladung."
                    )
                else:
                    event_type = "BUNKER_VERDACHT"
                    hint = (
                        f"Tiefgang +{delta:.1f}m – moeglicherweise Bunkern/Betankung "
                        f"oder leichte Beladung."
                    )

                # Watchlist-Treffer: Konfidenz hochstufen
                if mmsi in _WATCHLIST_MMSI:
                    conf = "high"
                    hint = "[WATCHLIST] " + hint

                alert = {
                    "mmsi":        mmsi,
                    "name":        name,
                    "lat":         lat,
                    "lon":         lon,
                    "draught_old": old_draught,
                    "draught_new": draught,
                    "delta_m":     round(delta, 2),
                    "event_type":  event_type,
                    "confidence":  conf,
                    "hint":        hint,
                    "timestamp":   datetime.fromtimestamp(now, tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
                }
                alerts.append(alert)
                conn.execute("""
                    INSERT INTO draught_alerts
                    (mmsi, name, lat, lon, draught_old, draught_new, delta_m,
                     event_type, confidence, hint, timestamp)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, (mmsi, name, lat, lon, old_draught, draught, round(delta,2),
                      event_type, conf, hint, now))

        # Neuen Snapshot eintragen
        conn.execute(
            "INSERT INTO draught_snapshots (mmsi, name, lat, lon, draught_m, timestamp) "
            "VALUES (?,?,?,?,?,?)",
            (mmsi, name, lat, lon, draught, now)
        )
        # Alte Snapshots aufraeumen (> 7 Tage)
        conn.execute("DELETE FROM draught_snapshots WHERE mmsi=? AND timestamp < ?",
                     (mmsi, now - 7 * 86400))

    conn.commit()
    conn.close()
    return alerts


def get_recent_alerts(hours: int = 48) -> list[dict]:
    """Gibt aktuelle Tiefgang-Alerts aus DB zurueck."""
    conn = _get_db()
    cutoff = time.time() - hours * 3600
    rows = conn.execute(
        "SELECT mmsi, name, lat, lon, draught_old, draught_new, delta_m, "
        "event_type, confidence, hint, timestamp FROM draught_alerts "
        "WHERE timestamp >= ? AND acknowledged = 0 ORDER BY timestamp DESC",
        (cutoff,)
    ).fetchall()
    conn.close()
    alerts = []
    for r in rows:
        alerts.append({
            "mmsi":        r[0],
            "name":        r[1],
            "lat":         r[2],
            "lon":         r[3],
            "draught_old": r[4],
            "draught_new": r[5],
            "delta_m":     r[6],
            "event_type":  r[7],
            "confidence":  r[8],
            "hint":        r[9],
            "timestamp":   datetime.fromtimestamp(r[10], tz=timezone.utc).strftime(
                               "%d.%m.%Y %H:%M UTC"
                           ) if isinstance(r[10], (int, float)) else str(r[10]),
        })
    return alerts


def draught_for_map(region: str) -> list[dict]:
    """Gibt Tiefgang-Alert-Marker fuer die Live-Karte zurueck."""
    alerts = get_recent_alerts(hours=48)
    markers = []
    for a in alerts:
        if not a.get("lat") or not a.get("lon"):
            continue
        markers.append({
            "lat":        a["lat"],
            "lon":        a["lon"],
            "mmsi":       a["mmsi"],
            "name":       a["name"],
            "delta_m":    a["delta_m"],
            "event_type": a["event_type"],
            "confidence": a["confidence"],
            "hint":       a["hint"],
            "timestamp":  a["timestamp"],
        })
    return markers


def draught_summary(region: str) -> str:
    """Text-Zusammenfassung fuer LLM."""
    alerts = get_recent_alerts(hours=48)
    if not alerts:
        return "[TIEFGANG] Keine verdaechtigen Tiefgangsaenderungen (letzte 48h)."
    conf_icons = {"high": "🔴", "medium": "🟡", "low": "⚪"}
    lines = [f"[TIEFGANG-DELTA – letzte 48h | {len(alerts)} Alert(s)]"]
    for a in alerts[:5]:
        icon = conf_icons.get(a["confidence"], "?")
        lines.append(
            f"  {icon} {a['name']} (MMSI {a['mmsi']}) | {a['event_type']} "
            f"| Delta {a['delta_m']:+.1f}m | {a['timestamp']}"
        )
        lines.append(f"    {a['hint']}")
    return "\n".join(lines)


# ── Direktaufruf zum Testen ──────────────────────────────────────────────────
if __name__ == "__main__":
    # Simulierter Test mit zwei Snapshots
    print("NEXUS Tiefgang-Delta Test")
    test_vessels = [
        {"mmsi": "123456789", "name": "MV TEST", "lat": 25.5, "lon": 55.0,
         "draught_m": 8.5},
    ]
    alerts1 = record_draught(test_vessels)
    print(f"Snapshot 1: {len(alerts1)} Alerts (erwartet: 0)")

    import time; time.sleep(1)  # Zeitstempel-Abstand

    test_vessels[0]["draught_m"] = 4.5  # Schiff wurde entladen
    test_vessels[0]["lat"] = 25.8       # Slight position change
    alerts2 = record_draught(test_vessels)
    print(f"Snapshot 2: {len(alerts2)} Alerts (erwartet: 1)")
    for a in alerts2:
        print(f"  [{a['confidence'].upper()}] {a['event_type']}: {a['hint']}")
    print()
    print(draught_summary("test"))
