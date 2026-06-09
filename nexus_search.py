"""
NEXUS - Search
Webrecherche via DuckDuckGo. Immer mit aktuellem Jahr in der Anfrage
und streng zeitgefiltertem Fallback-Mechanismus.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

import config


# =====================================================
# Erkennung: Suche / News / Analyse noetig?
# =====================================================

def needs_search(text: str) -> bool:
    """
    Prueft ob eine Websuche noetig ist.
    Lieber einmal zu viel suchen als halluzinieren.
    """
    if not text:
        return False
    lower = text.lower()

    # Immer suchen bei bestimmten Frageworten + Laenderangaben
    # (verhindert dass das Modell aus Trainingswissen halluziniert)
    question_starters = [
        "wie ist", "wie war", "wie wird", "wie laeuft",
        "was ist mit", "was passiert", "was gibt es",
        "wer ist", "wer war", "wann ist", "wo ist",
        "was weisst du ueber", "was weißt du ueber",
        "erklaer mir", "erzaehl mir", "berichte",
    ]
    if any(lower.startswith(q) or (" " + q) in lower for q in question_starters):
        return True

    # Trigger-Woerter aus config
    return any(t in lower for t in config.SEARCH_TRIGGER_WORDS)


def needs_news_search(text: str) -> bool:
    if not text:
        return False
    news_triggers = getattr(config, "SEARCH_NEWS_TRIGGERS", [
        "news", "nachrichten", "schlagzeilen", "neuigkeiten",
        "was ist passiert", "heute", "gestern", "letzte woche",
        "diese woche", "aktuell", "was gibt es neues",
    ])
    return any(t in text.lower() for t in news_triggers)


# =====================================================
# Hilfsfunktionen
# =====================================================

def _current_year() -> str:
    return str(datetime.now().year)


def _add_year(query: str) -> str:
    """Haengt das aktuelle Jahr an die Anfrage, wenn es nicht schon drin ist."""
    year = _current_year()
    if year in query:
        return query
    return f"{query} {year}"


def _fmt_date(date_str: str) -> str:
    if not date_str:
        return "Datum unbekannt"
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        raw = str(date_str)[:10]
        try:
            return datetime.strptime(raw, "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError:
            return raw


def _age_days(date_str: str) -> int:
    """Gibt das Alter in Tagen zurueck, oder -1 wenn unbekannt."""
    if not date_str:
        return -1
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - dt).days)
    except (ValueError, TypeError):
        return -1


def _age_warning(date_str: str) -> str:
    days = _age_days(date_str)
    if days < 0:
        return ""
    if days > 730:   # > 2 Jahre: komplett ablehnen
        return f" ⛔ NICHT VERWENDEN - {days // 365} Jahre alt! Nur historisch relevant."
    if days > 365:   # > 1 Jahr
        return f" ⚠ STARK VERALTET ({days} Tage) - fuer aktuelle Fakten ungeeignet"
    if days > 90:    # > 3 Monate
        return f" ⚠ VERALTET ({days} Tage alt)"
    if days > 30:
        return f" ({days} Tage alt)"
    return ""


def _age_label_historical(date_str: str) -> str:
    """
    Historischer Modus: Alter ist kein Makel, sondern Information.
    Alte Quellen werden als 'historische Quelle' markiert, nicht geblockt.
    """
    days = _age_days(date_str)
    if days < 0:
        return ""
    years = days // 365
    if years >= 50:
        return f" 📜 HISTORISCH - {years} Jahre alt"
    if years >= 20:
        return f" 📜 {years} Jahre alt (zeitgeschichtliche Quelle)"
    if years >= 10:
        return f" 📅 {years} Jahre alt"
    if years >= 2:
        return f" ({years} Jahre alt)"
    if days > 30:
        return f" ({days} Tage alt)"
    return ""


def _domain(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1) if m else url


# =====================================================
# DDGS Import
# =====================================================

def _import_ddgs():
    try:
        from ddgs import DDGS
        return DDGS
    except ImportError:
        pass
    try:
        from duckduckgo_search import DDGS
        return DDGS
    except ImportError as exc:
        raise RuntimeError("ddgs nicht installiert.") from exc


# =====================================================
# Allgemeine Websuche (mit Zeitfilter + Fallback)
# =====================================================

def web_search(query: str, max_results: Optional[int] = None) -> Optional[str]:
    """
    Websuche mit aktuellem Jahr in der Anfrage.
    Versucht erst den konfigurierten Zeitfilter, faellt dann auf "Monat", dann "Jahr" zurueck.
    """
    if not query:
        return None
    if max_results is None:
        max_results = config.SEARCH_MAX_RESULTS

    DDGS = _import_ddgs()
    today = datetime.now().strftime("%d.%m.%Y")
    year_query = _add_year(query)

    # Timelimit-Kaskade: Woche -> Monat -> Jahr -> kein Filter
    base_limit = getattr(config, "SEARCH_TIMELIMIT", "w")
    timelimit_cascade = {
        "d": ["d", "w", "m"],
        "w": ["w", "m", "y"],
        "m": ["m", "y", None],
        "y": ["y", None],
    }.get(base_limit, ["w", "m", None])

    results = []
    for tlimit in timelimit_cascade:
        results = []
        try:
            with DDGS() as ddgs:
                kwargs = dict(
                    region=config.SEARCH_REGION,
                    safesearch="moderate",
                    max_results=max_results,
                )
                if tlimit:
                    kwargs["timelimit"] = tlimit
                for hit in ddgs.text(year_query, **kwargs):
                    results.append(hit)
            if results:
                break
        except Exception:
            continue

    if not results:
        return None

    today_header = "[Heute: {}] Websuche: {!r}\n".format(today, query)
    lines = [today_header]
    for i, hit in enumerate(results, 1):
        title = hit.get("title", "") or hit.get("heading", "")
        body  = hit.get("body", "") or hit.get("snippet", "")
        url   = hit.get("href", "") or hit.get("url", "")
        raw_d = hit.get("date", "")
        date  = _fmt_date(raw_d) if raw_d else ""
        age   = _age_warning(raw_d) if raw_d else ""
        date_line = "    Datum: {}{}\n".format(date, age) if date else ""
        lines.append("[{}] {}\n{}    {}\n    Quelle: {} | {}".format(
            i, title, date_line, body, _domain(url), url
        ))
    return "\n".join(lines)


# =====================================================
# Nachrichtensuche (ddgs.news - mit echten Datumsstempeln)
# =====================================================

def news_search(query: str, max_results: Optional[int] = None) -> Optional[str]:
    """Aktuelle Nachrichten via ddgs.news(). Immer mit Datum."""
    if not query:
        return None
    if max_results is None:
        max_results = getattr(config, "SEARCH_NEWS_MAX_RESULTS", config.SEARCH_MAX_RESULTS)

    DDGS = _import_ddgs()
    today = datetime.now().strftime("%d.%m.%Y")
    year_query = _add_year(query)

    results = []
    # Kaskade: heute -> Woche -> Monat -> kein Filter
    for tlimit in ["d", "w", "m", None]:
        results = []
        try:
            with DDGS() as ddgs:
                kwargs = dict(region=config.SEARCH_REGION, safesearch="moderate",
                              max_results=max_results)
                if tlimit:
                    kwargs["timelimit"] = tlimit
                for hit in ddgs.news(year_query, **kwargs):
                    results.append(hit)
            if results:
                break
        except Exception:
            continue

    if not results:
        return web_search(query, max_results)  # Fallback auf allgemeine Suche

    lines = ["[Heute: {}] Nachrichtensuche: {!r}\n".format(today, query)]
    for i, hit in enumerate(results, 1):
        title  = hit.get("title", "")
        body   = hit.get("body", "") or hit.get("excerpt", "")
        url    = hit.get("url", "") or hit.get("href", "")
        source = hit.get("source", "") or _domain(url)
        raw_d  = hit.get("date", "")
        date   = _fmt_date(raw_d)
        age    = _age_warning(raw_d)
        lines.append("[{}] {}\n    Datum: {}{}\n    {}\n    Quelle: {} | {}".format(
            i, title, date, age, body, source, url
        ))
    return "\n".join(lines)


# =====================================================
# Mehrseitige Analyse-Recherche
# =====================================================

def multi_angle_search(query: str, max_per_source: int = 6) -> str:
    """Sammelt Quellen aus News + Web fuer objektive Lagebilder."""
    today = datetime.now().strftime("%d.%m.%Y")
    year_query = _add_year(query)
    all_results = []
    seen_urls: set = set()

    try:
        DDGS = _import_ddgs()
    except RuntimeError as exc:
        return "(Recherche nicht verfuegbar: {})".format(exc)

    # News aus verschiedenen Winkeln
    for q in [year_query, year_query + " aktuell", query + " aktuelle Entwicklungen"]:
        if len(all_results) >= max_per_source * 2:
            break
        for tlimit in ["d", "w", "m"]:
            try:
                with DDGS() as ddgs:
                    for hit in ddgs.news(q, region=config.SEARCH_REGION,
                                         safesearch="moderate", max_results=max_per_source,
                                         timelimit=tlimit):
                        url = hit.get("url", "") or hit.get("href", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            all_results.append(("news", hit))
                if all_results:
                    break
            except Exception:
                continue

    # Web-Hintergrundinformationen
    for q in [year_query, year_query + " Hintergruende Analyse"]:
        if len(all_results) >= max_per_source * 4:
            break
        for tlimit in ["w", "m", None]:
            try:
                with DDGS() as ddgs:
                    kwargs = dict(region=config.SEARCH_REGION, safesearch="moderate",
                                  max_results=max_per_source)
                    if tlimit:
                        kwargs["timelimit"] = tlimit
                    for hit in ddgs.text(q, **kwargs):
                        url = hit.get("href", "") or hit.get("url", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            all_results.append(("web", hit))
                if [r for r in all_results if r[0] == "web"]:
                    break
            except Exception:
                continue

    if not all_results:
        return "(Keine Quellen gefunden fuer: {!r})".format(query)

    header = (
        "[ANALYSE-RECHERCHE | {}]\n"
        "Thema: {}\n"
        "Quellen gesamt: {}\n"
        "WICHTIG: Nur diese Quellen verwenden - nichts erfinden.\n"
    ).format(today, query, len(all_results))

    lines = [header]
    for i, (src_type, hit) in enumerate(all_results, 1):
        if src_type == "news":
            title  = hit.get("title", "")
            body   = hit.get("body", "") or hit.get("excerpt", "")
            url    = hit.get("url", "")
            source = hit.get("source", "") or _domain(url)
            raw_d  = hit.get("date", "")
        else:
            title  = hit.get("title", "") or hit.get("heading", "")
            body   = hit.get("body", "") or hit.get("snippet", "")
            url    = hit.get("href", "") or hit.get("url", "")
            source = _domain(url)
            raw_d  = hit.get("date", "")

        date = _fmt_date(raw_d) if raw_d else "Datum unbekannt"
        age  = _age_warning(raw_d)
        lines.append("[Q{}] {}\n    Quelle: {} | Datum: {}{}\n    {}\n    URL: {}".format(
            i, title, source, date, age, body, url
        ))
    return "\n".join(lines)


# =====================================================
# Historische Suche (KEIN Zeitfilter, KEIN Jahresanhaengsel)
# =====================================================

def historical_search(query: str, max_results: Optional[int] = None) -> Optional[str]:
    """
    Historischer Modus: Sucht OHNE Zeitfilter und OHNE aktuelles Jahr.
    Alte Quellen sind ausdrücklich erwünscht - keine Alterswarnungen.
    Durchsucht das komplette Web ohne Datum-Einschraenkung.
    """
    if not query:
        return None
    if max_results is None:
        max_results = max(config.SEARCH_MAX_RESULTS + 4, 10)  # mehr Ergebnisse im hist. Modus

    DDGS = _import_ddgs()
    today = datetime.now().strftime("%d.%m.%Y")

    results = []
    try:
        with DDGS() as ddgs:
            kwargs = dict(
                region=config.SEARCH_REGION,
                safesearch="moderate",
                max_results=max_results,
                # KEIN timelimit -> ganzes Internet, alle Jahrzehnte
            )
            for hit in ddgs.text(query, **kwargs):
                results.append(hit)
    except Exception:
        pass

    if not results:
        return None

    header = (
        "[HISTORISCHE SUCHE | {}]\n"
        "Thema: {}\n"
        "Zeitraum: unbegrenzt (alle verfuegbaren Quellen)\n"
        "WICHTIG: Historische Quellen sind hier ausdruecklich erwuenscht.\n"
        "         Gib das Datum jeder Quelle an damit der Nutzer den zeitlichen Kontext versteht.\n"
    ).format(today, query)

    lines = [header]
    for i, hit in enumerate(results, 1):
        title = hit.get("title", "") or hit.get("heading", "")
        body  = hit.get("body", "") or hit.get("snippet", "")
        url   = hit.get("href", "") or hit.get("url", "")
        raw_d = hit.get("date", "")
        date  = _fmt_date(raw_d) if raw_d else "Datum unbekannt"
        age   = _age_label_historical(raw_d) if raw_d else ""
        lines.append("[{}] {}\n    Datum: {}{}\n    {}\n    Quelle: {} | {}".format(
            i, title, date, age, body, _domain(url), url
        ))
    return "\n".join(lines)


def historical_multi_search(query: str, max_per_source: int = 8) -> str:
    """
    Tiefer historischer Modus: kombiniert Web + News ohne Zeitfilter.
    Sammelt maximal viele Quellen fuer eine umfassende historische Analyse.
    """
    today = datetime.now().strftime("%d.%m.%Y")
    all_results = []
    seen_urls: set = set()

    try:
        DDGS = _import_ddgs()
    except RuntimeError as exc:
        return "(Recherche nicht verfuegbar: {})".format(exc)

    # Web-Suche ohne Zeitfilter - mehrere Winkel
    search_variants = [
        query,
        query + " Geschichte Hintergruende",
        query + " historisch Ursachen",
    ]
    for q in search_variants:
        if len(all_results) >= max_per_source * 3:
            break
        try:
            with DDGS() as ddgs:
                for hit in ddgs.text(q, region=config.SEARCH_REGION,
                                     safesearch="moderate", max_results=max_per_source):
                    url = hit.get("href", "") or hit.get("url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_results.append(("web", hit))
        except Exception:
            continue

    # News ohne Zeitfilter
    try:
        with DDGS() as ddgs:
            for hit in ddgs.news(query, region=config.SEARCH_REGION,
                                  safesearch="moderate", max_results=max_per_source):
                url = hit.get("url", "") or hit.get("href", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(("news", hit))
    except Exception:
        pass

    if not all_results:
        return "(Keine historischen Quellen gefunden fuer: {!r})".format(query)

    header = (
        "[HISTORISCHE ANALYSE | {}]\n"
        "Thema: {}\n"
        "Quellen gesamt: {} | Zeitraum: UNBEGRENZT\n"
        "ANWEISUNG: Beruecksichtige ALLE Quellen unabhaengig vom Alter.\n"
        "           Zeige zeitliche Entwicklung wenn moeglich.\n"
    ).format(today, query, len(all_results))

    lines = [header]
    for i, (src_type, hit) in enumerate(all_results, 1):
        if src_type == "news":
            title  = hit.get("title", "")
            body   = hit.get("body", "") or hit.get("excerpt", "")
            url    = hit.get("url", "")
            source = hit.get("source", "") or _domain(url)
            raw_d  = hit.get("date", "")
        else:
            title  = hit.get("title", "") or hit.get("heading", "")
            body   = hit.get("body", "") or hit.get("snippet", "")
            url    = hit.get("href", "") or hit.get("url", "")
            source = _domain(url)
            raw_d  = hit.get("date", "")

        date = _fmt_date(raw_d) if raw_d else "Datum unbekannt"
        age  = _age_label_historical(raw_d)
        lines.append("[Q{}][{}] {}\n    Quelle: {} | Datum: {}{}\n    {}\n    URL: {}".format(
            i, src_type.upper(), title, source, date, age, body, url
        ))
    return "\n".join(lines)


# =====================================================
# Query bereinigen
# =====================================================

_STRIP_PHRASES = [
    # Hoeﬂichkeit / Fuellwoerter
    "bitte", "kannst du", "koenntest du", "könntest du",
    "sag mir", "sag mir bitte", "erklaer mir", "erklaer",
    "ich moechte wissen", "ich möchte wissen",
    # Bejahungen / Reaktionen am Anfang (werden abgeschnitten)
    "super", "toll", "sehr gut", "danke", "danke schoen", "danke schön",
    "gut", "prima", "okay", "ok", "alles klar", "verstanden",
    "ja", "nein", "aha", "ach so", "interessant",
    "sicher", "natuerlich", "natürlich", "genau", "stimmt",
    # Fuellphrasen in Fragen
    "und was ist mit", "was ist mit", "was ist eigentlich mit",
    "und wie ist das mit", "und wie war das mit",
    "gewesen", "worden ist", "wurde ausgestahlt", "ausgestahlt",
    "erzaehl mir mehr", "mehr dazu", "noch mehr",
    # Zeitbezüge (für Geo-APIs irrelevant)
    "aktuell", "aktuelles", "aktueller", "aktuelle",
    "neueste", "neuester", "neuestes", "neuste",
    "heute", "jetzt", "gerade", "derzeit", "momentan",
    # Suchanweisungen
    "recherchiere", "recherchier",
    "suche im internet", "suche bitte", "suche nach", "suche",
    "google nach", "google", "im internet", "im netz", "online",
    "finde heraus", "finde bitte", "schau nach", "nachschauen",
    "was gibt es neues ueber", "was gibt es neues zu",
    "aktuelle nachrichten zu", "aktuelle nachrichten ueber",
    "neuigkeiten zu", "neuigkeiten ueber",
    "nachrichten zu", "nachrichten ueber",
    "news zu", "news ueber",
    # Lagebild-Präfixe
    "lagebild", "lagebericht", "lage bild", "lage bericht",
    "erstelle lagebild", "erstelle lage", "zeig lagebild",
    "zeige lagebild", "mach lagebild", "nexus lage",
]


def clean_query(text: str) -> str:
    import re as _re
    # Sonderzeichen die URLs / APIs brechen sofort entfernen
    text = _re.sub(r'[#&=<>{}|\\^`\[\]@!]', '', text).strip()
    cleaned = " " + text.lower() + " "
    for phrase in sorted(_STRIP_PHRASES, key=len, reverse=True):
        cleaned = cleaned.replace(" " + phrase + " ", " ")
    result = " ".join(cleaned.split()).strip(" ?.,!:")
    # Trailing Sonderzeichen nochmals weg
    result = _re.sub(r'[#&=<>{}|\\^`\[\]@!]+$', '', result).strip()
    return result


# =====================================================
# Folgefragen-Erkennung
# =====================================================

# Woerter die typischerweise eine Folgefrage einleiten
_FOLLOWUP_STARTERS = {
    "und", "aber", "warum", "wieso", "weshalb", "also",
    "stimmt", "wirklich", "noch", "immer", "heißt",
}

def is_followup_question(text: str) -> bool:
    """
    Erkennt kurze Folgefragen die ohne Kontext keinen Sinn ergeben.
    Beispiele: 'und heute auch noch?', 'warum?', 'aber wieso?'
    """
    if not text:
        return False
    stripped = text.lower().strip().strip("?!.,;:")
    words = stripped.split()
    # Kurz (max 8 Woerter) UND beginnt mit Konnektor (Satzzeichen ignorieren)
    first = words[0].strip("?!.,;:") if words else ""
    return len(words) <= 8 and first in _FOLLOWUP_STARTERS


def enrich_query_with_context(current_query: str, last_user_msg: str) -> str:
    """
    Reichert eine Folgefrage mit dem Thema der vorherigen Nachricht an.
    Beispiel: 'und heute auch noch?' + 'was macht merkel' -> 'merkel heute'
    """
    last_clean = clean_query(last_user_msg) or last_user_msg
    current_clean = clean_query(current_query) or current_query
    combined = "{} {}".format(last_clean, current_clean).strip()
    return combined if combined else current_query
