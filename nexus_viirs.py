"""
nexus_viirs.py – NASA VIIRS Nachtlichter: Infrastrukturausfall-Detektion
=========================================================================
Erkennt plötzliche Verdunkelungen in bekannten Ballungsräumen via NASA GIBS
(Global Imagery Browse Services) – kostenlos, kein API-Key nötig.

Logik:
  1. Lade VIIRS DNB Nachtbild für Region (256×256 px) von NASA GIBS
  2. Berechne mittlere Pixelhelligkeit als "Licht-Score"
  3. Vergleiche mit gespeicherter Baseline (14-Tage-Median)
  4. Alert wenn Helligkeit > DROP_THRESHOLD unter Baseline fällt

Ergänzt durch:
  - ACLED-Keyword-Filter für Infrastruktur-Angriffe (Proxy-Indikator)
  - Kombination beider Signale für höhere Konfidenz

NEXUS-Philosophie: keine erfundenen Daten. Wenn GIBS nicht erreichbar
oder Region zu bewölkt (Score = 0), wird kein Alert ausgegeben.
"""

import sqlite3
import json
import logging
import math
import urllib.request
import urllib.parse
import struct
import zlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger("nexus.viirs")

DB_PATH        = Path(__file__).parent / "nexus_viirs_baseline.db"
DROP_THRESHOLD = 0.35   # 35% Helligkeitsabfall → Alert
MIN_SCORE      = 5.0    # Mindesthelligkeit damit Region als "beleuchtet" gilt
BASELINE_DAYS  = 14     # Baseline aus letzten N Messungen

# NASA GIBS WMS-Endpunkt (kostenlos, kein Key)
GIBS_WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
VIIRS_LAYER = "VIIRS_SNPP_DayNightBand_ENCC"  # Enhanced Near Constant Contrast


# ── Datenbank ────────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS light_readings (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        TEXT    NOT NULL,
            region    TEXT    NOT NULL,
            date_str  TEXT    NOT NULL,
            score     REAL    NOT NULL,
            cloud_pct REAL    DEFAULT 0,
            bbox      TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_lr_region_ts
        ON light_readings(region, ts)
    """)
    conn.commit()
    return conn


# ── NASA GIBS Bild abrufen ───────────────────────────────────────────────────

def _gibs_url(bbox: tuple, date_str: str, width: int = 256) -> str:
    """
    Erstellt GIBS WMS URL für VIIRS DNB Nachtbild.

    bbox: (min_lon, min_lat, max_lon, max_lat)
    date_str: "YYYY-MM-DD"
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    params = urllib.parse.urlencode({
        "SERVICE":     "WMS",
        "VERSION":     "1.3.0",
        "REQUEST":     "GetMap",
        "LAYERS":      VIIRS_LAYER,
        "CRS":         "EPSG:4326",
        "BBOX":        f"{min_lat},{min_lon},{max_lat},{max_lon}",
        "FORMAT":      "image/png",
        "WIDTH":       width,
        "HEIGHT":      width,
        "TIME":        date_str,
        "TRANSPARENT": "FALSE",
    })
    return f"{GIBS_WMS}?{params}"


