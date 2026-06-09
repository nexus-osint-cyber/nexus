"""
nexus_patrol.py  — T174
Pattern-of-Life Engine für NEXUS.

Erkennt Abweichungen vom Normalverhalten von Entitäten (Schiffe, Flugzeuge)
durch Vergleich aktueller Positionen/Geschwindigkeiten mit historischer Baseline.

Basiert auf nexus_timeseries.py (T173) — braucht mindestens 24h gespeicherte Daten
um sinnvolle Baselines zu berechnen.

Anomalie-Typen:
  REGION_CHANGE   — Entität in ungewöhnlicher Region
  SPEED_ANOMALY   — Geschwindigkeit stark abweichend vom Median
  ANCHOR_DRIFT    — Normalerweise ankerndes Schiff ist plötzlich schnell
  SPEED_STOP      — Normalerweise fahrendes Schiff liegt still
  NEW_ENTITY      — Entität zum ersten Mal sichtbar

Verwendung:
  from nexus_patrol import patrol_anomalies, patrol_for_map
  python nexus_patrol.py --hours 72 --type ship
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# ─── Abhängigkeiten ───────────────────────────────────────────────────────────
try:
    from nexus_timeseries import get_entity_history, DB_PATH
    _TS_AVAILABLE = True
except ImportError:
    _TS_AVAILABLE = False

# ─── Konfiguration ────────────────────────────────────────────────────────────

# Mindest-Datenpunkte für eine valide Baseline
MIN_BASELINE_POINTS = 5

# Baseline-Fenster: wie viele Stunden Vergangenheit für "Normal"
BASELINE_HOURS = 168   # 7 Tage

# Aktuelles Fenster: letzten N Stunden für "Jetzt"
CURRENT_HOURS  = 6

# Anomalie-Schwellen
SPEED_SIGMA_THRESHOLD = 2.5    # Abweichung vom Median in Einheiten der MAD
ANCHOR_SPEED_THRESHOLD = 2.0   # Knoten – darunter = ankerndes Schiff
REGION_CHANGE_GRACE = 12       # Stunden – Regionswechsel innerhalb dieser Zeit ignorieren

# Prioritäts-Icons
_LEVEL_ICONS = {
    "KRITISCH": "🔴",
    "HOCH":     "🟠",
    "MITTEL":   "🟡",
    "NIEDRIG":  "🟢",
}

# ─── Datentypen ───────────────────────────────────────────────────────────────

@dataclass
class PatrolAnomaly:
    entity_id:    str
    entity_type:  str         # "ship" | "aircraft"
    anomaly_type: str         # REGION_CHANGE, SPEED_ANOMALY, etc.
    level:        str         # KRITISCH / HOCH / MITTEL / NIEDRIG
    description:  str
    lat:          float = 0.0
    lon:          float = 0.0
    region:       str   = ""
    name:         str   = ""
    baseline_val: float = 0.0
    current_val:  float = 0.0
    ts:           float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "entity_id":    self.entity_id,
            "entity_type":  self.entity_type,
            "anomaly_type": self.anomaly_type,
            "level":        self.level,
            "description":  self.description,
            "lat":          self.lat,
            "lon":          self.lon,
            "region":       self.region,
            "name":         self.name,
            "baseline_val": round(self.baseline_val, 2),
            "current_val":  round(self.current_val, 2),
            "ts":           self.ts,
            "ts_fmt":       datetime.fromtimestamp(self.ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        }


@dataclass
class PatrolReport:
    anomalies:   list[PatrolAnomaly] = field(default_factory=list)
    n_entities:  int = 0
    n_with_data: int = 0
    level:       str = "NORMAL"
    generated:   str = ""

    def to_dict(self) -> dict:
        return {
            "anomalies":   [a.to_dict() for a in self.anomalies],
            "n_entities":  self.n_entities,
            "n_with_data": self.n_with_data,
            "level":       self.level,
            "generated":   self.generated,
            "n_anomalies": len(self.anomalies),
        }


# ─── Hilfsfunktionen ─────────────────────────────────────────────────────────

def _mad(values: list[float]) -> float:
    """Median Absolute Deviation — robust gegen Ausreißer."""
    if not values:
        return 0.0
    med = statistics.median(values)
    return statistics.median([abs(v - med) for v in values])


def _anomaly_level(sigma: float) -> str:
    if sigma >= 5.0:
        return "KRITISCH"
    elif sigma >= 3.5:
        return "HOCH"
    elif sigma >= 2.5:
        return "MITTEL"
    else:
        return "NIEDRIG"


def _region_from_coords(lat: float, lon: float) -> str:
    """Schnelle Bbox-basierte Regionszuordnung."""
    regions = {
        "hormuz":   (24.0, 55.0, 27.5, 60.0),
        "taiwan":   (21.0, 119.0, 27.0, 123.0),
        "bab_el":   (11.0, 42.0, 13.5, 44.5),
        "malacca":  (1.0, 99.5, 6.5, 104.5),
        "suez":     (28.0, 32.0, 32.0, 34.5),
        "black_sea":(41.0, 28.0, 46.5, 41.5),
        "baltic":   (54.0, 14.0, 66.0, 30.0),
        "ukraine":  (44.0, 22.0, 52.5, 40.0),
    }
    for name, (lat_min, lon_min, lat_max, lon_max) in regions.items():
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return name
    return ""


# ─── Baseline-Berechnung ─────────────────────────────────────────────────────

def _compute_baseline(entity_id: str) -> Optional[dict]:
    """
    Berechnet Baseline-Verhalten einer Entität aus historischen Daten.
    Gibt None zurück wenn zu wenig Daten vorhanden.

    Baseline enthält:
      - median_speed, mad_speed
      - dominant_region (Region wo am häufigsten gesehen)
      - known_regions (alle bekannten Regionen)
      - median_lat, median_lon (gewohntes Operationsgebiet)
      - typical_state: "moving" | "anchoring" | "mixed"
      - n_points: Anzahl der Datenpunkte
    """
    if not _TS_AVAILABLE:
        return None

    history = get_entity_history(entity_id, BASELINE_HOURS)
    if len(history) < MIN_BASELINE_POINTS:
        return None

    speeds  = [p["speed"] for p in history if p["speed"] is not None]
    lats    = [p["lat"] for p in history]
    lons    = [p["lon"] for p in history]
    regions = [p["region"] for p in history if p["region"]]

    if not speeds:
        return None

    median_speed = statistics.median(speeds)
    mad_speed    = _mad(speeds)

    # Dominant region (häufigste)
    region_counts: dict[str, int] = {}
    for r in regions:
        region_counts[r] = region_counts.get(r, 0) + 1
    dominant_region = max(region_counts, key=region_counts.get) if region_counts else ""

    # Typischer Zustand
    anchoring_pct = sum(1 for s in speeds if s < ANCHOR_SPEED_THRESHOLD) / len(speeds)
    if anchoring_pct > 0.7:
        typical_state = "anchoring"
    elif anchoring_pct < 0.3:
        typical_state = "moving"
    else:
        typical_state = "mixed"

    return {
        "median_speed":    median_speed,
        "mad_speed":       max(mad_speed, 0.5),  # mind. 0.5 kn als Rauschen
        "dominant_region": dominant_region,
        "known_regions":   list(set(regions)),
        "median_lat":      statistics.median(lats),
        "median_lon":      statistics.median(lons),
        "typical_state":   typical_state,
        "anchoring_pct":   anchoring_pct,
        "n_points":        len(history),
    }


# ─── Anomalie-Detektion ───────────────────────────────────────────────────────

def _check_entity(entity_id: str, entity_type: str,
                  current_pos: dict, baseline: dict) -> list[PatrolAnomaly]:
    """Vergleicht aktuelle Position mit Baseline und gibt Anomalien zurück."""
    anomalies: list[PatrolAnomaly] = []
    now = time.time()

    cur_speed  = current_pos.get("speed", 0) or 0
    cur_lat    = current_pos.get("lat", 0)
    cur_lon    = current_pos.get("lon", 0)
    cur_region = current_pos.get("region", "") or _region_from_coords(cur_lat, cur_lon)
    meta       = json.loads(current_pos.get("meta", "{}") or "{}")
    name       = meta.get("name", entity_id)

    # ── 1. Regionswechsel ────────────────────────────────────────────────────
    dom_region = baseline["dominant_region"]
    known_regions = baseline["known_regions"]
    if dom_region and cur_region and cur_region not in known_regions:
        anomalies.append(PatrolAnomaly(
            entity_id=entity_id,
            entity_type=entity_type,
            anomaly_type="REGION_CHANGE",
            level="HOCH",
            description=(
                f"{'🚢' if entity_type == 'ship' else '✈️'} {name}: "
                f"Normalerweise in '{dom_region}', jetzt in '{cur_region}'"
            ),
            lat=cur_lat, lon=cur_lon,
            region=cur_region, name=name,
            baseline_val=0.0, current_val=0.0,
            ts=now,
        ))

    # ── 2. Geschwindigkeits-Anomalie ─────────────────────────────────────────
    med_spd = baseline["median_speed"]
    mad_spd = baseline["mad_speed"]
    if mad_spd > 0:
        sigma = abs(cur_speed - med_spd) / mad_spd
        if sigma >= SPEED_SIGMA_THRESHOLD:
            level = _anomaly_level(sigma)
            direction = "zu schnell" if cur_speed > med_spd else "zu langsam"
            anomalies.append(PatrolAnomaly(
                entity_id=entity_id,
                entity_type=entity_type,
                anomaly_type="SPEED_ANOMALY",
                level=level,
                description=(
                    f"{'🚢' if entity_type == 'ship' else '✈️'} {name}: "
                    f"Geschwindigkeit {cur_speed:.1f}kn ist {direction} "
                    f"(Baseline: {med_spd:.1f}±{mad_spd:.1f}kn, σ={sigma:.1f})"
                ),
                lat=cur_lat, lon=cur_lon,
                region=cur_region, name=name,
                baseline_val=med_spd, current_val=cur_speed,
                ts=now,
            ))

    # ── 3. Anchor-Drift (normalerweise ruhig, jetzt schnell) ─────────────────
    if (baseline["typical_state"] == "anchoring"
            and cur_speed > ANCHOR_SPEED_THRESHOLD * 3):
        anomalies.append(PatrolAnomaly(
            entity_id=entity_id,
            entity_type=entity_type,
            anomaly_type="ANCHOR_DRIFT",
            level="HOCH",
            description=(
                f"🚢 {name}: Normalerweise ankerndes Schiff jetzt mit "
                f"{cur_speed:.1f}kn unterwegs"
            ),
            lat=cur_lat, lon=cur_lon,
            region=cur_region, name=name,
            baseline_val=baseline["median_speed"],
            current_val=cur_speed,
            ts=now,
        ))

    # ── 4. Speed-Stop (normalerweise fahrend, jetzt still) ───────────────────
    if (baseline["typical_state"] == "moving"
            and baseline["median_speed"] > 5.0
            and cur_speed < ANCHOR_SPEED_THRESHOLD):
        anomalies.append(PatrolAnomaly(
            entity_id=entity_id,
            entity_type=entity_type,
            anomaly_type="SPEED_STOP",
            level="MITTEL",
            description=(
                f"{'🚢' if entity_type == 'ship' else '✈️'} {name}: "
                f"Normalerweise fahrend ({baseline['median_speed']:.1f}kn), "
                f"jetzt gestoppt ({cur_speed:.1f}kn)"
            ),
            lat=cur_lat, lon=cur_lon,
            region=cur_region, name=name,
            baseline_val=baseline["median_speed"],
            current_val=cur_speed,
            ts=now,
        ))

    return anomalies


def _get_latest_positions(entity_type: str, hours: float = CURRENT_HOURS) -> dict[str, dict]:
    """
    Holt aktuellste Position jeder Entität des gegebenen Typs
    aus der Zeitreihendatenbank.
    """
    if not _TS_AVAILABLE:
        return {}

    import sqlite3
    from nexus_timeseries import DB_PATH

    cutoff = time.time() - hours * 3600
    try:
        con = sqlite3.connect(str(DB_PATH), timeout=5)
        con.row_factory = sqlite3.Row
        # Neueste Position pro entity_id
        rows = con.execute(
            """
            SELECT ep.*
            FROM entity_positions ep
            INNER JOIN (
                SELECT entity_id, MAX(ts) as max_ts
                FROM entity_positions
                WHERE entity_type = ? AND ts >= ?
                GROUP BY entity_id
            ) latest ON ep.entity_id = latest.entity_id AND ep.ts = latest.max_ts
            """,
            (entity_type, cutoff),
        ).fetchall()
        con.close()
        return {r["entity_id"]: dict(r) for r in rows}
    except Exception:
        return {}


def _get_new_entities(entity_type: str,
                      new_hours: float = 24,
                      grace_hours: float = 168) -> list[dict]:
    """
    Findet Entitäten die in den letzten new_hours aufgetaucht sind
    aber davor (bis grace_hours) nie gesehen wurden.
    """
    if not _TS_AVAILABLE:
        return []

    import sqlite3
    from nexus_timeseries import DB_PATH

    now = time.time()
    new_cutoff   = now - new_hours  * 3600
    grace_cutoff = now - grace_hours * 3600

    try:
        con = sqlite3.connect(str(DB_PATH), timeout=5)
        con.row_factory = sqlite3.Row

        # Entitäten die in den letzten new_hours auftauchten
        recent = con.execute(
            "SELECT DISTINCT entity_id FROM entity_positions "
            "WHERE entity_type=? AND ts>=?",
            (entity_type, new_cutoff),
        ).fetchall()
        recent_ids = {r[0] for r in recent}

        # Welche davon hatten vorher (grace_cutoff bis new_cutoff) KEINE Einträge
        new_entities = []
        for eid in recent_ids:
            old_count = con.execute(
                "SELECT COUNT(*) FROM entity_positions "
                "WHERE entity_id=? AND ts>=? AND ts<?",
                (eid, grace_cutoff, new_cutoff),
            ).fetchone()[0]
            if old_count == 0:
                # Hol die neueste Position
                row = con.execute(
                    "SELECT * FROM entity_positions WHERE entity_id=? "
                    "ORDER BY ts DESC LIMIT 1",
                    (eid,),
                ).fetchone()
                if row:
                    new_entities.append(dict(row))

        con.close()
        return new_entities
    except Exception:
        return []


# ─── Haupt-Analyse ───────────────────────────────────────────────────────────

def patrol_anomalies(
    entity_types: list[str] | None = None,
    max_entities: int = 200,
) -> PatrolReport:
    """
    Analysiert alle bekannten Entitäten auf Verhaltensabweichungen.

    entity_types: ["ship", "aircraft"] oder None für alle
    max_entities: maximale Anzahl zu prüfender Entitäten

    Gibt PatrolReport zurück.
    """
    if entity_types is None:
        entity_types = ["ship", "aircraft"]

    if not _TS_AVAILABLE:
        return PatrolReport(
            generated=datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            level="NORMAL",
        )

    all_anomalies: list[PatrolAnomaly] = []
    n_entities  = 0
    n_with_data = 0

    for etype in entity_types:
        # Aktuelle Positionen holen
        current_positions = _get_latest_positions(etype, CURRENT_HOURS)
        n_entities += len(current_positions)

        checked = 0
        for entity_id, cur_pos in current_positions.items():
            if checked >= max_entities:
                break
            checked += 1

            baseline = _compute_baseline(entity_id)
            if baseline is None:
                continue

            n_with_data += 1
            anomalies = _check_entity(entity_id, etype, cur_pos, baseline)
            all_anomalies.extend(anomalies)

        # Neu aufgetauchte Entitäten
        new_entities = _get_new_entities(etype, new_hours=CURRENT_HOURS, grace_hours=72)
        for pos in new_entities:
            meta  = json.loads(pos.get("meta", "{}") or "{}")
            name  = meta.get("name", pos["entity_id"])
            region = pos.get("region", "") or _region_from_coords(pos["lat"], pos["lon"])
            all_anomalies.append(PatrolAnomaly(
                entity_id=pos["entity_id"],
                entity_type=etype,
                anomaly_type="NEW_ENTITY",
                level="NIEDRIG",
                description=(
                    f"{'🚢' if etype == 'ship' else '✈️'} {name}: "
                    f"Zum ersten Mal in den letzten 72h gesichtet"
                    + (f" in {region}" if region else "")
                ),
                lat=pos["lat"], lon=pos["lon"],
                region=region, name=name,
                ts=pos["ts"],
            ))

    # Sortierung: Level-Priorität
    _prio = {"KRITISCH": 4, "HOCH": 3, "MITTEL": 2, "NIEDRIG": 1}
    all_anomalies.sort(key=lambda a: _prio.get(a.level, 0), reverse=True)

    # Gesamtlevel
    if any(a.level == "KRITISCH" for a in all_anomalies):
        level = "KRITISCH"
    elif any(a.level == "HOCH" for a in all_anomalies):
        level = "HOCH"
    elif any(a.level == "MITTEL" for a in all_anomalies):
        level = "MITTEL"
    elif all_anomalies:
        level = "NIEDRIG"
    else:
        level = "NORMAL"

    return PatrolReport(
        anomalies=all_anomalies[:50],  # max 50
        n_entities=n_entities,
        n_with_data=n_with_data,
        level=level,
        generated=datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )


# ─── Livemap-Integration ─────────────────────────────────────────────────────

def patrol_for_map() -> list[dict]:
    """
    Gibt Leaflet-kompatible Marker für alle Anomalien zurück.
    Format: [{lat, lon, popup, icon, level, region}, ...]
    """
    report = patrol_anomalies()
    markers = []

    _level_color = {
        "KRITISCH": "#ff2222",
        "HOCH":     "#ff8800",
        "MITTEL":   "#ffcc00",
        "NIEDRIG":  "#44ff44",
        "NORMAL":   "#aaaaaa",
    }

    for a in report.anomalies:
        if not (a.lat and a.lon):
            continue

        icon = _LEVEL_ICONS.get(a.level, "⚠️")
        color = _level_color.get(a.level, "#aaaaaa")

        popup = (
            f"<b>{icon} PATTERN ANOMALIE</b><br>"
            f"<b>Level:</b> {a.level}<br>"
            f"<b>Typ:</b> {a.anomaly_type}<br>"
            f"<b>Entität:</b> {a.name or a.entity_id}<br>"
            f"<hr style='border-color:#1e3a4a;margin:4px 0'>"
            f"{a.description}<br>"
            + (f"<small>Baseline: {a.baseline_val:.1f} → Aktuell: {a.current_val:.1f}</small><br>"
               if a.baseline_val or a.current_val else "")
            + f"<small style='color:#888'>{a.ts_fmt if hasattr(a,'ts_fmt') else ''}</small>"
        )

        markers.append({
            "lat":    a.lat,
            "lon":    a.lon,
            "popup":  popup,
            "icon":   icon,
            "color":  color,
            "level":  a.level,
            "region": a.region,
            "type":   "patrol_anomaly",
            "entity_type": a.entity_type,
            "anomaly_type": a.anomaly_type,
        })

    return markers


def patrol_summary() -> dict:
    """Kompaktes Dict für Dashboard-Integration."""
    report = patrol_anomalies()
    by_type: dict[str, int] = {}
    for a in report.anomalies:
        by_type[a.anomaly_type] = by_type.get(a.anomaly_type, 0) + 1

    return {
        "level":        report.level,
        "n_anomalies":  len(report.anomalies),
        "n_entities":   report.n_entities,
        "n_with_data":  report.n_with_data,
        "by_type":      by_type,
        "top_anomaly":  report.anomalies[0].description if report.anomalies else "",
        "generated":    report.generated,
    }


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NEXUS Pattern-of-Life Engine")
    parser.add_argument("--type", choices=["ship", "aircraft", "all"], default="all")
    parser.add_argument("--json", action="store_true", help="JSON-Ausgabe")
    parser.add_argument("--summary", action="store_true", help="Nur Zusammenfassung")
    args = parser.parse_args()

    if not _TS_AVAILABLE:
        print("FEHLER: nexus_timeseries.py nicht gefunden. Bitte T173 erst ausführen.")
        raise SystemExit(1)

    etype = None if args.type == "all" else [args.type]
    report = patrol_anomalies(etype)

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    elif args.summary:
        s = patrol_summary()
        print(f"\n=== NEXUS Pattern-of-Life ===")
        for k, v in s.items():
            print(f"  {k:20s}: {v}")
    else:
        print(f"\n=== NEXUS Pattern-of-Life ===")
        print(f"  Gesamtlevel:  {report.level}")
        print(f"  Entitäten:    {report.n_entities} gesichtet, {report.n_with_data} mit Baseline")
        print(f"  Anomalien:    {len(report.anomalies)}")
        print(f"  Stand:        {report.generated}")

        if not report.anomalies:
            if report.n_with_data == 0:
                print("\n  ⚠️  Keine Baseline-Daten vorhanden.")
                print("  Tipp: nexus_timeseries.py --test-insert   (Testdaten einfügen)")
                print("  Oder mindestens 24h Betrieb um echte Baselines aufzubauen.")
            else:
                print("\n  ✅ Keine Anomalien erkannt.")
        else:
            print(f"\n{'─'*70}")
            for a in report.anomalies:
                icon = _LEVEL_ICONS.get(a.level, "⚠️")
                print(f"  {icon} [{a.level:8s}] {a.anomaly_type:15s}  {a.description[:65]}")
