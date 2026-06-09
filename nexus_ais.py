"""
NEXUS - AIS-Schiffspositionen
Echtzeit-Schiffsdaten via konfigurierbare API-Quellen.

KOSTENLOSE OPTIONEN (API-Key in config.py eintragen):

  1. AISStream.io  ← EMPFOHLEN – wirklich kostenlos, kein Limit
     → aisstream.io → Sign Up → API Key kopieren
     AISSTREAM_KEY = "dein-key"

  2. MarineTraffic Free Tier
     → marinetraffic.com/en/p/api → Free Plan
     MARINETRAFFIC_KEY = "dein-key"

  3. VesselFinder API
     → vesselfinder.com/developers
     VESSELFINDER_KEY = "dein-key"

Ohne Key: Karte zeigt maritime Zonen ohne Live-Schiffspositionen.
"""

from __future__ import annotations

import json
import threading
import time
import requests
from datetime import datetime, timezone
from typing import Optional

REQUEST_TIMEOUT = 15


# ── API-Keys aus config laden ────────────────────────────────────────────────

def _get_keys() -> dict:
    try:
        import config  # type: ignore
        return {
            "aisstream":          getattr(config, "AISSTREAM_KEY",          ""),
            "marinetraffic":      getattr(config, "MARINETRAFFIC_KEY",      ""),
            "vesselfinder":       getattr(config, "VESSELFINDER_KEY",       ""),
            "globalfishingwatch": getattr(config, "GLOBALFISHINGWATCH_KEY", ""),
        }
    except ImportError:
        return {}


# ── AISStream.io (kostenlos, WebSocket) ─────────────────────────────────────

def _fetch_aisstream(lat_min: float, lon_min: float,
                     lat_max: float, lon_max: float,
                     api_key: str, max_vessels: int = 40) -> list[dict]:
    """
    Holt Schiffspositionen via AISStream.io WebSocket.
    Sammelt Daten für max. 8 Sekunden, dann Verbindung schließen.
    """
    try:
        import websocket  # pip install websocket-client
    except ImportError:
        try:
            import websockets  # pip install websockets (async)
            return _fetch_aisstream_async(lat_min, lon_min, lat_max, lon_max,
                                          api_key, max_vessels)
        except ImportError:
            return []   # websocket-Bibliothek nicht installiert

    vessels: list[dict] = []
    done = threading.Event()

    subscribe_msg = json.dumps({
        "APIKey":       api_key,
        "BoundingBoxes": [[
            [lat_min, lon_min],
            [lat_max, lon_max],
        ]],
        "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport"],
    })

    def on_message(ws_app, msg):
        if len(vessels) >= max_vessels:
            done.set()
            return
        try:
            data = json.loads(msg)
            meta = data.get("MetaData") or {}
            lat  = meta.get("latitude")
            lon  = meta.get("longitude")
            if lat and lon and -90 <= lat <= 90 and -180 <= lon <= 180:
                vessels.append({
                    "lat":      round(lat, 5),
                    "lon":      round(lon, 5),
                    "mmsi":     str(meta.get("MMSI", "")),
                    "name":     (meta.get("ShipName") or "(unbekannt)").strip(),
                    "type":     "",
                    "flag":     meta.get("flag", ""),
                    "speed_kn": round(meta.get("Sog", 0) or 0, 1),
                    "heading":  meta.get("TrueHeading"),
                    "source":   "AISStream",
                })
        except Exception:
            pass

    def on_error(ws_app, err):
        done.set()

    def on_close(ws_app, *args):
        done.set()

    def on_open(ws_app):
        ws_app.send(subscribe_msg)

    ws = websocket.WebSocketApp(
        "wss://stream.aisstream.io/v0/stream",
        on_open=on_open, on_message=on_message,
        on_error=on_error, on_close=on_close,
    )
    t = threading.Thread(target=ws.run_forever, daemon=True)
    t.start()
    done.wait(timeout=8)   # max 8 Sekunden sammeln
    ws.close()
    return vessels