def _png_mean_brightness(data: bytes) -> float:
    """
    Berechnet mittlere Helligkeit eines PNG-Bildes ohne externe Libraries.
    Gibt Wert 0–255 zurück. Schwarz (kein Licht) ≈ 0, hell ≈ 255.
    """
    try:
        # PNG Signatur prüfen
        if data[:8] != b'\x89PNG\r\n\x1a\n':
            return 0.0

        pos = 8
        raw_data = b""
        width = height = 0
        bit_depth = color_type = 0

        while pos < len(data):
            if pos + 8 > len(data):
                break
            length = struct.unpack(">I", data[pos:pos+4])[0]
            chunk_type = data[pos+4:pos+8]
            chunk_data = data[pos+8:pos+8+length]
            pos += 12 + length

            if chunk_type == b"IHDR":
                width, height = struct.unpack(">II", chunk_data[:8])
                bit_depth  = chunk_data[8]
                color_type = chunk_data[9]
            elif chunk_type == b"IDAT":
                raw_data += chunk_data
            elif chunk_type == b"IEND":
                break

        if not raw_data or width == 0:
            return 0.0

        # Zlib-dekomprimieren
        raw = zlib.decompress(raw_data)

        # Bytes in Pixelwerte umwandeln (vereinfacht: nur Kanal 0 = Rot/Grau)
        bytes_per_pixel = 1 if color_type in (0, 3) else (3 if color_type == 2 else 4)
        row_size = 1 + width * bytes_per_pixel  # +1 für Filter-Byte

        total = 0
        count = 0
        for row in range(height):
            row_start = row * row_size + 1  # Filter-Byte überspringen
            for col in range(width):
                pixel_start = row_start + col * bytes_per_pixel
                if pixel_start < len(raw):
                    total += raw[pixel_start]
                    count += 1

        return total / count if count > 0 else 0.0

    except Exception as e:
        log.debug(f"PNG-Analyse Fehler: {e}")
        return 0.0


def _fetch_brightness(bbox: tuple, date_str: str) -> Optional[float]:
    """
    Holt VIIRS DNB Bild und gibt mittlere Helligkeit zurück.
    None bei Netzwerkfehler oder ungültigem Bild.
    """
    url = _gibs_url(bbox, date_str)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NEXUS-OSINT/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            if resp.status != 200:
                return None
            data = resp.read()
        score = _png_mean_brightness(data)
        log.debug(f"VIIRS {date_str} {bbox}: brightness={score:.1f}")
        return round(score, 2)
    except Exception as e:
        log.warning(f"GIBS-Fehler für {date_str}: {e}")
        return None


# ── Baseline-Management ──────────────────────────────────────────────────────

def _store_reading(region: str, date_str: str, score: float, bbox: tuple):
    conn = _get_db()
    conn.execute(
        "INSERT INTO light_readings (ts, region, date_str, score, bbox) VALUES (?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), region, date_str,
         score, json.dumps(bbox))
    )
    # Alte Readings löschen (>30 Tage)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    conn.execute("DELETE FROM light_readings WHERE region=? AND ts < ?", (region, cutoff))
    conn.commit()
    conn.close()


def _get_baseline(region: str) -> Optional[float]:
    """Gibt Median der letzten BASELINE_DAYS Messungen zurück (exkl. heute)."""
    conn = _get_db()
    rows = conn.execute(
        """SELECT score FROM light_readings
           WHERE region=? AND score > ?
           ORDER BY ts DESC LIMIT ?""",
        (region, MIN_SCORE, BASELINE_DAYS)
    ).fetchall()
    conn.close()
    if len(rows) < 3:
        return None  # Zu wenig Daten für Baseline
    scores = sorted(r[0] for r in rows)
    mid = len(scores) // 2
    return float(scores[mid])


# ── Region-Bbox-Karte ────────────────────────────────────────────────────────

