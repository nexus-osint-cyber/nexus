"""
NEXUS – Named Entity Recognition (NER) Modul (Stufe 3)
Extrahiert Personen, Organisationen, Waffen, Orte aus OSINT-Artikeln.
Ersetzt reines Keyword-Matching durch semantische Erkennung.

Nutzt spaCy (offline, kostenlos):
  pip install spacy --break-system-packages
  python -m spacy download de_core_news_sm   # Deutsch (klein, schnell)
  python -m spacy download en_core_web_sm    # Englisch
  # Optional für bessere Qualität:
  python -m spacy download de_core_news_lg
  python -m spacy download en_core_web_trf   # Transformer-basiert (langsamer, besser)

Ohne spaCy: Fallback auf regelbasierte Extraktion (weniger präzise).
"""

from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from typing import Optional

# ── spaCy lazy loading ────────────────────────────────────────────────────────
_nlp_de = None
_nlp_en = None
_spacy_available = None


def _get_nlp(lang: str = "de"):
    """Gibt spaCy-Modell zurück, lädt bei Bedarf."""
    global _nlp_de, _nlp_en, _spacy_available

    if _spacy_available is False:
        return None

    try:
        import spacy  # type: ignore
        _spacy_available = True
    except ImportError:
        _spacy_available = False
        return None

    if lang == "de":
        if _nlp_de is None:
            for model in ("de_core_news_sm", "de_core_news_md", "de_core_news_lg"):
                try:
                    _nlp_de = spacy.load(model)
                    break
                except OSError:
                    continue
        return _nlp_de

    if lang == "en":
        if _nlp_en is None:
            for model in ("en_core_web_sm", "en_core_web_md", "en_core_web_trf"):
                try:
                    _nlp_en = spacy.load(model)
                    break
                except OSError:
                    continue
        return _nlp_en

    return None


# ── Waffenlisten (domain-spezifisch, spaCy erkennt keine Waffen nativ) ────────
WEAPON_PATTERNS = [
    # Raketen / Drohnen
    r'\b(Shahed|Geran|Iskander|Kalibr|Kinzhal|Onyx|Zirkon|Kh-\d+|S-\d+|Patriot|HIMARS|ATACMS)\b',
    r'\b(MANPADS?|Stinger|Strela|Igla|Javelin|NLAW|ATGM|RPG-?\d*)\b',
    r'\b(Su-\d+|MiG-\d+|F-\d+|B-52|Tu-\d+|Mi-\d+|Ka-\d+)\b',  # Flugzeuge
    r'\b(T-\d+|Bradley|Abrams|Leopard|Challenger|Merkava)\b',   # Panzer
    r'\b(Drohne[n]?|UAV|MLRS|Artillerie|Panzerhaubitze|Haubitze)\b',
    r'\b(drone[s]?|missile[s]?|rocket[s]?|artillery|mortar)\b',
]
_WEAPON_RE = re.compile("|".join(WEAPON_PATTERNS), re.IGNORECASE)

# Militärische Akteure (einfache Muster)
ACTOR_PATTERNS = [
    r'\b(Russland|Russia|russisch[e]?[n]?[m]?[s]?|Russian)\b',
    r'\b(Ukraine|ukrainisch[e]?[n]?[m]?[s]?|Ukrainian)\b',
    r'\b(NATO|Nato|Pentagon|Bundeswehr|IRGC|Hisbollah|Hamas|Houthi[s]?)\b',
    r'\b(IDF|IAF|Mossad|FSB|GRU|CIA|MI6|BND)\b',
    r'\b(Wagner|Kadyrow|Tschetschenen|PMC)\b',
]
_ACTOR_RE = re.compile("|".join(ACTOR_PATTERNS), re.IGNORECASE)


# ── NER-Hauptfunktion ─────────────────────────────────────────────────────────