def _fetch_aisstream_async(lat_min, lon_min, lat_max, lon_max,
                            api_key, max_vessels) -> list[dict]:
    """Async AIS-Daten via websockets-Bibliothek (v16+ kompatibel)."""
    import asyncio

    async def _collect():
        try:
            import websockets as ws_lib
            vessels: list[dict] = []
            # Beide Klassen einschließen: Class A (Tanker/Cargo) + Class B (kleine Schiffe)
            sub = json.dumps({
                "APIKey":        api_key,
                "BoundingBoxes": [[[lat_min, lon_min], [lat_max, lon_max]]],
                "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport"],
            })
            async with ws_lib.connect(
                "wss://stream.aisstream.io/v0/stream",
                open_timeout=10,
            ) as ws:
                await ws.send(sub)
                # 15 Sekunden sammeln – Hormus kann kurze Pausen haben
                deadline = asyncio.get_event_loop().time() + 15
                while asyncio.get_event_loop().time() < deadline and len(vessels) < max_vessels:
                    try:
                        remaining = deadline - asyncio.get_event_loop().time()
                        raw  = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 5))
                        data = json.loads(raw)
                        meta = data.get("MetaData") or {}
                        lat  = meta.get("latitude")
                        lon  = meta.get("longitude")
                        if lat and lon and -90 <= lat <= 90 and -180 <= lon <= 180:
                            # SOG aus MetaData (direkt verfügbar) oder aus Message-Body
                            sog = meta.get("Sog") or 0
                            if not sog:
                                msg = data.get("Message", {})
                                body = msg.get("PositionReport") or msg.get("StandardClassBPositionReport") or {}
                                sog = body.get("Sog", 0)
                            vessels.append({
                                "lat":      round(lat, 5),
                                "lon":      round(lon, 5),
                                "mmsi":     str(meta.get("MMSI", "")),
                                "name":     (meta.get("ShipName") or "?").strip(),
                                "type":     "",
                                "flag":     meta.get("flag", ""),
                                "speed_kn": round(float(sog or 0), 1),
                                "heading":  meta.get("TrueHeading"),
                                "source":   "AISStream",
                            })
                    except asyncio.TimeoutError:
                        break
            return vessels
        except Exception:
            return []

    try:
        return asyncio.run(_collect())
    except RuntimeError:
        # Falls bereits ein Event-Loop läuft (z.B. Jupyter)
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _collect())
            return future.result(timeout=20)
    except Exception:
        return []


# ── MarineTraffic (Free Tier) ────────────────────────────────────────────────

def _fetch_marinetraffic(lat_min, lon_min, lat_max, lon_max, api_key) -> list[dict]:
    url = f"https://services.marinetraffic.com/api/exportvessels/v:8/{api_key}"
    params = {
        "MINLAT": lat_min, "MAXLAT": lat_max,
        "MINLON": lon_min, "MAXLON": lon_max,
        "protocol": "json",
    }
    try:
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json() or []
        vessels = []
        for v in data[:50]:
            lat = v.get("LAT"); lon = v.get("LON")
            if lat and lon:
                vessels.append({
                    "lat":      float(lat),
                    "lon":      float(lon),
                    "name":     v.get("NAME", "?"),
                    "type":     v.get("TYPE", ""),
                    "flag":     v.get("FLAG", ""),
                    "speed_kn": float(v.get("SPEED", 0) or 0) / 10,
                    "source":   "MarineTraffic",
                })
        return vessels
    except Exception:
        return []


# ── Bounding Box aus Region/Ort ──────────────────────────────────────────────

