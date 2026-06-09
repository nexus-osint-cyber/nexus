"""
NEXUS – Natural Language Query Interface
==========================================
Wandelt Freitext-Anfragen in NEXUS-Abfragen um.

Statt:  python nexus_query.py --theater MiddleEast --dept OSINT SIGINT
Einfach:  python nexus_nl_query.py "Was ist gerade im Nahen Osten los?"

Statt:  python nexus_query.py --region Iran --dept OSINT
Einfach:  python nexus_nl_query.py "Iran OSINT Lage"

Weitere Beispiele:
  "Ukraine Krieg aktuell"         → --theater EasternEurope
  "Gaza Raketen heute"             → --region Gaza --dept OSINT SIGINT
  "Hezbollah Aktivität Libanon"   → --region Lebanon --dept HUMINT SIGINT
  "IRGC Wikidata Profil"          → --entity IRGC
  "Drohnen Iran Russland"         → nexus_crosstheater + MiddleEast
  "Vorhersage Hezbollah"          → nexus_theater_predict --proxy Lebanon
  "Wie wahrscheinlich eskaliert Iran?" → nexus_theater_predict --driver Iran

Intent-Erkennung:
  1. LLM (Ollama/Claude) wenn verfügbar — versteht alle Sprachen + Kontext
  2. Keyword-Matching (offline Fallback) — erkennt Regionen/Theater/Depts

Gibt aus:
  - Erkannter Intent
  - Welches Kommando ausgeführt wird
  - Direktes Ergebnis

CLI:
  python nexus_nl_query.py "Was ist gerade im Nahen Osten los?"
  python nexus_nl_query.py --parse-only "Iran Eskalation"
  python nexus_nl_query.py --interactive
  echo "Ukraine Krieg" | python nexus_nl_query.py
"""

from __future__ import annotations

import json
import re
import sys
import argparse
import time
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════════
# Intent-Typen
# ═══════════════════════════════════════════════════════════════════════════════

INTENT_TYPES = {
    "theater":   "Theater-Abfrage (alle Akteure eines Konflikts)",
    "region":    "Region-Abfrage (einzelne Region + Abteilungen)",
    "entity":    "Akteur-Profil (Wikidata Entity-Lookup)",
    "predict":   "Vorhersage-Abfrage (Eskalationswahrscheinlichkeit)",
    "cross":     "Cross-Theater-Korrelation",
    "web":       "Theater-Web-Dashboard generieren",
    "unknown":   "Unbekannte Anfrage",
}

# ═══════════════════════════════════════════════════════════════════════════════
# Keyword-Mapping (offline Fallback)
# ═══════════════════════════════════════════════════════════════════════════════

THEATER_KEYWORDS: dict[str, list[str]] = {
    "MiddleEast": [
        "nahen osten", "nahost", "middle east", "israel", "gaza",
        "iran", "libanon", "lebanon", "jemen", "yemen", "syrien", "syria",
        "irak", "iraq", "hezbollah", "hamas", "houthis", "irgc", "quds",
        "nahost-konflikt", "iranisch", "israelisch", "palästina", "palestine",
        "teheran", "tel aviv", "beirut", "sanaa", "damaskus",
    ],
    "EasternEurope": [
        "ukraine", "russland", "russia", "osteuropa", "eastern europe",
        "belarus", "donbas", "donezk", "luhansk", "kiew", "kyiv", "moskau",
        "nato ostflanke", "kreml", "putin", "selenskyj", "krieg in europa",
        "ukrainekrieg", "russischer angriff", "drohnen ukraine",
    ],
    "AsiaPacific": [
        "china", "taiwan", "nordkorea", "north korea", "südkorea", "south korea",
        "japan", "asien", "pazifik", "asia pacific", "pla", "pekingstraße",
        "taiwan-straße", "kim jong", "halbleiter taiwan", "china taiwan",
    ],
}

