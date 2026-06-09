"""
NEXUS – Telegram MTProto-Modul (Telethon)
==========================================
Echter API-Zugang via MTProto-Protokoll.
Kein Scraping-Limit, voller Nachrichtentext, Media-Flags.

Erster Start: python nexus_telethon.py --setup
  → Telefonnummer + SMS-Code eingeben → Session-Datei wird gespeichert
  → Danach laeuft alles automatisch ohne weiteren Login

Installation:
  pip install telethon --break-system-packages

Session-Datei: nexus_telegram_session.session (im Projekt-Verzeichnis)
  WICHTIG: Session-Datei NIEMALS committen (in .gitignore eintragen!)
"""

from __future__ import annotations

import asyncio
import sys
import time
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

# ── Windows Python 3.8+ Event-Loop Fix ───────────────────────────────────────
# Python 3.8+ nutzt auf Windows standardmaessig ProactorEventLoop (IOCP).
# Telethon braucht SelectorEventLoop fuer TCP-Sockets. Muss VOR dem Import gesetzt werden.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ── Telethon Import ──────────────────────────────────────────────────────────
try:
    from telethon.sync import TelegramClient
    from telethon import errors as tg_errors
    from telethon.tl.types import (
        MessageMediaPhoto, MessageMediaDocument,
        MessageMediaWebPage, PeerChannel, Channel
    )
    _TELETHON_OK = True
    # Obfuskierter TCP-Modus: tarnt MTProto-Verkehr als normalen TLS-Datenstrom.
    # Hilft, wenn Router/Provider-Filter (Deep Packet Inspection) das "rohe"
    # MTProto-Protokoll abbrechen, obwohl normales HTTPS (Browser-Login) klappt.
    try:
        from telethon.network.connection.tcpobfuscated import ConnectionTcpObfuscated as _ConnObf
    except ImportError:
        _ConnObf = None
except ImportError:
    _TELETHON_OK = False
    _ConnObf = None

# ── Config-Import ──────────────────────────────────────────────────────────
try:
    import config
    _API_ID   = config.TELEGRAM_API_ID
    _API_HASH = config.TELEGRAM_API_HASH
    _SESSION  = getattr(config, "TELEGRAM_SESSION", "nexus_telegram_session")
    _CHANNELS = getattr(config, "TELEGRAM_CHANNELS", [])
except Exception:
    _API_ID   = 0
    _API_HASH = ""
    _SESSION  = "nexus_telegram_session"
    _CHANNELS = []

# ── Keyword-Scoring (geteilt mit nexus_telegram.py) ─────────────────────────
KEYWORD_SCORES: list[tuple[list[str], float, str]] = [
    (["explosion", "blast", "detonation", "strike", "hit", "انفجار", "взрыв"],     3.0, "detonation"),
    (["missile", "rocket", "ballistic", "cruise", "موشک", "ракета"],               2.5, "missile"),
    (["drone", "uav", "shahed", "fpv", "pjak", "بپلا", "дрон"],                    2.5, "drone"),
    (["artillery", "howitzer", "shelling", "bombardment", "توپخانه"],              2.0, "artillery"),
    (["airstrike", "air strike", "bombing", "f-35", "b-52", "حمله هوایی"],         2.5, "airstrike"),
    (["ceasefire", "truce", "deal", "agreement", "آتش‌بس", "перемирие"],           1.5, "diplomacy"),
    (["nuclear", "uranium", "enrichment", "natanz", "fordow", "هسته‌ای"],          4.0, "nuclear"),
    (["irgc", "sepah", "revolutionary guard", "quds force", "سپاه"],              2.0, "irgc"),
    (["dead", "killed", "casualties", "wounded", "کشته", "убит"],                 1.5, "casualties"),
    (["breaking", "urgent", "فوری", "срочно", "confirmed", "تایید شد"],            0.5, "urgency"),
    (["us forces", "american", "pentagon", "centcom", "نیروهای آمریکا"],           2.0, "us_forces"),
    (["israel", "idf", "mossad", "اسرائیل", "iaf"],                               2.0, "israel"),
    (["sanction", "oil", "tanker", "hormuz", "خلیج فارس", "تنگه هرمز"],           1.5, "economic"),
    (["earthquake", "seismic", "زلزله"],                                           1.5, "seismic"),
    (["blackout", "power outage", "electricity", "خاموشی"],                        1.5, "infrastructure"),
]

