#!/usr/bin/env python3
"""
nexus_longtest.py — NEXUS 24h Langzeit-Validierungstest
========================================================
Läuft alle 45 Minuten, sammelt Daten zur Zielregion,
erzeugt am Ende einen HTML-Vergleichsbericht.

Start:  python nexus_longtest.py
Bericht jederzeit: python nexus_longtest.py --bericht
"""

import json
import os
import sys
import time
import threading
import ctypes
import argparse
import socket
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone

MODULE_TIMEOUT = 45  # Sekunden max pro Modul

# ─── Konfiguration ────────────────────────────────────────────────────────────
ZIEL              = "Iran"
INTERVALL_MIN     = 30          # Minuten zwischen Läufen (T162: 30min für 12h-Test)
DAUER_STUNDEN     = 12          # Gesamtlaufzeit (T162: 12h Validierungstest)
BASIS_DIR         = os.path.dirname(os.path.abspath(__file__))
DATEN_DIR         = os.path.join(BASIS_DIR, "nexus_longtest_daten")
LOG_DATEI         = os.path.join(DATEN_DIR, "nexus_longtest.log")
BERICHT_DATEI     = os.path.join(DATEN_DIR, "nexus_longtest_bericht.html")

# ─── Hilfsfunktionen ──────────────────────────────────────────────────────────
def _ts() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _ts_datei() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")

def _ts_anzeige() -> str:
    return datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

def log(msg: str):
    os.makedirs(DATEN_DIR, exist_ok=True)
    zeile = f"[{_ts_anzeige()}] {msg}"
    print(zeile, flush=True)
    with open(LOG_DATEI, "a", encoding="utf-8") as f:
        f.write(zeile + "\n")

def verhindere_schlafmodus():
    """Verhindert Windows-Schlafmodus während des Tests."""
    try:
        ES_CONTINUOUS       = 0x80000000
        ES_SYSTEM_REQUIRED  = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        )
        log("✓ Windows-Schlafmodus deaktiviert (läuft durch Sperrbildschirm)")
    except Exception as e:
        log(f"⚠ Schlafmodus-Sperre nicht möglich: {e} — bitte manuell deaktivieren")

def aktiviere_schlafmodus():
    """Stellt Windows-Schlafmodus nach Test wieder her."""
    try:
        ES_CONTINUOUS = 0x80000000
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        log("✓ Windows-Schlafmodus wieder aktiviert")
    except Exception:
        pass

# ─── Datensammlung ────────────────────────────────────────────────────────────
def sammle_flights(ziel: str) -> dict:
    try:
        from nexus_flights import get_flights  # type: ignore
        ergebnis = get_flights(ziel)
        if not ergebnis:
            return {"status": "keine_daten", "count": 0}
        flugzeuge = ergebnis.get("aircraft", []) if isinstance(ergebnis, dict) else ergebnis
        if not flugzeuge:
            return {"status": "keine_daten", "count": 0}
        auffaellig = [f for f in flugzeuge
                      if f.get("isr") or f.get("military") or f.get("suspicious")]
        return {
            "status": "ok",
            "count": len(flugzeuge),
            "auffaellig": len(auffaellig),
            "details": auffaellig[:5]
        }
    except Exception as e:
        return {"status": "fehler", "fehler": str(e)}

def sammle_gdelt(ziel: str) -> dict:
    try:
        from nexus_gdelt import fetch_gdelt_articles  # type: ignore
        artikel = fetch_gdelt_articles(ziel, hours=6, max_records=20)
        if not artikel:
            return {"status": "keine_daten", "count": 0}
        return {
            "status": "ok",
            "count": len(artikel),
            "headlines": [a.get("title", "")[:100] for a in artikel[:5]]
        }
    except Exception as e:
        return {"status": "fehler", "fehler": str(e)}

def sammle_rss(ziel: str) -> dict:
    try:
        from nexus_rss import fetch_news  # type: ignore
        artikel = fetch_news(keyword_filter=ziel, max_total=20)
        if not artikel:
            return {"status": "keine_daten", "count": 0}
        return {
            "status": "ok",
            "count": len(artikel),
            "quellen": list({a.get("source", "?") for a in artikel})[:5],
            "headlines": [a.get("title", "")[:100] for a in artikel[:5]]
        }
    except Exception as e:
        return {"status": "fehler", "fehler": str(e)}

def sammle_seismik(ziel: str) -> dict:
    try:
        from nexus_seismic import get_earthquakes_for_region, get_detonation_candidates  # type: ignore
        ereignisse = get_earthquakes_for_region(ziel, hours=6, include_small=True)
        # get_detonation_candidates erwartet Region-String, nicht Event-Liste
        verdaechtig = get_detonation_candidates(ziel, hours=6) if ereignisse else []
        return {
            "status": "ok",
            "count": len(ereignisse or []),
            "verdaechtig": len(verdaechtig or []),
            "_kandidaten": verdaechtig or [],   # für Eskalations-Bridge
        }
    except Exception as e:
        return {"status": "fehler", "fehler": str(e)}

def sammle_gpsjam(ziel: str) -> dict:
    try:
        from nexus_gpsjam import check_gps_jamming  # type: ignore
        result = check_gps_jamming(ziel)
        if not result:
            return {"status": "keine_daten"}
        return {
            "status":    "ok",
            "jam_aktiv": result.get("jam_active", False),
            "intensity": result.get("intensity", "NONE"),
            "confidence": result.get("confidence", "low"),
            "details":   result,
        }
    except Exception as e:
        return {"status": "fehler", "fehler": str(e)}

def sammle_firms(ziel: str) -> dict:
    try:
        import config  # type: ignore
        from nexus_firms import fetch_firms_fires  # type: ignore
        key = getattr(config, "FIRMS_MAP_KEY", "")
        if not key:
            return {"status": "kein_key", "count": 0}
        feuer = fetch_firms_fires(ziel, days=1, map_key=key)
        if not feuer:
            return {"status": "keine_daten", "count": 0}
        intensiv = [f for f in feuer if f.get("frp", 0) > 100]
        return {
            "status":       "ok",
            "count":        len(feuer),
            "intensiv":     len(intensiv),
            "top_frp":      feuer[0].get("frp", 0) if feuer else 0,
            "top_koordinaten": {"lat": feuer[0]["lat"], "lon": feuer[0]["lon"]} if feuer else {},
        }
    except Exception as e:
        return {"status": "fehler", "fehler": str(e)}

