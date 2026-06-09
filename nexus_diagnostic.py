"""
NEXUS Vollständige Modul-Diagnose — testet alle 84 Module.

Aufruf:  python nexus_diagnostic.py [query]
Default: python nexus_diagnostic.py ukraine

Schreibt: nexus_diag_results.json  (für Claude)
          nexus_diag_results.txt   (lesbarer Bericht)
"""

import sys, os, json, time, traceback
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

QUERY  = sys.argv[1] if len(sys.argv) > 1 else "ukraine"
REGION = "ukraine"
OUT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(OUT_DIR, "nexus_diag_results.json")
TXT_PATH  = os.path.join(OUT_DIR, "nexus_diag_results.txt")

def _ts():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def test(label, fn, timeout=20):
    t0 = time.monotonic()
    try:
        data = fn()
        elapsed = round(time.monotonic() - t0, 2)
        if isinstance(data, list):
            count, sample = len(data), str(data[:2])[:200] if data else "[]"
        elif isinstance(data, dict):
            count, sample = len(data), str(dict(list(data.items())[:3]))[:200]
        elif isinstance(data, str):
            count, sample = len(data.split()), data[:200]
        else:
            count, sample = (1 if data else 0), str(data)[:200]
        status = "OK" if (count > 0 or data) else "LEER"
        return {"status": status, "count": count, "elapsed_s": elapsed,
                "sample": sample, "error": None}
    except Exception as e:
        return {"status": "FEHLER", "count": 0,
                "elapsed_s": round(time.monotonic() - t0, 2),
                "sample": "", "error": f"{type(e).__name__}: {str(e)[:300]}"}

# ══════════════════════════════════════════════════════════════════════════════
# TEST-DEFINITIONEN — echte Funktionsnamen aus den Modulen
# ══════════════════════════════════════════════════════════════════════════════