# Kanal-Metadaten (Iran-fokussiert + globale OSINT)
# Stand Juni 2026 — nur verifizierte, aktive Telegram-Handles.
# Ungültige/umbenannte Handles entfernt um nutzlose API-Fehler zu vermeiden.
# Neue Handles via TGStat.com oder t.me/s/<name> prüfen bevor Eintrag.
CHANNEL_META: dict[str, dict] = {
    # ── Iran / Naher Osten ────────────────────────────────────────────────────
    "iranintltv":        {"name": "Iran International",       "region": "iran",    "trust": 0.88, "lang": "fa/en"},
    "PersianLeaks":      {"name": "Persian Leaks",            "region": "iran",    "trust": 0.75, "lang": "fa"},
    "Irna_en":           {"name": "IRNA (Staatsagentur, EN)", "region": "iran",    "trust": 0.50, "lang": "en"},
    "MEE_Arabic":        {"name": "MEE Arabic",               "region": "mideast", "trust": 0.75, "lang": "ar"},
    # ── Global OSINT ─────────────────────────────────────────────────────────
    "warmonitor":        {"name": "War Monitor",              "region": "global",  "trust": 0.80, "lang": "en"},
    "OSINTdefender":     {"name": "OSINT Defender",           "region": "global",  "trust": 0.85, "lang": "en"},
    "intelslava":        {"name": "Intel Slava Z",            "region": "global",  "trust": 0.55, "lang": "en"},
    # ── Verifizierte Ukraine/Russland-Kanäle ─────────────────────────────────
    "rybar":             {"name": "Rybar (RU Mil Blog)",      "region": "ukraine", "trust": 0.50, "lang": "ru"},
    "wartranslated":     {"name": "War Translated",           "region": "ukraine", "trust": 0.82, "lang": "en"},
    "militarysummary":   {"name": "Military Summary",         "region": "global",  "trust": 0.70, "lang": "en"},
    # ENTFERNT (Stand Jun 2026 — Telegram-Handle ungültig/umbenannt):
    # "rybar_english"  → war nie gültiger Kanal-Handle (Rybar postet auf @rybar auf Russisch)
    # "middleeasteye"  → MEE hat keinen eigenen Telegram-Kanal mit diesem Handle
    # "Conflict_News"  → Handle existiert nicht (mögl. @conflictnews oder @conflict_news prüfen)
    # "GeoConfirmed"   → Handle existiert nicht (mögl. @GeoConfirmedUA für Ukraine-spezifisch)
}


def _score_text(text: str) -> tuple[float, list[str], list[str]]:
    """Bewertet Nachrichtentext nach OSINT-Schluesselwoertern."""
    t = text.lower()
    total = 0.0
    cats: list[str] = []
    kws: list[str] = []
    for kw_list, weight, category in KEYWORD_SCORES:
        for kw in kw_list:
            if kw in t:
                total += weight
                if category not in cats:
                    cats.append(category)
                kws.append(kw)
                break
    return round(total, 2), cats, kws[:6]


def _media_type(msg) -> str:
    """Erkennt Medientyp einer Telethon-Message."""
    if not _TELETHON_OK:
        return ""
    media = getattr(msg, "media", None)
    if media is None:
        return ""
    if isinstance(media, MessageMediaPhoto):
        return "photo"
    if isinstance(media, MessageMediaDocument):
        doc = getattr(media, "document", None)
        if doc:
            for attr in getattr(doc, "attributes", []):
                cls = type(attr).__name__
                if "Video" in cls:
                    return "video"
                if "Audio" in cls:
                    return "audio"
        return "document"
    return "media"


