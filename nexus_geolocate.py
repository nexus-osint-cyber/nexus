"""
NEXUS – Bild-Geolokalisierung  (Ebene 4 / Modul 4.8)
=====================================================
Extrahiert GPS-Koordinaten aus OSINT-Bildern in 4 Stufen:

  1. EXIF-GPS      direkt auslesen (sofort, exakt)
  2. Schatten-Analyse  Sonnenstand + Schatten → Tageszeit + Himmelsrichtung
  3. OCR-Text      Schilder, Straßennamen, Grid-Referenzen → Nominatim
  4. Kontext-NLP   LLaVA-beschriebener geo_hint → Nominatim-Suche

Öffentliche API:
  geolocate_image(url_or_path, meta)  → GeoResult
  geolocate_articles(articles, region)→ list[dict]  (Karten-Marker)
  enrich_vision_hits(hits, region)    → list[VisionHit]  (befüllt lat/lon)

Abhängigkeiten (alle optional):
  pip install pillow requests
  pip install pytesseract   (+ Tesseract-OCR installieren)
  pip install ephem         (Schatten-Berechnung)
"""

from __future__ import annotations

import json
import math
import re
import struct
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────────────────────

NOMINATIM_URL  = "https://nominatim.openstreetmap.org/search"
NOMINATIM_UA   = "NEXUS-OSINT/1.0 (nexus-osint@localhost)"
GEOCODE_CACHE: dict[str, Optional[tuple[float, float]]] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Ergebnis-Klasse
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GeoResult:
    lat:        Optional[float] = None
    lon:        Optional[float] = None
    method:     str             = ""     # exif | shadow | ocr | nlp_hint | failed
    confidence: float           = 0.0   # 0-1
    accuracy_km: float          = 50.0  # Geschätzter Fehler in km
    place_name: str             = ""
    timestamp_hint: str         = ""    # Zeitfenster aus Schatten ("ca. 14:00 UTC")
    raw_text:   str             = ""    # OCR-Text falls vorhanden


# ─────────────────────────────────────────────────────────────────────────────
# STUFE 1: EXIF-GPS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_exif_gps(data: bytes) -> Optional[tuple[float, float]]:
    """
    Parst GPS-IFD direkt aus JPEG-Bytes ohne externe Bibliothek.
    Gibt (lat, lon) zurück oder None.
    """
    try:
        # JPEG APP1 (Exif) suchen
        pos = 0
        while pos < len(data) - 2:
            marker = data[pos:pos+2]
            if marker == b'\xff\xe1':  # APP1
                seg_len = struct.unpack('>H', data[pos+2:pos+4])[0]
                seg = data[pos+4:pos+2+seg_len]
                if seg[:6] in (b'Exif\x00\x00', b'Exif\x00\xff'):
                    return _parse_tiff_gps(seg[6:])
                pos += 2 + seg_len
            elif marker[0:1] == b'\xff' and marker[1:2] not in (b'\xd8', b'\xd9'):
                seg_len = struct.unpack('>H', data[pos+2:pos+4])[0]
                pos += 2 + seg_len
            else:
                pos += 1
    except Exception:
        pass
    return None