_REGION_BOXES: dict[str, tuple[float, float, float, float]] = {
    "Hormuz-Strasse":  (22.0,  54.0,  28.0,  62.0),
    "Rotes Meer":      (10.0,  30.0,  28.0,  46.0),
    "Suez-Kanal":      (29.5,  31.5,  31.5,  33.5),
    "Bosporus":        (40.5,  28.5,  41.5,  29.5),
    "Taiwan-Strasse":  (20.0, 118.0,  28.0, 124.0),
    "Schwarzes Meer":  (40.0,  27.0,  47.0,  42.0),
    "Ostsee":          (53.0,   9.0,  66.0,  30.0),
    "Naher Osten":     (12.0,  32.0,  38.0,  62.0),
    "Mittelmeer":      (30.0,  -6.0,  46.0,  37.0),
    "Nordsee":         (51.0,  -4.0,  62.0,  10.0),
    "Persischer Golf": (22.0,  46.0,  30.0,  60.0),
}


def _region_to_bbox(region: str) -> Optional[tuple[float, float, float, float]]:
    for name, box in _REGION_BOXES.items():
        if name.lower() in region.lower() or region.lower() in name.lower():
            return box
    try:
        r = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": region, "count": 1, "language": "de", "format": "json"},
            timeout=8,
        )
        res = (r.json().get("results") or [None])[0]
        if res:
            lat, lon = res["latitude"], res["longitude"]
            m = 3.0
            return (lat - m, lon - m, lat + m, lon + m)
    except Exception:
        pass
    return None


# ── Hauptfunktion ────────────────────────────────────────────────────────────

def _fetch_myshiptracking(lat_min: float, lon_min: float,
                          lat_max: float, lon_max: float) -> list[dict]:
    """
    Kostenloser Fallback: myshiptracking.com öffentlicher Karten-Endpoint.
    Kein API-Key erforderlich.
    """
    try:
        url = (
            "https://www.myshiptracking.com/requests/vesselsonmap.php"
            f"?type=json&minlat={lat_min:.4f}&minlon={lon_min:.4f}"
            f"&maxlat={lat_max:.4f}&maxlon={lon_max:.4f}&zoom=6"
        )
        r = requests.get(url, timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0",
                                  "Referer": "https://www.myshiptracking.com/"})
        r.raise_for_status()
        data = r.json()
        vessels = []
        for item in (data if isinstance(data, list) else data.get("data", [])):
            try:
                lat = float(item.get("lat") or item.get("LAT") or 0)
                lon = float(item.get("lng") or item.get("LON") or item.get("lon") or 0)
                if not lat or not lon:
                    continue
                vessels.append({
                    "lat":      lat,
                    "lon":      lon,
                    "name":     str(item.get("name") or item.get("NAME") or "?"),
                    "mmsi":     str(item.get("mmsi") or item.get("MMSI") or ""),
                    "type":     str(item.get("type") or item.get("TYPE") or ""),
                    "flag":     str(item.get("flag") or item.get("FLAG") or ""),
                    "speed_kn": float(item.get("speed") or item.get("SPEED") or 0),
                    "heading":  item.get("heading") or item.get("HDG"),
                    "source":   "myshiptracking",
                })
            except Exception:
                continue
        return vessels
    except Exception:
        return []


def _fetch_datalastic(lat_min: float, lon_min: float,
                      lat_max: float, lon_max: float) -> list[dict]:
    """
    Zweiter kostenloser Fallback: datalastic.com öffentlicher Vessel-Feed.
    Kein API-Key für Basis-Daten erforderlich.
    """
    try:
        url = (
            "https://api.datalastic.com/api/v0/vessel_inarea"
            f"?lat_min={lat_min:.3f}&lon_min={lon_min:.3f}"
            f"&lat_max={lat_max:.3f}&lon_max={lon_max:.3f}"
        )
        r = requests.get(url, timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "NEXUS-OSINT/1.0"})
        r.raise_for_status()
        data = r.json()
        vessels = []
        for item in (data.get("data", []) if isinstance(data, dict) else data):
            try:
                lat = float(item.get("lat") or 0)
                lon = float(item.get("lng") or item.get("lon") or 0)
                if not lat or not lon:
                    continue
                vessels.append({
                    "lat":      lat,
                    "lon":      lon,
                    "name":     str(item.get("name") or "?"),
                    "mmsi":     str(item.get("mmsi") or ""),
                    "type":     str(item.get("vessel_type") or ""),
                    "flag":     str(item.get("flag") or ""),
                    "speed_kn": float(item.get("speed") or 0),
                    "heading":  item.get("heading"),
                    "source":   "datalastic",
                })
            except Exception:
                continue
        return vessels
    except Exception:
        return []