def fetch_channel_messages(
    channel: str,
    limit: int = 30,
    hours_back: int = 24,
) -> list[dict]:
    """
    Holt Nachrichten eines Telegram-Kanals via Telethon MTProto.
    Gibt nexus_telegram.py-kompatible Dicts zurueck.
    """
    if not _TELETHON_OK:
        return []
    if not _API_ID or not _API_HASH:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    ch_meta = CHANNEL_META.get(channel, {"name": f"@{channel}", "trust": 0.7})

    try:
        client = TelegramClient(
            _SESSION, _API_ID, _API_HASH,
            connection_retries=3, retry_delay=2, timeout=20,
            request_retries=3, flood_sleep_threshold=60,
            connection=_ConnObf if _ConnObf else None,
        )
        with client:
            messages = client.get_messages(channel, limit=limit)

        results = []
        for msg in messages:
            # Datum pruefen
            msg_date = getattr(msg, "date", None)
            if msg_date is None:
                continue
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=timezone.utc)
            if msg_date < cutoff:
                continue

            text = getattr(msg, "text", "") or getattr(msg, "message", "") or ""
            if not text or len(text) < 10:
                continue

            age_min = int((datetime.now(timezone.utc) - msg_date).total_seconds() / 60)
            lines   = [l.strip() for l in text.split("\n") if l.strip()]
            title   = lines[0][:160] if lines else text[:160]
            summary = " ".join(lines[1:3])[:280] if len(lines) > 1 else ""

            # Emojis aus Titel entfernen
            title = re.sub(r"[\U00010000-\U0010ffff]", "", title).strip()
            if len(title) < 8:
                title = re.sub(r"[\U00010000-\U0010ffff]", "", text[:120]).strip()

            score, cats, kws = _score_text(text)
            trust = ch_meta.get("trust", 0.7)
            score_w = round(score * trust, 2)

            media_t = _media_type(msg)

            # Weitergeleitete Nachrichten: Quelle vermerken
            fwd = getattr(msg, "fwd_from", None)
            fwd_from = ""
            if fwd:
                from_name = getattr(getattr(fwd, "from_name", None), "__class__", None)
                fwd_from = getattr(fwd, "from_name", "") or ""

            results.append({
                "title":            title,
                "summary":          summary,
                "url":              f"https://t.me/{channel}/{msg.id}",
                "source":           f"Telegram/{ch_meta['name']}",
                "date":             msg_date.strftime("%Y-%m-%d"),
                "age_min":          age_min,
                "has_media":        bool(media_t),
                "media_type":       media_t,
                "tg_channel":       channel,
                "score":            score_w,
                "categories":       cats,
                "matched_keywords": kws,
                "trust":            trust,
                "fwd_from":         fwd_from,
                "msg_id":           msg.id,
                "full_text":        text[:800],   # vollstaendiger Text (fuer LLM)
            })

        return results

    except tg_errors.FloodWaitError as e:
        print(f"[TELETHON] FloodWait {channel}: {e.seconds}s warten", file=sys.stderr)
        return []
    except tg_errors.ChannelPrivateError:
        print(f"[TELETHON] {channel}: privater Kanal – nicht beigetreten", file=sys.stderr)
        return []
    except (tg_errors.UsernameNotOccupiedError, ValueError) as exc:
        # Telethon wirft je nach Codepfad entweder UsernameNotOccupiedError
        # (RPC-Ebene) oder einen ValueError ("No user has ... as username",
        # intern aus _get_entity_from_string). Beide bedeuten: Kanal-Handle
        # existiert nicht (mehr) / wurde umbenannt -- einfach ueberspringen.
        print(f"[TELETHON] {channel}: Kanal-Handle existiert nicht (mehr) – uebersprungen ({exc})", file=sys.stderr)
        return []
    except Exception as exc:
        print(f"[TELETHON] {channel}: {exc}", file=sys.stderr)
        return []


def fetch_iran_signals(hours_back: int = 6, min_score: float = 1.0) -> list[dict]:
    """
    Holt und bewertet Nachrichten aus Iran-relevanten Kanaelen.
    Gibt nach Score sortierte Liste zurueck.
    """
    iran_channels = [
        ch for ch, meta in CHANNEL_META.items()
        if meta.get("region") in ("iran", "mideast", "global")
    ]
    # Config-Kanaele ergaenzen
    for ch in _CHANNELS:
        if ch not in iran_channels:
            iran_channels.append(ch)

    all_posts: list[dict] = []
    seen: set[str] = set()

    for ch in iran_channels:
        posts = fetch_channel_messages(ch, limit=25, hours_back=hours_back)
        for p in posts:
            url = p.get("url", "")
            if url not in seen:
                seen.add(url)
                if p["score"] >= min_score:
                    all_posts.append(p)
        time.sleep(0.2)   # Hoeflchkeitswartezeit zwischen Kanaelen

    all_posts.sort(key=lambda x: x["score"], reverse=True)
    return all_posts[:60]


