"""
NEXUS Query — Interaktives CLI mit Department-Filter & Theater-Modus
=====================================================================
Einheitliches Abfrage-Interface für alle NEXUS-Abteilungen.

Verwendung:
  python nexus_query.py --region Iran
  python nexus_query.py --region Gaza --dept OSINT SIGINT
  python nexus_query.py --region Lebanon --dept GEOINT --json
  python nexus_query.py --region Iran --entity "IRGC"
  python nexus_query.py --region Ukraine --dept ECONINT HUMANA --seq

  # Theater-Modus (alle beteiligten Akteure eines Konflikts):
  python nexus_query.py --theater MiddleEast
  python nexus_query.py --theater EasternEurope --dept OSINT SIGINT
  python nexus_query.py --theater MiddleEast --compact --json
  python nexus_query.py --list-theaters

Abteilungen:
  OSINT   – Nachrichten, RSS, GDELT, Telegram
  GEOINT  – Satellit, Militärinfrastruktur, Dark Zones
  SIGINT  – Seismik, Artillerie-Blitz, GPS-Jamming, Cyber
  HUMINT  – Akteurprofile, Regime-Struktur, HUMINT-Hits
  ECONINT – Handel, Sanktionen, Waffentransfers
  HUMANA  – Humanitäre Lage, IDP, Blockade
  ALL     – Alle 6 Abteilungen (default)

Theater (--theater):
  MiddleEast    – Iran + Israel + Gaza + Libanon + Jemen + Syrien + Irak
  EasternEurope – Ukraine + Russland + Belarus
  AsiaPacific   – China + Taiwan + Nordkorea + Südkorea + Japan
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════════
# ANSI-Farben
# ═══════════════════════════════════════════════════════════════════════════════

_C = {
    "reset":   "\033[0m",
    "bold":    "\033[1m",
    "dim":     "\033[2m",
    "red":     "\033[91m",
    "orange":  "\033[33m",
    "yellow":  "\033[93m",
    "green":   "\033[92m",
    "blue":    "\033[94m",
    "cyan":    "\033[96m",
    "magenta": "\033[95m",
    "white":   "\033[97m",
    "grey":    "\033[90m",
}

_LEVEL_COLOR = {
    "KRITISCH": _C["red"],
    "ROT":      "\033[31m",
    "ORANGE":   _C["orange"],
    "GELB":     _C["yellow"],
    "GRUEN":    _C["green"],
}

_DEPT_COLOR = {
    "OSINT":   _C["yellow"],
    "GEOINT":  _C["green"],
    "SIGINT":  _C["red"],
    "HUMINT":  _C["magenta"],
    "ECONINT": _C["cyan"],
    "HUMANA":  _C["orange"],
}

USE_COLOR = sys.stdout.isatty()   # Farben nur wenn Terminal


def _c(key: str, text: str) -> str:
    if not USE_COLOR: return text
    return _C.get(key, "") + text + _C["reset"]


def _lc(level: str, text: str) -> str:
    if not USE_COLOR: return text
    return _LEVEL_COLOR.get(level, "") + text + _C["reset"]


def _dc(dept: str, text: str) -> str:
    if not USE_COLOR: return text
    return _DEPT_COLOR.get(dept, "") + text + _C["reset"]


# ═══════════════════════════════════════════════════════════════════════════════
# Ausgabe-Funktionen
# ═══════════════════════════════════════════════════════════════════════════════

def _header(region: str, ts: str) -> None:
    line = "═" * 62
    print(_c("bold", f"\n╔{line}╗"))
    print(_c("bold", f"║  NEXUS QUERY — Region: {region:<36} ║"))
    print(_c("bold", f"║  {ts[:19]} UTC{' ' * 37}║"))
    print(_c("bold", f"╚{line}╝\n"))


def _print_master(result: dict) -> None:
    score  = result["master_score"]
    level  = result["master_level"]
    icon   = result["master_icon"]
    fusion = result.get("fusion_score")
    fl     = result.get("fusion_level", level)

    bar_f  = int(score / 5)
    bar    = "█" * bar_f + "░" * (20 - bar_f)
    s_str  = _lc(level, f"{score:5.1f}/100")

    print(_c("bold", f"  {icon} MASTER-SCORE : {s_str}  [{level}]"))
    print(f"  Score-Balken : │{bar}│")
    if fusion is not None and abs(fusion - score) >= 0.5:
        fi_bar = int(fusion / 5)
        fi_bar_str = "█" * fi_bar + "░" * (20 - fi_bar)
        print(f"  Fusion       : │{fi_bar_str}│ {_lc(fl, f'{fusion:5.1f}/100')}  [{fl}]")
    print()


def _print_dept_summary(result: dict) -> None:
    summary = result.get("dept_summary", {})
    if not summary:
        return

    print(_c("bold", "  ABTEILUNGS-ÜBERSICHT"))
    print("  " + "─" * 57)

    for dept, info in sorted(summary.items(),
                              key=lambda x: x[1].get("score", 0),
                              reverse=True):
        score  = info.get("score", 0.0)
        conf   = info.get("confidence", "none").upper()
        icon   = info.get("icon", "")
        label  = info.get("label", dept)
        weight = info.get("weight_pct", 0)
        n_src  = info.get("sources", 0)
        n_find = info.get("findings", 0)

        # Balken
        fill   = int(score / 5)
        bar    = "█" * fill + "░" * (20 - fill)

        # Level für Farbe
        if   score >= 81: dlvl = "KRITISCH"
        elif score >= 61: dlvl = "ROT"
        elif score >= 41: dlvl = "ORANGE"
        elif score >= 21: dlvl = "GELB"
        else:             dlvl = "GRUEN"

        s_str = _dc(dept, f"{score:5.1f}")
        print(f"  {icon} {_c('bold', dept):<10} │{bar}│ {s_str}  "
              f"[{conf}]  Gew.{weight}%  "
              f"({n_src} Quellen, {n_find} Funde)")
    print()


def _print_dept_detail(dept_name: str, dept: dict) -> None:
    """Detailansicht einer einzelnen Abteilung."""
    score  = dept.get("score", 0.0)
    conf   = dept.get("confidence", "none").upper()
    icon   = dept.get("icon", "")
    label  = dept.get("label", dept_name)
    srcs   = dept.get("sources", [])
    failed = dept.get("failed", [])

    print(_dc(dept_name, _c("bold", f"  ┌── {icon} {dept_name}: {label}")))
    print(f"  │  Score: {score:.1f}/100  [{conf}]")

    if srcs:
        print(f"  │  Quellen: {', '.join(srcs)}")
    if failed:
        print(f"  │  ✗ Fehler: {', '.join(failed)}")

    findings = dept.get("findings", [])
    if findings:
        print(f"  │")
        for f in findings:
            src   = f.get("source", "?")
            ftype = f.get("type", "")
            fs    = f.get("score", 0)
            print(f"  │  • [{src}] {ftype}  → +{fs:.0f} pt")

            # Quellenspezifische Details
            if ftype == "news_feed":
                for h in f.get("top_headlines", [])[:2]:
                    print(f"  │      ↳ {h[:65]}")
                if f.get("attack_keywords", 0) > 0:
                    print(f"  │      ⚠ Attack-Keywords: {f['attack_keywords']}")

            elif ftype == "conflict_events":
                print(f"  │      Gesamt: {f.get('total',0)}  "
                      f"Hochpriorität: {f.get('high_priority',0)}")

            elif ftype == "satellite_change":
                print(f"  │      AOIs: {f.get('aois_checked',0)}  "
                      f"Signifikant: {f.get('significant',0)}  "
                      f"Max-Δ: {f.get('max_change_score',0):.3f}")
                for s in f.get("top_sites", [])[:2]:
                    print(f"  │      ↳ {s.get('name','?')}: "
                          f"{s.get('change_type','?')} "
                          f"(Δ{s.get('score',0):.3f})")

            elif ftype == "military_infrastructure":
                print(f"  │      Objekte: {f.get('total_objects',0)}  "
                      f"Hochrisiko: {f.get('high_risk',0)}")
                btypes = f.get("by_type", {})
                if btypes:
                    parts = [f"{k}:{v}" for k,v in list(btypes.items())[:4]]
                    print(f"  │      Typen: {', '.join(parts)}")

            elif ftype == "military_dark_zones":
                print(f"  │      Sperrzonen: {f.get('total',0)}  "
                      f"Multi-Signal: {f.get('multi_signal',0)}  "
                      f"Max-Conf: {f.get('top_confidence',0):.2f}")

            elif ftype == "detonation_candidates":
                top = f.get("top_event", {})
                print(f"  │      Gesamt: {f.get('total',0)}  "
                      f"HIGH: {f.get('high_confidence',0)}  "
                      f"MED: {f.get('medium_confidence',0)}")
                if top.get("mag"):
                    print(f"  │      ↳ M{top['mag']} Tiefe:{top.get('depth_km','?')}km "
                          f"[{top.get('confidence','?')}]  "
                          f"({top.get('lat','?'):.2f},{top.get('lon','?'):.2f})")

            elif ftype == "gps_jamming":
                print(f"  │      Intensität: {f.get('intensity','?')}  "
                      f"Zone: {f.get('zone','?')}")

            elif ftype == "entity_profiles":
                print(f"  │      Aufgelöst: {f.get('actors_resolved',0)}/"
                      f"{f.get('actors_queried',0)} Akteure")
                for e in f.get("entities", [])[:3]:
                    print(f"  │      ↳ {e.get('name','?')} [{e.get('type','?')}] "
                          f"— {e.get('description','')[:50]}")

            elif ftype == "trade_anomaly":
                print(f"  │      Sanktions-Ind: {f.get('sanctions_indicator',False)}")
                for n in f.get("notes", [])[:2]:
                    print(f"  │      ↳ {n}")

            elif ftype == "arms_transfers":
                print(f"  │      Transfers: {f.get('transfer_count',0)}  "
                      f"Top-Lieferant: {f.get('top_supplier','?')}")
                hw = f.get("high_risk_weapons", [])
                if hw:
                    print(f"  │      Hochrisiko: {', '.join(hw)}")

            elif ftype == "humanitarian_situation":
                tr = f.get("top_report", "")[:65]
                print(f"  │      ↳ {tr}")
                kws = f.get("critical_keywords", [])
                if kws:
                    print(f"  │      Schlüsselwörter: {', '.join(kws)}")

            elif ftype == "social_surge":
                print(f"  │      Kanäle: {f.get('channels_active',0)}  "
                      f"Nachrichten: {f.get('message_count',0)}  "
                      f"Surge: {f.get('surge_active',False)}")
                hint = f.get("hint","")
                if hint:
                    print(f"  │      ↳ {hint[:70]}")

    print(f"  └" + "─" * 55)
    print()


def _print_entity_profile(entity_name: str, region: str) -> None:
    """Wikidata-Entity-Profil eines Akteurs."""
    print(_c("bold", f"\n  ENTITY-PROFIL: {entity_name}"))
    print("  " + "─" * 50)
    try:
        from nexus_wikidata import resolve_entity
        r = resolve_entity(entity_name)
        if r.get("status") == "found":
            print(f"  Name:        {r.get('label','?')}")
            print(f"  QID:         {r.get('qid','?')}")
            print(f"  Typ:         {r.get('instance_of','?')}")
            print(f"  Land:        {r.get('country','?')}")
            print(f"  Beschreibung: {r.get('description','')[:100]}")
            if r.get("wikipedia_url"):
                print(f"  Wikipedia:   {r['wikipedia_url']}")
            if r.get("parent_org"):
                print(f"  Überorg.:    {r['parent_org']}")
            if r.get("founder"):
                print(f"  Gründer:     {r['founder']}")
        else:
            print(f"  ✗ Kein Wikidata-Eintrag gefunden für: {entity_name}")
    except ImportError:
        print("  ✗ nexus_wikidata nicht verfügbar")
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# Spinner (für lange Wartezeiten)
# ═══════════════════════════════════════════════════════════════════════════════

def _spinner(msg: str, done_event) -> None:
    """Einfacher Konsolenspin während der Berechnung."""
    if not USE_COLOR:
        return
    import threading
    chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = 0
    while not done_event.is_set():
        print(f"\r  {chars[i % len(chars)]} {msg}...", end="", flush=True)
        i += 1
        time.sleep(0.12)
    print(f"\r  ✓ {msg} abgeschlossen" + " " * 20)


# ═══════════════════════════════════════════════════════════════════════════════
# Haupt-Query-Funktion
# ═══════════════════════════════════════════════════════════════════════════════

def run_query(
    region:   str,
    depts:    Optional[list[str]],
    as_json:  bool  = False,
    compact:  bool  = False,
    parallel: bool  = True,
    entity:   Optional[str] = None,
) -> int:
    """
    Führt eine NEXUS Department-Abfrage aus und gibt das Ergebnis aus.

    Returns
    -------
    Exit-Code: 0 = OK, 1 = Fehler
    """
    global USE_COLOR
    if as_json:
        USE_COLOR = False

    ts = datetime.now(timezone.utc).isoformat()

    # Optionales Entity-Lookup (unabhängig von Department-Scores)
    if entity:
        _print_entity_profile(entity, region)
        if not depts and not as_json:
            return 0

    # Department-Scores berechnen
    try:
        from nexus_departments import (
            compute_department_scores,
            format_department_report,
            department_brief,
            DEPARTMENTS,
        )
    except ImportError as e:
        print(f"✗ nexus_departments.py nicht gefunden: {e}", file=sys.stderr)
        return 1

    # Spinner starten (nur Terminal, nicht JSON)
    if not as_json and USE_COLOR:
        import threading
        done = threading.Event()
        spinner_thread = threading.Thread(
            target=_spinner,
            args=(f"Berechne Department-Scores für {region}", done),
            daemon=True,
        )
        spinner_thread.start()
    else:
        done = None

    t0 = time.time()
    try:
        result = compute_department_scores(
            region=region,
            depts=depts,
            parallel=parallel,
        )
    except Exception as e:
        if done: done.set()
        print(f"\n✗ Fehler: {e}", file=sys.stderr)
        return 1

    elapsed = round(time.time() - t0, 1)
    if done: done.set()

    # ── Ausgabe ──────────────────────────────────────────────────────────────
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0

    _header(region, ts)
    _print_master(result)
    _print_dept_summary(result)

    # Detailansicht pro Abteilung
    if not compact:
        print(_c("bold", "  DETAILBEFUNDE\n"))
        for dept_name in result.get("active_depts", []):
            dept = result.get("departments", {}).get(dept_name, {})
            _print_dept_detail(dept_name, dept)

    # Brief
    print(_c("dim", "  " + "─" * 57))
    print(_c("dim", f"  {department_brief(result)}"))
    print(_c("dim", f"  Berechnet in {elapsed}s | "
             f"Parallel: {parallel} | "
             f"Abteilungen: {len(result.get('active_depts',[]))}"))
    print()

    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# Theater-Query
# ═══════════════════════════════════════════════════════════════════════════════

def run_theater_query(
    theater_name: str,
    depts:        Optional[list[str]],
    as_json:      bool = False,
    compact:      bool = False,
    parallel:     bool = True,
    timeout:      int  = 120,
) -> int:
    """
    Führt eine NEXUS Theater-Abfrage aus.

    Returns
    -------
    Exit-Code: 0 = OK, 1 = Fehler
    """
    global USE_COLOR
    if as_json:
        USE_COLOR = False

    try:
        from nexus_theater import (
            compute_theater,
            format_theater_report,
            theater_brief,
            THEATERS,
            _normalize_theater_name,
        )
    except ImportError as e:
        print(f"✗ nexus_theater.py nicht gefunden: {e}", file=sys.stderr)
        return 1

    # Normalisieren + validieren
    normalized = _normalize_theater_name(theater_name)
    if normalized not in THEATERS:
        print(
            f"✗ Unbekanntes Theater: '{theater_name}'. "
            f"Verfügbare Theater: {', '.join(THEATERS.keys())}",
            file=sys.stderr,
        )
        return 1

    # Spinner
    if not as_json and USE_COLOR:
        import threading
        done = threading.Event()
        t_info = THEATERS[normalized]
        member_count = len(t_info.get("members", []))
        spinner_thread = threading.Thread(
            target=_spinner,
            args=(
                f"Berechne Theater {normalized} ({member_count} Regionen)",
                done,
            ),
            daemon=True,
        )
        spinner_thread.start()
    else:
        done = None

    t0 = time.time()
    try:
        result = compute_theater(
            theater_name=normalized,
            depts=depts,
            parallel=parallel,
            timeout=timeout,
        )
    except Exception as e:
        if done: done.set()
        print(f"\n✗ Theater-Fehler: {e}", file=sys.stderr)
        return 1

    elapsed = round(time.time() - t0, 1)
    if done: done.set()

    # JSON-Ausgabe
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0

    # Formatierter Report
    print(format_theater_report(result, compact=compact, use_color=USE_COLOR))
    print(_c("dim",
        f"  Berechnet in {elapsed}s | Parallel: {parallel} | "
        f"Regionen: {len(result.get('member_scores', {}))}"))
    print()
    print(_c("dim", f"  {theater_brief(result)}"))
    print()
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    global USE_COLOR

    ap = argparse.ArgumentParser(
        prog="nexus_query",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    ap.add_argument(
        "--region", "-r",
        default="Iran",
        help="Zielregion (default: Iran). Bekannte Regionen: "
             "Iran, Israel, Gaza, Lebanon, Yemen, Syria, Iraq, Ukraine, Russia",
    )
    ap.add_argument(
        "--dept", "-d",
        nargs="*",
        metavar="DEPT",
        help="Abteilungen: OSINT GEOINT SIGINT HUMINT ECONINT HUMANA "
             "(Mehrfachauswahl möglich, default: alle)",
    )
    ap.add_argument(
        "--entity", "-e",
        default=None,
        metavar="NAME",
        help="Wikidata Entity-Profil abrufen (z.B. 'IRGC', 'Hamas', 'Mossad')",
    )
    ap.add_argument(
        "--json", "-j",
        action="store_true",
        help="Maschinenlesbare JSON-Ausgabe (kein Color, kein Spinner)",
    )
    ap.add_argument(
        "--compact", "-c",
        action="store_true",
        help="Kompakte Ausgabe ohne Einzelbefunde",
    )
    ap.add_argument(
        "--seq", "-s",
        action="store_true",
        help="Sequentiell statt parallel (für Debugging)",
    )
    ap.add_argument(
        "--no-color",
        action="store_true",
        help="Keine ANSI-Farben (für Logs / Pipes)",
    )
    ap.add_argument(
        "--list-depts",
        action="store_true",
        help="Verfügbare Abteilungen auflisten und beenden",
    )
    ap.add_argument(
        "--theater", "-t",
        default=None,
        metavar="NAME",
        help=(
            "Theater-Modus: alle Akteure eines Konflikts auf einmal. "
            "Verfügbar: MiddleEast, EasternEurope, AsiaPacific. "
            "Überschreibt --region."
        ),
    )
    ap.add_argument(
        "--list-theaters",
        action="store_true",
        help="Alle verfügbaren Konflikts-Theater auflisten und beenden",
    )
    ap.add_argument(
        "--timeout",
        type=int,
        default=120,
        metavar="SEC",
        help="Timeout in Sekunden pro Region im Theater-Modus (default: 120)",
    )

    args = ap.parse_args()

    if args.no_color or args.json:
        USE_COLOR = False

    if args.list_depts:
        try:
            from nexus_departments import DEPARTMENTS
            print("Verfügbare NEXUS-Abteilungen:\n")
            for name, meta in DEPARTMENTS.items():
                mods = ", ".join(meta.get("modules", []))
                print(f"  {meta['icon']} {name:<10} — {meta['label']}")
                print(f"             Gewicht: {int(meta['weight']*100)}%")
                print(f"             Module:  {mods}")
                print(f"             {meta['desc']}")
                print()
        except ImportError:
            print("✗ nexus_departments.py nicht gefunden", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    if args.list_theaters:
        try:
            from nexus_theater import list_theaters
            list_theaters(use_color=USE_COLOR)
        except ImportError:
            print("✗ nexus_theater.py nicht gefunden", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    # Abteilungen normalisieren
    depts = None
    if args.dept:
        normalized = [d.upper() for d in args.dept]
        # "ALL" als Alias für alle
        if "ALL" in normalized:
            depts = None
        else:
            depts = normalized

    # ── Theater-Modus ─────────────────────────────────────────────────────────
    if args.theater:
        sys.exit(run_theater_query(
            theater_name=args.theater,
            depts=depts,
            as_json=args.json,
            compact=args.compact,
            parallel=not args.seq,
            timeout=getattr(args, "timeout", 120),
        ))

    # ── Standard Region-Modus ─────────────────────────────────────────────────
    sys.exit(run_query(
        region=args.region,
        depts=depts,
        as_json=args.json,
        compact=args.compact,
        parallel=not args.seq,
        entity=args.entity,
    ))


if __name__ == "__main__":
    main()

    print(_c("dim", f"  {theater_brief(result)}"))
    print()
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    global USE_COLOR

    ap = argparse.ArgumentParser(
        prog="nexus_query",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    ap.add_argument(
        "--region", "-r",
        default="Iran",
        help="Zielregion (default: Iran). Bekannte Regionen: "
             "Iran, Israel, Gaza, Lebanon, Yemen, Syria, Iraq, Ukraine, Russia",
    )
    ap.add_argument(
        "--dept", "-d",
        nargs="*",
        metavar="DEPT",
        help="Abteilungen: OSINT GEOINT SIGINT HUMINT ECONINT HUMANA "
             "(Mehrfachauswahl möglich, default: alle)",
    )
    ap.add_argument(
        "--entity", "-e",
        default=None,
        metavar="NAME",
        help="Wikidata Entity-Profil abrufen (z.B. 'IRGC', 'Hamas', 'Mossad')",
    )
    ap.add_argument(
        "--json", "-j",
        action="store_true",
        help="Maschinenlesbare JSON-Ausgabe (kein Color, kein Spinner)",
    )
    ap.add_argument(
        "--compact", "-c",
        action="store_true",
        help="Kompakte Ausgabe ohne Einzelbefunde",
    )
    ap.add_argument(
        "--seq", "-s",
        action="store_true",
        help="Sequentiell statt parallel (für Debugging)",
    )
    ap.add_argument(
        "--no-color",
        action="store_true",
        help="Keine ANSI-Farben (für Logs / Pipes)",
    )
    ap.add_argument(
        "--list-depts",
        action="store_true",
        help="Verfügbare Abteilungen auflisten und beenden",
    )
    ap.add_argument(
        "--theater", "-t",
        default=None,
        metavar="NAME",
        help=(
            "Theater-Modus: alle Akteure eines Konflikts auf einmal. "
            "Verfügbar: MiddleEast, EasternEurope, AsiaPacific. "
            "Überschreibt --region."
        ),
    )
    ap.add_argument(
        "--list-theaters",
        action="store_true",
        help="Alle verfügbaren Konflikts-Theater auflisten und beenden",
    )
    ap.add_argument(
        "--timeout",
        type=int,
        default=120,
        metavar="SEC",
        help="Timeout in Sekunden pro Region im Theater-Modus (default: 120)",
    )

    args = ap.parse_args()

    if args.no_color or args.json:
        USE_COLOR = False

    if args.list_depts:
        try:
            from nexus_departments import DEPARTMENTS
            print("Verfügbare NEXUS-Abteilungen:\n")
            for name, meta in DEPARTMENTS.items():
                mods = ", ".join(meta.get("modules", []))
                print(f"  {meta['icon']} {name:<10} — {meta['label']}")
                print(f"             Gewicht: {int(meta['weight']*100)}%")
                print(f"             Module:  {mods}")
                print(f"             {meta['desc']}")
                print()
        except ImportError:
            print("✗ nexus_departments.py nicht gefunden", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    if args.list_theaters:
        try:
            from nexus_theater import list_theaters
            list_theaters(use_color=USE_COLOR)
        except ImportError:
            print("✗ nexus_theater.py nicht gefunden", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    # Abteilungen normalisieren
    depts = None
    if args.dept:
        normalized = [d.upper() for d in args.dept]
        # "ALL" als Alias für alle
        if "ALL" in normalized:
            depts = None
        else:
            depts = normalized

    # ── Theater-Modus ─────────────────────────────────────────────────────────
    if args.theater:
        sys.exit(run_theater_query(
            theater_name=args.theater,
            depts=depts,
            as_json=args.json,
            compact=args.compact,
            parallel=not args.seq,
            timeout=getattr(args, "timeout", 120),
        ))

    # ── Standard Region-Modus ─────────────────────────────────────────────────
    sys.exit(run_query(
        region=args.region,
        depts=depts,
        as_json=args.json,
        compact=args.compact,
        parallel=not args.seq,
        entity=args.entity,
    ))


if __name__ == "__main__":
    main()
        entity=args.entity,
    ))


if __name__ == "__main__":
    main()
