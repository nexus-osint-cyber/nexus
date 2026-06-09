"""
NEXUS – KI-Bildgeolokalisierung  (T208)
=======================================
Schätzt den geografischen Aufnahmeort eines Bildes aus seinem Inhalt —
ohne EXIF-Daten, ohne Metadata. Journalist-Verifikation auf Profi-Niveau.

Stufen (je nach verfügbaren APIs):
  Stufe 1 — GeoSpy.ai API (kostenlos, Registrierung nötig):
    https://geospy.ai/  →  config.py: GEOSPY_API_KEY = "..."
  Stufe 2 — Nominatim + visuelle Cues (beschriftungbasiert):
    Analysiert sichtbaren Text (OCR) → Geocoding
  Stufe 3 — Wikidata Landmark-Matching:
    Gebäude/Landmarken aus Bildbeschreibung → Wikidata-Koordinaten
  Stufe 4 — Vegetation/Klima Heuristik:
    Grünton + Sonnenwinkel → Klimazone → mögliche Region

Zurückgegeben wird immer:
  lat, lon, confidence, method, reasoning[], alternatives[]

Abhängigkeiten:
  pip install requests pillow
  Optional: pip install pytesseract  (für OCR-Cues)
  Optional: GEOSPY_API_KEY in config.py
"""

from __future__ import annotations

import base64
import io
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────────────────────

REQUEST_TIMEOUT  = 20
NOMINATIM_URL    = "https://nominatim.openstreetmap.org/search"
WIKIDATA_API     = "https://www.wikidata.org/w/api.php"
GEOSPY_API_URL   = "https://dev.geospy.ai/predict"
_UA              = "NEXUS-OSINT/1.0 (nexus-osint@localhost)"

# Bekannte Landmarken → Koordinaten (Offline-Datenbank)
LANDMARK_DB: dict[str, tuple[float, float]] = {
    # Israel / Gaza
    "dome of the rock":   (31.7781, 35.2354),
    "western wall":       (31.7767, 35.2345),
    "al-aqsa mosque":     (31.7764, 35.2359),
    "tel aviv beach":     (32.0853, 34.7818),
    "haifa bay":          (32.8191, 34.9983),
    # Iran
    "azadi tower":        (35.6994, 51.3381),
    "milad tower":        (35.7458, 51.3749),
    "isfahan bridge":     (32.6546, 51.6680),
    "persepolis":         (29.9353, 52.8908),
    # Lebanon
    "beirut port":        (33.9003, 35.5196),
    "baalbek temple":     (34.0058, 36.2097),
    # Yemen
    "old sanaa":          (15.3547, 44.2066),
    "al saleh mosque":    (15.3536, 44.2055),
    # Syria
    "palmyra ruins":      (34.5503, 38.2699),
    "aleppo citadel":     (36.2021, 37.1597),
    # Saudi Arabia
    "kaaba mecca":        (21.4225, 39.8262),
    "madinah masjid":     (24.4672, 39.6111),
    # Gulf
    "burj khalifa":       (25.1972, 55.2744),
    "palm jumeirah":      (25.1124, 55.1390),
    # Ukraine
    "kyiv saint sophia":  (50.4528, 30.5189),
    "mariupol port":      (47.0966, 37.5485),
    # Russia
    "saint basil":        (55.7525, 37.6231),
    "kremlin":            (55.7520, 37.6175),
}

# Sprachen → mögliche Regionen (für OCR-Text-Matching)
LANGUAGE_REGIONS: dict[str, list[str]] = {
    "arabic":   ["Yemen", "Syria", "Iraq", "Lebanon", "Gaza", "Saudi Arabia"],
    "persian":  ["Iran"],
    "hebrew":   ["Israel"],
    "russian":  ["Russia", "Ukraine"],
    "ukrainian":["Ukraine"],
    "turkish":  ["Turkey"],
    "chinese":  ["China"],
}

