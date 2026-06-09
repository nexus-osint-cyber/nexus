"""
NEXUS – Cross-Theater-Korrelation
===================================
Erkennt Verbindungen ZWISCHEN Theatern.

Warum?
------
Iran ist sowohl Treiber im Nahost-Theater (→ Hamas, Hezbollah, Houthis)
als auch Lieferant im Osteuropa-Theater (→ Russland: Shahed-Drohnen).
Nordkorea liefert Artilleriemunition und Truppen an Russland
(AsiaPacific ↔ EasternEurope).

Wenn Iran eskaliert → Nahost-Score steigt UND EasternEurope bekommt
einen Korrelations-Boost, weil derselbe Akteur beide Theater beeinflusst.

Kernfunktionen:
  compute_cross_theater(theaters=None)
    → Scores aller Theater + Cross-Korrelations-Matrix + gemeinsame Akteure

  cross_theater_brief(result)
    → Einzeilige Log-Zusammenfassung

  format_cross_report(result)
    → Formatierter ANSI-Report

CLI:
  python nexus_crosstheater.py
  python nexus_crosstheater.py --json
  python nexus_crosstheater.py --brief
"""

from __future__ import annotations

import json
import sys
import argparse
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════════
# Cross-Theater-Verbindungen (explizit definiert)
# ═══════════════════════════════════════════════════════════════════════════════

# Akteure die in MEHREREN Theatern auftreten
CROSS_ACTORS: dict[str, dict] = {
    "Iran": {
        "appears_in": ["MiddleEast", "EasternEurope"],
        "roles": {
            "MiddleEast":   "driver",      # Treiber: finanziert Hamas/Hezbollah/Houthis
            "EasternEurope": "supplier",   # Lieferant: Shahed-Drohnen an Russland
        },
        "supply_chains": [
            {
                "from_theater": "MiddleEast",
                "to_theater":   "EasternEurope",
                "item":         "Shahed-136/131 Drohnen",
                "note":         "Selbe Produktionslinie wie für Houthi-Drohnen",
                "risk":         "high",
            },
        ],
        "correlation_weight": 0.35,  # Wie stark Iran-Eskalation auf andere Theater wirkt
    },
    "Russia": {
        "appears_in": ["EasternEurope", "MiddleEast"],
        "roles": {
            "EasternEurope": "driver",
            "MiddleEast":    "supporter",  # UN-Veto-Schutz für Iran, diplomatisch
        },
        "supply_chains": [
            {
                "from_theater": "EasternEurope",
                "to_theater":   "MiddleEast",
                "item":         "Diplomatischer Schutz, Waffentechnologie",
                "note":         "Russland blockiert UN-Resolutionen gegen Iran",
                "risk":         "medium",
            },
        ],
        "correlation_weight": 0.20,
    },
    "North Korea": {
        "appears_in": ["AsiaPacific", "EasternEurope"],
        "roles": {
            "AsiaPacific":   "driver",
            "EasternEurope": "supplier",   # Artilleriemunition + Truppen an Russland
        },
        "supply_chains": [
            {
                "from_theater": "AsiaPacific",
                "to_theater":   "EasternEurope",
                "item":         "Artilleriemunition 152mm, KN-25 Raketen, Truppen",
                "note":         "Bis zu 1 Mio. Granaten + 10.000+ Soldaten",
                "risk":         "high",
            },
        ],
        "correlation_weight": 0.25,
    },
    "China": {
        "appears_in": ["AsiaPacific", "EasternEurope"],
        "roles": {
            "AsiaPacific":   "driver",
            "EasternEurope": "enabler",    # Dual-Use-Güter, diplomatischer Schutz
        },
        "supply_chains": [
            {
                "from_theater": "AsiaPacific",
                "to_theater":   "EasternEurope",
                "item":         "Dual-Use-Güter, Halbleiter, Maschinenteile",
                "note":         "Umgehung westlicher Exportkontrollen über Drittländer",
                "risk":         "medium",
            },
        ],
        "correlation_weight": 0.15,
    },
    "USA": {
        "appears_in": ["MiddleEast", "EasternEurope", "AsiaPacific"],
        "roles": {
            "MiddleEast":    "supporter",  # Israel-Support
            "EasternEurope": "supporter",  # Ukraine-Support
            "AsiaPacific":   "deterrent",  # Taiwan-Verteidigung
        },
        "supply_chains": [],
        "correlation_weight": 0.10,
    },
}

