"""
NEXUS – Task 55: Daily Report mit Eskalations-Score + Email
===========================================================
Läuft täglich (cron/Task Scheduler) oder manuell.
Neu in Task 55:
  - Eskalations-Score (nexus_escalation.py) pro Region + global
  - Animierter Score-Balken im HTML
  - HTML-Email via SMTP (Gmail App-Password, Outlook, eigener SMTP)
  - Email-Konfiguration in config.py (SMTP_HOST, SMTP_PORT, ...)
  - --email Flag zum direkten Versand
  - --schedule richtet Windows Task Scheduler ein

Einrichten:
  python nexus_daily.py              → Bericht erstellen
  python nexus_daily.py --email      → Bericht erstellen + Email senden
  python nexus_daily.py --schedule   → Windows Task 07:00 Uhr einrichten
  python nexus_daily.py --remove     → Zeitplan entfernen

Email-Konfiguration in config.py:
  SMTP_HOST     = "smtp.gmail.com"
  SMTP_PORT     = 587
  SMTP_USER     = "deine@gmail.com"
  SMTP_PASSWORD = "app-passwort"     # Gmail: 16-stelliges App-Passwort
  SMTP_TO       = "ziel@email.com"   # Empfänger (auch mehrere: "a@b.com,c@d.com")
"""

from __future__ import annotations

import os
import sys
import subprocess
import smtplib
import webbrowser
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from datetime import datetime, timezone
from pathlib import Path


# ── Standard-Regionen wenn Watchlist leer ───────────────────────────────────
DEFAULT_REGIONS = [
    "Naher Osten",
    "Ukraine",
    "Hormuz-Strasse",
    "Taiwan-Strasse",
    "Rotes Meer",
]

REPORT_DIR = Path(__file__).parent / "nexus_reports"

# ── Email-Konfiguration aus config.py ────────────────────────────────────────
def _email_config() -> dict:
    """Liest Email-Einstellungen aus config.py."""
    try:
        import config  # type: ignore
        return {
            "host":     getattr(config, "SMTP_HOST",     "smtp.gmail.com"),
            "port":     getattr(config, "SMTP_PORT",     587),
            "user":     getattr(config, "SMTP_USER",     ""),
            "password": getattr(config, "SMTP_PASSWORD", ""),
            "to":       getattr(config, "SMTP_TO",       ""),
            "from":     getattr(config, "SMTP_FROM",     getattr(config, "SMTP_USER", "")),
        }
    except ImportError:
        return {"host": "", "port": 587, "user": "", "password": "", "to": "", "from": ""}


# ── Einzelner Regions-Bericht ────────────────────────────────────────────────

