"""Tests for nexus_nlp.py (T175 — Multilingual NLP)."""
import pytest


def test_import():
    import nexus_nlp  # noqa: F401


def test_detect_language_arabic():
    from nexus_nlp import detect_language
    text = "القوات الروسية تتقدم باتجاه خاركيف"
    lang = detect_language(text)
    assert lang == "ar"


def test_detect_language_russian():
    from nexus_nlp import detect_language
    text = "Русские войска продвигаются к Харькову"
    lang = detect_language(text)
    assert lang == "ru"


def test_detect_language_english():
    from nexus_nlp import detect_language
    lang = detect_language("The maritime patrol aircraft is conducting surveillance.")
    assert lang == "en"


def test_detect_language_german():
    from nexus_nlp import detect_language
    lang = detect_language("Keine ungewöhnliche Aktivität im Hormuz-Korridor.")
    assert lang == "de"


def test_detect_language_chinese():
    from nexus_nlp import detect_language
    lang = detect_language("中国海军舰艇正在南海进行演习")
    assert lang == "zh"


def test_detect_language_empty():
    from nexus_nlp import detect_language
    # Should not crash on empty string
    result = detect_language("")
    assert isinstance(result, str)


def test_nlp_result_dataclass():
    from nexus_nlp import NlpResult
    r = NlpResult(
        original_text="Test", detected_lang="en", translated_text="Test",
        translation_needed=False, translation_ok=True,
        entities=[], locations=[], sentiment=0.0, keywords=[], escalation_score=0.0
    )
    assert r.detected_lang == "en"


def test_analyze_text_english():
    """analyze_text on English text should not need translation."""
    from nexus_nlp import analyze_text
    result = analyze_text("Warship spotted near Hormuz strait.", translate=False)
    assert result.detected_lang == "en"
    assert result.translation_needed is False
    assert isinstance(result.keywords, list)
    assert isinstance(result.sentiment, float)


def test_analyze_text_returns_locations():
    from nexus_nlp import analyze_text
    result = analyze_text("Troops advancing near Kharkiv and Zaporizhzhia.", translate=False)
    # Should find at least some locations via spaCy or regex
    assert isinstance(result.locations, list)


def test_extract_locations_empty():
    from nexus_nlp import extract_locations
    locs = extract_locations("", lang="en")
    assert isinstance(locs, list)


def test_enrich_telegram_messages():
    from nexus_nlp import enrich_telegram_messages
    msgs = [
        {"text": "Drone strike near Kyiv.", "channel": "test"},
        {"text": "Keine Aktivität.", "channel": "test2"},
    ]
    enriched = enrich_telegram_messages(msgs)
    assert len(enriched) == 2
    for m in enriched:
        assert "language" in m
        assert "sentiment" in m


def test_enrich_telegram_empty():
    from nexus_nlp import enrich_telegram_messages
    assert enrich_telegram_messages([]) == []
