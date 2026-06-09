"""
NEXUS – Konflikt-Theater Modul
==============================
Zeigt alle beteiligten Akteure eines Konflikts als zusammenhängendes
"Theater" — nicht einzelne Regionen, sondern das ganze Ökosystem.

Warum Theater?
--------------
Konflikte sind keine Inseln. Der Nahost-Konflikt ist ein Netz:
  Iran → (Waffen/Geld) → Hamas (Gaza)
  Iran → (Waffen/Geld) → Hezbollah (Libanon)
  Iran → (Waffen/Geld) → Houthis (Jemen)
  Wenn Iran eskaliert, aktivieren sich Proxies — Israel wird von mehreren
  Seiten gleichzeitig angegriffen. NEXUS muss das Gesamtbild zeigen.

Nutzung:
  from nexus_theater import compute_theater, format_theater_report, THEATERS

  result = compute_theater("MiddleEast")
  print(format_theater_report(result))

  # Alle verfügbaren Theater auflisten:
  from nexus_theater import list_theaters
  list_theaters()

CLI:
  python nexus_theater.py --theater MiddleEast
  python nexus_theater.py --theater EasternEurope --dept OSINT SIGINT
  python nexus_theater.py --theater MiddleEast --json
  python nexus_theater.py --list
"""

from __future__ import annotations

import sys
import json
import time
import argparse
from datetime import datetime, timezone
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# ═══════════════════════════════════════════════════════════════════════════════
# Theater-Definitionen
# ═══════════════════════════════════════════════════════════════════════════════