def _fetch_globalfishingwatch(lat_min: float, lon_min: float,
                              lat_max: float, lon_max: float,
                              api_key: str) -> list[dict]:
    """
    Global Fishing Watch API – Fischereischiffe weltweit.
    Kostenloser Key: globalfishingwatch.org/our-apis/
    Relevant: Fischerfahrzeuge werden als Tarnung für Sanktionsumgehung genutzt.
    """
    try:
        url = "https://gateway.api.globalfishingwatch.org/v3/vessels/search"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        params = {
            "datasets[0]": "public-global-fishing-vessels:latest",
            "lat": (lat_min + lat_max) / 2,
            "lon": (lon_min + lon_max) / 2,
            "distance": 500,  # km Radius
            "limit": 50,
        }
        r = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        vessels = []
        for v in (data.get("entries") or []):
            lat = v.get("lat") or v.get("lastPositionLat")
            lon = v.get("lon") or v.get("lastPositionLon")
            if lat and lon and lat_min <= float(lat) <= lat_max and lon_min <= float(lon) <= lon_max:
                vessels.append({
                    "lat":      round(float(lat), 5),
                    "lon":      round(float(lon), 5),
                    "name":     str(v.get("shipname") or v.get("name") or "?"),
                    "mmsi":     str(v.get("mmsi") or ""),
                    "type":     "Fishing",
                    "flag":     str(v.get("flag") or ""),
                    "speed_kn": float(v.get("speed") or 0),
                    "source":   "GlobalFishingWatch",
                })
        return vessels
    except Exception:
        return []


def _fetch_vesselfinder_public(lat_min: float, lon_min: float,
                                lat_max: float, lon_max: float) -> list[dict]:
    """
    VesselFinder öffentlicher Tile-Endpoint – kein Key nötig.
    Aggregiert AIS-Daten aus eigenem Netzwerk.
    """
    try:
        url = (
            "https://www.vesselfinder.com/api/pub/vesselsonmap"
            f"?bbox={lon_min:.3f},{lat_min:.3f},{lon_max:.3f},{lat_max:.3f}"
            "&zoom=6&show_names=1"
        )
        r = requests.get(url, timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0",
                                  "Referer": "https://www.vesselfinder.com/"})
        r.raise_for_status()
        data = r.json()
        vessels = []
        for item in (data if isinstance(data, list) else data.get("data", [])):
            try:
                # VesselFinder Format: [mmsi, lat*600000, lon*600000, cog, sog*10, name, ...]
                if isinstance(item, list) and len(item) >= 6:
                    lat = item[1] / 600000 if isinstance(item[1], (int, float)) else 0
                    lon = item[2] / 600000 if isinstance(item[2], (int, float)) else 0
                    sog = (item[4] or 0) / 10
                    if lat and lon:
                        vessels.append({
                            "lat":      round(lat, 5),
                            "lon":      round(lon, 5),
                            "name":     str(item[5] if len(item) > 5 else "?"),
                            "mmsi":     str(item[0] or ""),
                            "type":     "",
                            "flag":     "",
                            "speed_kn": round(sog, 1),
                            "source":   "VesselFinder",
                        })
                elif isinstance(item, dict):
                    lat = float(item.get("lat") or 0)
                    lon = float(item.get("lng") or item.get("lon") or 0)
                    if lat and lon:
                        vessels.append({
                            "lat":      round(lat, 5),
                            "lon":      round(lon, 5),
                            "name":     str(item.get("name") or "?"),
                            "mmsi":     str(item.get("mmsi") or ""),
                            "type":     str(item.get("type") or ""),
                            "flag":     str(item.get("flag") or ""),
                            "speed_kn": float(item.get("speed") or 0),
                            "source":   "VesselFinder",
                        })
            except Exception:
                continue
        return vessels
    except Exception:
        return []