def sammle_viirs(ziel: str) -> dict:
    try:
        from nexus_viirs import check_darkness  # type: ignore
        result = check_darkness(ziel)
        if not result or result.get("status") == "unknown_region":
            return {"status": "unbekannte_region", "region": ziel}
        return {
            "status":         "ok",
            "alert":          result.get("alert", False),
            "score":          result.get("score", 0),
            "baseline":       result.get("baseline_score", 0),
            "drop_pct":       result.get("drop_pct", 0),
            "viirs_status":   result.get("status", "?"),
        }
    except Exception as e:
        return {"status": "fehler", "fehler": str(e)}

def sammle_lightning(ziel: str) -> dict:
    try:
        from nexus_lightning import analyze_lightning  # type: ignore
        result = analyze_lightning(ziel)
        if not result or result.get("error"):
            return {"status": "fehler", "fehler": result.get("error", "keine Daten") if result else "keine Daten"}
        return {
            "status":           "ok",
            "artillery_signal": result.get("artillery_signal", False),
            "confidence":       result.get("confidence", "none"),
            "lightning_count":  result.get("lightning_count", 0),
            "conflict_zone":    result.get("conflict_zone"),
            "signal_hint":      result.get("signal_hint", ""),
            "source":           result.get("source", "?"),
        }
    except Exception as e:
        return {"status": "fehler", "fehler": str(e)}

def sammle_acled(ziel: str) -> dict:
    try:
        from nexus_acled import acled_for_llm  # type: ignore
        text = acled_for_llm(ziel, days=1)
        if not text or "keine" in text.lower():
            return {"status": "keine_daten", "count": 0}
        return {
            "status": "ok",
            "count": text.count("\n"),
            "zusammenfassung": text[:300]
        }
    except Exception as e:
        return {"status": "fehler", "fehler": str(e)}

def sammle_telegram(ziel: str) -> dict:
    # T195: Telethon MTProto zuerst (echter API-Zugang, kein Scraping-Limit)
    try:
        from nexus_telethon import sammle_telegram_mtproto, _TELETHON_OK  # type: ignore
        if _TELETHON_OK:
            result = sammle_telegram_mtproto(region=ziel, stunden=6)
            if result.get("status") == "ok" and result.get("count", 0) > 0:
                return result
    except Exception:
        pass
    # Fallback: t.me Web-Scraping
    try:
        from nexus_telegram import fetch_osint_channels  # type: ignore
        posts = fetch_osint_channels(keyword_filter=ziel, limit_per_channel=5, max_channels=4)
        if not posts:
            return {"status": "keine_daten", "count": 0}
        return {
            "status": "ok",
            "count":  len(posts),
            "quelle": "scraping",
            "headlines": [p.get("title", "")[:100] for p in posts[:3]],
        }
    except Exception as e:
        return {"status": "fehler", "fehler": str(e)}

def sammle_reddit(ziel: str) -> dict:
    try:
        from nexus_reddit import fetch_osint_reddit  # type: ignore
        posts = fetch_osint_reddit(keyword_filter=ziel, limit_per_sub=10, max_subs=3)
        if not posts:
            return {"status": "keine_daten", "count": 0}
        return {
            "status": "ok",
            "count": len(posts),
            "headlines": [p.get("title", "")[:100] for p in posts[:5]]
        }
    except Exception as e:
        return {"status": "fehler", "fehler": str(e)}

def sammle_wirtschaft() -> dict:
    try:
        from nexus_economics import get_economic_indicators  # type: ignore
        daten = get_economic_indicators()
        if not daten:
            return {"status": "keine_daten"}
        return {
            "status": "ok",
            "oel_wti":   daten.get("oil_wti",   {}).get("price") if isinstance(daten.get("oil_wti"),   dict) else daten.get("oil_wti"),
            "oel_brent": daten.get("oil_brent", {}).get("price") if isinstance(daten.get("oil_brent"), dict) else daten.get("oil_brent"),
            "gold":      daten.get("gold",      {}).get("price") if isinstance(daten.get("gold"),      dict) else daten.get("gold"),
            "vix":       daten.get("vix",       {}).get("price") if isinstance(daten.get("vix"),       dict) else daten.get("vix"),
            "stress":    daten.get("market_stress", "?")
        }
    except Exception as e:
        return {"status": "fehler", "fehler": str(e)}

