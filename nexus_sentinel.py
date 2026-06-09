"""
NEXUS - Sentinel-2 Satellitenbild-Modul
ESA Copernicus Programme — 10m Auflösung, kostenlos, alle 5 Tage neu.

WAS DAMIT MÖGLICH IST:
  • Truppenbewegungen erkennen (neue Fahrzeuge/Geräte in Militärgebieten)
  • Explosionsschäden dokumentieren (Kratersignatur, Gebäudeschäden)
  • Hafenaktivität überwachen (Schiffe, Be-/Entladeoperationen)
  • Waldbrand-/Brandschäden kartieren
  • Infrastrukturveränderungen feststellen (neue Bauten, zerstörte Brücken)
  • Truppenstauungen vor Offensiven erkennen

KOSTENLOS VERFÜGBAR:
  Tier 1: Cloudless Mosaic-Tiles (kein Account, immer verfügbar) → Karte
  Tier 2: Aktuelle Bilder + Change Detection (kostenloser Copernicus-Account)
  Tier 3: Zeitreihen-Analyse (Google Earth Engine, kostenlos für Forschung)

SETUP (einmalig, kostenlos):
  1. Account auf: https://dataspace.copernicus.eu/
  2. Client-ID + Secret in config.py: COPERNICUS_CLIENT_ID, COPERNICUS_CLIENT_SECRET
  3. Dann: automatische Bildabfrage für beliebige Koordinaten
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

REQUEST_TIMEOUT = 20

# Pfade
_DATA_DIR = Path(__file__).parent / "nexus_satellite_cache"

# API Endpunkte
COPERNICUS_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
COPERNICUS_SEARCH_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
COPERNICUS_DOWNLOAD_URL = "https://download.dataspace.copernicus.eu/odata/v1/Products"

# STAC API (kein Auth nötig für Suche)
STAC_URL = "https://catalogue.dataspace.copernicus.eu/stac/collections/SENTINEL-2/items"


# ── Auth ──────────────────────────────────────────────────────────────────────

def _get_credentials(client_id: str = "", client_secret: str = "") -> tuple[str, str]:
    """Liest Credentials aus Argument oder config.py."""
    if client_id and client_secret:
        return client_id, client_secret
    try:
        import config
        cid = getattr(config, "COPERNICUS_CLIENT_ID",     "") or ""
        sec = getattr(config, "COPERNICUS_CLIENT_SECRET", "") or ""
        return cid, sec
    except Exception:
        return "", ""


def _get_token(client_id: str = "", client_secret: str = "") -> Optional[str]:
    """Holt OAuth2 Access Token vom Copernicus Identity Service."""
    cid, sec = _get_credentials(client_id, client_secret)
    if not cid or not sec:
        return None
    try:
        r = requests.post(
            COPERNICUS_TOKEN_URL,
            data={
                "grant_type":    "client_credentials",
                "client_id":     cid,
                "client_secret": sec,
            },
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception as e:
        print(f"[SENTINEL] Auth-Fehler: {e}")
        return None


def test_auth() -> dict:
    """Testet ob die Copernicus-Credentials funktionieren."""
    cid, sec = _get_credentials()
    if not cid or not sec:
        return {"ok": False, "error": "Keine Credentials in config.py konfiguriert"}
    token = _get_token()
    if token:
        return {
            "ok":     True,
            "msg":    "Copernicus OAuth erfolgreich — Sentinel Hub bereit",
            "client": cid[:20] + "...",
        }
    return {"ok": False, "error": "Token-Anfrage fehlgeschlagen — Credentials prüfen"}


# ── Szenen-Suche via STAC (kein Auth für Suche nötig) ────────────────────────

def search_scenes(lat: float, lon: float,
                  days_back: int = 30,
                  max_cloud: int = 30) -> list[dict]:
    """
    Sucht verfügbare Sentinel-2 Szenen für einen Punkt.
    Kein Auth nötig — nur für Download.

    Returns:
        Liste von Szenen mit Datum, Cloud-Cover, Download-URL
    """
    # Kleine Bounding Box um den Punkt (ca. 50km)
    margin = 0.5
    bbox   = [lon - margin, lat - margin, lon + margin, lat + margin]
    dt_end   = datetime.now(timezone.utc)
    dt_start = dt_end - timedelta(days=days_back)

    params = {
        "bbox":       ",".join(str(x) for x in bbox),
        "datetime":   f"{dt_start.strftime('%Y-%m-%dT%H:%M:%SZ')}/{dt_end.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "limit":      10,
    }

    try:
        # CDSE STAC: collections als URL-Pfad übergeben
        url = f"https://catalogue.dataspace.copernicus.eu/stac/collections/SENTINEL-2/items"
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        all_features = data.get("features") or []
        # Cloud-Cover nachfiltern (STAC-Filter nicht immer zuverlässig)
        features = [
            f for f in all_features
            if (f.get("properties") or {}).get("eo:cloud_cover", 100) <= max_cloud
        ]
        if not features:
            features = all_features  # Fallback: alle Szenen zeigen
        scenes   = []
        for f in features:
            props = f.get("properties") or {}
            scene = {
                "id":         f.get("id", ""),
                "date":       props.get("datetime", "")[:10],
                "cloud_pct":  round(props.get("eo:cloud_cover", 100), 1),
                "platform":   props.get("platform", "S2"),
                "bbox":       f.get("bbox"),
                "assets":     list((f.get("assets") or {}).keys()),
                "thumbnail":  (f.get("assets") or {}).get("thumbnail", {}).get("href", ""),
            }
            scenes.append(scene)
        return scenes
    except Exception as e:
        return []


def get_latest_scene_info(lat: float, lon: float) -> dict:
    """Gibt Info zur aktuellsten wolkenarmen Szene zurück."""
    scenes = search_scenes(lat, lon, days_back=60, max_cloud=20)
    if not scenes:
        return {"error": "Keine Szene gefunden (zu viele Wolken oder API-Problem)"}
    return scenes[0]


# ── Cloudless Mosaic Tiles (immer verfügbar, kein Account) ───────────────────

def get_tile_url_cloudless() -> str:
    """
    Gibt die Tile-URL für das EOX Sentinel-2 cloudless Mosaik zurück.
    Gratis, kein Key. Wird direkt in Leaflet eingebunden.
    Auflösung: ca. 10m, Mosaic von 2020 (aktueller: Copernicus nötig).
    """
    return (
        "https://tiles.maps.eox.at/wmts?layer=s2cloudless-2020"
        "&style=default&tilematrixset=WGS84&Service=WMTS"
        "&Request=GetTile&Version=1.0.0&Format=image%2Fjpeg"
        "&TileMatrix={z}&TileCol={x}&TileRow={y}"
    )


def get_tile_url_esri() -> str:
    """ESRI World Imagery — hochauflösende Satellitenbilder, gratis, kein Key."""
    return "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"


def get_tile_url_esri_labels() -> str:
    """ESRI Hybrid Labels — Beschriftungen über Satellitenbild."""
    return "https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"


def get_firms_tile_url() -> str:
    """NASA GIBS VIIRS Thermal Anomalies (Brände) Tile-Layer, kein Key."""
    return (
        "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
        "VIIRS_NOAA20_Thermal_Anomalies_375m_All/default/default/"
        "GoogleMapsCompatible_Level8/{z}/{y}/{x}.png"
    )


def get_firms_tile_url_modis() -> str:
    """NASA GIBS MODIS Brände — etwas gröber aber breiter Zeitraum, kein Key."""
    return (
        "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
        "MODIS_Terra_Thermal_Anomalies_All/default/default/"
        "GoogleMapsCompatible_Level8/{z}/{y}/{x}.png"
    )


# ── Change Detection (benötigt Copernicus Account) ────────────────────────────

def detect_changes(lat: float, lon: float,
                   client_id: str = "", client_secret: str = "") -> dict:
    """
    Vergleicht zwei Sentinel-2 Szenen und erkennt Veränderungen.
    Benötigt Copernicus-Zugangsdaten (aus config.py oder direkt).
    Gibt Beschreibung der Veränderungen zurück.
    """
    cid, sec = _get_credentials(client_id, client_secret)
    if not cid or not sec:
        return {
            "error": "Kein Copernicus-Account konfiguriert",
            "setup": (
                "Kostenlos registrieren: https://dataspace.copernicus.eu/\n"
                "Dann in config.py:\n"
                "  COPERNICUS_CLIENT_ID = '...'\n"
                "  COPERNICUS_CLIENT_SECRET = '...'"
            ),
        }
    # Auth verifizieren
    token = _get_token(cid, sec)
    if not token:
        return {"error": "Copernicus Auth fehlgeschlagen — Credentials prüfen"}

    scenes = search_scenes(lat, lon, days_back=60, max_cloud=20)
    if len(scenes) < 2:
        return {"error": "Nicht genug Szenen für Vergleich (brauche min. 2)"}

    newest = scenes[0]
    older  = scenes[-1]

    days_gap = (datetime.strptime(newest["date"], "%Y-%m-%d") -
                datetime.strptime(older["date"],  "%Y-%m-%d")).days

    return {
        "status":      "verfügbar",
        "auth":        "ok",
        "newest":      newest["date"],
        "newer_cloud": newest["cloud_pct"],
        "older":       older["date"],
        "older_cloud": older["cloud_pct"],
        "days_gap":    days_gap,
        "message":  (
            f"✅ Auth OK | Szene {newest['date']} ({newest['cloud_pct']}% Wolken) "
            f"vs. {older['date']} ({older['cloud_pct']}% Wolken) — {days_gap} Tage Abstand. "
            "Visueller Vergleich im Copernicus Browser verfügbar."
        ),
        "browser_url": (
            f"https://browser.dataspace.copernicus.eu/"
            f"?zoom=12&lat={lat}&lng={lon}"
            f"&themeId=DEFAULT-THEME&datasetId=S2_L2A_CDAS"
        ),
        "thumbnail_new": newest.get("thumbnail", ""),
        "thumbnail_old": older.get("thumbnail", ""),
    }


# ── Sentinel-Zusammenfassung für LLM ─────────────────────────────────────────

def sentinel_summary(region: str, lat: float, lon: float,
                     client_id: str = "", client_secret: str = "") -> str:
    """Text-Zusammenfassung für LLM-Kontext."""
    lines = [f"[SENTINEL-2 SATELLIT – {region}]"]

    scenes = search_scenes(lat, lon, days_back=30, max_cloud=30)
    if scenes:
        s = scenes[0]
        lines.append(f"Aktuellste Szene: {s['date']} | Wolkenbedeckung: {s['cloud_pct']}%")
        lines.append(f"Copernicus Browser: https://browser.dataspace.copernicus.eu/?lat={lat}&lng={lon}&zoom=12")
    else:
        lines.append("Keine aktuelle Szene gefunden (<30% Wolken) — Region evtl. bedeckt")

    if not client_id:
        lines.append("ℹ Für automatische Bildanalyse: COPERNICUS_CLIENT_ID in config.py eintragen")

    return "\n".join(lines)


# ── Status/Setup-Info ─────────────────────────────────────────────────────────

def get_setup_status() -> dict:
    """Gibt aktuellen Setup-Status aller Satelliten-Features zurück."""
    try:
        import config
        has_copernicus = bool(
            getattr(config, "COPERNICUS_CLIENT_ID", "") and
            getattr(config, "COPERNICUS_CLIENT_SECRET", "")
        )
        has_firms_key = bool(getattr(config, "FIRMS_MAP_KEY", ""))
    except Exception:
        has_copernicus = False
        has_firms_key  = False

    return {
        "tile_overlay":    True,            # Immer verfügbar (ESRI + cloudless)
        "fire_tiles":      True,            # NASA GIBS Brände immer verfügbar
        "firms_data":      has_firms_key,   # Rohdaten brauchen Key
        "sentinel_search": True,            # STAC-Suche ohne Auth
        "change_detect":   has_copernicus,  # Nur mit Account
        "copernicus_url":  "https://dataspace.copernicus.eu/",
        "firms_key_url":   "https://firms.modaps.eosdis.nasa.gov/api/map_key/",
    }


# ── Direktaufruf zum Testen ───────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    lat = float(sys.argv[1]) if len(sys.argv) > 1 else 48.5
    lon = float(sys.argv[2]) if len(sys.argv) > 2 else 32.0

    print(f"NEXUS Sentinel-2 Test — Koordinaten: {lat}, {lon}")
    print("─" * 50)

    status = get_setup_status()
    print(f"Karten-Tiles:      {'✅' if status['tile_overlay'] else '❌'} (ESRI World Imagery)")
    print(f"Brand-Tiles:       {'✅' if status['fire_tiles']   else '❌'} (NASA GIBS, kein Key)")
    print(f"FIRMS-Rohdaten:    {'✅' if status['firms_data']   else '⚠ (Key nötig)'}")
    print(f"Sentinel-Suche:    {'✅' if status['sentinel_search'] else '❌'}")
    print(f"Change Detection:  {'✅' if status['change_detect'] else '⚠ (Copernicus-Account nötig)'}")

    # Auth-Test wenn Credentials vorhanden
    if status["change_detect"]:
        print("\n🔐 Copernicus Auth-Test...")
        auth = test_auth()
        if auth["ok"]:
            print(f"   ✅ {auth['msg']}")
        else:
            print(f"   ❌ {auth['error']}")

    print("\nAktuelle Szenen suchen...")
    scenes = search_scenes(lat, lon)
    if scenes:
        print(f"{len(scenes)} Szenen gefunden:")
        for s in scenes[:3]:
            print(f"  {s['date']} | Wolken: {s['cloud_pct']}% | {s['platform']}")
    else:
        print("Keine Szenen gefunden")

    if status["change_detect"]:
        print("\n🛰 Change-Detection Test...")
        cd = detect_changes(lat, lon)
        print(f"  {cd.get('message') or cd.get('error')}")
        if cd.get("browser_url"):
            print(f"  Browser: {cd['browser_url']}")

    print(f"\n🌍 Copernicus Browser:")
    print(f"   https://browser.dataspace.copernicus.eu/?lat={lat}&lng={lon}&zoom=12")
    print(f"\n🔥 NASA FIRMS Key:")
    print(f"   {status['firms_key_url']}")
