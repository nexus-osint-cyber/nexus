"""
NEXUS - Generisches Resilienz-Toolkit für externe Datenquellen (T175)

Formalisiert das Muster, das sich bei der Reparatur toter/instabiler Quellen
mehrfach unabhängig voneinander bewährt hat (Reddit T157, GDELT T173,
ACLED/UCDP T102/T155, Flugdaten T156) — anstatt es jedes Mal neu ad-hoc zu
implementieren, stellt dieses Modul die Bausteine als wiederverwendbare
Helfer bereit, die jedes nexus_*.py-Datenmodul importieren kann.

Das Muster (aus den o.g. Fixes destilliert) besteht aus vier Säulen:

  1. REALISTISCHER BROWSER-USER-AGENT
     Viele kostenlose APIs (Reddit, GDELT, ...) drosseln oder leeren ihre
     Antworten bei Standard-"python-requests"-User-Agents. Ein normaler
     Chrome-UA behebt das in der Praxis zuverlässig.
     → BROWSER_HEADERS

  2. MULTI-STRATEGIE-KETTE (mehrere unabhängige Wege zum selben Ziel)
     Reddit: erst RSS-Feed, dann JSON-API als Fallback.
     Flugdaten: erst ADS-B Exchange (zeigt Militärflüge), dann OpenSky.
     GDELT: erst angefragtes Zeitfenster, dann deutlich breiteres Fenster.
     → try_strategies()

  3. RETRY MIT EXPONENTIELLEM BACKOFF bei 429/503
     Alle reparierten Quellen behandeln Rate-Limits (429) und temporäre
     Serverfehler (503) mit steigenden Wartezeiten statt sofort aufzugeben.
     → retry_request()

  4. IN-MEMORY TTL-CACHE
     Reduziert Last auf die Quelle, glättet kurzzeitige Ausfälle (ein Reload
     während eines kurzen Hänger der API liefert trotzdem die letzten
     funktionierenden Daten) und ist die Grundlage für Uptime-Messung.
     → TTLCache

Verwendung (Minimalbeispiel für ein neues/instabiles Modul):

    from nexus_resilience import BROWSER_HEADERS, TTLCache, try_strategies, retry_request

    _cache = TTLCache(ttl_seconds=300)

    def get_data(query: str) -> list[dict]:
        cached = _cache.get(query)
        if cached is not None:
            return cached

        def _primary():
            r = retry_request("GET", PRIMARY_URL, headers=BROWSER_HEADERS, params={...})
            return _parse_primary(r) if r is not None else []

        def _fallback():
            r = retry_request("GET", FALLBACK_URL, headers=BROWSER_HEADERS, params={...})
            return _parse_fallback(r) if r is not None else []

        result = try_strategies([("primär", _primary), ("fallback", _fallback)],
                                label="MeineQuelle")
        _cache.set(query, result)
        return result

T175 = eines der drei vom Nutzer ausdrücklich genehmigten Verbesserungen
("automatische Fallback-Strategien für tote Quellen nach Reddit-Vorbild").
"""

from __future__ import annotations

import sys
import time
from typing import Callable, Optional, TypeVar

import requests

T = TypeVar("T")

# ── Säule 1: Realistischer Browser-User-Agent ─────────────────────────────────
# Identisch zu dem UA, der das Reddit-403- und das GDELT-Leerantwort-Problem
# behoben hat (siehe nexus_reddit.py _HEADERS / nexus_gdelt.py _HEADERS).
BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
}


# ── Säule 4: Generischer In-Memory TTL-Cache ──────────────────────────────────
class TTLCache:
    """
    Simpler thread-naiver In-Memory-Cache mit Ablaufzeit.
    Jedes Modul bekommt seine eigene Instanz (kein globaler Shared-State,
    damit unterschiedliche TTLs/Module sich nicht gegenseitig stören).

    Beispiel:
        _cache = TTLCache(ttl_seconds=300)
        hit = _cache.get("ukraine_48h")
        if hit is None:
            hit = _cache.set("ukraine_48h", teure_abfrage())
    """

    def __init__(self, ttl_seconds: float = 300.0):
        self.ttl = ttl_seconds
        self._store: dict[str, T] = {}
        self._ts: dict[str, float] = {}

    def get(self, key: str) -> Optional[T]:
        if key not in self._store:
            return None
        if time.monotonic() - self._ts.get(key, 0.0) >= self.ttl:
            return None
        return self._store[key]

    def set(self, key: str, value: T) -> T:
        self._store[key] = value
        self._ts[key] = time.monotonic()
        return value

    def invalidate(self, key: Optional[str] = None) -> None:
        """Ohne key: kompletten Cache leeren. Mit key: nur diesen Eintrag."""
        if key is None:
            self._store.clear()
            self._ts.clear()
        else:
            self._store.pop(key, None)
            self._ts.pop(key, None)

    def age_seconds(self, key: str) -> Optional[float]:
        if key not in self._ts:
            return None
        return time.monotonic() - self._ts[key]