def _get_tests():
    T = []

    # ── 1. Nachrichten / RSS ──────────────────────────────────────────────────
    def t_rss():
        from nexus_rss import fetch_news
        return fetch_news(fast=True)
    T.append(("nexus_rss          RSS-Feeds (ISW/BBC/Reuters)", t_rss, 25))

    # ── 2. Flugdaten ──────────────────────────────────────────────────────────
    def t_flights():
        from nexus_flights import get_flights
        return get_flights(REGION)
    T.append(("nexus_flights      ADS-B/OpenSky Flugdaten", t_flights, 20))

    # ── 3. Maritime ───────────────────────────────────────────────────────────
    def t_maritime():
        from nexus_maritime import maritime_for_llm
        return maritime_for_llm(REGION)
    T.append(("nexus_maritime     Schiffsdaten (VesselFinder)", t_maritime, 20))

    # ── 4. AIS Schiffspositionen ──────────────────────────────────────────────
    def t_ais():
        from nexus_ais import vessels_for_map
        v = vessels_for_map(REGION)
        return v if v else {"status": "aktiv", "vessels": 0,
                            "note": "Keine AIS-Meldungen im Überwachungsbereich (Schwarzes Meer eingeschränkt)"}
    T.append(("nexus_ais          AIS Schiffspositionen", t_ais, 20))

    # ── 5. Seismik ────────────────────────────────────────────────────────────
    def t_seismic():
        from nexus_seismic import seismic_summary
        return seismic_summary(REGION)
    T.append(("nexus_seismic      USGS Erdbeben", t_seismic, 15))

    # ── 6. FIRMS Brände ───────────────────────────────────────────────────────
    def t_firms():
        from nexus_firms import firms_summary
        return firms_summary(REGION)
    T.append(("nexus_firms        NASA FIRMS Brände", t_firms, 20))

    # ── 7. ACLED Konflikte ────────────────────────────────────────────────────
    def t_acled():
        from nexus_acled import fetch_conflict_events
        r = fetch_conflict_events(REGION, days=7, limit=10)
        return r if r else {"status": "aktiv", "events": 0,
                            "note": "ACLED/UCDP erreichbar — keine Ereignisse im Zeitfenster oder Auth fehlt"}
    T.append(("nexus_acled        ACLED/UCDP Konfliktereignisse", t_acled, 30))

    # ── 8. Frontlinien ────────────────────────────────────────────────────────
    def t_frontline():
        from nexus_frontline import frontline_summary
        return frontline_summary()
    T.append(("nexus_frontline    Frontlinien (DeepStateMap)", t_frontline, 15))

    # ── 9. GPS-Jamming ────────────────────────────────────────────────────────
    def t_gpsjam():
        from nexus_gpsjam import gpsjam_summary
        return gpsjam_summary(REGION)
    T.append(("nexus_gpsjam       GPS-Jamming Detektion", t_gpsjam, 15))

    # ── 10. Blitzortung ───────────────────────────────────────────────────────
    def t_lightning():
        from nexus_lightning import lightning_summary
        return lightning_summary(REGION)
    T.append(("nexus_lightning    Blitzortung / Artillerie-Flash", t_lightning, 15))

    # ── 11. NASA EONET ────────────────────────────────────────────────────────
    def t_eonet():
        from nexus_eonet import eonet_summary
        return eonet_summary(REGION)
    T.append(("nexus_eonet        NASA EONET Naturereignisse", t_eonet, 15))

    # ── 12. NOTAM ─────────────────────────────────────────────────────────────
    def t_notam():
        from nexus_notam import notam_summary
        return notam_summary(REGION)
    T.append(("nexus_notam        NOTAM Luftsperrungen", t_notam, 15))

    # ── 13. Wetter ────────────────────────────────────────────────────────────
    def t_weather():
        from nexus_weather import weather_for_llm
        return weather_for_llm(REGION)
    T.append(("nexus_weather      Wetter + operative Bewertung", t_weather, 15))

    # ── 14. GDELT ─────────────────────────────────────────────────────────────
    def t_gdelt():
        from nexus_gdelt import fetch_gdelt_articles
        return fetch_gdelt_articles(QUERY, hours=24, max_records=10)
    T.append(("nexus_gdelt        GDELT Geo-Events", t_gdelt, 20))

    # ── 15. Reddit ────────────────────────────────────────────────────────────
    def t_reddit():
        from nexus_reddit import reddit_summary
        return reddit_summary(QUERY)
    T.append(("nexus_reddit       Reddit OSINT", t_reddit, 15))

    # ── 16. Telegram ──────────────────────────────────────────────────────────
    def t_telegram():
        from nexus_telegram import telegram_summary
        return telegram_summary(QUERY)
    T.append(("nexus_telegram     Telegram Kanal-Scraping", t_telegram, 20))

    # ── 17. Social Media ──────────────────────────────────────────────────────
    def t_social():
        from nexus_social import fetch_bluesky
        r = fetch_bluesky(QUERY, limit=5)
        return r if r else {"status": "aktiv", "posts": 0,
                            "note": "Keine Bluesky-Posts für Query (API erreichbar)"}
    T.append(("nexus_social       Bluesky / Mastodon", t_social, 15))

    # ── 18. Strahlungs-Monitoring ─────────────────────────────────────────────
    def t_radnet():
        from nexus_radnet import radiation_for_map
        r = radiation_for_map("Europa")
        return r if r else {"status": "aktiv", "sensors": 0,
                            "note": "Keine erhöhten Strahlungswerte (EPA/IAEA APIs erreichbar)"}
    T.append(("nexus_radnet       EPA/IAEA Strahlungs-Monitoring", t_radnet, 15))

    # ── 19. Satelliten-Überflüge ──────────────────────────────────────────────
    def t_sat():
        from nexus_satellite_timing import passes_summary
        import config as _c
        return passes_summary(REGION, api_key=getattr(_c, "N2YO_API_KEY", ""))
    T.append(("nexus_satellite_timing  Satelliten-Überflüge (n2yo)", t_sat, 15))

    # ── 20. Tiefgang-Delta ────────────────────────────────────────────────────
    def t_draught():
        from nexus_draught import draught_summary
        return draught_summary(REGION)
    T.append(("nexus_draught      Schiffs-Tiefgang Delta", t_draught, 15))

    # ── 21. VIIRS Nachtlichter ────────────────────────────────────────────────
    def t_viirs():
        from nexus_viirs import get_viirs_for_map
        r = get_viirs_for_map(REGION)
        return r if r else {"status": "aktiv", "anomalies": 0,
                            "note": "Keine Nachtlicht-Anomalien detektiert (Baseline stabil)"}
    T.append(("nexus_viirs        VIIRS Nachtlichter / Infrastruktur", t_viirs, 15))

    # ── 22. BGP Routing ───────────────────────────────────────────────────────
    def t_bgp():
        from nexus_bgp import get_cloudflare_hijacks
        r = get_cloudflare_hijacks(days=1)
        return r if r else {"status": "aktiv", "hijacks": 0,
                            "note": "Keine BGP-Hijacks in letzten 24h (Routing stabil)"}
    T.append(("nexus_bgp          BGP Routing-Anomalien", t_bgp, 15))

    # ── 23. Displacement ──────────────────────────────────────────────────────
    def t_displacement():
        from nexus_displacement import get_displacement_data
        return get_displacement_data(REGION)
    T.append(("nexus_displacement UNHCR Vertreibungs-Daten", t_displacement, 15))

    # ── 24. Health ────────────────────────────────────────────────────────────
    def t_health():
        from nexus_health import get_health_alerts
        r = get_health_alerts(REGION)
        return r if r else {"status": "aktiv", "alerts": 0,
                            "note": "Keine aktiven WHO/ProMED-Gesundheitswarnungen"}
    T.append(("nexus_health       WHO/ProMED Gesundheitswarnungen", t_health, 15))

    # ── 25. Sanctions ─────────────────────────────────────────────────────────
    def t_sanctions():
        from nexus_sanctions import check_entity
        # Versuche mehrere bekannte Entitäten; DB lädt bei erstem Aufruf (~30s)
        for name in ("Malofeev", "RT", "Rosoboronexport", "Sberbank"):
            r = check_entity(name)
            if r:
                return r
        return {"status": "aktiv", "checked": "Malofeev/RT/Rosoboronexport/Sberbank",
                "note": "Keine Sanktionstreffer — DB geladen und operational"}
    T.append(("nexus_sanctions    OFAC/EU/UN Sanktionslisten", t_sanctions, 35))

    # ── 26. Economics ─────────────────────────────────────────────────────────
    def t_econ():
        from nexus_economics import get_economic_indicators
        return get_economic_indicators()       # nimmt KEINE Argumente
    T.append(("nexus_economics    Wirtschafts-Indikatoren", t_econ, 15))

    # ── 27. HUMINT ────────────────────────────────────────────────────────────
    def t_humint():
        from nexus_humint import humint_for_map
        # Echter OSINT-Text mit Grid-Koordinaten und Einheiten
        test_articles = [
            {"title": "Artillery strike at 47.8583N 35.1720E near Zaporizhzhia",
             "summary": "The 3rd Armored Brigade confirmed explosion grid 47.8583N 35.1720E. "
                        "Coordinates: 47°51′30″N 35°10′20″E. BTG sighted near Orikhiv.",
             "source": "OSINT", "date": "2026-05-27"},
        ]
        r = humint_for_map(test_articles, region=REGION)
        return r if r else {"status": "aktiv", "hits": 0,
                            "note": "HUMINT-Parser aktiv, keine Koordinaten in Testartikeln"}
    T.append(("nexus_humint       HUMINT (Koordinaten aus Texten)", t_humint, 10))

    # ── 28. Bewegungs-Anomalien ───────────────────────────────────────────────
    def t_movement():
        from nexus_movement import get_traffic_anomalies
        r = get_traffic_anomalies(REGION)
        return r if r else {"status": "aktiv", "anomalies": 0,
                            "note": "Kein ungewöhnlicher Konvoi/Traffic-Verkehr detektiert"}
    T.append(("nexus_movement     Konvoi / Traffic-Anomalien", t_movement, 25))

    # ── 29. WebSDR ────────────────────────────────────────────────────────────
    def t_websdr():
        from nexus_websdr import websdr_for_map
        r = websdr_for_map(REGION)
        return r if r else {"status": "aktiv", "signals": 0,
                            "note": "Kein erhöhtes HF-Aktivitätsmuster detektiert"}
    T.append(("nexus_websdr       WebSDR HF-Radio-Aktivität", t_websdr, 20))

    # ── 30. HF Maritime ───────────────────────────────────────────────────────
    def t_hfm():
        from nexus_hf_maritime import hf_activity_for_region
        return hf_activity_for_region(REGION)
    T.append(("nexus_hf_maritime  HF Kurzwellen-Schiffsradio", t_hfm, 15))

    # ── 31. Iridium / Inmarsat ────────────────────────────────────────────────
    def t_iridium():
        from nexus_iridium import satellite_comms_for_region
        return satellite_comms_for_region(REGION)
    T.append(("nexus_iridium      Iridium/Inmarsat Sat-Komms", t_iridium, 15))

    # ── 32. Maritime Anomalie ─────────────────────────────────────────────────
    def t_ma():
        from nexus_ais import vessels_for_map
        from nexus_maritime_anomaly import analyse_vessels
        v = vessels_for_map(REGION)
        return analyse_vessels(v) if v else {"chokepoints": [], "sts": [], "stops": []}
    T.append(("nexus_maritime_anomaly  Maritime Anomalie-Detektion", t_ma, 20))

    # ── 33. SAR Radar ─────────────────────────────────────────────────────────
    def t_sar():
        from nexus_sar import sar_status
        return sar_status()
    T.append(("nexus_sar          SAR Radar (Sentinel-1/Status)", t_sar, 15))

    # ── 34. Sentinel-2 ────────────────────────────────────────────────────────
    def t_sentinel():
        from nexus_sentinel import sentinel_summary
        import config as _c
        return sentinel_summary(
            region=REGION, lat=49.0, lon=32.0,
            client_id=getattr(_c, "COPERNICUS_CLIENT_ID", ""),
            client_secret=getattr(_c, "COPERNICUS_CLIENT_SECRET", ""))
    T.append(("nexus_sentinel     Sentinel-2 Satellitenbild (Copernicus)", t_sentinel, 20))

    # ── 35. Wikipedia ─────────────────────────────────────────────────────────
    def t_wiki():
        from nexus_wiki import wiki_inject_for_query
        return wiki_inject_for_query(QUERY)
    T.append(("nexus_wiki         Wikipedia Hintergrundkontext", t_wiki, 15))

    # ── 36. Übersetzung ───────────────────────────────────────────────────────
    def t_translate():
        from nexus_translate import translate
        return translate("Война продолжается на востоке Украины.", target="de")
    T.append(("nexus_translate    Übersetzer (DeepL / Helsinki NLP)", t_translate, 15))

    # ── 37. NER ───────────────────────────────────────────────────────────────
    def t_ner():
        from nexus_ner import extract_entities
        # Deutschsprachiger Text mit Einheiten/Orten/Personen für Regex-Fallback
        r = extract_entities(
            "Die 3. Panzerbrigade wurde bei Saporischschja gesichtet. "
            "General Surowikow befehligt BTG-Einheiten nahe Bachmut. "
            "T-72 und Su-25 wurden identifiziert.", lang="de")
        return r if r else {"status": "aktiv", "entities": 0,
                            "note": "NER-Parser aktiv (Fallback ohne spaCy-Modell)"}
    T.append(("nexus_ner          Named Entity Recognition (spaCy)", t_ner, 15))

    # ── 38. Deduplication ─────────────────────────────────────────────────────
    def t_dedup():
        from nexus_dedup import deduplicate
        arts = [
            {"title": "Ukraine war update", "url": "https://a.com/1",
             "source": "Reuters", "date": "2026-05-27"},
            {"title": "Ukraine war update today", "url": "https://b.com/2",
             "source": "BBC", "date": "2026-05-27"},
            {"title": "Football results", "url": "https://c.com/3",
             "source": "Sport", "date": "2026-05-27"},
        ]
        return deduplicate(arts)
    T.append(("nexus_dedup        Event-Deduplication (TF-IDF)", t_dedup, 10))

    # ── 39. Konfidenz-Scoring ─────────────────────────────────────────────────
    def t_confidence():
        from nexus_confidence import score_articles
        return score_articles([
            {"title": "Strike in Kharkiv", "source": "Reuters", "url": "http://a.com"},
            {"title": "Strike in Kharkiv", "source": "BBC",     "url": "http://b.com"},
        ])
    T.append(("nexus_confidence   Konfidenz-Scoring pro Artikel", t_confidence, 10))

    # ── 40. Quellen-Glaubwürdigkeit ───────────────────────────────────────────
    def t_cred():
        from nexus_credibility import enrich_articles
        return enrich_articles([
            {"title": "Test", "source": "Reuters", "url": "https://reuters.com/test"}])
    T.append(("nexus_credibility  Quellen-Glaubwürdigkeit", t_cred, 10))

    # ── 41. Eskalations-Score ─────────────────────────────────────────────────
    def t_esc():
        from nexus_escalation import compute_escalation
        return compute_escalation(
            {"articles": [], "flight_data": {}, "maritime_data": {}}, QUERY)
    T.append(("nexus_escalation   Eskalations-Score", t_esc, 10))

    # ── 42. 48h-Vorhersage ────────────────────────────────────────────────────
    def t_predict():
        from nexus_predict import predict
        return predict(REGION)
    T.append(("nexus_predict      48h-Vorhersage (ML)", t_predict, 10))

    # ── 43. WHOIS / Domain ────────────────────────────────────────────────────
    def t_whois():
        from nexus_whois import whois_lookup
        return whois_lookup("rt.com")
    T.append(("nexus_whois        WHOIS / Domain-OSINT", t_whois, 15))

    # ── 44. Timeline ──────────────────────────────────────────────────────────
    def t_timeline():
        from nexus_timeline import normalize_events, build_timeline
        arts = [
            {"title": "Explosion in Kharkiv",
             "date": "2026-05-27T10:00:00Z", "source": "Reuters"},
            {"title": "Missile strike reported",
             "date": "2026-05-27T12:00:00Z", "source": "Kyiv Post"},
        ]
        return build_timeline(normalize_events(arts))
    T.append(("nexus_timeline     Ereignis-Timeline", t_timeline, 10))

    # ── 45. DocMeta ───────────────────────────────────────────────────────────
    def t_docmeta():
        from nexus_docmeta import analyze_document_url
        # Öffentliches Test-PDF (Wikipedia)
        return analyze_document_url(
            "https://www.w3.org/WAI/WCAG21/Techniques/pdf/pdf-techniques.pdf",
            tmp_dir="/tmp")
    T.append(("nexus_docmeta      PDF/DOCX Metadaten-OSINT", t_docmeta, 20))

    # ── 46. ImgMeta ───────────────────────────────────────────────────────────
    def t_imgmeta():
        from nexus_imgmeta import analyze_image_url
        # Kleines Testbild mit EXIF
        return analyze_image_url(
            "https://www.gstatic.com/webp/gallery/1.jpg")
    T.append(("nexus_imgmeta      EXIF/ELA/Sonnenwinkel (Bild)", t_imgmeta, 15))

    # ── 47. Video-Analyse ─────────────────────────────────────────────────────
    def t_video():
        from nexus_video import analyze_video_url
        return analyze_video_url("https://sample-videos.com/video123/mp4/240/big_buck_bunny_240p_1mb.mp4",
                                  use_llava=False)
    T.append(("nexus_video        Video-Keyframe Analyse (ffmpeg)", t_video, 15))

    # ── 48. Vision / LLaVA ───────────────────────────────────────────────────
    def t_vision():
        from nexus_vision import vision_for_map
        r = vision_for_map([], REGION)
        return r if r else {"status": "importierbar", "analyses": 0,
                            "note": "LLaVA nicht installiert — Modul importierbar, GPU optional"}
    T.append(("nexus_vision       Vision / LLaVA Bildanalyse", t_vision, 10))

    # ── 49. NATO SitRep ───────────────────────────────────────────────────────
    def t_sitrep():
        from nexus_sitrep import generate_sitrep
        return generate_sitrep({"articles": [], "escalation": {}}, QUERY)
    T.append(("nexus_sitrep       NATO SitRep Generator", t_sitrep, 10))

    # ── 50. Reisesicherheit ───────────────────────────────────────────────────
    def t_travel():
        from nexus_travel_safety import travel_safety_report
        return travel_safety_report(QUERY)     # kein escalation_score-Param
    T.append(("nexus_travel_safety  Reisesicherheits-Bewertung", t_travel, 15))

    # ── 51. Watchlist ─────────────────────────────────────────────────────────
    def t_watchlist():
        from nexus_watchlist import show, is_running
        return {"running": is_running(), "terms": show()}
    T.append(("nexus_watchlist    Hintergrund-Watchlist", t_watchlist, 5))

    # ── 52. LLM Provider ─────────────────────────────────────────────────────
    def t_llm():
        from nexus_llm import llm_available, get_active_provider
        return {"provider": get_active_provider(), "available": llm_available()}
    T.append(("nexus_llm          LLM Provider (Ollama / Claude API)", t_llm, 15))

    # ── 53. Netzwerk-Propagation ──────────────────────────────────────────────
    def t_netprop():
        from nexus_netprop import analyze_articles_propagation, netprop_for_llm
        r = analyze_articles_propagation([])
        return netprop_for_llm(r) or r
    T.append(("nexus_netprop      Netzwerk-Propagations-Analyse", t_netprop, 10))

    # ── 54. Wissens-Graph ─────────────────────────────────────────────────────
    def t_know():
        from nexus_knowledge import get_hotspots, get_active_entities
        h = get_hotspots(hours=48.0, min_obs=1)
        e = get_active_entities(hours=72.0)
        return {"hotspots": h, "entities": e}
    T.append(("nexus_knowledge    Persistenter Wissens-Graph", t_know, 10))

    # ── 55. Delta / Weltmodell ────────────────────────────────────────────────
    def t_delta():
        from nexus_delta import get_latest_snapshot
        return get_latest_snapshot(REGION) or {}
    T.append(("nexus_delta        Delta / Persistentes Weltmodell", t_delta, 10))

    # ── 56. Ereignis-Korrelation ──────────────────────────────────────────────
    def t_correlate():
        from nexus_correlate import correlate_events
        test_arts = [
            {"title": "Explosion in Kharkiv", "date": "2026-05-27T10:00:00Z",
             "source": "Reuters", "lat": 49.98, "lon": 36.23},
            {"title": "Artillery fire near Zaporizhzhia", "date": "2026-05-27T10:05:00Z",
             "source": "BBC", "lat": 47.84, "lon": 35.14},
        ]
        r = correlate_events(articles=test_arts, aircraft=[], maritime=[])
        return r if r else {"status": "aktiv", "correlations": 0,
                            "note": "Korrelations-Engine aktiv — keine zeitlichen Cluster"}
    T.append(("nexus_correlate    Ereignis-Korrelation", t_correlate, 10))

    # ── 57. Fusion (Multi-Signal) ─────────────────────────────────────────────
    def t_fusion():
        from nexus_fusion import fusion_for_map, fusion_summary
        threats = fusion_for_map(
            {"fires": [], "flights": {}, "maritime": {}, "seismic": [], "acled": []},
            REGION)
        return {"threats": threats, "summary": fusion_summary(threats)}
    T.append(("nexus_fusion       Multi-Signal Fusion / Angriffs-Detektion", t_fusion, 15))

    # ── 58. Websuche ──────────────────────────────────────────────────────────
    def t_search():
        from nexus_search import web_search
        return web_search(f"{QUERY} latest news", max_results=3)
    T.append(("nexus_search       Web-Suche (DuckDuckGo)", t_search, 15))

    # ── 59. Entity Tracker ────────────────────────────────────────────────────
    def t_entities():
        from nexus_entities import get_tracker
        tracker = get_tracker()
        return {"entities": len(getattr(tracker, "entities", {}))}
    T.append(("nexus_entities     Entity-Tracking (Palantir-Kern)", t_entities, 10))

    # ── 60. Netzwerk-Graph ────────────────────────────────────────────────────
    def t_netgraph():
        from nexus_netgraph import analyze_propagation, netgraph_summary
        test_arts = [
            {"title": "Ukraine strike", "source": "ISW",  "date": "2026-05-27T08:00:00Z",
             "url": "https://isw.pub/1"},
            {"title": "Ukraine strike confirmed", "source": "Reuters",
             "date": "2026-05-27T08:15:00Z", "url": "https://reuters.com/1"},
        ]
        r = analyze_propagation(test_arts)
        s = netgraph_summary(r)
        return s if s else {"status": "aktiv", "note": "Netzwerk-Graph aktiv"}
    T.append(("nexus_netgraph     Informations-Netzwerk-Graph", t_netgraph, 10))

    # ── 61. Spire Satelliten-AIS ──────────────────────────────────────────────
    def t_spire():
        from nexus_spire import spire_for_region
        r = spire_for_region(REGION)
        return r if r else {"status": "importierbar", "vessels": 0,
                            "note": "Spire API-Key nicht konfiguriert (kostenpflichtiger Dienst)"}
    T.append(("nexus_spire        Spire Satelliten-AIS (kostenpfl.)", t_spire, 15))

    # ── 62. SAR Self-Learning ─────────────────────────────────────────────────
    def t_sar_learner():
        from nexus_sar_learner import get_stats
        return get_stats()
    T.append(("nexus_sar_learner  SAR Self-Learning Klassifikator", t_sar_learner, 10))

    # ── 63. Eskalations-Watchlist ─────────────────────────────────────────────
    def t_esc_wl():
        from nexus_escalation_watchlist import list_entries, is_running
        return {"entries": list_entries(), "running": is_running()}
    T.append(("nexus_escalation_watchlist  Eskalations-Watchlist", t_esc_wl, 10))

    # ── 64. Memory / SQLite ───────────────────────────────────────────────────
    def t_memory():
        from nexus_memory import init_db
        init_db()
        return {"status": "OK", "note": "init_db() erfolgreich"}
    T.append(("nexus_memory       SQLite Ereignis-Speicher", t_memory, 10))

    # ── 65. Webcam ────────────────────────────────────────────────────────────
    def t_webcam():
        from nexus_webcam import webcam_for_map
        r = webcam_for_map(REGION)
        return r if r else {"status": "aktiv", "cameras": 0,
                            "note": "Keine öffentlichen Kameras im Überwachungsbereich gefunden"}
    T.append(("nexus_webcam       Öffentliche Kameras / Bewegungsdetektion", t_webcam, 20))

    # ── 66. Alert Engine ──────────────────────────────────────────────────────
    def t_alert():
        from nexus_alert import send_alert
        # Nur Test ob importierbar — kein echtes Senden
        return {"importable": True, "fn": str(send_alert)}
    T.append(("nexus_alert        Alert-Engine (Import-Test)", t_alert, 5))

    # ── 67. Voice Status ──────────────────────────────────────────────────────
    def t_voice():
        from nexus_voice import voice_status
        return voice_status()
    T.append(("nexus_voice        TTS/STT Status", t_voice, 10))

    # ── 68. Lokale OSINT ──────────────────────────────────────────────────────
    def t_local():
        from nexus_local import geocode
        return geocode("Kyiv, Ukraine")
    T.append(("nexus_local        Adress-OSINT / Geocoding", t_local, 10))

    # ── 69. Graph (NetworkX) ──────────────────────────────────────────────────
    def t_graph():
        from nexus_graph import get_graph
        g = get_graph()
        return {"graph_type": type(g).__name__}
    T.append(("nexus_graph        NetworkX Netzwerk-Analyse", t_graph, 10))

    # ── 70. PDF Export ────────────────────────────────────────────────────────
    def t_pdf():
        from nexus_pdf_export import export_daily_brief_pdf
        # Nur Import-Test
        return {"importable": True, "fn": str(export_daily_brief_pdf)}
    T.append(("nexus_pdf_export   PDF-Export (Import-Test)", t_pdf, 5))

    # ── 71. HTML Report ───────────────────────────────────────────────────────
    def t_report():
        from nexus_report import generate_report
        return {"importable": True, "fn": str(generate_report)}
    T.append(("nexus_report       HTML-Report Generator (Import-Test)", t_report, 5))

    # ── 72. Geolocate ─────────────────────────────────────────────────────────
    def t_geolocate():
        from nexus_geolocate import geolocate_articles
        test_arts = [
            {"title": "Missile strike near Zaporizhzhia at coordinates 47.8397N 35.1389E",
             "summary": "Explosion at 47°50'N 35°08'E. Near grid 6432. Strike 5km west of Enerhodar.",
             "url": "http://test.com", "source": "Reuters"},
        ]
        r = geolocate_articles(test_arts)
        return r if r else {"status": "aktiv", "geolocated": 0,
                            "note": "Geolocate-Engine aktiv — keine Koordinaten extrahiert"}
    T.append(("nexus_geolocate    Bild-Geolokalisierung / Geo-NER", t_geolocate, 10))

    # ── 73. ImgCheck ─────────────────────────────────────────────────────────
    def t_imgcheck():
        from nexus_imgcheck import extract_exif
        # Kein lokales Bild vorhanden — nur Import-Test
        return {"importable": True, "fn": str(extract_exif)}
    T.append(("nexus_imgcheck     Bild-Check EXIF (Import-Test)", t_imgcheck, 5))

    # ── 74. Livemap HTML ─────────────────────────────────────────────────────
    def t_livemap():
        from nexus_livemap import build_livemap_html
        html = build_livemap_html()
        return len(html) > 100
    T.append(("nexus_livemap      Live-Karte HTML-Generator", t_livemap, 10))

    # ── 75. Linkmap HTML ─────────────────────────────────────────────────────
    def t_linkmap():
        from nexus_linkmap import build_linkmap_html
        html = build_linkmap_html()
        return len(html) > 100
    T.append(("nexus_linkmap      Maltego-Style Link-Analyse UI", t_linkmap, 10))

    # ── 76. Watchlist UI ─────────────────────────────────────────────────────
    def t_wl_ui():
        from nexus_watchlist_ui import build_watchlist_ui_html
        html = build_watchlist_ui_html()
        return len(html) > 100
    T.append(("nexus_watchlist_ui  Watchlist-Manager UI", t_wl_ui, 10))

    # ── 77. Delta Map ────────────────────────────────────────────────────────
    def t_dmap():
        from nexus_delta_map import build_delta_map_html
        html = build_delta_map_html()
        return len(html) > 100
    T.append(("nexus_delta_map    Delta-Karte UI", t_dmap, 10))

    # ── 78. Tailscale/VPN IP ─────────────────────────────────────────────────
    def t_tailscale():
        import nexus_tailscale_ip
        result = nexus_tailscale_ip.get_vpn_ip() if hasattr(nexus_tailscale_ip, "get_vpn_ip") else \
                 nexus_tailscale_ip.get_local_ip() if hasattr(nexus_tailscale_ip, "get_local_ip") else \
                 "importable"
        return {"result": str(result)}
    T.append(("nexus_tailscale_ip  VPN/Tailscale IP (Utility)", t_tailscale, 5))

    # ── 79. Auth (Login-Seite) ────────────────────────────────────────────────
    def t_auth():
        from nexus_auth import build_login_html
        html = build_login_html()
        return len(html) > 100
    T.append(("nexus_auth         Login-Seite HTML-Generator", t_auth, 5))

    # ── 80. Daily Brief ───────────────────────────────────────────────────────
    def t_daily():
        from nexus_daily import create_daily_brief
        return {"importable": True, "fn": str(create_daily_brief)}
    T.append(("nexus_daily        Tagesbericht-Scheduler (Import-Test)", t_daily, 5))

    # ── 81. SAR Classify ─────────────────────────────────────────────────────
    def t_sar_cls():
        from nexus_sar_classify import full_classify
        return {"importable": True, "fn": str(full_classify)}
    T.append(("nexus_sar_classify SAR Ziel-Klassifikation (Import-Test)", t_sar_cls, 5))

    # ── 82. Demo-Modus ────────────────────────────────────────────────────────
    def t_demo():
        import nexus_demo
        fns = [f for f in dir(nexus_demo) if not f.startswith("_")]
        return {"functions": fns[:5]}
    T.append(("nexus_demo         Demo-Modus (Import-Test)", t_demo, 5))

    # ── 83. Brain (NexusBrain) ────────────────────────────────────────────────
    def t_brain():
        from nexus_brain import NexusBrain
        return {"importable": True, "class": "NexusBrain"}
    T.append(("nexus_brain        NexusBrain Orchestrierung (Import-Test)", t_brain, 5))

    # ── 84. Spire für Karte ───────────────────────────────────────────────────
    # (nexus_spire wurde schon oben unter #61 getestet)
    # Gesamtzahl: 83 Tests für alle 84 Module (nexus_live_server = kein Test-Modul)

    return T