REGION_KEYWORDS: dict[str, list[str]] = {
    "Iran":        ["iran", "teheran", "iranisch", "mullah", "irgc", "quds force", "khamenei"],
    "Israel":      ["israel", "idf", "mossad", "tel aviv", "netanjahu", "shin bet"],
    "Gaza":        ["gaza", "hamas", "gazastreifen", "palästina", "pij", "rafah"],
    "Lebanon":     ["libanon", "lebanon", "hezbollah", "beirut", "südlibanon"],
    "Yemen":       ["jemen", "yemen", "houthis", "ansar allah", "sanaa", "rotes meer"],
    "Ukraine":     ["ukraine", "kiew", "kyiv", "donbas", "charkiw", "odessa", "selenskyj"],
    "Russia":      ["russland", "russia", "moskau", "kreml", "putin", "vks"],
    "Syria":       ["syrien", "syria", "damaskus", "hts", "sdf", "assads"],
    "Iraq":        ["irak", "iraq", "bagdad", "pmf", "kataib"],
    "Belarus":     ["belarus", "weißrussland", "lukaschenko", "minsk"],
    "China":       ["china", "pekingstraße", "pla", "xi jinping", "taiwan-straße"],
    "Taiwan":      ["taiwan", "taipeh", "roc"],
    "North Korea": ["nordkorea", "north korea", "kim jong", "pjöngjang", "dprk"],
}

DEPT_KEYWORDS: dict[str, list[str]] = {
    "OSINT": [
        "nachrichten", "news", "berichte", "medien", "social media",
        "telegram", "osint", "meldungen", "open source",
    ],
    "GEOINT": [
        "satellit", "satellitenbilder", "karte", "geospatial",
        "militärinfrastruktur", "dunkelzone", "geoint", "aufnahmen",
    ],
    "SIGINT": [
        "signale", "seismik", "erdbeben", "artillerie", "gps jamming",
        "cyber", "sigint", "elektronisch", "frequenz",
    ],
    "HUMINT": [
        "akteure", "personen", "regierung", "kommandeure", "humint",
        "profil", "wikidata", "entität", "entity",
    ],
    "ECONINT": [
        "wirtschaft", "sanktionen", "handel", "waffen", "rüstung",
        "econint", "embargo", "sipri", "comtrade",
    ],
    "HUMANA": [
        "humanitär", "flüchtlinge", "idp", "blockade", "hunger",
        "humana", "reliefweb", "ocha", "vertreibung",
    ],
}

PREDICT_KEYWORDS = [
    "wahrscheinlichkeit", "vorhersage", "predict", "prognose",
    "wird", "eskaliert", "aktivierung", "wann", "how likely",
    "wahrscheinlich", "chance", "probability",
]

CROSS_KEYWORDS = [
    "verbindung", "cross", "zusammenhang", "lieferung", "liefert",
    "drohnen iran russland", "shahed", "nordkorea russland", "munition",
    "verbindungen zwischen", "both theaters", "mehrere theater",
]

ENTITY_KEYWORDS = [
    "wer ist", "profil von", "entity", "wikidata", "akteur",
    "organisation", "was ist", "beschreibe",
]

WEB_KEYWORDS = [
    "karte", "dashboard", "web", "browser", "html", "visualisierung",
    "grafisch", "anzeigen", "zeige mir", "öffne",
]


# ═══════════════════════════════════════════════════════════════════════════════
# Intent-Parsing
# ═══════════════════════════════════════════════════════════════════════════════

class ParsedIntent:
    def __init__(self):
        self.intent:      str = "unknown"
        self.theater:     Optional[str] = None
        self.region:      Optional[str] = None
        self.depts:       list[str] = []
        self.entity:      Optional[str] = None
        self.predict_driver: Optional[str] = None
        self.predict_proxy:  Optional[str] = None
        self.open_web:    bool = False
        self.compact:     bool = False
        self.confidence:  float = 0.0
        self.raw_query:   str = ""
        self.method:      str = "keyword"   # "llm" oder "keyword"

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    def __repr__(self) -> str:
        parts = [f"intent={self.intent}"]
        if self.theater:   parts.append(f"theater={self.theater}")
        if self.region:    parts.append(f"region={self.region}")
        if self.depts:     parts.append(f"depts={self.depts}")
        if self.entity:    parts.append(f"entity={self.entity}")
        if self.predict_driver: parts.append(f"driver={self.predict_driver}")
        if self.predict_proxy:  parts.append(f"proxy={self.predict_proxy}")
        return f"ParsedIntent({', '.join(parts)}, conf={self.confidence:.2f})"


def _normalize(text: str) -> str:
    return text.lower().strip()


def _count_keywords(text: str, keywords: list[str]) -> int:
    return sum(1 for kw in keywords if kw in text)