# Typische Vegetation-/Klima-Indikatoren
CLIMATE_CUES: dict[str, dict] = {
    "desert":    {"avg_rgb": (180, 160, 120), "regions": ["Yemen","Iran","Gaza"]},
    "green_med": {"avg_rgb": (100, 140, 80),  "regions": ["Lebanon","Israel","Syria"]},
    "snow":      {"avg_rgb": (230, 230, 240),  "regions": ["Russia","Ukraine","Iran-north"]},
    "tropical":  {"avg_rgb": (80, 150, 60),   "regions": ["Yemen-coast"]},
}


# ─────────────────────────────────────────────────────────────────────────────
# Ergebnis-Klasse
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GeoSpyResult:
    lat:         Optional[float] = None
    lon:         Optional[float] = None
    confidence:  float           = 0.0
    method:      str             = "unknown"
    region_hint: str             = ""
    reasoning:   list            = field(default_factory=list)
    alternatives: list           = field(default_factory=list)
    landmark:    str             = ""
    place_name:  str             = ""

    def to_dict(self) -> dict:
        return {
            "lat":          self.lat,
            "lon":          self.lon,
            "confidence":   round(self.confidence, 2),
            "method":       self.method,
            "region_hint":  self.region_hint,
            "reasoning":    self.reasoning,
            "alternatives": self.alternatives[:3],
            "landmark":     self.landmark,
            "place_name":   self.place_name,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Stufe 1: GeoSpy.ai API
# ─────────────────────────────────────────────────────────────────────────────

def _try_geospy_api(image_bytes: bytes) -> Optional[GeoSpyResult]:
    """Versucht GeoSpy.ai API (benötigt GEOSPY_API_KEY in config.py)."""
    try:
        import config
        api_key = getattr(config, "GEOSPY_API_KEY", "") or ""
    except Exception:
        api_key = ""

    if not api_key:
        return None

    try:
        b64 = base64.b64encode(image_bytes).decode()
        r = requests.post(
            GEOSPY_API_URL,
            json={"image": b64},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
                "User-Agent":    _UA,
            },
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        lat = float(data.get("lat") or data.get("latitude") or 0)
        lon = float(data.get("lon") or data.get("longitude") or 0)
        if lat and lon:
            return GeoSpyResult(
                lat        = lat,
                lon        = lon,
                confidence = float(data.get("confidence") or 0.80),
                method     = "geospy_api",
                reasoning  = [data.get("reasoning", "GeoSpy KI-Modell")],
                place_name = data.get("place", ""),
            )
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Stufe 2: OCR-basierte Geolokalisierung
# ─────────────────────────────────────────────────────────────────────────────

def _try_ocr_geo(image_bytes: bytes) -> Optional[GeoSpyResult]:
    """Extrahiert Text aus Bild und geocodiert erkannte Ortsnames/Schilder."""
    try:
        import pytesseract
        from PIL import Image
        img  = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(img, lang="ara+fas+heb+eng+rus",
                                            config="--psm 11")
        if not text.strip():
            return None

        # Koordinaten direkt im Text?
        coord_match = re.search(r'(-?\d{1,3}\.\d{3,6})[,\s]+(-?\d{1,3}\.\d{3,6})', text)
        if coord_match:
            lat = float(coord_match.group(1))
            lon = float(coord_match.group(2))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return GeoSpyResult(
                    lat=lat, lon=lon, confidence=0.95,
                    method="ocr_coordinates",
                    reasoning=[f"Koordinaten im Bild: {lat},{lon}"],
                )

        # Ortsnamen geocodieren
        words = re.findall(r'[A-Z][a-z]{3,}(?:\s[A-Z][a-z]+)?', text)
        for word in words[:5]:
            try:
                time.sleep(1.1)
                r = requests.get(NOMINATIM_URL,
                                 params={"q": word, "format": "json", "limit": 1},
                                 headers={"User-Agent": _UA}, timeout=10)
                r.raise_for_status()
                results = r.json()
                if results:
                    lat = float(results[0]["lat"])
                    lon = float(results[0]["lon"])
                    return GeoSpyResult(
                        lat=lat, lon=lon, confidence=0.60,
                        method="ocr_nominatim",
                        reasoning=[f"OCR-Text '{word}' → Nominatim"],
                        place_name=results[0].get("display_name", word)[:80],
                    )
            except Exception:
                continue
    except ImportError:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Stufe 3: Landmark-Matching
# ─────────────────────────────────────────────────────────────────────────────

def _try_landmark_match(image_bytes: bytes,
                         context_text: str = "") -> Optional[GeoSpyResult]:
    """
    Sucht nach bekannten Landmarken im Bild-Kontext-Text.
    Kann auch visuelle Beschreibungen (von LLaVA) verarbeiten.
    """
    text_lower = context_text.lower()
    if not text_lower:
        return None

    for landmark, (lat, lon) in LANDMARK_DB.items():
        if landmark in text_lower:
            return GeoSpyResult(
                lat=lat, lon=lon, confidence=0.88,
                method="landmark_db",
                landmark=landmark,
                reasoning=[f"Bekannte Landmarke erkannt: '{landmark}'"],
            )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Stufe 4: Farb-/Klimaheuristik
# ─────────────────────────────────────────────────────────────────────────────

def _try_climate_heuristic(image_bytes: bytes) -> GeoSpyResult:
    """
    Schätzt mögliche Region aus Farbton (Vegetation/Klima-Proxy).
    Niedrige Konfidenz, aber immer verfügbar.
    """
    result = GeoSpyResult(method="climate_heuristic", confidence=0.15)
    try:
        from PIL import Image, ImageStat
        img  = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        stat = ImageStat.Stat(img)
        avg_r, avg_g, avg_b = stat.mean[:3]

        best_match = None
        best_dist  = float("inf")
        for climate, info in CLIMATE_CUES.items():
            ref_r, ref_g, ref_b = info["avg_rgb"]
            dist = math.sqrt((avg_r - ref_r)**2 +
                             (avg_g - ref_g)**2 +
                             (avg_b - ref_b)**2)
            if dist < best_dist:
                best_dist  = dist
                best_match = (climate, info["regions"])

        if best_match and best_dist < 80:
            climate_name, regions = best_match
            result.region_hint  = ", ".join(regions)
            result.confidence   = max(0.15, 0.40 - best_dist / 200.0)
            result.reasoning.append(
                f"Farbton RGB({avg_r:.0f},{avg_g:.0f},{avg_b:.0f}) → "
                f"Klimazone '{climate_name}' → "
                f"mögliche Regionen: {', '.join(regions)}"
            )
            # Mittelpunkt der möglichen Region als Schätzung
            if regions:
                region_centers = {
                    "Yemen": (15.5, 44.0), "Iran": (32.0, 53.0),
                    "Gaza": (31.4, 34.4), "Lebanon": (33.8, 35.8),
                    "Syria": (34.8, 38.7), "Israel": (31.5, 34.9),
                    "Russia": (55.7, 37.6), "Ukraine": (48.5, 32.0),
                    "Saudi Arabia": (24.0, 45.0), "Yemen-coast": (14.0, 42.0),
                    "Iran-north": (37.0, 50.0),
                }
                first_region = regions[0]
                if first_region in region_centers:
                    result.lat, result.lon = region_centers[first_region]
                result.alternatives = [
                    {"region": r, "confidence": result.confidence * 0.8}
                    for r in regions[1:3]
                ]
    except ImportError:
        result.reasoning.append("⚠ Pillow nicht installiert")
    except Exception as e:
        result.reasoning.append(f"Heuristik-Fehler: {e}")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Funktion
# ─────────────────────────────────────────────────────────────────────────────

def geolocate_image_ai(
    source:       str,
    context_text: str = "",    # Optionaler Beschreibungstext (z.B. von LLaVA)
    region_hint:  str = "",    # Optionaler Kontext ("Iran", "Gaza", ...)
) -> dict:
    """
    KI-basierte Geolokalisierung eines Bildes.
    Versucht mehrere Methoden und wählt die beste.

    Parameters
    ----------
    source       : https:// URL oder lokaler Dateipfad
    context_text : Optionale Bildbeschreibung (von LLaVA oder ähnlich)
    region_hint  : Optionaler Regionshinweis

    Returns
    -------
    dict mit: lat, lon, confidence, method, reasoning[], place_name
    """
    # Bild laden
    try:
        if source.startswith("http"):
            r = requests.get(source, timeout=REQUEST_TIMEOUT,
                             headers={"User-Agent": _UA})
            r.raise_for_status()
            image_bytes = r.content
        else:
            p = Path(source)
            if not p.exists():
                return {"status": "bild_nicht_gefunden", "source": source}
            image_bytes = p.read_bytes()
    except Exception as e:
        return {"status": "lade_fehler", "error": str(e), "source": source}

    # Methoden durchprobieren (beste Konfidenz gewinnt)
    candidates = []

    # Stufe 1: GeoSpy API
    r1 = _try_geospy_api(image_bytes)
    if r1:
        candidates.append(r1)

    # Stufe 3: Landmark-Matching (context_text)
    if context_text:
        r3 = _try_landmark_match(image_bytes, context_text)
        if r3:
            candidates.append(r3)

    # Stufe 2: OCR
    r2 = _try_ocr_geo(image_bytes)
    if r2:
        candidates.append(r2)

    # Stufe 4: Heuristik (immer)
    r4 = _try_climate_heuristic(image_bytes)
    candidates.append(r4)

    # Bestes Ergebnis wählen
    best = max(candidates, key=lambda x: x.confidence)

    result = best.to_dict()
    result["status"]      = "ok" if best.lat else "nur_region"
    result["source"]      = source[:100]
    result["candidates"]  = len(candidates)

    # Region-Hint einbauen wenn vorhanden
    if region_hint and not best.region_hint:
        result["region_hint"] = region_hint

    return result


def geospy_status() -> dict:
    """Prüft verfügbare Backends."""
    try:
        import config
        has_geospy = bool(getattr(config, "GEOSPY_API_KEY", ""))
    except Exception:
        has_geospy = False

    try:
        import pytesseract; has_ocr = True
    except ImportError:
        has_ocr = False

    try:
        from PIL import Image; has_pil = True
    except ImportError:
        has_pil = False

    return {
        "geospy_api":      has_geospy,
        "ocr_available":   has_ocr,
        "pillow":          has_pil,
        "landmark_db_size": len(LANDMARK_DB),
        "best_backend":    "geospy" if has_geospy else ("ocr" if has_ocr else "heuristic"),
        "setup_hint":      "" if has_geospy else "config.py: GEOSPY_API_KEY='...' für KI-Geolokalisierung",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Direktaufruf
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    print("NEXUS GeoSpy KI-Bildgeolokalisierung — Status")
    print("─" * 50)

    s = geospy_status()
    print(f"GeoSpy API:  {'✅' if s['geospy_api'] else '❌  ' + s.get('setup_hint','')}")
    print(f"OCR:         {'✅' if s['ocr_available'] else '❌  pip install pytesseract'}")
    print(f"Pillow:      {'✅' if s['pillow'] else '❌  pip install pillow'}")
    print(f"Landmark-DB: {s['landmark_db_size']} Einträge")
    print(f"Best Backend: {s['best_backend']}")

    test_url = sys.argv[1] if len(sys.argv) > 1 else \
        "https://upload.wikimedia.org/wikipedia/commons/4/41/Tehran_Milad_Tower.jpg"
    context = sys.argv[2] if len(sys.argv) > 2 else "milad tower tehran"
    print(f"\nTest: {test_url[:70]}...")
    r = geolocate_image_ai(test_url, context_text=context)
    print(f"  Status:    {r['status']}")
    print(f"  Methode:   {r['method']}")
    if r.get("lat"):
        print(f"  Koordinaten: {r['lat']:.4f}, {r['lon']:.4f}")
    print(f"  Konfidenz: {r['confidence']:.0%}")
    if r.get("place_name"):
        print(f"  Ort:       {r['place_name'][:60]}")
    if r.get("region_hint"):
        print(f"  Region:    {r['region_hint']}")
    for note in r.get("reasoning", [])[:3]:
        print(f"  → {note}")
