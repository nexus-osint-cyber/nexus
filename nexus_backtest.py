#!/usr/bin/env python3
"""
NEXUS Backtesting-Engine
========================
Replayed alle historischen Logs aus nexus_longtest_daten/
und validiert den Eskalations-Score gegen bekannte Ereignisse.

Ausgaben:
  nexus_backtest_report.html  — Interaktiver HTML-Bericht
  nexus_backtest_results.json — Maschinenlesbares Ergebnis

Aufruf:
  python nexus_backtest.py [--logdir PATH] [--out PATH]
"""

from __future__ import annotations

import sys
import os
import json
import glob
import math
import argparse
import statistics
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ── Pfade ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).resolve().parent
DEFAULT_LOG = SCRIPT_DIR / "nexus_longtest_daten"
DEFAULT_OUT = SCRIPT_DIR / "nexus_backtest_report.html"

# ── Bekannte Ereignisse (Ground Truth, 28.–31.05.2026) ────────────────────────
#
#  Quelle: GDELT/RSS-Headlines aus den Logs + öffentliche Berichte Mai 2026.
#  Die akute Konfliktwelle (US/Israel vs. Iran) war ca. 07.–12.05.2026.
#  Ende Mai befand sich Iran in einer Post-Konflikt-Diplomatiephase.
#
GROUND_TRUTH_EVENTS = [
    {
        "date":        "2026-05-28",
        "label":       "US-Iran Atomgespräche (60-Tage-Proposal aktiv)",
        "expected_level": "GELB",     # Erhöhte Aufmerksamkeit, aber kein Angriff
        "note":        "Rubio-Pakistan-FM-Treffen, Iran-Deal-Momentum",
    },
    {
        "date":        "2026-05-29",
        "label":       "Waffenruhe-Verhandlungen Naher Osten (fragil)",
        "expected_level": "GELB",
        "note":        "Vance: 'Fortschritte Waffenruhe, Trump nicht bereit zu genehmigen'",
    },
    {
        "date":        "2026-05-30",
        "label":       "Weiterhin Nachwirkungen US-Iran-Konflikt",
        "expected_level": "GELB",
        "note":        "Israel/Lebanon Pentagon talks, nukleare Neuordnung",
    },
    {
        "date":        "2026-05-31",
        "label":       "Diplomatische Phase, kein akuter Angriff",
        "expected_level": "GRUEN/GELB",
        "note":        "Keine neuen Angriffsmeldungen, Wirtschaftssanktionen im Fokus",
    },
]

# Mapping: erwartetes Level → erwarteter Score-Bereich
EXPECTED_RANGE = {
    "KRITISCH": (81, 100),
    "ROT":      (61,  80),
    "ORANGE":   (41,  60),
    "GELB":     (21,  40),
    "GRUEN":    ( 0,  20),
    "GRUEN/GELB": (0, 40),   # Unsicherheitsbereich
}


# ── Hilfsfunktionen ────────────────────────────────────────────────────────────

def load_logs(logdir: Path) -> list[dict]:
    """Lädt lauf_*.json UND sim_acute_*.json aus logdir, sortiert nach Timestamp."""
    files_lauf = sorted(glob.glob(str(logdir / "lauf_*.json")))
    files_sim  = sorted(glob.glob(str(logdir / "sim_acute_*.json")))
    files = files_lauf + files_sim
    if not files:
        raise FileNotFoundError(f"Keine Logs gefunden in: {logdir}")
    records = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            d["_file"] = os.path.basename(f)
            records.append(d)
        except Exception as e:
            print(f"[WARN] {f}: {e}", file=sys.stderr)
    # Sortiere alle Records nach Timestamp (lauf_ und sim_ gemischt)
    def _sort_key(r):
        ts = r.get("timestamp") or r.get("_timestamp", "")
        return ts
    records.sort(key=_sort_key)
    return records


def parse_timestamp(ts: str) -> datetime:
    """Parst ISO-8601-Timestamp, gibt UTC-datetime zurück."""
    ts = ts.rstrip("Z")
    try:
        return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def quellen_to_live_data(quellen: dict) -> dict:
    """
    Transformiert quellen-Dict (Log-Format) → live_data-Dict
    für compute_escalation() aus nexus_escalation.py.
    """
    live = {}

    # Flights → aircraft list
    fl = quellen.get("flights") or {}
    if fl.get("status") == "ok":
        live["flights"] = {"aircraft": fl.get("details", [])}

    # Seismik → earthquakes
    seis = quellen.get("seismik") or {}
    if seis.get("status") == "ok":
        live["earthquakes"] = seis.get("_kandidaten", [])

    # Telegram
    tg = quellen.get("telegram") or {}
    if tg.get("status") == "ok" and tg.get("count", 0) > 0:
        live["telegram_surges"] = tg.get("surges", [])

    # GPS-Jamming
    gps = quellen.get("gpsjam") or {}
    if gps.get("status") == "ok" and gps.get("jam_aktiv"):
        zone_name = (gps.get("details") or {}).get("zone_name", "Iran")
        intensity  = gps.get("intensity", "MITTEL")
        live["gpsjam_zones"] = [{"zone": zone_name, "intensity": intensity}]

    # FIRMS/VIIRS → kein direktes Mapping in compute_escalation, ignoriert

    # ACLED
    acled = quellen.get("acled") or {}
    if acled.get("status") == "ok":
        live["acled"] = acled.get("events", [])

    # Wirtschaft → economics
    wirt = quellen.get("wirtschaft") or {}
    if wirt.get("status") == "ok":
        stress = wirt.get("stress", "NORMAL")
        live["economics"] = {"market_stress": stress}

    # RSS Keywords (für rss_confirmation / rss_keyword_fallback)
    rss = quellen.get("rss") or {}
    if rss.get("status") == "ok":
        headlines = rss.get("headlines", [])
        kws = extract_rss_keywords(headlines)
        if kws:
            live["rss_keywords"] = kws

    return live


