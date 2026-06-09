"""
NEXUS – Eskalations-Watchlist & Alert-Engine  (Ebene 4 / Module 4.3)
======================================================================
Überwacht Eskalations-Scores pro Region und alarmiert wenn ein
konfigurierter Schwellenwert überschritten wird.

Kanäle (alle optional, Fallback auf Terminal):
  • Desktop-Notification  (via nexus_alert.py – Windows Toast / Terminal)
  • Email                 (via config.py SMTP – gleiche Einstellung wie Daily-Report)
  • Discord Webhook       (Embed mit Score-Farbe, Signal-Liste)
  • Telegram Bot          (Markdown-Nachricht)

SQLite-Tabelle  nexus_escalation_watchlist.db  (im gleichen Verzeichnis)
  Spalten: id, region, threshold, direction, webhook_url, webhook_type,
           telegram_chat_id, telegram_bot_token, email, cooldown_min,
           enabled, last_alert_ts, last_score, created_ts

REST-API  (wird von nexus_live_server.py eingebunden):
  GET  /api/esc_watchlist          → alle Einträge
  POST /api/esc_watchlist          → neuen Eintrag anlegen
  DELETE /api/esc_watchlist?id=N   → Eintrag löschen
  PUT  /api/esc_watchlist?id=N     → Eintrag aktivieren/deaktivieren

CLI:
  python nexus_escalation_watchlist.py --list
  python nexus_escalation_watchlist.py --add ukraine 50
  python nexus_escalation_watchlist.py --remove 1
  python nexus_escalation_watchlist.py --test-alert 1
  python nexus_escalation_watchlist.py --run          (Daemon-Modus)
"""

from __future__ import annotations

import json
import logging
import smtplib
import sqlite3
import threading
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.parse import urljoin
from urllib.error import URLError
import urllib.request

import config

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────────────────────

DB_PATH         = Path(__file__).parent / "nexus_escalation_watchlist.db"
POLL_INTERVAL   = 60          # Sekunden zwischen Score-Abfragen
DEFAULT_COOLDOWN = 30         # Minuten Pause zwischen zwei Alerts pro Region
LIVE_API_BASE   = f"http://localhost:{getattr(config, 'NEXUS_PORT', 11430)}"

# Score-Richtungen
DIR_ABOVE = "above"   # Alarm wenn Score > threshold
DIR_BELOW = "below"   # Alarm wenn Score < threshold (z.B. Entwarnung)

# Webhook-Typen
WH_DISCORD  = "discord"
WH_TELEGRAM = "telegram"
WH_GENERIC  = "generic"      # Generischer POST mit JSON-Body

# Discord Score → Farbe (dezimal)
_DISCORD_COLORS = {
    "KRITISCH": 0xFF0044,
    "ROT":      0xFF2200,
    "ORANGE":   0xFF8800,
    "GELB":     0xFFCC00,
    "GRUEN":    0x00FF88,
}

