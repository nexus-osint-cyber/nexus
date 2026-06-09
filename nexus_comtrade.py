"""
NEXUS – UN Comtrade Handelsdaten (T203)
=======================================
Nutzt UN Comtrade Free API für Handel-als-Proxy Analyse.

Eskalations-Indikatoren:
  • Handelseinbruch auf Null → Sanktionen / Blockade aktiv
  • Anstieg von Dual-Use HS-Codes → mögliche Aufrüstung
  • Veränderte Handelspartner → Embargo-Umgehung

Dual-Use HS-Codes (militärisch relevant):
  8803  Teile für Luftfahrzeuge
  8802  Luftfahrzeuge inkl. Drohnen
  8705  Spezialfahrzeuge (militärisch nutzbar)
  8901-8906  Schiffe und Boote
  8711  Motorräder (militär-logistisch)
  2707  Treibstoffe / Chemikalien
  2804  Wasserstoff (Raketen-Treibstoff)
  2850  Stickstoffverbindungen (Explosivstoffe)
  3601-3604  Sprengstoffe und Pyrotechnik
  9301-9307  Waffen und Munition (direkt)

API: https://comtradeapi.un.org/ (kostenlos, 250 Req/h)
Kein API-Key nötig für Basic-Zugang.

Abhängigkeiten: pip install requests
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────────────────────

COMTRADE_BASE    = "https://comtradeapi.un.org/data/v1/get"
COMTRADE_META    = "https://comtradeapi.un.org/public/v1/getDA"
REQUEST_TIMEOUT  = 25
_CACHE_DIR       = Path(__file__).parent / "nexus_comtrade_cache"
_CACHE_TTL_H     = 48   # Handelsdaten ändern sich selten

# UN Comtrade Ländercodes (M49)
COUNTRY_CODES: dict[str, str] = {
    "Iran":         "364",
    "Israel":       "376",
    "Russia":       "643",
    "Ukraine":      "804",
    "China":        "156",
    "USA":          "842",
    "Germany":      "276",
    "Yemen":        "887",
    "Gaza":         "275",    # Palestine M49
    "Palestine":    "275",
    "Syria":        "760",
    "Iraq":         "368",
    "Lebanon":      "422",
    "Saudi Arabia": "682",
    "UAE":          "784",
    "Turkey":       "792",
    "North Korea":  "408",
    "Pakistan":     "586",
    "Qatar":        "634",
    "Kuwait":       "414",
    "Jordan":       "400",
}

# Dual-Use HS-Codes (2-stellig → Kategorie-Name)
DUAL_USE_HS: dict[str, str] = {
    "88": "Luftfahrzeuge & Drohnen",
    "87": "Fahrzeuge & Militärfahrzeuge",
    "89": "Schiffe & Marine",
    "93": "Waffen & Munition (direkt)",
    "36": "Sprengstoffe & Pyrotechnik",
    "28": "Chemikalien / Raketentreibstoffe",
    "84": "Maschinen (Dual-Use)",
    "85": "Elektronik (Dual-Use)",
    "90": "Optik & Präzisionsinstrumente",
}

# Vollständige HS4-Codes mit hohem Risiko
HIGH_RISK_HS4 = {
    "8802": "Flugzeuge/Drohnen",
    "8803": "Flugzeugteile",
    "8901": "Kreuzfahrtschiffe/Tankschiffe",
    "8906": "Kriegsschiffe",
    "9301": "Militärwaffen",
    "9302": "Revolver/Pistolen",
    "9303": "Andere Feuerwaffen",
    "9304": "Andere Waffen",
    "9305": "Waffenparts",
    "9306": "Bomben/Raketen/Torpedos",
    "3601": "Treibladungen",
    "3602": "Explosivstoffe",
    "2804": "Wasserstoff",
    "2850": "Stickstoff-Verbindungen",
}


# ─────────────────────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(exist_ok=True)
    return _CACHE_DIR / (key[:80].replace("/","_") + ".json")

def _cache_get(key: str) -> Optional[dict]:
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
        if time.time() - d.get("ts", 0) < _CACHE_TTL_H * 3600:
            return d.get("data")
    except Exception:
        pass
    return None

def _cache_set(key: str, data: dict) -> None:
    try:
        _cache_path(key).write_text(json.dumps({"ts": time.time(), "data": data}))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# API-Abfragen
# ─────────────────────────────────────────────────────────────────────────────

def _get_trade_data(
    reporter_code: str,
    partner_code:  str = "0",        # 0 = World
    hs_code:       str = "TOTAL",    # "TOTAL" oder HS-2-Stellig
    year:          str = "",         # "" = letztes verfügbares Jahr
    flow:          str = "X,M",      # X=Export, M=Import, X,M=beide
) -> Optional[dict]:
    """Ruft UN Comtrade API ab."""
    cache_key = f"{reporter_code}_{partner_code}_{hs_code}_{year}_{flow}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # Parameter aufbauen
    params = {
        "reporterCode": reporter_code,
        "period":       year or "recent",
        "partnerCode":  partner_code,
        "cmdCode":      hs_code,
        "flowCode":     flow,
        "maxRecords":   "500",
        "format":       "JSON",
        "countSumOnly": "0",
    }

    try:
        # Comtrade v1 endpoint (kein Key nötig für öffentliche Daten)
        r = requests.get(
            f"{COMTRADE_BASE}/C/A/HS",
            params=params,
            headers={"User-Agent": "NEXUS-OSINT/1.0"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 429:
            time.sleep(10)
            return None
        r.raise_for_status()
        data = r.json()
        _cache_set(cache_key, data)
        return data
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Analyse-Funktionen
# ─────────────────────────────────────────────────────────────────────────────

def get_trade_volume(
    country:  str,
    partner:  str = "World",
    years:    int = 3,
) -> dict:
    """
    Holt Handelsvolumen für ein Land über mehrere Jahre.
    Erkennt Einbrüche (Sanktions-Indikator).

    Returns
    -------
    dict mit: country, total_by_year, trend, sanctions_indicator, anomaly_score
    """
    country_code  = COUNTRY_CODES.get(country, "")
    partner_code  = COUNTRY_CODES.get(partner, "0") if partner != "World" else "0"

    if not country_code:
        return {"status": "unbekanntes_land", "country": country}

    cache_key = f"volume_{country}_{partner}_{years}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    current_year = datetime.now().year
    year_data = {}

    for y in range(current_year - years, current_year + 1):
        data = _get_trade_data(
            reporter_code=country_code,
            partner_code=partner_code,
            hs_code="TOTAL",
            year=str(y),
        )
        if not data:
            continue
        # Handelsvolumen summieren
        rows = data.get("data") or []
        total_usd = sum(
            float(row.get("primaryValue") or 0)
            for row in rows
        )
        if total_usd > 0:
            year_data[y] = round(total_usd / 1e6, 1)   # Mio USD
        time.sleep(0.5)

    if not year_data:
        result = {
            "status":  "keine_daten",
            "country": country,
            "partner": partner,
            "note":    "Comtrade API hatte keine Daten (evtl. nicht-gemeldetes Land)",
        }
        _cache_set(cache_key, result)
        return result

    # Trend-Analyse
    years_list = sorted(year_data.keys())
    values     = [year_data[y] for y in years_list]

    if len(values) >= 2:
        trend_pct = ((values[-1] - values[0]) / max(values[0], 1)) * 100
    else:
        trend_pct = 0.0

    # Einbruchs-Detektion: >50% Rückgang innerhalb 2 Jahre
    sanctions_indicator = False
    if len(values) >= 2:
        max_val = max(values[:-1]) if len(values) > 1 else values[0]
        if max_val > 0 and values[-1] < max_val * 0.5:
            sanctions_indicator = True

    result = {
        "status":              "ok",
        "country":             country,
        "partner":             partner,
        "trade_by_year_mUSD":  year_data,
        "trend_pct":           round(trend_pct, 1),
        "sanctions_indicator": sanctions_indicator,
        "latest_volume_mUSD":  values[-1] if values else 0,
        "anomaly_score":       min(1.0, abs(trend_pct) / 80.0) if trend_pct < -40 else 0.0,
        "timestamp":           datetime.now(timezone.utc).isoformat(),
    }
    _cache_set(cache_key, result)
    return result


def get_dual_use_flows(country: str, year: str = "") -> dict:
    """
    Analysiert Dual-Use Warenströme für ein Land.
    Erkennt Rüstungs-relevante Handelsveränderungen.

    Returns
    -------
    dict mit: high_risk_imports, high_risk_exports, escalation_indicators
    """
    country_code = COUNTRY_CODES.get(country, "")
    if not country_code:
        return {"status": "unbekanntes_land", "country": country}

    cache_key = f"dualuse_{country}_{year}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    high_risk = {}
    for hs2, cat_name in list(DUAL_USE_HS.items())[:6]:   # Top 6 Kategorien
        data = _get_trade_data(
            reporter_code=country_code,
            hs_code=hs2,
            year=year,
            flow="M",   # Nur Importe (Aufrüstungsindikator)
        )
        if not data:
            time.sleep(0.3)
            continue
        rows = data.get("data") or []
        total = sum(float(r.get("primaryValue") or 0) for r in rows)
        if total > 0:
            high_risk[cat_name] = {
                "hs_code":   hs2,
                "value_mUSD": round(total / 1e6, 2),
                "partners":  list({
                    r.get("partnerDesc", "")
                    for r in rows[:5]
                    if r.get("partnerDesc")
                }),
            }
        time.sleep(0.4)

    # Waffen-Direktimporte (HS 93)
    weapons_data = _get_trade_data(country_code, hs_code="93", flow="M")
    if weapons_data:
        rows = weapons_data.get("data") or []
        total_weapons = sum(float(r.get("primaryValue") or 0) for r in rows)
        if total_weapons > 0:
            high_risk["Direkte Waffen (HS93)"] = {
                "hs_code":    "93",
                "value_mUSD": round(total_weapons / 1e6, 2),
            }

    # Eskalations-Indikator
    esc_total = sum(v.get("value_mUSD", 0) for v in high_risk.values())

    result = {
        "status":              "ok",
        "country":             country,
        "year":                year or "latest",
        "dual_use_imports":    high_risk,
        "total_dual_use_mUSD": round(esc_total, 1),
        "escalation_indicator": esc_total > 100,   # >100 Mio = signifikant
        "timestamp":           datetime.now(timezone.utc).isoformat(),
    }
    _cache_set(cache_key, result)
    return result


def comtrade_escalation_signal(region: str) -> dict:
    """
    Gibt Eskalationssignal basierend auf Handelsdaten zurück.
    Für nexus_escalation.py.
    """
    vol = get_trade_volume(region)
    score = 0.0
    notes = []

    if vol.get("sanctions_indicator"):
        score += 8.0
        notes.append(f"Handelseinbruch {vol.get('trend_pct',0):.0f}% → Sanktions-Indikator")

    if vol.get("anomaly_score", 0) > 0.5:
        score += 5.0
        notes.append("Handelsanomalie erkannt")

    return {
        "status":  "ok" if vol.get("status") == "ok" else "keine_daten",
        "score":   round(score, 1),
        "notes":   notes,
        "region":  region,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Direktaufruf
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    country = sys.argv[1] if len(sys.argv) > 1 else "Iran"
    print(f"NEXUS UN Comtrade Analyse — {country}")
    print("─" * 55)
    print("⚠ Hinweis: Comtrade API kann träge sein (~10s)")
    print()

    v = get_trade_volume(country, years=2)
    print(f"Handelsvolumen ({country}):")
    print(f"  Status: {v['status']}")
    if v.get("trade_by_year_mUSD"):
        for yr, val in sorted(v["trade_by_year_mUSD"].items()):
            print(f"  {yr}: {val:,.0f} Mio USD")
    print(f"  Trend:  {v.get('trend_pct', 0):+.1f}%")
    print(f"  Sanktions-Indikator: {'⚠ JA' if v.get('sanctions_indicator') else 'nein'}")

    sig = comtrade_escalation_signal(country)
    print(f"\nEskalations-Signal: {sig['score']:.1f} pts")
    for n in sig.get("notes", []):
        print(f"  • {n}")