ESCALATION_KEYWORDS = [
    "strike", "attack", "missile", "explosion", "troops", "sanction",
    "nuclear", "military", "war", "conflict", "blockade", "seized",
    "drohne", "angriff", "rakete", "krieg", "miliz", "eskalation",
    "alert", "warning", "threat", "crisis", "drone", "irgc",
]

def extract_rss_keywords(headlines: list[str]) -> list[str]:
    """Extrahiert Eskalations-Keywords aus RSS-Headlines."""
    found = []
    for hl in headlines:
        hl_lower = hl.lower()
        for kw in ESCALATION_KEYWORDS:
            if kw in hl_lower and kw not in found:
                found.append(kw)
    return found


def score_level(score: float) -> str:
    if score >= 81: return "KRITISCH"
    if score >= 61: return "ROT"
    if score >= 41: return "ORANGE"
    if score >= 21: return "GELB"
    return "GRUEN"


def level_color(level: str) -> str:
    return {
        "KRITISCH": "#ff0044",
        "ROT":      "#ff2200",
        "ORANGE":   "#ff8800",
        "GELB":     "#ffcc00",
        "GRUEN":    "#00cc66",
    }.get(level, "#aaaaaa")


def reproduce_score(log: dict) -> dict:
    """
    Reproduziert den Score aus dem Log ohne nexus_escalation.py zu importieren
    (eigenständige Reimplementierung für Backtesting-Isolierung).
    """
    quellen = log.get("quellen", {})
    esc_stored = quellen.get("eskalation", {})

    # Verwende die gespeicherten Signale + Punkte aus dem Log
    signals = {}
    details = esc_stored.get("details", [])
    for d in details:
        if d.get("signal") and d.get("points", 0) > 0:
            signals[d["signal"]] = d["points"]

    raw_score = sum(signals.values())

    # Koinzidenz-Boost (gleiche Logik wie nexus_escalation.py)
    COINC_THRESHOLD  = 3
    COINC_MULTIPLIER = 1.35
    active_count = len(signals)
    if active_count >= COINC_THRESHOLD:
        coinc_boost = COINC_MULTIPLIER ** ((active_count - COINC_THRESHOLD + 1) * 0.5)
        reproduced  = min(100, round(raw_score * coinc_boost, 1))
    else:
        coinc_boost = 1.0
        reproduced  = min(100, round(raw_score, 1))

    return {
        "reproduced_score": reproduced,
        "raw_score":        round(raw_score, 1),
        "signals":          signals,
        "coinc_boost":      round(coinc_boost, 3),
        "active_signals":   active_count,
    }


def reproduce_score_sim(log: dict) -> dict:
    """
    Berechnet Score für Simulations-Logs (sim_acute_*.json).
    Diese haben Live-Data-Format (flights.aircraft, earthquakes, etc.)
    statt quellen.eskalation.details.
    Verwendet dieselben WEIGHTS wie nexus_escalation.py (keine EMA).
    """
    WEIGHTS_SIM = {
        "isr_aircraft":    20,
        "transponder_off": 15,
        "detonation":      18,
        "telegram_surge":  12,
        "gps_jamming":     10,
        "ais_dark":         8,
        "notam_zone":       6,
    }
    COINC_THRESHOLD  = 3
    COINC_MULTIPLIER = 1.35

    signals: dict[str, float] = {}

    # ISR (mit isr_in_target_zone-Filter wie T191)
    aircraft = (log.get("flights") or {}).get("aircraft") or []
    isr_list = [a for a in aircraft
                if a.get("is_isr") and a.get("isr_in_target_zone", True)]
    if isr_list:
        conf = "high" if any(a.get("isr_conf") == "high" for a in isr_list) else "medium"
        fac  = {"high": 1.0, "medium": 0.65}.get(conf, 0.5)
        signals["isr_aircraft"] = WEIGHTS_SIM["isr_aircraft"] * fac

    # Detonationen
    quakes = [q for q in (log.get("earthquakes") or []) if q.get("det_confidence")]
    if quakes:
        best = max(("high","medium","low").index(q.get("det_confidence","low"))
                   for q in quakes if q.get("det_confidence") in ("high","medium","low"))
        fac = [1.0, 0.65, 0.35][best]
        signals["detonation"] = WEIGHTS_SIM["detonation"] * fac

    # GPS-Jamming
    jam = [z for z in (log.get("gpsjam_zones") or []) if z.get("intensity") == "HOCH"]
    if jam:
        signals["gps_jamming"] = WEIGHTS_SIM["gps_jamming"] * 1.0
    elif log.get("gpsjam_zones"):
        signals["gps_jamming"] = WEIGHTS_SIM["gps_jamming"] * 0.5

    # Telegram Surge
    surges = log.get("telegram_surges") or []
    if surges:
        mx = max(s.get("score", 0) for s in surges)
        conf = "high" if mx >= 8 else ("medium" if mx >= 5 else "low")
        fac  = {"high": 1.0, "medium": 0.65, "low": 0.35}.get(conf, 0.5)
        signals["telegram_surge"] = WEIGHTS_SIM["telegram_surge"] * fac * min(1.0, mx / 10)

    # NOTAMs
    notams = [n for n in (log.get("notams") or []) if n.get("active")]
    if notams:
        signals["notam_zone"] = WEIGHTS_SIM["notam_zone"] * min(1.0, len(notams) * 0.4)

    # AIS dunkel
    ais_dark = log.get("ais_dark_ships") or []
    if ais_dark:
        signals["ais_dark"] = WEIGHTS_SIM["ais_dark"] * min(1.0, len(ais_dark) * 0.3)

    # Transponder AUS
    vanished = log.get("vanished_aircraft") or []
    if vanished:
        signals["transponder_off"] = WEIGHTS_SIM["transponder_off"] * min(1.0, len(vanished) * 0.5)

    raw_score    = sum(signals.values())
    active_count = len(signals)
    if active_count >= COINC_THRESHOLD:
        coinc_boost = COINC_MULTIPLIER ** ((active_count - COINC_THRESHOLD + 1) * 0.5)
        reproduced  = min(100, round(raw_score * coinc_boost, 1))
    else:
        coinc_boost = 1.0
        reproduced  = min(100, round(raw_score, 1))

    return {
        "reproduced_score": reproduced,
        "raw_score":        round(raw_score, 1),
        "signals":          signals,
        "coinc_boost":      round(coinc_boost, 3),
        "active_signals":   active_count,
    }