def extract_entities(text: str, lang: str = "de") -> dict:
    """
    Extrahiert Named Entities aus Text.
    Gibt Dict mit Kategorien zurück.
    """
    if not text or len(text.strip()) < 10:
        return {}

    entities = {
        "persons":       [],
        "organizations": [],
        "locations":     [],
        "weapons":       [],
        "actors":        [],
        "dates":         [],
        "misc":          [],
    }

    # 1. spaCy (wenn verfügbar)
    nlp = _get_nlp(lang)
    if nlp:
        try:
            doc = nlp(text[:1000])   # Maximal 1000 Zeichen für Performance
            for ent in doc.ents:
                label = ent.label_
                name  = ent.text.strip()
                if len(name) < 2:
                    continue
                if label in ("PER", "PERSON"):
                    entities["persons"].append(name)
                elif label in ("ORG", "NORP"):
                    entities["organizations"].append(name)
                elif label in ("GPE", "LOC", "FAC"):
                    entities["locations"].append(name)
                elif label == "DATE":
                    entities["dates"].append(name)
                elif label in ("PRODUCT", "WEAPON", "LAW", "EVENT"):
                    entities["misc"].append(name)
        except Exception:
            pass

    # 2. Regex-basierte Ergänzung (immer)
    for match in _WEAPON_RE.finditer(text):
        w = match.group().strip()
        if w not in entities["weapons"]:
            entities["weapons"].append(w)

    for match in _ACTOR_RE.finditer(text):
        a = match.group().strip()
        if a not in entities["actors"]:
            entities["actors"].append(a)

    # Deduplizieren + normalisieren
    for key in entities:
        seen = set()
        clean = []
        for e in entities[key]:
            e_norm = e.strip().rstrip(".,;")
            if e_norm.lower() not in seen and len(e_norm) > 1:
                seen.add(e_norm.lower())
                clean.append(e_norm)
        entities[key] = clean[:10]   # Max 10 pro Kategorie

    return {k: v for k, v in entities.items() if v}  # Leere weglassen


def enrich_articles_with_ner(articles: list) -> list:
    """
    Fügt NER-Entities jedem Artikel hinzu.
    Erkennt automatisch Sprache.
    """
    for a in articles:
        title   = (a.get("title")   or "").strip()
        summary = (a.get("summary") or "").strip()
        text    = f"{title}. {summary}"

        # Sprache aus nexus_translate falls vorhanden
        lang = a.get("lang", "de")
        if lang not in ("de", "en"):
            lang = "en"   # Für andere Sprachen englisches Modell nutzen

        try:
            entities = extract_entities(text, lang=lang)
            if entities:
                a["entities"] = entities
        except Exception:
            pass
    return articles


# ── Semantische Korrelation ────────────────────────────────────────────────────

def build_entity_graph(articles: list) -> dict:
    """
    Baut Entity-Co-Occurrence-Graph auf.
    Welche Entitäten erscheinen zusammen in Artikeln?
    """
    cooccurrence: dict[str, Counter] = defaultdict(Counter)
    entity_docs:  dict[str, list]   = defaultdict(list)   # Welche Artikel erwähnen Entität X

    for i, a in enumerate(articles):
        ents = a.get("entities", {})
        all_in_article = []
        for cat, items in ents.items():
            if cat in ("weapons", "actors", "organizations", "locations", "persons"):
                all_in_article.extend([(cat, item) for item in items])

        for j, (cat1, e1) in enumerate(all_in_article):
            key1 = f"{cat1}:{e1}"
            entity_docs[key1].append(i)
            for cat2, e2 in all_in_article[j+1:]:
                key2 = f"{cat2}:{e2}"
                cooccurrence[key1][key2] += 1
                cooccurrence[key2][key1] += 1

    return {
        "cooccurrence": {k: dict(v) for k, v in cooccurrence.items()},
        "entity_article_map": {k: v for k, v in entity_docs.items()},
    }


