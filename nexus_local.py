"""
nexus_local.py – Lokal-OSINT für einzelne Adressen und Stadtteile
==================================================================
Geocodiert eine Adresse/einen Ort und fragt alle verfügbaren NEXUS-Signalquellen
in einem konfigurierbaren Radius ab. Zeigt lokale Ereignisse statt Krisenregionen.

Verwendung in main.py:
    @ Hauptstraße 12, Berlin              → 25km Radius
    @ Kölner Dom, 5km                     → 5km Radius
    @ 48.1374,11.5755                     → GPS-Koordinaten, 10km Radius
    @ London Heathrow, 50km               → 50km Radius

Ausgabe:
    - Lokale Nachrichten (GDELT + RSS in der Nähe)
    - Brände / Naturereignisse (FIRMS, EONET)
    - Flugereignisse (Flughäfen, ISR, Hubschrauber)
    - Seismik + Wetter
    - ACLED-Konfliktereignisse (auch in Deutschland/Westeuropa vorhanden)
    - AIS-Schiffe wenn nahe Küste/Wasserstraße
    - Aktuelle Stimmung (Social Media Geo-Posts)
"""

import re
import math
import logging
import time
import urllib.request
import urllib.parse
import json
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("nexus.local")

# ── Standardradien ────────────────────────────────────────────────────────────
DEFAULT_RADIUS_KM   = 25    # Standard für Adressen
MAX_RADIUS_KM       = 200   # Obergrenze
MIN_RADIUS_KM       = 1


@dataclass
class LocalResult:
    """Ergebnis einer Lokal-OSINT-Abfrage."""
    query:        str
    address:      str           # Normierte Adresse (Nominatim)
    lat:          float
    lon:          float
    radius_km:    float
    bbox:         tuple          # (min_lat, max_lat, min_lon, max_lon)

    # Signal-Ergebnisse
    news:         list = field(default_factory=list)    # GDELT + RSS
    fires:        list = field(default_factory=list)    # FIRMS
    flights:      list = field(default_factory=list)    # OpenSky
    earthquakes:  list = field(default_factory=list)    # USGS
    acled:        list = field(default_factory=list)    # ACLED
    ais:          list = field(default_factory=list)    # AIS-Schiffe
    weather:      dict = field(default_factory=dict)    # Open-Meteo
    notams:       list = field(default_factory=list)    # NOTAMs
    social:       list = field(default_factory=list)    # Social Media

    # Meta
    errors:       list = field(default_factory=list)
    elapsed_s:    float = 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# ADRESS-PARSING
# ═══════════════════════════════════════════════════════════════════════════════

def parse_local_query(user_input: str) -> tuple[str, float]:
    """
    Parst einen @ Befehl in (Adress-String, Radius-km).

    Beispiele:
        "@ Berlin Mitte"             → ("Berlin Mitte", 25.0)
        "@ Kölner Dom, 5km"          → ("Kölner Dom", 5.0)
        "@ 48.137, 11.576, 10"       → ("48.137,11.576", 10.0)
        "@ London 50"                → ("London", 50.0)
    """
    # @ am Anfang entfernen
    s = re.sub(r"^@\s*", "", user_input.strip())

    # Radius am Ende suchen: "5km", "5 km", "5", nur wenn am Ende
    radius = DEFAULT_RADIUS_KM
    radius_match = re.search(r",?\s*(\d+(?:\.\d+)?)\s*km?\s*$", s, re.IGNORECASE)
    if radius_match:
        r = float(radius_match.group(1))
        radius = max(MIN_RADIUS_KM, min(MAX_RADIUS_KM, r))
        s = s[:radius_match.start()].strip().rstrip(",").strip()

    # GPS-Koordinaten erkennen: "48.137,11.576" oder "48.137 11.576"
    gps_match = re.match(
        r"^(-?\d{1,3}\.\d+)[,\s]+(-?\d{1,3}\.\d+)$", s.strip()
    )
    if gps_match:
        return (s.strip(), radius)

    return (s.strip(), radius)


def geocode(address: str) -> Optional[tuple[float, float, str]]:
    """
    Geocodiert eine Adresse via Nominatim (OpenStreetMap, kostenlos).

    Returns: (lat, lon, display_name) oder None bei Fehler.
    """
    # GPS direkt erkennen
    gps_match = re.match(
        r"^(-?\d{1,3}\.\d+)[,\s]+(-?\d{1,3}\.\d+)$", address.strip()
    )
    if gps_match:
        lat = float(gps_match.group(1))
        lon = float(gps_match.group(2))
        return (lat, lon, f"{lat:.4f}°N, {lon:.4f}°E")

    # Nominatim-Geocoding
    try:
        params = urllib.parse.urlencode({
            "q":              address,
            "format":         "json",
            "limit":          1,
            "accept-language": "de",
        })
        url = f"https://nominatim.openstreetmap.org/search?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "NEXUS-OSINT/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        if data:
            return (float(data[0]["lat"]), float(data[0]["lon"]),
                    data[0].get("display_name", address))
    except Exception as e:
        log.warning(f"Geocoding Fehler: {e}")
    return None