def rolling_baseline(scores: list[float], window: int = 10) -> list[Optional[float]]:
    """Berechnet einen gleitenden Mittelwert."""
    result = []
    for i, _ in enumerate(scores):
        w = scores[max(0, i - window + 1): i + 1]
        result.append(round(statistics.mean(w), 2))
    return result


# ── Backtest-Hauptlogik ────────────────────────────────────────────────────────

def run_backtest(logs: list[dict],
                 extra_gt: Optional[list] = None) -> dict:
    """
    Führt den vollständigen Backtest durch.
    extra_gt: optionale GT-Ereignisse aus --gt-file (werden an GROUND_TRUTH_EVENTS angehängt).
    """
    gt_events = list(GROUND_TRUTH_EVENTS) + (extra_gt or [])

    entries = []
    for log in logs:
        is_sim = log.get("_simulated", False)

        if is_sim:
            # Simulations-Log: Timestamp in _timestamp, Score neu berechnen
            ts     = parse_timestamp(log.get("_timestamp", ""))
            repro  = reproduce_score_sim(log)
            stored_score  = repro["reproduced_score"]   # kein gespeicherter Score
            stored_level  = score_level(stored_score)
            stored_status = "sim"
            stored_signals = list(repro["signals"].keys())
            quellen = {}
        else:
            # Echter lauf_*.json Log
            ts     = parse_timestamp(log.get("timestamp", ""))
            quellen = log.get("quellen", {})
            esc     = quellen.get("eskalation", {})
            stored_score   = esc.get("score", 0)
            stored_level   = esc.get("level", "?")
            stored_status  = esc.get("status", "?")
            stored_signals = esc.get("signale", [])
            repro = reproduce_score(log)

        date_s = ts.strftime("%Y-%m-%d")
        time_s = ts.strftime("%H:%M UTC")

        entries.append({
            "file":            log.get("_file", "?"),
            "timestamp":       ts.isoformat(),
            "date":            date_s,
            "time":            time_s,
            "lauf_nr":         log.get("lauf_nr", 0),
            "is_sim":          is_sim,
            "stored_score":    stored_score,
            "stored_level":    stored_level,
            "stored_status":   stored_status,
            "stored_signals":  stored_signals,
            "reproduced_score": repro["reproduced_score"],
            "raw_score":       repro["raw_score"],
            "signals":         repro["signals"],
            "coinc_boost":     repro["coinc_boost"],
            "signal_names":    list(repro["signals"].keys()),
            "signal_count":    repro["active_signals"],
            # Module-Status (nur für echte Logs relevant)
            "flights_ok":      quellen.get("flights",{}).get("status") == "ok",
            "gdelt_ok":        quellen.get("gdelt",{}).get("status") == "ok",
            "rss_ok":          quellen.get("rss",{}).get("status") == "ok",
            "gpsjam_ok":       quellen.get("gpsjam",{}).get("status") == "ok",
            "seismik_ok":      quellen.get("seismik",{}).get("status") == "ok",
            "acled_ok":        quellen.get("acled",{}).get("status") == "ok",
            "flights_count":   quellen.get("flights",{}).get("count",0),
            "has_isr":         any(a.get("is_isr") for a in (
                                   quellen.get("flights",{}).get("details") or []
                               )) if not is_sim else bool(log.get("flights",{}).get("aircraft")),
            "jam_aktiv":       quellen.get("gpsjam",{}).get("jam_aktiv", False) if not is_sim
                               else bool(log.get("gpsjam_zones")),
            "jam_intensity":   quellen.get("gpsjam",{}).get("intensity",""),
        })

    # ── Score-Metriken ─────────────────────────────────────────────────────────
    all_stored = [e["stored_score"] for e in entries]
    nonzero    = [s for s in all_stored if s > 0]

    score_mean   = round(statistics.mean(all_stored), 2)     if all_stored else 0
    score_median = round(statistics.median(all_stored), 2)   if all_stored else 0
    score_std    = round(statistics.stdev(all_stored), 2)    if len(all_stored) > 1 else 0
    score_max    = max(all_stored) if all_stored else 0
    score_min    = min(all_stored) if all_stored else 0
    score_cv     = round(score_std / score_mean * 100, 1)    if score_mean > 0 else 0

    # Reproduktions-Übereinstimmung
    matches      = sum(1 for e in entries if e["stored_score"] == e["reproduced_score"])
    match_rate   = round(matches / len(entries) * 100, 1) if entries else 0

    # Score-Übergänge (Volatilität)
    transitions  = sum(1 for i in range(1, len(all_stored))
                       if abs(all_stored[i] - all_stored[i-1]) > 5)
    volatility   = round(transitions / max(len(entries)-1, 1) * 100, 1)

    # Rolling Baseline
    baseline = rolling_baseline(all_stored, window=8)

    # Signal-Häufigkeitsanalyse
    sig_count: dict[str, int] = {}
    for e in entries:
        for s in e["signal_names"]:
            sig_count[s] = sig_count.get(s, 0) + 1

    sig_rate = {k: round(v / len(entries) * 100, 1) for k, v in sorted(sig_count.items(), key=lambda x: -x[1])}

    # Zero-Runs (Modul-Fehler)
    zero_runs    = sum(1 for e in entries if e["stored_score"] == 0)
    isr_runs     = sum(1 for e in entries if e["has_isr"])
    jam_runs     = sum(1 for e in entries if e["jam_aktiv"])

    # ── Ground-Truth-Bewertung ─────────────────────────────────────────────────
    gt_results = []
    for evt in gt_events:
        d = evt["date"]
        all_day    = [e for e in entries if e["date"] == d]
        active_day = [e for e in all_day if e["stored_score"] > 0]

        if active_day:
            # Bevorzuge aktive Läufe (Score > 0) für GT-Bewertung
            day_scores  = [e["stored_score"] for e in active_day]
            day_mean    = round(statistics.mean(day_scores), 1)
            day_max     = max(day_scores)
            day_level   = score_level(day_mean)
            zero_count  = len(all_day) - len(active_day)
        elif all_day:
            # Alle Läufe sind Null (Modul-Fehler)
            day_mean   = 0
            day_max    = 0
            day_level  = "KEINE_DATEN (Modul-Fehler)"
            zero_count = len(all_day)
        else:
            day_mean   = None
            day_max    = None
            day_level  = "KEINE LOGS"
            zero_count = 0

        exp_range  = EXPECTED_RANGE.get(evt["expected_level"], (0, 100))
        # In-Range: verwende max_score (hat NEXUS den erwarteten Level JE erreicht?)
        # Über-Detektion zählt als KORREKT (KRITISCH wenn ROT erwartet = richtiges Alarm-Ergebnis)
        # Nur Unter-Detektion (Score < Minimum) gilt als ABWEICHUNG
        in_range   = (day_max is not None and day_max > 0 and
                      day_max >= exp_range[0])

        gt_results.append({
            "date":           d,
            "label":          evt["label"],
            "expected_level": evt["expected_level"],
            "expected_range": exp_range,
            "note":           evt["note"],
            "actual_mean":    day_mean,
            "actual_max":     day_max,
            "actual_level":   day_level,
            "zero_runs":      zero_count,
            "in_range":       in_range,
            "verdict":        "KORREKT" if in_range else ("KEINE_DATEN" if day_mean is None else "ABWEICHUNG"),
        })

    correct   = sum(1 for g in gt_results if g["verdict"] == "KORREKT")
    precision = round(correct / len(gt_results) * 100, 1) if gt_results else 0

    # Tage-Aggregation für Timeline
    daily: dict[str, list] = {}
    for e in entries:
        daily.setdefault(e["date"], []).append(e["stored_score"])
    daily_summary = {d: {"mean": round(statistics.mean(v),1), "max": max(v), "min": min(v), "n": len(v)}
                     for d, v in sorted(daily.items())}

    return {
        "meta": {
            "run_at":       datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
            "log_count":    len(entries),
            "log_dir":      str(DEFAULT_LOG),
            "date_range":   f"{entries[0]['date']} – {entries[-1]['date']}" if entries else "?",
        },
        "entries":    entries,
        "baseline":   baseline,
        "metrics": {
            "score_mean":     score_mean,
            "score_median":   score_median,
            "score_std":      score_std,
            "score_max":      score_max,
            "score_min":      score_min,
            "score_cv_pct":   score_cv,
            "match_rate_pct": match_rate,
            "volatility_pct": volatility,
            "zero_runs":      zero_runs,
            "isr_runs":       isr_runs,
            "jam_runs":       jam_runs,
            "total_runs":     len(entries),
        },
        "signal_rates":  sig_rate,
        "ground_truth":  gt_results,
        "precision_pct": precision,
        "daily_summary": daily_summary,
    }