THEATERS: dict[str, dict] = {

    "MiddleEast": {
        "label":       "Nahost-Konflikt-Theater",
        "icon":        "🌍",
        "description": (
            "Iran und seine Proxy-Netzwerke gegen Israel. Gleichzeitige Fronten "
            "in Gaza (Hamas), Libanon (Hezbollah), Jemen (Houthis) und Syrien. "
            "Iran ist der strategische Antreiber — wenn Teheran eskaliert, "
            "aktivieren sich alle Proxies gleichzeitig."
        ),
        "members": ["Iran", "Israel", "Gaza", "Lebanon", "Yemen", "Syria", "Iraq"],
        "member_weights": {
            "Iran":    0.30,   # strategischer Treiber
            "Israel":  0.20,   # primäres Ziel
            "Gaza":    0.15,   # aktivster Proxy-Frontstaat
            "Lebanon": 0.15,   # Hezbollah-Front
            "Yemen":   0.10,   # Houthi-Drohnen
            "Syria":   0.05,   # Transitroute Iran
            "Iraq":    0.05,   # schiitische Milizen
        },
        "driver_regions":  ["Iran"],
        "primary_targets": ["Israel"],
        "actor_chains": [
            {
                "from":     "Iran",
                "to":       "Gaza",
                "via":      "Hamas",
                "relation": "funds_arms",
                "label":    "Finanzierung & Raketentechnik",
                "risk":     "high",
            },
            {
                "from":     "Iran",
                "to":       "Lebanon",
                "via":      "Hezbollah",
                "relation": "funds_arms",
                "label":    "Waffen, Training, Kommando",
                "risk":     "high",
            },
            {
                "from":     "Iran",
                "to":       "Yemen",
                "via":      "Houthis (Ansar Allah)",
                "relation": "funds_arms",
                "label":    "Drohnen, Marschflugkörper, Ballistische Raketen",
                "risk":     "high",
            },
            {
                "from":     "Iran",
                "to":       "Iraq",
                "via":      "PMF / Kataib Hezbollah",
                "relation": "funds_arms",
                "label":    "Schiitische Milizen, Drohnenangriffe",
                "risk":     "medium",
            },
            {
                "from":     "Iran",
                "to":       "Syria",
                "via":      "IRGC / Quds Force",
                "relation": "transit_support",
                "label":    "Waffentransit nach Libanon/Gaza",
                "risk":     "medium",
            },
            {
                "from":     "Russia",
                "to":       "Iran",
                "via":      "bilateral",
                "relation": "supports",
                "label":    "Drohnen-Technologie (Shahed), Sanktions-Schutz",
                "risk":     "medium",
            },
            {
                "from":     "Gaza",
                "to":       "Israel",
                "via":      "Hamas / PIJ",
                "relation": "attacks",
                "label":    "Raketenangriffe, Tunnelinfrastruktur",
                "risk":     "critical",
            },
            {
                "from":     "Lebanon",
                "to":       "Israel",
                "via":      "Hezbollah",
                "relation": "attacks",
                "label":    "Raketen, Panzerabwehr, Drohnen",
                "risk":     "critical",
            },
            {
                "from":     "Yemen",
                "to":       "Israel",
                "via":      "Houthis",
                "relation": "attacks",
                "label":    "Ballistische Raketen, Drohnenangriffe",
                "risk":     "high",
            },
        ],
        "escalation_triggers": [
            {
                "trigger":     "Iran OSINT > 70",
                "consequence": "Proxy-Aktivierung wahrscheinlich binnen 48-72h",
                "chain":       ["Gaza", "Lebanon", "Yemen"],
            },
            {
                "trigger":     "Israel GEOINT > 60",
                "consequence": "IDF-Mobilisierung / Bodenoperation-Vorbereitung",
                "chain":       ["Gaza", "Lebanon"],
            },
            {
                "trigger":     "Yemen SIGINT > 50",
                "consequence": "Neue Drohnen-/Raketenserie auf Israel/Rotes Meer",
                "chain":       ["Israel"],
            },
        ],
    },

    "EasternEurope": {
        "label":       "Osteuropa-Kriegstheater",
        "icon":        "🌐",
        "description": (
            "Russlands Krieg gegen die Ukraine. Belarus als Transitland. "
            "NATO-Flanke im Baltikum und Polen als potenzielle Ausweitung."
        ),
        "members": ["Ukraine", "Russia", "Belarus"],
        "member_weights": {
            "Ukraine": 0.40,   # Hauptschauplatz
            "Russia":  0.45,   # Angreifer / Treiber
            "Belarus": 0.15,   # Transitland, Unterstützer
        },
        "driver_regions":  ["Russia"],
        "primary_targets": ["Ukraine"],
        "actor_chains": [
            {
                "from":     "Russia",
                "to":       "Ukraine",
                "via":      "VKS / Armee",
                "relation": "attacks",
                "label":    "Raketenangriffe, Bodenangriffe, Drohnen (Shahed)",
                "risk":     "critical",
            },
            {
                "from":     "Russia",
                "to":       "Belarus",
                "via":      "bilateral",
                "relation": "transit_support",
                "label":    "Truppenstationierung, Raketenabschüsse von Belarus",
                "risk":     "high",
            },
            {
                "from":     "Iran",
                "to":       "Russia",
                "via":      "bilateral",
                "relation": "supports",
                "label":    "Shahed-Drohnen, Technologie-Transfer",
                "risk":     "medium",
            },
            {
                "from":     "North Korea",
                "to":       "Russia",
                "via":      "bilateral",
                "relation": "supports",
                "label":    "Artilleriemunition, Truppen",
                "risk":     "medium",
            },
        ],
        "escalation_triggers": [
            {
                "trigger":     "Russia SIGINT > 75",
                "consequence": "Große Raketenoffensive auf ukrainische Infrastruktur",
                "chain":       ["Ukraine"],
            },
            {
                "trigger":     "Belarus GEOINT > 60",
                "consequence": "Truppenbewegungen → mögliche zweite Front",
                "chain":       ["Ukraine"],
            },
        ],
    },

    "AsiaPacific": {
        "label":       "Asien-Pazifik Spannungszone",
        "icon":        "🌏",
        "description": (
            "Taiwan-Straße als zentraler Spannungspunkt. China, Taiwan, USA "
            "und Nord-/Südkorea als Akteure. Verbunden durch die Halbleiter- "
            "und Seehandelsketten."
        ),
        "members": ["China", "Taiwan", "North Korea", "South Korea", "Japan"],
        "member_weights": {
            "China":       0.40,
            "Taiwan":      0.30,
            "North Korea": 0.15,
            "South Korea": 0.10,
            "Japan":       0.05,
        },
        "driver_regions":  ["China", "North Korea"],
        "primary_targets": ["Taiwan", "South Korea"],
        "actor_chains": [
            {
                "from":     "China",
                "to":       "Taiwan",
                "via":      "PLA",
                "relation": "threatens",
                "label":    "Militärübungen, Luftraumverletzungen, Seeblockade-Szenario",
                "risk":     "high",
            },
            {
                "from":     "North Korea",
                "to":       "South Korea",
                "via":      "KPA",
                "relation": "threatens",
                "label":    "Raketentest, DMZ-Provokationen",
                "risk":     "high",
            },
            {
                "from":     "China",
                "to":       "North Korea",
                "via":      "bilateral",
                "relation": "supports",
                "label":    "Wirtschaftliche Unterstützung, diplomatischer Schutz",
                "risk":     "medium",
            },
        ],
        "escalation_triggers": [
            {
                "trigger":     "China SIGINT > 70",
                "consequence": "PLA-Übungen um Taiwan → mögliche Seeblockade",
                "chain":       ["Taiwan"],
            },
            {
                "trigger":     "North Korea OSINT > 65",
                "consequence": "Raketen-/Nukleartest → Eskalation Halbinsel",
                "chain":       ["South Korea", "Japan"],
            },
        ],
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# Hilfsfunktionen
# ═══════════════════════════════════════════════════════════════════════════════

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(v)))


def _level_from_score(score: float) -> tuple[str, str]:
    """Gibt (level_name, emoji) für einen Score 0–100 zurück."""
    if   score >= 81: return "KRITISCH", "⛔"
    elif score >= 61: return "ROT",       "🔴"
    elif score >= 41: return "ORANGE",    "🟠"
    elif score >= 21: return "GELB",      "🟡"
    else:             return "GRUEN",     "🟢"