def telethon_summary(region: str = "Iran", hours_back: int = 6) -> str:
    """
    Text-Zusammenfassung fuer LLM-Kontext (kompatibel mit nexus_telegram.telegram_summary).
    """
    if not _TELETHON_OK:
        return (
            "[TELEGRAM MTProto] Telethon nicht installiert.\n"
            "  Installieren: pip install telethon --break-system-packages"
        )
    if not _API_ID:
        return "[TELEGRAM MTProto] API-ID nicht konfiguriert (config.TELEGRAM_API_ID fehlt)"

    posts = fetch_iran_signals(hours_back=hours_back, min_score=0.5)
    if not posts:
        return f"[TELEGRAM MTProto] Keine relevanten Nachrichten in den letzten {hours_back}h."

    lines = [
        f"[TELEGRAM MTProto – {region} | letzte {hours_back}h]",
        f"Nachrichten analysiert: {len(posts)} | Top-Score: {posts[0]['score']:.1f}",
        "",
    ]
    for p in posts[:12]:
        age_s = f"{p['age_min']}min" if p['age_min'] < 120 else f"{p['age_min']//60}h"
        cats  = ", ".join(p.get("categories", [])[:3])
        media = f" [{p['media_type'].upper()}]" if p.get("media_type") else ""
        lines.append(
            f"  [{p['score']:.1f}★ | {p['source']} | vor {age_s}{media}]"
        )
        lines.append(f"  {p['title'][:120]}")
        if cats:
            lines.append(f"  → {cats}")
        lines.append("")

    return "\n".join(lines)


def telethon_for_escalation(region: str = "Iran",
                            _posts: "list | None" = None) -> dict:
    """
    Kompaktes Eskalations-Signal fuer nexus_escalation.py.
    Format identisch mit nexus_telegram.telegram_for_escalation().

    _posts: optional vorgefetchte Post-Liste — verhindert doppelten Kanal-Abruf
            wenn sammle_telegram_mtproto() bereits fetch_iran_signals() aufgerufen hat.
    """
    posts = _posts if _posts is not None else fetch_iran_signals(hours_back=3, min_score=0.5)

    top_score = posts[0]["score"] if posts else 0.0
    cat_counts: dict[str, int] = {}
    for p in posts:
        for cat in p.get("categories", []):
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

    hint = ""
    if posts:
        best = posts[0]
        cats = ", ".join(best.get("categories", [])[:3])
        hint = f"{best.get('source','')}: {best.get('title','')[:80]} [{cats}]"

    # Surge: mehr als 10 hochscore-Posts in 3h = Surge-Signal
    surge_active = len([p for p in posts if p["score"] >= 3.0]) >= 5

    return {
        "surge_active":    surge_active,
        "surge_factor":    round(top_score / 3.0, 2) if top_score > 0 else 0.0,
        "top_score":       top_score,
        "message_count":   len(posts),
        "categories":      cat_counts,
        "hint":            hint,
        "channels_active": len(set(p["tg_channel"] for p in posts)),
        "source":          "MTProto",
    }


# ── Longtest-kompatibler Wrapper ─────────────────────────────────────────────
def sammle_telegram_mtproto(region: str = "Iran", stunden: int = 6) -> dict:
    """
    Wrapper fuer nexus_longtest.py.
    Gibt strukturiertes Ergebnis-Dict zurueck.
    """
    if not _TELETHON_OK:
        return {
            "status":  "fehler",
            "fehler":  "Telethon nicht installiert",
            "install": "pip install telethon --break-system-packages",
            "count":   0,
        }
    if not _API_ID:
        return {
            "status": "fehler",
            "fehler": "TELEGRAM_API_ID nicht konfiguriert",
            "count":  0,
        }

    posts = fetch_iran_signals(hours_back=stunden, min_score=0.5)

    top3 = []
    for p in posts[:3]:
        top3.append({
            "quelle":     p.get("source", ""),
            "titel":      p.get("title", "")[:120],
            "score":      p["score"],
            "kategorien": p.get("categories", []),
            "alter_min":  p.get("age_min", 0),
        })

    # _posts übergeben → kein zweiter fetch_iran_signals()-Aufruf (verhindert doppelten Kanal-Abruf)
    eskalation = telethon_for_escalation(region, _posts=posts)

    return {
        "status":       "ok",
        "count":        len(posts),
        "top_score":    posts[0]["score"] if posts else 0.0,
        "kategorien":   eskalation["categories"],
        "surge_aktiv":  eskalation["surge_active"],
        "top3":         top3,
        "eskalation":   eskalation,
    }