def sammle_eskalation(ziel: str, quellen: dict) -> dict:
    """Berechnet Eskalations-Score aus den bereits gesammelten Quellen-Daten."""
    try:
        from nexus_escalation import compute_escalation  # type: ignore

        # Bridge: unsere kompakten Quellen-Dicts → live_data-Format für compute_escalation
        live_data: dict = {}

        # Flights → aircraft-Liste mit is_isr-Flag
        fl = quellen.get("flights", {})
        if fl.get("status") == "ok":
            aircraft = []
            for f in fl.get("details", []):
                susp = str(f.get("suspicious", "")).lower()
                is_isr = bool(
                    f.get("isr") or f.get("military") or
                    any(k in susp for k in ("aufkl", "isr", "drohn", "awacs", "rc-135", "p-8"))
                )
                aircraft.append({
                    **f,
                    "is_isr":   is_isr,
                    "isr_conf": "medium" if is_isr else "low",
                    "isr_type": f.get("ac_type", "UNKNOWN"),
                    "isr_role": "SURVEILLANCE" if is_isr else "",
                })
            live_data["flights"] = {"aircraft": aircraft}

        # Seismik → Detonations-Kandidaten
        seis = quellen.get("seismik", {})
        if seis.get("status") == "ok" and seis.get("_kandidaten"):
            live_data["earthquakes"] = seis["_kandidaten"]

        # Wirtschaft → market_stress
        wirt = quellen.get("wirtschaft", {})
        if wirt.get("status") == "ok":
            stress = wirt.get("stress", "NORMAL")
            live_data["economics"] = {
                "market_stress": stress,
                "oil_wti":   wirt.get("oel_wti"),
                "oil_brent": wirt.get("oel_brent"),
            }

        # ACLED → priority-Liste (falls Text vorhanden, synthetisch)
        acl = quellen.get("acled", {})
        if acl.get("status") == "ok" and acl.get("count", 0) > 0:
            live_data["acled"] = [{"priority": "HOCH"}] * min(acl["count"], 5)

        # RSS Keyword-Extraktion (T193: sync mit _ATTACK_KW/_MED_KW aus nexus_escalation)
        rss = quellen.get("rss", {})
        if rss.get("status") == "ok" and rss.get("headlines"):
            try:
                from nexus_escalation import _ATTACK_KW, _MED_KW  # type: ignore
                _search_kws = _ATTACK_KW | _MED_KW
            except ImportError:
                # Fallback falls Import nicht klappt
                _search_kws = {
                    "attack", "strike", "missile", "bombing", "rockets", "explosion",
                    "airstrike", "air strike", "shelling", "killed", "casualties",
                    "war", "invasion", "offensive", "drone attack", "angriff",
                    "rakete", "beschuss", "escalation", "military", "crisis",
                }
            # Zusätzlich: Compound-Phrasen die als ganzes matchen müssen
            _compound_phrases = [
                "air strike", "drone attack", "rocket attack", "missile strike",
                "ground invasion", "ceasefire violation", "nuclear facility",
                "ballistic missile", "cruise missile", "precision strike",
            ]
            found_kws: list[str] = []
            all_text = " ".join(rss.get("headlines") or []).lower()
            # 1. Compound-Phrasen zuerst (höhere Spezifität)
            for phrase in _compound_phrases:
                if phrase in all_text and phrase not in found_kws:
                    found_kws.append(phrase)
            # 2. Einzelwörter aus _ATTACK_KW / _MED_KW
            for kw in _search_kws:
                if kw in all_text and kw not in found_kws:
                    found_kws.append(kw)
            if found_kws:
                live_data["rss_keywords"] = found_kws

        # GPS-Jammer → gpsjam_zones
        gps = quellen.get("gpsjam", {})
        if gps.get("status") == "ok" and gps.get("jam_aktiv"):
            live_data["gpsjam_zones"] = [{
                "intensity": gps.get("intensity", "LOW"),
                "confidence": gps.get("confidence", "low"),
            }]

        # FIRMS Feuer → fire_signals
        firms = quellen.get("firms", {})
        if firms.get("status") == "ok" and firms.get("count", 0) > 0:
            live_data["fire_signals"] = [{
                "count":   firms.get("count", 0),
                "intensiv": firms.get("intensiv", 0),
                "top_frp": firms.get("top_frp", 0),
            }]

        # VIIRS Verdunkelung → infrastructure_dark
        viirs = quellen.get("viirs", {})
        if viirs.get("status") == "ok" and viirs.get("alert"):
            live_data["infrastructure_dark"] = [{
                "drop_pct": viirs.get("drop_pct", 0),
                "region":   ziel,
            }]

        # Blitzortung → artillery_signal (lightning_signals)
        blitz = quellen.get("lightning", {})
        if blitz.get("status") == "ok" and blitz.get("artillery_signal"):
            live_data["lightning_signals"] = [{
                "confidence": blitz.get("confidence", "low"),
                "count":      blitz.get("lightning_count", 0),
                "hint":       blitz.get("signal_hint", ""),
            }]

        # Telegram Surge → telegram_surges
        tg = quellen.get("telegram", {})
        if tg.get("status") == "ok":
            esk_tg = tg.get("eskalation", {})
            if esk_tg.get("surge_active") or esk_tg.get("top_score", 0) > 2.0:
                live_data["telegram_surges"] = [{
                    "score":   esk_tg.get("top_score", 0),
                    "categories": list(esk_tg.get("categories", {}).keys()),
                }]

        ergebnis = compute_escalation(live_data, region=ziel)
        if isinstance(ergebnis, dict):
            return {
                "status":  "ok",
                "score":   ergebnis.get("score", 0),
                "level":   ergebnis.get("level", "?"),
                "trend":   ergebnis.get("trend", "?"),
                "signale": ergebnis.get("active_signals", []),
                "details": ergebnis.get("signal_details", [])[:4],
            }
        return {"status": "ok", "score": 0, "level": "?"}
    except Exception as e:
        return {"status": "fehler", "fehler": str(e)}

def sammle_suche(ziel: str) -> dict:
    try:
        from nexus_search import historical_search  # type: ignore
        text = historical_search(f"{ziel} aktuell nachrichten")
        if not text:
            return {"status": "keine_daten", "count": 0}
        # Prompt-Header-Zeilen rausfiltern (beginnen mit "[" oder enthalten Meta-Keywords)
        _skip = ("HISTORISCHE SUCHE", "WICHTIG:", "Zeitraum:", "Gib das Datum",
                 "Thema:", "[", "unbegrenzt", "ausdruecklich")
        zeilen = [
            z.strip() for z in text.split("\n")
            if z.strip() and len(z.strip()) > 20
            and not any(z.strip().startswith(s) or s in z for s in _skip)
        ]
        return {
            "status": "ok" if zeilen else "keine_daten",
            "count": len(zeilen),
            "headlines": zeilen[:5]
        }
    except Exception as e:
        return {"status": "fehler", "fehler": str(e)}