def _deduplicate_vessels(all_vessels: list[dict]) -> list[dict]:
    """
    Entfernt Duplikate über mehrere Quellen.
    Priorisiert: AISStream > MarineTraffic > VesselFinder > GFW > MyShipTracking > Datalastic
    Gleiche MMSI oder sehr nah beieinander (<500m) = selbes Schiff.
    """
    from math import radians, cos, sin, sqrt, atan2

    def haversine_m(lat1, lon1, lat2, lon2):
        R = 6371000
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
        return R * 2 * atan2(sqrt(a), sqrt(1 - a))

    source_priority = {
        "AISStream": 0, "MarineTraffic": 1, "VesselFinder": 2,
        "GlobalFishingWatch": 3, "myshiptracking": 4, "datalastic": 5,
    }

    seen_mmsi: dict[str, dict] = {}
    result: list[dict] = []

    for v in all_vessels:
        mmsi = v.get("mmsi", "").strip()
        src  = v.get("source", "")
        prio = source_priority.get(src, 9)

        if mmsi and mmsi != "0":
            if mmsi not in seen_mmsi:
                seen_mmsi[mmsi] = v
                result.append(v)
            else:
                existing_prio = source_priority.get(seen_mmsi[mmsi].get("source", ""), 9)
                if prio < existing_prio:
                    # Bessere Quelle gefunden – ersetzen
                    idx = next(i for i, r in enumerate(result) if r.get("mmsi") == mmsi)
                    # Quellen zusammenführen
                    v["sources"] = list({seen_mmsi[mmsi]["source"], src})
                    result[idx] = v
                    seen_mmsi[mmsi] = v
                else:
                    # Quellinfo ergänzen
                    existing = seen_mmsi[mmsi]
                    srcs = existing.get("sources", [existing.get("source", "")])
                    if src not in srcs:
                        srcs.append(src)
                    existing["sources"] = srcs
        else:
            # Kein MMSI → Geo-Duplikat-Check (<500m)
            is_dup = False
            for existing in result:
                d = haversine_m(v["lat"], v["lon"], existing["lat"], existing["lon"])
                if d < 500:
                    is_dup = True
                    break
            if not is_dup:
                result.append(v)

    return result