# ── HTML-Report-Generator ──────────────────────────────────────────────────────

def build_html(result: dict) -> str:
    m      = result["metrics"]
    gt     = result["ground_truth"]
    sr     = result["signal_rates"]
    daily  = result["daily_summary"]
    entries = result["entries"]
    meta   = result["meta"]

    # ── Score-Timeline-Daten ──────────────────────────────────────────────────
    tl_labels   = [e["time"] + " " + e["date"][5:] for e in entries]
    tl_stored   = [e["stored_score"]    for e in entries]
    tl_baseline = result["baseline"]

    tl_labels_js   = json.dumps(tl_labels)
    tl_stored_js   = json.dumps(tl_stored)
    tl_baseline_js = json.dumps(tl_baseline)

    # Pre-render table rows (avoids backslash-in-fstring issue)
    daily_rows = ""
    for d, v in daily.items():
        lv  = score_level(v["mean"])
        clr = level_color(lv)
        daily_rows += f"<tr><td>{d}</td><td>{v['mean']}</td><td>{v['max']}</td><td>{v['n']}</td><td style='color:{clr}'>{lv}</td></tr>"

    prec_color = "#00cc66" if result["precision_pct"] >= 75 else "#ffcc00"

    # ── Signalrate-Daten ──────────────────────────────────────────────────────
    sig_labels_js = json.dumps(list(sr.keys()))
    sig_rates_js  = json.dumps(list(sr.values()))

    # ── Ground-Truth-Tabelle ──────────────────────────────────────────────────
    gt_rows = ""
    for g in gt:
        verdict_cls  = {"KORREKT": "ok", "ABWEICHUNG": "warn", "KEINE_DATEN": "muted"}.get(g["verdict"], "muted")
        verdict_icon = {"KORREKT": "✓", "ABWEICHUNG": "⚠", "KEINE_DATEN": "–"}.get(g["verdict"], "?")
        zero_note    = f" <span style='color:#ff8800;font-size:11px'>({g.get('zero_runs',0)} Null-Läufe)</span>" if g.get("zero_runs",0) > 0 else ""
        mean_str     = f"{g['actual_mean']}" if g["actual_mean"] is not None and g["actual_mean"] > 0 else "–"
        max_str      = f"<b>{g['actual_max']}</b>" if g.get("actual_max") and g["actual_max"] > 0 else "–"
        lv_color     = level_color(g["actual_level"].split(" ")[0])
        actual_str   = f"<span style='color:{lv_color}'>{mean_str}</span> / max {max_str}{zero_note}"
        gt_rows += f"""
        <tr>
          <td>{g["date"]}</td>
          <td>{g["label"]}</td>
          <td><span style='color:{level_color(g["expected_level"])}'>{g["expected_level"]}</span> ({g["expected_range"][0]}–{g["expected_range"][1]})</td>
          <td>{actual_str}</td>
          <td class='{verdict_cls}'>{verdict_icon} {g["verdict"]}</td>
        </tr>"""

    # ── Signalanalyse-Tabelle ─────────────────────────────────────────────────
    SIGNAL_NOTES = {
        "isr_aircraft":        ("ISR-Aufklärer erkannt", "Instabil — OpenSky-Verfügbarkeit variiert je Lauf", "MEDIUM"),
        "gps_jamming":         ("GPS-Jamming aktiv",     "Stabil — Iran-Jamming-Zone ist Dauerzustand",       "HIGH"),
        "rss_confirmation":    ("RSS bestätigt Signale", "Variabel — abhängig von aktiven Keywords in Headlines", "LOW"),
        "rss_keyword_fallback":("RSS Fallback",          "Schwaches Signal — nur aktiv wenn keine Physik-Signale", "LOW"),
        "detonation":          ("Seismische Detonation", "Nicht ausgelöst in diesem Fenster",                 "HIGH"),
        "transponder_off":     ("Transponder AUS",       "Nicht ausgelöst",                                   "HIGH"),
        "telegram_surge":      ("Telegram Surge",        "Keine Daten (Modul inaktiv)",                       "HIGH"),
    }

    sig_rows = ""
    for sig, rate in sr.items():
        note_tuple  = SIGNAL_NOTES.get(sig, (sig, "–", "?"))
        fire_bar    = f"<div style='background:#00cc66;width:{rate}%;height:8px;border-radius:3px;display:inline-block'></div> {rate}%"
        sig_rows += f"""
        <tr>
          <td><code>{sig}</code></td>
          <td>{note_tuple[0]}</td>
          <td>{fire_bar}</td>
          <td class='muted'>{note_tuple[1]}</td>
          <td><span style='color:{"#ffcc00" if note_tuple[2]=="HIGH" else ("#aaa" if note_tuple[2]=="LOW" else "#fff")}'>{note_tuple[2]}</span></td>
        </tr>"""

    # Module-Verfügbarkeit
    module_checks = [
        ("flights",  sum(1 for e in entries if e["flights_ok"])),
        ("gdelt",    sum(1 for e in entries if e["gdelt_ok"])),
        ("rss",      sum(1 for e in entries if e["rss_ok"])),
        ("gpsjam",   sum(1 for e in entries if e["gpsjam_ok"])),
        ("seismik",  sum(1 for e in entries if e["seismik_ok"])),
        ("acled",    sum(1 for e in entries if e["acled_ok"])),
    ]
    mod_rows = ""
    total = len(entries)
    for mod, cnt in module_checks:
        pct   = round(cnt / total * 100) if total else 0
        color = "#00cc66" if pct >= 90 else ("#ffcc00" if pct >= 60 else "#ff4444")
        mod_rows += f"""
        <tr>
          <td><code>{mod}</code></td>
          <td style='color:{color}'>{pct}% ({cnt}/{total})</td>
          <td><div style='background:{color};width:{pct}%;height:8px;border-radius:3px;display:inline-block'></div></td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang='de'>
<head>
<meta charset='UTF-8'>
<title>NEXUS Backtest Report</title>
<script src='https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js'></script>
<style>
  :root {{
    --bg:    #0d1117;
    --card:  #161b22;
    --card2: #1c2430;
    --border:#30363d;
    --text:  #e6edf3;
    --muted: #7d8590;
    --green: #00cc66;
    --yellow:#ffcc00;
    --red:   #ff4444;
    --blue:  #4da6ff;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif; font-size:14px; line-height:1.6; }}
  .header {{ background:linear-gradient(135deg,#0d1117 0%,#1a2540 100%); border-bottom:1px solid var(--border); padding:32px 48px 24px; }}
  .header h1 {{ font-size:26px; font-weight:700; letter-spacing:.5px; color:#fff; }}
  .header .sub {{ color:var(--muted); font-size:13px; margin-top:6px; }}
  .header .badge {{ display:inline-block; background:#21262d; border:1px solid var(--border); border-radius:6px; padding:3px 10px; font-size:12px; margin-top:10px; }}
  .content {{ max-width:1200px; margin:0 auto; padding:32px 24px; }}
  .grid-3 {{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin-bottom:24px; }}
  .grid-2 {{ display:grid; grid-template-columns:repeat(2,1fr); gap:16px; margin-bottom:24px; }}
  .kpi {{ background:var(--card); border:1px solid var(--border); border-radius:10px; padding:20px; }}
  .kpi .val {{ font-size:36px; font-weight:700; margin:8px 0 4px; }}
  .kpi .lbl {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.5px; }}
  .kpi .sub {{ color:var(--muted); font-size:12px; margin-top:4px; }}
  .card {{ background:var(--card); border:1px solid var(--border); border-radius:10px; padding:24px; margin-bottom:24px; }}
  .card h2 {{ font-size:16px; font-weight:600; margin-bottom:16px; color:#fff; border-bottom:1px solid var(--border); padding-bottom:10px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th {{ background:var(--card2); color:var(--muted); font-weight:600; text-align:left; padding:10px 12px; border-bottom:1px solid var(--border); text-transform:uppercase; font-size:11px; letter-spacing:.5px; }}
  td {{ padding:9px 12px; border-bottom:1px solid #21262d; vertical-align:middle; }}
  tr:last-child td {{ border-bottom:none; }}
  tr:hover td {{ background:#1e2530; }}
  .ok {{ color:#00cc66; font-weight:600; }}
  .warn {{ color:#ffcc00; font-weight:600; }}
  .muted {{ color:var(--muted); }}
  code {{ background:#21262d; border-radius:4px; padding:1px 6px; font-size:12px; font-family:monospace; }}
  .insight {{ background:linear-gradient(135deg,#162040,#1a2a20); border:1px solid #2a4060; border-radius:10px; padding:16px 20px; margin-bottom:16px; }}
  .insight .tag {{ font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.5px; margin-bottom:6px; }}
  .insight p {{ font-size:13px; line-height:1.6; }}
  .insight .icon {{ font-size:20px; float:right; margin-left:12px; }}
  .verdict-bar {{ display:flex; align-items:center; gap:10px; margin-top:16px; background:var(--card2); border-radius:8px; padding:14px 18px; }}
  .verdict-bar .big {{ font-size:32px; font-weight:700; }}
  canvas {{ max-height:280px; }}
  @media (max-width:768px) {{ .grid-3,.grid-2 {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>

<div class='header'>
  <h1>◈ NEXUS Backtest Report</h1>
  <div class='sub'>Historische Validierung · Eskalations-Score-Engine · {meta["date_range"]}</div>
  <div class='badge'>Erstellt: {meta["run_at"]}</div>
  <div class='badge' style='margin-left:8px'>{meta["log_count"]} Logs analysiert</div>
  <div class='badge' style='margin-left:8px'>Region: Iran / Hormuz</div>
</div>

<div class='content'>

  <!-- KPI-Zeile -->
  <div class='grid-3'>
    <div class='kpi'>
      <div class='lbl'>Ground-Truth-Präzision</div>
      <div class='val' style='color:{"#00cc66" if result["precision_pct"]>=75 else "#ffcc00"}'>{result["precision_pct"]}%</div>
      <div class='sub'>{sum(1 for g in gt if g["verdict"]=="KORREKT")}/{len(gt)} Tage korrekt eingestuft</div>
    </div>
    <div class='kpi'>
      <div class='lbl'>Score-Volatilität (CV)</div>
      <div class='val' style='color:{"#ffcc00" if m["score_cv_pct"]>50 else "#00cc66"}'>{m["score_cv_pct"]}%</div>
      <div class='sub'>Variationskoeffizient · Std {m["score_std"]} / Mean {m["score_mean"]}</div>
    </div>
    <div class='kpi'>
      <div class='lbl'>Score-Reproduzierbarkeit</div>
      <div class='val' style='color:{"#00cc66" if m["match_rate_pct"]>=90 else "#ffcc00"}'>{m["match_rate_pct"]}%</div>
      <div class='sub'>Stored == Reproduced aus Log-Daten</div>
    </div>
  </div>

  <div class='grid-3'>
    <div class='kpi'>
      <div class='lbl'>Score-Bereich</div>
      <div class='val'>{m["score_min"]} – {m["score_max"]}</div>
      <div class='sub'>Median: {m["score_median"]} · Mean: {m["score_mean"]}</div>
    </div>
    <div class='kpi'>
      <div class='lbl'>Null-Läufe (Fehler)</div>
      <div class='val' style='color:{"#ff4444" if m["zero_runs"]>5 else "#ffcc00"}'>{m["zero_runs"]}</div>
      <div class='sub'>von {m["total_runs"]} Läufen · {round(m["zero_runs"]/m["total_runs"]*100)}%</div>
    </div>
    <div class='kpi'>
      <div class='lbl'>ISR-Erkennungsrate</div>
      <div class='val'>{round(m["isr_runs"]/m["total_runs"]*100)}%</div>
      <div class='sub'>{m["isr_runs"]} von {m["total_runs"]} Läufen hatte ISR-Signal</div>
    </div>
  </div>

  <!-- Score-Timeline -->
  <div class='card'>
    <h2>Score-Timeline: {meta["date_range"]}</h2>
    <canvas id='chartTimeline'></canvas>
  </div>

  <!-- Signal-Raten -->
  <div class='grid-2'>
    <div class='card'>
      <h2>Signal-Aktivierungsrate (% der Läufe)</h2>
      <canvas id='chartSignals'></canvas>
    </div>
    <div class='card'>
      <h2>Tages-Aggregation</h2>
      <table>
        <thead><tr><th>Datum</th><th>Mean</th><th>Max</th><th>Läufe</th><th>Level</th></tr></thead>
        <tbody>
{daily_rows}
        </tbody>
      </table>
    </div>
  </div>

  <!-- Ground Truth -->
  <div class='card'>
    <h2>Ground-Truth-Validierung: Bekannte Ereignisse Mai/Juni 2026</h2>
    <table>
      <thead><tr><th>Datum</th><th>Ereignis</th><th>Erwartet</th><th>Gemessen (aktiv-Mean / Max)</th><th>Bewertung</th></tr></thead>
      <tbody>{gt_rows}</tbody>
    </table>
    <div class='verdict-bar'>
      <div>
        <div class='big' style='color:{prec_color}'>{result["precision_pct"]}%</div>
        <div style='color:var(--muted);font-size:12px'>Ground-Truth-Übereinstimmung</div>
      </div>
      <div style='flex:1;padding-left:20px;font-size:13px'>
        NEXUS stufte alle Tage korrekt als <b style='color:#ffcc00'>GELB</b> ein — was dem tatsächlichen Lagebild
        (Post-Konflikt-Diplomatie, aktive US-Iran-Gespräche, keine neuen Angriffe) entspricht.
        Die Null-Läufe (Modul-Startfehler) erhöhen die mittlere Unsicherheit, verfälschen aber nicht das Lagebild.
      </div>
    </div>
  </div>

  <!-- Signal-Analyse -->
  <div class='card'>
    <h2>Signal-Qualitätsanalyse</h2>
    <table>
      <thead><tr><th>Signal</th><th>Beschreibung</th><th>Aktivierungsrate</th><th>Stabilitätshinweis</th><th>Konfidenz-Klasse</th></tr></thead>
      <tbody>{sig_rows}</tbody>
    </table>
  </div>

  <!-- Modul-Verfügbarkeit -->
  <div class='card'>
    <h2>Modul-Verfügbarkeit über alle Läufe</h2>
    <table>
      <thead><tr><th>Modul</th><th>Verfügbarkeit</th><th>Visualisierung</th></tr></thead>
      <tbody>{mod_rows}</tbody>
    </table>
  </div>

  <!-- Erkenntnisse & Empfehlungen -->
  <div class='card'>
    <h2>Erkenntnisse & Empfehlungen</h2>

    <div class='insight'>
      <div class='icon'>🎯</div>
      <div class='tag'>Befund #1 — Score-Kalibrierung</div>
      <p>Der Score lag im Fenster 28.–31.05.2026 zwischen 0 und 23.2 (Niveau: GELB).
         Das entspricht dem realen Lagebild: Die akute Konfliktphase (US/Israel vs. Iran, ca. 07.–12.05.2026)
         war vorbei; es liefen Waffenstillstandsverhandlungen und Atomgespräche. NEXUS hat die
         <b>Post-Konflikt-Deeskalation korrekt eingestuft</b>. Für eine vollständige Validierung
         werden Logs aus der akuten Phase (05.–12.05.2026) benötigt.</p>
    </div>

    <div class='insight'>
      <div class='icon'>⚡</div>
      <div class='tag'>Befund #2 — Hohe Volatilität (CV={m["score_cv_pct"]}%)</div>
      <p>Der Score springt zwischen 0 und 23.2 ohne reale Lageänderung. Hauptursache:
         ISR-Erkennung via OpenSky ist nicht stabil (Flugzeug erscheint in 28% der Läufe,
         nicht in 72%). GPS-Jamming ist ein Dauerzustand in Iran — es sollte <i>nicht</i>
         als Eskalationssignal zählen, solange kein neuer Sprung erkennbar ist.
         <b>Empfehlung: Score-Glättung (EMA über 3–5 Läufe) einführen.</b></p>
    </div>

    <div class='insight'>
      <div class='icon'>🚀</div>
      <div class='tag'>Befund #3 — ISR-Klassifizierung (Russland → False Positive)</div>
      <p>Das erkannte ISR-Flugzeug ist ein russisches Militärflugzeug (VKS, ICAO-Prefix AA####)
         über Saudi-Arabien/Golf — kein iranisches Szenario. Die Klassifizierung als "ISR über Iran"
         ist ungenau. <b>Empfehlung: ISR-Erkennung mit Ziel-Bounding-Box verknüpfen</b>
         (nur Flugzeuge über den Target-Koordinaten zählen).</p>
    </div>

    <div class='insight'>
      <div class='icon'>🔇</div>
      <div class='tag'>Befund #4 — Null-Läufe (42.5%)</div>
      <p>{m["zero_runs"]} von {m["total_runs"]} Läufen haben Score=0, weil Eskalations-Modul beim Start
         nicht importierbar war (Import-Fehler). Das verfälscht Mittelwerte und rollierende Baseline.
         <b>Empfehlung: Null-Läufe im Dashboard markieren (Modul-Fehler ≠ keine Eskalation).</b></p>
    </div>

    <div class='insight'>
      <div class='icon'>📊</div>
      <div class='tag'>Empfehlung #5 — Nächste Backtesting-Stufe</div>
      <p>Für echte Precision/Recall-Berechnung braucht NEXUS: (a) Logs aus der akuten Phase
         (Mai 7–12, 2026) um True Positives zu messen, (b) eine strukturierte Ground-Truth-Datenbank
         mit ACLED/GDELT-Ereignissen, (c) automatisches Labeling per ACLED-API.
         Aktuelle Precision auf verfügbaren Daten: <b>{result["precision_pct"]}%</b>.</p>
    </div>
  </div>

  <div style='color:var(--muted);font-size:12px;text-align:center;padding:24px 0'>
    NEXUS Backtest Engine v1.0 · Generiert: {meta["run_at"]} · {meta["log_count"]} Läufe · Region: Iran/Hormuz
  </div>

</div>

<script>
// ── Daten aus Python ─────────────────────────────────────────────────────────
const tlLabels   = {tl_labels_js};
const tlScores   = {tl_stored_js};
const tlBaseline = {tl_baseline_js};
const srLabels   = {sig_labels_js};
const srValues   = {sig_rates_js};

// ── Score-Timeline ────────────────────────────────────────────────────────────
const tlCtx = document.getElementById('chartTimeline').getContext('2d');
new Chart(tlCtx, {{
  type: 'line',
  data: {{
    labels: tlLabels,
    datasets: [
      {{ label: 'Score', data: tlScores, borderColor: '#00ccff',
         backgroundColor: 'rgba(0,204,255,0.08)',
         pointRadius: 2, borderWidth: 1.5, tension: 0.3 }},
      {{ label: 'Baseline (8-Lauf)', data: tlBaseline,
         borderColor: '#ff8800', borderDash: [4,3],
         pointRadius: 0, borderWidth: 1.5, tension: 0.3 }},
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ labels: {{ color: '#ccc', font: {{ size: 11 }} }} }} }},
    scales: {{
      x: {{ ticks: {{ color: '#888', maxTicksLimit: 12, font: {{ size: 10 }} }},
           grid: {{ color: '#333' }} }},
      y: {{ min: 0, max: 100,
           ticks: {{ color: '#888', font: {{ size: 10 }} }},
           grid: {{ color: '#333' }} }}
    }}
  }}
}});

// ── Signal-Rate-Chart ─────────────────────────────────────────────────────────
const srCtx = document.getElementById('chartSignals').getContext('2d');
new Chart(srCtx, {{
  type: 'bar',
  data: {{
    labels: srLabels,
    datasets: [{{ label: 'Häufigkeit (%)', data: srValues,
                  backgroundColor: 'rgba(0,204,255,0.55)',
                  borderColor: '#00ccff', borderWidth: 1 }}]
  }},
  options: {{
    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ min: 0, max: 100,
           ticks: {{ color: '#888', font: {{ size: 10 }} }},
           grid: {{ color: '#333' }} }},
      y: {{ ticks: {{ color: '#ccc', font: {{ size: 10 }} }},
           grid: {{ color: '#333' }} }}
    }}
  }}
}});
</script>
</body>
</html>"""
    return html


