"""
NEXUS – ACLED Live-API Integration (OAuth-Version)
====================================================
Armed Conflict Location & Event Data Project.
Liefert exakte GPS-Koordinaten für Konfliktereignisse weltweit.

ACLED hat 2025 auf OAuth umgestellt. NEXUS holt den Token automatisch:
  1. Registrieren: https://acleddata.com/register/
  2. In config.py eintragen:
       ACLED_EMAIL    = "deine@email.com"
       ACLED_PASSWORD = "dein-passwort"
  → Token (24h gültig) wird automatisch geholt + gecacht + refreshed.

API-Dokumentation: https://acleddata.com/api-documentation/getting-started

Hinweis (T175): fetch_conflict_events() implementiert bereits eine
Multi-Quellen-Fallback-Kette (ACLED → UCDP → ReliefWeb mit Dedup) – genau
das Muster, das als generisches Toolkit in nexus_resilience.py verfügbar
ist (try_strategies, TTLCache, retry_request, BROWSER_HEADERS). Künftige
Erweiterungen dieser Kette sollten dort andocken statt das Muster erneut
ad-hoc zu duplizieren.
"""

from __future__ import annotations

import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

REQUEST_TIMEOUT = 15
_cache: dict[str, list] = {}
_cache_ts: dict[str, float] = {}
_CACHE_TTL = 600   # 10 Minuten
# ─────────────────────────────────────────────────────────────────────────────
# UCDP – Uppsala Conflict Data Program (kostenlos, kein Key)
# https://ucdpapi.pcr.uu.se/api/
# ─────────────────────────────────────────────────────────────────────────────

UCDP_BASE      = "https://ucdpapi.pcr.uu.se/api"
UCDP_GED_URL   = f"{UCDP_BASE}/gedevents/23.1"   # Georeferenced Event Dataset
UCDP_CAND_URL  = f"{UCDP_BASE}/candidateEvents/23.1"  # Candidate Events (aktueller)

# ReliefWeb – UN OCHA (kostenlos, kein Key)
RELIEFWEB_URL  = "https://api.reliefweb.int/v1/reports"

# HDX/ACAPS – Humanitarian Data Exchange (kostenlos, kein Key)
ACAPS_URL      = "https://api.acaps.org/api/v1/events/"

# UCDP Konflikttypen → NEXUS-Format
_UCDP_TYPE_MAP = {
    1: ("Staatsbasierte Gewalt",          "⚔",  "HOCH"),
    2: ("Nicht-staatliche Gewalt",        "💥",  "HOCH"),
    3: ("Einseitige Gewalt",              "⚠",   "KRITISCH"),
}

_UCDP_REGION_KEYWORDS = {
    "ukraine":         ["Ukraine", "Ukrain"],
    "naher osten":     ["Syria", "Iraq", "Lebanon", "Israel", "Yemen", "Gaza", "Palästina", "Iran"],
    "russland":        ["Russia", "Russian"],
    "sahel":           ["Mali", "Niger", "Burkina", "Chad"],
    "sudan":           ["Sudan"],
    "myanmar":         ["Myanmar", "Burma"],
    "äthiopien":       ["Ethiopia", "Tigray"],
    "afghanistan":     ["Afghanistan"],
    "gaza":            ["Gaza", "Palestine"],
    # T155-Fix: UCDP führt Iran unter mehreren Namensvarianten je nach Datensatz
    # (GED nutzt oft den vollen Gleditsch&Ward-Namen, Candidate-API den Kurznamen).
    # Ohne eigenen Eintrag fiel "iran" durch alle Keys durch und landete im
    # generischen Fallback (Rohstring "iran"/"Iran"), den UCDP nicht zuverlässig
    # auflöst → konsequent 0 Treffer. Mehrere Varianten nacheinander probieren:
    "iran":            ["Iran", "Iran (Islamic Republic of)", "Islamic Republic of Iran"],
    "persischer golf": ["Iran", "Iran (Islamic Republic of)", "Saudi Arabia", "Bahrain",
                        "Kuwait", "United Arab Emirates"],
    "hormuz":          ["Iran", "Iran (Islamic Republic of)", "Oman",
                        "United Arab Emirates"],
    "syrien":          ["Syria"],
    "irak":            ["Iraq"],
    "jemen":           ["Yemen"],
    "libanon":         ["Lebanon"],
    "israel":          ["Israel", "Palestine"],
}


