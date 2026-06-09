"""
NEXUS – Sentinel-2 Optische Change Detection  (T197)
=====================================================
Erkennt Bodenveränderungen in Konfliktzonen via Copernicus CDSE API.
Nutzt STAC-Bildpaare + Thumbnail-Pixelvergleich für strukturierte Ausgabe.

Rückgabe (echte strukturierte Daten, kein Newsletter-Text):
  detect_ground_changes(region, days_back) → list[ChangeZone]
  Jede Zone hat: lat, lon, bbox, change_type, change_score, confidence,
                 date_new, date_old, days_gap, thumbnail_url, browser_url

Verfügbare Zonen (vorkonfiguriert):
  Iran: Natanz, Fordow, Parchin, IRGC-Basis Isfahan, Kharg Island
  Israel/Gaza: Gazastreifen Nord/Süd, Südlibanon, Nordisrael
  Gulf: Strait of Hormuz, Hudaydah (Jemen), Bandar Abbas
  Ukraine: Kherson, Zaporizhzhia (Erweiterbar)

Abhängigkeiten:
  pip install requests pillow
  Copernicus-Account: https://dataspace.copernicus.eu/
  config.py: COPERNICUS_CLIENT_ID, COPERNICUS_CLIENT_SECRET
"""

from __future__ import annotations

import io
import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────────────────────

REQUEST_TIMEOUT = 20
_CACHE_DIR      = Path(__file__).parent / "nexus_s2_cache"
_CACHE_TTL_H    = 6    # Thumbnails 6h cachen

COPERNICUS_TOKEN_URL  = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CDSE_STAC_URL         = "https://catalogue.dataspace.copernicus.eu/stac/collections/SENTINEL-2/items"

# ─────────────────────────────────────────────────────────────────────────────
# Vordefinierte Konflikt-AOIs (Area of Interest)
# lat_min, lon_min, lat_max, lon_max
# ─────────────────────────────────────────────────────────────────────────────

CONFLICT_AOIS: dict[str, dict] = {
    # ── Iran ──────────────────────────────────────────────────────────────────
    "Natanz_Nuclear":       {"lat": 33.724, "lon": 51.726, "margin": 0.12,
                             "region": "Iran", "type": "nuclear_facility"},
    "Fordow_Nuclear":       {"lat": 34.884, "lon": 50.993, "margin": 0.08,
                             "region": "Iran", "type": "nuclear_facility"},
    "Parchin_Military":     {"lat": 35.519, "lon": 51.784, "margin": 0.10,
                             "region": "Iran", "type": "military_base"},
    "Isfahan_IRGC":         {"lat": 32.617, "lon": 51.677, "margin": 0.15,
                             "region": "Iran", "type": "military_base"},
    "Kharg_Island":         {"lat": 29.243, "lon": 50.323, "margin": 0.10,
                             "region": "Iran", "type": "oil_terminal"},
    "Bandar_Abbas_Port":    {"lat": 27.195, "lon": 56.278, "margin": 0.12,
                             "region": "Iran", "type": "naval_port"},
    # ── Israel / Gaza / Libanon ───────────────────────────────────────────────
    "Gaza_North":           {"lat": 31.55,  "lon": 34.50,  "margin": 0.12,
                             "region": "Gaza", "type": "conflict_zone"},
    "Gaza_South":           {"lat": 31.25,  "lon": 34.35,  "margin": 0.12,
                             "region": "Gaza", "type": "conflict_zone"},
    "Rafah_Crossing":       {"lat": 31.277, "lon": 34.252, "margin": 0.06,
                             "region": "Gaza", "type": "border_crossing"},
    "South_Lebanon":        {"lat": 33.20,  "lon": 35.55,  "margin": 0.15,
                             "region": "Lebanon", "type": "conflict_zone"},
    "Dahieh_Beirut":        {"lat": 33.828, "lon": 35.506, "margin": 0.06,
                             "region": "Lebanon", "type": "urban_target"},
    # ── Jemen / Rotes Meer ────────────────────────────────────────────────────
    "Hudaydah_Port":        {"lat": 14.797, "lon": 42.963, "margin": 0.10,
                             "region": "Yemen", "type": "naval_port"},
    "Sanaa_Military":       {"lat": 15.354, "lon": 44.206, "margin": 0.10,
                             "region": "Yemen", "type": "military_base"},
    "Hodeida_Coastline":    {"lat": 14.950, "lon": 42.800, "margin": 0.20,
                             "region": "Yemen", "type": "coastline"},
    # ── Straße von Hormus ─────────────────────────────────────────────────────
    "Strait_of_Hormuz":     {"lat": 26.56,  "lon": 56.27,  "margin": 0.25,
                             "region": "Gulf", "type": "strategic_waterway"},
    "Abu_Musa_Island":      {"lat": 25.876, "lon": 55.033, "margin": 0.08,
                             "region": "Gulf", "type": "military_installation"},
    # ── Irak ──────────────────────────────────────────────────────────────────
    "Ain_al_Asad_Airbase":  {"lat": 33.785, "lon": 42.441, "margin": 0.12,
                             "region": "Iraq", "type": "military_base"},
}