# Theater-zu-Theater Korrelationsmatrix (manuell kalibriert)
# Wert = wie stark ein Theater-Score-Anstieg auf das andere wirkt (0–1)
THEATER_CORRELATION_MATRIX: dict[tuple[str, str], float] = {
    ("MiddleEast",   "EasternEurope"): 0.25,  # Iran → Russland
    ("EasternEurope","MiddleEast"):    0.15,  # Russland → Iran diplomatisch
    ("AsiaPacific",  "EasternEurope"): 0.20,  # NK → Russland Munition
    ("EasternEurope","AsiaPacific"):   0.10,  # Russland-Erfolge ermutigen NK
    ("MiddleEast",   "AsiaPacific"):   0.05,
    ("AsiaPacific",  "MiddleEast"):    0.05,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Hilfsfunktionen
# ═══════════════════════════════════════════════════════════════════════════════

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(v)))


def _level(score: float) -> tuple[str, str]:
    if   score >= 81: return "KRITISCH", "⛔"
    elif score >= 61: return "ROT",       "🔴"
    elif score >= 41: return "ORANGE",    "🟠"
    elif score >= 21: return "GELB",      "🟡"
    else:             return "GRUEN",     "🟢"


# ═══════════════════════════════════════════════════════════════════════════════
# Kern-Berechnung
# ═══════════════════════════════════════════════════════════════════════════════

