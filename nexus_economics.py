"""
NEXUS – Wirtschaftsindikatoren-Modul (Stufe 3)
Liest Echtzeit-Finanzdaten von Yahoo Finance – kostenlos, kein API-Key.

Indikatoren:
  CL=F   – WTI-Öl (US-Rohöl, wichtigster Kriegsindikator)
  BZ=F   – Brent-Öl (europäischer Rohöl-Preis)
  GC=F   – Gold (Risikobarometer, steigt bei Krisen)
  ^VIX   – VIX Volatilitätsindex (Angst-Index, >30 = hohe Spannung)
  ^GSPC  – S&P 500 (Gesamtmarktstimmung)
  HG=F   – Kupfer (Industrieaktivität / Kriegsproduktion)

Interpretation für OSINT:
  Öl +5% in 24h   → mögliche Angebotsunterbrechung / Eskalation
  Gold +2% in 24h → Risikoaversion, Anleger flüchten in sichere Häfen
  VIX > 30        → erhöhte Marktangst
  VIX > 40        → extreme Spannung / Krisenmodus
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

import requests

REQUEST_TIMEOUT = 6
_cache: dict[str, dict] = {}
_cache_ts: dict[str, float] = {}
_CACHE_TTL = 300  # 5 Minuten

# ── Symbole ─────────────────────────────────────────────────────────────────

SYMBOLS = {
    "wti_oil":   ("CL=F",  "WTI-Öl",       "USD/Barrel",  "🛢"),
    "brent_oil": ("BZ=F",  "Brent-Öl",     "USD/Barrel",  "🛢"),
    "gold":      ("GC=F",  "Gold",          "USD/Unze",    "🥇"),
    "vix":       (r"^VIX", "VIX Angst-Idx", "Punkte",      "📊"),
    "sp500":     (r"^GSPC","S&P 500",       "Punkte",      "📈"),
    "copper":    ("HG=F",  "Kupfer",        "USD/Pfund",   "🔧"),
}


# ── Abruf ───────────────────────────────────────────────────────────────────

def _fetch_yahoo(symbol: str) -> Optional[dict]:
    """Ruft aktuellen Kurs von Yahoo Finance ab."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    try:
        r = requests.get(
            url,
            params={"interval": "1d", "range": "5d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        result = data["chart"]["result"]
        if not result:
            return None
        meta = result[0]["meta"]
        closes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
        # Letzter + vorletzter Schlusskurs für % Änderung
        valid = [c for c in closes if c is not None]
        price = meta.get("regularMarketPrice") or (valid[-1] if valid else None)
        prev  = valid[-2] if len(valid) >= 2 else None
        if price is None:
            return None
        change_pct = ((price - prev) / prev * 100) if prev and prev > 0 else None
        return {
            "price":       round(price, 2),
            "prev":        round(prev, 2) if prev else None,
            "change_pct":  round(change_pct, 2) if change_pct is not None else None,
            "currency":    meta.get("currency", "USD"),
            "market_time": meta.get("regularMarketTime"),
        }
    except Exception:
        return None


def _get(symbol: str) -> Optional[dict]:
    """Gecachter Abruf."""
    now = time.monotonic()
    if symbol in _cache and now - _cache_ts.get(symbol, 0) < _CACHE_TTL:
        return _cache[symbol]
    result = _fetch_yahoo(symbol)
    if result:
        _cache[symbol]    = result
        _cache_ts[symbol] = now
    return result


# ── OSINT-Interpretation ────────────────────────────────────────────────────

def _interpret(key: str, data: dict) -> str:
    """Gibt eine kurze OSINT-Einschätzung basierend auf dem Preis zurück."""
    pct = data.get("change_pct")
    p   = data.get("price", 0)

    if key == "wti_oil" or key == "brent_oil":
        if pct and pct >= 5:
            return "⚠ STARKER ANSTIEG – mögliche Angebotsunterbrechung / Eskalation"
        if pct and pct >= 2:
            return "↑ erhöht – Markt preist Risiken ein"
        if pct and pct <= -3:
            return "↓ fallend – Nachfragerückgang oder Deeskalation"
        return "→ stabil"

    if key == "gold":
        if pct and pct >= 2:
            return "⚠ Risikoaversion – Anleger flüchten in sichere Häfen"
        if pct and pct >= 1:
            return "↑ leicht erhöht – gewisse Unsicherheit"
        return "→ stabil"

    if key == "vix":
        if p >= 40:
            return "🔴 EXTREM – Krisenmodus / Panik am Markt"
        if p >= 30:
            return "🟠 HOCH – starke Marktangst"
        if p >= 20:
            return "🟡 ERHÖHT – überdurchschnittliche Unsicherheit"
        return "🟢 NORMAL – ruhige Marktlage"

    if key == "sp500":
        if pct and pct <= -2:
            return "⚠ Starker Rückgang – Risk-off Stimmung"
        if pct and pct <= -1:
            return "↓ leicht schwächer"
        if pct and pct >= 1:
            return "↑ Risikobereitschaft hoch"
        return "→ stabil"

    if key == "copper":
        if pct and pct >= 3:
            return "↑ Nachfrage steigt – Industrieaktivität wächst"
        if pct and pct <= -3:
            return "↓ Rückgang – Industrieerwartungen schwächer"
        return "→ stabil"

    return ""


# ── Haupt-API ───────────────────────────────────────────────────────────────

def get_economic_indicators() -> dict:
    """
    Ruft alle Indikatoren ab.
    Gibt Dict zurück mit allen Kursen + OSINT-Bewertung.
    """
    ts = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    result = {
        "timestamp": ts,
        "indicators": {},
        "osint_signals": [],   # Nur auffällige Signale
        "market_stress": "NORMAL",
    }

    stress_score = 0

    for key, (sym, name, unit, icon) in SYMBOLS.items():
        data = _get(sym)
        if not data:
            continue
        interpretation = _interpret(key, data)
        entry = {
            "symbol":         sym,
            "name":           name,
            "icon":           icon,
            "price":          data["price"],
            "change_pct":     data.get("change_pct"),
            "unit":           unit,
            "interpretation": interpretation,
        }
        result["indicators"][key] = entry

        # Stress-Score
        if key == "vix":
            p = data["price"]
            if p >= 40:
                stress_score += 3
            elif p >= 30:
                stress_score += 2
            elif p >= 20:
                stress_score += 1
        if key in ("wti_oil", "brent_oil") and data.get("change_pct", 0) and data["change_pct"] >= 3:
            stress_score += 1
        if key == "gold" and data.get("change_pct", 0) and data["change_pct"] >= 2:
            stress_score += 1
        if key == "sp500" and data.get("change_pct", 0) and data["change_pct"] <= -2:
            stress_score += 1

        # Auffällige Signale sammeln
        if any(kw in interpretation for kw in ["⚠", "🔴", "🟠", "EXTREM", "HOCH", "Risikoaversion"]):
            result["osint_signals"].append(f"{icon} {name}: {interpretation}")

    # Gesamtstress
    if stress_score >= 4:
        result["market_stress"] = "KRITISCH"
    elif stress_score >= 2:
        result["market_stress"] = "ERHÖHT"
    elif stress_score >= 1:
        result["market_stress"] = "LEICHT_ERHÖHT"
    else:
        result["market_stress"] = "NORMAL"

    return result


def economics_for_llm() -> str:
    """Gibt formatierten Text für LLM-Kontext zurück."""
    data = get_economic_indicators()
    if not data["indicators"]:
        return ""

    lines = [f"\n[WIRTSCHAFTSINDIKATOREN – {data['timestamp']}]"]
    lines.append(f"Marktstress: {data['market_stress']}")
    lines.append("")

    for key, ind in data["indicators"].items():
        pct_str = f" ({'+' if (ind['change_pct'] or 0) >= 0 else ''}{ind['change_pct']}%)" if ind.get("change_pct") is not None else ""
        lines.append(
            f"{ind['icon']} {ind['name']}: {ind['price']} {ind['unit']}{pct_str}"
            f" → {ind['interpretation']}"
        )

    if data["osint_signals"]:
        lines.append("")
        lines.append("OSINT-SIGNALE:")
        for sig in data["osint_signals"]:
            lines.append(f"  {sig}")

    return "\n".join(lines)


def economics_summary_line() -> str:
    """Gibt eine einzelne Zusammenfassungszeile zurück (für Terminal/Report-Header)."""
    data = get_economic_indicators()
    inds = data["indicators"]
    parts = []
    for key in ("wti_oil", "gold", "vix"):
        if key in inds:
            ind = inds[key]
            pct = ind.get("change_pct")
            pct_str = f"{'+' if pct and pct >= 0 else ''}{pct:.1f}%" if pct is not None else ""
            parts.append(f"{ind['icon']}{ind['name']} {ind['price']} {ind['unit']}" +
                         (f" {pct_str}" if pct_str else ""))
    stress = data["market_stress"]
    stress_color = {
        "KRITISCH":       "\033[91m",
        "ERHÖHT":         "\033[93m",
        "LEICHT_ERHÖHT":  "\033[93m",
        "NORMAL":         "\033[92m",
    }.get(stress, "")
    reset = "\033[0m"
    return f"{' | '.join(parts)}  {stress_color}[Marktstress: {stress}]{reset}"


# ── Direktaufruf zum Testen ──────────────────────────────────────────────────
if __name__ == "__main__":
    print("NEXUS Wirtschaftsindikatoren")
    print("─" * 50)
    data = get_economic_indicators()
    print(f"Marktstress: {data['market_stress']}")
    print()
    for key, ind in data["indicators"].items():
        pct = ind.get("change_pct")
        pct_str = f" ({'+' if pct and pct >= 0 else ''}{pct}%)" if pct is not None else ""
        print(f"{ind['icon']} {ind['name']:<20} {ind['price']:>10} {ind['unit']:<12}{pct_str}")
        if ind["interpretation"] != "→ stabil":
            print(f"   → {ind['interpretation']}")
    if data["osint_signals"]:
        print("\n⚡ OSINT-SIGNALE:")
        for s in data["osint_signals"]:
            print(f"  {s}")
    print()
    print("LLM-Kontext Preview:")
    print(economics_for_llm()[:300])
