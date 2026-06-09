"""
nexus_verify.py — NEXUS Nachrichten-Verifikation
==================================================
Prüft RSS/GDELT-Schlagzeilen automatisch gegen eigene Sensordaten.

Beispiel:
  Schlagzeile: "US blockiert iranische Häfen an der Straße von Hormuz"
  → NEXUS prüft: AIS-Daten (Schiffe dunkel?), FIRMS (Feuer an Häfen?),
                  Seismik (Explosionen nahe Hormuz?), GDELT (weitere Quellen?)
  → Ergebnis: "TEILWEISE BESTÄTIGT — 3 AIS-dunkle Schiffe nahe Bandar Abbas,
               keine Seismik-Ereignisse, GDELT: 12 Artikel bestätigen Blockade"

Claim-Typen die NEXUS verifizieren kann:
  - naval_blockade:    Schiffe / Seeblockade / Hafen gesperrt
  - airstrike:         Luftangriff / Bombardierung / Explosion
  - fire_damage:       Brand / Feuer an Infrastruktur
  - troop_movement:    Truppenbewegungen / Konvoi
  - nuclear:           Nuklear / Atomanlage / Strahlung
  - ceasefire:         Waffenstillstand / Deal / Einigung

Aufruf:
  python nexus_verify.py --claim "US blockiert iranische Häfen" --region Iran
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from typing import Optional

# ── Claim-Extraktion ──────────────────────────────────────────────────────────

_CLAIM_PATTERNS: dict[str, list[str]] = {
    "naval_blockade": [
        # Englisch
        "blockade", "blockad", "naval block", "port block", "seaport",
        "naval", "vessel", "fleet", "flotilla", "warship", "destroyer",
        "strait", "harbor", "harbour", "port closed", "port seiz",
        # Deutsch
        "blockiert", "blockier", "seeblockade", "hafen gesperr", "hafen block",
        "häfen", "hafen", "meerenge", "flotte", "marine", "kriegsschiff",
        "hormuz", "hormus", "seeweg", "schifffahrt",
    ],
    "airstrike": [
        # Englisch
        "airstrike", "air strike", "bombing", "bombed", "missile strike",
        "strike on", "hit", "destroyed", "attack on", "targeted",
        "drone strike", "precision strike",
        # Deutsch
        "luftangriff", "bombardier", "raketenangriff", "angriff auf",
        "getroffen", "zerstört", "drohnenangriff", "beschossen",
        "einschlag", "treffer",
    ],
    "fire_damage": [
        # Englisch
        "fire", "burning", "in flames", "refinery fire", "ablaze", "blaze",
        "explosion", "exploded",
        # Deutsch
        "feuer", "brand", "brennt", "flammen", "explosion", "raffinerie",
        "brennend", "explodiert",
    ],
    "troop_movement": [
        # Englisch
        "troop", "troops", "forces", "advance", "convoy", "military vehicle",
        "tank", "armor", "armoured", "mobilization", "deployment",
        # Deutsch
        "soldaten", "truppen", "vormarsch", "konvoi", "panzer",
        "mobilisierung", "truppenverlegung", "einheiten",
    ],
    "nuclear": [
        "nuclear", "nuklear", "atom", "uranium", "enrichment", "anreicherung",
        "reactor", "reaktor", "radiation", "strahlung", "natanz", "fordow",
        "bushehr", "kernwaffe", "atomwaffe", "kernkraft",
    ],
    "ceasefire": [
        # Englisch
        "ceasefire", "truce", "deal", "agreement", "negotiation", "peace deal",
        "peace talks", "accord",
        # Deutsch
        "waffenstillstand", "waffenruhe", "einigung", "verhandlung",
        "friedensabkommen", "deal", "abkommen",
    ],
}

# Orte → GPS-Koordinaten für Verifikation
_LOCATION_COORDS: dict[str, tuple[float, float]] = {
    # Meerengen / Seewege
    "hormuz":          (26.57, 56.50),
    "hormus":          (26.57, 56.50),
    "persischer golf": (26.00, 51.00),
    "persian gulf":    (26.00, 51.00),
    "golf von oman":   (23.00, 59.00),
    "gulf of oman":    (23.00, 59.00),
    "rotes meer":      (20.00, 38.00),
    "red sea":         (20.00, 38.00),
    "schwarzes meer":  (43.00, 34.00),
    "black sea":       (43.00, 34.00),
    "taiwan-strasse":  (24.00, 119.50),
    "taiwan strait":   (24.00, 119.50),
    "korea":           (37.00, 127.00),
    # Spezifische Häfen
    "bandar abbas":    (27.18, 56.27),
    "bandar imam":     (30.43, 49.07),
    "kharg":           (29.24, 50.32),
    "lavan":           (26.80, 53.35),
    "assaluyeh":       (27.50, 52.60),
    "south pars":      (27.50, 52.60),
    "natanz":          (33.72, 51.73),
    "fordow":          (34.88, 50.98),
    "bushehr":         (28.92, 50.83),
    "abadan":          (30.34, 48.28),
    "ahvaz":           (31.32, 48.67),
    "isfahan":         (32.66, 51.68),
    "teheran":         (35.69, 51.39),
    "tehran":          (35.69, 51.39),
    "tabriz":          (38.08, 46.30),
    "gaza":            (31.50, 34.47),
    "beirut":          (33.89, 35.50),
    "damascus":        (33.51, 36.29),
    "kyiv":            (50.45, 30.52),
    "kharkiv":         (49.99, 36.23),
}


def _detect_claim_type(text: str) -> list[str]:
    """Erkennt welche Claim-Typen in einem Text vorkommen."""
    t = text.lower()
    found = []
    for ctype, keywords in _CLAIM_PATTERNS.items():
        if any(kw in t for kw in keywords):
            found.append(ctype)
    return found


def _extract_location(text: str, region: str = "") -> Optional[tuple[float, float, str]]:
    """
    Extrahiert GPS-Koordinaten aus Ortsname im Text.
    1. Feste Tabelle für bekannte Meerengen/Häfen
    2. Nominatim-Geocoding für alles andere
    3. Fallback: Region-Zentrum wenn Claim Hafen/Port enthält
    """
    t = text.lower()

    # 1. Feste Tabelle (Meerengen, wichtige Häfen)
    for loc_name, coords in sorted(_LOCATION_COORDS.items(), key=lambda x: -len(x[0])):
        if loc_name in t:
            return (coords[0], coords[1], loc_name)

    # 2. Nominatim: Ortsnamen aus Text extrahieren und geocodieren
    # Generische Wörter die KEINE Ortsnamen sind überspringen
    _SKIP_WORDS = {
        "häfen", "hafen", "ports", "port", "harbor", "navy", "marine",
        "blockade", "blockiert", "angriff", "attack", "strike", "forces",
        "troops", "soldaten", "feuer", "fire", "explosion", "krieg", "war",
        "deal", "ceasefire", "waffenstillstand", "einigung", "agreement",
        "nord", "süd", "ost", "west", "north", "south", "east", "west",
        "iran", "ukraine", "russland", "israel", "gaza", "syrien",
    }
    import re
    kandidaten = re.findall(r'\b[A-ZÄÖÜ][a-zäöü]{3,}\b', text)
    for kandidat in kandidaten[:4]:
        if kandidat.lower() in _SKIP_WORDS:
            continue
        try:
            import requests as _req
            r = _req.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": kandidat, "format": "json", "limit": 1},
                headers={"User-Agent": "nexus-osint/2.0"},
                timeout=5,
            )
            results = r.json()
            if results:
                lat = float(results[0]["lat"])
                lon = float(results[0]["lon"])
                return (lat, lon, kandidat)
        except Exception:
            continue

    # 3. Fallback: Wenn Claim "Hafen/Port/harbor" enthält → Region-Zentrum nutzen
    port_words = ["hafen", "häfen", "port", "harbor", "harbour", "pier", "naval"]
    if any(w in t for w in port_words) and region:
        try:
            import requests as _req
            r = _req.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": region, "format": "json", "limit": 1},
                headers={"User-Agent": "nexus-osint/2.0"},
                timeout=5,
            )
            results = r.json()
            if results:
                lat = float(results[0]["lat"])
                lon = float(results[0]["lon"])
                return (lat, lon, f"{region} (Küstengebiet)")
        except Exception:
            pass

    return None


# ── Verifikations-Checks ──────────────────────────────────────────────────────

def _check_ais(lat: float, lon: float, radius_km: float = 100) -> dict:
    """Prüft ob AIS-dunkle Schiffe nahe einer Position gemeldet wurden."""
    try:
        from nexus_maritime import get_vessel_positions  # type: ignore
        vessels = get_vessel_positions(f"{lat},{lon}")
        dark = [v for v in (vessels or []) if v.get("ais_dark") or v.get("transponder_off")]
        nearby = []
        for v in (vessels or []):
            try:
                from nexus_firms import _haversine_km
                d = _haversine_km(lat, lon, float(v.get("lat", 0)), float(v.get("lon", 0)))
                if d <= radius_km:
                    nearby.append({**v, "_dist_km": round(d, 1)})
            except Exception:
                continue
        return {
            "vessels_nearby": len(nearby),
            "dark_vessels":   len(dark),
            "top_vessels":    nearby[:3],
        }
    except Exception as e:
        return {"error": str(e), "vessels_nearby": 0, "dark_vessels": 0}


def _check_firms(lat: float, lon: float, radius_km: float = 50) -> dict:
    """Prüft ob FIRMS Feuer in der Nähe einer Position meldet."""
    try:
        from nexus_firms import fetch_firms_fires, _haversine_km, _get_key  # type: ignore
        key = _get_key()
        if not key:
            return {"error": "kein FIRMS Key", "fires_nearby": 0}

        # Kleine BBox um den Punkt
        deg = radius_km / 111.0
        from nexus_firms import FIRMS_API_BASE  # type: ignore
        import csv, io, requests
        bbox_str = f"{lon-deg:.3f},{lat-deg:.3f},{lon+deg:.3f},{lat+deg:.3f}"
        url = f"{FIRMS_API_BASE}/{key}/VIIRS_NOAA21_NRT/{bbox_str}/1"
        r = requests.get(url, timeout=12)
        if not r.ok:
            return {"fires_nearby": 0}

        fires = []
        for row in csv.DictReader(io.StringIO(r.text.strip())):
            try:
                fires.append({
                    "lat":  float(row.get("latitude",  0)),
                    "lon":  float(row.get("longitude", 0)),
                    "frp":  float(row.get("frp", 0) or 0),
                    "conf": row.get("confidence", ""),
                })
            except Exception:
                continue

        anomalies = []
        from nexus_firms import classify_fire  # type: ignore
        for f in fires:
            cls = classify_fire(f["lat"], f["lon"], f["frp"])
            if cls["type"] == "anomaly":
                anomalies.append({**f, "note": cls["note"]})

        return {
            "fires_nearby":    len(fires),
            "anomaly_fires":   len(anomalies),
            "top_anomalies":   anomalies[:3],
        }
    except Exception as e:
        return {"error": str(e), "fires_nearby": 0}


def _check_seismic(lat: float, lon: float, radius_km: float = 150) -> dict:
    """Prüft ob Seismik-Ereignisse nahe einer Position vorliegen."""
    try:
        from nexus_seismic import get_earthquakes  # type: ignore
        quakes = get_earthquakes(f"{lat:.2f},{lon:.2f}", radius_km=radius_km, min_mag=2.0)
        det = [q for q in (quakes or []) if q.get("det_confidence")]
        return {
            "quakes_nearby":    len(quakes or []),
            "detonation_candidates": len(det),
            "top_events":       (quakes or [])[:3],
        }
    except Exception as e:
        return {"error": str(e), "quakes_nearby": 0}


def _check_gdelt(claim_text: str, location: str = "") -> dict:
    """Prüft wie viele GDELT-Artikel den Claim bestätigen."""
    try:
        from nexus_gdelt import fetch_gdelt_articles  # type: ignore
        query = f"{location} {claim_text[:50]}" if location else claim_text[:80]
        articles = fetch_gdelt_articles(query, hours=48, max_records=15)
        return {
            "articles": len(articles),
            "top_titles": [a["title"][:80] for a in articles[:3]],
        }
    except Exception as e:
        return {"error": str(e), "articles": 0}


# ── Gesamt-Verifikation ───────────────────────────────────────────────────────

def verify_claim(claim: str, region: str = "") -> dict:
    """
    Prüft einen Claim/Schlagzeile gegen alle verfügbaren NEXUS-Sensordaten.

    Rückgabe:
      verdict:     'BESTÄTIGT' | 'TEILWEISE_BESTÄTIGT' | 'UNBESTÄTIGT' | 'WIDERLEGT'
      confidence:  0.0 - 1.0
      evidence:    Liste von Belegen
      checks:      Einzelergebnisse pro Sensor
      summary:     Kurzer Text für Analyst
    """
    claim_types = _detect_claim_type(claim)
    location    = _extract_location(claim + " " + region, region)

    checks: dict  = {}
    evidence: list = []
    score = 0.0
    max_score = 0.0

    # ── Vessel Traffic Check (relevant für: naval_blockade) ──────────────────
    if "naval_blockade" in claim_types:
        max_score += 35
        try:
            from nexus_vessel_traffic import analyse_region_ports, STRATEGIC_PORTS  # type: ignore
            # Passenden Hafen für Region finden
            region_ports = [(n, d) for n, d in STRATEGIC_PORTS.items()
                            if d[3].lower() in (region or "").lower()
                            or (region or "").lower() in d[3].lower()]
            if region_ports:
                port_name, port_data = region_ports[0]
                from nexus_vessel_traffic import analyse_port_blockade  # type: ignore
                traffic = analyse_port_blockade(port_name)
                checks["vessel_traffic"] = traffic
                verdict = traffic.get("verdict", "")
                conf    = traffic.get("confidence", 0)
                if "BLOCKADE" in verdict:
                    score += 35
                    evidence.append(f"✅ HAFENVERKEHR: {port_name} — {verdict} ({conf*100:.0f}%)")
                elif "EINBRUCH" in verdict or "RUECKGANG" in verdict:
                    score += 18
                    evidence.append(f"🟡 HAFENVERKEHR: {port_name} — {verdict} ({conf*100:.0f}%)")
                elif "NORMALBETRIEB" in verdict:
                    evidence.append(f"❌ HAFENVERKEHR: {port_name} zeigt normalen Betrieb — kein Blockade-Signal")
                else:
                    evidence.append(f"❓ HAFENVERKEHR: {port_name} — keine Daten verfügbar")
            else:
                evidence.append(f"❓ HAFENVERKEHR: Keine bekannten Häfen für {region}")
        except Exception as e:
            evidence.append(f"❓ HAFENVERKEHR: Nicht verfügbar ({e})")

    # ── SAR-Check via Sentinel-1 (dunkle Schiffe ohne Transponder) ───────────
    if "naval_blockade" in claim_types:
        max_score += 25
        sar_region = "hormuz" if "iran" in (region or "").lower() else (region or "")
        try:
            from nexus_sar import detect_ships, sh_available  # type: ignore
            if sh_available():
                # Erweiterte SAR-Analyse mit Bildverbesserung
                try:
                    from nexus_sar_vision import full_sar_analysis  # type: ignore
                    sar_full = full_sar_analysis(sar_region, baseline_ships=80)
                    consensus = sar_full.get("multi_threshold", {}).get("consensus", 0)
                    checks["sar"] = sar_full
                    sar_count = consensus or sar_full.get("sar_count", 0)
                except Exception:
                    sar = detect_ships(sar_region, max_ships=15)
                    sar_count = sar.ship_count
                    checks["sar"] = {"ships": sar_count}

                # Normwert je Region
                _SAR_NORMAL = {"hormuz": 80, "hormuz-strasse": 80,
                               "persischer golf": 60, "rotes meer": 40}
                normal = _SAR_NORMAL.get(sar_region.lower(), 50)
                count  = sar_count

                if count == 0:
                    score += 25
                    evidence.append(
                        f"⚠️ SAR Sentinel-1: 0 Schiffe nahe {sar_region} "
                        f"(normal: ~{normal}) — Meerenge LEER → starkes Blockade-Signal"
                    )
                elif count < normal * 0.3:
                    score += 22
                    pct = round(count / normal * 100)
                    evidence.append(
                        f"⚠️ SAR Sentinel-1: nur {count} Schiffe ({pct}% von ~{normal}) "
                        f"nahe {sar_region} → STARKER Verkehrseinbruch"
                    )
                elif count < normal * 0.6:
                    score += 12
                    pct = round(count / normal * 100)
                    evidence.append(
                        f"🟡 SAR Sentinel-1: {count} Schiffe ({pct}% von ~{normal}) "
                        f"nahe {sar_region} — moderater Rückgang"
                    )
                else:
                    evidence.append(
                        f"❌ SAR Sentinel-1: {count} Schiffe nahe {sar_region} "
                        f"(normal: ~{normal}) — Verkehr normal, kein Blockade-Signal"
                    )
            else:
                evidence.append("❓ SAR: Sentinel Hub nicht verfügbar")
        except Exception as e:
            evidence.append(f"❓ SAR: Nicht verfügbar ({str(e)[:40]})")

    # ── AIS-Check (relevant für: naval_blockade) ──────────────────────────────
    if "naval_blockade" in claim_types or location:
        max_score += 30
        coords = location or _extract_location(region, region)
        if coords:
            ais = _check_ais(coords[0], coords[1])
            checks["ais"] = ais
            if ais.get("dark_vessels", 0) > 0:
                score += 20
                evidence.append(f"✅ AIS: {ais['dark_vessels']} dunkle Schiffe nahe {coords[2] if location else region}")
            elif ais.get("vessels_nearby", 0) > 3:
                score += 10
                evidence.append(f"🟡 AIS: {ais['vessels_nearby']} Schiffe im Gebiet (kein AIS-Dark)")
            else:
                evidence.append(f"❌ AIS: Keine auffälligen Schiffsbewegungen gefunden")

    # ── Ölpreis-Check (relevant für: naval_blockade, Hormuz) ─────────────────
    if "naval_blockade" in claim_types:
        max_score += 20
        try:
            from nexus_economics import get_market_data  # type: ignore
            econ = get_market_data()
            oil_change = econ.get("oil_change_pct_24h", 0) or 0
            brent = econ.get("brent", 0) or 0
            if oil_change >= 10:
                score += 20
                evidence.append(f"✅ ÖLPREIS: Brent +{oil_change:.1f}% in 24h (${brent:.0f}) — starkes Blockade-Signal")
            elif oil_change >= 5:
                score += 12
                evidence.append(f"🟡 ÖLPREIS: Brent +{oil_change:.1f}% in 24h — moderater Anstieg")
            elif oil_change <= -5:
                evidence.append(f"❌ ÖLPREIS: Brent {oil_change:.1f}% — Preisrückgang spricht gegen Blockade")
            else:
                evidence.append(f"❓ ÖLPREIS: Brent {oil_change:+.1f}% — kein eindeutiges Signal")
        except Exception as e:
            evidence.append(f"❓ ÖLPREIS: Nicht verfügbar ({str(e)[:30]})")

    # ── FIRMS-Check (relevant für: airstrike, fire_damage) ───────────────────
    if any(t in claim_types for t in ["airstrike", "fire_damage"]) or location:
        max_score += 25
        coords = location or _extract_location(region, region)
        if coords:
            firms = _check_firms(coords[0], coords[1])
            checks["firms"] = firms
            if firms.get("anomaly_fires", 0) > 0:
                score += 25
                evidence.append(f"✅ FIRMS: {firms['anomaly_fires']} Anomalie-Feuer nahe {coords[2] if location else region} (nicht Industrieanlage)")
            elif firms.get("fires_nearby", 0) > 0:
                score += 10
                evidence.append(f"🟡 FIRMS: {firms['fires_nearby']} Feuerpunkte gefunden (möglicherweise industriell)")
            else:
                evidence.append(f"❌ FIRMS: Keine Feuerpunkte in der Region")

    # ── Seismik-Check (relevant für: airstrike, explosion) ───────────────────
    if any(t in claim_types for t in ["airstrike", "fire_damage", "nuclear"]):
        max_score += 20
        coords = location or _extract_location(region, region)
        if coords:
            seis = _check_seismic(coords[0], coords[1])
            checks["seismic"] = seis
            if seis.get("detonation_candidates", 0) > 0:
                score += 20
                evidence.append(f"✅ SEISMIK: {seis['detonation_candidates']} Detonations-Kandidat(en) nahe {coords[2] if location else region}")
            elif seis.get("quakes_nearby", 0) > 0:
                score += 5
                evidence.append(f"🟡 SEISMIK: {seis['quakes_nearby']} Seismik-Events, keine klaren Detonationssignaturen")
            else:
                evidence.append(f"❌ SEISMIK: Keine seismischen Ereignisse")

    # ── GDELT-Check (immer) ───────────────────────────────────────────────────
    max_score += 25
    gdelt = _check_gdelt(claim, location[2] if location else region)
    checks["gdelt"] = gdelt
    n = gdelt.get("articles", 0)
    if n >= 5:
        score += 25
        evidence.append(f"✅ GDELT: {n} Artikel bestätigen den Claim")
    elif n >= 2:
        score += 15
        evidence.append(f"🟡 GDELT: {n} Artikel gefunden (geringe Quellendichte)")
    elif n == 1:
        score += 5
        evidence.append(f"🟡 GDELT: 1 Artikel gefunden (Einzelquelle)")
    else:
        evidence.append(f"❌ GDELT: Keine bestätigenden Artikel gefunden")

    # ── Verdict ───────────────────────────────────────────────────────────────
    confidence = round(score / max_score, 2) if max_score > 0 else 0.0

    if confidence >= 0.70:
        verdict = "BESTÄTIGT"
        verdict_icon = "✅"
    elif confidence >= 0.40:
        verdict = "TEILWEISE_BESTÄTIGT"
        verdict_icon = "🟡"
    elif confidence >= 0.15:
        verdict = "UNBESTÄTIGT"
        verdict_icon = "❓"
    else:
        verdict = "NICHT_VERIFIZIERBAR"
        verdict_icon = "⬜"

    # ── Summary ───────────────────────────────────────────────────────────────
    top_ev = "; ".join([e[2:] for e in evidence[:3]])  # Icons entfernen
    summary = (
        f"{verdict_icon} {verdict} ({confidence*100:.0f}% Konfidenz) | "
        f"Claim: '{claim[:60]}' | "
        f"Belege: {top_ev}"
    )

    return {
        "claim":       claim,
        "claim_types": claim_types,
        "location":    location[2] if location else "",
        "verdict":     verdict,
        "verdict_icon": verdict_icon,
        "confidence":  confidence,
        "evidence":    evidence,
        "checks":      checks,
        "summary":     summary,
        "timestamp":   datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
    }


def verify_headlines(headlines: list[str], region: str = "",
                     max_claims: int = 3) -> list[dict]:
    """
    Verifiziert eine Liste von Headlines automatisch.
    Filtert zuerst auf operative Claims (keine politischen Statements).
    """
    operative_types = {"naval_blockade", "airstrike", "fire_damage", "troop_movement", "nuclear"}
    results = []

    for h in headlines:
        if len(results) >= max_claims:
            break
        types = _detect_claim_type(h)
        # Nur operative Claims verifizieren
        if any(t in operative_types for t in types):
            result = verify_claim(h, region)
            results.append(result)

    return results


def verify_summary(headlines: list[str], region: str = "") -> str:
    """Kurze Text-Zusammenfassung der Verifikations-Ergebnisse für LLM."""
    results = verify_headlines(headlines, region, max_claims=3)
    if not results:
        return f"[VERIFY] Keine verifizierbaren operativen Claims in den Headlines für {region}."

    lines = [f"[NEXUS VERIFIKATION — {region}]"]
    for r in results:
        lines.append(f"  {r['verdict_icon']} {r['verdict']} ({r['confidence']*100:.0f}%): {r['claim'][:80]}")
        for ev in r["evidence"][:2]:
            lines.append(f"    {ev}")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NEXUS Claim-Verifikation")
    parser.add_argument("--claim",  type=str, required=True, help="Zu prüfender Claim/Schlagzeile")
    parser.add_argument("--region", type=str, default="",    help="Region (z.B. Iran)")
    args = parser.parse_args()

    print(f"\n[NEXUS VERIFY] Prüfe: '{args.claim}'")
    print(f"[NEXUS VERIFY] Region: {args.region or '(keine)'}\n")

    result = verify_claim(args.claim, args.region)

    print(f"VERDICT:     {result['verdict_icon']} {result['verdict']}")
    print(f"KONFIDENZ:   {result['confidence']*100:.0f}%")
    print(f"CLAIM-TYPEN: {', '.join(result['claim_types']) or 'unbekannt'}")
    print(f"ORT erkannt: {result['location'] or '(keiner)'}")
    print(f"\nBELEGE:")
    for ev in result["evidence"]:
        print(f"  {ev}")
    print(f"\nSUMMARY:\n  {result['summary']}")