def _risk_icon(risk: str) -> str:
    return {"critical": "⛔", "high": "🔴", "medium": "🟠",
            "low": "🟡"}.get(risk, "⚪")


def _bar(score: float, width: int = 20) -> str:
    """ASCII-Balken: ████░░░░░░"""
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled)


# ═══════════════════════════════════════════════════════════════════════════════
# Kern-Berechnung
# ═══════════════════════════════════════════════════════════════════════════════

def compute_theater(
    theater_name: str,
    depts:        Optional[list[str]] = None,
    parallel:     bool = True,
    timeout:      int  = 120,
) -> dict:
    """
    Berechnet alle Department-Scores für alle Mitglieder eines Theaters
    und gibt ein vollständiges Theater-Bild zurück.

    Parameters
    ----------
    theater_name : z.B. "MiddleEast", "EasternEurope", "AsiaPacific"
    depts        : Nur diese Abteilungen berechnen (None = alle)
    parallel     : Regionen parallel berechnen (empfohlen)
    timeout      : Max. Sekunden pro Region

    Returns
    -------
    {
      theater_name, label, icon, description,
      theater_score, theater_level, theater_icon,
      members: {region: dept_result, ...},
      member_scores: {region: score, ...},
      actor_chains: [...],
      active_chains: [...],          # Ketten mit hohem Risiko
      correlation_alerts: [...],     # Querverbindungen erkannt
      driver_status: {...},          # Status der Treiber-Regionen
      escalation_warnings: [...],    # ausgelöste Trigger
      timestamp: "..."
    }

    Raises
    ------
    ValueError : Unbekanntes Theater
    """
    theater_name = _normalize_theater_name(theater_name)
    if theater_name not in THEATERS:
        available = ", ".join(THEATERS.keys())
        raise ValueError(
            f"Unbekanntes Theater: '{theater_name}'. "
            f"Verfügbare Theater: {available}"
        )

    theater = THEATERS[theater_name]
    members  = theater["members"]
    weights  = theater.get("member_weights", {})

    # ── Alle Regionen parallel bewerten ──────────────────────────────────────
    try:
        from nexus_departments import compute_department_scores
    except ImportError as e:
        raise ImportError(
            f"nexus_departments.py nicht gefunden: {e}. "
            "Bitte sicherstellen, dass die Datei im NEXUS-Verzeichnis liegt."
        )

    member_results: dict[str, dict] = {}

    if parallel and len(members) > 1:
        with ThreadPoolExecutor(max_workers=min(8, len(members))) as ex:
            fut_map = {
                ex.submit(compute_department_scores, m, depts, True, timeout): m
                for m in members
            }
            for fut in as_completed(fut_map, timeout=timeout + 30):
                region = fut_map[fut]
                try:
                    member_results[region] = fut.result(timeout=timeout + 15)
                except Exception as exc:
                    # Fehler für diese Region, aber Theater läuft weiter
                    member_results[region] = {
                        "region":       region,
                        "master_score": 0.0,
                        "master_level": "GRUEN",
                        "master_icon":  "🟢",
                        "departments":  {},
                        "error":        str(exc),
                        "timestamp":    _ts(),
                    }
        # Timeouts auffüllen
        for m in members:
            if m not in member_results:
                member_results[m] = {
                    "region":       m,
                    "master_score": 0.0,
                    "master_level": "GRUEN",
                    "master_icon":  "🟢",
                    "departments":  {},
                    "error":        "timeout",
                    "timestamp":    _ts(),
                }
    else:
        for m in members:
            try:
                member_results[m] = compute_department_scores(m, depts, True, timeout)
            except Exception as exc:
                member_results[m] = {
                    "region": m, "master_score": 0.0,
                    "error": str(exc), "timestamp": _ts(),
                }

    # ── Member-Scores extrahieren ─────────────────────────────────────────────
    member_scores: dict[str, float] = {
        m: member_results[m].get("master_score", 0.0)
        for m in members
    }

    # ── Theater-Score berechnen ───────────────────────────────────────────────
    # Gewichteter Durchschnitt der Mitglieder
    total_w   = sum(weights.get(m, 1.0 / len(members)) for m in members)
    weighted  = sum(
        member_scores[m] * weights.get(m, 1.0 / len(members))
        for m in members
    )
    base_score = _clamp(weighted / total_w) if total_w > 0 else 0.0

    # Korrelations-Boost: wenn 3+ Mitglieder über 40 → Theater-Score +5
    hot_members = [m for m in members if member_scores.get(m, 0) >= 40]
    corr_boost  = min(10.0, len(hot_members) * 2.5) if len(hot_members) >= 3 else 0.0
    theater_score = _clamp(base_score + corr_boost)

    theater_level, theater_icon = _level_from_score(theater_score)

    # ── Aktive Ketten erkennen ────────────────────────────────────────────────
    active_chains = _detect_active_chains(
        theater["actor_chains"], member_scores
    )

    # ── Korrelations-Alerts ───────────────────────────────────────────────────
    correlation_alerts = _detect_correlations(
        theater, member_scores, member_results
    )

    # ── Eskalations-Trigger prüfen ────────────────────────────────────────────
    escalation_warnings = _check_triggers(
        theater.get("escalation_triggers", []),
        member_results,
        member_scores,
    )

    # ── Driver-Status ─────────────────────────────────────────────────────────
    driver_status = {}
    for dr in theater.get("driver_regions", []):
        score = member_scores.get(dr, 0.0)
        level, icon = _level_from_score(score)
        driver_status[dr] = {
            "score":   score,
            "level":   level,
            "icon":    icon,
            "proxies": _get_proxies_for_driver(dr, theater["actor_chains"]),
        }

    return {
        "theater_name":        theater_name,
        "label":               theater["label"],
        "icon":                theater["icon"],
        "description":         theater["description"],
        "theater_score":       round(theater_score, 1),
        "theater_level":       theater_level,
        "theater_icon":        theater_icon,
        "base_score":          round(base_score, 1),
        "correlation_boost":   round(corr_boost, 1),
        "members":             member_results,
        "member_scores":       {m: round(v, 1) for m, v in member_scores.items()},
        "member_levels":       {m: _level_from_score(v)[0] for m, v in member_scores.items()},
        "actor_chains":        theater["actor_chains"],
        "active_chains":       active_chains,
        "correlation_alerts":  correlation_alerts,
        "driver_status":       driver_status,
        "escalation_warnings": escalation_warnings,
        "hot_members":         hot_members,
        "depts_queried":       depts,
        "timestamp":           _ts(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Analyse-Hilfsfunktionen
# ═══════════════════════════════════════════════════════════════════════════════

def _detect_active_chains(
    chains: list[dict],
    member_scores: dict[str, float],
) -> list[dict]:
    """
    Eine Kette gilt als "aktiv/alarmierend" wenn:
    - from-Region Score > 45  (Treiber ist heiß)
    - ODER to-Region Score > 45  (Ziel-Region ist heiß)
    - UND die Relation kein "supports" ist
    """
    active = []
    for chain in chains:
        src   = chain.get("from", "")
        dst   = chain.get("to",   "")
        rel   = chain.get("relation", "")
        src_s = member_scores.get(src, 0.0)
        dst_s = member_scores.get(dst, 0.0)

        is_hot = (src_s >= 45 or dst_s >= 45)
        is_hostile = rel in ("attacks", "funds_arms", "threatens")

        if is_hot and is_hostile:
            active.append({
                **chain,
                "from_score": round(src_s, 1),
                "to_score":   round(dst_s, 1),
                "alert":      "HOCH" if (src_s >= 60 or dst_s >= 60) else "MITTEL",
            })

    # Sortiert nach Risiko
    risk_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    active.sort(key=lambda c: risk_order.get(c.get("risk", "low"), 3))
    return active


def _detect_correlations(
    theater: dict,
    member_scores: dict[str, float],
    member_results: dict[str, dict],
) -> list[dict]:
    """
    Erkennt auffällige Muster über Regionen hinweg.
    Beispiel: Iran OSINT hoch + Hezbollah-Region (Libanon) noch ruhig
    = "Pre-Activation" Muster.
    """
    alerts = []
    chains   = theater.get("actor_chains", [])
    drivers  = theater.get("driver_regions", [])
    targets  = theater.get("primary_targets", [])

    # Muster 1: Treiber heiß, Proxy noch ruhig → Pre-Activation
    for dr in drivers:
        dr_score = member_scores.get(dr, 0.0)
        if dr_score >= 55:
            for chain in chains:
                if chain.get("from") == dr and chain.get("relation") == "funds_arms":
                    proxy_region = chain.get("to", "")
                    proxy_score  = member_scores.get(proxy_region, 0.0)
                    if proxy_score < 35:
                        alerts.append({
                            "type":    "pre_activation",
                            "driver":  dr,
                            "proxy":   proxy_region,
                            "via":     chain.get("via", "?"),
                            "message": (
                                f"{dr} (Score {dr_score:.0f}) ist erhöht, "
                                f"{proxy_region} noch ruhig (Score {proxy_score:.0f}). "
                                f"Typisches Muster vor Proxy-Aktivierung via {chain.get('via','?')}."
                            ),
                            "severity": "warning",
                        })

    # Muster 2: Mehrere Proxies gleichzeitig heiß → koordinierter Angriff
    hot_proxies = []
    for chain in chains:
        src = chain.get("from", "")
        dst = chain.get("to",   "")
        if src in drivers and chain.get("relation") == "funds_arms":
            if member_scores.get(dst, 0.0) >= 50:
                hot_proxies.append(dst)
    if len(hot_proxies) >= 2:
        alerts.append({
            "type":    "multi_front",
            "regions": hot_proxies,
            "message": (
                f"Mehrere Proxy-Regionen gleichzeitig erhöht: "
                f"{', '.join(hot_proxies)}. "
                "Muster deutet auf koordinierten Multi-Front-Angriff hin."
            ),
            "severity": "critical",
        })

    # Muster 3: Primäres Ziel heiß ABER Treiber noch ruhig → reaktiver Konflikt
    for tgt in targets:
        tgt_score = member_scores.get(tgt, 0.0)
        all_drivers_cool = all(member_scores.get(d, 0.0) < 30 for d in drivers)
        if tgt_score >= 60 and all_drivers_cool:
            alerts.append({
                "type":    "reactive_escalation",
                "target":  tgt,
                "message": (
                    f"{tgt} (Score {tgt_score:.0f}) eskaliert stark, "
                    "Treiber-Regionen aber ruhig. "
                    "Muster: unilaterale Eskalation oder unabhängige Aktion."
                ),
                "severity": "info",
            })

    return alerts


def _check_triggers(
    triggers: list[dict],
    member_results: dict[str, dict],
    member_scores: dict[str, float],
) -> list[dict]:
    """
    Prüft vordefinierte Eskalations-Trigger.
    Trigger-Format: "Region DEPT > WERT" oder "Region OSINT > 70"
    """
    warnings = []
    for t in triggers:
        trigger_str = t.get("trigger", "")
        fired = _eval_trigger(trigger_str, member_results, member_scores)
        if fired:
            warnings.append({
                "trigger":     trigger_str,
                "consequence": t.get("consequence", ""),
                "chain":       t.get("chain", []),
                "fired":       True,
            })
    return warnings


def _eval_trigger(
    trigger: str,
    member_results: dict[str, dict],
    member_scores: dict[str, float],
) -> bool:
    """
    Wertet einen Trigger-String aus.
    Format: "Region DEPT > N" oder "Region OSINT > N"
    Beispiel: "Iran OSINT > 70", "Israel GEOINT > 60"
    """
    import re
    m = re.match(
        r"(\w+)\s+(OSINT|GEOINT|SIGINT|HUMINT|ECONINT|HUMANA|[A-Z]+)\s*([><=]+)\s*(\d+)",
        trigger.strip(),
    )
    if m:
        region, dept, op, val_str = m.groups()
        val = float(val_str)
        dept_upper = dept.upper()

        # Dept-Score aus den Ergebnissen holen
        region_result = member_results.get(region, {})
        depts_map     = region_result.get("departments", {})
        dept_result   = depts_map.get(dept_upper, {})
        actual        = float(dept_result.get("score", 0.0))

        if   op == ">":  return actual > val
        elif op == ">=": return actual >= val
        elif op == "<":  return actual < val
        elif op == "<=": return actual <= val
        elif op == "==": return actual == val
        return False

    # Fallback: nur Region + Score
    m2 = re.match(r"(\w+)\s*([><=]+)\s*(\d+)", trigger.strip())
    if m2:
        region, op, val_str = m2.groups()
        val    = float(val_str)
        actual = float(member_scores.get(region, 0.0))
        if   op == ">":  return actual > val
        elif op == ">=": return actual >= val
        elif op == "<":  return actual < val
        elif op == "<=": return actual <= val
    return False


def _get_proxies_for_driver(driver: str, chains: list[dict]) -> list[str]:
    """Gibt alle Regionen zurück, die der Driver via funds_arms/supports beliefert."""
    return [
        c.get("to", "")
        for c in chains
        if c.get("from") == driver
        and c.get("relation") in ("funds_arms", "supports", "transit_support")
        and c.get("to", "")
    ]


def _normalize_theater_name(name: str) -> str:
    """Normalisiert Groß-/Kleinschreibung: 'middleeast' → 'MiddleEast'"""
    lower_map = {k.lower(): k for k in THEATERS}
    return lower_map.get(name.lower(), name)


# ═══════════════════════════════════════════════════════════════════════════════
# Formatierter Report
# ═══════════════════════════════════════════════════════════════════════════════

# ANSI-Farben
_USE_COLOR = sys.stdout.isatty()

_COLORS = {
    "reset":    "\033[0m",
    "bold":     "\033[1m",
    "dim":      "\033[2m",
    "red":      "\033[31m",
    "orange":   "\033[33m",
    "yellow":   "\033[33m",
    "green":    "\033[32m",
    "blue":     "\033[34m",
    "cyan":     "\033[36m",
    "magenta":  "\033[35m",
    "white":    "\033[97m",
    "bright_red":    "\033[91m",
    "bright_green":  "\033[92m",
    "bright_yellow": "\033[93m",
    "bright_blue":   "\033[94m",
    "bright_cyan":   "\033[96m",
}

_LEVEL_COLORS = {
    "KRITISCH": "bright_red",
    "ROT":      "red",
    "ORANGE":   "orange",
    "GELB":     "bright_yellow",
    "GRUEN":    "bright_green",
}


def _c(style: str, text: str, use_color: Optional[bool] = None) -> str:
    use = _USE_COLOR if use_color is None else use_color
    if not use:
        return text
    col = _COLORS.get(style, "")
    return f"{col}{text}{_COLORS['reset']}" if col else text


def _score_color(score: float, use_color: bool = True) -> str:
    if not use_color:
        return ""
    if   score >= 81: return _COLORS["bright_red"]
    elif score >= 61: return _COLORS["red"]
    elif score >= 41: return _COLORS["orange"]
    elif score >= 21: return _COLORS["bright_yellow"]
    else:             return _COLORS["bright_green"]


def format_theater_report(
    result:    dict,
    compact:   bool = False,
    use_color: bool = True,
) -> str:
    """
    Gibt einen vollständigen Theater-Report als formatierten String zurück.

    Parameters
    ----------
    result    : Rückgabe von compute_theater()
    compact   : True = nur Übersicht ohne Akteur-Details
    use_color : True = ANSI-Farben

    Returns
    -------
    Formatierter Multi-Zeilen String
    """
    global _USE_COLOR
    _USE_COLOR = use_color

    lines = []
    W = 65   # Breite der Ausgabe

    def sep(char: str = "═") -> str:
        return _c("dim", "  " + char * W, use_color)

    def line(txt: str = "") -> None:
        lines.append(txt)

    # ── Header ────────────────────────────────────────────────────────────────
    line()
    line(sep())
    theater_name = result.get("theater_name", "?")
    label        = result.get("label", theater_name)
    icon         = result.get("icon", "🌍")
    line(_c("bold", f"  {icon}  NEXUS · THEATER · {theater_name.upper()}", use_color))
    line(_c("dim",  f"     {label}", use_color))
    line(sep())

    # ── Theater-Score ─────────────────────────────────────────────────────────
    t_score = result.get("theater_score", 0.0)
    t_level = result.get("theater_level", "GRUEN")
    t_icon  = result.get("theater_icon", "🟢")
    boost   = result.get("correlation_boost", 0.0)
    bar     = _bar(t_score, 22)
    col     = _score_color(t_score, use_color)
    rst     = _COLORS["reset"] if use_color else ""

    line()
    line(f"  {col}{t_icon}  THEATER-SCORE  {bar}  {t_score:.0f}/100  [{t_level}]{rst}")
    if boost > 0:
        line(_c("dim",
            f"     Basiswert {result.get('base_score',0):.0f} + "
            f"Korrelations-Boost +{boost:.1f} "
            f"({len(result.get('hot_members',[]))} heiße Regionen)", use_color))
    line()

    # ── Mitglieder-Übersicht ──────────────────────────────────────────────────
    line(_c("bold", "  MITGLIEDER-STATUS", use_color))
    line(sep("─"))

    member_scores = result.get("member_scores", {})
    member_levels = result.get("member_levels", {})
    theater       = THEATERS.get(result.get("theater_name", ""), {})
    weights       = theater.get("member_weights", {})
    drivers       = theater.get("driver_regions", [])
    targets       = theater.get("primary_targets", [])

    for region in result.get("members", {}).keys():
        score  = member_scores.get(region, 0.0)
        level  = member_levels.get(region, "GRUEN")
        _, lic = _level_from_score(score)
        bar_m  = _bar(score, 14)
        col_m  = _score_color(score, use_color)
        rst    = _COLORS["reset"] if use_color else ""
        w_pct  = int(weights.get(region, 0) * 100)

        role_tag = ""
        if region in drivers:  role_tag = _c("bright_red", " [TREIBER]", use_color)
        elif region in targets: role_tag = _c("cyan", " [ZIEL]", use_color)

        errors = result["members"][region].get("error", "")
        err_tag = _c("dim", " ⚠ timeout/error", use_color) if errors else ""

        line(
            f"  {col_m}{lic}  {region:<14}{bar_m}  {score:5.1f}  "
            f"w:{w_pct:2d}%{rst}{role_tag}{err_tag}"
        )

    line()

    # ── Akteur-Ketten ─────────────────────────────────────────────────────────
    if not compact:
        line(_c("bold", "  AKTEUR-KETTEN", use_color))
        line(sep("─"))

        for chain in result.get("actor_chains", []):
            src   = chain.get("from", "?")
            dst   = chain.get("to",   "?")
            via   = chain.get("via",  "?")
            rel   = chain.get("relation", "?")
            label_c = chain.get("label", "")
            risk  = chain.get("risk", "low")
            r_icon = _risk_icon(risk)

            # Pfeil-Typ je nach Relation
            arrow = {"attacks": "──ANGRIFF──▶", "funds_arms": "──VERSORGUNG──▶",
                     "transit_support": "──TRANSIT──▶",
                     "supports": "──SUPPORT──▶",
                     "threatens": "──DROHUNG──▶"}.get(rel, "──────▶")

            src_score = member_scores.get(src, -1)
            dst_score = member_scores.get(dst, -1)
            src_tag = f"({src_score:.0f})" if src_score >= 0 else ""
            dst_tag = f"({dst_score:.0f})" if dst_score >= 0 else ""

            line(
                f"  {r_icon}  {_c('bold', src, use_color)}{src_tag} "
                f"{arrow} "
                f"{_c('bold', dst, use_color)}{dst_tag}"
            )
            if via:
                line(_c("dim", f"       via {via}", use_color))
            if label_c:
                line(_c("dim", f"       {label_c}", use_color))

        line()

    # ── Aktive Ketten (alarmierend) ───────────────────────────────────────────
    active = result.get("active_chains", [])
    if active:
        line(_c("bold", f"  ⚠  AKTIVE KETTEN ({len(active)} erhöht)", use_color))
        line(sep("─"))
        for chain in active:
            alert = chain.get("alert", "MITTEL")
            col_a = _COLORS["bright_red"] if alert == "HOCH" else _COLORS["orange"]
            rst   = _COLORS["reset"] if use_color else ""
            if not use_color: col_a = rst = ""

            line(
                f"  {col_a}{'⛔' if alert=='HOCH' else '🟠'} "
                f"{chain.get('from','?')}({chain.get('from_score',0):.0f}) "
                f"→ {chain.get('to','?')}({chain.get('to_score',0):.0f}) "
                f"via {chain.get('via','?')}{rst}"
            )
        line()

    # ── Korrelations-Alerts ───────────────────────────────────────────────────
    corr = result.get("correlation_alerts", [])
    if corr:
        line(_c("bold", f"  🔗  KORRELATIONS-MUSTER ({len(corr)})", use_color))
        line(sep("─"))
        sev_icons = {"critical": "⛔", "warning": "⚠️", "info": "ℹ️"}
        for alert in corr:
            icon_a = sev_icons.get(alert.get("severity", "info"), "ℹ️")
            line(f"  {icon_a}  {alert.get('message','')}")
        line()

    # ── Eskalations-Warnungen ─────────────────────────────────────────────────
    ews = result.get("escalation_warnings", [])
    if ews:
        line(_c("bold", f"  🚨  ESKALATIONS-TRIGGER AUSGELÖST ({len(ews)})", use_color))
        line(sep("─"))
        for ew in ews:
            line(_c("bright_red", f"  ⛔  [{ew.get('trigger','')}]", use_color))
            line(f"       → {ew.get('consequence','')}")
            if ew.get("chain"):
                line(_c("dim", f"       Betroffene Regionen: {', '.join(ew['chain'])}", use_color))
        line()

    # ── Treiber-Status ────────────────────────────────────────────────────────
    ds = result.get("driver_status", {})
    if ds and not compact:
        line(_c("bold", "  🎯  TREIBER-STATUS", use_color))
        line(sep("─"))
        for dr, info in ds.items():
            s = info.get("score", 0.0)
            _, lic2 = _level_from_score(s)
            proxies = info.get("proxies", [])
            col_d = _score_color(s, use_color)
            rst   = _COLORS["reset"] if use_color else ""
            line(f"  {col_d}{lic2}  {dr}: {s:.0f}/100{rst}")
            if proxies:
                line(_c("dim", f"       Proxies: {', '.join(proxies)}", use_color))
        line()

    # ── Department-Heatmap ────────────────────────────────────────────────────
    if not compact:
        line(_c("bold", "  📊  DEPARTMENT-HEATMAP", use_color))
        line(sep("─"))
        dept_names = ["OSINT", "GEOINT", "SIGINT", "HUMINT", "ECONINT", "HUMANA"]
        dept_icons = {"OSINT":"⚡","GEOINT":"🛰","SIGINT":"📡",
                      "HUMINT":"👤","ECONINT":"📊","HUMANA":"🏥"}

        # Header-Zeile
        header = "  " + " " * 14
        for dn in dept_names:
            header += f"{dept_icons.get(dn,'')+dn:<11}"
        line(_c("dim", header, use_color))

        for region in result.get("members", {}).keys():
            row_str = f"  {region:<14}"
            region_result = result["members"][region]
            depts_map     = region_result.get("departments", {})
            for dn in dept_names:
                dept_r = depts_map.get(dn, {})
                ds_val = dept_r.get("score", -1.0) if dept_r else -1.0
                if ds_val < 0:
                    row_str += _c("dim", f"{'—':<11}", use_color)
                else:
                    col_d = _score_color(ds_val, use_color)
                    rst   = _COLORS["reset"] if use_color else ""
                    row_str += f"{col_d}{ds_val:5.0f}{rst}      "
            line(row_str)
        line()

    # ── Footer ────────────────────────────────────────────────────────────────
    line(sep())
    ts = result.get("timestamp", _ts())
    line(_c("dim",
        f"  Theater: {result.get('theater_name','')} · "
        f"Regionen: {len(result.get('member_scores',{}))} · "
        f"Zeitstempel: {ts}", use_color))
    line()

    return "\n".join(lines)


def theater_brief(result: dict) -> str:
    """Einzeilige Zusammenfassung für Logs."""
    name  = result.get("theater_name", "?")
    score = result.get("theater_score", 0.0)
    level = result.get("theater_level", "?")
    icon  = result.get("theater_icon", "")
    hot   = result.get("hot_members", [])
    ews   = len(result.get("escalation_warnings", []))
    chains= len(result.get("active_chains", []))

    parts = [f"THEATER {name} {icon} {score:.0f}/100 [{level}]"]
    if hot:
        parts.append(f"Heiße Regionen: {', '.join(hot)}")
    if chains:
        parts.append(f"{chains} aktive Ketten")
    if ews:
        parts.append(f"{ews} Trigger ausgelöst")
    return " | ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# Öffentliche Hilfsfunktionen
# ═══════════════════════════════════════════════════════════════════════════════

def list_theaters(use_color: bool = True) -> None:
    """Gibt alle verfügbaren Theater mit Beschreibung aus."""
    print()
    for name, t in THEATERS.items():
        members = ", ".join(t.get("members", []))
        drivers = ", ".join(t.get("driver_regions", []))
        targets = ", ".join(t.get("primary_targets", []))
        print(f"  {t['icon']}  {_c('bold', name, use_color)}")
        print(f"     {t['label']}")
        print(_c("dim", f"     Mitglieder: {members}", use_color))
        print(_c("dim", f"     Treiber:    {drivers}", use_color))
        print(_c("dim", f"     Ziele:      {targets}", use_color))
        print()


def get_theater_names() -> list[str]:
    """Gibt alle verfügbaren Theater-Namen zurück."""
    return list(THEATERS.keys())


# ═══════════════════════════════════════════════════════════════════════════════
# Spinner
# ═══════════════════════════════════════════════════════════════════════════════

def _spinner_thread(msg: str, done_event) -> None:
    import threading
    if not sys.stdout.isatty():
        return
    chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = 0
    while not done_event.is_set():
        print(f"\r  {chars[i % len(chars)]} {msg}...", end="", flush=True)
        i += 1
        time.sleep(0.12)
    print(f"\r  ✓ {msg} abgeschlossen" + " " * 30)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    ap = argparse.ArgumentParser(
        prog="nexus_theater",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    ap.add_argument(
        "--theater", "-t",
        default="MiddleEast",
        metavar="NAME",
        help=(
            "Theater-Name (default: MiddleEast). "
            "Verfügbar: " + ", ".join(THEATERS.keys())
        ),
    )
    ap.add_argument(
        "--dept", "-d",
        nargs="*",
        metavar="DEPT",
        help="Nur diese Abteilungen berechnen: OSINT GEOINT SIGINT HUMINT ECONINT HUMANA",
    )
    ap.add_argument(
        "--json", "-j",
        action="store_true",
        help="JSON-Ausgabe (kein Farbcode, kein Spinner)",
    )
    ap.add_argument(
        "--compact", "-c",
        action="store_true",
        help="Kompakte Ausgabe ohne Akteur-Ketten-Details",
    )
    ap.add_argument(
        "--seq", "-s",
        action="store_true",
        help="Sequentiell statt parallel (Debugging)",
    )
    ap.add_argument(
        "--no-color",
        action="store_true",
        help="Keine ANSI-Farben",
    )
    ap.add_argument(
        "--list", "-l",
        action="store_true",
        help="Alle verfügbaren Theater auflisten und beenden",
    )
    ap.add_argument(
        "--timeout",
        type=int,
        default=120,
        metavar="SEC",
        help="Timeout in Sekunden pro Region (default: 120)",
    )

    args = ap.parse_args()

    use_color = not (args.no_color or args.json)

    if args.list:
        list_theaters(use_color=use_color)
        sys.exit(0)

    theater_name = _normalize_theater_name(args.theater)
    if theater_name not in THEATERS:
        print(
            f"✗ Unbekanntes Theater: '{args.theater}'. "
            f"Verfügbar: {', '.join(THEATERS.keys())}",
            file=sys.stderr,
        )
        sys.exit(1)

    depts = None
    if args.dept:
        normalized = [d.upper() for d in args.dept]
        if "ALL" not in normalized:
            depts = normalized

    # Spinner
    if not args.json and use_color:
        import threading
        done = threading.Event()
        t = threading.Thread(
            target=_spinner_thread,
            args=(
                f"Berechne Theater {theater_name} "
                f"({len(THEATERS[theater_name]['members'])} Regionen)",
                done,
            ),
            daemon=True,
        )
        t.start()
    else:
        done = None

    t0 = time.time()
    try:
        result = compute_theater(
            theater_name=theater_name,
            depts=depts,
            parallel=not args.seq,
            timeout=args.timeout,
        )
    except Exception as exc:
        if done: done.set()
        print(f"\n✗ Fehler: {exc}", file=sys.stderr)
        sys.exit(1)

    elapsed = round(time.time() - t0, 1)
    if done: done.set()

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        sys.exit(0)

    print(format_theater_report(result, compact=args.compact, use_color=use_color))
    print(_c("dim",
        f"  Berechnet in {elapsed}s | Parallel: {not args.seq} | "
        f"Regionen: {len(result.get('member_scores', {}))}",
        use_color))
    print()
    print(_c("dim", f"  {theater_brief(result)}", use_color))
    print()


if __name__ == "__main__":
    main()