# ── Setup / Erster Start ─────────────────────────────────────────────────────
def setup_session() -> None:
    """
    Interaktiver Erststart: Telefonnummer + SMS-Code.
    Danach ist die Session gespeichert und kein Login mehr noetig.
    """
    if not _TELETHON_OK:
        print("❌ Telethon nicht installiert.")
        print("   pip install telethon --break-system-packages")
        return

    if not _API_ID or not _API_HASH:
        print("❌ API-ID oder API-Hash fehlt in config.py")
        print("   TELEGRAM_API_ID  = <deine Nummer>")
        print("   TELEGRAM_API_HASH = '<dein hash>'")
        return

    # Windows: SelectorEventLoop sicherstellen (nochmal explizit fuer diesen Thread)
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)

    print("=" * 60)
    print("NEXUS Telegram MTProto – Erster Start")
    print("=" * 60)
    print(f"Session-Datei: {_SESSION}.session")
    print()
    print("Du wirst nach deiner Telefonnummer gefragt.")
    print("Format: +49XXXXXXXXXX (mit Laendervorwahl, z.B. +4917612345678)")
    print("Telegram sendet dann einen Code per SMS oder in der App.")
    print()

    # WICHTIG: Keine feste DC-Vorgabe mehr! Eine zuvor hartkodierte DC2-Bindung
    # (Amsterdam) konnte dazu fuehren, dass SendCode-Anfragen am falschen
    # Datacenter landen und Telegram den Code intern "verschluckt", ohne
    # eine Fehlermeldung zurueckzugeben (= Code kommt nie an, kein Fehler).
    # Telethon verhandelt das richtige Heimat-DC fuer den Account automatisch
    # ueber die eingebaute Migration (PHONE_MIGRATE_X). Ausserdem: alte
    # Session-Datei vor dem allerersten Login loeschen, falls sie eine
    # falsche DC-Bindung enthaelt.
    try:
        import os as _os
        _sess_file = f"{_SESSION}.session"
        if _os.path.exists(_sess_file):
            try:
                _os.remove(_sess_file)
                print(f"🗑️  Alte Session-Datei geloescht ({_sess_file}) — Neuverhandlung des Datacenters erzwungen")
            except OSError as _e:
                print(f"⚠️  Konnte alte Session-Datei nicht loeschen: {_e}")

        client = TelegramClient(
            _SESSION,
            _API_ID,
            _API_HASH,
            connection_retries=5,
            retry_delay=3,
            timeout=30,
            request_retries=5,
            flood_sleep_threshold=60,
            device_model="NEXUS OSINT",
            system_version="Windows 10",
            app_version="2.0",
            connection=_ConnObf if _ConnObf else None,
        )
        if _ConnObf:
            print("🔒 Obfuskierter Verbindungsmodus aktiv (umgeht DPI-Filter, die rohes MTProto blockieren)")
        else:
            print("⚠️  ConnectionTcpObfuscated nicht verfügbar — Standard-Verbindung wird genutzt")
        with client:
            # start() macht interaktiven Login falls kein Session-File
            client.start()
            me = client.get_me()
            print(f"\n✅ Eingeloggt als: {me.first_name} (@{me.username})")
            print(f"   Session gespeichert: {_SESSION}.session")

            print()
            print("Teste Kanaele...")
            for ch in list(CHANNEL_META.keys())[:3]:
                try:
                    msgs = client.get_messages(ch, limit=1)
                    print(f"  ✅ @{ch}: OK ({len(msgs)} Nachricht)")
                except Exception as e:
                    print(f"  ⚠️  @{ch}: {e}")
    except Exception as exc:
        print(f"\n❌ Verbindungsfehler: {exc}")
        print()
        print("Moegliche Ursachen und Loesungen:")
        print("  1. Windows Firewall: Python in Firewall-Ausnahmen eintragen")
        print("     → Systemsteuerung → Firewall → App zulassen → python.exe")
        print("  2. Antivirus blockiert: Python.exe temporaer ausschliessen")
        print("  3. Netz blockiert Port 443: VPN versuchen oder Mobil-Hotspot")
        print("  4. Erneut versuchen: python nexus_telethon.py --setup")


# ── Direktaufruf ─────────────────────────────────────────────────────────────
if __name__ == "__main__":

    if not _TELETHON_OK:
        print("Telethon fehlt: pip install telethon --break-system-packages")
        sys.exit(1)

    if "--setup" in sys.argv:
        setup_session()
    elif "--test" in sys.argv:
        region = sys.argv[2] if len(sys.argv) > 2 else "Iran"
        print(telethon_summary(region, hours_back=12))
    else:
        print("Nutzung: python nexus_telethon.py [--setup | --test [Region]]")
