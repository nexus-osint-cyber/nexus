"""
nexus_nlp.py  — T175
Multi-Language NLP für NEXUS.

Unterstützte Sprachen: EN, DE, AR, FA, RU, ZH + automatische Erkennung.

Strategie:
  1. Spracherkennung via langdetect (leichtgewichtig, kein API-Key)
  2. Übersetzung AR/FA/RU/ZH → EN via Ollama (lokal, offline)
     Fallback: googletrans (kostenlos, kein API-Key)
  3. NER via spaCy (en_core_web_sm) für EN-Text
  4. Sentiment-Klassifikation via Keyword-Matching (ohne ML-Dependency)
  5. Ort-Extraktion kombiniert aus NER + spezifischen Geo-Keywords

Verwendung:
  from nexus_nlp import analyze_text, extract_locations, translate_to_en
  python nexus_nlp.py --text "Корабли вошли в Ормузский пролив" --verbose
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

# ─── Optionale Imports ────────────────────────────────────────────────────────

try:
    from langdetect import detect as _langdetect, DetectorFactory
    DetectorFactory.seed = 42  # Deterministisch
    _LANGDETECT = True
except ImportError:
    _LANGDETECT = False

try:
    import spacy
    _SPACY_MODELS: dict[str, object] = {}
    _SPACY = True
except ImportError:
    _SPACY = False

# ─── Sprachen-Konfiguration ───────────────────────────────────────────────────

# Sprachen die Übersetzung benötigen
_TRANSLATE_LANGS = {"ar", "fa", "ru", "zh-cn", "zh-tw", "zh", "uk", "tr", "he"}

# spaCy Modell-Map
_SPACY_MODEL_MAP = {
    "en": "en_core_web_sm",
    "de": "de_core_news_sm",
    "fr": "fr_core_news_sm",
    "es": "es_core_news_sm",
}

# Militärische Schlüsselwörter für Sentiment (EN)
_ESCALATION_KEYWORDS = {
    "attack", "strike", "bomb", "missile", "fire", "killed", "airstrike",
    "invasion", "troops", "deploy", "offensive", "explosion", "artillery",
    "warship", "fighter", "drone", "intercept", "blockade", "sanction",
    "threat", "warning", "escalation", "conflict", "war", "crisis",
    "naval", "military", "weapon", "ammunition", "siege", "capture",
    "withdraw", "advance", "retreat", "casualties", "wounded", "dead",
}
_DE_ESCALATION_KEYWORDS = {
    "angriff", "rakete", "bombe", "beschuss", "explosion", "drohne",
    "truppen", "invasion", "blockade", "sanktion", "eskalation", "krieg",
    "kriegsschiff", "artillerie", "offensive", "rückzug", "besatzung",
    "streitkräfte", "soldat", "gefallen", "verwundet", "getötet",
}
_RU_KEYWORDS = {
    "атак", "удар", "ракет", "бомб", "взрыв", "войск", "наступлен",
    "отступ", "блокад", "санкц", "эскалац", "война", "военн",
    "корабл", "артиллер", "дрон", "захват", "осад",
}
_AR_KEYWORDS = {
    "هجوم", "ضربة", "صاروخ", "قصف", "انفجار", "قوات", "حصار",
    "عملية", "مقاتلة", "طائرة", "حرب", "عسكري", "سفينة",
}
_ZH_KEYWORDS = {
    "攻击", "导弹", "轰炸", "爆炸", "军队", "封锁", "制裁",
    "战舰", "无人机", "占领", "撤退", "冲突", "战争",
}

# Geo-Keywords für schnelle Location-Extraktion
_GEO_KEYWORDS = [
    "strait", "gulf", "sea", "ocean", "bay", "port", "harbor", "channel",
    "hormuz", "taiwan", "ukraine", "russia", "iran", "israel", "gaza",
    "bab el", "malacca", "suez", "black sea", "baltic", "red sea",
    "mediterranean", "persian gulf", "south china sea",
    "damascus", "kyiv", "moscow", "beijing", "tehran", "tel aviv",
    "baghdad", "kabul", "beirut", "riyadh", "doha", "ankara",
]

# ─── Datentypen ───────────────────────────────────────────────────────────────

@dataclass
class NlpResult:
    text_original: str
    text_en:       str
    language:      str          # ISO 639-1 Code
    translated:    bool
    entities:      list[dict]   # [{text, label, confidence}]
    locations:     list[str]    # Ortsnamen
    sentiment:     str          # "ESKALATION" | "NEUTRAL" | "DE-ESKALATION"
    sentiment_score: float      # 0.0–1.0
    keywords:      list[str]    # Militärische Schlagwörter gefunden
    processing_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "language":      self.language,
            "translated":    self.translated,
            "text_en":       self.text_en[:500],
            "entities":      self.entities,
            "locations":     self.locations,
            "sentiment":     self.sentiment,
            "sentiment_score": round(self.sentiment_score, 3),
            "keywords":      self.keywords[:15],
        }


# ─── Spracherkennung ─────────────────────────────────────────────────────────

def detect_language(text: str) -> str:
    """
    Erkennt die Sprache eines Textes.
    Gibt ISO 639-1 Code zurück (z.B. "en", "ar", "ru", "zh-cn").
    Fallback: "en"
    """
    if not text or len(text.strip()) < 10:
        return "en"

    # Script-basierte Schnellerkennung (zuverlässiger als statistische Methoden)
    # Arabisch/Farsi (beide benutzen arabisches Script)
    if re.search(r'[؀-ۿ]', text):
        # Farsi-spezifische Buchstaben: پ چ ژ گ
        if re.search(r'[پچژگ]', text):
            return "fa"
        return "ar"

    # Kyrillisch → Russisch (oder Ukrainisch)
    if re.search(r'[Ѐ-ӿ]', text):
        # Ukrainisch-spezifisch: і ї є ґ
        if re.search(r'[іїєґ]', text):
            return "uk"
        return "ru"

    # Chinesisch (CJK Unified Ideographs)
    if re.search(r'[一-鿿]', text):
        return "zh"

    # Japanisch (Hiragana/Katakana)
    if re.search(r'[぀-ヿ]', text):
        return "ja"

    # Hebräisch
    if re.search(r'[֐-׿]', text):
        return "he"

    # Türkisch-spezifische Buchstaben
    if re.search(r'[şğçöüıİĞŞÇÖÜ]', text):
        return "tr"

    # langdetect für lateinische Schriften
    if _LANGDETECT:
        try:
            lang = _langdetect(text)
            return lang
        except Exception:
            pass

    # Einfacher DE-Check
    de_words = {"und", "der", "die", "das", "ist", "nicht", "auch", "mit",
                "von", "für", "an", "in", "zu", "den", "bei", "nach"}
    words = set(text.lower().split())
    if len(words & de_words) >= 2:
        return "de"

    return "en"


# ─── Übersetzung ─────────────────────────────────────────────────────────────

def _translate_ollama(text: str, source_lang: str) -> Optional[str]:
    """Übersetzt via Ollama (lokal, keine API-Kosten)."""
    lang_names = {
        "ar": "Arabic", "fa": "Persian/Farsi", "ru": "Russian",
        "zh": "Chinese", "zh-cn": "Chinese", "zh-tw": "Chinese (Traditional)",
        "uk": "Ukrainian", "tr": "Turkish", "he": "Hebrew", "de": "German",
    }
    lang_name = lang_names.get(source_lang, source_lang)

    prompt = (
        f"Translate the following {lang_name} text to English. "
        f"Output ONLY the English translation, nothing else:\n\n{text[:800]}"
    )
    payload = json.dumps({
        "model": "llama3.2",   # Schnelles Modell für Translation
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 400},
    }).encode()

    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
            result = data.get("response", "").strip()
            if result and len(result) > 5:
                return result
    except Exception:
        pass

    # Fallback: mistral
    payload2 = json.dumps({
        "model": "mistral",
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 400},
    }).encode()
    try:
        req2 = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload2,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req2, timeout=20) as resp2:
            data2 = json.loads(resp2.read().decode())
            result2 = data2.get("response", "").strip()
            if result2 and len(result2) > 5:
                return result2
    except Exception:
        pass

    return None


def _translate_googletrans(text: str, source_lang: str) -> Optional[str]:
    """Übersetzt via Google Translate (kostenlos, kein API-Key nötig)."""
    try:
        from googletrans import Translator
        translator = Translator()
        result = translator.translate(text[:500], src=source_lang, dest="en")
        return result.text
    except Exception:
        pass

    # Direkter HTTP-Fallback (inoffiziell)
    try:
        import urllib.parse
        sl = source_lang.split("-")[0]  # zh-cn → zh
        url = (
            "https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl={sl}&tl=en&dt=t&q={urllib.parse.quote(text[:400])}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            parts = data[0]
            return "".join(p[0] for p in parts if p[0])
    except Exception:
        pass

    return None


def translate_to_en(text: str, source_lang: str = "") -> tuple[str, bool]:
    """
    Übersetzt Text nach Englisch.
    Gibt (übersetzter_text, wurde_übersetzt) zurück.
    Wenn keine Übersetzung nötig oder möglich: Original zurück.
    """
    if not source_lang:
        source_lang = detect_language(text)

    # Kein Übersetzen nötig
    if source_lang in ("en", "unknown"):
        return text, False

    if source_lang not in _TRANSLATE_LANGS and source_lang not in ("de", "fr", "es"):
        return text, False

    # Ollama bevorzugen (lokal, privat)
    result = _translate_ollama(text, source_lang)
    if result:
        return result, True

    # Fallback: googletrans
    result = _translate_googletrans(text, source_lang)
    if result:
        return result, True

    return text, False


# ─── NER ─────────────────────────────────────────────────────────────────────

def _load_spacy_model(lang: str = "en") -> Optional[object]:
    """Lädt spaCy-Modell lazy."""
    if not _SPACY:
        return None
    model_name = _SPACY_MODEL_MAP.get(lang, "en_core_web_sm")
    if model_name not in _SPACY_MODELS:
        try:
            _SPACY_MODELS[model_name] = spacy.load(model_name)
        except OSError:
            # Modell nicht installiert
            try:
                import subprocess, sys
                subprocess.run(
                    [sys.executable, "-m", "spacy", "download", model_name,
                     "--break-system-packages"],
                    capture_output=True, timeout=60,
                )
                _SPACY_MODELS[model_name] = spacy.load(model_name)
            except Exception:
                return None
    return _SPACY_MODELS.get(model_name)


def extract_entities_spacy(text: str, lang: str = "en") -> list[dict]:
    """Extrahiert Named Entities via spaCy."""
    nlp = _load_spacy_model(lang)
    if nlp is None:
        return []
    try:
        doc = nlp(text[:1000])
        return [
            {"text": ent.text, "label": ent.label_, "confidence": 0.9}
            for ent in doc.ents
            if ent.label_ in ("GPE", "LOC", "ORG", "PERSON", "FAC", "NORP")
        ]
    except Exception:
        return []


def extract_entities_regex(text: str) -> list[dict]:
    """
    Regex-basierte Entitäts-Extraktion als Fallback.
    Erkennt Großgeschriebene Wörter, bekannte Militär-Abkürzungen, Koordinaten.
    """
    entities = []

    # Koordinaten (z.B. "26.5°N 56.2°E" oder "26.5, 56.2")
    coord_pattern = r'(\d{1,3}\.?\d*)[°\s]?[NS][,\s]+(\d{1,3}\.?\d*)[°\s]?[EW]'
    for m in re.finditer(coord_pattern, text, re.IGNORECASE):
        entities.append({"text": m.group(0), "label": "COORD", "confidence": 0.95})

    # Militärische Einheiten (z.B. "3rd Battalion", "12th Fleet")
    unit_pattern = r'\b(\d{1,3}(?:st|nd|rd|th)?\s+(?:Battalion|Brigade|Division|Fleet|Squadron|Corps|Army|Group|Wing|Regiment))\b'
    for m in re.finditer(unit_pattern, text, re.IGNORECASE):
        entities.append({"text": m.group(0), "label": "MIL_UNIT", "confidence": 0.85})

    # Schiffsnamen (USS, USNS, HMS, RFS)
    ship_pattern = r'\b(USS|USNS|HMS|RFS|CNS|INS|HNLMS)\s+[A-Z][A-Z\s]+\b'
    for m in re.finditer(ship_pattern, text):
        entities.append({"text": m.group(0).strip(), "label": "SHIP", "confidence": 0.9})

    # Callsigns (Großbuchstaben + Ziffern, z.B. "JAKE11", "COBRA21")
    call_pattern = r'\b([A-Z]{3,8}\d{1,3})\b'
    for m in re.finditer(call_pattern, text):
        cs = m.group(0)
        if len(cs) >= 5:
            entities.append({"text": cs, "label": "CALLSIGN", "confidence": 0.7})

    return entities


def extract_entities(text: str, lang: str = "en") -> list[dict]:
    """Kombiniert spaCy + Regex NER."""
    entities = extract_entities_spacy(text, lang)
    regex_ents = extract_entities_regex(text)
    # Deduplizieren
    seen = {e["text"].lower() for e in entities}
    for e in regex_ents:
        if e["text"].lower() not in seen:
            entities.append(e)
            seen.add(e["text"].lower())
    return entities[:30]


# ─── Locations ────────────────────────────────────────────────────────────────

def extract_locations(text: str, lang: str = "en") -> list[str]:
    """Extrahiert Ortsnamen aus Text."""
    locations = []

    # Aus NER
    entities = extract_entities(text, lang)
    locations.extend(
        e["text"] for e in entities
        if e["label"] in ("GPE", "LOC", "FAC", "COORD")
    )

    # Geo-Keyword Matching
    text_lower = text.lower()
    for kw in _GEO_KEYWORDS:
        if kw in text_lower:
            # Finde den originalen Wortlaut im Text
            pattern = re.compile(re.escape(kw), re.IGNORECASE)
            m = pattern.search(text)
            if m:
                locations.append(m.group(0))

    # Deduplizieren (case-insensitive)
    seen: set[str] = set()
    unique = []
    for loc in locations:
        key = loc.lower().strip()
        if key not in seen and len(key) > 2:
            seen.add(key)
            unique.append(loc.strip())

    return unique[:15]


# ─── Sentiment / Eskalations-Analyse ─────────────────────────────────────────

def _keyword_sentiment(text: str, lang: str) -> tuple[str, float, list[str]]:
    """
    Keyword-basiertes Sentiment speziell für Sicherheits-/Konflikt-Texte.
    Gibt (sentiment, score, matched_keywords) zurück.
    """
    text_lower = text.lower()
    matched: list[str] = []

    # Sprach-spezifische Keywords
    if lang in ("ru", "uk"):
        kw_set = _RU_KEYWORDS
    elif lang in ("ar", "fa"):
        kw_set = _AR_KEYWORDS
    elif lang in ("zh", "zh-cn", "zh-tw"):
        kw_set = _ZH_KEYWORDS
    elif lang == "de":
        kw_set = _DE_ESCALATION_KEYWORDS
    else:
        kw_set = _ESCALATION_KEYWORDS

    for kw in kw_set:
        if kw in text_lower:
            matched.append(kw)

    # Für nicht-EN auch EN-Keywords prüfen (falls Text schon übersetzt)
    if lang != "en":
        for kw in _ESCALATION_KEYWORDS:
            if kw in text_lower and kw not in matched:
                matched.append(kw)

    n = len(matched)
    text_len = max(len(text.split()), 1)

    # Score normalisiert auf 0–1
    # Mehr Treffer = höherer Score, aber mit Diminishing Returns
    score = min(1.0, (n / max(text_len / 20, 3)) * 1.5)

    if score >= 0.5:
        sentiment = "ESKALATION"
    elif score >= 0.15:
        sentiment = "ERHÖHT"
    else:
        sentiment = "NEUTRAL"

    return sentiment, score, matched[:10]


# ─── Haupt-Analyse ───────────────────────────────────────────────────────────

def analyze_text(
    text: str,
    source_lang: str = "",
    translate: bool = True,
) -> NlpResult:
    """
    Vollständige NLP-Pipeline für einen Text.

    1. Spracherkennung
    2. Übersetzung (falls nötig und translate=True)
    3. NER
    4. Location-Extraktion
    5. Sentiment-Klassifikation

    Beispiel:
        result = analyze_text("Корабли вошли в Ормузский пролив")
        print(result.language)     # "ru"
        print(result.text_en)      # "Ships entered the Strait of Hormuz"
        print(result.locations)    # ["Strait of Hormuz"]
        print(result.sentiment)    # "ESKALATION"
    """
    t0 = time.time()

    # 1. Spracherkennung
    lang = source_lang or detect_language(text)

    # 2. Übersetzung
    text_en = text
    was_translated = False
    if translate and lang != "en":
        text_en, was_translated = translate_to_en(text, lang)

    # 3. Entitäten (auf EN-Text wenn übersetzt, sonst Original)
    nlp_text = text_en if was_translated else text
    nlp_lang = "en" if was_translated else lang
    entities = extract_entities(nlp_text, nlp_lang)

    # 4. Locations
    locations = extract_locations(nlp_text, nlp_lang)
    # Zusätzlich aus Original-Text für Eigennamen die nicht übersetzt wurden
    if was_translated:
        orig_locs = extract_locations(text, lang)
        seen = {l.lower() for l in locations}
        for loc in orig_locs:
            if loc.lower() not in seen:
                locations.append(loc)

    # 5. Sentiment — auf Original-Text (für Genauigkeit in Originalsprache)
    sentiment, score, keywords = _keyword_sentiment(text, lang)
    # Zusätzlich auf EN-Übersetzung prüfen
    if was_translated:
        sent2, score2, kw2 = _keyword_sentiment(text_en, "en")
        if score2 > score:
            sentiment = sent2
            score = max(score, score2)
        keywords = list(set(keywords + kw2))[:10]

    return NlpResult(
        text_original=text,
        text_en=text_en,
        language=lang,
        translated=was_translated,
        entities=entities,
        locations=locations[:10],
        sentiment=sentiment,
        sentiment_score=round(score, 3),
        keywords=keywords[:10],
        processing_ms=round((time.time() - t0) * 1000, 1),
    )


def analyze_batch(
    texts: list[str],
    translate: bool = True,
    max_items: int = 50,
) -> list[NlpResult]:
    """Analysiert eine Liste von Texten."""
    results = []
    for text in texts[:max_items]:
        if not text or not text.strip():
            continue
        try:
            results.append(analyze_text(text, translate=translate))
        except Exception:
            pass
    return results


# ─── Integration für Telegram/GDELT ──────────────────────────────────────────

def enrich_telegram_messages(messages: list[dict]) -> list[dict]:
    """
    Reichert Telegram-Nachrichten mit NLP-Analyse an.
    Erwartet Liste von {text, channel, ts, ...} Dicts.
    Fügt {language, text_en, locations, sentiment, keywords} hinzu.
    """
    enriched = []
    for msg in messages:
        text = msg.get("text") or msg.get("message") or ""
        if not text:
            enriched.append(msg)
            continue
        try:
            result = analyze_text(text, translate=True)
            msg = dict(msg)
            msg["language"]  = result.language
            msg["text_en"]   = result.text_en
            msg["locations"] = result.locations
            msg["sentiment"] = result.sentiment
            msg["keywords"]  = result.keywords
            msg["entities"]  = result.entities[:5]
        except Exception:
            pass
        enriched.append(msg)
    return enriched


def enrich_gdelt_events(events: list[dict]) -> list[dict]:
    """
    Reichert GDELT-Events mit NLP-Analyse an.
    Extrahiert Locations aus Title/URL und bewertet Sentiment.
    """
    enriched = []
    for evt in events:
        title = evt.get("title") or evt.get("headline") or ""
        if not title:
            enriched.append(evt)
            continue
        try:
            result = analyze_text(title, translate=True)
            evt = dict(evt)
            if result.locations and not evt.get("locations"):
                evt["locations"] = result.locations
            evt["sentiment"]  = result.sentiment
            evt["keywords"]   = result.keywords
            evt["language"]   = result.language
        except Exception:
            pass
        enriched.append(evt)
    return enriched


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NEXUS Multi-Language NLP")
    parser.add_argument("--text",        help="Text zum Analysieren")
    parser.add_argument("--lang",        default="", help="Sprache (auto wenn leer)")
    parser.add_argument("--no-translate",action="store_true", help="Keine Übersetzung")
    parser.add_argument("--detect-only", action="store_true", help="Nur Spracherkennung")
    parser.add_argument("--test",        action="store_true", help="Testfälle durchlaufen")
    parser.add_argument("--verbose",     action="store_true", help="Ausführliche Ausgabe")
    parser.add_argument("--json",        action="store_true", help="JSON-Ausgabe")
    args = parser.parse_args()

    if args.test:
        test_cases = [
            ("Корабли вошли в Ормузский пролив", "ru"),
            ("الهجوم على ناقلة النفط في خليج عمان", "ar"),
            ("伊朗革命卫队在霍尔木兹海峡扣押船只", "zh"),
            ("کشتی‌های جنگی وارد خلیج فارس شدند", "fa"),
            ("USS Abraham Lincoln enters Persian Gulf amid tensions", "en"),
            ("Russische Kriegsschiffe im Schwarzen Meer", "de"),
        ]
        print("=== NEXUS NLP Testfälle ===\n")
        for text, expected_lang in test_cases:
            r = analyze_text(text, translate=not args.no_translate)
            status = "✅" if r.language == expected_lang else f"⚠️(erwartet:{expected_lang})"
            print(f"{status} [{r.language}] {r.sentiment:12s} | {text[:50]}")
            if r.translated:
                print(f"   → EN: {r.text_en[:70]}")
            if r.locations:
                print(f"   📍 {', '.join(r.locations[:3])}")
            if r.keywords:
                print(f"   🔑 {', '.join(r.keywords[:5])}")
            print(f"   ⏱️  {r.processing_ms:.0f}ms")
            print()
    elif args.text:
        if args.detect_only:
            lang = detect_language(args.text)
            print(f"Sprache: {lang}")
        else:
            r = analyze_text(args.text, args.lang, not args.no_translate)
            if args.json:
                print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(f"\n=== NLP Analyse ===")
                print(f"  Sprache:   {r.language} {'(übersetzt)' if r.translated else ''}")
                print(f"  Sentiment: {r.sentiment} ({r.sentiment_score:.2f})")
                print(f"  Orte:      {', '.join(r.locations) or '—'}")
                print(f"  Keywords:  {', '.join(r.keywords) or '—'}")
                print(f"  Entitäten: {', '.join(e['text']+'/'+e['label'] for e in r.entities[:5]) or '—'}")
                print(f"  Zeit:      {r.processing_ms:.0f}ms")
                if r.translated and args.verbose:
                    print(f"\n  Original:    {r.text_original[:200]}")
                    print(f"  Übersetzung: {r.text_en[:200]}")
    else:
        parser.print_help()