def _build_region_section(region: str) -> dict:
    """Sammelt alle Daten für eine Region + Eskalations-Score. Fehler werden abgefangen."""
    section = {
        "region":     region,
        "flights":    None,
        "weather":    None,
        "maritime":   None,
        "seismic":    [],
        "articles":   [],
        "gdelt":      [],
        "escalation": None,   # NEU: Eskalations-Score
        "acled":      None,   # NEU: ACLED-Daten
        "errors":     [],
    }

    try:
        from nexus_flights import get_flights  # type: ignore
        fd = get_flights(region)
        if fd and "error" not in fd:
            section["flights"] = fd
    except Exception as e:
        section["errors"].append(f"Flug: {e}")

    try:
        from nexus_weather import weather_for_report  # type: ignore
        wd = weather_for_report(region)
        if wd and "error" not in wd:
            section["weather"] = wd
    except Exception as e:
        section["errors"].append(f"Wetter: {e}")

    try:
        from nexus_maritime import get_maritime_situation  # type: ignore
        md = get_maritime_situation(region)
        if md and "error" not in md:
            section["maritime"] = md
    except Exception as e:
        section["errors"].append(f"Maritime: {e}")

    try:
        from nexus_seismic import get_earthquakes_for_region  # type: ignore
        section["seismic"] = get_earthquakes_for_region(region, hours=24)
    except Exception as e:
        section["errors"].append(f"Seismik: {e}")

    try:
        from nexus_gdelt import fetch_gdelt_articles  # type: ignore
        section["gdelt"] = fetch_gdelt_articles(region, hours=24, max_records=10)
    except Exception as e:
        section["errors"].append(f"GDELT: {e}")

    try:
        from nexus_rss import fetch_news  # type: ignore
        section["articles"] = fetch_news(fast=True, keyword_filter=region[:30]) or []
    except Exception as e:
        section["errors"].append(f"RSS: {e}")

    # Telegram OSINT-Kanäle
    try:
        from nexus_telegram import fetch_osint_channels as _tg  # type: ignore
        tg_arts = _tg(keyword_filter=region, limit_per_channel=5, max_channels=4)
        if tg_arts:
            existing = {a.get("title", "") for a in section["articles"]}
            for a in tg_arts:
                if a.get("title", "") not in existing:
                    section["articles"].append(a)
                    existing.add(a["title"])
    except Exception as e:
        section["errors"].append(f"Telegram: {e}")

    # Reddit OSINT-Subreddits
    try:
        from nexus_reddit import fetch_osint_reddit as _reddit  # type: ignore
        reddit_arts = _reddit(keyword_filter=region, limit_per_sub=10, max_subs=3)
        if reddit_arts:
            existing = {a.get("title", "") for a in section["articles"]}
            for a in reddit_arts:
                if a.get("title", "") not in existing:
                    section["articles"].append(a)
                    existing.add(a["title"])
    except Exception as e:
        section["errors"].append(f"Reddit: {e}")

    # NEU: Eskalations-Score
    try:
        from nexus_escalation import compute_escalation  # type: ignore
        live_snapshot = {
            "flights":    section["flights"],
            "maritime":   section["maritime"],
            "seismic":    {"candidates": section["seismic"]},
        }
        section["escalation"] = compute_escalation(live_snapshot, region)
    except Exception as e:
        section["errors"].append(f"Eskalation: {e}")

    # NEU: ACLED-Zusammenfassung
    try:
        from nexus_acled import acled_for_escalation  # type: ignore
        section["acled"] = acled_for_escalation(region, days_short=1, days_long=7)
    except Exception as e:
        section["errors"].append(f"ACLED: {e}")

    return section


def _esc_color(score: float) -> str:
    """Farbe passend zum Eskalations-Score."""
    if score >= 80:  return "#ff2222"
    if score >= 60:  return "#ff6600"
    if score >= 40:  return "#ffaa00"
    if score >= 20:  return "#aacc00"
    return "#00cc88"


