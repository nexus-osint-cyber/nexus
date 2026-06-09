"""
NEXUS – Predictive Theater Analytics
======================================
Berechnet Wahrscheinlichkeiten für Proxy-Aktivierung und Eskalation
basierend auf historischen Score-Zeitreihen (nexus_timeseries.py SQLite).

Kernidee:
  Iran OSINT steigt über 48h → mit Wahrscheinlichkeit P eskaliert
  Hezbollah (Libanon) binnen 72h.

  Das ist kein Machine-Learning im großen Sinn — es ist eine kalibrierte
  Lagged-Korrelations-Analyse: wir messen den Pearson-Korrelationskoeffizienten
  zwischen dem Treiber-Score (T-lag) und dem Proxy-Score (T+0) über alle
  historischen Datenpunkte und konvertieren das in eine Wahrscheinlichkeit.

Vorhersage-Modelle:
  1. Lagged Correlation (Pearson, 24h/48h/72h Lag)
  2. Schwellenwert-Überschreitung (Threshold Breach Rate)
  3. Score-Trend-Analyse (EMA-Steigerung über N Stunden)
  4. Ensemble (gewichtetes Mittel der drei Modelle)

Ausgabe (pro Akteur-Kette):
  {
    "driver":       "Iran",
    "proxy":        "Lebanon",
    "lag_hours":    72,
    "probability":  0.67,
    "confidence":   "medium",
    "model_scores": {"lagged_corr": 0.71, "threshold": 0.58, "trend": 0.62},
    "signal":       "Iran OSINT: +12.3 über 48h (EMA-Anstieg)",
    "last_updated": "2026-06-08T..."
  }

CLI:
  python nexus_theater_predict.py --theater MiddleEast
  python nexus_theater_predict.py --driver Iran --proxy Lebanon
  python nexus_theater_predict.py --all
  python nexus_theater_predict.py --json
"""

from __future__ import annotations

import json
import sys
import math
import argparse
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════════
# Vorhersage-Konfigurationen (kalibrierte Ketten)
# ═══════════════════════════════════════════════════════════════════════════════