def parse_keyword(query: str) -> ParsedIntent:
    """
    Offline Keyword-Matching — funktioniert ohne LLM.
    Erkennt: Theater, Region, Departments, Entities, Predict-Intent.
    """
    intent = ParsedIntent()
    intent.raw_query = query
    intent.method    = "keyword"
    text = _normalize(query)

    # ── Entity-Check ─────────────────────────────────────────────────────────
    entity_hits = _count_keywords(text, ENTITY_KEYWORDS)
    known_entities = [
        "irgc", "hamas", "hezbollah", "mossad", "cia", "fsb", "gru",
        "putin", "khamenei", "netanjahu", "pla", "quds force",
    ]
    entity_direct = next((e for e in known_entities if e in text), None)

    # ── Theater-Erkennung ─────────────────────────────────────────────────────
    theater_scores: dict[str, int] = {}
    for tn, kws in THEATER_KEYWORDS.items():
        theater_scores[tn] = _count_keywords(text, kws)

    best_theater = max(theater_scores, key=theater_scores.get) if theater_scores else None
    best_theater_score = theater_scores.get(best_theater, 0)

    # ── Region-Erkennung ──────────────────────────────────────────────────────
    region_scores: dict[str, int] = {}
    for rn, kws in REGION_KEYWORDS.items():
        region_scores[rn] = _count_keywords(text, kws)

    best_region = max(region_scores, key=region_scores.get) if region_scores else None
    best_region_score = region_scores.get(best_region, 0)

    # ── Department-Erkennung ──────────────────────────────────────────────────
    matched_depts = []
    for dept, kws in DEPT_KEYWORDS.items():
        if _count_keywords(text, kws) > 0:
            matched_depts.append(dept)

    # ── Predict-Intent ────────────────────────────────────────────────────────
    predict_hits = _count_keywords(text, PREDICT_KEYWORDS)

    # ── Cross-Theater-Intent ──────────────────────────────────────────────────
    cross_hits = _count_keywords(text, CROSS_KEYWORDS)

    # ── Web-Intent ───────────────────────────────────────────────────────────
    web_hits = _count_keywords(text, WEB_KEYWORDS)

    # ── Intent-Entscheidung ───────────────────────────────────────────────────
    if entity_hits >= 1 and entity_direct:
        intent.intent   = "entity"
        intent.entity   = entity_direct.upper()
        intent.confidence = 0.75

    elif predict_hits >= 1 and best_region_score >= 1:
        intent.intent = "predict"
        intent.predict_driver = best_region if best_region_score >= 1 else None
        # Suche nach Proxy
        regions_found = [r for r, s in region_scores.items() if s >= 1]
        intent.predict_proxy = regions_found[1] if len(regions_found) >= 2 else None
        intent.confidence = 0.65

    elif cross_hits >= 1:
        intent.intent     = "cross"
        intent.confidence = 0.70

    elif web_hits >= 1 and (best_theater_score >= 1 or best_region_score >= 1):
        intent.intent   = "web"
        intent.theater  = best_theater if best_theater_score >= 1 else None
        intent.confidence = 0.68

    elif best_theater_score >= 2:
        # Genug Theater-Keywords → Theater-Abfrage
        intent.intent    = "theater"
        intent.theater   = best_theater
        intent.depts     = matched_depts
        intent.confidence = min(0.95, 0.50 + best_theater_score * 0.1)

    elif best_region_score >= 1:
        intent.intent    = "region"
        intent.region    = best_region
        intent.depts     = matched_depts if matched_depts else None
        intent.confidence = min(0.90, 0.45 + best_region_score * 0.12)

    elif best_theater_score >= 1:
        # Nur 1 Theater-Keyword → trotzdem Theater
        intent.intent    = "theater"
        intent.theater   = best_theater
        intent.depts     = matched_depts
        intent.confidence = 0.45

    else:
        intent.intent     = "unknown"
        intent.confidence = 0.10

    return intent