def find_semantic_clusters(articles: list, min_cooccurrence: int = 2) -> list[dict]:
    """
    Findet semantisch zusammenhängende Ereigniscluster.
    Beispiel: [Schahed + Charkiw + Ukraine] in 4 Artikeln → Cluster
    """
    graph = build_entity_graph(articles)
    cooc  = graph["cooccurrence"]
    art_map = graph["entity_article_map"]

    clusters = []
    seen_pairs = set()

    for entity, co_entities in cooc.items():
        for co_entity, count in co_entities.items():
            if count < min_cooccurrence:
                continue
            pair = tuple(sorted([entity, co_entity]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            # Artikel die beide Entitäten erwähnen
            arts_e1 = set(art_map.get(entity, []))
            arts_e2 = set(art_map.get(co_entity, []))
            shared  = arts_e1 & arts_e2

            if len(shared) >= min_cooccurrence:
                # Entitätsnamen ohne Kategorie-Prefix
                e1_name = entity.split(":", 1)[-1]
                e2_name = co_entity.split(":", 1)[-1]
                clusters.append({
                    "entities":    [e1_name, e2_name],
                    "entity_keys": [entity, co_entity],
                    "article_idx": sorted(shared),
                    "n_articles":  len(shared),
                    "confidence":  "HOCH" if count >= 3 else "MITTEL",
                    "title":       f"{e1_name} ↔ {e2_name}",
                    "type":        "semantic",
                })

    # Sortieren nach Häufigkeit
    clusters.sort(key=lambda x: -x["n_articles"])
    return clusters[:10]


def ner_context_for_llm(articles: list) -> str:
    """Gibt NER-basierten Kontext für LLM zurück."""
    # Entity-Frequenz berechnen
    all_entities: dict[str, Counter] = defaultdict(Counter)
    for a in articles:
        for cat, items in (a.get("entities") or {}).items():
            for item in items:
                all_entities[cat][item] += 1

    if not any(all_entities.values()):
        return ""

    lines = ["\n[NAMED ENTITY ANALYSE]"]

    cat_labels = {
        "persons":       "Personen",
        "organizations": "Organisationen",
        "locations":     "Orte",
        "weapons":       "Waffen/Systeme",
        "actors":        "Militärische Akteure",
    }
    for cat, label in cat_labels.items():
        top = all_entities.get(cat, Counter()).most_common(5)
        if top:
            items_str = ", ".join(f"{name} ({cnt}x)" for name, cnt in top)
            lines.append(f"  {label}: {items_str}")

    clusters = find_semantic_clusters(articles)
    if clusters:
        lines.append("  Semantische Cluster:")
        for cl in clusters[:3]:
            lines.append(f"    [{cl['confidence']}] {cl['title']} — {cl['n_articles']} Artikel")

    return "\n".join(lines)


def ner_status() -> dict:
    """Prüft ob spaCy verfügbar ist."""
    nlp = _get_nlp("de")
    if nlp:
        return {"available": True, "model": str(nlp.meta.get("name", "unbekannt")), "mode": "spaCy"}
    nlp_en = _get_nlp("en")
    if nlp_en:
        return {"available": True, "model": str(nlp_en.meta.get("name", "unbekannt")), "mode": "spaCy (EN)"}
    if _spacy_available is False:
        return {
            "available": False,
            "mode": "Regex-Fallback",
            "install": "pip install spacy --break-system-packages && python -m spacy download de_core_news_sm",
        }
    return {"available": False, "mode": "Regex-Fallback (kein Modell geladen)"}


# ── Direktaufruf zum Testen ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("NEXUS NER Test")
    print("─" * 50)

    status = ner_status()
    print(f"Status: {status['mode']}" + (f" ({status['model']})" if status.get("model") else ""))

    tests = [
        ("de", "Russische Streitkräfte haben Charkiw mit Shahed-Drohnen beschossen. General Surovikin koordinierte den Angriff."),
        ("en", "Ukrainian forces used HIMARS to strike Russian ammunition depots near Kherson. The Wagner Group retreated."),
    ]

    for lang, text in tests:
        print(f"\n[{lang.upper()}] {text[:80]}...")
        ents = extract_entities(text, lang=lang)
        for cat, items in ents.items():
            if items:
                print(f"  {cat}: {', '.join(items)}")