def compute_cross_theater(
    theaters: Optional[list[str]] = None,
    depts:    Optional[list[str]] = None,
    parallel: bool = True,
    timeout:  int  = 120,
) -> dict:
    """
    Berechnet alle Theater + Cross-Theater-Korrelation.

    Parameters
    ----------
    theaters : Theater-Namen (None = alle)
    depts    : Abteilungs-Filter
    parallel : Parallel berechnen
    timeout  : Timeout pro Theater

    Returns
    -------
    {
      theaters:        {name: theater_result},
      theater_scores:  {name: score},
      cross_scores:    {name: score_nach_korrelation},
      cross_links:     [{from, to, actor, supply, boost, risk}],
      active_actors:   [{actor, theaters, current_role, risk}],
      global_score:    float,
      global_level:    str,
      timestamp:       str,
    }
    """
    try:
        from nexus_theater import THEATERS, compute_theater
    except ImportError as e:
        raise ImportError(f"nexus_theater.py nicht gefunden: {e}")

    target_theaters = theaters or list(THEATERS.keys())
    target_theaters = [t for t in target_theaters if t in THEATERS]

    # ── Alle Theater parallel berechnen ──────────────────────────────────────
    theater_results: dict[str, dict] = {}

    if parallel and len(target_theaters) > 1:
        with ThreadPoolExecutor(max_workers=len(target_theaters)) as ex:
            fut_map = {
                ex.submit(compute_theater, tn, depts, True, timeout): tn
                for tn in target_theaters
            }
            for fut in as_completed(fut_map, timeout=timeout + 30):
                tn = fut_map[fut]
                try:
                    theater_results[tn] = fut.result(timeout=timeout + 10)
                except Exception as exc:
                    theater_results[tn] = {
                        "theater_name":  tn,
                        "theater_score": 0.0,
                        "error": str(exc),
                        "timestamp": _ts(),
                    }
        for tn in target_theaters:
            if tn not in theater_results:
                theater_results[tn] = {
                    "theater_name": tn, "theater_score": 0.0,
                    "error": "timeout", "timestamp": _ts(),
                }
    else:
        for tn in target_theaters:
            try:
                theater_results[tn] = compute_theater(tn, depts, True, timeout)
            except Exception as exc:
                theater_results[tn] = {
                    "theater_name": tn, "theater_score": 0.0,
                    "error": str(exc), "timestamp": _ts(),
                }

    # ── Basis-Scores ──────────────────────────────────────────────────────────
    theater_scores: dict[str, float] = {
        tn: theater_results[tn].get("theater_score", 0.0)
        for tn in target_theaters
    }

    # ── Cross-Korrelations-Boost ──────────────────────────────────────────────
    # Für jedes Theater: addiere Boost aus anderen Theatern via Korrelationsmatrix
    cross_boosts: dict[str, float] = {tn: 0.0 for tn in target_theaters}

    for (src_t, dst_t), corr in THEATER_CORRELATION_MATRIX.items():
        if src_t not in theater_scores or dst_t not in theater_scores:
            continue
        src_score = theater_scores[src_t]
        # Boost nur wenn Quell-Theater erhöht ist (> 30)
        if src_score > 30:
            boost = _clamp((src_score - 30) * corr * 0.5)
            cross_boosts[dst_t] = cross_boosts.get(dst_t, 0.0) + boost

    cross_scores: dict[str, float] = {
        tn: _clamp(theater_scores[tn] + cross_boosts.get(tn, 0.0))
        for tn in target_theaters
    }

    # ── Cross-Links (aktive Versorgungsketten) ────────────────────────────────
    cross_links = []
    for actor_name, actor in CROSS_ACTORS.items():
        for sc in actor.get("supply_chains", []):
            src_t = sc["from_theater"]
            dst_t = sc["to_theater"]
            if src_t not in theater_scores or dst_t not in theater_scores:
                continue
            src_score = theater_scores.get(src_t, 0.0)
            dst_score = theater_scores.get(dst_t, 0.0)
            # Finde Score des Akteurs im Quell-Theater
            actor_score = _get_actor_score(actor_name, src_t, theater_results)
            boost = cross_boosts.get(dst_t, 0.0)
            cross_links.append({
                "actor":      actor_name,
                "from":       src_t,
                "to":         dst_t,
                "item":       sc["item"],
                "note":       sc["note"],
                "risk":       sc["risk"],
                "src_score":  round(src_score, 1),
                "dst_score":  round(dst_score, 1),
                "actor_score":round(actor_score, 1),
                "boost":      round(boost, 1),
                "active":     actor_score >= 30 or src_score >= 35,
            })

    # Sortiert nach Risiko
    risk_ord = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    cross_links.sort(key=lambda l: risk_ord.get(l["risk"], 3))

    # ── Aktive Cross-Akteure ──────────────────────────────────────────────────
    active_actors = []
    for actor_name, actor in CROSS_ACTORS.items():
        theaters_present = [
            t for t in actor.get("appears_in", [])
            if t in theater_scores
        ]
        if len(theaters_present) < 2:
            continue
        actor_scores = {
            t: _get_actor_score(actor_name, t, theater_results)
            for t in theaters_present
        }
        max_score = max(actor_scores.values()) if actor_scores else 0.0
        if max_score >= 20:  # Nur relevante Akteure
            active_actors.append({
                "actor":     actor_name,
                "theaters":  theaters_present,
                "scores":    {t: round(s, 1) for t, s in actor_scores.items()},
                "roles":     actor.get("roles", {}),
                "max_score": round(max_score, 1),
                "risk":      "high" if max_score >= 60 else
                             "medium" if max_score >= 35 else "low",
            })
    active_actors.sort(key=lambda a: -a["max_score"])

    # ── Globaler Score ────────────────────────────────────────────────────────
    if cross_scores:
        global_score = _clamp(sum(cross_scores.values()) / len(cross_scores))
    else:
        global_score = 0.0
    global_level, global_icon = _level(global_score)

    return {
        "theaters":       theater_results,
        "theater_scores": {t: round(v, 1) for t, v in theater_scores.items()},
        "cross_boosts":   {t: round(v, 1) for t, v in cross_boosts.items()},
        "cross_scores":   {t: round(v, 1) for t, v in cross_scores.items()},
        "cross_links":    cross_links,
        "active_actors":  active_actors,
        "global_score":   round(global_score, 1),
        "global_level":   global_level,
        "global_icon":    global_icon,
        "correlation_matrix": {
            f"{s} → {d}": round(v, 2)
            for (s, d), v in THEATER_CORRELATION_MATRIX.items()
            if s in target_theaters and d in target_theaters
        },
        "timestamp": _ts(),
    }


