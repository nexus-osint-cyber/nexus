"""
NEXUS – Reisesicherheits-Modus (T91)
=====================================
Strukturierte Sicherheitsbewertung für Reiseziele.
Aktivierung: Befehl "R <Ziel>" in main.py

Öffentliche API:
  travel_safety_report(destination, query_func) -> dict
  format_travel_brief(report) -> str   (für Konsolausgabe)

Die Funktion sammelt automatisch:
  - Eskalations-Score der Region
  - Aktuelle ACLED/GDELT Konfliktereignisse
  - FIRMS Brandherde
  - Seismische Aktivität
  - Reisewarnungen aus RSS-Feeds (Auswärtiges Amt, UK FCDO)
  - Wetterbedingungen
  - NOTAM Luftsperrgebiete
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Callable, Optional


# ── Reisewarnung-Quellen ──────────────────────────────────────────────────────
_TRAVEL_ADVISORY_FEEDS = [
    # Auswärtiges Amt
    "https://www.auswaertiges-amt.de/opendata/travelwarning",
    # UK FCDO
    "https://www.gov.uk/foreign-travel-advice.atom",
]

# ── Ampel-Schwellen ───────────────────────────────────────────────────────────
def _esc_to_risk(score: float) -> dict:
    """Wandelt Eskalations-Score (0-100) in strukturierte Risikoampel um."""
    if score >= 75:
        return {"level": "KRITISCH", "color": "\033[91m", "badge": "🔴", "travel": "Nicht reisen"}
    elif score >= 50:
        return {"level": "HOCH",     "color": "\033[31m", "badge": "🟠", "travel": "Nur bei zwingenden Gründen"}
    elif score >= 30:
        return {"level": "ERHÖHT",   "color": "\033[33m", "badge": "🟡", "travel": "Mit erhöhter Vorsicht"}
    elif score >= 15:
        return {"level": "MODERAT",  "color": "\033[93m", "badge": "🟡", "travel": "Normale Vorsicht"}
    else:
        return {"level": "NIEDRIG",  "color": "\033[32m", "badge": "🟢", "travel": "Unauffällig"}


# ── Reisewarnung-RSS ──────────────────────────────────────────────────────────
def _fetch_travel_advisories(destination: str) -> list[dict]:
    """Sucht nach aktuellen Reisewarnungen für das Zielland."""
    advisories = []
    dest_lower = destination.lower()

    # Auswärtiges Amt JSON API
    try:
        import requests
        r = requests.get(
            "https://www.auswaertiges-amt.de/opendata/travelwarning",
            timeout=8
        )
        if r.ok:
            data = r.json()
            items = data.get("response", {}).get("items", {})
            for iso, entry in list(items.items())[:200]:
                title = (entry.get("title", "") or "").lower()
                if any(kw in title for kw in [dest_lower, dest_lower[:5]]):
                    url = entry.get("link", "")
                    advisories.append({
                        "source": "Auswärtiges Amt",
                        "country": entry.get("title", ""),
                        "url": url,
                        "level": entry.get("warning", "unbekannt"),
                    })
    except Exception:
        pass

    # UK FCDO RSS
    try:
        import requests
        import xml.etree.ElementTree as ET
        r = requests.get(
            "https://www.gov.uk/foreign-travel-advice.atom",
            timeout=8
        )
        if r.ok:
            root = ET.fromstring(r.text)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.findall("atom:entry", ns)[:50]:
                title = (entry.findtext("atom:title", "", ns) or "").lower()
                if dest_lower[:5] in title:
                    advisories.append({
                        "source": "UK FCDO",
                        "country": entry.findtext("atom:title", "", ns),
                        "url": entry.find("atom:link", ns).get("href", "") if entry.find("atom:link", ns) is not None else "",
                        "level": "Reisewarnung aktiv",
                    })
    except Exception:
        pass

    return advisories[:5]


# ── Haupt-Analysefunktion ─────────────────────────────────────────────────────
def travel_safety_report(
    destination: str,
    query_func: Optional[Callable] = None,
) -> dict:
    """
    Erstellt vollständige Reisesicherheits-Analyse für ein Zielland/-stadt.

    Args:
        destination: Reiseziel (z.B. "Ukraine", "Tel Aviv", "Sudan")
        query_func:  optionale Funktion die NEXUS-Pipeline aufruft
                     Signatur: query_func(query: str) -> dict

    Returns:
        dict mit allen Sicherheitsindikatoren
    """
    ts = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    report = {
        "destination": destination,
        "timestamp": ts,
        "escalation": {},
        "conflicts": [],
        "fires": [],
        "seismic": [],
        "notams": [],
        "travel_advisories": [],
        "weather": {},
        "summary": {},
    }

    # ── NEXUS Live-Daten abrufen ──────────────────────────────────────────────
    live_data = {}
    if query_func:
        try:
            live_data = query_func(destination) or {}
        except Exception:
            pass
    else:
        # Direkter API-Abruf über Live-Server
        try:
            import requests
            r = requests.get(
                f"http://localhost:11430/api/data?query={destination}",
                timeout=15
            )
            if r.ok:
                live_data = r.json()
        except Exception:
            pass

    # ── Eskalations-Score ─────────────────────────────────────────────────────
    esc = live_data.get("escalation", {})
    score = esc.get("score", 0)
    risk = _esc_to_risk(score)
    report["escalation"] = {
        "score": score,
        "level": esc.get("level", risk["level"]),
        "color": esc.get("color", "#888"),
        "risk": risk,
        "signals": esc.get("signal_details", [])[:6],
    }

    # ── Konfliktereignisse (ACLED + GDELT) ────────────────────────────────────
    conflicts = []
    for a in (live_data.get("acled") or [])[:10]:
        conflicts.append({
            "type": "ACLED",
            "event": a.get("event_type", "Konflikt"),
            "date": a.get("date", ""),
            "fatalities": a.get("fatalities", 0),
            "location": a.get("title", "")[:80],
            "lat": a.get("lat"), "lon": a.get("lon"),
        })
    for g in (live_data.get("gdelt_points") or [])[:5]:
        conflicts.append({
            "type": "GDELT",
            "event": "Medien-Event",
            "date": "",
            "fatalities": 0,
            "location": (g.get("title") or "")[:80],
            "lat": g.get("lat"), "lon": g.get("lon"),
        })
    report["conflicts"] = conflicts

    # ── Brände / FIRMS ────────────────────────────────────────────────────────
    report["fires"] = [
        {"frp": f.get("frp", 0), "lat": f.get("lat"), "lon": f.get("lon")}
        for f in (live_data.get("fires") or [])[:5]
    ]

    # ── Seismik ───────────────────────────────────────────────────────────────
    report["seismic"] = [
        {
            "mag": q.get("magnitude", q.get("mag", 0)),
            "place": q.get("place", ""),
            "depth_km": q.get("depth_km", 0),
            "impact": q.get("osint_hint", ""),
        }
        for q in (live_data.get("earthquakes") or [])[:5]
    ]

    # ── NOTAMs ────────────────────────────────────────────────────────────────
    report["notams"] = [
        {
            "id": n.get("notam_id", ""),
            "text": (n.get("text") or n.get("description") or "")[:100],
            "lower_ft": n.get("lower_ft", 0),
            "upper_ft": n.get("upper_ft", 0),
        }
        for n in (live_data.get("notams") or [])[:5]
    ]

    # ── Reisewarnungen (Auswärtiges Amt + FCDO) ───────────────────────────────
    report["travel_advisories"] = _fetch_travel_advisories(destination)

    # ── Wetter ────────────────────────────────────────────────────────────────
    wd = live_data.get("weather") or live_data.get("weather_data") or {}
    report["weather"] = {
        "temp_c": wd.get("temp_c", wd.get("temperature", "?")),
        "desc": wd.get("weather_desc", wd.get("description", "–")),
        "wind_kmh": wd.get("wind_kmh", wd.get("wind_speed", "?")),
        "ops_rating": wd.get("ops_rating", "–"),
    }

    # ── Zusammenfassung ───────────────────────────────────────────────────────
    n_conflicts = len(conflicts)
    n_fires = len(report["fires"])
    n_seismic = len([q for q in report["seismic"] if (q["mag"] or 0) > 3])
    n_notam = len(report["notams"])
    n_advisory = len(report["travel_advisories"])

    rec = risk["travel"]
    if score >= 50 or n_advisory > 0:
        rec = "Von Reise abraten"
    elif score >= 30 or n_conflicts > 3:
        rec = "Nur mit hoher Vorsicht reisen"
    elif score >= 15:
        rec = "Standardvorsicht, regelmäßig Lage prüfen"
    else:
        rec = "Keine besonderen Einschränkungen"

    report["summary"] = {
        "risk_level": risk["level"],
        "risk_badge": risk["badge"],
        "recommendation": rec,
        "n_conflicts": n_conflicts,
        "n_fires": n_fires,
        "n_seismic": n_seismic,
        "n_notam": n_notam,
        "n_advisory": n_advisory,
    }

    return report


# ── Konsolenausgabe ───────────────────────────────────────────────────────────
def format_travel_brief(report: dict) -> str:
    """Formatiert Reisebericht für Konsolenausgabe (ANSI-Farben)."""
    R = "\033[0m"
    C = "\033[96m"    # cyan
    Y = "\033[93m"    # yellow
    G = "\033[92m"    # green
    RED = "\033[91m"
    B = "\033[1m"

    dest = report.get("destination", "?")
    ts = report.get("timestamp", "")
    esc = report.get("escalation", {})
    risk = esc.get("risk", {})
    summ = report.get("summary", {})

    badge = risk.get("badge", "?")
    level = risk.get("level", "?")
    score = esc.get("score", 0)
    rec = summ.get("recommendation", "–")

    col = RED if score >= 50 else (Y if score >= 25 else G)

    lines = [
        f"\n{C}{'═'*60}{R}",
        f"{C}  ◈ NEXUS REISESICHERHEIT – {B}{dest.upper()}{R}{C}  {R}",
        f"{C}  {ts}{R}",
        f"{C}{'═'*60}{R}",
        f"",
        f"  {badge} {B}RISIKOSTUFE: {col}{level}{R}  (Score: {col}{score}/100{R})",
        f"  📋 Empfehlung: {B}{rec}{R}",
        f"",
        f"  {C}── LAGEINDIKATOREN ──────────────────────────────{R}",
        f"  ⚔  Konfliktereignisse:  {summ.get('n_conflicts', 0)}",
        f"  🔥 Brandherde (FIRMS): {summ.get('n_fires', 0)}",
        f"  ⚡ Seismik (M>3):       {summ.get('n_seismic', 0)}",
        f"  🚫 NOTAMs:              {summ.get('n_notam', 0)}",
        f"  ⚠  Reisewarnungen:     {summ.get('n_advisory', 0)}",
    ]

    # Reisewarnungen
    for adv in report.get("travel_advisories", []):
        lines.append(f"     {Y}→ {adv['source']}: {adv['country']} ({adv['level']}){R}")

    # Top-Konflikte
    conflicts = report.get("conflicts", [])
    if conflicts:
        lines.append(f"\n  {C}── TOP-KONFLIKTE ──────────────────────────────────{R}")
        for c in conflicts[:5]:
            fatal = f"  Fatal: {c['fatalities']}" if c.get("fatalities") else ""
            lines.append(f"  [{c['type']}] {c['location'][:55]}{fatal}")

    # NOTAMs
    notams = report.get("notams", [])
    if notams:
        lines.append(f"\n  {C}── LUFTSPERRGEBIETE (NOTAMs) ────────────────────{R}")
        for n in notams[:3]:
            alt = f"  {n['lower_ft']}–{n['upper_ft']} ft" if n.get("upper_ft") else ""
            lines.append(f"  🚫 {n['id']}  {n['text'][:50]}{alt}")

    # Wetter
    w = report.get("weather", {})
    if w.get("desc"):
        lines.append(f"\n  {C}── WETTER & OPERATIVE BEDINGUNGEN ──────────────{R}")
        lines.append(f"  ⛅ {w['desc']}  {w['temp_c']}°C  💨 {w['wind_kmh']} km/h")
        lines.append(f"  Ops-Rating: {w.get('ops_rating', '–')}")

    # Eskalations-Signale
    sigs = esc.get("signals", [])
    if sigs:
        lines.append(f"\n  {C}── ESKALATIONS-SIGNALE ──────────────────────────{R}")
        for s in sigs[:5]:
            nm = s.get("name", "")
            wt = round(s.get("weight", 0) * 100)
            lines.append(f"  • {nm:<28} Gewicht: {wt}%")

    lines.append(f"\n{C}{'═'*60}{R}\n")
    return "\n".join(lines)
