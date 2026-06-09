"""
NEXUS - Auto-Übersetzungsmodul
Übersetzt russische, arabische, englische Artikel auf Deutsch.
Nutzt LibreTranslate (lokal oder öffentliche Instanz) – kostenlos, kein Key.

SETUP (einmalig):
  pip install libretranslatepy --break-system-packages
  ODER: pip install requests  (nur für API-Modus)

API-Modi:
  1. Öffentliche LibreTranslate-Instanz (kein Setup, aber Rate-Limit)
  2. Lokale Instanz (keine Limits):
       pip install libretranslate --break-system-packages
       libretranslate --host 0.0.0.0 --port 5000
"""

from __future__ import annotations
import re
from typing import Optional

import requests

# Öffentliche LibreTranslate-Instanzen (Fallback-Kette)
_LT_HOSTS = [
    "https://libretranslate.de",
    "https://translate.argosopentech.com",
    "https://libretranslate.com",
]
_LOCAL_HOST = "http://localhost:5000"
REQUEST_TIMEOUT = 8

# Sprach-Erkennung (einfache Heuristik für OSINT-Quellen)
_CYRILLIC   = re.compile(r'[Ѐ-ӿ]')
_ARABIC     = re.compile(r'[؀-ۿ]')
_LATIN_ONLY = re.compile(r'^[\x00-\x7F\s\d\W]+$')


def detect_language(text: str) -> str:
    """Erkennt Sprache anhand von Zeichensätzen. Gibt ISO-Code zurück."""
    if _CYRILLIC.search(text):
        return "ru"
    if _ARABIC.search(text):
        return "ar"
    # Englisch vs Deutsch: sehr vereinfacht
    en_words = {"the","is","are","was","were","in","of","to","and","for","with","has","have"}
    words = set(text.lower().split()[:20])
    if len(words & en_words) >= 3:
        return "en"
    return "de"  # Default: Deutsch (kein Übersetzungsbedarf)


def _translate_deepl_free(text: str, source: str, target: str = "DE") -> Optional[str]:
    """
    Versucht DeepL Free API (500.000 Zeichen/Monat kostenlos).
    Benötigt DEEPL_API_KEY in config.py (kostenlos auf deepl.com/pro-api).
    """
    try:
        import config  # type: ignore
        key = getattr(config, "DEEPL_API_KEY", "")
    except ImportError:
        key = ""
    if not key:
        return None
    # DeepL Free: api-free.deepl.com, Paid: api.deepl.com
    base = "https://api-free.deepl.com" if key.endswith(":fx") else "https://api.deepl.com"
    # Sprachcode-Mapping (NEXUS → DeepL)
    _lang_map = {"ru": "RU", "ar": "AR", "en": "EN", "de": "DE", "uk": "UK"}
    src = _lang_map.get(source.lower(), source.upper())
    tgt = _lang_map.get(target.lower(), target.upper())
    if src == tgt:
        return text
    try:
        r = requests.post(
            f"{base}/v2/translate",
            headers={"Authorization": f"DeepL-Auth-Key {key}"},
            data={"text": text[:500], "source_lang": src, "target_lang": tgt},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 200:
            result = r.json().get("translations", [{}])[0].get("text", "")
            return result if result else None
    except Exception:
        pass
    return None


def translate(text: str, source: str = "auto", target: str = "de",
              max_chars: int = 500) -> Optional[str]:
    """
    Übersetzt Text. Fallback-Kette (komplett kostenlos, kein Key nötig):
      1. DeepL Free (500k Zeichen/Monat – falls Key in config.py)
      2. LibreTranslate lokal (localhost:5000 – falls selbst gehostet)
      3. LibreTranslate öffentlich (Rate-Limits)
      4. MyMemory API (kostenlos, kein Key, 5.000 Wörter/Tag)
      5. Google Translate (inoffiziell, kein Key, Rate-Limit)
    Gibt None zurück wenn alle Methoden fehlschlagen.
    """
    if not text or len(text.strip()) < 5:
        return None
    if source == "de" or target == "de" and source == "de":
        return text  # Schon Deutsch

    text = text[:max_chars]

    # 0. DeepL Free (beste Qualität, 500k Zeichen/Monat kostenlos)
    deepl_result = _translate_deepl_free(text, source=source, target=target)
    if deepl_result and deepl_result != text:
        return deepl_result

    # 1. Lokale Instanz versuchen (schnellste, keine Rate-Limits)
    try:
        r = requests.post(
            f"{_LOCAL_HOST}/translate",
            json={"q": text, "source": source, "target": target, "format": "text"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 200:
            result = r.json().get("translatedText", "")
            if result:
                return result
    except Exception:
        pass

    # 2. Öffentliche Instanzen (Fallback)
    for host in _LT_HOSTS:
        try:
            r = requests.post(
                f"{host}/translate",
                json={"q": text, "source": source, "target": target, "format": "text"},
                timeout=REQUEST_TIMEOUT,
                headers={"Content-Type": "application/json"},
            )
            if r.status_code == 200:
                result = r.json().get("translatedText", "")
                if result and result != text:
                    return result
        except Exception:
            continue

    # 3. MyMemory API (kostenlos, kein Key, 5.000 Wörter/Tag)
    try:
        lang_pair = f"{source}|{target}" if source != "auto" else f"en|{target}"
        r = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:400], "langpair": lang_pair},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            result = data.get("responseData", {}).get("translatedText", "")
            # MyMemory gibt manchmal Fehlermeldungen als Übersetzung zurück
            if result and result != text and len(result) > 3 and "INVALID" not in result.upper():
                return result
    except Exception:
        pass

    # 4. Google Translate (inoffiziell, kein Key, Rate-Limit möglich)
    try:
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx", "sl": source, "tl": target,
                "dt": "t", "q": text[:400],
            },
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data and isinstance(data[0], list):
                result = "".join(part[0] for part in data[0] if part and part[0])
                if result and result != text:
                    return result
    except Exception:
        pass

    return None  # Alle Methoden fehlgeschlagen