# Region → relevante AOIs
REGION_MAP: dict[str, list[str]] = {
    "Iran":    ["Natanz_Nuclear","Fordow_Nuclear","Parchin_Military",
                "Isfahan_IRGC","Kharg_Island","Bandar_Abbas_Port"],
    "Gaza":    ["Gaza_North","Gaza_South","Rafah_Crossing"],
    "Israel":  ["Gaza_North","Gaza_South","South_Lebanon"],
    "Lebanon": ["South_Lebanon","Dahieh_Beirut"],
    "Yemen":   ["Hudaydah_Port","Sanaa_Military","Hodeida_Coastline"],
    "Gulf":    ["Strait_of_Hormuz","Abu_Musa_Island","Bandar_Abbas_Port"],
    "Iraq":    ["Ain_al_Asad_Airbase"],
}

# ─────────────────────────────────────────────────────────────────────────────
# Ergebnis-Klasse
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ChangeZone:
    name:         str
    region:       str
    aoi_type:     str
    lat:          float
    lon:          float
    bbox:         list[float]         # [lat_min, lon_min, lat_max, lon_max]
    change_score: float               # 0.0 – 1.0 (normiert)
    change_type:  str                 # vegetation_loss | construction | damage | no_change
    confidence:   float               # 0.0 – 1.0
    date_new:     str                 # YYYY-MM-DD
    date_old:     str                 # YYYY-MM-DD
    days_gap:     int
    cloud_new:    float
    cloud_old:    float
    scene_id_new: str   = ""
    scene_id_old: str   = ""
    thumbnail_new: str  = ""
    thumbnail_old: str  = ""
    browser_url:  str   = ""
    pixel_diff_pct: float = 0.0      # % der Pixel mit signifikanter Änderung
    notes:        str   = ""

    def to_dict(self) -> dict:
        return {
            "name":           self.name,
            "region":         self.region,
            "aoi_type":       self.aoi_type,
            "lat":            self.lat,
            "lon":            self.lon,
            "bbox":           self.bbox,
            "change_score":   round(self.change_score, 3),
            "change_type":    self.change_type,
            "confidence":     round(self.confidence, 2),
            "date_new":       self.date_new,
            "date_old":       self.date_old,
            "days_gap":       self.days_gap,
            "cloud_new":      self.cloud_new,
            "cloud_old":      self.cloud_old,
            "pixel_diff_pct": round(self.pixel_diff_pct, 1),
            "thumbnail_new":  self.thumbnail_new,
            "thumbnail_old":  self.thumbnail_old,
            "browser_url":    self.browser_url,
            "notes":          self.notes,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────────────────

_token_cache: dict = {"token": None, "expires": 0.0}

def _get_token() -> Optional[str]:
    """Cached OAuth2 Token vom CDSE Identity Service."""
    now = time.time()
    if _token_cache["token"] and _token_cache["expires"] > now + 30:
        return _token_cache["token"]
    try:
        import config
        cid = getattr(config, "COPERNICUS_CLIENT_ID",     "") or ""
        sec = getattr(config, "COPERNICUS_CLIENT_SECRET", "") or ""
    except Exception:
        return None
    if not cid or not sec:
        return None
    try:
        r = requests.post(
            COPERNICUS_TOKEN_URL,
            data={"grant_type": "client_credentials",
                  "client_id": cid, "client_secret": sec},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        _token_cache["token"]   = data.get("access_token")
        _token_cache["expires"] = now + data.get("expires_in", 600) - 10
        return _token_cache["token"]
    except Exception as e:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# STAC Szenen-Suche
# ─────────────────────────────────────────────────────────────────────────────

def _search_scenes(bbox: list[float], days_back: int = 60,
                   max_cloud: int = 25) -> list[dict]:
    """Sucht Sentinel-2 L2A Szenen via CDSE STAC (kein Auth nötig)."""
    lat_min, lon_min, lat_max, lon_max = bbox
    dt_end   = datetime.now(timezone.utc)
    dt_start = dt_end - timedelta(days=days_back)
    params = {
        "bbox":     f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "datetime": (f"{dt_start.strftime('%Y-%m-%dT%H:%M:%SZ')}/"
                     f"{dt_end.strftime('%Y-%m-%dT%H:%M:%SZ')}"),
        "limit": 20,
        "collections": "SENTINEL-2",
    }
    try:
        r = requests.get(CDSE_STAC_URL, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        features = r.json().get("features") or []
        scenes = []
        for f in features:
            props = f.get("properties") or {}
            cloud = props.get("eo:cloud_cover", 100)
            if cloud > max_cloud:
                continue
            scenes.append({
                "id":        f.get("id", ""),
                "date":      (props.get("datetime") or "")[:10],
                "cloud_pct": round(float(cloud), 1),
                "thumbnail": ((f.get("assets") or {})
                              .get("thumbnail", {}).get("href", "")
                              or (f.get("assets") or {})
                              .get("QUICKLOOK", {}).get("href", "")),
                "bbox":      f.get("bbox"),
            })
        # Sortieren: neueste zuerst
        scenes.sort(key=lambda s: s["date"], reverse=True)
        return scenes
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Thumbnail-Download + Pixel-Differenz
# ─────────────────────────────────────────────────────────────────────────────

def _download_thumbnail(url: str, token: Optional[str] = None) -> Optional[bytes]:
    """Lädt Thumbnail herunter (mit oder ohne Auth)."""
    if not url:
        return None
    try:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        return r.content
    except Exception:
        return None


def _pixel_diff(img1_bytes: bytes, img2_bytes: bytes,
                threshold: int = 30) -> dict:
    """
    Berechnet Pixel-Differenz zwischen zwei Thumbnails.
    Gibt change_score (0-1), pixel_diff_pct und groben change_type zurück.
    """
    try:
        from PIL import Image, ImageFilter
        img1 = Image.open(io.BytesIO(img1_bytes)).convert("RGB").resize((256, 256))
        img2 = Image.open(io.BytesIO(img2_bytes)).convert("RGB").resize((256, 256))

        px1 = list(img1.getdata())
        px2 = list(img2.getdata())
        total = len(px1)
        if total == 0:
            return {"change_score": 0, "pixel_diff_pct": 0, "change_type": "no_data"}

        # Pixel-Differenz
        diff_pixels = 0
        channel_sums = [0, 0, 0]
        for (r1,g1,b1), (r2,g2,b2) in zip(px1, px2):
            d = (abs(r1-r2) + abs(g1-g2) + abs(b1-b2)) / 3
            if d > threshold:
                diff_pixels += 1
                channel_sums[0] += (r2 - r1)
                channel_sums[1] += (g2 - g1)
                channel_sums[2] += (b2 - b1)

        diff_pct    = diff_pixels / total * 100
        change_score = min(1.0, diff_pct / 25.0)   # 25% Pixel-Diff = Score 1.0

        # Change-Typ aus Farbkanal-Verschiebung ableiten
        if diff_pixels > 0:
            avg_dr = channel_sums[0] / diff_pixels
            avg_dg = channel_sums[1] / diff_pixels
            avg_db = channel_sums[2] / diff_pixels
            # Grünkanal-Verlust = Vegetation zerstört
            if avg_dg < -15:
                change_type = "vegetation_loss"
            # Grauton-Zunahme = neue Bebauung oder Krater
            elif abs(avg_dr) < 10 and abs(avg_dg) < 10 and diff_pct > 5:
                change_type = "structural_change"
            # Rotkanal-Zunahme = Feuer/Brand-Spuren
            elif avg_dr > 20:
                change_type = "burn_scar"
            elif diff_pct > 8:
                change_type = "significant_change"
            elif diff_pct > 3:
                change_type = "minor_change"
            else:
                change_type = "no_change"
        else:
            change_type = "no_change"

        return {
            "change_score":   round(change_score, 3),
            "pixel_diff_pct": round(diff_pct, 1),
            "change_type":    change_type,
        }

    except ImportError:
        # Pillow nicht installiert — nur Metadaten-basierte Einschätzung
        return {"change_score": 0.0, "pixel_diff_pct": 0.0,
                "change_type": "no_pillow"}
    except Exception:
        return {"change_score": 0.0, "pixel_diff_pct": 0.0,
                "change_type": "error"}


def _confidence_from_meta(scene_new: dict, scene_old: dict,
                           days_gap: int, pixel_result: dict) -> float:
    """Berechnet Konfidenz basierend auf Cloud-Cover + Zeitabstand + Diff-Qualität."""
    cloud_penalty = (scene_new["cloud_pct"] + scene_old["cloud_pct"]) / 2.0 / 100.0
    cloud_factor  = 1.0 - cloud_penalty * 0.6   # bis -60% bei 100% Wolken

    # Zu kleiner oder zu großer Zeitabstand reduziert Konfidenz
    if days_gap < 3:
        time_factor = 0.5
    elif days_gap < 10:
        time_factor = 0.85
    elif days_gap > 120:
        time_factor = 0.65
    else:
        time_factor = 1.0

    # Pillow nicht verfügbar
    if pixel_result.get("change_type") in ("no_pillow", "error"):
        pixel_factor = 0.5   # Niedrige Konfidenz ohne Pixel-Analyse
    else:
        pixel_factor = 1.0

    base_conf = 0.70 if pixel_result.get("change_score", 0) > 0.1 else 0.40
    return round(min(0.95, base_conf * cloud_factor * time_factor * pixel_factor), 2)


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Funktion
# ─────────────────────────────────────────────────────────────────────────────

def detect_ground_changes(
    region:    str,
    days_back: int  = 60,
    max_cloud: int  = 25,
    min_score: float = 0.0,   # Nur Zonen mit change_score >= min_score zurückgeben
    aoi_names: Optional[list[str]] = None,  # Spezifische AOIs, None = alle der Region
) -> list[dict]:
    """
    Erkennt Bodenveränderungen in Konfliktzonen via Sentinel-2 Bildpaare.

    Parameters
    ----------
    region    : z.B. "Iran", "Gaza", "Yemen", "Lebanon", "Gulf"
    days_back : Wie weit zurück suchen (Standard 60 Tage)
    max_cloud : Max Wolkenbedeckung in % (Standard 25)
    min_score : Mindestscore für Rückgabe (0.0 = alles)
    aoi_names : Optionale AOI-Auswahl; None = alle für die Region

    Returns
    -------
    list[dict] — Strukturierte Veränderungs-Daten mit lat/lon/score/type
    """
    token = _get_token()

    # AOIs für die Region bestimmen
    if aoi_names:
        selected = {k: v for k, v in CONFLICT_AOIS.items() if k in aoi_names}
    else:
        # Region-Map prüfen (case-insensitive)
        region_key = next(
            (k for k in REGION_MAP if k.lower() == region.lower()), None
        )
        if region_key:
            aoi_names_list = REGION_MAP[region_key]
            selected = {k: CONFLICT_AOIS[k] for k in aoi_names_list
                        if k in CONFLICT_AOIS}
        else:
            # Fallback: alle AOIs deren region-Feld passt
            selected = {k: v for k, v in CONFLICT_AOIS.items()
                        if v["region"].lower() == region.lower()}

    if not selected:
        return []

    results: list[dict] = []

    for aoi_name, aoi in selected.items():
        lat, lon, margin = aoi["lat"], aoi["lon"], aoi["margin"]
        bbox = [lat - margin, lon - margin, lat + margin, lon + margin]

        # Szenen suchen
        scenes = _search_scenes(bbox, days_back=days_back, max_cloud=max_cloud)
        if len(scenes) < 2:
            # Wenige Szenen → Fallback mit höherem Cloud-Limit
            scenes = _search_scenes(bbox, days_back=days_back, max_cloud=60)
        if len(scenes) < 2:
            continue

        scene_new = scenes[0]
        scene_old = scenes[-1]

        days_gap = 0
        try:
            d_new = datetime.strptime(scene_new["date"], "%Y-%m-%d")
            d_old = datetime.strptime(scene_old["date"], "%Y-%m-%d")
            days_gap = (d_new - d_old).days
        except Exception:
            pass

        # Thumbnails herunterladen und vergleichen
        pixel_result = {"change_score": 0.0, "pixel_diff_pct": 0.0,
                        "change_type": "no_data"}

        if scene_new.get("thumbnail") and scene_old.get("thumbnail"):
            img_new = _download_thumbnail(scene_new["thumbnail"], token)
            img_old = _download_thumbnail(scene_old["thumbnail"], token)
            if img_new and img_old:
                pixel_result = _pixel_diff(img_new, img_old)

        if pixel_result["change_score"] < min_score:
            continue

        conf = _confidence_from_meta(scene_new, scene_old, days_gap, pixel_result)

        browser_url = (
            f"https://browser.dataspace.copernicus.eu/"
            f"?zoom=13&lat={lat}&lng={lon}"
            f"&themeId=DEFAULT-THEME&datasetId=S2_L2A_CDAS"
        )

        zone = ChangeZone(
            name          = aoi_name,
            region        = aoi["region"],
            aoi_type      = aoi["type"],
            lat           = lat,
            lon           = lon,
            bbox          = bbox,
            change_score  = pixel_result["change_score"],
            change_type   = pixel_result["change_type"],
            confidence    = conf,
            date_new      = scene_new["date"],
            date_old      = scene_old["date"],
            days_gap      = days_gap,
            cloud_new     = scene_new["cloud_pct"],
            cloud_old     = scene_old["cloud_pct"],
            scene_id_new  = scene_new.get("id", ""),
            scene_id_old  = scene_old.get("id", ""),
            thumbnail_new = scene_new.get("thumbnail", ""),
            thumbnail_old = scene_old.get("thumbnail", ""),
            browser_url   = browser_url,
            pixel_diff_pct= pixel_result["pixel_diff_pct"],
            notes         = (f"Scenes found: {len(scenes)} | "
                             f"Cloud Ø {(scene_new['cloud_pct']+scene_old['cloud_pct'])/2:.0f}%"),
        )
        results.append(zone.to_dict())

    # Sortieren: höchster Change-Score zuerst
    results.sort(key=lambda z: z["change_score"], reverse=True)
    return results


def sentinel2_status() -> dict:
    """Setup-Status prüfen."""
    token = _get_token()
    try:
        import config
        has_creds = bool(
            getattr(config, "COPERNICUS_CLIENT_ID", "") and
            getattr(config, "COPERNICUS_CLIENT_SECRET", "")
        )
    except Exception:
        has_creds = False
    try:
        from PIL import Image
        has_pillow = True
    except ImportError:
        has_pillow = False
    return {
        "credentials":   has_creds,
        "auth_ok":       bool(token),
        "pillow":        has_pillow,
        "aoi_count":     len(CONFLICT_AOIS),
        "regions":       list(REGION_MAP.keys()),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Direktaufruf
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    region = sys.argv[1] if len(sys.argv) > 1 else "Gaza"
    print(f"NEXUS Sentinel-2 Change Detection — Region: {region}")
    print("─" * 60)

    status = sentinel2_status()
    print(f"Credentials: {'✅' if status['credentials'] else '❌'}")
    print(f"Auth OK:     {'✅' if status['auth_ok'] else '❌  (Token-Fehler — Credentials prüfen)'}")
    print(f"Pillow:      {'✅' if status['pillow'] else '⚠  pip install pillow  (für Pixel-Diff)'}")
    print(f"AOIs gesamt: {status['aoi_count']}  |  Regionen: {', '.join(status['regions'])}")
    print()

    print(f"Suche Szenenpaare für {region}...")
    zones = detect_ground_changes(region, days_back=45)

    if not zones:
        print("Keine Ergebnisse (zu viele Wolken oder keine Szenen verfügbar)")
    else:
        for z in zones:
            score_bar = "█" * int(z["change_score"] * 10) + "░" * (10 - int(z["change_score"] * 10))
            print(f"\n📍 {z['name']}  ({z['aoi_type']})")
            print(f"   Koordinaten:  {z['lat']:.4f}, {z['lon']:.4f}")
            print(f"   Change-Score: [{score_bar}] {z['change_score']:.3f}")
            print(f"   Typ:          {z['change_type']}")
            print(f"   Konfidenz:    {z['confidence']:.0%}")
            print(f"   Szenen:       {z['date_new']} vs {z['date_old']}  ({z['days_gap']} Tage)")
            print(f"   Wolken:       {z['cloud_new']}% / {z['cloud_old']}%")
            print(f"   Pixel-Diff:   {z['pixel_diff_pct']:.1f}%")
            print(f"   Browser:      {z['browser_url']}")