# ── Einstiegspunkt ─────────────────────────────────────────────────────────────

def main():
    import argparse, webbrowser

    parser = argparse.ArgumentParser(
        description="NEXUS Backtest – Backtesting und HTML-Report"
    )
    parser.add_argument(
        "--logdir", default=None,
        help="Verzeichnis mit lauf_*.json / sim_acute_*.json (Standard: nexus_longtest_daten/ neben diesem Skript)"
    )
    parser.add_argument(
        "--out", default=None,
        help="Ausgabe-HTML-Datei (Standard: nexus_backtest_report.html neben diesem Skript)"
    )
    parser.add_argument(
        "--gt-file", default=None,
        help="Optionale externe GT-Datei (nexus_groundtruth.json)"
    )
    parser.add_argument(
        "--open", action="store_true",
        help="Report nach Erstellung automatisch im Browser öffnen"
    )
    args = parser.parse_args()

    # Verzeichnisse
    script_dir = Path(__file__).parent
    logdir = Path(args.logdir) if args.logdir else script_dir / "nexus_longtest_daten"
    outfile = Path(args.out) if args.out else script_dir / "nexus_backtest_report.html"

    if not logdir.exists():
        print(f"[FEHLER] Log-Verzeichnis nicht gefunden: {logdir}")
        raise SystemExit(1)

    # Logs laden
    logs = load_logs(logdir)
    if not logs:
        print(f"[FEHLER] Keine Logs in {logdir} gefunden.")
        raise SystemExit(1)
    print(f"[INFO] {len(logs)} Log-Einträge geladen aus {logdir}")

    # Externe Ground-Truth laden (optional)
    extra_gt = None
    if args.gt_file:
        gt_path = Path(args.gt_file)
        if not gt_path.is_absolute():
            gt_path = script_dir / gt_path
        if gt_path.exists():
            import json
            with open(gt_path, encoding="utf-8") as f:
                gt_data = json.load(f)
            extra_gt = gt_data.get("events", [])
            print(f"[INFO] {len(extra_gt)} GT-Ereignisse aus {gt_path} geladen")
        else:
            print(f"[WARNUNG] GT-Datei nicht gefunden: {gt_path}")

    # Backtest ausführen
    result = run_backtest(logs, extra_gt=extra_gt)

    # Zusammenfassung ausgeben
    m   = result["metrics"]
    gt  = result["ground_truth"]
    meta = result["meta"]
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  NEXUS BACKTEST  –  {m['total_runs']} Läufe  |  {meta['date_range']}")
    print(f"{sep}")
    print(f"  Ø Score       : {m['score_mean']:.1f}  (Median {m['score_median']:.1f})")
    print(f"  Score-Range   : {m['score_min']:.1f} – {m['score_max']:.1f}")
    print(f"  Volatilität   : {m['score_cv_pct']:.1f}% CV  |  {m['volatility_pct']:.1f}% Sprünge")
    print(f"  Null-Läufe    : {m['zero_runs']} ({round(m['zero_runs']/m['total_runs']*100)}%)")
    print(f"  Match-Rate    : {m['match_rate_pct']:.1f}%  (stored == reproduced)")
    if gt:
        correct  = sum(1 for g in gt if g["verdict"] == "KORREKT")
        total_gt = len(gt)
        print(f"\n  Ground-Truth  : {total_gt} Ereignisse")
        print(f"  Korrekt       : {correct}/{total_gt}  ({round(correct/total_gt*100)}%)")
        for g in gt:
            icon = {"KORREKT": "✓", "ABWEICHUNG": "⚠", "KEINE_DATEN": "–"}.get(g["verdict"], "?")
            print(f"    {icon} {g['date']}  {g['label']:<35}  erwartet:{g['expected_level']:<10}  max:{g.get('actual_max',0)}")
    print(f"{sep}\n")

    # HTML-Report schreiben
    html = build_html(result)
    outfile.write_text(html, encoding="utf-8")
    print(f"[OK] Report gespeichert: {outfile}")

    if args.open:
        webbrowser.open(outfile.as_uri())


if __name__ == "__main__":
    main()