def _parse_tiff_gps(tiff: bytes) -> Optional[tuple[float, float]]:
    """Liest GPS IFD (Tag 0x8825) aus TIFF-Header."""
    try:
        if tiff[:2] == b'II':
            endian = '<'
        elif tiff[:2] == b'MM':
            endian = '>'
        else:
            return None

        ifd_offset = struct.unpack(endian + 'I', tiff[4:8])[0]
        num_entries = struct.unpack(endian + 'H', tiff[ifd_offset:ifd_offset+2])[0]

        gps_ifd_offset = None
        for i in range(num_entries):
            entry = tiff[ifd_offset+2 + i*12 : ifd_offset+2 + i*12 + 12]
            tag, typ, cnt, val_off = struct.unpack(endian + 'HHII', entry)
            if tag == 0x8825:  # GPSInfo IFD Pointer
                gps_ifd_offset = val_off
                break

        if gps_ifd_offset is None:
            return None

        gps_tags = {}
        num_gps = struct.unpack(endian + 'H', tiff[gps_ifd_offset:gps_ifd_offset+2])[0]
        for i in range(num_gps):
            entry = tiff[gps_ifd_offset+2 + i*12 : gps_ifd_offset+2 + i*12 + 12]
            tag, typ, cnt, val_off = struct.unpack(endian + 'HHII', entry)
            gps_tags[tag] = (typ, cnt, val_off)

        def read_rational(offset):
            num = struct.unpack(endian + 'I', tiff[offset:offset+4])[0]
            den = struct.unpack(endian + 'I', tiff[offset+4:offset+8])[0]
            return num / den if den != 0 else 0.0

        def dms_to_dd(tag_id):
            if tag_id not in gps_tags:
                return None
            typ, cnt, off = gps_tags[tag_id]
            d = read_rational(off)
            m = read_rational(off+8)
            s = read_rational(off+16)
            return d + m/60 + s/3600

        lat = dms_to_dd(2)   # GPSLatitude
        lon = dms_to_dd(4)   # GPSLongitude

        if lat is None or lon is None:
            return None

        # Vorzeichen aus N/S E/W (Tags 1 und 3)
        lat_ref_tag = gps_tags.get(1)
        lon_ref_tag = gps_tags.get(3)
        if lat_ref_tag:
            typ, cnt, off = lat_ref_tag
            ref = tiff[off:off+1].decode("ascii", errors="ignore")
            if ref == "S":
                lat = -lat
        if lon_ref_tag:
            typ, cnt, off = lon_ref_tag
            ref = tiff[off:off+1].decode("ascii", errors="ignore")
            if ref == "W":
                lon = -lon

        if -90 <= lat <= 90 and -180 <= lon <= 180 and (abs(lat) > 0.001 or abs(lon) > 0.001):
            return round(lat, 6), round(lon, 6)
    except Exception:
        pass
    return None