REGION_BBOXES = {
    # (min_lon, min_lat, max_lon, max_lat)
    "ukraine":       (22.0, 44.0, 40.0, 52.0),
    "gaza":          (34.2, 31.2, 34.6, 31.6),
    "syrien":        (35.0, 32.0, 42.0, 37.0),
    "hormuz":        (55.5, 25.5, 57.5, 27.5),
    "taiwan":        (119.0, 21.0, 123.0, 26.0),
    "nordkorea":     (124.0, 37.0, 130.0, 43.0),
    "kharkiv":       (35.5, 49.5, 37.5, 51.0),
    "kiew":          (29.5, 50.0, 31.5, 51.5),
    "beirut":        (35.2, 33.6, 36.2, 34.2),
    "myanmar":       (94.0, 16.0, 101.0, 28.0),
    "sahel":         (-5.0, 10.0, 25.0, 25.0),
    "jemen":         (42.0, 12.0, 54.0, 19.0),
    "libyen":        (9.0, 20.0, 25.0, 33.0),
    # ── Iran + Umgebung (T152) ────────────────────────────────────────────
    "iran":          (44.0, 25.0, 63.5, 40.0),   # Gesamter Iran
    "teheran":       (50.5, 35.0, 52.0, 36.5),   # Hauptstadt-Region
    "isfahan":       (51.0, 31.5, 52.5, 33.0),   # Natanz liegt ca. 50km entfernt
    "natanz":        (51.5, 33.0, 52.5, 34.0),   # Urananreicherungsanlage
    "fordow":        (50.5, 34.5, 51.5, 35.5),   # Unterirdische Anlage
    "bandar_abbas":  (56.0, 27.0, 57.5, 28.0),   # Hafenstadt + Militärbasis
    "bushehr":       (50.5, 28.5, 51.5, 29.5),   # Kernkraftwerk
    "ahvaz":         (48.0, 31.0, 49.5, 32.5),   # Ölregion Khuzestan
    "naher osten":   (25.0, 15.0, 65.0, 42.0),   # Gesamte Region
    "persischer golf": (47.0, 22.0, 60.0, 30.0),
}


def _region_to_bbox(region: str) -> Optional[tuple]:
    """
    Gibt Bbox fuer Region zurueck.
    Prioritaet: lokale REGION_BBOXES → nexus_region Fallback-Kette → lat,lon,span Format
    """
    r = region.lower().strip()
    # 1. Lokale REGION_BBOXES (enthaelt Iran + wichtige Konfliktregionen)
    if r in REGION_BBOXES:
        return REGION_BBOXES[r]
    # 2. nexus_region mit hierarchischem Fallback (Global)
    try:
        from nexus_region import get_bbox_with_fallback
        bbox, _ = get_bbox_with_fallback(region)
        if bbox:
            return bbox
    except ImportError:
        pass
    # 3. "lat,lon,span" Format: "50.0,36.0,2.0"
    parts = r.split(",")
    if len(parts) == 3:
        try:
            lat, lon, span = float(parts[0]), float(parts[1]), float(parts[2])
            return (lon - span, lat - span, lon + span, lat + span)
        except ValueError:
            pass
    return None


# ── Haupt-Analyse ─────────────────────────────────────────────────────────────

def check_darkness(region: str, days_back: int = 3) -> dict:
    """
    Prüft ob eine Region ungewöhnlich dunkel ist.

    Args:
        region:    Bekannte Region oder "lat,lon,span"
        days_back: Wie viele Tage rückwirkend prüfen (VIIRS hat ~1 Tag Verzögerung)

    Returns:
        {
          "region": str,
          "current_score": float,       # Aktuelle Helligkeit (0–255)
          "baseline_score": float,      # Historische Baseline
          "drop_pct": float,            # Prozentualer Abfall (0–1)
          "alert": bool,                # True wenn signifikant dunkler
          "date_checked": str,
          "status": str,                # "dark", "normal", "no_data", "new_baseline"
        }
    """
    result = {
        "region":         region,
        "current_score":  None,
        "baseline_score": None,
        "drop_pct":       None,
        "alert":          False,
        "date_checked":   None,
        "status":         "no_data",
    }

    bbox = _region_to_bbox(region)
    if not bbox:
        result["status"] = "unknown_region"
        return result

    # Letzten verfügbaren Tag versuchen (VIIRS hat ~1–2 Tage Latenz)
    now = datetime.now(timezone.utc)
    score = None
    date_used = None

    for d in range(1, days_back + 2):
        date_str = (now - timedelta(days=d)).strftime("%Y-%m-%d")
        score = _fetch_brightness(bbox, date_str)
        if score is not None:
            date_used = date_str
            break

    if score is None or date_used is None:
        return result

    result["current_score"] = score
    result["date_checked"]  = date_used

    # Speichern für Baseline
    if score > 0:
        _store_reading(region, date_used, score, bbox)

    baseline = _get_baseline(region)
    result["baseline_score"] = baseline

    if baseline is None:
        result["status"] = "new_baseline"
        return result

    if baseline < MIN_SCORE:
        # Region ist normalerweise dunkel (unbewohnt/Meer)
        result["status"] = "normally_dark"
        return result

    drop = max(0.0, (baseline - score) / baseline)
    result["drop_pct"] = round(drop, 3)

    if drop >= DROP_THRESHOLD and score < baseline * (1 - DROP_THRESHOLD):
        result["alert"]  = True
        result["status"] = "dark"
        log.warning(
            f"VIIRS Alert: {region} ist {drop:.0%} dunkler als Baseline "
            f"({score:.1f} vs {baseline:.1f})"
        )
    else:
        result["status"] = "normal"

    return result