PREDICTION_CHAINS: list[dict] = [
    # ── Nahost ────────────────────────────────────────────────────────────────
    {
        "theater":    "MiddleEast",
        "driver":     "Iran",
        "driver_dept": "OSINT",       # Welche Abteilung tracken wir
        "proxy":      "Lebanon",
        "proxy_dept": "SIGINT",
        "lag_hours":  72,
        "base_rate":  0.30,           # Hintergrund-Wahrscheinlichkeit (ohne Signal)
        "sensitivity": 1.8,           # Wie stark Treiber-Anstieg auf P wirkt
        "description": "Iran OSINT ↑ → Hezbollah (Libanon) Aktivierung",
    },
    {
        "theater":    "MiddleEast",
        "driver":     "Iran",
        "driver_dept": "OSINT",
        "proxy":      "Gaza",
        "proxy_dept": "SIGINT",
        "lag_hours":  48,
        "base_rate":  0.35,
        "sensitivity": 1.5,
        "description": "Iran OSINT ↑ → Hamas (Gaza) Raketensalve",
    },
    {
        "theater":    "MiddleEast",
        "driver":     "Iran",
        "driver_dept": "ECONINT",
        "proxy":      "Yemen",
        "proxy_dept": "SIGINT",
        "lag_hours":  96,
        "base_rate":  0.25,
        "sensitivity": 1.3,
        "description": "Iran ECONINT ↑ → Houthi-Drohnen (Jemen) auf Rotes Meer",
    },
    {
        "theater":    "MiddleEast",
        "driver":     "Lebanon",
        "driver_dept": "SIGINT",
        "proxy":      "Israel",
        "proxy_dept": "GEOINT",
        "lag_hours":  12,
        "base_rate":  0.55,
        "sensitivity": 2.0,
        "description": "Hezbollah SIGINT ↑ → IDF Gegenangriff (Israel GEOINT)",
    },
    # ── Osteuropa ─────────────────────────────────────────────────────────────
    {
        "theater":    "EasternEurope",
        "driver":     "Russia",
        "driver_dept": "SIGINT",
        "proxy":      "Ukraine",
        "proxy_dept": "SIGINT",
        "lag_hours":  24,
        "base_rate":  0.65,
        "sensitivity": 1.2,
        "description": "Russland SIGINT ↑ → Großangriff auf ukrainische Infrastruktur",
    },
    {
        "theater":    "EasternEurope",
        "driver":     "Russia",
        "driver_dept": "OSINT",
        "proxy":      "Belarus",
        "proxy_dept": "GEOINT",
        "lag_hours":  48,
        "base_rate":  0.20,
        "sensitivity": 1.6,
        "description": "Russland OSINT ↑ → Belarus Truppenbewegungen (zweite Front)",
    },
    # ── Asien-Pazifik ─────────────────────────────────────────────────────────
    {
        "theater":    "AsiaPacific",
        "driver":     "North Korea",
        "driver_dept": "OSINT",
        "proxy":      "South Korea",
        "proxy_dept": "SIGINT",
        "lag_hours":  36,
        "base_rate":  0.40,
        "sensitivity": 1.4,
        "description": "Nordkorea OSINT ↑ → Südkorea Alarm (DMZ-Provokation)",
    },
    {
        "theater":    "AsiaPacific",
        "driver":     "China",
        "driver_dept": "GEOINT",
        "proxy":      "Taiwan",
        "proxy_dept": "SIGINT",
        "lag_hours":  48,
        "base_rate":  0.20,
        "sensitivity": 2.2,
        "description": "China GEOINT ↑ (PLA-Übungen) → Taiwan Verteidigungsalarm",
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# Zeitreihen-Daten laden
# ═══════════════════════════════════════════════════════════════════════════════

def _load_timeseries(region: str, dept: str, hours: int = 168) -> list[tuple[float, float]]:
    """
    Lädt Score-Zeitreihe aus nexus_timeseries.py SQLite.
    Gibt Liste von (unix_timestamp, score) zurück.
    Fallback: leere Liste wenn kein nexus_timeseries.py.
    """
    try:
        from nexus_timeseries import get_signal_history
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        raw = get_signal_history(
            region=region,
            signal=dept,
            since=cutoff.isoformat(),
        ) or []
        return [(float(r.get("timestamp_unix", 0)), float(r.get("value", 0)))
                for r in raw if "timestamp_unix" in r or "value" in r]
    except Exception:
        return []


def _load_dept_score_now(region: str, dept: str) -> float:
    """Holt den aktuellen Department-Score direkt aus nexus_departments.py."""
    try:
        from nexus_departments import DEPT_FUNCTIONS
        fn = DEPT_FUNCTIONS.get(dept.upper())
        if fn:
            result = fn(region)
            return float(result.get("score", 0.0))
    except Exception:
        pass
    return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Modell-Funktionen
# ═══════════════════════════════════════════════════════════════════════════════

def _pearson(xs: list[float], ys: list[float]) -> float:
    """Pearson-Korrelationskoeffizient."""
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx  = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy  = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx * dy == 0:
        return 0.0
    return max(-1.0, min(1.0, num / (dx * dy)))


def _corr_to_prob(corr: float, base_rate: float) -> float:
    """
    Konvertiert Korrelationskoeffizient → Wahrscheinlichkeit.
    Formel: logistisch skaliert um base_rate.
    """
    # Normalisiere corr (−1..+1) → Faktor (0.5..2.0)
    factor = 1.0 + corr * 0.8
    p = base_rate * factor
    return max(0.05, min(0.97, p))


def _lagged_correlation_model(
    driver_series:   list[tuple[float, float]],
    proxy_series:    list[tuple[float, float]],
    lag_seconds:     float,
    base_rate:       float,
) -> float:
    """
    Model 1: Pearson-Korrelation mit zeitlichem Versatz (Lag).
    Paart driver(T) mit proxy(T + lag).
    """
    if len(driver_series) < 5 or len(proxy_series) < 5:
        return base_rate

    driver_dict = dict(driver_series)
    proxy_times = sorted(proxy_series, key=lambda x: x[0])

    paired_d, paired_p = [], []
    for pt, pv in proxy_times:
        target_driver_time = pt - lag_seconds
        # Nächster Driver-Zeitpunkt vor/bei target
        candidates = [(t, v) for t, v in driver_series if abs(t - target_driver_time) < 3600 * 3]
        if candidates:
            _, dv = min(candidates, key=lambda x: abs(x[0] - target_driver_time))
            paired_d.append(dv)
            paired_p.append(pv)

    if len(paired_d) < 4:
        return base_rate

    corr = _pearson(paired_d, paired_p)
    return _corr_to_prob(corr, base_rate)


def _threshold_breach_model(
    driver_series: list[tuple[float, float]],
    proxy_series:  list[tuple[float, float]],
    lag_seconds:   float,
    threshold:     float = 50.0,
    base_rate:     float = 0.3,
) -> float:
    """
    Model 2: Wie oft folgte auf Driver > threshold binnen lag_seconds ein Proxy > threshold?
    """
    if len(driver_series) < 5 or len(proxy_series) < 5:
        return base_rate

    proxy_dict = dict(proxy_series)
    proxy_times_sorted = sorted(proxy_dict.keys())

    hits = 0
    total = 0
    for t, v in driver_series:
        if v >= threshold:
            total += 1
            # Prüfe ob Proxy binnen lag eskaliert
            future_times = [pt for pt in proxy_times_sorted if t < pt <= t + lag_seconds]
            if any(proxy_dict[pt] >= threshold for pt in future_times):
                hits += 1

    if total == 0:
        return base_rate
    empirical = hits / total
    # Interpoliere mit base_rate (prior)
    return max(0.05, min(0.97, 0.4 * base_rate + 0.6 * empirical))


def _trend_model(
    driver_series:  list[tuple[float, float]],
    lookback_hours: float,
    base_rate:      float,
    sensitivity:    float,
) -> tuple[float, str]:
    """
    Model 3: EMA-Trendstärke des Treibers.
    Gibt (Wahrscheinlichkeit, Signal-Text) zurück.
    """
    if len(driver_series) < 3:
        return base_rate, "Keine Trenddaten"

    sorted_series = sorted(driver_series, key=lambda x: x[0])
    cutoff_ts = sorted_series[-1][0] - lookback_hours * 3600

    recent = [v for t, v in sorted_series if t >= cutoff_ts]
    older  = [v for t, v in sorted_series if t < cutoff_ts]

    if not recent:
        return base_rate, "Keine aktuellen Daten"

    recent_mean = sum(recent) / len(recent)
    older_mean  = sum(older)  / len(older) if older else recent_mean

    delta = recent_mean - older_mean
    current = sorted_series[-1][1]

    # Trendstärke normiert auf 0..1
    trend_strength = max(-1.0, min(1.0, delta / 20.0))
    p = base_rate + trend_strength * base_rate * (sensitivity - 1.0)
    p = max(0.05, min(0.97, p))

    signal_text = (
        f"Score: {current:.0f} | Δ{lookback_hours:.0f}h: "
        f"{'+'  if delta >= 0 else ''}{delta:.1f} | "
        f"EMA-Trend: {'↑ steigend' if delta > 3 else '↓ fallend' if delta < -3 else '→ stabil'}"
    )
    return p, signal_text


# ═══════════════════════════════════════════════════════════════════════════════
# Haupt-Vorhersagefunktion
# ═══════════════════════════════════════════════════════════════════════════════

def predict_chain(chain: dict, use_live: bool = True) -> dict:
    """
    Berechnet Eskalations-Wahrscheinlichkeit für eine Akteur-Kette.

    Parameters
    ----------
    chain    : Konfiguration aus PREDICTION_CHAINS
    use_live : True = aktuellen Score live abrufen

    Returns
    -------
    Vorhersage-Dict
    """
    driver      = chain["driver"]
    driver_dept = chain["driver_dept"]
    proxy       = chain["proxy"]
    proxy_dept  = chain["proxy_dept"]
    lag_h       = chain["lag_hours"]
    base_rate   = chain["base_rate"]
    sensitivity = chain.get("sensitivity", 1.5)
    lag_s       = lag_h * 3600.0

    # Zeitreihen laden (168h Rückblick = 1 Woche)
    driver_series = _load_timeseries(driver, driver_dept, hours=168)
    proxy_series  = _load_timeseries(proxy,  proxy_dept,  hours=168)

    # Aktuellen Score abrufen (für Trend-Modell)
    current_driver_score = _load_dept_score_now(driver, driver_dept) if use_live else 0.0

    # Modell 1: Lagged Correlation
    p_corr = _lagged_correlation_model(driver_series, proxy_series, lag_s, base_rate)

    # Modell 2: Threshold Breach
    p_thresh = _threshold_breach_model(driver_series, proxy_series, lag_s,
                                       threshold=45.0, base_rate=base_rate)

    # Modell 3: Trend
    p_trend, signal_text = _trend_model(driver_series, lookback_hours=48.0,
                                        base_rate=base_rate, sensitivity=sensitivity)

    # Ensemble (gewichtetes Mittel)
    # Trend bekommt mehr Gewicht wenn wir wenig historische Daten haben
    if len(driver_series) < 10:
        weights = (0.20, 0.20, 0.60)  # wenig Daten → mehr Gewicht auf Trend
    else:
        weights = (0.35, 0.35, 0.30)

    p_ensemble = (
        weights[0] * p_corr +
        weights[1] * p_thresh +
        weights[2] * p_trend
    )
    p_ensemble = max(0.05, min(0.97, p_ensemble))

    # Konfidenz basierend auf Datenmenge
    n_data = min(len(driver_series), len(proxy_series))
    if   n_data >= 50: confidence = "high"
    elif n_data >= 15: confidence = "medium"
    elif n_data >= 5:  confidence = "low"
    else:              confidence = "none"

    # Wenn keine historischen Daten → nur Trend/Live
    if n_data < 5 and current_driver_score > 0:
        # Heuristik: Score-basiert
        p_heuristic = base_rate + (current_driver_score / 100.0) * base_rate * sensitivity
        p_ensemble  = max(0.05, min(0.97, p_heuristic))
        confidence  = "none"
        signal_text = f"Keine Zeitreihendaten — Score-Heuristik: {driver} {driver_dept}={current_driver_score:.0f}"

    return {
        "theater":           chain["theater"],
        "driver":            driver,
        "driver_dept":       driver_dept,
        "proxy":             proxy,
        "proxy_dept":        proxy_dept,
        "lag_hours":         lag_h,
        "probability":       round(p_ensemble, 3),
        "probability_pct":   round(p_ensemble * 100, 1),
        "confidence":        confidence,
        "model_scores": {
            "lagged_corr": round(p_corr,   3),
            "threshold":   round(p_thresh, 3),
            "trend":       round(p_trend,  3),
        },
        "current_driver_score": round(current_driver_score, 1),
        "signal":              signal_text,
        "description":         chain["description"],
        "data_points":         n_data,
        "timestamp":           datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def predict_theater(
    theater_name: str,
    use_live:     bool = True,
) -> list[dict]:
    """Alle Vorhersage-Ketten für ein Theater."""
    chains = [c for c in PREDICTION_CHAINS if c["theater"] == theater_name]
    return [predict_chain(c, use_live) for c in chains]


def predict_all(use_live: bool = True) -> dict[str, list[dict]]:
    """Alle Vorhersage-Ketten aller Theater."""
    result: dict[str, list[dict]] = {}
    for chain in PREDICTION_CHAINS:
        tn = chain["theater"]
        if tn not in result:
            result[tn] = []
        result[tn].append(predict_chain(chain, use_live))
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Formatierung
# ═══════════════════════════════════════════════════════════════════════════════

_USE_COLOR = sys.stdout.isatty()
_C = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[91m", "orange": "\033[33m", "yellow": "\033[93m",
    "green": "\033[92m", "cyan": "\033[96m",
}


def _c(style: str, text: str) -> str:
    if not _USE_COLOR: return text
    return _C.get(style, "") + text + _C["reset"]


def _prob_color(p: float) -> str:
    if not _USE_COLOR: return ""
    if   p >= 0.75: return _C["red"]
    elif p >= 0.55: return _C["orange"]
    elif p >= 0.35: return _C["yellow"]
    else:           return _C["green"]


def _prob_bar(p: float, w: int = 20) -> str:
    filled = round(p * w)
    return "█" * filled + "░" * (w - filled)


def format_predictions(predictions: list[dict] | dict, theater_name: str = "") -> str:
    lines = []

    # Normalisieren: Dict (alle Theater) oder List (ein Theater)
    if isinstance(predictions, dict):
        all_chains = []
        for tn, chains in predictions.items():
            for c in chains:
                all_chains.append((tn, c))
    else:
        all_chains = [(theater_name, c) for c in predictions]

    # Sortieren nach Wahrscheinlichkeit absteigend
    all_chains.sort(key=lambda x: -x[1].get("probability", 0))

    lines.append("")
    lines.append(_c("dim", "  " + "═" * 65))
    lines.append(_c("bold", "  🔮  NEXUS · PREDICTIVE THEATER ANALYTICS"))
    lines.append(_c("dim",  "       Eskalations-Wahrscheinlichkeiten (Proxy-Aktivierung)"))
    lines.append(_c("dim", "  " + "═" * 65))
    lines.append("")

    conf_icons = {"high": "✓", "medium": "~", "low": "?", "none": "⚠"}
    current_theater = None

    for tn, pred in all_chains:
        if tn != current_theater:
            current_theater = tn
            lines.append(_c("bold", f"  ── {tn} ────────────────────────────────────"))

        p   = pred.get("probability", 0.0)
        pct = pred.get("probability_pct", 0.0)
        col = _prob_color(p)
        rst = _C["reset"] if _USE_COLOR else ""
        conf = pred.get("confidence", "none")
        ci   = conf_icons.get(conf, "?")
        lag  = pred.get("lag_hours", "?")
        bar  = _prob_bar(p)

        lines.append(
            f"  {col}{bar}  {pct:5.1f}%  {ci}  "
            f"{pred.get('driver','?')}→{pred.get('proxy','?')} "
            f"[{lag}h]{rst}"
        )
        lines.append(_c("dim", f"       {pred.get('description','')}"))
        sig = pred.get("signal", "")
        if sig:
            lines.append(_c("dim", f"       Signal: {sig}"))

        # Modell-Detail (kompakt)
        ms = pred.get("model_scores", {})
        if ms:
            lines.append(_c("dim",
                f"       Modelle: Korr={ms.get('lagged_corr',0):.2f}  "
                f"Schwelle={ms.get('threshold',0):.2f}  "
                f"Trend={ms.get('trend',0):.2f}  "
                f"n={pred.get('data_points',0)}"))
        lines.append("")

    lines.append(_c("dim", "  Konfidenz: ✓=hoch  ~=mittel  ?=niedrig  ⚠=keine Daten"))
    lines.append("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    global _USE_COLOR
    ap = argparse.ArgumentParser(
        prog="nexus_theater_predict",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--theater", "-t", default=None,
        help="Nur dieses Theater (z.B. MiddleEast)")
    ap.add_argument("--driver",  default=None,
        help="Nur Ketten mit diesem Treiber (z.B. Iran)")
    ap.add_argument("--proxy",   default=None,
        help="Nur Ketten mit diesem Proxy (z.B. Lebanon)")
    ap.add_argument("--all",     action="store_true",
        help="Alle Theater (default wenn kein --theater)")
    ap.add_argument("--no-live", action="store_true",
        help="Keinen Live-Score abrufen (nur historische Daten)")
    ap.add_argument("--json",    "-j", action="store_true")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()

    if args.no_color or args.json:
        _USE_COLOR = False

    use_live = not args.no_live
    t0 = time.time()

    if args.theater:
        raw = predict_theater(args.theater, use_live=use_live)
        # Filter
        if args.driver:
            raw = [c for c in raw if c["driver"].lower() == args.driver.lower()]
        if args.proxy:
            raw = [c for c in raw if c["proxy"].lower() == args.proxy.lower()]
        result = {args.theater: raw}
    elif args.driver or args.proxy:
        all_r = predict_all(use_live=use_live)
        result = {}
        for tn, chains in all_r.items():
            filtered = chains
            if args.driver:
                filtered = [c for c in filtered if c["driver"].lower() == args.driver.lower()]
            if args.proxy:
                filtered = [c for c in filtered if c["proxy"].lower() == args.proxy.lower()]
            if filtered:
                result[tn] = filtered
    else:
        result = predict_all(use_live=use_live)

    elapsed = round(time.time() - t0, 1)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    print(format_predictions(result))
    print(_c("dim", f"  Berechnet in {elapsed}s | Live-Scores: {use_live}"))
    print()


if __name__ == "__main__":
    main()
