"""
NEXUS - Wetter-Modul mit operativer Bewertung
Open-Meteo API (komplett kostenlos, kein API-Key nötig).
Liefert nicht nur Wetterdaten, sondern bewertet die operativen Konsequenzen:
Sandsturm, Nebel, Sturm = keine Flugoperationen, maritime Einschränkungen usw.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import requests

# ======================================================
# Region-Koordinaten (Zentrum der Krisenregionen)
# ======================================================
REGION_COORDS: dict[str, tuple[float, float, str]] = {
    # (Breitengrad, Längengrad, Landesname)
    "Naher Osten":       (29.5,   45.0,  "Kuwait/Irak-Grenze"),
    "Iran":              (32.4,   53.7,  "Teheran, Iran"),
    "Persischer Golf":   (26.0,   54.0,  "Persischer Golf"),
    "Hormuz-Strasse":    (26.5,   56.5,  "Strait of Hormuz"),
    "Rotes Meer":        (20.0,   39.0,  "Rotes Meer (Mitte)"),
    "Ukraine":           (49.0,   32.0,  "Zentral-Ukraine"),
    "Gaza":              (31.4,   34.3,  "Gazastreifen"),
    "Israel":            (31.8,   35.2,  "Jerusalem"),
    "Taiwan-Strasse":    (24.5,  119.5,  "Taiwan-Strasse"),
    "Korea-Halbinsel":   (37.5,  127.0,  "Seoul, Südkorea"),
    "Sahel":             (15.0,    5.0,  "Niger/Mali-Grenzgebiet"),
    "Schwarzes Meer":    (43.0,   34.0,  "Schwarzes Meer (Mitte)"),
    "Suez-Kanal":        (30.5,   32.3,  "Suez-Kanal"),
    "Bosporus":          (41.1,   29.0,  "Istanbul/Bosporus"),
    "Syrien":            (34.8,   38.9,  "Zentral-Syrien"),
    "Jemen":             (15.5,   44.2,  "Sanaa, Jemen"),
    "Libyen":            (27.0,   18.0,  "Zentral-Libyen"),
}

OPEN_METEO_URL   = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL    = "https://geocoding-api.open-meteo.com/v1/search"
REQUEST_TIMEOUT  = 10


def _geocode_location(name: str) -> Optional[tuple[float, float, str]]:
    """
    Löst beliebigen Ortsnamen/Land zu (lat, lon, display_name) auf.
    Nutzt Open-Meteo Geocoding API (kostenlos, kein Key).
    Gibt None zurück wenn nicht gefunden.
    """
    try:
        r = requests.get(GEOCODING_URL, params={
            "name": name, "count": 1, "language": "de", "format": "json"
        }, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        results = r.json().get("results", [])
        if results:
            res = results[0]
            display = res.get("name", name)
            country = res.get("country", "")
            if country and country != display:
                display = f"{display}, {country}"
            return (res["latitude"], res["longitude"], display)
    except Exception:
        pass
    return None


# ======================================================
# WMO Wettercodes → Klartext + operative Relevanz
# ======================================================
WMO_CODES = {
    0:  ("Klarer Himmel",           "optimal"),
    1:  ("Meist klar",              "optimal"),
    2:  ("Teils bewölkt",           "gut"),
    3:  ("Bedeckt",                 "eingeschraenkt"),
    45: ("Nebel",                   "kritisch"),
    48: ("Gefrierender Nebel",      "kritisch"),
    51: ("Leichter Nieselregen",    "eingeschraenkt"),
    53: ("Mäßiger Nieselregen",     "eingeschraenkt"),
    55: ("Starker Nieselregen",     "eingeschraenkt"),
    61: ("Leichter Regen",          "eingeschraenkt"),
    63: ("Mäßiger Regen",           "eingeschraenkt"),
    65: ("Starker Regen",           "kritisch"),
    71: ("Leichter Schneefall",     "eingeschraenkt"),
    73: ("Mäßiger Schneefall",      "eingeschraenkt"),
    75: ("Starker Schneefall",      "kritisch"),
    77: ("Schneekörner",            "eingeschraenkt"),
    80: ("Leichte Regenschauer",    "eingeschraenkt"),
    81: ("Mäßige Regenschauer",     "eingeschraenkt"),
    82: ("Heftige Regenschauer",    "kritisch"),
    85: ("Schneeschauer",           "eingeschraenkt"),
    86: ("Starke Schneeschauer",    "kritisch"),
    95: ("Gewitter",                "kritisch"),
    96: ("Gewitter mit Hagel",      "kritisch"),
    99: ("Gewitter mit schwerem Hagel", "kritisch"),
}


def _wmo_label(code: int) -> tuple[str, str]:
    """Gibt (Beschreibung, Kategorie) zurück."""
    return WMO_CODES.get(code, (f"Code {code}", "unbekannt"))


# ======================================================
# Operative Bewertung
# ======================================================

def _operational_assessment(
    weather_desc: str,
    weather_cat: str,
    wind_kmh: float,
    visibility_km: Optional[float],
    precipitation_mm: float,
    dust_aod: Optional[float] = None,
) -> dict:
    """
    Bewertet die operativen Einschränkungen basierend auf Wetterdaten.
    Gibt ein Dict mit Ampel-Status und Erklärungen zurück.
    """
    issues = []
    air_ops    = "✅ Möglich"
    naval_ops  = "✅ Möglich"
    ground_ops = "✅ Möglich"
    overall    = "gruen"

    # ── Sichtweite ──────────────────────────────────────────────────────────
    if visibility_km is not None:
        if visibility_km < 0.2:
            issues.append("⛔ Sichtweite <200m – Luftoperationen unmöglich")
            air_ops   = "⛔ Unmöglich (Sichtweite <200m)"
            overall   = "rot"
        elif visibility_km < 1.0:
            issues.append("⚠ Sichtweite <1km – stark eingeschränkte Luftoperationen")
            air_ops   = "⚠ Stark eingeschränkt (Sichtweite {:.1f}km)".format(visibility_km)
            overall   = "gelb" if overall != "rot" else "rot"
        elif visibility_km < 5.0:
            issues.append("⚠ Sichtweite <5km – eingeschränkte Präzisionsoperationen")
            overall   = "gelb" if overall != "rot" else "rot"

    # ── Wind ────────────────────────────────────────────────────────────────
    if wind_kmh > 80:
        issues.append("⛔ Sturm ({}km/h) – Drohnen/Helikopter geerdet, Seegang extrem".format(int(wind_kmh)))
        air_ops   = "⛔ Unmöglich (Sturm {}km/h)".format(int(wind_kmh))
        naval_ops = "⛔ Extrem gefährlich ({}km/h)".format(int(wind_kmh))
        overall   = "rot"
    elif wind_kmh > 50:
        issues.append("⚠ Starker Wind ({}km/h) – Drohnen eingeschränkt, erhöhter Seegang".format(int(wind_kmh)))
        air_ops   = "⚠ Eingeschränkt (Wind {}km/h)".format(int(wind_kmh))
        naval_ops = "⚠ Erschwerter Betrieb (Seegang)".format()
        overall   = "gelb" if overall != "rot" else "rot"
    elif wind_kmh > 30:
        issues.append("Wind {}km/h – leichte maritime Einschränkungen".format(int(wind_kmh)))

    # ── Niederschlag ────────────────────────────────────────────────────────
    if precipitation_mm > 20:
        issues.append("⛔ Starker Niederschlag ({}mm/h) – Radarsignatur eingeschränkt".format(precipitation_mm))
        overall = "rot" if precipitation_mm > 30 else ("gelb" if overall == "gruen" else overall)
    elif precipitation_mm > 5:
        issues.append("⚠ Mäßiger Niederschlag ({}mm/h)".format(precipitation_mm))

    # ── Wetterkategorie ─────────────────────────────────────────────────────
    if weather_cat == "kritisch":
        if "Gewitter" in weather_desc:
            issues.append("⛔ {} – Elektronik gefährdet, Operationen pausiert".format(weather_desc))
            air_ops   = "⛔ Eingestellt (Gewitter)"
            naval_ops = "⛔ Eingestellt (Gewitter)"
            overall   = "rot"
        elif "Nebel" in weather_desc:
            if "⚠" not in air_ops and "⛔" not in air_ops:
                issues.append("⚠ {} – VFR-Flüge eingestellt".format(weather_desc))
                air_ops = "⚠ VFR eingestellt ({})".format(weather_desc)
                overall = "gelb" if overall == "gruen" else overall

    # ── Sandsturm-Schätzung (Arabische Halbinsel / Sahel / Libyen) ──────────
    # Open-Meteo liefert keinen direkten Sandsturm-Index,
    # aber hoher Wind + geringe Sichtweite in Trockenregionen = Sandsturm
    if dust_aod and dust_aod > 0.5:
        issues.append("⛔ SANDSTURM-VERDACHT (Staubindex {:.2f}) – alle Außenoperationen eingestellt".format(dust_aod))
        air_ops    = "⛔ Eingestellt (Sandsturm)"
        naval_ops  = "⚠ Eingeschränkt (Sichtweite)"
        ground_ops = "⛔ Eingestellt (Sandsturm)"
        overall    = "rot"

    # Gesamtbewertung wenn keine Probleme
    if not issues:
        issues.append("Keine wetterbedingten Einschränkungen")

    return {
        "overall":    overall,
        "air_ops":    air_ops,
        "naval_ops":  naval_ops,
        "ground_ops": ground_ops,
        "issues":     issues,
    }


# ======================================================
# Hauptfunktion: Wetter für Region abrufen
# ======================================================

def get_weather(region_name: str) -> dict:
    """
    Ruft aktuelle Wetterdaten + 6h-Vorhersage für eine Region ab.
    Gibt strukturiertes Dict mit operativer Bewertung zurück.
    """
    # 1. Bekannte Krisenregionen prüfen
    coords = None
    matched_name = region_name
    for name, data in REGION_COORDS.items():
        if region_name.lower() in name.lower() or name.lower() in region_name.lower():
            coords = data
            matched_name = name
            break

    # 2. Geocoding-Fallback: beliebiger Ort/Land über Open-Meteo Geocoding API
    if not coords:
        geo = _geocode_location(region_name)
        if geo:
            lat_g, lon_g, display = geo
            coords = (lat_g, lon_g, display)
            matched_name = display
        else:
            return {"error": f"Ort '{region_name}' nicht gefunden", "region": region_name}

    lat, lon, location_desc = coords

    params = {
        "latitude":              lat,
        "longitude":             lon,
        "current":               [
            "temperature_2m",
            "relative_humidity_2m",
            "apparent_temperature",
            "weather_code",
            "wind_speed_10m",
            "wind_gusts_10m",
            "wind_direction_10m",
            "visibility",
            "precipitation",
        ],
        "hourly":                [
            "temperature_2m",
            "weather_code",
            "wind_speed_10m",
            "visibility",
            "precipitation",
        ],
        "forecast_hours":        6,
        "wind_speed_unit":       "kmh",
        "timezone":              "UTC",
    }

    try:
        r = requests.get(OPEN_METEO_URL, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except requests.Timeout:
        return {"error": "Open-Meteo API Timeout", "region": matched_name}
    except Exception as exc:
        return {"error": str(exc), "region": matched_name}

    current = data.get("current", {})
    hourly  = data.get("hourly", {})

    # Aktuelle Werte extrahieren
    temp        = current.get("temperature_2m")
    humidity    = current.get("relative_humidity_2m")
    wind_kmh    = current.get("wind_speed_10m", 0) or 0
    wind_gusts  = current.get("wind_gusts_10m", 0) or 0
    wind_dir    = current.get("wind_direction_10m")
    wmo_code    = current.get("weather_code", 0)
    visibility_m = current.get("visibility")
    precip      = current.get("precipitation", 0) or 0

    visibility_km = (visibility_m / 1000.0) if visibility_m is not None else None

    weather_desc, weather_cat = _wmo_label(wmo_code)

    # Sandsturm-Heuristik: Wind > 40km/h + Sichtweite < 2km + Trockenregion
    dust_heuristic = None
    dry_regions = {"Iran", "Jemen", "Libyen", "Sahel", "Syrien",
                   "Gaza", "Israel", "Naher Osten", "Persischer Golf",
                   "Hormuz-Strasse", "Suez-Kanal", "Jemen"}
    if matched_name in dry_regions:
        if wind_kmh > 40 and (visibility_km is not None and visibility_km < 2.0):
            dust_heuristic = (wind_kmh / 100.0)  # vereinfachter Index

    # Operative Bewertung
    ops = _operational_assessment(
        weather_desc, weather_cat,
        wind_kmh, visibility_km, precip, dust_heuristic
    )

    # 6h-Vorhersage zusammenfassen
    forecast_items = []
    h_times  = hourly.get("time", [])
    h_codes  = hourly.get("weather_code", [])
    h_winds  = hourly.get("wind_speed_10m", [])
    h_vis    = hourly.get("visibility", [])
    for i in range(min(6, len(h_times))):
        try:
            t = datetime.fromisoformat(h_times[i]).strftime("%H:%M")
            wc = h_codes[i] if i < len(h_codes) else 0
            wd, _ = _wmo_label(wc)
            wnd = h_winds[i] if i < len(h_winds) else 0
            vis = (h_vis[i] / 1000) if i < len(h_vis) and h_vis[i] else None
            forecast_items.append({
                "time": t,
                "weather": wd,
                "wind_kmh": round(wnd) if wnd else 0,
                "visibility_km": round(vis, 1) if vis else None,
            })
        except Exception:
            continue

    ts = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    return {
        "region":          matched_name,
        "location":        location_desc,
        "timestamp":       ts,
        "temperature_c":   temp,
        "humidity_pct":    humidity,
        "wind_kmh":        round(wind_kmh),
        "wind_gusts_kmh":  round(wind_gusts),
        "wind_direction":  _wind_dir_label(wind_dir),
        "weather_code":    wmo_code,
        "weather_desc":    weather_desc,
        "weather_cat":     weather_cat,
        "visibility_km":   round(visibility_km, 1) if visibility_km else None,
        "precipitation_mm": precip,
        "dust_warning":    dust_heuristic is not None and dust_heuristic > 0.3,
        "ops":             ops,
        "forecast":        forecast_items,
        "lat":             lat,
        "lon":             lon,
    }


def _wind_dir_label(degrees: Optional[float]) -> str:
    if degrees is None:
        return "?"
    dirs = ["N","NO","O","SO","S","SW","W","NW"]
    idx = round(degrees / 45) % 8
    return dirs[idx]


# ======================================================
# Formatierung
# ======================================================

def weather_for_report(region_name: str) -> dict:
    """
    Gibt das vollständige Wetter-Dict für den HTML-Report zurück.
    Wird von main.py als weather_data für generate_report() genutzt.
    """
    return get_weather(region_name)


def weather_for_llm(region_name: str) -> str:
    """Gibt formatierten Wetterkontext als String für den LLM zurück."""
    w = get_weather(region_name)
    if "error" in w:
        return f"[WETTER] Fehler für {region_name}: {w['error']}"

    ops = w.get("ops", {})
    vis_str = f"{w['visibility_km']}km" if w.get("visibility_km") else "keine Daten"
    lines = [
        f"[WETTER – {w['region']} | {w['timestamp']}]",
        f"Standort:     {w['location']}",
        f"Temperatur:   {w['temperature_c']}°C",
        f"Wetter:       {w['weather_desc']}",
        f"Wind:         {w['wind_kmh']}km/h {w['wind_direction']} (Böen: {w['wind_gusts_kmh']}km/h)",
        f"Sichtweite:   {vis_str}",
        f"Niederschlag: {w['precipitation_mm']}mm/h",
    ]
    if w.get("dust_warning"):
        lines.append("⛔ SANDSTURM-WARNUNG")
    lines += [
        "",
        "OPERATIVE BEWERTUNG:",
        f"  Luft:   {ops.get('air_ops','?')}",
        f"  See:    {ops.get('naval_ops','?')}",
        f"  Boden:  {ops.get('ground_ops','?')}",
        "⚠ Wetterpausen ≠ Waffenstillstand",
    ]
    for fc in (w.get("forecast") or [])[:6]:
        lines.append(
            f"  {fc.get('time','?')} | {fc.get('weather','?')} | {fc.get('wind_kmh','?')}km/h"
        )
    return "\n".join(lines)


# ── Direktaufruf zum Testen ──────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    region = sys.argv[1] if len(sys.argv) > 1 else "Tehran"
    import json as _json
    print(_json.dumps(weather_for_report(region), ensure_ascii=False, indent=2))