# ─── Haupt-Sammelroutine ──────────────────────────────────────────────────────
def ein_lauf(ziel: str, lauf_nr: int, gesamt_laeufe: int) -> dict:
    log(f"━━━ LAUF {lauf_nr}/{gesamt_laeufe} startet — Ziel: {ziel} ━━━")
    ergebnis = {
        "timestamp":  _ts(),
        "lauf_nr":    lauf_nr,
        "ziel":       ziel,
        "quellen":    {}
    }

    # Eskalation läuft ZULETZT — nach allen Datenquellen
    module = [
        # ── Nachrichten & Social Media ────────────────────────────────────
        ("flights",    lambda: sammle_flights(ziel)),
        ("gdelt",      lambda: sammle_gdelt(ziel)),
        ("rss",        lambda: sammle_rss(ziel)),
        ("reddit",     lambda: sammle_reddit(ziel)),
        ("telegram",   lambda: sammle_telegram(ziel)),
        ("suche",      lambda: sammle_suche(ziel)),
        # ── Physikalische Sensoren ────────────────────────────────────────
        ("seismik",    lambda: sammle_seismik(ziel)),
        ("gpsjam",     lambda: sammle_gpsjam(ziel)),
        ("firms",      lambda: sammle_firms(ziel)),
        ("viirs",      lambda: sammle_viirs(ziel)),
        ("lightning",  lambda: sammle_lightning(ziel)),
        # ── Wirtschaft & Konflikt-DB ──────────────────────────────────────
        ("acled",      lambda: sammle_acled(ziel)),
        ("wirtschaft", sammle_wirtschaft),
    ]

    for name, fn in module:
        print(f"  [{lauf_nr}] {name:12s} ...", end=" ", flush=True)
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(fn)
                try:
                    daten = future.result(timeout=MODULE_TIMEOUT)
                except FuturesTimeout:
                    daten = {"status": "timeout", "fehler": f">{MODULE_TIMEOUT}s"}
                    print(f"⏱ TIMEOUT", flush=True)
                    ergebnis["quellen"][name] = daten
                    continue
            ergebnis["quellen"][name] = daten
            status = daten.get("status", "?")
            count  = daten.get("count", "")
            print(f"✓ {status} {('('+str(count)+')') if count else ''}", flush=True)
        except Exception as e:
            ergebnis["quellen"][name] = {"status": "ausnahme", "fehler": str(e)}
            print(f"✗ {e}", flush=True)

    # Eskalations-Score aus gesammelten Daten berechnen
    print(f"  [{lauf_nr}] eskalation  ...", end=" ", flush=True)
    try:
        esk_daten = sammle_eskalation(ziel, ergebnis["quellen"])
        ergebnis["quellen"]["eskalation"] = esk_daten
        score = esk_daten.get("score", 0)
        level = esk_daten.get("level", "?")
        print(f"✓ {esk_daten.get('status','?')} — Score {score}/100 [{level}]", flush=True)
        # Score-Erklärung: Top-Signale anzeigen
        details = esk_daten.get("details", [])
        if details:
            teile = [f"{d.get('icon','·')} {d.get('label','?')[:40]} +{d.get('points',0)}pt"
                     for d in details[:3]]
            print(f"         ↳ " + " · ".join(teile), flush=True)
        # Push-Alert wenn Score hoch genug
        try:
            from nexus_push import monitor_check  # type: ignore
            monitor_check(ziel, score, level, esk_daten.get("signale", []))
        except Exception:
            pass
    except Exception as e:
        ergebnis["quellen"]["eskalation"] = {"status": "ausnahme", "fehler": str(e)}
        print(f"✗ {e}", flush=True)

    # Auto-Verifikation: Top-Headlines gegen Sensordaten prüfen
    rss_headlines = ergebnis["quellen"].get("rss", {}).get("headlines", []) or []
    if rss_headlines and len(rss_headlines) >= 2:
        print(f"  [{lauf_nr}] verifikation ...", end=" ", flush=True)
        try:
            from nexus_verify import verify_headlines  # type: ignore
            verifs = verify_headlines(rss_headlines[:10], ziel, max_claims=2)
            ergebnis["quellen"]["verifikation"] = verifs
            if verifs:
                for v in verifs:
                    print(f"\n         {v['verdict_icon']} {v['verdict']} ({v['confidence']*100:.0f}%): {v['claim'][:60]}", flush=True)
                print(f"  ✓ {len(verifs)} Claim(s) geprüft", flush=True)
            else:
                print("✓ keine operativen Claims", flush=True)
        except Exception as e:
            print(f"✗ {e}", flush=True)

    # Datei speichern
    dateiname = os.path.join(DATEN_DIR, f"lauf_{_ts_datei()}.json")
    with open(dateiname, "w", encoding="utf-8") as f:
        json.dump(ergebnis, f, ensure_ascii=False, indent=2)
    log(f"  → Gespeichert: {os.path.basename(dateiname)}")

    return ergebnis