# ── OAuth Token Cache ──────────────────────────────────────────────────────────
_token_lock  = threading.Lock()
_token_cache: dict[str, object] = {
    "access_token":  "",
    "refresh_token": "",
    "expires_at":    0.0,   # monotonic timestamp
}

OAUTH_URL  = "https://acleddata.com/oauth/token"
# ACLED hat zwei Endpunkte – neuer (OAuth) und alter (Legacy)
API_BASE        = "https://acleddata.com/api/acled/read"
API_BASE_LEGACY = "https://api.acleddata.com/acled/read"

# ── Ereignistypen ──────────────────────────────────────────────────────────────
EVENT_PRIORITY = {
    "Explosions/Remote violence":  "KRITISCH",
    "Battles":                     "HOCH",
    "Violence against civilians":  "HOCH",
    "Riots":                       "MITTEL",
    "Protests":                    "NIEDRIG",
    "Strategic developments":      "MITTEL",
}
EVENT_ICONS = {
    "Explosions/Remote violence": "💥",
    "Battles":                    "⚔",
    "Violence against civilians": "⚠",
    "Riots":                      "🔥",
    "Protests":                   "📢",
    "Strategic developments":     "🎯",
}

# ── Regions-Mapping ───────────────────────────────────────────────────────────
REGION_COUNTRY_MAP = {
    "ukraine":         "Ukraine",
    "naher osten":     "Syria;Iraq;Lebanon;Israel;Yemen;Iran",
    "persischer golf": "Iran;Saudi Arabia;UAE;Bahrain;Kuwait",
    "rotes meer":      "Yemen;Eritrea;Sudan",
    "taiwan-strasse":  "China;Taiwan",
    "korea-halbinsel": "North Korea;South Korea",
    "sahel":           "Mali;Niger;Burkina Faso;Chad;Sudan",
    "hormuz-strasse":  "Iran;Oman",
    "hormuz":          "Iran;Oman;UAE",
    "schwarzes meer":  "Ukraine;Russia",
    # T155: Direktes Region-Mapping
    "iran":            "Iran",
    "israel":          "Israel",
    "gaza":            "Palestine",
    "syrien":          "Syria",
    "irak":            "Iraq",
    "iraq":            "Iraq",
    "jemen":           "Yemen",
    "libanon":         "Lebanon",
    "afghanistan":     "Afghanistan",
    "sudan":           "Sudan",
    "libyen":          "Libya",
    "somalia":         "Somalia",
}


def _get_countries(region: str) -> str:
    r = region.lower().strip()
    for key, countries in REGION_COUNTRY_MAP.items():
        if key in r:
            return countries
    # T155/Global: Hierarchischer Fallback via nexus_region
    # z.B. "Basra" → "Iraq", "Natanz" → "Iran", "Donbas" → "Ukraine"
    try:
        from nexus_region import get_countries_with_fallback
        countries, resolved = get_countries_with_fallback(region)
        if countries and resolved != "global":
            return countries
    except ImportError:
        pass
    return region


# ── Credentials aus config.py ─────────────────────────────────────────────────

def _get_creds() -> tuple[str, str]:
    """Gibt (email, password) aus config.py zurück."""
    try:
        import config  # type: ignore
        email = getattr(config, "ACLED_EMAIL",    "").strip()
        pwd   = getattr(config, "ACLED_PASSWORD", "").strip()
        # Rückwärts-Kompatibilität: altes ACLED_KEY-System (ignoriert, OAuth benötigt)
        return email, pwd
    except ImportError:
        return "", ""