def parse_llm(query: str) -> Optional[ParsedIntent]:
    """
    LLM-basiertes Intent-Parsing via Ollama oder Claude API.
    Gibt None zurück wenn kein LLM verfügbar.
    """
    prompt = f"""Du bist ein NEXUS Intelligence System Parser.
Analysiere die folgende Anfrage und antworte NUR mit einem JSON-Objekt.

Verfügbare Theater: MiddleEast, EasternEurope, AsiaPacific
Verfügbare Regionen: Iran, Israel, Gaza, Lebanon, Yemen, Syria, Iraq, Ukraine, Russia, Belarus, China, Taiwan, North Korea
Verfügbare Departments: OSINT, GEOINT, SIGINT, HUMINT, ECONINT, HUMANA
Intent-Typen: theater, region, entity, predict, cross, web, unknown

Anfrage: "{query}"

Antworte mit JSON:
{{
  "intent": "theater|region|entity|predict|cross|web|unknown",
  "theater": "MiddleEast|EasternEurope|AsiaPacific|null",
  "region": "Regionname|null",
  "depts": ["OSINT", ...] oder [],
  "entity": "Entitätsname|null",
  "predict_driver": "Regionname|null",
  "predict_proxy": "Regionname|null",
  "confidence": 0.0-1.0
}}"""

    try:
        from nexus_llm import query_llm
        response = query_llm(prompt, max_tokens=300, temperature=0.1)
        if not response:
            return None
        # JSON aus Response extrahieren
        match = re.search(r'\{[\s\S]+\}', response)
        if not match:
            return None
        data = json.loads(match.group(0))
        intent = ParsedIntent()
        intent.intent           = data.get("intent", "unknown")
        intent.theater          = data.get("theater") or None
        intent.region           = data.get("region") or None
        intent.depts            = data.get("depts") or []
        intent.entity           = data.get("entity") or None
        intent.predict_driver   = data.get("predict_driver") or None
        intent.predict_proxy    = data.get("predict_proxy") or None
        intent.confidence       = float(data.get("confidence", 0.5))
        intent.raw_query        = query
        intent.method           = "llm"
        return intent
    except Exception:
        return None


def parse_intent(query: str, use_llm: bool = True) -> ParsedIntent:
    """
    Haupt-Parser: versucht LLM, fällt auf Keyword-Matching zurück.
    """
    if use_llm:
        llm_result = parse_llm(query)
        if llm_result and llm_result.confidence >= 0.5:
            return llm_result
    return parse_keyword(query)


# ═══════════════════════════════════════════════════════════════════════════════
# Query-Ausführung
# ═══════════════════════════════════════════════════════════════════════════════

def execute_intent(
    intent:   ParsedIntent,
    as_json:  bool = False,
    compact:  bool = False,
    parallel: bool = True,
) -> int:
    """
    Führt den erkannten Intent aus.
    Returns exit-code (0 = OK, 1 = Fehler).
    """
    i = intent

    if i.intent == "theater":
        if not i.theater:
            print("✗ Theater nicht erkannt.", file=sys.stderr)
            return 1
        try:
            from nexus_query import run_theater_query
            return run_theater_query(
                theater_name=i.theater,
                depts=i.depts or None,
                as_json=as_json,
                compact=compact,
                parallel=parallel,
            )
        except ImportError:
            # Direkter Fallback
            try:
                from nexus_theater import compute_theater, format_theater_report, theater_brief
                result = compute_theater(i.theater, i.depts or None, parallel)
                print(format_theater_report(result, compact=compact))
                return 0
            except Exception as e:
                print(f"✗ Theater-Fehler: {e}", file=sys.stderr)
                return 1

    elif i.intent == "region":
        if not i.region:
            print("✗ Region nicht erkannt.", file=sys.stderr)
            return 1
        try:
            from nexus_query import run_query
            return run_query(
                region=i.region,
                depts=i.depts or None,
                as_json=as_json,
                compact=compact,
                parallel=parallel,
            )
        except ImportError as e:
            print(f"✗ nexus_query.py nicht gefunden: {e}", file=sys.stderr)
            return 1

    elif i.intent == "entity":
        if not i.entity:
            print("✗ Entity nicht erkannt.", file=sys.stderr)
            return 1
        try:
            from nexus_query import run_query
            return run_query(
                region=i.region or "",
                depts=None,
                entity=i.entity,
                as_json=as_json,
            )
        except ImportError as e:
            print(f"✗ nexus_query.py nicht gefunden: {e}", file=sys.stderr)
            return 1

    elif i.intent == "predict":
        try:
            from nexus_theater_predict import (
                predict_all, predict_theater, format_predictions
            )
            if i.theater:
                preds = {i.theater: predict_theater(i.theater)}
            else:
                preds = predict_all()
            if as_json:
                print(json.dumps(preds, indent=2, ensure_ascii=False, default=str))
            else:
                print(format_predictions(preds))
            return 0
        except ImportError as e:
            print(f"✗ nexus_theater_predict.py nicht gefunden: {e}", file=sys.stderr)
            return 1

    elif i.intent == "cross":
        try:
            from nexus_crosstheater import compute_cross_theater, format_cross_report
            result = compute_cross_theater(parallel=parallel)
            if as_json:
                print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
            else:
                print(format_cross_report(result))
            return 0
        except ImportError as e:
            print(f"✗ nexus_crosstheater.py nicht gefunden: {e}", file=sys.stderr)
            return 1

    elif i.intent == "web":
        tn = i.theater or "MiddleEast"
        try:
            from nexus_theater_web import generate_theater_html
            path = generate_theater_html(theater_name=tn, open_browser=True)
            print(f"  ✓ Dashboard geöffnet: {path}")
            return 0
        except ImportError as e:
            print(f"✗ nexus_theater_web.py nicht gefunden: {e}", file=sys.stderr)
            return 1

    else:
        print(f"  ⚠  Anfrage nicht verstanden: '{intent.raw_query}'")
        print("  Versuche es mit:")
        print("    → 'Iran Lage aktuell'")
        print("    → 'Naher Osten Theater'")
        print("    → 'Ukraine Krieg SIGINT'")
        print("    → 'Vorhersage Hezbollah'")
        print("    → 'Cross-Theater Drohnen'")
        return 1


