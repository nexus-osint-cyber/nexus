"""
NEXUS - Quellen-Gesundheits-Monitor (T176)
Live-Überwachung der wichtigsten externen OSINT-Datenquellen — im Gegensatz zum
Post-Mortem-Ansatz von nexus_diagnostic.py (der Module einmalig testet und das
Ergebnis in nexus_diag_results.json ablegt), prüft dieses Modul die kritischen
Quell-Endpunkte AKTIV und LIVE bei jedem Dashboard-Aufruf (mit kurzem Cache),
und führt eine rollierende Erfolgshistorie pro Quelle (Uptime-%, Latenz-Trend).

Architektur:
  _SOURCES   – kuratierte Liste der externen APIs/Feeds mit Leicht-Check
               (HEAD/GET auf Basis-Endpunkt, kurzer Timeout)
  check_all_sources() – parallele Live-Prüfung (ThreadPoolExecutor), gecached
  _HISTORY   – In-Memory rollierende Erfolgshistorie (letzte 40 Checks/Quelle)
               → Uptime-% und Sparkline-Daten fürs Dashboard

T176 = eines der drei vom Nutzer ausdrücklich genehmigten Verbesserungen
("Quellen-Gesundheits-Dashboard für Live-Monitoring statt Post-Mortem-Analyse").
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

import requests

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "*/*",
}

_TIMEOUT = 6          # Sekunden – bewusst kurz, das ist ein Health-Ping, kein Datenabruf
_CACHE_TTL = 90       # Sekunden – Live genug, aber schont die APIs bei häufigem Reload
_HISTORY_MAX = 40     # wie viele Checks pro Quelle für Uptime-% behalten werden

# ── Kuratierte Liste der wichtigsten externen Quellen ─────────────────────────
# (Leichtgewichtige Erreichbarkeits-Checks – kein voller Datenabruf, daher schnell)
_SOURCES: dict[str, dict] = {
    "GDELT":            {"url": "https://api.gdeltproject.org/api/v2/doc/doc?query=test&mode=artlist&maxrecords=1&format=json",
                         "category": "Nachrichten/Geo-Events", "module": "nexus_gdelt.py"},
    "ACLED/UCDP":       {"url": "https://ucdpapi.pcr.uu.se/api/gedevents/24.0?pagesize=1",
                         "category": "Konfliktdaten", "module": "nexus_acled.py"},
    "ReliefWeb":        {"url": "https://api.reliefweb.int/v1/reports?limit=1",
                         "category": "Humanitäre Lage", "module": "nexus_acled.py"},
    "Reddit":           {"url": "https://www.reddit.com/r/worldnews/new/.rss",
                         "category": "Social Media", "module": "nexus_reddit.py"},
    "USGS Erdbeben":    {"url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_day.geojson",
                         "category": "Seismik", "module": "nexus_seismic.py"},
    "NASA FIRMS":       {"url": "https://firms.modaps.eosdis.nasa.gov/",
                         "category": "Satelliten/Brände", "module": "nexus_firms.py"},
    "OpenSky Flights":  {"url": "https://opensky-network.org/api/states/all?lamin=0&lomin=0&lamax=1&lomax=1",
                         "category": "Flugdaten", "module": "nexus_flights.py"},
    "ADS-B (adsb.lol)": {"url": "https://api.adsb.lol/v2/point/0/0/1",
                         "category": "Flugdaten", "module": "nexus_flights.py"},
    "aviationweather (NOTAM)": {"url": "https://aviationweather.gov/api/data/notam?format=json&loc=KJFK",
                         "category": "Luftsperren", "module": "nexus_notam.py"},
    "Sentinel Hub":     {"url": "https://services.sentinel-hub.com/oauth/token",
                         "category": "SAR-Satellitenbilder", "module": "nexus_sar.py"},
    "ASF SAR-Katalog":  {"url": "https://api.daac.asf.alaska.edu/services/search/param?platform=Sentinel-1&maxResults=1&output=json",
                         "category": "SAR-Satellitenbilder", "module": "nexus_sar.py"},
    "Open-Meteo Wetter":{"url": "https://api.open-meteo.com/v1/forecast?latitude=50&longitude=10&current_weather=true",
                         "category": "Wetter", "module": "nexus_weather.py"},
    "Wikipedia":        {"url": "https://de.wikipedia.org/w/api.php?action=query&format=json&list=search&srsearch=test",
                         "category": "Hintergrund-Kontext", "module": "nexus_wiki.py"},
    "Nominatim Geocoding": {"url": "https://nominatim.openstreetmap.org/search?q=Berlin&format=json&limit=1",
                         "category": "Geocoding", "module": "nexus_search.py"},
    "GPSJam":           {"url": "https://gpsjam.org/",
                         "category": "GPS-Störungen", "module": "nexus_gpsjam.py"},
    "Blitzortung":      {"url": "https://www.blitzortung.org/en/live_lightning_maps.php",
                         "category": "Blitz-Ortung", "module": "nexus_lightning.py"},
    "ntfy.sh Push":     {"url": "https://ntfy.sh/",
                         "category": "Alert-Versand", "module": "nexus_alerts.py"},
    "EONET (NASA)":     {"url": "https://eonet.gsfc.nasa.gov/api/v3/events?limit=1",
                         "category": "Naturereignisse", "module": "nexus_eonet.py"},
    "n2yo Satelliten":  {"url": "https://api.n2yo.com/rest/v1/satellite/",
                         "category": "Satelliten-Timing", "module": "nexus_satellite_timing.py"},
    "ISW Reports (RSS)":{"url": "https://www.understandingwar.org/backgrounder/russian-offensive-campaign-assessment",
                         "category": "Spezial-OSINT", "module": "nexus_rss.py"},
}

# ── Internes Status-Cache + rollierende Historie ──────────────────────────────
_CACHE: Optional[list[dict]] = None
_CACHE_TS: float = 0.0
_HISTORY: dict[str, list[dict]] = {}


def _classify(elapsed: float, ok: bool, code: Optional[int]) -> str:
    """Bewertet einen Check anhand Antwortzeit + Status-Code."""
    if not ok:
        return "fehler"
    if code is not None and code >= 400 and code != 405:   # 405 = Method not allowed (HEAD oft blockiert, aber Server lebt)
        return "fehler"
    if elapsed > _TIMEOUT * 0.7:
        return "langsam"
    return "ok"


def _check_one(name: str, spec: dict) -> dict:
    """
    Führt einen leichten Live-Check gegen eine Quelle aus.
    Versucht zuerst HEAD (spart Bandbreite), fällt bei 403/405 auf GET zurück —
    manche APIs blocken HEAD-Requests grundlos.
    """
    url = spec["url"]
    t0 = time.monotonic()
    ok, code, err = False, None, None

    for method in ("HEAD", "GET"):
        try:
            r = requests.request(method, url, headers=_HEADERS, timeout=_TIMEOUT,
                                 allow_redirects=True, stream=(method == "GET"))
            code = r.status_code
            if method == "GET":
                # Nur ein paar Bytes lesen – das ist ein Erreichbarkeits-Check, kein Datenabruf
                next(r.iter_content(chunk_size=256), b"")
                r.close()
            ok = code < 500   # 4xx zählt noch als "Server lebt", nur Server-Fehler = down
            if code not in (403, 405) or method == "GET":
                break
        except requests.Timeout:
            err = "Timeout"
            continue
        except Exception as exc:
            err = str(exc)[:120]
            continue

    elapsed = time.monotonic() - t0
    elapsed_ms = round(elapsed * 1000)
    status = _classify(elapsed, ok, code) if ok or code else "fehler"
    if not ok and code is None:
        status = "fehler"

    result = {
        "name":       name,
        "category":   spec.get("category", "?"),
        "module":     spec.get("module", "?"),
        "status":     status,            # ok | langsam | fehler
        "latency_ms": elapsed_ms,
        "http_code":  code,
        "error":      err if status == "fehler" else None,
        "checked_at": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
    }

    # Rollierende Historie pflegen (für Uptime-% + Sparkline)
    hist = _HISTORY.setdefault(name, [])
    hist.append({"ts": time.time(), "status": status, "latency_ms": elapsed_ms})
    if len(hist) > _HISTORY_MAX:
        del hist[: len(hist) - _HISTORY_MAX]

    return result


def _uptime_pct(name: str) -> Optional[float]:
    hist = _HISTORY.get(name) or []
    if not hist:
        return None
    ok_count = sum(1 for h in hist if h["status"] in ("ok", "langsam"))
    return round(100.0 * ok_count / len(hist), 1)


def check_all_sources(force: bool = False) -> dict:
    """
    Prüft alle kuratierten Quellen parallel und live.
    Cached für _CACHE_TTL Sekunden, damit nicht jeder Dashboard-Reload
    sämtliche externen APIs neu anpingt (Quellenschonung + Geschwindigkeit).
    """
    global _CACHE, _CACHE_TS
    now = time.monotonic()
    if not force and _CACHE is not None and (now - _CACHE_TS) < _CACHE_TTL:
        return _build_summary(_CACHE, from_cache=True)

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = {ex.submit(_check_one, name, spec): name for name, spec in _SOURCES.items()}
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:
                name = futures[fut]
                results.append({
                    "name": name, "category": _SOURCES[name].get("category", "?"),
                    "module": _SOURCES[name].get("module", "?"),
                    "status": "fehler", "latency_ms": None, "http_code": None,
                    "error": str(exc)[:120],
                    "checked_at": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
                })

    # Stabil sortieren (nach Name) für konsistente UI-Reihenfolge
    results.sort(key=lambda r: r["name"])
    _CACHE = results
    _CACHE_TS = now
    return _build_summary(results, from_cache=False)


def _build_summary(results: list[dict], from_cache: bool) -> dict:
    ok      = sum(1 for r in results if r["status"] == "ok")
    langsam = sum(1 for r in results if r["status"] == "langsam")
    fehler  = sum(1 for r in results if r["status"] == "fehler")

    enriched = []
    for r in results:
        rr = dict(r)
        rr["uptime_pct"] = _uptime_pct(r["name"])
        rr["history"]    = [h["status"] for h in (_HISTORY.get(r["name"]) or [])][-20:]
        enriched.append(rr)

    return {
        "sources": enriched,
        "summary": {"ok": ok, "langsam": langsam, "fehler": fehler, "total": len(results)},
        "timestamp": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S UTC"),
        "from_cache": from_cache,
        "cache_ttl_s": _CACHE_TTL,
    }


def health_summary_text() -> str:
    """Kurztext-Zusammenfassung für LLM-Kontext / Reports."""
    data = check_all_sources()
    s = data["summary"]
    lines = [f"[QUELLEN-GESUNDHEIT] {s['ok']} OK · {s['langsam']} langsam · {s['fehler']} down "
             f"(von {s['total']}, Stand {data['timestamp']})"]
    for r in data["sources"]:
        if r["status"] != "ok":
            lat = f"{r['latency_ms']}ms" if r["latency_ms"] is not None else "?"
            lines.append(f"  ⚠ {r['name']} ({r['category']}): {r['status']} – {lat}"
                         + (f" – {r['error']}" if r.get("error") else ""))
    return "\n".join(lines)


# ── Direktaufruf zum Testen ───────────────────────────────────────────────────
if __name__ == "__main__":
    print("NEXUS Quellen-Gesundheits-Check (live)...\n")
    data = check_all_sources(force=True)
    s = data["summary"]
    print(f"Zusammenfassung: {s['ok']} OK · {s['langsam']} langsam · {s['fehler']} Fehler "
          f"(von {s['total']})\n")
    for r in data["sources"]:
        icon = {"ok": "✅", "langsam": "🟡", "fehler": "❌"}.get(r["status"], "?")
        lat = f"{r['latency_ms']}ms" if r["latency_ms"] is not None else "–"
        up = f"{r['uptime_pct']}%" if r["uptime_pct"] is not None else "–"
        print(f"  {icon} {r['name']:<22} [{r['category']:<22}] {lat:>7} · Uptime {up:>6} "
              f"· HTTP {r['http_code']}")
        if r.get("error"):
            print(f"       └─ {r['error']}")
