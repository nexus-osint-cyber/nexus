"""
NEXUS - Blitzortung / Artillerie-Flash Detektor
Nutzt oeffentliche Blitzdaten von Blitzortung.org (Community-Netzwerk, kostenlos).
Artillerie-Signal-Logik: Blitz-Cluster ohne meteorologisches Gewitter in Konfliktzone
= moeglicherweise Mündungsfeuer / Detonation.

Quellen:
  Blitzortung:   https://www.blitzortung.org/en/live_lightning_maps.php
  API-Endpunkt:  https://data.blitzortung.org/Data/Protected/   (kein freier JSON-Feed)
  Fallback:      OpenWeatherMap kostenloser Tier fuer Gewittererkennung

Ansatz ohne API-Key:
  1. OpenWeatherMap (kostenlos, 60 req/min): aktuelle Wetterlage pruefen
     Falls Thunderstorm-Code (2xx) -> natuerliches Gewitter, kein Artillerie-Signal
  2. Falls kein Gewitter aber Konfliktzone + aktuell aktive Meldungen: Hinweis ausgeben
  3. Blitzortung.org cached JSON (inoffiziell, kann sich aendern):
     https://map.blitzortung.org/   -> WebSocket-basiert, fuer NEXUS nicht direkt nutzbar

Realistisches Fallback-Modell fuer NEXUS:
  - Wetterdaten (bereits in nexus_weather.py) auf Thunderstorm-Code pruefen
  - Wenn KEIN Gewitter aber Konfliktzone + Tag/Nacht-Muster stimmt -> Flag
  - OpenLightning API (kostenpflichtig) als optionaler Key (OPENLIGHTNING_KEY in config.py)

WICHTIG: Ohne echten Blitz-Feed ist das ein probabilistisches Modell, kein Echtzeit-Tracker.
         Trotzdem OSINT-relevant als Kontext-Indikator.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

REQUEST_TIMEOUT = 10

# ── Konfliktzone-Bboxes (identisch mit nexus_seismic fuer Konsistenz) ────────
_CONFLICT_ZONES: list[tuple] = [
    (44.0,  22.0,  55.0,  42.0,  "Ukraine"),
    (29.0,  34.0,  35.0,  38.0,  "Gaza/Westjordanland"),
    (32.0,  35.5,  33.5,  36.5,  "Israel/Libanon"),
    (33.0,  35.5,  38.0,  42.5,  "Syrien"),
    (12.0,  42.0,  23.0,  55.0,  "Jemen"),
    (24.0,  44.0,  38.0,  63.0,  "Iran"),
    (28.0,  60.0,  38.0,  75.0,  "Afghanistan/Pakistan"),
    ( 3.0,   5.0,  15.0,  23.0,  "Sahel/Sudan"),
    (20.0,  46.0,  30.0,  60.0,  "Persischer Golf"),
    (38.0,  44.0,  42.0,  50.0,  "Suedkaukasus"),
    (33.0, 122.0,  43.0, 132.0,  "Koreanische Halbinsel"),
]

# OpenWeatherMap Gewittercodes (2xx = Thunderstorm)
_OWM_THUNDER_CODES = set(range(200, 233))

# Optional: OpenLightning.io API
_OPENLIGHTNING_BASE = "https://api.openlightning.io/v1"


def _get_owm_key() -> str:
    try:
        import config
        return getattr(config, "OPENWEATHER_KEY", "")
    except Exception:
        return ""


def _get_openlightning_key() -> str:
    try:
        import config
        return getattr(config, "OPENLIGHTNING_KEY", "")
    except Exception:
        return ""


def _geocode(region: str) -> Optional[tuple[float, float]]:
    try:
        r = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": region, "count": 1, "language": "de", "format": "json"},
            timeout=8,
        )
        res = (r.json().get("results") or [None])[0]
        if res:
            return res["latitude"], res["longitude"]
    except Exception:
        pass
    return None


def _in_conflict_zone(lat: float, lon: float) -> Optional[str]:
    for lat_min, lon_min, lat_max, lon_max, name in _CONFLICT_ZONES:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return name
    return None


def _check_thunderstorm_owm(lat: float, lon: float, api_key: str) -> dict:
    """
    Prueft via OpenWeatherMap ob aktuell ein Gewitter vorhanden ist.
    Gibt dict {has_thunder, condition, temp_c, clouds_pct} zurueck.
    """
    try:
        r = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"lat": lat, "lon": lon, "appid": api_key, "units": "metric"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        d = r.json()
        weather_id  = (d.get("weather") or [{}])[0].get("id", 0)
        description = (d.get("weather") or [{}])[0].get("description", "")
        temp_c      = (d.get("main") or {}).get("temp")
        clouds_pct  = (d.get("clouds") or {}).get("all", 0)
        return {
            "has_thunder": weather_id in _OWM_THUNDER_CODES,
            "condition":   description,
            "temp_c":      temp_c,
            "clouds_pct":  clouds_pct,
            "weather_id":  weather_id,
        }
    except Exception:
        return {"has_thunder": False, "condition": "unbekannt",
                "temp_c": None, "clouds_pct": 0, "weather_id": 0}


def _fetch_openlightning(lat: float, lon: float, radius_km: float,
                          api_key: str, minutes: int = 30) -> list[dict]:
    """
    Holt echte Blitzdaten von OpenLightning.io (API-Key erforderlich).
    Gibt Liste von {lat, lon, time_utc, type} zurueck.
    """
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    try:
        r = requests.get(
            f"{_OPENLIGHTNING_BASE}/strikes",
            params={
                "lat":    lat,
                "lon":    lon,
                "radius": radius_km * 1000,  # Meter
                "since":  since,
            },
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        strikes = []
        for s in (data.get("strikes") or [])[:100]:
            strikes.append({
                "lat":      s.get("lat"),
                "lon":      s.get("lon"),
                "time_utc": s.get("time"),
                "type":     s.get("type", "CG"),  # CG = Cloud-to-Ground
            })
        return strikes
    except Exception:
        return []


def analyze_lightning(region: str, radius_km: float = 150.0) -> dict:
    """
    Hauptfunktion: Analysiert Blitzaktivitaet in einer Region und gibt OSINT-Bewertung.

    Returns dict:
      region, lat, lon, conflict_zone, has_thunderstorm, lightning_count,
      artillery_signal (bool), confidence ("high"/"medium"/"low"/"none"),
      signal_hint (str), source (str)
    """
    geo = _geocode(region)
    if not geo:
        return {"region": region, "error": "Geocoding fehlgeschlagen", "artillery_signal": False}

    lat, lon = geo
    conflict = _in_conflict_zone(lat, lon)
    owm_key  = _get_owm_key()
    ol_key   = _get_openlightning_key()

    # 1. Wetterlage pruefen
    weather = {"has_thunder": False, "condition": "unbekannt", "clouds_pct": 0}
    if owm_key:
        weather = _check_thunderstorm_owm(lat, lon, owm_key)

    # 2. Blitzdaten holen (falls OpenLightning Key vorhanden)
    strikes: list[dict] = []
    source = "Wetter-Modell (kein Blitz-API-Key)"
    if ol_key:
        strikes = _fetch_openlightning(lat, lon, radius_km, ol_key, minutes=60)
        source  = f"OpenLightning.io ({len(strikes)} Blitze/60min)"

    lightning_count = len(strikes)

    # 3. Artillerie-Signal-Logik
    artillery_signal = False
    confidence       = "none"
    signal_hint      = ""

    if conflict:
        if ol_key and lightning_count > 0 and not weather["has_thunder"]:
            # Echte Blitze ohne Gewitter = starkes Signal
            if lightning_count >= 10:
                artillery_signal = True
                confidence       = "high"
                signal_hint      = (
                    f"{lightning_count} elektromagnetische Signale in {conflict} "
                    f"ohne Gewitteraktivitaet – moeglicherweise Mündungsfeuer/Detonationen. "
                    f"Wetter: {weather['condition']} ({weather['clouds_pct']}% Bewoelkung)."
                )
            elif lightning_count >= 3:
                artillery_signal = True
                confidence       = "medium"
                signal_hint      = (
                    f"{lightning_count} Signale in {conflict} bei klarem Wetter – "
                    f"Artillerie nicht ausgeschlossen."
                )
        elif not ol_key and conflict and not weather.get("has_thunder", False):
            # Kein echter Blitz-Feed: kontext-basiertes Signal aus Wetterlage
            # Wenn trockenes/klares Wetter in Konfliktzone und aktive Berichte vorhanden:
            clouds = weather.get("clouds_pct", 50)
            if clouds < 30:
                confidence    = "low"
                signal_hint   = (
                    f"Klares Wetter ({clouds}% Bewoelkung) in {conflict} – "
                    f"kein Gewitter. Falls akustische/seismische Ereignisse bekannt: "
                    f"Artillerie-Hintergrund moeglich. "
                    f"(Echtzeit-Blitzdaten benoetigen OPENLIGHTNING_KEY in config.py)"
                )
        elif weather.get("has_thunder"):
            signal_hint = (
                f"Aktives Gewitter in {conflict} ({weather['condition']}) – "
                f"natuerliche Blitzaktivitaet ueberlagert Signal. "
                f"Artillerie-Erkennung zuzeit nicht moeglich."
            )

    return {
        "region":           region,
        "lat":              lat,
        "lon":              lon,
        "conflict_zone":    conflict or "",
        "has_thunderstorm": weather.get("has_thunder", False),
        "weather_condition":weather.get("condition", ""),
        "clouds_pct":       weather.get("clouds_pct", 0),
        "lightning_count":  lightning_count,
        "artillery_signal": artillery_signal,
        "confidence":       confidence,
        "signal_hint":      signal_hint,
        "source":           source,
        "timestamp":        datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
    }


def lightning_summary(region: str) -> str:
    """Text-Zusammenfassung fuer LLM-Kontext."""
    r = analyze_lightning(region)
    if r.get("error"):
        return f"[LIGHTNING] {r['error']}"

    conf_icons = {"high": "🔴", "medium": "🟡", "low": "⚪", "none": ""}
    icon = conf_icons.get(r["confidence"], "")

    lines = [
        f"[LIGHTNING – {region}]",
        f"Konfliktzone: {r['conflict_zone'] or 'Nein'} | "
        f"Gewitter: {'Ja' if r['has_thunderstorm'] else 'Nein'} ({r['weather_condition']}) | "
        f"Bewoelkung: {r['clouds_pct']}%",
        f"Blitzdaten: {r['source']}",
    ]
    if r["signal_hint"]:
        lines.append(f"{icon} {r['signal_hint']}")
    else:
        lines.append("Kein Artillerie-Signal.")
    return "\n".join(lines)


def lightning_for_map(region: str) -> list[dict]:
    """
    Gibt Blitz-Marker fuer die Live-Karte zurueck.
    Ohne OpenLightning Key: nur Signal-Punkt am Regionszentrum.
    """
    r = analyze_lightning(region)
    if not r.get("lat") or not r.get("lon"):
        return []
    if not r.get("conflict_zone"):
        return []

    markers = []
    # Individuelle Blitz-Marker (nur mit API-Key)
    # (strikes-Liste nicht in analyze_lightning return, hier nur Signal-Marker)
    if r.get("artillery_signal"):
        markers.append({
            "lat":       r["lat"],
            "lon":       r["lon"],
            "type":      "artillery_signal",
            "confidence":r["confidence"],
            "hint":      r["signal_hint"],
            "region":    r["region"],
            "count":     r["lightning_count"],
        })
    return markers


# ── Direktaufruf zum Testen ──────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    region = sys.argv[1] if len(sys.argv) > 1 else "Ukraine"
    print(f"NEXUS Lightning – {region}")
    print(lightning_summary(region))
    print()
    markers = lightning_for_map(region)
    if markers:
        for m in markers:
            print(f"  SIGNAL [{m['confidence'].upper()}] @ {m['lat']:.3f},{m['lon']:.3f}")
            print(f"  {m['hint']}")
    else:
        print("Keine Artillerie-Marker.")