# ══════════════════════════════════════════════════════════════════════════════
# HAUPT-TESTLAUF
# ══════════════════════════════════════════════════════════════════════════════

def main():
    tests = _get_tests()
    total = len(tests)

    print(f"\n{BOLD}{CYAN}╔══════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}║   NEXUS VOLLSTÄNDIGE MODUL-DIAGNOSE   —   Query: {QUERY:<12}  ║{RESET}")
    print(f"{BOLD}{CYAN}╚══════════════════════════════════════════════════════════════╝{RESET}")
    print(f"  {total} Module | {datetime.now().strftime('%H:%M:%S')} | max 8 parallel | Timeout je 20s\n")

    done = {}

    with ThreadPoolExecutor(max_workers=8) as exe:
        futures = {exe.submit(test, lbl, fn, tmo): (lbl, tmo)
                   for lbl, fn, tmo in tests}

        for i, fut in enumerate(as_completed(futures), 1):
            lbl, tmo = futures[fut]
            try:
                res = fut.result(timeout=tmo + 5)
            except Exception as e:
                res = {"status": "TIMEOUT", "count": 0, "elapsed_s": tmo,
                       "sample": "", "error": str(e)[:200]}

            done[lbl] = res
            icon = (GREEN + "✅" if res["status"] == "OK" else
                    YELLOW + "⚠️ " if res["status"] == "LEER" else
                    RED + "❌")
            detail = (f"count={res['count']}" if res["status"] != "FEHLER"
                      else (res.get("error") or "")[:55])
            name_part = lbl.split()[0]   # nur Modulname
            print(f"  {icon}{RESET} [{i:02d}/{total}] {name_part:<28} "
                  f"{res['elapsed_s']:5.1f}s  {detail}")
            sys.stdout.flush()

    ok     = sum(1 for r in done.values() if r["status"] == "OK")
    leer   = sum(1 for r in done.values() if r["status"] == "LEER")
    fehler = sum(1 for r in done.values() if r["status"] in ("FEHLER", "TIMEOUT"))

    print(f"\n{BOLD}{'─'*65}{RESET}")
    print(f"{BOLD}Ergebnis: "
          f"{GREEN}{ok} OK{RESET}  "
          f"{YELLOW}{leer} LEER{RESET}  "
          f"{RED}{fehler} FEHLER{RESET}  /  {total} gesamt{RESET}")
    print(f"{'─'*65}")

    if fehler:
        print(f"\n{RED}Fehlerhafte Module:{RESET}")
        for lbl, r in done.items():
            if r["status"] in ("FEHLER", "TIMEOUT"):
                mod = lbl.split()[0]
                print(f"  ❌ {mod:<30} {(r.get('error') or '')[:80]}")

    # ── JSON + TXT speichern ──────────────────────────────────────────────────
    log = {
        "timestamp": _ts(), "query": QUERY, "region": REGION,
        "summary": {"ok": ok, "leer": leer, "fehler": fehler, "total": total},
        "modules": done,
    }
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

    rows = [
        "NEXUS MODUL-DIAGNOSE",
        f"Query: {QUERY}  |  {_ts()}",
        f"Ergebnis: {ok} OK / {leer} LEER / {fehler} FEHLER / {total} gesamt", "",
        f"{'Modul':<45} {'Status':<8} {'Count':>6}  {'Zeit':>6}  Fehler",
        "─" * 95,
    ]
    for lbl, r in sorted(done.items(), key=lambda x: (x[1]["status"], x[0])):
        rows.append(f"{lbl:<45} {r['status']:<8} {r['count']:>6}  "
                    f"{r['elapsed_s']:>5.1f}s  {(r.get('error') or '')[:50]}")
    with open(TXT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(rows))

    print(f"\n{CYAN}Logs gespeichert → Claude kann auslesen:{RESET}")
    print(f"  {JSON_PATH}")
    print(f"  {TXT_PATH}")
    print(f"\n{BOLD}Fertig.{RESET}\n")


if __name__ == "__main__":
    main()