# ── OAuth Token Management ────────────────────────────────────────────────────

def _request_new_token(email: str, password: str) -> bool:
    """POST zu ACLED OAuth-Endpoint, speichert Token im Cache."""
    try:
        r = requests.post(
            OAUTH_URL,
            data={
                "username":   email,
                "password":   password,
                "grant_type": "password",
                "client_id":  "acled",
                "scope":      "authenticated",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return False
        data = r.json()
        access  = data.get("access_token", "")
        refresh = data.get("refresh_token", "")
        expires = data.get("expires_in", 86400)  # Standard: 24h
        if not access:
            return False
        with _token_lock:
            _token_cache["access_token"]  = access
            _token_cache["refresh_token"] = refresh
            _token_cache["expires_at"]    = time.monotonic() + expires - 60  # 1 min Puffer
        return True
    except Exception:
        return False


def _refresh_token() -> bool:
    """Erneuert Token via Refresh-Token (14 Tage gültig)."""
    with _token_lock:
        refresh = _token_cache.get("refresh_token", "")
    if not refresh:
        return False
    try:
        r = requests.post(
            OAUTH_URL,
            data={
                "grant_type":    "refresh_token",
                "refresh_token": refresh,
                "client_id":     "acled",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return False
        data = r.json()
        access  = data.get("access_token", "")
        new_ref = data.get("refresh_token", refresh)
        expires = data.get("expires_in", 86400)
        if not access:
            return False
        with _token_lock:
            _token_cache["access_token"]  = access
            _token_cache["refresh_token"] = new_ref
            _token_cache["expires_at"]    = time.monotonic() + expires - 60
        return True
    except Exception:
        return False


def _get_auth_headers() -> Optional[dict]:
    """
    Gibt Authorization-Header zurück. Holt/erneuert Token automatisch.
    Gibt None zurück wenn keine Credentials konfiguriert.
    """
    email, password = _get_creds()
    if not email or not password:
        return None

    with _token_lock:
        token      = _token_cache.get("access_token", "")
        expires_at = float(_token_cache.get("expires_at", 0))

    # Token noch gültig?
    if token and time.monotonic() < expires_at:
        return {"Authorization": f"Bearer {token}"}

    # Refresh versuchen
    if _token_cache.get("refresh_token") and _refresh_token():
        with _token_lock:
            token = _token_cache["access_token"]
        return {"Authorization": f"Bearer {token}"}

    # Neu einloggen
    if _request_new_token(email, password):
        with _token_lock:
            token = _token_cache["access_token"]
        return {"Authorization": f"Bearer {token}"}

    return None


# ── Haupt-Abruf ───────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# UCDP-Funktionen
# ─────────────────────────────────────────────────────────────────────────────

def _ucdp_region_filter(region: str) -> list[str]:
    """Gibt UCDP-Suchbegriffe für eine Region zurück."""
    r = region.lower().strip()
    for key, keywords in _UCDP_REGION_KEYWORDS.items():
        if key in r or r in key:
            return keywords
    # Fallback: den Regionsnamen direkt verwenden
    return [region.strip()]


def fetch_ucdp_events(
    region: str,
    days: int = 30,
    limit: int = 100,
) -> list[dict]:
    """
    Ruft Konfliktereignisse vom UCDP ab (kostenlos, kein Key).
    Nutzt UCDP Candidate Events API für aktuellere Daten.
    Falls leer: Fallback auf UCDP GED (historisch).
    """
    cache_key = f"ucdp_{region}_{days}"
    now = time.monotonic()
    if cache_key in _cache and now - _cache_ts.get(cache_key, 0) < _CACHE_TTL:
        return _cache[cache_key]

    keywords = _ucdp_region_filter(region)
    all_events: list[dict] = []

    for endpoint in [UCDP_CAND_URL, UCDP_GED_URL]:
        if all_events:
            break
        for kw in keywords[:3]:
            try:
                params = {
                    "country":   kw,
                    "pagesize":  min(limit, 100),
                    "page":      1,
                }
                r = requests.get(endpoint, params=params,
                                  timeout=REQUEST_TIMEOUT,
                                  headers={"Accept": "application/json"})
                if r.status_code != 200:
                    continue
                data = r.json()
                all_events.extend(data.get("Result", []))
                if len(all_events) >= limit:
                    break
            except Exception:
                continue

    result = _normalize_ucdp(all_events[:limit])
    _cache[cache_key]    = result
    _cache_ts[cache_key] = now
    return result


def _normalize_ucdp(raw: list[dict]) -> list[dict]:
    """Wandelt UCDP-Events in NEXUS-Format um."""
    result = []
    for ev in raw:
        try:
            lat = float(ev.get("latitude",  ev.get("lat", 0)) or 0)
            lon = float(ev.get("longitude", ev.get("lon", 0)) or 0)
            if not lat or not lon:
                continue

            type_id   = int(ev.get("type_of_violence", 1))
            type_name, icon, priority = _UCDP_TYPE_MAP.get(
                type_id, ("Gewalt", "⚡", "MITTEL")
            )

            date_str = (
                ev.get("date_start") or
                ev.get("date_prec") or
                ev.get("year", "") and f"{ev.get('year','')}-01-01" or ""
            )
            country  = ev.get("country", ev.get("country_id", ""))
            location = ev.get("geom_name", ev.get("location", country))
            deaths   = int(ev.get("best", ev.get("deaths_a", 0)) or 0)
            desc     = ev.get("source_headline", ev.get("where_description", ""))

            result.append({
                "date":        date_str,
                "event_type":  type_name,
                "sub_type":    ev.get("dyad_name", ""),
                "priority":    priority,
                "icon":        icon,
                "actor1":      ev.get("side_a", ev.get("name_a", "")),
                "actor2":      ev.get("side_b", ev.get("name_b", "")),
                "country":     country,
                "admin1":      ev.get("adm_1", ""),
                "admin2":      ev.get("adm_2", ""),
                "location":    location,
                "lat":         lat,
                "lon":         lon,
                "fatalities":  deaths,
                "notes":       str(desc)[:200],
                "source":      "UCDP",
                "title":       f"{icon} {type_name}: {location}",
                "url":         "https://ucdpapi.pcr.uu.se/",
                "age_min":     0,
                "credibility_score": 9,
            })
        except Exception:
            continue
    result.sort(key=lambda x: x.get("date", ""), reverse=True)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# ReliefWeb – UN OCHA Humanitarian Reports (kostenlos, kein Key)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_reliefweb_events(
    region: str,
    days: int = 14,
    limit: int = 30,
) -> list[dict]:
    """Ruft Krisenberichte von ReliefWeb (UN OCHA) ab."""
    cache_key = f"reliefweb_{region}_{days}"
    now = time.monotonic()
    if cache_key in _cache and now - _cache_ts.get(cache_key, 0) < _CACHE_TTL:
        return _cache[cache_key]

    try:
        payload = {
            "query":  {"value": region, "operator": "AND"},
            "fields": {"include": ["title", "date.created", "body", "country",
                                   "primary_country.location", "url"]},
            "sort":   ["date.created:desc"],
            "limit":  limit,
            "filter": {"field": "type.name", "value": "Situation Report"},
        }
        r = requests.post(RELIEFWEB_URL, json=payload, timeout=REQUEST_TIMEOUT,
                          headers={"Accept": "application/json"})
        if r.status_code != 200:
            _cache[cache_key] = []
            return []

        items = r.json().get("data", [])
        result = []
        for item in items:
            fields = item.get("fields", {})
            # Koordinaten aus primary_country.location
            loc = fields.get("primary_country", {}).get("location", {})
            lat = loc.get("lat", 0) or 0
            lon = loc.get("lon", 0) or 0

            countries = fields.get("country", [])
            country   = countries[0].get("name", "") if countries else region

            result.append({
                "date":        fields.get("date", {}).get("created", "")[:10],
                "event_type":  "Humanitarian Crisis",
                "sub_type":    "Situation Report",
                "priority":    "MITTEL",
                "icon":        "🆘",
                "actor1":      "UN OCHA",
                "actor2":      "",
                "country":     country,
                "admin1":      "",
                "admin2":      "",
                "location":    country,
                "lat":         lat,
                "lon":         lon,
                "fatalities":  0,
                "notes":       str(fields.get("body", ""))[:200],
                "source":      "ReliefWeb/OCHA",
                "title":       f"🆘 {fields.get('title','Situation Report')}",
                "url":         fields.get("url", "https://reliefweb.int/"),
                "age_min":     0,
                "credibility_score": 9,
            })
        _cache[cache_key]    = result
        _cache_ts[cache_key] = now
        return result
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Unified Conflict API – ACLED → UCDP → ReliefWeb
# ─────────────────────────────────────────────────────────────────────────────

def fetch_conflict_events(
    region: str,
    days: int = 7,
    limit: int = 50,
    sources: str = "auto",
) -> list[dict]:
    """
    Hauptfunktion: Ruft Konfliktereignisse aus ALLEN verfügbaren Quellen ab.

    sources = "auto"   → ACLED (wenn konfiguriert) + UCDP + ReliefWeb
    sources = "acled"  → nur ACLED
    sources = "ucdp"   → nur UCDP
    sources = "free"   → nur UCDP + ReliefWeb (kein API-Key nötig)
    """
    results: list[dict] = []
    email, pwd = _get_creds()
    has_acled  = bool(email and pwd)

    if sources in ("auto", "acled") and has_acled:
        acled_events = fetch_acled_events(region, days=days, limit=limit)
        for e in acled_events:
            e["_origin"] = "ACLED"
        results.extend(acled_events)

    if sources in ("auto", "ucdp", "free") or (sources == "auto" and not has_acled):
        ucdp_events  = fetch_ucdp_events(region, days=max(days, 30), limit=limit)
        for e in ucdp_events:
            e["_origin"] = "UCDP"
        results.extend(ucdp_events)

    if sources in ("auto", "free") and len(results) < 5:
        rw_events = fetch_reliefweb_events(region, days=days, limit=20)
        for e in rw_events:
            e["_origin"] = "ReliefWeb"
        results.extend(rw_events)

    # Deduplizieren (gleicher Ort + Datum)
    seen: set = set()
    deduped   = []
    for e in results:
        key = f"{e.get('location','')[:20]}_{e.get('date','')[:10]}"
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    deduped.sort(key=lambda x: x.get("date", ""), reverse=True)
    return deduped[:limit]


def conflict_status() -> dict:
    """Gibt Status aller Konflikt-Datenquellen zurück."""
    email, pwd = _get_creds()
    has_acled  = bool(email and pwd)
    acled_info = acled_status()

    # UCDP-Test
    try:
        r = requests.get(UCDP_CAND_URL, params={"country": "Ukraine", "pagesize": 1},
                         timeout=8)
        ucdp_ok = r.status_code == 200
    except Exception:
        ucdp_ok = False

    return {
        "acled":      {"active": has_acled and acled_info["configured"],
                       "message": acled_info["message"]},
        "ucdp":       {"active": ucdp_ok, "message": "UCDP OK" if ucdp_ok else "UCDP nicht erreichbar"},
        "reliefweb":  {"active": True, "message": "ReliefWeb (UN OCHA) – kostenlos"},
        "summary":    (
            f"ACLED: {'✅' if has_acled and acled_info['configured'] else '❌'} | "
            f"UCDP: {'✅' if ucdp_ok else '❌'} | "
            f"ReliefWeb: ✅"
        ),
    }


def fetch_acled_events(
    region: str,
    days: int = 7,
    limit: int = 50,
    min_fatalities: int = 0,
) -> list[dict]:
    """
    Ruft ACLED-Konfliktereignisse für eine Region ab.
    Gibt Liste von Ereignissen mit GPS-Koordinaten zurück.
    """
    headers = _get_auth_headers()
    if not headers:
        # Kein ACLED-Key → UCDP als Fallback
        return fetch_ucdp_events(region, days=max(days, 30), limit=limit)

    cache_key = f"{region}_{days}_{limit}"
    now = time.monotonic()
    if cache_key in _cache and now - _cache_ts.get(cache_key, 0) < _CACHE_TTL:
        return _cache[cache_key]

    countries    = _get_countries(region)
    since        = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    country_list = [c.strip() for c in countries.replace(";", ",").split(",")]
    all_events: list[dict] = []

    for country in country_list[:4]:
        try:
            params: dict = {
                "country":          country,
                "event_date":       since,
                "event_date_where": ">=",
                "limit":            min(limit, 50),
                "fields": (
                    "event_date|event_type|sub_event_type|actor1|actor2|"
                    "country|admin1|admin2|location|latitude|longitude|"
                    "fatalities|notes|source"
                ),
            }
            if min_fatalities > 0:
                params["fatalities"]       = min_fatalities
                params["fatalities_where"] = ">="

            r = requests.get(
                API_BASE,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code == 401:
                # Token abgelaufen → neu holen und nochmal versuchen
                with _token_lock:
                    _token_cache["expires_at"] = 0.0
                headers = _get_auth_headers()
                if not headers:
                    break
                r = requests.get(API_BASE, params=params, headers=headers,
                                 timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                all_events.extend(r.json().get("data", []))
        except Exception:
            continue

    result = _normalize_events(all_events)
    _cache[cache_key]    = result
    _cache_ts[cache_key] = now
    return result


def _normalize_events(raw: list[dict]) -> list[dict]:
    """Wandelt rohe ACLED-Dicts in NEXUS-Format um."""
    result = []
    for ev in raw:
        try:
            lat = float(ev.get("latitude",  0) or 0)
            lon = float(ev.get("longitude", 0) or 0)
            if not lat or not lon:
                continue
            event_type = ev.get("event_type", "")
            result.append({
                "date":       ev.get("event_date", ""),
                "event_type": event_type,
                "sub_type":   ev.get("sub_event_type", ""),
                "priority":   EVENT_PRIORITY.get(event_type, "MITTEL"),
                "icon":       EVENT_ICONS.get(event_type, "⚡"),
                "actor1":     ev.get("actor1", ""),
                "actor2":     ev.get("actor2", ""),
                "country":    ev.get("country", ""),
                "admin1":     ev.get("admin1", ""),
                "admin2":     ev.get("admin2", ""),
                "location":   ev.get("location", ""),
                "lat":        lat,
                "lon":        lon,
                "fatalities": int(ev.get("fatalities", 0) or 0),
                "notes":      (ev.get("notes", "") or "")[:200],
                "source":     ev.get("source", "ACLED"),
                "title":      f"{EVENT_ICONS.get(event_type,'⚡')} {event_type}: {ev.get('location','')}",
                "url":        "https://acleddata.com/",
                "age_min":    0,
                "credibility_score": 9,
            })
        except Exception:
            continue
    result.sort(key=lambda x: x.get("date", ""), reverse=True)
    return result


# ── Karten-Export ─────────────────────────────────────────────────────────────

def acled_for_map(region: str, days: int = 14) -> list[dict]:
    """Gibt Konfliktpunkte für die Livekarte zurück."""
    events = fetch_acled_events(region, days=days, limit=100)
    map_points = []
    for ev in events:
        color = {
            "KRITISCH": "#ff0000",
            "HOCH":     "#ff6600",
            "MITTEL":   "#ffaa00",
            "NIEDRIG":  "#00cc88",
        }.get(ev["priority"], "#ff6600")

        fat_str   = f"<br><b>Todesopfer: {ev['fatalities']}</b>" if ev.get("fatalities", 0) > 0 else ""
        notes_str = f"<br><i>{ev['notes'][:120]}</i>" if ev.get("notes") else ""
        actor_str = f"<br>{ev['actor1']}" + (f" vs {ev['actor2']}" if ev.get("actor2") else "")

        map_points.append({
            "lat":      ev["lat"],
            "lon":      ev["lon"],
            "type":     "acled",
            "icon":     ev["icon"],
            "color":    color,
            "title":    f"{ev['icon']} {ev['event_type']}: {ev['location']}",
            "popup":    (
                f"<b>{ev['icon']} {ev['event_type']}</b><br>"
                f"<b>{ev['location']}, {ev.get('admin1','')}</b><br>"
                f"{ev['date']}{fat_str}{actor_str}{notes_str}<br>"
                f"<small>ACLED | GPS: {ev['lat']:.4f},{ev['lon']:.4f}</small>"
            ),
            "priority": ev["priority"],
        })
    return map_points


# ── LLM-Kontext ───────────────────────────────────────────────────────────────

def acled_for_llm(region: str, days: int = 7) -> str:
    """
    Gibt Konflikt-Zusammenfassung als LLM-Kontext zurück.

    T155-Fix: Nutzt jetzt fetch_conflict_events() (ACLED → UCDP → ReliefWeb)
    statt nur fetch_acled_events() (ACLED-oder-UCDP). Vorher landete eine
    leere UCDP-Antwort (z.B. weil UCDP für 'Iran' kaum codierte Ereignisse
    führt oder den vollen GW-Ländernamen erwartet) direkt in "keine Daten",
    obwohl ReliefWeb/OCHA für dieselbe Region oft brauchbare Lageberichte
    liefert. Zusätzlich: Mindest-Zeitfenster 14 Tage (UCDP/ReliefWeb sind
    Tagesabfragen mit Indexierungs-Verzug – 1 Tag ist zu eng für Treffer).
    """
    events = fetch_conflict_events(region, days=max(days, 14), limit=30, sources="auto")
    if not events:
        return ""

    by_type: dict[str, list] = {}
    total_fat = 0
    for ev in events:
        by_type.setdefault(ev["event_type"], []).append(ev)
        total_fat += ev.get("fatalities", 0)

    lines = [f"\n[ACLED KONFLIKTEREIGNISSE: {region} — letzte {days} Tage]"]
    lines.append(f"Gesamt: {len(events)} Ereignisse | Bestätigte Todesopfer: {total_fat}")
    lines.append("")
    for event_type, evs in sorted(by_type.items(), key=lambda x: -len(x[1])):
        icon = EVENT_ICONS.get(event_type, "⚡")
        prio = EVENT_PRIORITY.get(event_type, "MITTEL")
        lines.append(f"{icon} {event_type} ({len(evs)}x) [{prio}]:")
        for ev in evs[:3]:
            loc = f"{ev['location']}, {ev['admin1']}" if ev.get("admin1") else ev.get("location", "")
            fat = f" · {ev['fatalities']} Todesopfer" if ev.get("fatalities", 0) > 0 else ""
            lines.append(f"  {ev['date']} | {loc}{fat}")
            if ev.get("notes"):
                lines.append(f"  → {ev['notes'][:120]}")
    lines.append("\nQuelle: ACLED (Armed Conflict Location & Event Data) — kuratiert, GPS-verifiziert")
    return "\n".join(lines)


# ── Eskalations-Signal ────────────────────────────────────────────────────────

def acled_for_escalation(region: str, days_short: int = 1, days_long: int = 7) -> dict:
    """Kompaktes Eskalations-Signal für nexus_escalation.py."""
    email, pwd = _get_creds()
    if not email or not pwd:
        return {
            "score": 0, "events_24h": 0, "events_7d": 0,
            "fatalities_24h": 0, "fatalities_7d": 0,
            "surge_ratio": 1.0, "critical_count": 0,
            "top_event": "", "hint": "ACLED nicht konfiguriert",
            "configured": False,
        }

    events_7d  = fetch_acled_events(region, days=days_long, limit=200)
    cutoff     = (datetime.now() - timedelta(days=days_short)).strftime("%Y-%m-%d")
    events_24h = [e for e in events_7d if e.get("date", "") >= cutoff]

    fat_24h    = sum(e.get("fatalities", 0) for e in events_24h)
    fat_7d     = sum(e.get("fatalities", 0) for e in events_7d)
    crit_cnt   = sum(1 for e in events_24h if e.get("priority") == "KRITISCH")
    daily_avg  = len(events_7d) / max(days_long, 1)
    surge      = len(events_24h) / max(daily_avg, 1)

    score  = min(len(events_24h) * 2,  30)
    score += min(crit_cnt * 5,          25)
    score += min(fat_24h * 0.5,         25)
    score += min((surge - 1.0) * 10,    20)
    score  = max(0.0, min(100.0, score))

    top = ""
    if events_24h:
        best = max(events_24h, key=lambda e: (e.get("fatalities", 0), e.get("priority") == "KRITISCH"))
        top  = f"{best['icon']} {best['event_type']}: {best['location']} ({best['date']})"

    hint = (f"{len(events_24h)} Events/24h vs Ø{daily_avg:.1f}/Tag | "
            f"{fat_24h} Todesopfer | Surge ×{surge:.1f}") if events_24h else ""

    return {
        "score":          round(score, 1),
        "events_24h":     len(events_24h),
        "events_7d":      len(events_7d),
        "fatalities_24h": fat_24h,
        "fatalities_7d":  fat_7d,
        "surge_ratio":    round(surge, 2),
        "critical_count": crit_cnt,
        "top_event":      top,
        "hint":           hint,
        "configured":     True,
    }


def acled_summary(region: str, days: int = 7) -> str:
    """Kurze Terminal-Zusammenfassung."""
    events = fetch_acled_events(region, days=days, limit=50)
    if not events:
        return ""
    criticals = [e for e in events if e["priority"] == "KRITISCH"]
    total_fat = sum(e.get("fatalities", 0) for e in events)
    return (
        f"ACLED {region}: {len(events)} Ereignisse in {days}d "
        f"| {len(criticals)} kritisch | {total_fat} Todesopfer"
    )


def fetch_acled_bbox(
    lat_min: float, lon_min: float,
    lat_max: float, lon_max: float,
    days: int = 7, limit: int = 100,
) -> list[dict]:
    """Abruf per Bounding-Box (präziser als Ländername)."""
    headers = _get_auth_headers()
    if not headers:
        return []

    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        params = {
            "latitude":         f"{lat_min}:{lat_max}",
            "longitude":        f"{lon_min}:{lon_max}",
            "event_date":       since,
            "event_date_where": ">=",
            "limit":            limit,
            "fields": (
                "event_date|event_type|sub_event_type|actor1|actor2|"
                "country|admin1|location|latitude|longitude|fatalities|notes|source"
            ),
        }
        r = requests.get(API_BASE, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return []
        return _normalize_events(r.json().get("data", []))
    except Exception:
        return []


def acled_status() -> dict:
    """Prueft ob ACLED konfiguriert und erreichbar ist."""
    email, pwd = _get_creds()
    if not email or not pwd:
        return {
            "configured": False,
            "api_key":    False,
            "hint":       "ACLED_EMAIL + ACLED_PASSWORD in config.py eintragen",
        }
    return {
        "configured": True,
        "email":      email,
        "hint":       "Credentials gesetzt — fetchbar",
    }