def _bbox(lat: float, lon: float, radius_km: float) -> tuple:
    """Berechnet Bounding-Box (min_lat, max_lat, min_lon, max_lon) für Radius."""
    delta_lat = radius_km / 111.0
    delta_lon = radius_km / (111.0 * math.cos(math.radians(lat)))
    return (
        round(lat - delta_lat, 4),
        round(lat + delta_lat, 4),
        round(lon - delta_lon, 4),
        round(lon + delta_lon, 4),
    )


def _in_bbox(item_lat: float, item_lon: float, bbox: tuple) -> bool:
    """Prüft ob Koordinate in Bounding-Box liegt."""
    min_lat, max_lat, min_lon, max_lon = bbox
    return (min_lat <= item_lat <= max_lat) and (min_lon <= item_lon <= max_lon)


def _dist_km(lat1, lon1, lat2, lon2) -> float:
    """Entfernung in km zwischen zwei Punkten."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.asin(math.sqrt(min(a, 1.0)))


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL-ABFRAGEN (BBox-gefiltert)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_local_news(lat: float, lon: float, bbox: tuple, radius_km: float) -> list:
    """GDELT + RSS-Nachrichten in der Nähe."""
    results = []
    try:
        from nexus_gdelt import get_gdelt_events  # type: ignore
        # GDELT erwartet Region-String → wir basteln eine Koordinaten-Region
        region_str = f"{lat:.3f},{lon:.3f}"
        events = get_gdelt_events(region_str) or []
        for e in events:
            e_lat = e.get("lat") or e.get("ActionGeo_Lat")
            e_lon = e.get("lon") or e.get("ActionGeo_Long")
            if e_lat and e_lon:
                try:
                    if _in_bbox(float(e_lat), float(e_lon), bbox):
                        dist = _dist_km(lat, lon, float(e_lat), float(e_lon))
                        e["dist_km"] = round(dist, 1)
                        results.append(e)
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        log.debug(f"GDELT lokal: {e}")
    return sorted(results, key=lambda x: x.get("dist_km", 999))[:20]


def _get_local_fires(lat: float, lon: float, bbox: tuple) -> list:
    """NASA FIRMS Brände in der Nähe."""
    results = []
    try:
        from nexus_fires import get_firms_fires  # type: ignore
        # FIRMS-Daten für BBox direkt abrufen
        min_lat, max_lat, min_lon, max_lon = bbox
        fires = get_firms_fires(
            min_lat=min_lat, max_lat=max_lat,
            min_lon=min_lon, max_lon=max_lon
        ) or []
        for f in fires:
            f_lat = f.get("latitude") or f.get("lat")
            f_lon = f.get("longitude") or f.get("lon")
            if f_lat and f_lon:
                dist = _dist_km(lat, lon, float(f_lat), float(f_lon))
                f["dist_km"] = round(dist, 1)
                results.append(f)
    except Exception as e:
        log.debug(f"FIRMS lokal: {e}")
    return sorted(results, key=lambda x: x.get("dist_km", 999))[:15]


def _get_local_flights(lat: float, lon: float, bbox: tuple) -> list:
    """Flugzeuge im Bereich."""
    results = []
    try:
        from nexus_flights import get_flights  # type: ignore
        min_lat, max_lat, min_lon, max_lon = bbox
        flights = get_flights(
            lamin=min_lat, lamax=max_lat,
            lomin=min_lon, lomax=max_lon
        ) or []
        for fl in (flights if isinstance(flights, list) else flights.get("aircraft", [])):
            f_lat = fl.get("lat") or fl.get("latitude")
            f_lon = fl.get("lon") or fl.get("longitude")
            if f_lat and f_lon:
                dist = _dist_km(lat, lon, float(f_lat), float(f_lon))
                fl["dist_km"] = round(dist, 1)
                results.append(fl)
    except Exception as e:
        log.debug(f"Flights lokal: {e}")
    return sorted(results, key=lambda x: x.get("dist_km", 999))[:20]


def _get_local_earthquakes(bbox: tuple) -> list:
    """USGS Erdbeben in der Nähe (letzte 30 Tage)."""
    results = []
    try:
        from nexus_seismic import get_earthquakes  # type: ignore
        min_lat, max_lat, min_lon, max_lon = bbox
        quakes = get_earthquakes(
            min_lat=min_lat, max_lat=max_lat,
            min_lon=min_lon, max_lon=max_lon,
            min_mag=1.0,
        ) or []
        results = quakes[:10]
    except Exception as e:
        log.debug(f"Seismik lokal: {e}")
    return results


def _get_local_acled(lat: float, lon: float, bbox: tuple) -> list:
    """ACLED-Konfliktereignisse in der Nähe (auch Proteste, Störungen)."""
    results = []
    try:
        from nexus_acled import get_acled_events  # type: ignore
        min_lat, max_lat, min_lon, max_lon = bbox
        events = get_acled_events(
            lat1=min_lat, lat2=max_lat,
            lon1=min_lon, lon2=max_lon,
        ) or []
        for ev in events:
            e_lat = ev.get("latitude") or ev.get("lat")
            e_lon = ev.get("longitude") or ev.get("lon")
            if e_lat and e_lon:
                dist = _dist_km(lat, lon, float(e_lat), float(e_lon))
                ev["dist_km"] = round(dist, 1)
                results.append(ev)
    except Exception as e:
        log.debug(f"ACLED lokal: {e}")
    return sorted(results, key=lambda x: x.get("dist_km", 999))[:15]


def _get_local_ais(lat: float, lon: float, bbox: tuple, radius_km: float) -> list:
    """AIS-Schiffe – nur sinnvoll wenn Küste/Wasserstraße in der Nähe."""
    # Heuristik: nur abrufen wenn min. eine Seite der BBox in Küstennähe
    # (Vereinfachung: immer versuchen, leere Liste ist kein Fehler)
    results = []
    try:
        from nexus_ais import get_ais_vessels  # type: ignore
        region_str = f"{lat:.3f},{lon:.3f}"
        vessels = get_ais_vessels(region_str) or []
        for v in vessels:
            v_lat = v.get("lat") or v.get("latitude")
            v_lon = v.get("lon") or v.get("longitude")
            if v_lat and v_lon:
                try:
                    if _in_bbox(float(v_lat), float(v_lon), bbox):
                        dist = _dist_km(lat, lon, float(v_lat), float(v_lon))
                        v["dist_km"] = round(dist, 1)
                        results.append(v)
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        log.debug(f"AIS lokal: {e}")
    return sorted(results, key=lambda x: x.get("dist_km", 999))[:15]


def _get_local_weather(lat: float, lon: float) -> dict:
    """Aktuelles Wetter via Open-Meteo (kostenlos, kein Key)."""
    try:
        params = urllib.parse.urlencode({
            "latitude":    lat,
            "longitude":   lon,
            "current":     "temperature_2m,wind_speed_10m,precipitation,weather_code,visibility",
            "wind_speed_unit": "ms",
            "timezone":    "auto",
        })
        url = f"https://api.open-meteo.com/v1/forecast?{params}"
        with urllib.request.urlopen(url, timeout=6) as resp:
            data = json.loads(resp.read())
        cur = data.get("current", {})
        code = cur.get("weather_code", 0)
        desc = _wmo_code(code)
        return {
            "temp_c":     cur.get("temperature_2m"),
            "wind_ms":    cur.get("wind_speed_10m"),
            "precip_mm":  cur.get("precipitation"),
            "visibility": cur.get("visibility"),
            "desc":       desc,
            "code":       code,
        }
    except Exception as e:
        log.debug(f"Wetter lokal: {e}")
        return {}


def _wmo_code(code: int) -> str:
    """WMO Wetterbedingungen-Code → Beschreibung."""
    codes = {
        0: "Klar", 1: "Meist klar", 2: "Teilweise bewölkt", 3: "Bewölkt",
        45: "Nebel", 48: "Eisnebel",
        51: "Leichter Nieselregen", 53: "Nieselregen", 55: "Starker Nieselregen",
        61: "Leichter Regen", 63: "Regen", 65: "Starker Regen",
        71: "Leichter Schneefall", 73: "Schneefall", 75: "Starker Schneefall",
        80: "Regenschauer", 81: "Regenschauer", 82: "Starke Schauer",
        85: "Schneeschauer", 86: "Starke Schneeschauer",
        95: "Gewitter", 96: "Gewitter mit Hagel", 99: "Schweres Gewitter",
    }
    return codes.get(code, f"Code {code}")


def _get_local_notams(lat: float, lon: float, radius_km: float) -> list:
    """NOTAMs in der Nähe."""
    results = []
    try:
        from nexus_notam import get_notams  # type: ignore
        notams = get_notams() or []
        for n in notams:
            n_lat = n.get("lat") or n.get("latitude")
            n_lon = n.get("lon") or n.get("longitude")
            if n_lat and n_lon:
                try:
                    dist = _dist_km(lat, lon, float(n_lat), float(n_lon))
                    if dist <= radius_km:
                        n["dist_km"] = round(dist, 1)
                        results.append(n)
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        log.debug(f"NOTAM lokal: {e}")
    return sorted(results, key=lambda x: x.get("dist_km", 999))[:10]


# ═══════════════════════════════════════════════════════════════════════════════
# HAUPT-FUNKTION
# ═══════════════════════════════════════════════════════════════════════════════

def local_osint(query: str, radius_km: float = DEFAULT_RADIUS_KM) -> LocalResult:
    """
    Vollständige Lokal-OSINT-Abfrage für eine Adresse/Koordinate.

    Args:
        query:     Adresse, Ort, POI oder "lat,lon"
        radius_km: Suchradius in km (Standard: 25)

    Returns:
        LocalResult mit allen gefundenen Signalen
    """
    t0 = time.time()
    log.info(f"Lokal-OSINT: '{query}' Radius={radius_km}km")

    # Geocoding
    geo = geocode(query)
    if not geo:
        # Leeres Ergebnis mit Fehler
        return LocalResult(
            query=query, address="Nicht gefunden", lat=0, lon=0,
            radius_km=radius_km, bbox=(0, 0, 0, 0),
            errors=["Adresse konnte nicht geocodiert werden."]
        )

    lat, lon, address = geo
    bbox = _bbox(lat, lon, radius_km)

    result = LocalResult(
        query=query,
        address=address,
        lat=lat,
        lon=lon,
        radius_km=radius_km,
        bbox=bbox,
    )

    # Alle Signale parallel abfragen (mit Fehlertoleranz)
    collectors = [
        ("news",        lambda: _get_local_news(lat, lon, bbox, radius_km)),
        ("fires",       lambda: _get_local_fires(lat, lon, bbox)),
        ("flights",     lambda: _get_local_flights(lat, lon, bbox)),
        ("earthquakes", lambda: _get_local_earthquakes(bbox)),
        ("acled",       lambda: _get_local_acled(lat, lon, bbox)),
        ("ais",         lambda: _get_local_ais(lat, lon, bbox, radius_km)),
        ("weather",     lambda: _get_local_weather(lat, lon)),
        ("notams",      lambda: _get_local_notams(lat, lon, radius_km)),
    ]

    for name, fn in collectors:
        try:
            setattr(result, name, fn())
        except Exception as e:
            result.errors.append(f"{name}: {e}")
            log.debug(f"Collector {name}: {e}")

    result.elapsed_s = round(time.time() - t0, 1)
    log.info(
        f"Lokal-OSINT fertig: {lat:.3f},{lon:.3f} | "
        f"Nachrichten={len(result.news)}, Brände={len(result.fires)}, "
        f"Flüge={len(result.flights)}, {result.elapsed_s}s"
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# TERMINAL-AUSGABE
# ═══════════════════════════════════════════════════════════════════════════════

def format_local_terminal(r: LocalResult) -> str:
    """Formatiert ein LocalResult für die NEXUS-Terminal-Ausgabe."""
    if not r.lat and not r.lon:
        return f"\n\033[31m✗ Lokal-OSINT: {r.errors[0] if r.errors else 'Unbekannter Fehler'}\033[0m\n"

    lines = [
        "",
        f"\033[36m╔══ 📍 LOKAL-OSINT: {r.address[:60]} ══╗\033[0m",
        f"\033[36m║  GPS: {r.lat:.4f}°N, {r.lon:.4f}°E  │  Radius: {r.radius_km:.0f} km  │  "
        f"{r.elapsed_s}s\033[0m",
        f"\033[36m╠══════════════════════════════════════════════════════╣\033[0m",
    ]

    # Wetter
    if r.weather:
        w = r.weather
        lines.append(
            f"\033[33m║  🌤 Wetter: {w.get('desc','?')} │ "
            f"{w.get('temp_c','?')}°C │ "
            f"Wind {w.get('wind_ms','?')} m/s │ "
            f"Niederschlag {w.get('precip_mm','0')} mm\033[0m"
        )

    # Nachrichten
    if r.news:
        lines.append(f"\033[37m║\033[0m")
        lines.append(f"\033[33m║  📰 NACHRICHTEN in {r.radius_km:.0f}km ({len(r.news)}):\033[0m")
        for n in r.news[:5]:
            title = (n.get("title") or n.get("SOURCEURL") or "")[:60]
            dist  = n.get("dist_km", "?")
            lines.append(f"\033[37m║    [{dist}km] {title}\033[0m")

    # Brände
    if r.fires:
        lines.append(f"\033[37m║\033[0m")
        lines.append(f"\033[31m║  🔥 BRÄNDE in {r.radius_km:.0f}km ({len(r.fires)}):\033[0m")
        for f in r.fires[:4]:
            frp  = f.get("frp", f.get("bright_ti4", "?"))
            dist = f.get("dist_km", "?")
            acq  = f.get("acq_date", f.get("acq_datetime", ""))[:10]
            lines.append(f"\033[37m║    [{dist}km] FRP={frp}  {acq}\033[0m")

    # Flüge
    if r.flights:
        lines.append(f"\033[37m║\033[0m")
        lines.append(f"\033[35m║  ✈ FLÜGE in {r.radius_km:.0f}km ({len(r.flights)}):\033[0m")
        for fl in r.flights[:5]:
            cs   = fl.get("callsign", fl.get("icao24", "?")).strip()
            alt  = fl.get("baro_altitude") or fl.get("altitude") or "?"
            dist = fl.get("dist_km", "?")
            cat  = fl.get("category", "")
            isr  = " ⚠ ISR" if fl.get("is_isr") else ""
            lines.append(f"\033[37m║    [{dist}km] {cs} Alt={alt}m {cat}{isr}\033[0m")

    # Erdbeben
    if r.earthquakes:
        lines.append(f"\033[37m║\033[0m")
        lines.append(f"\033[33m║  🌍 SEISMIK ({len(r.earthquakes)}):\033[0m")
        for eq in r.earthquakes[:3]:
            mag  = eq.get("magnitude") or eq.get("mag", "?")
            place= eq.get("place", "")[:40]
            time_= eq.get("time", "")[:16]
            lines.append(f"\033[37m║    M{mag} {place} ({time_})\033[0m")

    # ACLED
    if r.acled:
        lines.append(f"\033[37m║\033[0m")
        lines.append(f"\033[31m║  ⚔ KONFLIKT-/SICHERHEITSEREIGNISSE ({len(r.acled)}):\033[0m")
        for ev in r.acled[:4]:
            etype = ev.get("event_type", ev.get("type", ""))[:25]
            loc   = ev.get("location", ev.get("admin1", ""))[:25]
            dist  = ev.get("dist_km", "?")
            date  = ev.get("event_date", ev.get("date", ""))[:10]
            lines.append(f"\033[37m║    [{dist}km] {etype} – {loc} ({date})\033[0m")

    # NOTAMs
    if r.notams:
        lines.append(f"\033[37m║\033[0m")
        lines.append(f"\033[35m║  🚫 NOTAM LUFTSPERRUNGEN ({len(r.notams)}):\033[0m")
        for n in r.notams[:3]:
            title = (n.get("title") or n.get("text") or "")[:50]
            dist  = n.get("dist_km", "?")
            lines.append(f"\033[37m║    [{dist}km] {title}\033[0m")

    # AIS
    if r.ais:
        lines.append(f"\033[37m║\033[0m")
        lines.append(f"\033[36m║  ⚓ SCHIFFE in {r.radius_km:.0f}km ({len(r.ais)}):\033[0m")
        for v in r.ais[:4]:
            name = (v.get("name") or v.get("mmsi") or "?")[:20]
            typ  = (v.get("ship_type") or v.get("type_name") or "")[:20]
            dist = v.get("dist_km", "?")
            lines.append(f"\033[37m║    [{dist}km] {name} ({typ})\033[0m")

    # Zusammenfassung
    total = (len(r.news) + len(r.fires) + len(r.flights) +
             len(r.earthquakes) + len(r.acled))
    if total == 0:
        lines.append(f"\033[37m║\033[0m")
        lines.append(f"\033[32m║  ✓ Keine auffälligen Signale im Umkreis von {r.radius_km:.0f}km.\033[0m")

    if r.errors:
        lines.append(f"\033[37m║\033[0m")
        lines.append(f"\033[90m║  (Nicht verfügbar: {', '.join(r.errors[:3])})\033[0m")

    lines.append(f"\033[36m╚══════════════════════════════════════════════════════╝\033[0m")
    lines.append("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE TEST
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    query   = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Berlin Mitte, 25km"
    addr, r = parse_local_query("@ " + query)
    print(f"Geocodiere: '{addr}' Radius={r}km ...")
    result = local_osint(addr, r)
    print(format_local_terminal(result))