# ── Säule 3: Retry mit exponentiellem Backoff ─────────────────────────────────
def retry_request(method: str, url: str, *,
                   headers: Optional[dict] = None,
                   params: Optional[dict] = None,
                   json_body: Optional[dict] = None,
                   timeout: float = 15.0,
                   max_attempts: int = 3,
                   retry_statuses: tuple[int, ...] = (429, 503),
                   base_delay: float = 2.0,
                   label: str = "Quelle",
                   stream: bool = False) -> Optional[requests.Response]:
    """
    HTTP-Request mit automatischem Retry + exponentiellem Backoff bei
    Rate-Limits (429) und temporären Server-Fehlern (503) — das Muster, das
    Reddit (T157) und GDELT (T173) zuverlässig gemacht hat.

    Backoff-Formel: base_delay * 2^Versuch  (z.B. bei base_delay=2: 2s, 4s, 8s)

    Gibt das Response-Objekt zurück (Status kann trotzdem != 200 sein —
    der Aufrufer prüft/parsed selbst), oder None bei Timeout/Verbindungsfehler
    nach allen Versuchen.
    """
    headers = headers or BROWSER_HEADERS
    last_exc: Optional[Exception] = None

    for attempt in range(max_attempts):
        try:
            r = requests.request(method, url, headers=headers, params=params,
                                 json=json_body, timeout=timeout, stream=stream)

            if r.status_code in retry_statuses and attempt < max_attempts - 1:
                wait = base_delay * (2 ** attempt)
                print(f"[{label}] HTTP {r.status_code} – Backoff {wait:.0f}s "
                      f"(Versuch {attempt + 1}/{max_attempts})", file=sys.stderr)
                time.sleep(wait)
                continue

            return r

        except requests.Timeout as exc:
            last_exc = exc
            print(f"[{label}] Timeout (Versuch {attempt + 1}/{max_attempts})", file=sys.stderr)
            if attempt < max_attempts - 1:
                time.sleep(base_delay)
            continue
        except requests.RequestException as exc:
            last_exc = exc
            print(f"[{label}] Verbindungsfehler: {exc} "
                  f"(Versuch {attempt + 1}/{max_attempts})", file=sys.stderr)
            if attempt < max_attempts - 1:
                time.sleep(base_delay)
            continue

    if last_exc is not None:
        print(f"[{label}] Aufgegeben nach {max_attempts} Versuchen: {last_exc}", file=sys.stderr)
    return None


# ── Säule 2: Multi-Strategie-Kette ────────────────────────────────────────────
def try_strategies(strategies: list[tuple[str, Callable[[], list]]], *,
                    label: str = "Quelle",
                    is_success: Optional[Callable[[list], bool]] = None) -> list:
    """
    Probiert mehrere unabhängige Beschaffungswege der Reihe nach durch und
    liefert das Ergebnis des ERSTEN, der etwas Verwertbares zurückgibt —
    exakt das Muster aus nexus_reddit.fetch_subreddit() (RSS → JSON-API)
    und nexus_flights.get_flights() (ADS-B Exchange → OpenSky).

    strategies: Liste von (Name, Callable[[], list]) — jede Funktion soll
                bei Erfolg eine nicht-leere Liste, bei Misserfolg [] liefern
                und selbst alle Exceptions abfangen (sonst bricht die Kette ab).
    is_success: optionale Custom-Prüfung "war das Ergebnis brauchbar?"
                (Default: nicht-leere Liste = Erfolg).

    Loggt, welche Strategie gegriffen hat (oder dass alle fehlschlugen) —
    das macht spätere Diagnose ("warum liefert Quelle X nichts?") deutlich
    einfacher als stille Fallback-Ketten ohne Logging.
    """
    success = is_success or (lambda r: bool(r))

    for i, (name, fn) in enumerate(strategies, start=1):
        try:
            result = fn()
        except Exception as exc:
            print(f"[{label}] Strategie {i}/{len(strategies)} ('{name}') "
                  f"warf Exception: {exc}", file=sys.stderr)
            continue

        if success(result):
            if i > 1:
                print(f"[{label}] Strategie {i}/{len(strategies)} ('{name}') "
                      f"erfolgreich (vorherige lieferten nichts)", file=sys.stderr)
            return result

        print(f"[{label}] Strategie {i}/{len(strategies)} ('{name}') "
              f"lieferte nichts Verwertbares – versuche nächste", file=sys.stderr)

    print(f"[{label}] Alle {len(strategies)} Strategien lieferten nichts Verwertbares", file=sys.stderr)
    return []


# ── Direktaufruf zum Selbsttest ───────────────────────────────────────────────
if __name__ == "__main__":
    print("NEXUS Resilienz-Toolkit (T175) – Selbsttest\n")

    cache = TTLCache(ttl_seconds=2)
    cache.set("x", [1, 2, 3])
    print("Cache-Hit direkt danach:", cache.get("x"))
    time.sleep(2.1)
    print("Cache-Hit nach Ablauf (sollte None sein):", cache.get("x"))

    def _fail():
        return []

    def _ok():
        return ["ergebnis"]

    out = try_strategies([("erste (schlägt fehl)", _fail),
                          ("zweite (klappt)", _ok)],
                         label="Selbsttest")
    print("\ntry_strategies-Ergebnis:", out)

    print("\nBROWSER_HEADERS UA:", BROWSER_HEADERS["User-Agent"][:50], "...")
    print("\nSelbsttest abgeschlossen.")