def _section_to_html(sec: dict) -> str:
    """Baut HTML-Block für eine Region inkl. Eskalations-Score-Balken."""
    region  = sec["region"]
    fd      = sec["flights"]
    wd      = sec["weather"]
    md      = sec["maritime"]
    seismic = sec["seismic"]
    arts    = (sec["articles"] or []) + (sec["gdelt"] or [])
    esc     = sec.get("escalation") or {}
    acled   = sec.get("acled") or {}

    esc_score = esc.get("score", 0)
    esc_level = esc.get("level", "GRÜN")
    esc_icon  = esc.get("icon", "🟢")
    esc_color = _esc_color(esc_score)

    # Alarm-Level
    alerts = []
    if fd and fd.get("suspicious"):
        alerts.append(f"✈ {len(fd['suspicious'])} auffällige Flugzeuge")
    if md and md.get("alert_count", 0) > 0:
        alerts.append(f"⚓ {md['alert_count']} Maritime-Alarme")
    if seismic:
        big = [q for q in seismic if q.get("mag", 0) >= 4.5]
        if big:
            alerts.append(f"🌍 {len(big)} Erdbeben ≥M4.5")
    if acled.get("events_24h", 0) > 0:
        alerts.append(f"⚔ {acled['events_24h']} ACLED-Events/24h")

    status_color = "#ff4444" if (esc_score >= 40 or alerts) else "#00ff88"
    status_text  = " | ".join(alerts) if alerts else "Keine kritischen Alarme"

    bar_w = max(4, int(esc_score))

    html = [
        f'<div style="margin-bottom:18px;border:1px solid #1e3a4a;border-radius:4px;overflow:hidden">',
        # Header
        f'<div style="background:#0a1620;padding:8px 14px;display:flex;justify-content:space-between;align-items:center">',
        f'  <span style="color:#00d4ff;font-weight:bold;letter-spacing:2px">{region.upper()}</span>',
        f'  <span style="color:{status_color};font-size:11px">{status_text}</span>',
        f'</div>',
        # Eskalations-Score-Balken
        f'<div style="background:#080e14;padding:6px 14px;display:flex;align-items:center;gap:10px">',
        f'  <span style="color:{esc_color};font-size:11px;min-width:110px">{esc_icon} {esc_level} ({esc_score:.0f}/100)</span>',
        f'  <div style="flex:1;background:#1a2530;border-radius:2px;height:6px">',
        f'    <div style="background:{esc_color};width:{bar_w}%;height:6px;border-radius:2px;'
        f'transition:width 0.5s"></div>',
        f'  </div>',
        f'</div>',
        f'<div style="padding:10px 14px;background:#111820">',
    ]

    # Wetter
    if wd:
        html.append(
            f'<div style="font-size:11px;color:#8aa0b0;margin-bottom:5px">'
            f'⛅ {wd.get("weather_desc","?")} | {wd.get("temperature_c","?")}°C '
            f'| {wd.get("wind_kmh","?")}km/h</div>'
        )

    # Flüge
    if fd:
        susp = len(fd.get("suspicious", []))
        c    = "#ff4444" if susp else "#00cc88"
        html.append(
            f'<div style="font-size:11px;color:#8aa0b0;margin-bottom:5px">'
            f'✈ {fd.get("total",0)} Transponder | '
            f'<span style="color:{c}">{susp} auffällig</span></div>'
        )

    # Erdbeben
    for q in seismic[:2]:
        hint = f' — {q["osint_hint"]}' if q.get("osint_hint") else ""
        html.append(
            f'<div style="font-size:11px;color:#8aa0b0;margin-bottom:4px">'
            f'🌍 M{q["mag"]} | {q.get("place","")[:50]}{hint}</div>'
        )

    # ACLED-Zeile
    if acled.get("configured") and acled.get("events_24h", 0) > 0:
        fat = acled.get("fatalities_24h", 0)
        crit = acled.get("critical_count", 0)
        fat_s = f" | {fat} Todesopfer" if fat > 0 else ""
        html.append(
            f'<div style="font-size:11px;color:#ff8800;margin-bottom:5px">'
            f'⚔ ACLED: {acled["events_24h"]} Events/24h'
            f' | {crit} kritisch{fat_s}</div>'
        )
        if acled.get("top_event"):
            html.append(
                f'<div style="font-size:10px;color:#6a8090;margin-bottom:5px;'
                f'border-left:2px solid #ff6600;padding-left:8px">'
                f'{acled["top_event"][:100]}</div>'
            )

    # Eskalations-Signale
    if esc.get("signal_details"):
        sigs = esc["signal_details"][:3]
        sig_text = " | ".join(f'{s.get("name","?")} ({s.get("score",0):.0f})' for s in sigs)
        html.append(
            f'<div style="font-size:10px;color:#6a8090;margin-bottom:5px">'
            f'Signale: {sig_text}</div>'
        )

    # Top-Meldungen
    for a in arts[:4]:
        age   = a.get("age_min", 9999)
        age_s = f"{age}min" if age < 120 else f"{age//60}h"
        t = (a.get("title") or "")[:90].replace("<", "&lt;")
        u = a.get("url", "#")
        s = a.get("source", "")
        html.append(
            f'<div style="font-size:11px;margin-bottom:4px;border-left:2px solid #1e3a4a;padding-left:8px">'
            f'<a href="{u}" target="_blank" style="color:#c8d6e0">{t}</a>'
            f'<span style="color:#4a6070;font-size:10px"> · {s} · vor {age_s}</span></div>'
        )

    html.append('</div></div>')
    return "\n".join(html)


# ── Kompletter Tagesbericht ──────────────────────────────────────────────────