def _exif_gps_from_url(url: str) -> Optional[GeoResult]:
    """Lädt Bild-Header und extrahiert EXIF-GPS."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 NEXUS-OSINT/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read(128 * 1024)  # Nur erste 128KB für EXIF

        coords = _parse_exif_gps(data)
        if coords:
            return GeoResult(
                lat=coords[0], lon=coords[1],
                method="exif", confidence=0.95, accuracy_km=0.1,
            )
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# STUFE 2: Schatten-Analyse (Sonnenstand)
# ─────────────────────────────────────────────────────────────────────────────

def _shadow_analysis(image_data: bytes, region_lat: float, region_lon: float,
                     capture_time=None) -> Optional[str]:
    """
    Grobe Schatten-Analyse: gibt Zeitfenster-Hinweis zurück.
    Benötigt ephem für präzise Berechnung.
    """
    try:
        import ephem
        sun = ephem.Sun()
        obs = ephem.Observer()
        obs.lat  = str(region_lat)
        obs.long = str(region_lon)
        obs.date = capture_time or ephem.now()
        sun.compute(obs)
        alt_deg  = math.degrees(sun.alt)
        az_deg   = math.degrees(sun.az)
        utc_time = ephem.Date(obs.date).datetime().strftime("%H:%M UTC")
        return f"Sonnenstand ca. {alt_deg:.0f}° Höhe, {az_deg:.0f}° Azimut (≈ {utc_time})"
    except ImportError:
        pass
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# STUFE 3: OCR-Text → Geocoding
# ─────────────────────────────────────────────────────────────────────────────

# Regex für Grid-Referenzen im Text
_RE_MGRS   = re.compile(r'\b(\d{1,2}[C-X])\s*([A-Z]{2})\s*(\d{4,5})\s*(\d{4,5})\b', re.I)
_RE_COORD  = re.compile(r'\b(-?\d{1,3}\.\d{3,6})[,\s]\s*(-?\d{1,3}\.\d{3,6})\b')
_RE_STREET = re.compile(r'(?:вул\.|str\.|straße|улица|вулиця|street|st\.)\s+[\w\.\-]+', re.I)
_RE_CITY   = re.compile(r'(?:м\.|місто|город|city|г\.)\s+[\w\-]+', re.I)


def _ocr_image(image_data: bytes) -> str:
    """Versucht OCR mit pytesseract. Gibt Text oder '' zurück."""
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(image_data))
        text = pytesseract.image_to_string(img, lang="ukr+rus+eng", config="--psm 11")
        return text[:2000]
    except Exception:
        return ""


def _nominatim_geocode(query: str) -> Optional[tuple[float, float, str]]:
    """Geocodiert einen Ortsnamen via Nominatim. Returns (lat, lon, display_name)."""
    if not query or len(query) < 3:
        return None

    cache_key = query.lower()[:80]
    if cache_key in GEOCODE_CACHE:
        result = GEOCODE_CACHE[cache_key]
        return result

    try:
        params = urllib.parse.urlencode({
            "q": query, "format": "json",
            "limit": 1, "addressdetails": 0,
        })
        req = urllib.request.Request(
            f"{NOMINATIM_URL}?{params}",
            headers={"User-Agent": NOMINATIM_UA},
        )
        time.sleep(1.1)  # Nominatim Rate-Limit: 1 Req/s
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if data:
                lat = float(data[0]["lat"])
                lon = float(data[0]["lon"])
                name = data[0].get("display_name", query)[:80]
                GEOCODE_CACHE[cache_key] = (lat, lon, name)
                return lat, lon, name
    except Exception:
        pass

    GEOCODE_CACHE[cache_key] = None
    return None


def _ocr_to_geo(ocr_text: str, region_hint: str = "") -> Optional[GeoResult]:
    """Versucht aus OCR-Text Koordinaten zu extrahieren."""
    if not ocr_text:
        return None

    # 1. Direkte Koordinaten im Text?
    for m in _RE_COORD.finditer(ocr_text):
        try:
            lat, lon = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lon <= 180 and abs(lat) > 1 and abs(lon) > 1:
                return GeoResult(lat=lat, lon=lon, method="ocr_coord",
                                 confidence=0.85, accuracy_km=1.0, raw_text=ocr_text[:200])
        except ValueError:
            pass

    # 2. Straßen-/Ortsnamen
    place_candidates = []
    for pat in (_RE_STREET, _RE_CITY):
        for m in pat.finditer(ocr_text):
            place_candidates.append(m.group().strip()[:50])

    # Region-Hint + Ort kombinieren
    if region_hint:
        for cand in place_candidates[:3]:
            query = f"{cand}, {region_hint}"
            result = _nominatim_geocode(query)
            if result:
                return GeoResult(lat=result[0], lon=result[1],
                                 method="ocr_nominatim", confidence=0.60,
                                 accuracy_km=5.0, place_name=result[2],
                                 raw_text=ocr_text[:200])

    # Nur Ort ohne Region
    for cand in place_candidates[:2]:
        result = _nominatim_geocode(cand)
        if result:
            return GeoResult(lat=result[0], lon=result[1],
                             method="ocr_nominatim", confidence=0.45,
                             accuracy_km=10.0, place_name=result[2],
                             raw_text=ocr_text[:200])
    return None


# ─────────────────────────────────────────────────────────────────────────────
# STUFE 4: NLP geo_hint aus LLaVA
# ─────────────────────────────────────────────────────────────────────────────

def _hint_to_geo(geo_hint: str, region: str = "") -> Optional[GeoResult]:
    """Geocodiert den geo_hint-Text aus der Vision-Analyse."""
    if not geo_hint or len(geo_hint) < 4:
        return None

    # Direkte Koordinaten im Hint?
    m = _RE_COORD.search(geo_hint)
    if m:
        try:
            lat, lon = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return GeoResult(lat=lat, lon=lon, method="hint_coord",
                                 confidence=0.75, accuracy_km=2.0)
        except ValueError:
            pass

    # Hint + Region geocodieren
    queries = [
        f"{geo_hint}, {region}" if region else geo_hint,
        geo_hint,
    ]
    for q in queries:
        result = _nominatim_geocode(q)
        if result:
            return GeoResult(lat=result[0], lon=result[1],
                             method="hint_nominatim", confidence=0.50,
                             accuracy_km=15.0, place_name=result[2])
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Funktion
# ─────────────────────────────────────────────────────────────────────────────

import io  # noqa: E402 (nach PIL/pytesseract Import)


def geolocate_image(
    url_or_path: str,
    region:      str  = "",
    geo_hint:    str  = "",
    region_lat:  float = 0.0,
    region_lon:  float = 0.0,
) -> GeoResult:
    """
    Geolokalisiert ein Bild. Probiert alle 4 Methoden der Reihe nach.
    Gibt das beste Ergebnis zurück.
    """
    # Stufe 1: EXIF
    if url_or_path.startswith(("http://", "https://")):
        exif_result = _exif_gps_from_url(url_or_path)
        if exif_result:
            return exif_result

    # Stufe 4: geo_hint aus LLaVA (vor OCR, weil oft schneller)
    if geo_hint:
        hint_result = _hint_to_geo(geo_hint, region)
        if hint_result:
            return hint_result

    # Stufe 3: OCR (nur wenn pytesseract verfügbar)
    try:
        import pytesseract  # noqa: F401
        try:
            req = urllib.request.Request(
                url_or_path,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                img_data = resp.read(5 * 1024 * 1024)
            ocr_text = _ocr_image(img_data)
            if ocr_text:
                ocr_result = _ocr_to_geo(ocr_text, region)
                if ocr_result:
                    return ocr_result
        except Exception:
            pass
    except ImportError:
        pass

    # Stufe 2: Schatten (gibt nur Zeithinweis, keine Koordinaten)
    ts_hint = ""
    if region_lat and region_lon:
        ts_hint = _shadow_analysis(b"", region_lat, region_lon) or ""

    return GeoResult(method="failed", confidence=0.0, timestamp_hint=ts_hint)


# ─────────────────────────────────────────────────────────────────────────────
# Batch: VisionHits mit Koordinaten anreichern
# ─────────────────────────────────────────────────────────────────────────────

def enrich_vision_hits(
    hits: list,  # list[VisionHit]
    region: str = "",
    region_lat: float = 0.0,
    region_lon: float = 0.0,
) -> list:
    """
    Befüllt lat/lon in VisionHits die noch keine Koordinaten haben.
    Gibt angereicherte Liste zurück.
    """
    enriched = []
    for hit in hits:
        if hit.lat and hit.lon:
            enriched.append(hit)
            continue

        geo = geolocate_image(
            hit.url,
            region=region,
            geo_hint=hit.geo_hint,
            region_lat=region_lat,
            region_lon=region_lon,
        )
        if geo.lat and geo.lon:
            hit.lat          = geo.lat
            hit.lon          = geo.lon
            hit.coord_method = geo.method
            if geo.accuracy_km <= 20:
                hit.confidence = min(1.0, hit.confidence + 0.1)
        enriched.append(hit)

    return enriched


# ─────────────────────────────────────────────────────────────────────────────
# Artikel-Batch → Karten-Marker
# ─────────────────────────────────────────────────────────────────────────────

def geolocate_articles(
    articles: list[dict],
    region:   str   = "",
    region_lat: float = 0.0,
    region_lon: float = 0.0,
) -> list[dict]:
    """
    Geolokalisiert Bilder aus Artikeln die direkte Bild-URLs haben.
    Gibt Marker-Liste für Karte zurück.
    """
    markers = []
    for art in articles[:30]:
        for key in ("image_url", "image", "thumbnail", "photo"):
            url = art.get(key, "")
            if not url or not url.startswith("http"):
                continue
            geo = geolocate_image(url, region, "", region_lat, region_lon)
            if geo.lat and geo.lon and geo.confidence >= 0.4:
                markers.append({
                    "lat":        geo.lat,
                    "lon":        geo.lon,
                    "method":     geo.method,
                    "confidence": geo.confidence,
                    "accuracy_km":geo.accuracy_km,
                    "place_name": geo.place_name,
                    "ts_hint":    geo.timestamp_hint,
                    "image_url":  url,
                    "title":      f"📍 Bild-Geo ({geo.method})",
                    "text":       art.get("title", "")[:100],
                    "source":     art.get("source", ""),
                    "color":      "#cc44ff",
                    "icon":       "📍",
                })
            break  # Ein Bild pro Artikel reicht

    return markers


# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# T198: Journalist Chronolocation — Zeitpunkt & Ort Verifikation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ChronoResult:
    """Ergebnis der Chronolocation-Verifikation."""
    # Zeitpunkt-Verifikation
    time_plausible:       bool    = False
    claimed_datetime:     str     = ""
    expected_sun_alt_deg: float   = 0.0   # Erwartete Sonnen-Höhe zum Behaupteten Zeitpunkt
    expected_sun_az_deg:  float   = 0.0   # Erwartetes Azimut
    observed_shadow_dir:  Optional[float] = None  # Gemessene Schattenrichtung (Grad)
    shadow_consistent:    bool    = False   # Passt Schatten zur Sonne?
    solar_noon_utc:       str     = ""      # Mittagszeit am Ort
    sunrise_utc:          str     = ""      # Sonnenaufgang
    sunset_utc:           str     = ""      # Sonnenuntergang

    # Ort-Verifikation
    location_plausible:   bool    = False
    claimed_lat:          float   = 0.0
    claimed_lon:          float   = 0.0
    osm_place_name:       str     = ""      # Nächster OSM-Ort
    osm_dist_km:          float   = 0.0
    landmark_matches:     list    = field(default_factory=list)   # OSM-Gebäude in der Nähe

    # Gesamt-Einschätzung
    confidence:           float   = 0.0    # 0–1
    verdict:              str     = ""     # PLAUSIBLE | SUSPICIOUS | INCONSISTENT | INSUFFICIENT_DATA
    notes:                list    = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "time_plausible":       self.time_plausible,
            "claimed_datetime":     self.claimed_datetime,
            "expected_sun_alt_deg": round(self.expected_sun_alt_deg, 1),
            "expected_sun_az_deg":  round(self.expected_sun_az_deg, 1),
            "observed_shadow_dir":  self.observed_shadow_dir,
            "shadow_consistent":    self.shadow_consistent,
            "solar_noon_utc":       self.solar_noon_utc,
            "sunrise_utc":          self.sunrise_utc,
            "sunset_utc":           self.sunset_utc,
            "location_plausible":   self.location_plausible,
            "claimed_lat":          self.claimed_lat,
            "claimed_lon":          self.claimed_lon,
            "osm_place_name":       self.osm_place_name,
            "osm_dist_km":          round(self.osm_dist_km, 1),
            "landmark_matches":     self.landmark_matches[:5],
            "confidence":           round(self.confidence, 2),
            "verdict":              self.verdict,
            "notes":                self.notes,
        }


def _compute_sun_position(lat: float, lon: float, dt_utc) -> dict:
    """
    Berechnet Sonnenposition für Koordinaten + UTC-Zeitpunkt.
    Gibt alt_deg, az_deg, ist_tag zurück.
    """
    try:
        import ephem
        obs         = ephem.Observer()
        obs.lat     = str(lat)
        obs.long    = str(lon)
        obs.date    = dt_utc
        obs.pressure = 0   # keine Refraktion
        sun         = ephem.Sun()
        sun.compute(obs)
        alt = math.degrees(float(sun.alt))
        az  = math.degrees(float(sun.az))
        # Sonnenauf- und -untergang
        try:
            rise = obs.next_rising(sun,  start=ephem.Date(dt_utc) - 1)
            sset = obs.next_setting(sun, start=ephem.Date(dt_utc) - 1)
            rise_str = ephem.Date(rise).datetime().strftime("%H:%M UTC")
            set_str  = ephem.Date(sset).datetime().strftime("%H:%M UTC")
        except Exception:
            rise_str = set_str = "n/a"
        # Mittag
        try:
            noon = obs.next_transit(sun, start=ephem.Date(dt_utc) - 1)
            noon_str = ephem.Date(noon).datetime().strftime("%H:%M UTC")
        except Exception:
            noon_str = "n/a"
        return {
            "alt_deg": round(alt, 1),
            "az_deg":  round(az, 1),
            "ist_tag": alt > 0,
            "sunrise": rise_str,
            "sunset":  set_str,
            "noon":    noon_str,
            "shadow_dir_deg": round((az + 180) % 360, 1),  # Schatten = Gegen-Azimut
        }
    except ImportError:
        # Fallback: einfache Formel (ohne ephem)
        return _sun_position_simple(lat, lon, dt_utc)
    except Exception as e:
        return {"alt_deg": 0, "az_deg": 0, "ist_tag": False, "error": str(e)}


def _sun_position_simple(lat: float, lon: float, dt_utc) -> dict:
    """Einfache Sonnenposition ohne ephem (Genauigkeit ±2°)."""
    from datetime import timezone as tz
    if hasattr(dt_utc, 'timetuple'):
        year = dt_utc.year
        doy  = dt_utc.timetuple().tm_yday
        hour = dt_utc.hour + dt_utc.minute / 60.0
    else:
        import datetime as _dt
        d = _dt.datetime.utcnow()
        year, doy, hour = d.year, d.timetuple().tm_yday, d.hour + d.minute/60.0

    # Solar time
    B        = math.radians((360 / 365) * (doy - 81))
    EoT      = 9.87 * math.sin(2*B) - 7.53 * math.cos(B) - 1.5 * math.sin(B)
    solar_t  = hour + lon/15 + EoT/60
    hour_ang = math.radians(15 * (solar_t - 12))

    # Declination
    decl = math.radians(23.45 * math.sin(math.radians((360/365)*(doy-81))))
    lat_r = math.radians(lat)

    # Altitude
    sin_alt = (math.sin(lat_r)*math.sin(decl) +
               math.cos(lat_r)*math.cos(decl)*math.cos(hour_ang))
    alt_deg = math.degrees(math.asin(max(-1, min(1, sin_alt))))

    # Azimuth
    cos_az = ((math.sin(decl) - math.sin(lat_r)*sin_alt) /
              (math.cos(lat_r)*math.cos(math.radians(alt_deg)) + 1e-9))
    az_deg = math.degrees(math.acos(max(-1, min(1, cos_az))))
    if hour_ang > 0:
        az_deg = 360 - az_deg

    shadow_dir = (az_deg + 180) % 360
    return {
        "alt_deg":       round(alt_deg, 1),
        "az_deg":        round(az_deg, 1),
        "ist_tag":       alt_deg > 0,
        "sunrise":       "n/a",
        "sunset":        "n/a",
        "noon":          "n/a",
        "shadow_dir_deg": round(shadow_dir, 1),
    }


def _detect_shadow_direction(image_data: bytes) -> Optional[float]:
    """
    Schätzt Schatten-Hauptrichtung aus dem Bild via Gradientenanalyse.
    Gibt Grad (0–360, gemessen von Nord) zurück oder None.
    Benötigt Pillow.
    """
    try:
        from PIL import Image, ImageFilter
        import struct

        img   = Image.open(io.BytesIO(image_data)).convert("L").resize((128, 128))
        # Sobel-ähnliche Kanten
        edges = img.filter(ImageFilter.FIND_EDGES)
        px    = list(edges.getdata())
        w, h  = edges.size

        # Gewichteter Durchschnitt der Kantenpixel-Positionen
        # Schatten-Kanten haben typisch hohen Kontrast
        weight_x = weight_y = total = 0
        for y in range(h):
            for x in range(w):
                v = px[y*w + x]
                if v > 60:   # nur starke Kanten
                    weight_x += (x - w//2) * v
                    weight_y += (y - h//2) * v
                    total += v

        if total == 0:
            return None

        angle = math.degrees(math.atan2(weight_x, -weight_y)) % 360
        return round(angle, 0)
    except Exception:
        return None


def _overpass_nearby(lat: float, lon: float, radius_m: int = 500) -> list[dict]:
    """
    Fragt OSM Overpass API nach Gebäuden/Landmarken in der Nähe.
    Gibt Liste von {name, type, lat, lon, dist_m} zurück.
    """
    query = f"""