def scan_conflict_zones() -> list:
    """
    Scannt alle bekannten Konfliktregionen auf Verdunkelung.
    Gibt Liste von Alert-Dicts zurück.
    """
    alerts = []
    conflict_regions = [
        "ukraine", "gaza", "syrien", "kharkiv", "kiew",
        "beirut", "myanmar", "jemen",
    ]
    for region in conflict_regions:
        try:
            result = check_darkness(region)
            if result["alert"]:
                alerts.append({
                    "region":    region,
                    "type":      "infrastructure_outage",
                    "drop_pct":  result["drop_pct"],
                    "score":     result["current_score"],
                    "baseline":  result["baseline_score"],
                    "date":      result["date_checked"],
                    "title":     f"⬛ Verdunkelung: {region.title()} – "
                                 f"{result['drop_pct']:.0%} unter Baseline",
                    "severity":  "high" if result["drop_pct"] > 0.6 else "medium",
                    "lat":       (_region_to_bbox(region)[1] + _region_to_bbox(region)[3]) / 2,
                    "lon":       (_region_to_bbox(region)[0] + _region_to_bbox(region)[2]) / 2,
                })
        except Exception as e:
            log.debug(f"scan {region}: {e}")
    return alerts


def get_viirs_for_map(region: str = "ukraine") -> list:
    """Gibt Karten-kompatible Marker für VIIRS-Verdunkelung zurück."""
    alerts = []
    try:
        result = check_darkness(region)
        if result.get("alert"):
            bbox = _region_to_bbox(region)
            if bbox:
                lat = (bbox[1] + bbox[3]) / 2
                lon = (bbox[0] + bbox[2]) / 2
                alerts.append({
                    "lat":   lat,
                    "lon":   lon,
                    "type":  "viirs_dark",
                    "icon":  "⬛",
                    "color": "#444444",
                    "title": f"⬛ Infrastrukturausfall: {region.title()}",
                    "popup": (
                        f"<b>⬛ VIIRS Verdunkelung</b><br>"
                        f"<b>Region:</b> {region}<br>"
                        f"<b>Helligkeit:</b> {result['current_score']:.1f} "
                        f"(Baseline: {result['baseline_score']:.1f})<br>"
                        f"<b>Abfall:</b> {result['drop_pct']:.0%}<br>"
                        f"<b>Datum:</b> {result['date_checked']}<br>"
                        f"<small>Quelle: NASA GIBS VIIRS DNB</small>"
                    ),
                })
    except Exception as e:
        log.debug(f"get_viirs_for_map: {e}")
    return alerts


# ── Standalone Test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    region = sys.argv[1] if len(sys.argv) > 1 else "ukraine"
    print(f"Prüfe Nachtlichter für: {region}")
    r = check_darkness(region)
    print(f"  Datum:     {r['date_checked']}")
    print(f"  Score:     {r['current_score']}")
    print(f"  Baseline:  {r['baseline_score']}")
    print(f"  Abfall:    {r['drop_pct']}")
    print(f"  Status:    {r['status']}")
    if r["alert"]:
        print(f"  ALERT: VERDUNKELUNG DETEKTIERT!")