# ─── HTML-Berichtsgenerator ───────────────────────────────────────────────────
def generiere_bericht() -> str:
    """Liest alle Lauf-JSONs und erzeugt einen HTML-Vergleichsbericht."""
    os.makedirs(DATEN_DIR, exist_ok=True)
    dateien = sorted([
        os.path.join(DATEN_DIR, f)
        for f in os.listdir(DATEN_DIR)
        if f.startswith("lauf_") and f.endswith(".json")
    ])

    if not dateien:
        return "<p>Keine Daten vorhanden.</p>"

    alle_laeufe = []
    for df in dateien:
        try:
            with open(df, encoding="utf-8") as f:
                alle_laeufe.append(json.load(f))
        except Exception:
            pass

    if not alle_laeufe:
        return "<p>Keine gültigen Daten.</p>"

    # Statistiken berechnen
    gesamt_laeufe = len(alle_laeufe)
    start_ts = alle_laeufe[0]["timestamp"]
    end_ts   = alle_laeufe[-1]["timestamp"]
    ziel     = alle_laeufe[0].get("ziel", ZIEL)

    total_flights   = sum(l["quellen"].get("flights",   {}).get("auffaellig", 0) or 0 for l in alle_laeufe)
    total_gdelt     = sum(l["quellen"].get("gdelt",     {}).get("count", 0) or 0 for l in alle_laeufe)
    total_rss       = sum(l["quellen"].get("rss",       {}).get("count", 0) or 0 for l in alle_laeufe)
    total_reddit    = sum(l["quellen"].get("reddit",    {}).get("count", 0) or 0 for l in alle_laeufe)
    total_telegram  = sum(l["quellen"].get("telegram",  {}).get("count", 0) or 0 for l in alle_laeufe)
    total_seismik   = sum(l["quellen"].get("seismik",   {}).get("count", 0) or 0 for l in alle_laeufe)
    total_acled     = sum(l["quellen"].get("acled",     {}).get("count", 0) or 0 for l in alle_laeufe)

    # Eskalations-Verlauf
    esk_verlauf = []
    for l in alle_laeufe:
        score = l["quellen"].get("eskalation", {}).get("score", 0) or 0
        ts    = l["timestamp"][:16].replace("T", " ")
        esk_verlauf.append({"ts": ts, "score": score})

    # Zeitlinie aller Headlines
    zeitlinie_html = ""
    for lauf in alle_laeufe:
        ts_kurz = lauf["timestamp"][:16].replace("T", " ") + " UTC"
        lauf_nr = lauf.get("lauf_nr", "?")

        quellen_blöcke = ""

        # Flights
        fl = lauf["quellen"].get("flights", {})
        if fl.get("auffaellig", 0):
            for d in fl.get("details", []):
                call = d.get("callsign", d.get("hex", "?"))
                typ  = d.get("type", "")
                quellen_blöcke += f'<div class="ereignis flights">✈ <b>{call}</b> {typ}</div>'

        # GDELT
        for h in lauf["quellen"].get("gdelt", {}).get("headlines", []):
            quellen_blöcke += f'<div class="ereignis gdelt">📡 GDELT: {h}</div>'

        # RSS
        for h in lauf["quellen"].get("rss", {}).get("headlines", []):
            quellen_blöcke += f'<div class="ereignis rss">📰 RSS: {h}</div>'

        # Reddit
        for h in lauf["quellen"].get("reddit", {}).get("headlines", []):
            quellen_blöcke += f'<div class="ereignis reddit">🔴 Reddit: {h}</div>'

        # Telegram
        for t in lauf["quellen"].get("telegram", {}).get("texte", []):
            quellen_blöcke += f'<div class="ereignis telegram">📨 Telegram: {t}</div>'

        # Suche
        for h in lauf["quellen"].get("suche", {}).get("headlines", []):
            quellen_blöcke += f'<div class="ereignis suche">🌐 Web: {h}</div>'

        # Seismik
        eis = lauf["quellen"].get("seismik", {})
        if eis.get("verdaechtig", 0):
            quellen_blöcke += f'<div class="ereignis seismik">🔴 SEISMIK: {eis.get("verdaechtig")} verdächtige Ereignisse</div>'

        # Eskalation
        esk = lauf["quellen"].get("eskalation", {})
        score = esk.get("score", 0)
        level = esk.get("level", "?")
        farbe = "#4caf50" if score < 30 else "#ff9800" if score < 60 else "#f44336"

        if not quellen_blöcke:
            quellen_blöcke = '<div class="ereignis leer">Keine relevanten Ereignisse in diesem Intervall</div>'

        zeitlinie_html += f"""
        <div class="lauf-block">
          <div class="lauf-header">
            <span class="lauf-zeit">⏱ {ts_kurz} (Lauf {lauf_nr})</span>
            <span class="esk-badge" style="background:{farbe}">ESK {score}/100 – {level}</span>
          </div>
          <div class="lauf-inhalt">{quellen_blöcke}</div>
        </div>"""

    # Sparkline-Daten für Chart.js
    sparkline_labels = json.dumps([e["ts"] for e in esk_verlauf])
    sparkline_data   = json.dumps([e["score"] for e in esk_verlauf])

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NEXUS Langtest — {ziel} — Vergleichsbericht</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  :root {{
    --bg:      #0a0e1a;
    --card:    #111827;
    --border:  #1e2d40;
    --accent:  #00d4ff;
    --green:   #4caf50;
    --orange:  #ff9800;
    --red:     #f44336;
    --text:    #e2e8f0;
    --sub:     #8892a4;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', monospace; }}
  header {{ background: var(--card); border-bottom: 1px solid var(--border); padding: 20px 30px; }}
  header h1 {{ color: var(--accent); font-size: 1.6rem; letter-spacing: 3px; text-transform: uppercase; }}
  header p  {{ color: var(--sub); margin-top: 4px; font-size: 0.9rem; }}
  .container {{ max-width: 1400px; margin: 0 auto; padding: 24px 20px; }}

  .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 24px; }}
  .stat-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; text-align: center; }}
  .stat-card .zahl {{ font-size: 2rem; font-weight: bold; color: var(--accent); }}
  .stat-card .label {{ font-size: 0.8rem; color: var(--sub); margin-top: 4px; }}

  .chart-box {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px;
                padding: 20px; margin-bottom: 24px; }}
  .chart-box h2 {{ color: var(--accent); font-size: 1rem; margin-bottom: 16px; }}

  .vergleich-box {{ background: var(--card); border: 2px solid var(--orange); border-radius: 8px;
                    padding: 20px; margin-bottom: 24px; }}
  .vergleich-box h2 {{ color: var(--orange); font-size: 1rem; margin-bottom: 12px; }}
  .vergleich-box p {{ color: var(--sub); font-size: 0.85rem; line-height: 1.6; }}
  .vergleich-box textarea {{
    width: 100%; height: 200px; background: #0d1525; border: 1px solid var(--border);
    color: var(--text); padding: 12px; border-radius: 6px; font-size: 0.85rem; margin-top: 12px; }}

  .zeitlinie h2 {{ color: var(--accent); font-size: 1rem; margin-bottom: 16px; }}
  .lauf-block {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px;
                 margin-bottom: 12px; overflow: hidden; }}
  .lauf-header {{ background: #0d1525; padding: 10px 16px; display: flex;
                  justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }}
  .lauf-zeit {{ color: var(--accent); font-size: 0.85rem; font-weight: bold; }}
  .esk-badge {{ padding: 3px 10px; border-radius: 12px; font-size: 0.78rem; font-weight: bold; color: #fff; }}
  .lauf-inhalt {{ padding: 12px 16px; }}

  .ereignis {{ padding: 5px 8px; margin: 3px 0; border-radius: 4px; font-size: 0.82rem; line-height: 1.4; }}
  .ereignis.flights  {{ background: rgba(0,212,255,0.08); border-left: 3px solid var(--accent); }}
  .ereignis.gdelt    {{ background: rgba(76,175,80,0.08);  border-left: 3px solid var(--green); }}
  .ereignis.rss      {{ background: rgba(255,152,0,0.08);  border-left: 3px solid var(--orange); }}
  .ereignis.reddit   {{ background: rgba(255,86,0,0.08);   border-left: 3px solid #ff5600; }}
  .ereignis.telegram {{ background: rgba(41,182,246,0.08); border-left: 3px solid #29b6f6; }}
  .ereignis.suche    {{ background: rgba(156,39,176,0.08); border-left: 3px solid #9c27b0; }}
  .ereignis.seismik  {{ background: rgba(244,67,54,0.12);  border-left: 3px solid var(--red); }}
  .ereignis.leer     {{ color: var(--sub); font-style: italic; }}

  .filter-bar {{ margin-bottom: 16px; display: flex; gap: 8px; flex-wrap: wrap; }}
  .filter-btn {{ padding: 6px 14px; border-radius: 16px; border: 1px solid var(--border);
                 background: transparent; color: var(--sub); cursor: pointer; font-size: 0.82rem; }}
  .filter-btn:hover, .filter-btn.aktiv {{ background: var(--accent); color: #000; border-color: var(--accent); }}

  footer {{ text-align: center; color: var(--sub); font-size: 0.8rem; padding: 24px; }}
</style>
</head>
<body>
<header>
  <h1>◈ NEXUS Langzeit-Validierungstest</h1>
  <p>Ziel: <b>{ziel}</b> &nbsp;|&nbsp; Start: {start_ts[:16].replace("T"," ")} UTC &nbsp;|&nbsp;
     Ende: {end_ts[:16].replace("T"," ")} UTC &nbsp;|&nbsp; {gesamt_laeufe} Läufe</p>
</header>

<div class="container">

  <!-- Statistik-Kacheln -->
  <div class="stat-grid">
    <div class="stat-card"><div class="zahl">{gesamt_laeufe}</div><div class="label">Läufe gesamt</div></div>
    <div class="stat-card"><div class="zahl" style="color:#00d4ff">{total_flights}</div><div class="label">ISR/Militärflüge</div></div>
    <div class="stat-card"><div class="zahl" style="color:#4caf50">{total_gdelt}</div><div class="label">GDELT Events</div></div>
    <div class="stat-card"><div class="zahl" style="color:#ff9800">{total_rss}</div><div class="label">RSS Artikel</div></div>
    <div class="stat-card"><div class="zahl" style="color:#ff5600">{total_reddit}</div><div class="label">Reddit Posts</div></div>
    <div class="stat-card"><div class="zahl" style="color:#29b6f6">{total_telegram}</div><div class="label">Telegram Posts</div></div>
    <div class="stat-card"><div class="zahl" style="color:#f44336">{total_seismik}</div><div class="label">Seismik-Ereignisse</div></div>
    <div class="stat-card"><div class="zahl" style="color:#9c27b0">{total_acled}</div><div class="label">ACLED Konflikte</div></div>
  </div>

  <!-- Eskalations-Chart -->
  <div class="chart-box">
    <h2>📈 Eskalations-Verlauf über 24h</h2>
    <canvas id="esk-chart" height="80"></canvas>
  </div>

  <!-- Vergleich mit Mainstream-Medien -->
  <div class="vergleich-box">
    <h2>📺 Vergleich: NEXUS vs. Mainstream-Medien</h2>
    <p>Trage unten die wichtigsten Meldungen ein, die heute in Tagesschau, Spiegel, Reuters etc.
       zu <b>{ziel}</b> erschienen sind — dann kannst du vergleichen, ob NEXUS sie früher erkannt hat.</p>
    <textarea placeholder="Beispiel:
08:00 Tagesschau — Iran droht mit Vergeltung nach Angriff auf XY
12:30 Reuters — Iranisches Militär hält Manöver ab
19:00 Spiegel — Neue Sanktionen gegen Iran beschlossen
..."></textarea>
    <p style="margin-top:8px; font-size:0.75rem; color:#666">
    (Diese Notizen werden nicht gespeichert — zum Drucken: Strg+P)</p>
  </div>

  <!-- Zeitlinie -->
  <div class="zeitlinie">
    <h2>🕐 Ereignis-Zeitlinie</h2>
    <div class="filter-bar">
      <button class="filter-btn aktiv" onclick="filter('alle')">Alle</button>
      <button class="filter-btn" onclick="filter('flights')">✈ Flüge</button>
      <button class="filter-btn" onclick="filter('gdelt')">📡 GDELT</button>
      <button class="filter-btn" onclick="filter('rss')">📰 RSS</button>
      <button class="filter-btn" onclick="filter('telegram')">📨 Telegram</button>
      <button class="filter-btn" onclick="filter('seismik')">🌍 Seismik</button>
    </div>
    {zeitlinie_html}
  </div>

</div>

<footer>NEXUS OSINT Langtest — generiert {_ts_anzeige()}</footer>

<script>
// Eskalations-Chart
const ctx = document.getElementById('esk-chart').getContext('2d');
new Chart(ctx, {{
  type: 'line',
  data: {{
    labels: {sparkline_labels},
    datasets: [{{
      label: 'Eskalations-Score',
      data: {sparkline_data},
      borderColor: '#00d4ff',
      backgroundColor: 'rgba(0,212,255,0.1)',
      tension: 0.4,
      fill: true,
      pointRadius: 4,
      pointBackgroundColor: '#00d4ff'
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ color: '#8892a4', maxRotation: 45, font: {{ size: 10 }} }}, grid: {{ color: '#1e2d40' }} }},
      y: {{ min: 0, max: 100, ticks: {{ color: '#8892a4' }}, grid: {{ color: '#1e2d40' }} }}
    }}
  }}
}});

// Filter
function filter(klasse) {{
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('aktiv'));
  event.target.classList.add('aktiv');
  document.querySelectorAll('.ereignis').forEach(el => {{
    if (klasse === 'alle') {{
      el.style.display = '';
    }} else {{
      el.style.display = el.classList.contains(klasse) ? '' : 'none';
    }}
  }});
}}
</script>
</body>
</html>"""

    with open(BERICHT_DATEI, "w", encoding="utf-8") as f:
        f.write(html)

    log(f"✓ Bericht gespeichert: {BERICHT_DATEI}")
    return BERICHT_DATEI

# ─── Live-Status-Server (Handy-Zugriff) ──────────────────────────────────────
STATUS_PORT = 11431
_status_cache = {"lauf_nr": 0, "gesamt": 0, "letzter": {}, "alle": []}

def _eigene_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def _status_html() -> str:
    st = _status_cache
    lauf_nr  = st.get("lauf_nr", 0)
    gesamt   = st.get("gesamt", 0)
    prozent  = int(lauf_nr / gesamt * 100) if gesamt else 0
    letzter  = st.get("letzter", {})
    ts       = letzter.get("timestamp", "–")[:16].replace("T", " ") + " UTC" if letzter.get("timestamp") else "–"
    ziel     = letzter.get("ziel", ZIEL)

    # Eskalations-Score
    esk      = letzter.get("quellen", {}).get("eskalation", {})
    score    = esk.get("score", 0) or 0
    level    = esk.get("level", "?")
    farbe    = "#4caf50" if score < 30 else "#ff9800" if score < 60 else "#f44336"

    # Letzte Headlines
    headlines_html = ""
    for quelle, symbol in [("rss","📰"), ("gdelt","📡"), ("reddit","🔴"),
                            ("telegram","📨"), ("suche","🌐")]:
        items = letzter.get("quellen", {}).get(quelle, {}).get("headlines") or \
                letzter.get("quellen", {}).get(quelle, {}).get("texte") or []
        for h in items[:2]:
            headlines_html += f'<div class="item">{symbol} {h}</div>'
    if not headlines_html:
        headlines_html = '<div class="item leer">Noch keine Daten — erster Lauf läuft...</div>'

    # ISR Flüge
    fl = letzter.get("quellen", {}).get("flights", {})
    auff = fl.get("auffaellig", 0) or 0
    flights_html = ""
    for d in fl.get("details", [])[:3]:
        call = d.get("callsign", d.get("hex", "?"))
        flights_html += f'<div class="item flights">✈ {call}</div>'

    # Verlauf der letzten Scores
    verlauf_scores = [
        (l.get("timestamp","")[:16].replace("T"," "),
         l.get("quellen",{}).get("eskalation",{}).get("score",0) or 0)
        for l in st.get("alle", [])[-8:]
    ]
    verlauf_html = ""
    for ts_v, sc_v in verlauf_scores:
        fb = "#4caf50" if sc_v < 30 else "#ff9800" if sc_v < 60 else "#f44336"
        verlauf_html += f'<div class="vrow"><span class="vts">{ts_v}</span><span class="vbar" style="width:{max(sc_v,2)}%;background:{fb}"></span><span class="vsc">{sc_v}</span></div>'

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>NEXUS Live — {ziel}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0a0e1a;color:#e2e8f0;font-family:'Segoe UI',sans-serif;padding:12px;max-width:480px;margin:auto}}
  h1{{color:#00d4ff;font-size:1.1rem;letter-spacing:2px;text-transform:uppercase;margin-bottom:4px}}
  .sub{{color:#8892a4;font-size:.78rem;margin-bottom:16px}}
  .card{{background:#111827;border:1px solid #1e2d40;border-radius:10px;padding:14px;margin-bottom:12px}}
  .card h2{{font-size:.82rem;color:#8892a4;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px}}
  .progress-bg{{background:#1e2d40;border-radius:6px;height:10px;overflow:hidden;margin-bottom:6px}}
  .progress-fill{{background:#00d4ff;height:10px;border-radius:6px;transition:.5s}}
  .prog-text{{font-size:.78rem;color:#8892a4}}
  .esk-big{{font-size:2.4rem;font-weight:bold;text-align:center;padding:8px 0}}
  .esk-level{{text-align:center;font-size:.85rem;color:#8892a4}}
  .item{{padding:5px 0;font-size:.8rem;border-bottom:1px solid #1e2d40;line-height:1.4}}
  .item:last-child{{border-bottom:none}}
  .item.leer{{color:#556}}
  .item.flights{{color:#00d4ff}}
  .vrow{{display:flex;align-items:center;gap:6px;margin:3px 0;font-size:.75rem}}
  .vts{{color:#8892a4;width:100px;flex-shrink:0}}
  .vbar{{height:8px;border-radius:4px;min-width:2px;transition:.3s}}
  .vsc{{color:#e2e8f0;width:24px;text-align:right}}
  .badge{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:.72rem;font-weight:bold;color:#fff;margin-left:6px}}
  .refresh{{text-align:center;color:#556;font-size:.72rem;margin-top:12px}}
  a{{color:#00d4ff;text-decoration:none}}
</style>
</head>
<body>
<h1>◈ NEXUS Langtest</h1>
<p class="sub">Ziel: <b>{ziel}</b> &nbsp;·&nbsp; Stand: {ts}</p>

<div class="card">
  <h2>Fortschritt</h2>
  <div class="progress-bg"><div class="progress-fill" style="width:{prozent}%"></div></div>
  <p class="prog-text">Lauf {lauf_nr} / {gesamt} &nbsp;·&nbsp; {prozent}% abgeschlossen</p>
</div>

<div class="card">
  <h2>Eskalations-Score</h2>
  <div class="esk-big" style="color:{farbe}">{score}</div>
  <div class="esk-level">{level} &nbsp;<span class="badge" style="background:{farbe}">/100</span></div>
</div>

{"<div class='card'><h2>ISR / Militärflüge (" + str(auff) + ")</h2>" + flights_html + "</div>" if auff else ""}

<div class="card">
  <h2>Letzte Meldungen</h2>
  {headlines_html}
</div>

{"<div class='card'><h2>Eskalations-Verlauf</h2>" + verlauf_html + "</div>" if verlauf_html else ""}

<p class="refresh">🔄 Auto-Refresh alle 60s &nbsp;·&nbsp; <a href="/">Jetzt aktualisieren</a></p>
</body>
</html>"""

class _StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        html = _status_html().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(html))
        self.end_headers()
        self.wfile.write(html)

    def log_message(self, *args):
        pass  # Keine Log-Ausgabe

def starte_status_server():
    try:
        server = HTTPServer(("0.0.0.0", STATUS_PORT), _StatusHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        ip = _eigene_ip()
        log(f"📱 Handy-Live-View: http://{ip}:{STATUS_PORT}  (im Heimnetz erreichbar)")
        return server
    except Exception as e:
        log(f"⚠ Status-Server nicht gestartet: {e}")
        return None

# ─── Hilfsfunktionen ─────────────────────────────────────────────────────────

def warte_bis(zielzeit: float):
    """Wartet bis zum angegebenen Unix-Timestamp (time.time()-Wert).
    Gibt verbleibende Sekunden in 30s-Schritten aus."""
    verbleibend = zielzeit - time.time()
    if verbleibend <= 0:
        return
    log(f"⏳ Nächster Lauf in {int(verbleibend)}s ...")
    while True:
        verbleibend = zielzeit - time.time()
        if verbleibend <= 0:
            break
        time.sleep(min(30, verbleibend))


# ─── Department-Auswertung ───────────────────────────────────────────────────
def dept_auswertung(alle_laeufe: list) -> None:
    """
    Wertet alle Läufe nach Abteilungen aus und gibt eine übersichtliche
    Zusammenfassung auf der Konsole aus.

    Abteilungen und ihre Module (analog nexus_departments.py):
      OSINT   – rss, gdelt, acled, telegram, reddit
      GEOINT  – firms, viirs
      SIGINT  – seismik, gpsjam, lightning
      HUMINT  – suche (Entities, Akteure)
      ECONINT – wirtschaft
    """
    if not alle_laeufe:
        return

    DEPT_MODULE = {
        "⚡ OSINT":   ["rss",      "gdelt",   "acled",   "telegram", "reddit"],
        "🛰 GEOINT":  ["firms",    "viirs"],
        "📡 SIGINT":  ["seismik",  "gpsjam",  "lightning"],
        "🔍 HUMINT":  ["suche"],
        "💰 ECONINT": ["wirtschaft"],
    }

    n = len(alle_laeufe)

    log("╔══════════════════════════════════════════════════════════════╗")
    log("  NEXUS – DEPARTMENT-AUSWERTUNG")
    log(f"  Basis: {n} Läufe")
    log("╠══════════════════════════════════════════════════════════════╣")

    for dept_name, module in DEPT_MODULE.items():
        erfolge_gesamt = 0
        daten_gesamt   = 0
        modul_zeilen   = []

        for modul in module:
            erfolge = 0
            daten   = 0
            for lauf in alle_laeufe:
                q = lauf.get("quellen", {}).get(modul, {})
                status = q.get("status", "")
                # Erfolg: hat irgendwelche Daten geliefert oder Status OK
                count = q.get("count", 0) or 0
                auffaellig = q.get("auffaellig", 0) or 0
                total = count + auffaellig
                if status not in ("", "ausnahme", "timeout", "fehler", "unbekannte_region") or total > 0:
                    erfolge += 1
                daten += total

            quote = (erfolge / n * 100) if n > 0 else 0
            erfolge_gesamt += erfolge
            daten_gesamt   += daten

            if quote >= 80:
                icon = "✅"
            elif quote >= 40:
                icon = "⚠️ "
            else:
                icon = "❌"
            avg = daten / n if n > 0 else 0
            modul_zeilen.append(f"    {icon} {modul:12s}  {quote:5.1f}% OK  ∅{avg:5.1f} Datenpunkte/Lauf")

        dept_quote = (erfolge_gesamt / (n * len(module)) * 100) if n > 0 and module else 0
        if dept_quote >= 80:
            dept_icon = "🟢"
        elif dept_quote >= 40:
            dept_icon = "🟡"
        else:
            dept_icon = "🔴"

        log(f"  {dept_icon} {dept_name}  —  Gesamtquote: {dept_quote:.1f}%  |  {daten_gesamt} Datenpunkte total")
        for z in modul_zeilen:
            log(z)

    # Eskalations-Score Übersicht
    scores = [l.get("quellen", {}).get("eskalation", {}).get("score", 0) or 0
              for l in alle_laeufe]
    if scores:
        avg_score = sum(scores) / len(scores)
        max_score = max(scores)
        min_score = min(scores)
        log("╠══════════════════════════════════════════════════════════════╣")
        log(f"  📊 ESKALATIONS-SCORE  ∅{avg_score:.1f}  |  Max: {max_score}  |  Min: {min_score}")
        kritisch = sum(1 for s in scores if s >= 70)
        if kritisch:
            log(f"  ⚠️  {kritisch}× Score ≥ 70 (KRITISCH) in {n} Läufen")

    log("╚══════════════════════════════════════════════════════════════╝")


# ─── Haupt-Testschleife ───────────────────────────────────────────────────────
def starte_test():
    os.makedirs(DATEN_DIR, exist_ok=True)
    verhindere_schlafmodus()

    intervall_sek  = INTERVALL_MIN * 60
    gesamt_laeufe  = int((DAUER_STUNDEN * 60) / INTERVALL_MIN)

    _status_cache["gesamt"] = gesamt_laeufe

    log(f"╔══════════════════════════════════════════════════════╗")
    log(f"  NEXUS 24h LANGZEIT-VALIDIERUNGSTEST")
    log(f"  Ziel:      {ZIEL}")
    log(f"  Intervall: {INTERVALL_MIN} Minuten")
    log(f"  Läufe:     {gesamt_laeufe} geplant")
    log(f"  Dauer:     {DAUER_STUNDEN} Stunden")
    log(f"  Daten:     {DATEN_DIR}")
    log(f"  PC-Schlaf: DEAKTIVIERT (läuft durch Sperrbildschirm)")
    log(f"╚══════════════════════════════════════════════════════╝")
    log(f"  Abbrechen: Strg+C — Bericht jederzeit: python nexus_longtest.py --bericht")

    starte_status_server()

    try:
        for lauf_nr in range(1, gesamt_laeufe + 1):
            naechster = time.time() + intervall_sek

            ergebnis = ein_lauf(ZIEL, lauf_nr, gesamt_laeufe)
            _status_cache["lauf_nr"] = lauf_nr
            _status_cache["letzter"] = ergebnis
            _status_cache["alle"].append(ergebnis)

            # Zwischenbericht alle 4 Läufe (3h)
            if lauf_nr % 4 == 0:
                log("  → Zwischenbericht wird generiert...")
                generiere_bericht()

            if lauf_nr < gesamt_laeufe:
                warte_sek = max(0, naechster - time.time())
                log(f"  → Nächster Lauf in {int(warte_sek/60)}m {int(warte_sek%60)}s "
                    f"(Lauf {lauf_nr+1}/{gesamt_laeufe})")

                # Warte in 30s-Schritten (für sauberes Strg+C)
                while time.time() < naechster:
                    time.sleep(min(30, naechster - time.time()))

    except KeyboardInterrupt:
        log("\n⚠ Test durch Benutzer unterbrochen.")

    finally:
        dept_auswertung(_status_cache.get("alle", []))
        log("Finaler Bericht wird generiert...")
        bericht_pfad = generiere_bericht()
        aktiviere_schlafmodus()
        log(f"Test beendet. Bericht: {bericht_pfad}")

        # Bericht im Browser oeffnen
        try:
            import subprocess
            subprocess.Popen(["start", "", bericht_pfad], shell=True)
        except Exception:
            pass

# --- Entry Point ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NEXUS Langzeit-Validierungstest")
    parser.add_argument("--bericht",   action="store_true")
    parser.add_argument("--ziel",      default=ZIEL)
    parser.add_argument("--ziele",     default="")
    parser.add_argument("--intervall", type=int, default=INTERVALL_MIN)
    parser.add_argument("--dauer",     type=int, default=DAUER_STUNDEN)
    parser.add_argument("--brief",     action="store_true")
    parser.add_argument("--vergleich", type=int, default=0)
    args = parser.parse_args()

    INTERVALL_MIN = args.intervall
    DAUER_STUNDEN = args.dauer

    if args.ziele:
        regionen = [r.strip() for r in args.ziele.split(",") if r.strip()]
    else:
        regionen = [args.ziel]
    ZIEL = regionen[0]

    if args.bericht:
        pfad = generiere_bericht()
        print(f"Bericht: {pfad}")
        try:
            import subprocess
            subprocess.Popen(["start", "", pfad], shell=True)
        except Exception:
            pass

    elif args.brief:
        try:
            from nexus_brief import generiere_brief, speichere_brief_html  # type: ignore
            bericht = generiere_brief(6, ZIEL)
            print(bericht["text"])
            pfad = speichere_brief_html(bericht["html"])
            print(f"[Brief] HTML: {pfad}")
            import subprocess
            subprocess.Popen(["start", "", pfad], shell=True)
        except Exception as e:
            print(f"[Brief] Fehler: {e}")

    elif args.vergleich > 0:
        try:
            from nexus_compare import vergleiche, formatiere_text, generiere_html  # type: ignore
            vgl = vergleiche(args.vergleich, ZIEL)
            print(formatiere_text(vgl))
            html = generiere_html(vgl)
            pfad = os.path.join(DATEN_DIR, "nexus_compare.html")
            os.makedirs(DATEN_DIR, exist_ok=True)
            with open(pfad, "w", encoding="utf-8") as f:
                f.write(html)
            import subprocess
            subprocess.Popen(["start", "", pfad], shell=True)
        except Exception as e:
            print(f"[Vergleich] Fehler: {e}")

    else:
        if len(regionen) == 1:
            starte_test()
        else:
            intervall_sek = INTERVALL_MIN * 60
            gesamt_laeufe = int((DAUER_STUNDEN * 60) / INTERVALL_MIN)
            log(f"Multi-Region Modus: {', '.join(regionen)}")
            starte_status_server()
            try:
                for lauf_nr in range(1, gesamt_laeufe + 1):
                    naechster = time.time() + intervall_sek
                    for region in regionen:
                        ein_lauf(region, lauf_nr, gesamt_laeufe)
                    warte_bis(naechster)
                log("Multi-Region-Test abgeschlossen.")
                generiere_bericht()
            except KeyboardInterrupt:
                log("Abgebrochen -- generiere Abschlussbericht...")
                generiere_bericht()