def create_daily_brief(
    regions: list[str] = None,
    auto_open: bool = True,
    pdf: bool = True,
    send_email: bool = False,
) -> str:
    """
    Erstellt den kompletten Tagesbericht als HTML (+ optional PDF + Email).
    Gibt HTML-Dateipfad zurück.
    """
    if not regions:
        try:
            from nexus_memory import wl_list  # type: ignore
            wl = wl_list()
            regions = [e["term"] for e in wl] if wl else DEFAULT_REGIONS
        except Exception:
            regions = DEFAULT_REGIONS

    ts    = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    ts_fn = datetime.now().strftime("%Y%m%d_%H%M")

    sections_data: list[dict] = []
    sections_html = ""
    for region in regions:
        sec = _build_region_section(region)
        sections_data.append(sec)
        sections_html += _section_to_html(sec)

    # Globaler Eskalations-Score (Mittelwert der Regionen)
    esc_scores = [
        s.get("escalation", {}).get("score", 0)
        for s in sections_data
        if s.get("escalation")
    ]
    global_score = sum(esc_scores) / len(esc_scores) if esc_scores else 0
    global_color = _esc_color(global_score)
    global_bar   = max(4, int(global_score))

    # Gesamt-Alarme
    total_alerts = 0
    for sec in sections_data:
        if sec["flights"] and sec["flights"].get("suspicious"):
            total_alerts += len(sec["flights"]["suspicious"])
        if sec["maritime"] and sec["maritime"].get("alert_count", 0):
            total_alerts += sec["maritime"]["alert_count"]
        if sec.get("acled", {}).get("critical_count", 0):
            total_alerts += sec["acled"]["critical_count"]

    alert_color = "#ff4444" if total_alerts > 0 else "#00cc88"
    alert_text  = f"{total_alerts} aktive Alarme" if total_alerts else "Keine kritischen Alarme"

    # Top-Signal-Zusammenfassung
    top_signals_html = ""
    for sec in sections_data:
        esc = sec.get("escalation") or {}
        if esc.get("score", 0) >= 30:
            sc  = esc["score"]
            lvl = esc.get("level", "?")
            ico = esc.get("icon", "")
            col = _esc_color(sc)
            bw  = max(4, int(sc))
            sigs = ", ".join(
                s.get("name", "?") for s in (esc.get("signal_details") or [])[:3]
            )
            top_signals_html += (
                f'<tr>'
                f'<td style="padding:4px 8px;color:#00d4ff">{sec["region"]}</td>'
                f'<td style="padding:4px 8px">'
                f'  <div style="background:#1a2530;border-radius:2px;height:8px;width:160px;display:inline-block;vertical-align:middle">'
                f'    <div style="background:{col};width:{bw}%;height:8px;border-radius:2px"></div>'
                f'  </div>'
                f'  <span style="color:{col};margin-left:8px">{ico} {lvl} {sc:.0f}/100</span>'
                f'</td>'
                f'<td style="padding:4px 8px;color:#6a8090;font-size:10px">{sigs}</td>'
                f'</tr>'
            )

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NEXUS Tagesbericht – {ts}</title>
<style>
  body{{background:#0a0e14;color:#c8d6e0;font-family:'Courier New',monospace;font-size:13px;margin:0;padding:0}}
  a{{color:#00d4ff;text-decoration:none}} a:hover{{text-decoration:underline}}
  .header{{background:linear-gradient(90deg,#0a1a2e,#0d2035);border-bottom:2px solid #00d4ff;padding:14px 24px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}}
  .title{{color:#00d4ff;font-size:20px;font-weight:bold;letter-spacing:4px}}
  .body{{padding:20px 24px;max-width:960px;margin:0 auto}}
  .summary-box{{background:#111820;border:1px solid #1e3a4a;border-radius:4px;padding:14px 18px;margin-bottom:20px}}
  .footer{{border-top:1px solid #1e3a4a;padding:8px 24px;color:#4a6070;font-size:10px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:4px}}
  table{{border-collapse:collapse;width:100%}}
  @media(max-width:600px){{.header{{flex-direction:column}}.body{{padding:12px 14px}}}}
</style>
</head>
<body>
<div class="header">
  <div>
    <div class="title">◈ NEXUS TAGESBERICHT</div>
    <div style="color:#4a6070;font-size:10px;letter-spacing:2px">AUTOMATISCHER OSINT-LAGEBERICHT</div>
  </div>
  <div style="text-align:right">
    <div style="color:#00ff88">{ts}</div>
    <div style="color:#4a6070;font-size:10px">{len(regions)} Regionen | v0.8</div>
  </div>
</div>
<div class="body">

  <!-- Gesamt-Status -->
  <div class="summary-box">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px">
      <span style="color:#c8d6e0;font-weight:bold">GESAMTLAGE</span>
      <span style="color:{alert_color}">{alert_text}</span>
    </div>
    <!-- Globaler Eskalations-Score -->
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:6px">
      <span style="color:#8aa0b0;font-size:11px;min-width:140px">Globaler Esc.-Score:</span>
      <div style="flex:1;background:#1a2530;border-radius:3px;height:10px">
        <div style="background:{global_color};width:{global_bar}%;height:10px;border-radius:3px"></div>
      </div>
      <span style="color:{global_color};font-weight:bold;min-width:50px;text-align:right">{global_score:.0f}/100</span>
    </div>
  </div>

  <!-- Eskalations-Übersicht Tabelle -->
  {"<div class='summary-box'><div style='color:#00d4ff;font-size:11px;letter-spacing:2px;margin-bottom:8px'>ESKALATIONS-ÜBERSICHT</div><table>" + top_signals_html + "</table></div>" if top_signals_html else ""}

  <!-- Regions-Sektionen -->
  {sections_html}

</div>
<div class="footer">
  <span>NEXUS OSINT v0.8 | Tagesbericht | Nur für informatorische Zwecke</span>
  <span>{ts}</span>
</div>
</body>
</html>"""

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    filepath = REPORT_DIR / f"nexus_daily_{ts_fn}.html"
    filepath.write_text(html, encoding="utf-8")

    if auto_open:
        try:
            webbrowser.open(filepath.as_uri())
        except Exception:
            pass

    if pdf:
        try:
            from nexus_pdf_export import export_daily_brief_pdf  # type: ignore
            pdf_path = export_daily_brief_pdf(regions=regions, auto_open=False)
            print(f"[NEXUS] PDF gespeichert: {Path(pdf_path).name}", flush=True)
        except Exception as _pe:
            print(f"[NEXUS] PDF-Export übersprungen: {_pe}", flush=True)

    if send_email:
        ok, msg = send_daily_email(html, ts, global_score)
        print(f"[NEXUS] Email: {'✅ ' + msg if ok else '❌ ' + msg}", flush=True)

    return str(filepath)


# ── Email-Versand ─────────────────────────────────────────────────────────────

def send_daily_email(html_content: str, timestamp: str, esc_score: float = 0) -> tuple[bool, str]:
    """
    Versendet den Tagesbericht als HTML-Email via SMTP.

    Konfiguration in config.py:
      SMTP_HOST     = "smtp.gmail.com"       # oder smtp-mail.outlook.com
      SMTP_PORT     = 587                     # 587 = TLS, 465 = SSL
      SMTP_USER     = "deine@gmail.com"
      SMTP_PASSWORD = "app-passwort"
      SMTP_TO       = "empfaenger@email.com"

    Gmail App-Passwort erstellen:
      myaccount.google.com → Sicherheit → 2FA → App-Passwörter → 'Mail' → 16-stelliger Code
    """
    cfg = _email_config()
    if not cfg["user"] or not cfg["password"] or not cfg["to"]:
        return False, ("SMTP nicht konfiguriert. config.py: "
                       "SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_TO setzen.")

    esc_color = _esc_color(esc_score)
    subject   = f"[NEXUS] Tagesbericht {timestamp} | Esc-Score: {esc_score:.0f}/100"

    # Kompakte Email-Version (manche Clients kürzen sehr lange HTML-Mails)
    email_html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="background:#0a0e14;color:#c8d6e0;font-family:monospace;font-size:13px;margin:0;padding:20px">
<div style="max-width:700px;margin:0 auto">
  <div style="background:#0a1a2e;border-bottom:2px solid #00d4ff;padding:12px 16px;margin-bottom:16px">
    <div style="color:#00d4ff;font-size:18px;font-weight:bold;letter-spacing:3px">◈ NEXUS TAGESBERICHT</div>
    <div style="color:#4a6070;font-size:10px">{timestamp}</div>
  </div>
  <div style="background:#111820;border:1px solid #1e3a4a;padding:12px;margin-bottom:16px">
    <div style="color:#8aa0b0;font-size:11px;margin-bottom:6px">GLOBALER ESKALATIONS-SCORE</div>
    <div style="background:#1a2530;border-radius:3px;height:12px;margin-bottom:6px">
      <div style="background:{esc_color};width:{max(4,int(esc_score))}%;height:12px;border-radius:3px"></div>
    </div>
    <div style="color:{esc_color};font-size:18px;font-weight:bold">{esc_score:.0f} / 100</div>
  </div>
  {html_content}
  <div style="color:#4a6070;font-size:10px;margin-top:16px;border-top:1px solid #1e3a4a;padding-top:8px">
    NEXUS OSINT v0.8 | Nur für informatorische Zwecke | {timestamp}
  </div>
</div>
</body></html>"""

    recipients = [r.strip() for r in cfg["to"].split(",") if r.strip()]

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = cfg["from"] or cfg["user"]
        msg["To"]      = ", ".join(recipients)
        msg["Date"]    = formatdate(localtime=True)

        # Plaintext-Fallback
        plain = f"NEXUS Tagesbericht {timestamp}\nEskalations-Score: {esc_score:.0f}/100\n"
        msg.attach(MIMEText(plain, "plain", "utf-8"))
        msg.attach(MIMEText(email_html, "html", "utf-8"))

        port = cfg["port"]
        if port == 465:
            # SSL (direkt)
            with smtplib.SMTP_SSL(cfg["host"], port, timeout=15) as srv:
                srv.login(cfg["user"], cfg["password"])
                srv.sendmail(cfg["user"], recipients, msg.as_string())
        else:
            # TLS (STARTTLS)
            with smtplib.SMTP(cfg["host"], port, timeout=15) as srv:
                srv.ehlo()
                srv.starttls()
                srv.ehlo()
                srv.login(cfg["user"], cfg["password"])
                srv.sendmail(cfg["user"], recipients, msg.as_string())

        return True, f"Gesendet an {', '.join(recipients)}"

    except smtplib.SMTPAuthenticationError:
        return False, ("SMTP-Auth fehlgeschlagen. Bei Gmail: App-Passwort verwenden, "
                       "nicht das normale Passwort.")
    except smtplib.SMTPException as e:
        return False, f"SMTP-Fehler: {e}"
    except Exception as e:
        return False, f"Email-Fehler: {e}"


# ── Windows Task Scheduler einrichten ────────────────────────────────────────

def setup_scheduler(hour: int = 7, minute: int = 0) -> bool:
    """Richtet Windows Task Scheduler für tägliche 07:00 Ausführung ein."""
    script_path = Path(__file__).resolve()
    python_exe  = sys.executable
    task_name   = "NEXUS_DailyBrief"
    time_str    = f"{hour:02d}:{minute:02d}"

    cmd = [
        "schtasks", "/create", "/f",
        "/tn",  task_name,
        "/tr",  f'"{python_exe}" "{script_path}"',
        "/sc",  "daily",
        "/st",  time_str,
        "/ru",  "SYSTEM",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0
    except Exception:
        return False


def remove_scheduler() -> bool:
    try:
        result = subprocess.run(
            ["schtasks", "/delete", "/f", "/tn", "NEXUS_DailyBrief"],
            capture_output=True, text=True
        )
        return result.returncode == 0
    except Exception:
        return False


# ── Direktaufruf ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if "--schedule" in sys.argv:
        ok = setup_scheduler(hour=7, minute=0)
        print("✅ Tagesbericht täglich 07:00 Uhr eingerichtet." if ok
              else "❌ Fehler beim Einrichten. Als Admin ausführen?")
    elif "--remove" in sys.argv:
        ok = remove_scheduler()
        print("✅ Zeitplan entfernt." if ok else "❌ Fehler.")
    elif "--email-test" in sys.argv:
        print("NEXUS: Teste Email-Versand...")
        cfg = _email_config()
        print(f"  SMTP Host  : {cfg['host']}:{cfg['port']}")
        print(f"  Von        : {cfg['user']}")
        print(f"  An         : {cfg['to']}")
        ok, msg = send_daily_email(
            "<p>NEXUS Email Test — Verbindung erfolgreich.</p>",
            datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
            esc_score=42.0,
        )
        print("✅ Email gesendet:" if ok else "❌ Fehler:", msg)
    else:
        do_email = "--email" in sys.argv
        print("NEXUS: Erstelle Tagesbericht" + (" + Email" if do_email else "") + "...")
        path = create_daily_brief(send_email=do_email)
        print(f"✅ Gespeichert: {path}")
        if do_email:
            print("   (Email-Versand oben geloggt)")