def _get_actor_score(
    actor: str,
    theater: str,
    theater_results: dict,
) -> float:
    """Holt den Score eines Akteurs aus einem Theater-Ergebnis."""
    tr = theater_results.get(theater, {})
    member_scores = tr.get("member_scores", {})
    return float(member_scores.get(actor, 0.0))


# ═══════════════════════════════════════════════════════════════════════════════
# Formatierung
# ═══════════════════════════════════════════════════════════════════════════════

_USE_COLOR = sys.stdout.isatty()
_C = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[91m", "orange": "\033[33m", "yellow": "\033[93m",
    "green": "\033[92m", "cyan": "\033[96m", "blue": "\033[94m",
}


def _c(style: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return _C.get(style, "") + text + _C["reset"]


def _bar(score: float, w: int = 16) -> str:
    filled = round(score / 100 * w)
    return "█" * filled + "░" * (w - filled)


def _score_col(score: float) -> str:
    if not _USE_COLOR:
        return ""
    if   score >= 81: return _C["red"]
    elif score >= 61: return "\033[31m"
    elif score >= 41: return _C["orange"]
    elif score >= 21: return _C["yellow"]
    else:             return _C["green"]


def format_cross_report(result: dict) -> str:
    lines = []
    W = 65

    def sep(c="═"): return _c("dim", "  " + c * W)
    def line(t=""): lines.append(t)

    line(); line(sep())
    line(_c("bold", "  🌐  NEXUS · CROSS-THEATER-KORRELATION"))
    line(_c("dim",  "       Verbindungen zwischen Konflikts-Theatern"))
    line(sep())

    # Global Score
    gs = result.get("global_score", 0.0)
    gl = result.get("global_level", "GRUEN")
    gi = result.get("global_icon", "🟢")
    col = _score_col(gs)
    rst = _C["reset"] if _USE_COLOR else ""
    line()
    line(f"  {col}{gi}  GLOBALER SCORE  {_bar(gs)}  {gs:.0f}/100  [{gl}]{rst}")
    line()

    # Theater-Scores
    line(_c("bold", "  THEATER-ÜBERSICHT"))
    line(sep("─"))
    t_scores = result.get("theater_scores", {})
    c_scores  = result.get("cross_scores", {})
    c_boosts  = result.get("cross_boosts", {})
    for tn in sorted(t_scores):
        base  = t_scores[tn]
        boost = c_boosts.get(tn, 0.0)
        final = c_scores.get(tn, base)
        _, lic = _level(final)
        col = _score_col(final)
        rst = _C["reset"] if _USE_COLOR else ""
        boost_str = _c("dim", f"  (+{boost:.1f} Cross-Boost)") if boost >= 1.0 else ""
        line(f"  {col}{lic}  {tn:<16} {_bar(final,14)}  {final:5.1f}{rst}{boost_str}")
    line()

    # Cross-Links (Versorgungsketten über Theater)
    links = result.get("cross_links", [])
    if links:
        line(_c("bold", f"  🔗  CROSS-THEATER-VERSORGUNGSKETTEN ({len(links)})"))
        line(sep("─"))
        risk_icons = {"critical": "⛔", "high": "🔴", "medium": "🟠", "low": "🟡"}
        for lnk in links:
            ri  = risk_icons.get(lnk["risk"], "⚪")
            act = lnk["active"]
            col = _C["red"] if act else _C.get("dim", "")
            rst = _C["reset"] if _USE_COLOR else ""
            status = "AKTIV" if act else "latent"
            line(f"  {ri}  {col}{lnk['actor']}: "
                 f"{lnk['from']} ──▶ {lnk['to']}  [{status}]{rst}")
            line(_c("dim", f"       {lnk['item']}"))
            line(_c("dim", f"       {lnk['note']}"))
            if lnk.get("boost", 0) >= 1:
                line(_c("dim",
                    f"       Score-Boost auf {lnk['to']}: +{lnk['boost']:.1f}"))
        line()

    # Aktive Cross-Akteure
    actors = result.get("active_actors", [])
    if actors:
        line(_c("bold", f"  👤  CROSS-AKTEURE ({len(actors)})"))
        line(sep("─"))
        for ac in actors:
            theaters_str = " · ".join(
                f"{t}({ac['scores'].get(t,0):.0f})" for t in ac["theaters"]
            )
            risk_icons2 = {"high": "🔴", "medium": "🟠", "low": "🟡"}
            ri2 = risk_icons2.get(ac["risk"], "⚪")
            line(f"  {ri2}  {_c('bold', ac['actor'])}  —  {theaters_str}")
            roles_str = "  ".join(
                f"{t}: {r}" for t, r in ac.get("roles", {}).items()
                if t in ac["theaters"]
            )
            if roles_str:
                line(_c("dim", f"       Rollen: {roles_str}"))
        line()

    # Korrelationsmatrix
    matrix = result.get("correlation_matrix", {})
    if matrix:
        line(_c("bold", "  📊  KORRELATIONSMATRIX"))
        line(sep("─"))
        for pair, val in matrix.items():
            bar_v = _bar(val * 100, 10)
            line(_c("dim", f"  {pair:<32} {bar_v}  {val:.2f}"))
        line()

    line(sep())
    line(_c("dim", f"  Zeitstempel: {result.get('timestamp','?')}"))
    line()
    return "\n".join(lines)


def cross_theater_brief(result: dict) -> str:
    gs     = result.get("global_score", 0.0)
    gl     = result.get("global_level", "?")
    gi     = result.get("global_icon", "")
    links  = [l for l in result.get("cross_links", []) if l.get("active")]
    actors = result.get("active_actors", [])
    boosts = {t: v for t, v in result.get("cross_boosts", {}).items() if v >= 2}
    parts  = [f"CROSS-THEATER {gi} {gs:.0f}/100 [{gl}]"]
    if links:
        parts.append(f"{len(links)} aktive Versorgungsketten")
    if actors:
        parts.append(f"Cross-Akteure: {', '.join(a['actor'] for a in actors[:3])}")
    if boosts:
        parts.append("Boosts: " + ", ".join(f"{t}+{v:.0f}" for t, v in boosts.items()))
    return " | ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    global _USE_COLOR
    ap = argparse.ArgumentParser(
        prog="nexus_crosstheater",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--theaters", "-t", nargs="*",
        help="Nur diese Theater (default: alle)")
    ap.add_argument("--dept", "-d", nargs="*", metavar="DEPT",
        help="Abteilungsfilter")
    ap.add_argument("--json", "-j", action="store_true")
    ap.add_argument("--brief", "-b", action="store_true")
    ap.add_argument("--seq",  "-s", action="store_true")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()

    if args.no_color or args.json:
        _USE_COLOR = False

    depts = [d.upper() for d in args.dept] if args.dept else None

    t0 = time.time()
    result = compute_cross_theater(
        theaters=args.theaters,
        depts=depts,
        parallel=not args.seq,
    )
    elapsed = round(time.time() - t0, 1)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return
    if args.brief:
        print(cross_theater_brief(result))
        return

    print(format_cross_report(result))
    print(_c("dim", f"  Berechnet in {elapsed}s"))
    print()


if __name__ == "__main__":
    main()