# ─────────────────────────────────────────────────────────────────────────────
# Datenbank
# ─────────────────────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Legt die Watchlist-Tabelle an falls nicht vorhanden."""
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS esc_watchlist (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                region           TEXT    NOT NULL,
                threshold        INTEGER NOT NULL DEFAULT 50,
                direction        TEXT    NOT NULL DEFAULT 'above',
                webhook_url      TEXT,
                webhook_type     TEXT    DEFAULT 'discord',
                telegram_chat_id TEXT,
                telegram_bot_token TEXT,
                email            TEXT,
                cooldown_min     INTEGER DEFAULT 30,
                enabled          INTEGER DEFAULT 1,
                last_alert_ts    TEXT,
                last_score       REAL    DEFAULT 0,
                created_ts       TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────────────────────

def add_entry(
    region:            str,
    threshold:         int  = 50,
    direction:         str  = DIR_ABOVE,
    webhook_url:       str  = "",
    webhook_type:      str  = WH_DISCORD,
    telegram_chat_id:  str  = "",
    telegram_bot_token:str  = "",
    email:             str  = "",
    cooldown_min:      int  = DEFAULT_COOLDOWN,
) -> int:
    """Legt neuen Watchlist-Eintrag an. Gibt die neue ID zurück."""
    init_db()
    with _db() as conn:
        cur = conn.execute(
            """INSERT INTO esc_watchlist
               (region, threshold, direction, webhook_url, webhook_type,
                telegram_chat_id, telegram_bot_token, email, cooldown_min)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (region.strip(), int(threshold), direction,
             webhook_url or "", webhook_type or WH_DISCORD,
             telegram_chat_id or "", telegram_bot_token or "",
             email or "", int(cooldown_min)),
        )
        conn.commit()
        return cur.lastrowid


def remove_entry(entry_id: int) -> bool:
    init_db()
    with _db() as conn:
        cur = conn.execute("DELETE FROM esc_watchlist WHERE id=?", (entry_id,))
        conn.commit()
        return cur.rowcount > 0


def set_enabled(entry_id: int, enabled: bool) -> bool:
    init_db()
    with _db() as conn:
        cur = conn.execute(
            "UPDATE esc_watchlist SET enabled=? WHERE id=?",
            (1 if enabled else 0, entry_id),
        )
        conn.commit()
        return cur.rowcount > 0


def update_entry(entry_id: int, **kwargs) -> bool:
    """Aktualisiert beliebige Felder eines Eintrags."""
    allowed = {"region", "threshold", "direction", "webhook_url", "webhook_type",
               "telegram_chat_id", "telegram_bot_token", "email", "cooldown_min", "enabled"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False
    init_db()
    sql = "UPDATE esc_watchlist SET " + ", ".join(f"{k}=?" for k in fields) + " WHERE id=?"
    vals = list(fields.values()) + [entry_id]
    with _db() as conn:
        cur = conn.execute(sql, vals)
        conn.commit()
        return cur.rowcount > 0


def list_entries() -> list[dict]:
    init_db()
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM esc_watchlist ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def get_entry(entry_id: int) -> Optional[dict]:
    init_db()
    with _db() as conn:
        row = conn.execute("SELECT * FROM esc_watchlist WHERE id=?", (entry_id,)).fetchone()
    return dict(row) if row else None


def _mark_alerted(entry_id: int, score: float) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _db() as conn:
        conn.execute(
            "UPDATE esc_watchlist SET last_alert_ts=?, last_score=? WHERE id=?",
            (ts, score, entry_id),
        )
        conn.commit()


def _update_last_score(entry_id: int, score: float) -> None:
    with _db() as conn:
        conn.execute(
            "UPDATE esc_watchlist SET last_score=? WHERE id=?",
            (score, entry_id),
        )
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Score vom Live-Server abrufen
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_score(region: str, timeout: int = 15) -> Optional[dict]:
    """
    Fragt den lokalen NEXUS Live-Server nach dem aktuellen Eskalations-Score.
    Gibt dict mit score, level, color, signal_details zurück oder None.
    """
    url = f"{LIVE_API_BASE}/api/data?query={urllib.parse.quote(region)}"
    try:
        req = Request(url, headers={"User-Agent": "NEXUS-WatchlistEngine/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        esc = data.get("escalation") or {}
        if not esc:
            return None
        return {
            "score":          esc.get("score", 0),
            "level":          esc.get("level", "GRUEN"),
            "color":          esc.get("color", "#00ff88"),
            "icon":           esc.get("icon",  "🟢"),
            "signal_count":   esc.get("signal_count", 0),
            "signal_details": esc.get("signal_details", []),
            "coinc_note":     esc.get("coinc_note", ""),
            "llm_explanation":esc.get("llm_explanation", ""),
            "timestamp":      esc.get("timestamp", ""),
        }
    except Exception as exc:
        logger.debug("[EscWatch] Score-Abfrage für '%s' fehlgeschlagen: %s", region, exc)
        return None


def _should_alert(entry: dict, score: float) -> bool:
    """Prüft ob ein Alert ausgelöst werden soll."""
    if not entry.get("enabled"):
        return False

    threshold = entry.get("threshold", 50)
    direction = entry.get("direction", DIR_ABOVE)

    triggered = (
        (direction == DIR_ABOVE and score >= threshold) or
        (direction == DIR_BELOW and score <  threshold)
    )
    if not triggered:
        return False

    # Cooldown prüfen
    cooldown_min = entry.get("cooldown_min", DEFAULT_COOLDOWN)
    last_ts_str  = entry.get("last_alert_ts") or ""
    if last_ts_str:
        try:
            last_ts = datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))
            elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds() / 60
            if elapsed < cooldown_min:
                logger.debug("[EscWatch] Cooldown aktiv für '%s': noch %.0fmin",
                             entry.get("region"), cooldown_min - elapsed)
                return False
        except Exception:
            pass

    return True


# ─────────────────────────────────────────────────────────────────────────────
# Alert-Ausgang: Discord Webhook
# ─────────────────────────────────────────────────────────────────────────────

def send_discord_alert(
    webhook_url:    str,
    region:         str,
    score:          float,
    level:          str,
    color_hex:      str,
    signal_details: list[dict],
    llm_text:       str = "",
    coinc_note:     str = "",
    threshold:      int = 0,
) -> bool:
    """
    Sendet eine Discord Embed-Nachricht an den Webhook.
    Gibt True bei Erfolg zurück.
    """
    if not webhook_url:
        return False

    color_int = _DISCORD_COLORS.get(level, 0x3b82f6)

    # Signal-Felder für den Embed
    fields = []
    for sig in signal_details[:6]:
        fields.append({
            "name":   sig.get("label", sig.get("signal", "?")),
            "value":  f"+{sig.get('points', 0):.0f} Pkt · {sig.get('conf', '?').upper()}",
            "inline": True,
        })
    if coinc_note:
        fields.append({"name": "⚡ Koinzidenz", "value": coinc_note, "inline": False})

    desc = f"**Score: {score}/100** – Stufe {level}"
    if threshold:
        dir_str = "≥" if score >= threshold else "<"
        desc += f"\nSchwellenwert {dir_str} {threshold} ausgelöst"
    if llm_text:
        desc += f"\n\n*{llm_text[:300]}*"

    payload = {
        "embeds": [{
            "title":       f"🛰 NEXUS ALERT – {region}",
            "description": desc,
            "color":       color_int,
            "fields":      fields,
            "footer":      {"text": f"NEXUS OSINT · {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M UTC')}"},
            "thumbnail":   {"url": ""},
        }]
    }

    try:
        body = json.dumps(payload).encode("utf-8")
        req  = Request(
            webhook_url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "NEXUS-OSINT/4.0"},
            method="POST",
        )
        with urlopen(req, timeout=10) as resp:
            return resp.status in (200, 204)
    except Exception as exc:
        logger.warning("[EscWatch] Discord-Webhook Fehler: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Alert-Ausgang: Telegram Bot
# ─────────────────────────────────────────────────────────────────────────────

def send_telegram_alert(
    bot_token:      str,
    chat_id:        str,
    region:         str,
    score:          float,
    level:          str,
    signal_details: list[dict],
    llm_text:       str = "",
    threshold:      int = 0,
) -> bool:
    """Sendet eine Telegram-Nachricht via Bot-API."""
    if not bot_token or not chat_id:
        return False

    icon_map = {"KRITISCH": "⛔", "ROT": "🔴", "ORANGE": "🟠", "GELB": "🟡", "GRUEN": "🟢"}
    icon = icon_map.get(level, "🔵")

    lines = [
        f"{icon} *NEXUS ALERT – {region}*",
        f"Score: `{score}/100` – Stufe: *{level}*",
    ]
    if threshold:
        lines.append(f"Schwellenwert `{threshold}` ausgelöst")
    lines.append("")
    for sig in signal_details[:5]:
        lines.append(f"• {sig.get('label', '?')} (+{sig.get('points', 0):.0f}Pkt)")
    if llm_text:
        lines.append(f"\n_{llm_text[:200]}_")
    lines.append(f"\n_NEXUS OSINT · {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M UTC')}_")

    text = "\n".join(lines)
    url  = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": "Markdown",
    }
    try:
        body = json.dumps(payload).encode("utf-8")
        req  = Request(url, data=body,
                       headers={"Content-Type": "application/json"},
                       method="POST")
        with urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.warning("[EscWatch] Telegram-Fehler: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Alert-Ausgang: Email
# ─────────────────────────────────────────────────────────────────────────────

def send_email_alert(
    to_addr:        str,
    region:         str,
    score:          float,
    level:          str,
    color_hex:      str,
    signal_details: list[dict],
    llm_text:       str = "",
    threshold:      int = 0,
) -> bool:
    """Sendet eine Alert-Email über den konfigurierten SMTP-Server."""
    if not to_addr:
        return False

    smtp_host = getattr(config, "SMTP_HOST", "")
    smtp_port = getattr(config, "SMTP_PORT", 587)
    smtp_user = getattr(config, "SMTP_USER", "")
    smtp_pw   = getattr(config, "SMTP_PASSWORD", "")
    smtp_from = getattr(config, "SMTP_FROM", "") or smtp_user

    if not smtp_host or not smtp_user or not smtp_pw:
        logger.warning("[EscWatch] SMTP nicht konfiguriert – Email übersprungen")
        return False

    icon_map  = {"KRITISCH": "⛔", "ROT": "🔴", "ORANGE": "🟠", "GELB": "🟡", "GRUEN": "🟢"}
    icon      = icon_map.get(level, "🔵")
    ts_str    = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    subj      = f"{icon} NEXUS ALERT – {region} – {score}/100 ({level})"

    sig_rows = "".join(
        f"<tr><td style='padding:4px 8px;border-bottom:1px solid #1e293b'>{s.get('icon','')}"
        f" {s.get('label','?')}</td>"
        f"<td style='padding:4px 8px;text-align:right;border-bottom:1px solid #1e293b;color:#94a3b8'>"
        f"+{s.get('points',0):.0f}Pkt</td></tr>"
        for s in signal_details[:8]
    )

    html_body = f"""<!DOCTYPE html>
<html><body style="background:#0a0f1a;color:#e2e8f0;font-family:'Segoe UI',sans-serif;padding:24px">
  <div style="max-width:600px;margin:0 auto">
    <div style="border-left:4px solid {color_hex};padding:12px 20px;background:#111827;border-radius:6px;margin-bottom:16px">
      <div style="font-size:22px;font-weight:700;color:{color_hex}">{icon} NEXUS ALERT</div>
      <div style="font-size:28px;font-weight:800;margin:8px 0">{region}</div>
      <div style="font-size:18px;color:{color_hex}">Score: {score}/100 – {level}</div>
      {"<div style='margin-top:6px;font-size:12px;color:#64748b'>Schwellenwert " + str(threshold) + " ausgelöst</div>" if threshold else ""}
    </div>
    {"<div style='background:#0f172a;border-radius:6px;padding:12px 16px;margin-bottom:16px;font-style:italic;color:#94a3b8;font-size:13px'>" + llm_text + "</div>" if llm_text else ""}
    <table style="width:100%;border-collapse:collapse;background:#111827;border-radius:6px">
      <thead><tr style="background:#1e293b">
        <th style="padding:8px;text-align:left;font-size:11px;letter-spacing:1px;color:#64748b">SIGNAL</th>
        <th style="padding:8px;text-align:right;font-size:11px;letter-spacing:1px;color:#64748b">PUNKTE</th>
      </tr></thead>
      <tbody>{sig_rows}</tbody>
    </table>
    <div style="margin-top:16px;font-size:11px;color:#334155;text-align:center">
      NEXUS OSINT · {ts_str} · Automatischer Alert
    </div>
  </div>
</body></html>"""

    msg                          = MIMEMultipart("alternative")
    msg["Subject"]               = subj
    msg["From"]                  = smtp_from
    msg["To"]                    = to_addr
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(smtp_user, smtp_pw)
            srv.sendmail(smtp_from, to_addr.split(","), msg.as_string())
        logger.info("[EscWatch] Alert-Email gesendet an %s", to_addr)
        return True
    except Exception as exc:
        logger.warning("[EscWatch] Email-Fehler: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Alert-Funktion (alle Kanäle)
# ─────────────────────────────────────────────────────────────────────────────

def fire_alert(entry: dict, score_data: dict) -> dict[str, bool]:
    """
    Feuert alle konfigurierten Alert-Kanäle für einen Watchlist-Eintrag.
    Gibt dict mit Ergebnis pro Kanal zurück: {discord: True, email: False, ...}
    """
    region  = entry.get("region", "?")
    score   = score_data.get("score", 0)
    level   = score_data.get("level", "GRUEN")
    color   = score_data.get("color", "#00ff88")
    sigs    = score_data.get("signal_details", [])
    llm_txt = score_data.get("llm_explanation", "")
    coinc   = score_data.get("coinc_note", "")
    thresh  = entry.get("threshold", 0)

    results: dict[str, bool] = {}

    # 1. Desktop-Notification (immer)
    try:
        from nexus_alert import send_alert, PRIORITY_CRITICAL, PRIORITY_HIGH, PRIORITY_MEDIUM
        prio = PRIORITY_CRITICAL if score >= 80 else (PRIORITY_HIGH if score >= 50 else PRIORITY_MEDIUM)
        top_sig = sigs[0].get("label", "Aktives Signal") if sigs else "Eskalation erkannt"
        send_alert(
            title=f"🛰 NEXUS: {region} – {score}/100 ({level})",
            message=f"Schwellenwert {thresh} {'überschritten' if score >= thresh else 'unterschritten'} · {top_sig}",
            priority=prio,
            sound=True,
        )
        results["desktop"] = True
    except Exception as exc:
        logger.debug("[EscWatch] Desktop-Alert: %s", exc)
        results["desktop"] = False

    # 2. Discord Webhook
    wh_url  = entry.get("webhook_url", "")
    wh_type = entry.get("webhook_type", WH_DISCORD)
    if wh_url and wh_type == WH_DISCORD:
        results["discord"] = send_discord_alert(
            wh_url, region, score, level, color, sigs, llm_txt, coinc, thresh
        )
    elif wh_url and wh_type == WH_TELEGRAM:
        # webhook_url wird als bot_token:chat_id genutzt wenn kein separates Feld
        results["discord"] = False
    else:
        results["discord"] = False

    # 3. Telegram Bot
    tg_token  = entry.get("telegram_bot_token", "")
    tg_chat   = entry.get("telegram_chat_id", "")
    if tg_token and tg_chat:
        results["telegram"] = send_telegram_alert(
            tg_token, tg_chat, region, score, level, sigs, llm_txt, thresh
        )
    else:
        results["telegram"] = False

    # 4. Email
    email_to = entry.get("email", "")
    if email_to:
        results["email"] = send_email_alert(
            email_to, region, score, level, color, sigs, llm_txt, thresh
        )
    else:
        results["email"] = False

    # Terminal-Log immer
    icon_map = {"KRITISCH": "⛔", "ROT": "🔴", "ORANGE": "🟠", "GELB": "🟡", "GRUEN": "🟢"}
    icon = icon_map.get(level, "🔵")
    ts   = datetime.now(timezone.utc).strftime("%H:%M UTC")
    sent = [k for k, v in results.items() if v]
    print(f"\n\033[93m{'═'*60}")
    print(f"  {icon} NEXUS ESK-ALERT [{level}] – {ts}")
    print(f"  Region: {region} · Score: {score}/100")
    print(f"  Schwelle: {thresh} · Kanäle: {', '.join(sent) or 'nur Terminal'}")
    if sigs:
        print(f"  Top-Signal: {sigs[0].get('label','?')}")
    print(f"{'═'*60}\033[0m\n")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Background-Engine
# ─────────────────────────────────────────────────────────────────────────────

_engine_thread:  threading.Thread | None = None
_engine_running: bool                    = False
_engine_lock:    threading.Lock          = threading.Lock()


def _engine_loop() -> None:
    """Hintergrund-Thread: prüft jede Minute alle aktiven Watchlist-Einträge."""
    global _engine_running
    logger.info("[EscWatch] Engine gestartet (Intervall: %ds)", POLL_INTERVAL)

    while _engine_running:
        try:
            entries = list_entries()
            active  = [e for e in entries if e.get("enabled")]

            for entry in active:
                if not _engine_running:
                    break
                region    = entry.get("region", "")
                entry_id  = entry["id"]
                score_data = _fetch_score(region)

                if score_data is None:
                    logger.debug("[EscWatch] Kein Score für '%s' (Server offline?)", region)
                    continue

                score = score_data["score"]
                _update_last_score(entry_id, score)

                if _should_alert(entry, score):
                    logger.info("[EscWatch] ALERT ausgelöst: '%s' Score=%s Threshold=%s",
                                region, score, entry.get("threshold"))
                    fire_alert(entry, score_data)
                    _mark_alerted(entry_id, score)

                time.sleep(1)  # kurze Pause zwischen Regionen

        except Exception as exc:
            logger.error("[EscWatch] Engine-Fehler: %s", exc)

        # Warte bis zum nächsten Zyklus
        for _ in range(POLL_INTERVAL):
            if not _engine_running:
                return
            time.sleep(1)


def start_engine() -> bool:
    """Startet den Alert-Engine-Thread. Gibt True zurück wenn erfolgreich."""
    global _engine_thread, _engine_running
    with _engine_lock:
        if _engine_running:
            return True
        init_db()
        _engine_running = True
        _engine_thread  = threading.Thread(
            target=_engine_loop,
            daemon=True,
            name="nexus-esc-watchlist",
        )
        _engine_thread.start()
    return True


def stop_engine() -> None:
    global _engine_running
    _engine_running = False


def is_running() -> bool:
    return _engine_running


# ─────────────────────────────────────────────────────────────────────────────
# REST-API-Handler (für nexus_live_server.py)
# ─────────────────────────────────────────────────────────────────────────────

def handle_api_get() -> dict:
    """GET /api/esc_watchlist – alle Einträge + Engine-Status"""
    entries = list_entries()
    # last_score pro Region anreichern
    return {
        "entries":        entries,
        "engine_running": is_running(),
        "poll_interval":  POLL_INTERVAL,
        "count":          len(entries),
        "active_count":   sum(1 for e in entries if e.get("enabled")),
    }


def handle_api_post(body: dict) -> dict:
    """POST /api/esc_watchlist – neuen Eintrag anlegen"""
    region    = (body.get("region") or "").strip()
    threshold = int(body.get("threshold") or 50)
    if not region:
        return {"error": "region erforderlich"}
    if not 0 <= threshold <= 100:
        return {"error": "threshold muss zwischen 0 und 100 liegen"}
    new_id = add_entry(
        region            = region,
        threshold         = threshold,
        direction         = body.get("direction", DIR_ABOVE),
        webhook_url       = body.get("webhook_url", ""),
        webhook_type      = body.get("webhook_type", WH_DISCORD),
        telegram_chat_id  = body.get("telegram_chat_id", ""),
        telegram_bot_token= body.get("telegram_bot_token", ""),
        email             = body.get("email", ""),
        cooldown_min      = int(body.get("cooldown_min") or DEFAULT_COOLDOWN),
    )
    return {"created": True, "id": new_id, "region": region, "threshold": threshold}


def handle_api_delete(entry_id: int) -> dict:
    """DELETE /api/esc_watchlist?id=N"""
    ok = remove_entry(entry_id)
    return {"deleted": ok, "id": entry_id}


def handle_api_put(entry_id: int, body: dict) -> dict:
    """PUT /api/esc_watchlist?id=N – Felder aktualisieren"""
    ok = update_entry(entry_id, **body)
    return {"updated": ok, "id": entry_id}


# ─────────────────────────────────────────────────────────────────────────────
# Formatierte Ausgabe (Terminal)
# ─────────────────────────────────────────────────────────────────────────────

def show_table() -> str:
    entries = list_entries()
    if not entries:
        return (
            "📋 Eskalations-Watchlist ist leer.\n"
            "  Hinzufügen: python nexus_escalation_watchlist.py --add <region> <threshold>\n"
            "  Beispiel:   python nexus_escalation_watchlist.py --add ukraine 50"
        )
    lines = [
        f"{'─'*72}",
        f"  {'ID':>3}  {'REGION':<20}  {'SCHWELLE':>8}  {'RICHTUNG':<7}  {'STATUS':<8}  {'LETZTER SCORE':>13}",
        f"{'─'*72}",
    ]
    for e in entries:
        stat   = "✓ AKTIV" if e.get("enabled") else "– PAUSE"
        score  = f"{e.get('last_score', 0):.0f}/100" if e.get("last_score") else "–"
        dirstr = "≥ threshold" if e.get("direction") == DIR_ABOVE else "< threshold"
        lines.append(
            f"  {e['id']:>3}  {e['region']:<20}  {e['threshold']:>8}  {dirstr:<7}  {stat:<8}  {score:>13}"
        )
        channels = []
        if e.get("webhook_url"):    channels.append(f"Discord")
        if e.get("telegram_bot_token"): channels.append("Telegram")
        if e.get("email"):          channels.append(f"Email→{e['email'][:20]}")
        if channels:
            lines.append(f"       Kanäle: {', '.join(channels)}")
        if e.get("last_alert_ts"):
            lines.append(f"       Letzter Alert: {e['last_alert_ts'][:16]}")
    lines.append(f"{'─'*72}")
    lines.append(f"  Engine: {'✓ LÄUFT' if is_running() else '✗ GESTOPPT'} · Poll: alle {POLL_INTERVAL}s")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import urllib.parse
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    args = sys.argv[1:]

    if not args or "--list" in args:
        print(show_table())
        sys.exit(0)

    if "--add" in args:
        idx   = args.index("--add")
        reg   = args[idx + 1] if idx + 1 < len(args) else ""
        thr   = int(args[idx + 2]) if idx + 2 < len(args) else 50
        if not reg:
            print("Fehler: Region angeben. Beispiel: --add ukraine 50")
            sys.exit(1)
        new_id = add_entry(reg, thr)
        print(f"✓ Eintrag #{new_id} angelegt: {reg} Schwelle={thr}")
        print(show_table())
        sys.exit(0)

    if "--remove" in args:
        idx = args.index("--remove")
        eid = int(args[idx + 1]) if idx + 1 < len(args) else -1
        if remove_entry(eid):
            print(f"✓ Eintrag #{eid} entfernt.")
        else:
            print(f"✗ Eintrag #{eid} nicht gefunden.")
        sys.exit(0)

    if "--test-alert" in args:
        idx = args.index("--test-alert")
        eid = int(args[idx + 1]) if idx + 1 < len(args) else -1
        entry = get_entry(eid)
        if not entry:
            print(f"✗ Eintrag #{eid} nicht gefunden.")
            sys.exit(1)
        print(f"Teste Alert für Eintrag #{eid} ({entry['region']})...")
        fake_score = {
            "score": float(entry["threshold"]) + 5,
            "level": "ORANGE",
            "color": "#ff8800",
            "icon":  "🟠",
            "signal_count": 3,
            "signal_details": [
                {"signal": "test_isr",     "label": "TEST: ISR-Aufklärer aktiv",   "points": 20.0, "conf": "high",   "icon": "🔎"},
                {"signal": "test_gps",     "label": "TEST: GPS-Jamming Zone",       "points": 10.0, "conf": "medium", "icon": "📡"},
                {"signal": "test_telegram","label": "TEST: Telegram Surge x4.2",    "points": 5.0,  "conf": "low",    "icon": "⚡"},
            ],
            "llm_explanation": "Dies ist ein Test-Alert von NEXUS. Im Einsatz erscheint hier die LLM-Begründung des Scores.",
            "coinc_note":       "Test-Koinzidenz",
            "timestamp":        datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
        }
        results = fire_alert(entry, fake_score)
        print(f"Alert gesendet über: {[k for k,v in results.items() if v] or ['Terminal']}")
        sys.exit(0)

    if "--run" in args:
        print(f"Starte NEXUS Eskalations-Watchlist-Engine (Poll alle {POLL_INTERVAL}s)...")
        print("Drücke Ctrl+C zum Beenden.")
        print(show_table())
        start_engine()
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            print("\nEngine gestoppt.")
            stop_engine()
        sys.exit(0)

    print(__doc__)