def enrich_articles_with_translation(articles: list) -> list:
    """
    Fügt jedem Artikel eine deutsche Übersetzung hinzu wenn nötig.
    Übersetzt Titel + Summary für russische/arabische/englische Artikel.
    Gibt angereicherte Artikel-Liste zurück.
    """
    for a in articles:
        title   = (a.get("title") or "").strip()
        summary = (a.get("summary") or "").strip()

        lang = detect_language(title or summary)
        a["lang"] = lang

        if lang == "de":
            continue  # Kein Übersetzungsbedarf

        # Titel übersetzen
        translated_title = translate(title, source=lang, target="de", max_chars=200)
        if translated_title and translated_title != title:
            a["title_original"] = title
            a["title"]          = translated_title
            a["translated"]     = True

        # Summary übersetzen
        if summary:
            translated_summary = translate(summary, source=lang, target="de", max_chars=400)
            if translated_summary and translated_summary != summary:
                a["summary_original"] = summary
                a["summary"]          = translated_summary

    return articles


def translation_status() -> dict:
    """Prüft ob LibreTranslate erreichbar ist."""
    # Lokal
    try:
        r = requests.get(f"{_LOCAL_HOST}/languages", timeout=3)
        if r.status_code == 200:
            langs = [l["code"] for l in r.json()]
            return {"available": True, "mode": "lokal", "languages": langs}
    except Exception:
        pass

    # Öffentlich
    for host in _LT_HOSTS:
        try:
            r = requests.get(f"{host}/languages", timeout=4)
            if r.status_code == 200:
                return {"available": True, "mode": host, "languages": ["ru","ar","en","de"]}
        except Exception:
            continue

    return {
        "available": False,
        "mode": "nicht erreichbar",
        "setup": (
            "Lokale Instanz starten:\n"
            "  pip install libretranslate --break-system-packages\n"
            "  libretranslate --host 0.0.0.0 --port 5000\n"
            "Öffentliche Instanzen sind auch verfügbar aber mit Rate-Limit."
        ),
    }


# ── Direktaufruf zum Testen ───────────────────────────────────────────────────
if __name__ == "__main__":
    print("NEXUS Übersetzungs-Test")
    print("─" * 40)

    status = translation_status()
    if status["available"]:
        print(f"✅ LibreTranslate erreichbar: {status['mode']}")
    else:
        print(f"⚠ LibreTranslate nicht erreichbar")
        print(status.get("setup", ""))

    tests = [
        ("ru", "Российские войска нанесли удар по Харькову"),
        ("ar", "قوات روسية تضرب خاركيف"),
        ("en", "Russian forces strike Kharkiv, casualties reported"),
    ]
    for lang, text in tests:
        print(f"\n[{lang.upper()}] {text}")
        result = translate(text, source=lang, target="de")
        if result:
            print(f"[DE]  {result}")
        else:
            print("[DE]  Übersetzung fehlgeschlagen")