[out:json][timeout:10];
(
  node["name"](around:{radius_m},{lat},{lon});
  way["building"](around:{radius_m},{lat},{lon});
  way["amenity"](around:{radius_m},{lat},{lon});
);
out center qt 20;
"""
    try:
        r = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=12,
        )
        r.raise_for_status()
        elements = r.json().get("elements", [])
        results = []
        for el in elements[:20]:
            tags = el.get("tags", {})
            name = tags.get("name") or tags.get("name:en") or ""
            if not name:
                continue
            if el.get("type") == "node":
                elat, elon = el.get("lat", lat), el.get("lon", lon)
            else:
                c = el.get("center", {})
                elat, elon = c.get("lat", lat), c.get("lon", lon)
            # Distanz berechnen
            dist_m = int(_haversine_m(lat, lon, elat, elon))
            results.append({
                "name":    name[:60],
                "type":    tags.get("building") or tags.get("amenity") or "osm",
                "lat":     round(elat, 5),
                "lon":     round(elon, 5),
                "dist_m":  dist_m,
            })
        results.sort(key=lambda x: x["dist_m"])
        return results[:10]
    except Exception:
        return []


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Distanz in Metern."""
    R = 6_371_000.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    Δφ = math.radians(lat2 - lat1)
    Δλ = math.radians(lon2 - lon1)
    a  = math.sin(Δφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(Δλ/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def verify_chronolocation(
    image_path_or_url: str,
    claimed_lat:       float,
    claimed_lon:       float,
    claimed_datetime:  str,              # ISO 8601: "2024-10-15T14:30:00" oder "2024-10-15"
    region_hint:       str = "",         # Optionaler Kontext
    check_osm:         bool = True,      # Overpass-Abfrage durchführen
) -> dict:
    """
    Verifiziert ob ein Journalistenbild wirklich zu Ort + Zeit passt.
    Prüft: Sonnenstand, Schatten-Konsistenz, OSM-Landmarken.

    Parameters
    ----------
    image_path_or_url : Lokaler Pfad oder https:// URL zum Bild
    claimed_lat       : Behauptete Breite
    claimed_lon       : Behauptete Länge
    claimed_datetime  : Behaupteter Aufnahme-Zeitpunkt (UTC)
    region_hint       : Optional, z.B. "Gaza" für Kontext
    check_osm         : Overpass-Abfrage für Landmarken (benötigt Internet)

    Returns
    -------
    dict  (ChronoResult.to_dict()) mit:
        verdict      : PLAUSIBLE | SUSPICIOUS | INCONSISTENT | INSUFFICIENT_DATA
        confidence   : 0–1
        time_plausible    : bool
        location_plausible: bool
        notes        : Liste von Erläuterungen
    """
    result = ChronoResult(
        claimed_lat       = claimed_lat,
        claimed_lon       = claimed_lon,
        claimed_datetime  = claimed_datetime,
    )

    # ── Datetime parsen ──────────────────────────────────────────────────────
    dt_utc = None
    try:
        from datetime import datetime as _dt, timezone as _tz
        s = claimed_datetime.replace("Z", "+00:00")
        if "T" in s:
            dt_utc = _dt.fromisoformat(s)
            if dt_utc.tzinfo is None:
                import datetime as _dtmod
                dt_utc = dt_utc.replace(tzinfo=_tz.utc)
        else:
            # Nur Datum — Mittag nehmen
            dt_utc = _dt.fromisoformat(s + "T12:00:00").replace(tzinfo=_tz.utc)
    except Exception as e:
        result.notes.append(f"⚠ Datetime parse-Fehler: {e}")

    # ── Bild laden ───────────────────────────────────────────────────────────
    image_data = None
    if image_path_or_url:
        try:
            if image_path_or_url.startswith("http"):
                r = requests.get(image_path_or_url, timeout=10)
                r.raise_for_status()
                image_data = r.content
            else:
                p = Path(image_path_or_url)
                if p.exists():
                    image_data = p.read_bytes()
        except Exception as e:
            result.notes.append(f"⚠ Bild-Ladefehler: {e}")

    # ── Sonnen-Analyse ───────────────────────────────────────────────────────
    if dt_utc:
        sun = _compute_sun_position(claimed_lat, claimed_lon, dt_utc)
        result.expected_sun_alt_deg = sun.get("alt_deg", 0)
        result.expected_sun_az_deg  = sun.get("az_deg", 0)
        result.solar_noon_utc       = sun.get("noon", "")
        result.sunrise_utc          = sun.get("sunrise", "")
        result.sunset_utc           = sun.get("sunset", "")

        # Ist überhaupt Tageslicht?
        if sun.get("ist_tag", False):
            result.time_plausible = True
            result.notes.append(
                f"☀ Sonne {sun['alt_deg']:.0f}° hoch, Azimut {sun['az_deg']:.0f}° "
                f"→ Schatten Richtung {sun.get('shadow_dir_deg',0):.0f}°"
            )
        else:
            result.time_plausible = False
            result.notes.append(
                f"🌙 Sonne unter dem Horizont ({sun['alt_deg']:.0f}°) zum behaupteten Zeitpunkt"
            )

        # Schatten-Richtung aus Bild extrahieren
        if image_data:
            obs_shadow = _detect_shadow_direction(image_data)
            if obs_shadow is not None:
                result.observed_shadow_dir = obs_shadow
                expected_shadow = sun.get("shadow_dir_deg", 0)
                delta = abs(obs_shadow - expected_shadow)
                if delta > 180:
                    delta = 360 - delta
                # Toleranz ±40° (Bildkompression, Kamera-Ausrichtung)
                result.shadow_consistent = delta <= 40
                result.notes.append(
                    f"{'✅' if result.shadow_consistent else '❌'} "
                    f"Schatten beobachtet {obs_shadow:.0f}° | "
                    f"erwartet {expected_shadow:.0f}° | Abweichung {delta:.0f}°"
                )
            else:
                result.notes.append("ℹ Schatten-Richtung nicht automatisch extrahierbar")
    else:
        result.notes.append("⚠ Zeitpunkt unbekannt — Sonnen-Check übersprungen")

    # ── OSM Landmarken-Check ─────────────────────────────────────────────────
    if check_osm and claimed_lat and claimed_lon:
        # Nominatim-Reverse-Geocoding
        try:
            rev_url = (f"https://nominatim.openstreetmap.org/reverse"
                       f"?lat={claimed_lat}&lon={claimed_lon}&format=json")
            r = requests.get(rev_url,
                             headers={"User-Agent": NOMINATIM_UA},
                             timeout=10)
            if r.ok:
                data = r.json()
                result.osm_place_name = (data.get("display_name") or "")[:100]
                result.location_plausible = True
                result.notes.append(f"📍 OSM: {result.osm_place_name[:70]}")
        except Exception:
            pass

        # Overpass: Gebäude + Landmarken in der Nähe
        landmarks = _overpass_nearby(claimed_lat, claimed_lon, radius_m=300)
        result.landmark_matches = landmarks
        if landmarks:
            result.notes.append(
                f"🏛 {len(landmarks)} OSM-Objekte in 300m: "
                + ", ".join(f"{lm['name']}" for lm in landmarks[:3])
            )
        else:
            result.notes.append("ℹ Keine benannten Objekte in 300m (abgelegen oder ungemappt)")

    # ── Gesamt-Konfidenz und Verdict ─────────────────────────────────────────
    score = 0.0
    factors = 0

    if dt_utc:
        factors += 1
        score   += 1.0 if result.time_plausible else 0.0

    if result.observed_shadow_dir is not None:
        factors += 1
        score   += 1.0 if result.shadow_consistent else 0.0

    if result.location_plausible:
        factors += 1
        score   += 1.0

    if result.landmark_matches:
        factors += 1
        score   += 1.0   # Landmarks gefunden = höhere Konfidenz

    if factors == 0:
        result.confidence = 0.1
        result.verdict    = "INSUFFICIENT_DATA"
    else:
        result.confidence = round(score / factors, 2)
        if result.confidence >= 0.75:
            result.verdict = "PLAUSIBLE"
        elif result.confidence >= 0.50:
            result.verdict = "SUSPICIOUS"
        elif result.confidence >= 0.25:
            result.verdict = "INCONSISTENT"
        else:
            result.verdict = "INCONSISTENT"

    # Sonderfall: expliziter Nacht-Fehler
    if dt_utc and not sun.get("ist_tag", True):
        result.verdict    = "INCONSISTENT"
        result.confidence = min(result.confidence, 0.2)

    return result.to_dict()


def chronolocation_batch(
    items: list[dict],
    default_region: str = "",
) -> list[dict]:
    """
    Batch-Verifikation einer Liste von Bild-Items.
    Jedes Item: {"url": str, "lat": float, "lon": float, "datetime": str,
                 "source": str}
    Returns: Liste der ChronoResult-Dicts mit original item-Feldern.
    """
    results = []
    for item in items:
        url  = item.get("url") or item.get("image_url") or ""
        lat  = float(item.get("lat") or item.get("latitude") or 0)
        lon  = float(item.get("lon") or item.get("longitude") or 0)
        dt   = item.get("datetime") or item.get("date") or item.get("timestamp") or ""
        if isinstance(dt, (int, float)):
            from datetime import datetime as _dt, timezone as _tz
            dt = _dt.fromtimestamp(dt, tz=_tz.utc).isoformat()
        chrono = verify_chronolocation(
            url, lat, lon, str(dt),
            region_hint=default_region,
        )
        chrono["source"]  = item.get("source", "")
        chrono["item_url"] = url
        results.append(chrono)
    return results


# CLI-Test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    test_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/6/65/T-72B3_tank.jpg/640px-T-72B3_tank.jpg"
    print(f"Geolocating: {test_url}")
    result = geolocate_image(test_url, region="Russia", geo_hint="Moskau Oblast")
    print(f"  Methode:   {result.method}")
    print(f"  Konfidenz: {result.confidence:.0%}")
    if result.lat:
        print(f"  Koordinaten: {result.lat:.4f}, {result.lon:.4f}")
    if result.place_name:
        print(f"  Ort: {result.place_name}")
    if result.timestamp_hint:
        print(f"  Zeit: {result.timestamp_hint}")

    print("\n─ Chronolocation Test ─")
    chrono = verify_chronolocation(
        test_url,
        claimed_lat=33.5138,
        claimed_lon=36.2765,   # Damaskus
        claimed_datetime="2024-10-15T10:30:00",
        region_hint="Syria",
    )
    print(f"  Verdict:   {chrono['verdict']}")
    print(f"  Konfidenz: {chrono['confidence']:.0%}")
    print(f"  Sonne:     {chrono['expected_sun_alt_deg']:.0f}° Höhe, {chrono['expected_sun_az_deg']:.0f}° Azimut")
    for note in chrono['notes']:
        print(f"  {note}")