# ═══════════════════════════════════════════════════════════════════════════════
# Interaktiver Modus
# ═══════════════════════════════════════════════════════════════════════════════

def interactive_mode(use_llm: bool = True) -> None:
    """
    Interaktiver NEXUS-Chat-Modus.
    Eingabe: Freitext → Intent → Ergebnis → nächste Eingabe.
    """
    print("\n  🎯  NEXUS — Natural Language Interface")
    print("  Tippe eine Anfrage oder 'exit' zum Beenden.")
    print("  Beispiele: 'Iran Lage', 'Gaza Raketen', 'Naher Osten',")
    print("             'Vorhersage Hezbollah', 'Cross-Theater Drohnen'\n")

    while True:
        try:
            query = input("  NEXUS> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Auf Wiedersehen.")
            break

        if not query:
            continue
        if query.lower() in ("exit", "quit", "bye", "q", "beenden"):
            print("  Auf Wiedersehen.")
            break

        t0 = time.time()
        intent = parse_intent(query, use_llm=use_llm)
        elapsed = round(time.time() - t0, 1)

        print(f"\n  → Intent: {intent.intent}  "
              f"Conf: {intent.confidence:.0%}  "
              f"Methode: {intent.method}")

        execute_intent(intent, compact=True)
        print(f"  (in {elapsed}s)\n")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    ap = argparse.ArgumentParser(
        prog="nexus_nl_query",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("query", nargs="?", default=None,
        help="Freitext-Anfrage (oder via stdin)")
    ap.add_argument("--parse-only", "-p", action="store_true",
        help="Nur Intent parsen, nicht ausführen")
    ap.add_argument("--interactive", "-i", action="store_true",
        help="Interaktiver Chat-Modus")
    ap.add_argument("--no-llm", action="store_true",
        help="Kein LLM verwenden (nur Keyword-Matching)")
    ap.add_argument("--json", "-j", action="store_true")
    ap.add_argument("--compact", "-c", action="store_true")
    ap.add_argument("--seq", "-s", action="store_true")
    args = ap.parse_args()

    use_llm = not args.no_llm

    if args.interactive:
        interactive_mode(use_llm=use_llm)
        return

    # Query aus Argument oder stdin
    query = args.query
    if not query and not sys.stdin.isatty():
        query = sys.stdin.read().strip()
    if not query:
        ap.print_help()
        sys.exit(1)

    # Parsen
    intent = parse_intent(query, use_llm=use_llm)

    if args.parse_only or args.json:
        if args.json:
            print(json.dumps(intent.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(f"  Anfrage: '{query}'")
            print(f"  Intent:  {intent}")
            print(f"  Methode: {intent.method}")
        return

    # Info ausgeben + ausführen
    print(f"\n  → Erkannt: {intent.intent.upper()}  "
          f"(Konfidenz: {intent.confidence:.0%}, Methode: {intent.method})")

    rc = execute_intent(
        intent,
        as_json=args.json,
        compact=args.compact,
        parallel=not args.seq,
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