def get_vessels(region: str) -> dict:
    """
    Schiffsdaten aus verifizierten Quellen – nur Quellen die wirklich liefern.

    Aktiv (getestet, funktioniert):
      AISStream.io   – WebSocket, Echtzeit, kostenloser Key (aisstream.io)
      MarineTraffic  – REST, nur mit kostenpflichtigem Key

    Vorbereitet (brauchen Key, aktuell nicht konfiguriert):
      GlobalFishingWatch – kostenloser Key über globalfishingwatch.org
      Datalastic         – kostenpflichtiger Key

    Deaktiviert (APIs geblockt/geändert ohne Key):
      VesselFinder   – 404 seit API-Update
      MyShipTracking – geblockt ohne Session-Token
      MarineTraffic  – 403 für öffentliche Tile-Endpoints
    """
    import concurrent.futures

    keys = _get_keys()
    bbox = _region_to_bbox(region)
    ts   = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    if not bbox:
        return {
            "region": region, "timestamp": ts, "vessel_count": 0,
            "vessels": [], "source": "keine Region erkannt",
            "has_key": False, "bbox": None,
        }

    lat_min, lon_min, lat_max, lon_max = bbox
    all_vessels: list[dict] = []
    active_sources: list[str] = []

    # ── Aktive Quellen ───────────────────────────────────────────────────────
    tasks = []

    if keys.get("aisstream"):
        def run_aisstream():
            return _fetch_aisstream(lat_min, lon_min, lat_max, lon_max,
                                    keys["aisstream"]), "AISStream.io"
        tasks.append(run_aisstream)

    if keys.get("marinetraffic"):
        def run_marinetraffic():
            return _fetch_marinetraffic(lat_min, lon_min, lat_max, lon_max,
                                        keys["marinetraffic"]), "MarineTraffic"
        tasks.append(run_marinetraffic)

    if keys.get("globalfishingwatch"):
        def run_gfw():
            return _fetch_globalfishingwatch(lat_min, lon_min, lat_max, lon_max,
                                             keys["globalfishingwatch"]), "GlobalFishingWatch"
        tasks.append(run_gfw)

    # Spire Maritime Satellite-AIS (globale Abdeckung, auch Hormuz/Rotes Meer)
    try:
        from nexus_spire import fetch_spire_vessels  # type: ignore
        if _get_keys().get("spire") or __import__("nexus_spire")._get_key():
            def run_spire():
                v = fetch_spire_vessels(lat_min, lon_min, lat_max, lon_max)
                return v, "Spire-SatAIS"
            tasks.append(run_spire)
    except Exception:
        pass

    if not tasks:
        return {
            "region": region, "timestamp": ts, "vessel_count": 0,
            "vessels": [], "source": "kein API-Key konfiguriert (AISSTREAM_KEY in config.py)",
            "has_key": False, "bbox": bbox,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(fn): fn.__name__ for fn in tasks}
        for future in concurrent.futures.as_completed(futures, timeout=20):
            try:
                result = future.result()
                if result:
                    vessels, src = result
                    if vessels and src:
                        for v in vessels:
                            v.setdefault("source", src)
                        all_vessels.extend(vessels)
                        active_sources.append(f"{src}({len(vessels)})")
            except Exception:
                pass

    # ── Deduplizieren ────────────────────────────────────────────────────────
    merged = _deduplicate_vessels(all_vessels)

    source_str = " + ".join(active_sources) if active_sources else "keine Daten empfangen"
    has_key    = bool(keys.get("aisstream") or keys.get("marinetraffic"))

    return {
        "region":       region,
        "timestamp":    ts,
        "vessel_count": len(merged),
        "vessels":      merged,
        "source":       source_str,
        "has_key":      has_key,
        "bbox":         bbox,
        "raw_count":    len(all_vessels),   # vor Deduplizierung
    }


def vessels_for_map(region: str) -> list[dict]:
    """Gibt Schiffs-Marker fuer die Live-Karte zurueck."""
    result = get_vessels(region)
    return [
        {
            "lat":     v["lat"],
            "lon":     v["lon"],
            "name":    v.get("name", "?"),
            "mmsi":    v.get("mmsi", ""),
            "type":    v.get("type", ""),
            "flag":    v.get("flag", ""),
            "speed":   v.get("speed_kn", 0),
            "heading": v.get("heading"),
            "source":  v.get("source", "AIS"),
            "sources": v.get("sources", []),
        }
        for v in result.get("vessels", [])
    ]


if __name__ == "__main__":
    import sys
    region = sys.argv[1] if len(sys.argv) > 1 else "Rotes Meer"
    result = get_vessels(region)
    print(f"Region:  {result['region']}")
    print(f"Quellen: {result['source']}")
    print(f"Schiffe: {result['vessel_count']} (roh: {result.get('raw_count',0)})")
    print()
    for v in result["vessels"][:20]:
        srcs = ",".join(v.get("sources", [v.get("source","?")]))
        print(f"  [{srcs}] {v.get('name','?'):22s}  {v.get('speed_kn',0):5.1f}kn")
