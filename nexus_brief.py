"""
nexus_brief.py — NEXUS 6h-Kurzbericht
======================================
Liest die letzten Lauf-JSONs aus nexus_longtest_daten/ und erzeugt
einen kompakten 5-Satz-Kurzbericht über die letzte 6 Stunden.

Ausgabe:
  - Text (für Terminal / Email)
  - HTML-Datei (für Browser / Weitergabe)

Aufruf:
  python nexus_brief.py               → Bericht der letzten 6h
  python nexus_brief.py --stunden 12  → Bericht der letzten 12h
  python nexus_brief.py --region Iran → nur Iran-Läufe
  python nexus_brief.py --email       → zusätzlich per Email senden
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

BASIS_DIR  = os.path.dirname(os.path.abspath(__file__))
DATEN_DIR  = os.path.join(BASIS_DIR, "nexus_longtest_daten")
BRIEF_HTML = os.path.join(DATEN_DIR, "nexus_brief.html")


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _lade_laeufe(stunden: int = 6, region: str = "") -> list[dict]:
    """Lädt alle Lauf-JSONs der letzten N Stunden."""
    if not os.path.isdir(DATEN_DIR):
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=stunden)
    laeufe = []

    for fname in sorted(os.listdir(DATEN_DIR)):
        if not (fname.startswith("lauf_") and fname.endswith(".json")):
            continue
        fpath = os.path.join(DATEN_DIR, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            ts_str = data.get("timestamp", "")
            # ISO-Timestamp parsen
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts < cutoff:
                continue
            if region and data.get("ziel", "").lower() != region.lower():
                continue
            laeufe.append(data)
        except Exception:
            continue

    return laeufe


def _score_trend(laeufe: list[dict]) -> str:
    """Berechnet ob Score steigt, fällt oder stabil ist."""
    scores = [l["quellen"].get("eskalation", {}).get("score", 0) or 0 for l in laeufe]
    if len(scores) < 2:
        return "stabil"
    delta = scores[-1] - scores[0]
    if delta > 5:
        return "steigend ↑"
    elif delta < -5:
        return "fallend ↓"
    return "stabil →"


def _modul_verfuegbarkeit(laeufe: list[dict]) -> dict[str, float]:
    """Berechnet Verfügbarkeit (% OK-Läufe) pro Modul."""
    if not laeufe:
        return {}
    module = ["flights", "gdelt", "rss", "seismik", "gpsjam",
              "firms", "viirs", "acled", "wirtschaft", "telegram", "reddit"]
    result = {}
    for m in module:
        ok = sum(1 for l in laeufe
                 if l["quellen"].get(m, {}).get("status") == "ok")
        result[m] = round(ok / len(laeufe) * 100)
    return result


def _top_headlines(laeufe: list[dict], n: int = 5) -> list[str]:
    """Sammelt die wichtigsten RSS-Headlines über alle Läufe."""
    seen: set = set()
    headlines = []
    for lauf in reversed(laeufe):  # neueste zuerst
        rss = lauf["quellen"].get("rss", {})
        for h in (rss.get("headlines") or []):
            h = h.strip()
            if h and h not in seen and len(h) > 20:
                seen.add(h)
                headlines.append(h)
                if len(headlines) >= n:
                    return headlines
    return headlines


def _aktive_signale(laeufe: list[dict]) -> list[str]:
    """Aggregiert alle aktiven Eskalations-Signale über alle Läufe."""
    signal_counts: dict[str, int] = {}
    for lauf in laeufe:
        for sig in lauf["quellen"].get("eskalation", {}).get("signale", []):
            signal_counts[sig] = signal_counts.get(sig, 0) + 1
    # Nur Signale die in >50% der Läufe aufgetaucht sind
    threshold = max(1, len(laeufe) // 2)
    return [s for s, c in sorted(signal_counts.items(), key=lambda x: -x[1])
            if c >= threshold]


# ── Bericht generieren ────────────────────────────────────────────────────────

def generiere_brief(stunden: int = 6, region: str = "") -> dict:
    """
    Erstellt den Kurzbericht als strukturiertes Dict.
    Gibt zurück: text, html, stats, laeufe_count
    """
    laeufe = _lade_laeufe(stunden, region)

    if not laeufe:
        return {
            "text": f"Keine Daten für die letzten {stunden}h verfügbar.",
            "html": "<p>Keine Daten verfügbar.</p>",
            "stats": {},
            "laeufe_count": 0,
        }

    ziel          = laeufe[-1].get("ziel", region or "Unbekannt")
    jetzt         = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    start_ts      = laeufe[0]["timestamp"][:16].replace("T", " ") + " UTC"
    end_ts        = laeufe[-1]["timestamp"][:16].replace("T", " ") + " UTC"

    # Score-Statistiken
    scores        = [l["quellen"].get("eskalation", {}).get("score", 0) or 0 for l in laeufe]
    score_max     = max(scores) if scores else 0
    score_min     = min(scores) if scores else 0
    score_avg     = round(sum(scores) / len(scores), 1) if scores else 0
    score_aktuell = scores[-1] if scores else 0
    score_level   = laeufe[-1]["quellen"].get("eskalation", {}).get("level", "?")
    trend         = _score_trend(laeufe)

    # Daten-Statistiken
    total_flights = sum(l["quellen"].get("flights", {}).get("count", 0) or 0 for l in laeufe)
    total_gdelt   = sum(l["quellen"].get("gdelt",   {}).get("count", 0) or 0 for l in laeufe)
    total_rss     = sum(l["quellen"].get("rss",     {}).get("count", 0) or 0 for l in laeufe)
    total_firms   = sum(l["quellen"].get("firms",   {}).get("count", 0) or 0 for l in laeufe)
    total_seismik = sum(l["quellen"].get("seismik", {}).get("count", 0) or 0 for l in laeufe)

    verfuegbarkeit = _modul_verfuegbarkeit(laeufe)
    headlines      = _top_headlines(laeufe, 5)
    signale        = _aktive_signale(laeufe)

    # ── 5-Satz-Textbericht ────────────────────────────────────────────────────
    satz1 = (f"NEXUS-Kurzbericht für {ziel} | {jetzt} | "
             f"Zeitraum: {start_ts} – {end_ts} ({len(laeufe)} Läufe)")

    satz2 = (f"Eskalations-Score: {score_aktuell}/100 [{score_level}], "
             f"Trend {trend}. Durchschnitt {score_avg}, "
             f"Maximum {score_max}, Minimum {score_min}.")

    if signale:
        sig_str = ", ".join(signale[:4])
        satz3 = f"Persistente Signale: {sig_str}."
    else:
        satz3 = "Keine persistenten Eskalations-Signale in diesem Zeitraum."

    satz4_teile = []
    if total_flights:  satz4_teile.append(f"{total_flights} Flüge erfasst")
    if total_gdelt:    satz4_teile.append(f"{total_gdelt} GDELT-Artikel")
    if total_rss:      satz4_teile.append(f"{total_rss} RSS-Meldungen")
    if total_firms:    satz4_teile.append(f"{total_firms} Feuerpunkte (FIRMS)")
    if total_seismik:  satz4_teile.append(f"{total_seismik} Seismikereignisse")
    satz4 = ("Datenquellen: " + ", ".join(satz4_teile) + ".") if satz4_teile else ""

    # Module mit schlechter Verfügbarkeit
    probleme = [f"{m} ({v}%)" for m, v in verfuegbarkeit.items() if v < 50]
    if probleme:
        satz5 = f"Eingeschränkte Module: {', '.join(probleme)}."
    else:
        satz5 = "Alle Module liefen stabil."

    text_bericht = "\n".join(filter(None, [satz1, satz2, satz3, satz4, satz5]))

    if headlines:
        text_bericht += "\n\nTop-Schlagzeilen:\n"
        for i, h in enumerate(headlines, 1):
            text_bericht += f"  {i}. {h}\n"

    # ── HTML-Bericht ──────────────────────────────────────────────────────────
    level_colors = {
        "GRUEN": "#00c853", "GELB": "#ffd600", "ORANGE": "#ff6d00",
        "ROT": "#d32f2f", "KRITISCH": "#b71c1c",
    }
    level_color = level_colors.get(score_level, "#888")

    trend_color = "#00c853" if "fallend" in trend else ("#d32f2f" if "steigend" in trend else "#888")

    headlines_html = "".join(
        f'<li style="margin:4px 0;color:#ccc">{h[:120]}</li>' for h in headlines
    ) if headlines else "<li style='color:#666'>Keine Headlines verfügbar</li>"

    signale_html = " ".join(
        f'<span style="background:#1a3a5c;color:#4fc3f7;padding:3px 8px;border-radius:12px;font-size:12px">{s}</span>'
        for s in (signale[:6] if signale else ["keine"])
    )

    # Verfügbarkeits-Balken
    verf_html = ""
    for modul, pct in list(verfuegbarkeit.items())[:8]:
        bar_color = "#00c853" if pct >= 80 else ("#ffd600" if pct >= 50 else "#d32f2f")
        verf_html += f"""
        <div style="display:flex;align-items:center;margin:3px 0">
          <span style="width:90px;color:#aaa;font-size:12px">{modul}</span>
          <div style="background:#1a1a2e;width:120px;height:8px;border-radius:4px;overflow:hidden">
            <div style="background:{bar_color};width:{pct}%;height:100%"></div>
          </div>
          <span style="color:{bar_color};font-size:11px;margin-left:6px">{pct}%</span>
        </div>"""

    html_bericht = f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>NEXUS {stunden}h-Kurzbericht — {ziel}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #0a0a1a; color: #e0e0e0; font-family: 'Segoe UI', sans-serif;
            padding: 20px; max-width: 900px; margin: 0 auto; }}
    .header {{ background: linear-gradient(135deg, #0d1b2a, #1a2a4a);
               border: 1px solid #1e3a5f; border-radius: 12px; padding: 20px;
               margin-bottom: 20px; }}
    .title {{ font-size: 22px; font-weight: 700; color: #4fc3f7; }}
    .subtitle {{ color: #888; font-size: 13px; margin-top: 4px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }}
    .card {{ background: #0d1b2a; border: 1px solid #1e3a5f; border-radius: 10px; padding: 16px; }}
    .card-title {{ font-size: 11px; color: #4fc3f7; text-transform: uppercase;
                   letter-spacing: 1px; margin-bottom: 10px; }}
    .score-big {{ font-size: 48px; font-weight: 900; color: {level_color}; line-height: 1; }}
    .score-level {{ font-size: 16px; color: {level_color}; font-weight: 600; margin-top: 4px; }}
    .trend {{ font-size: 14px; color: {trend_color}; margin-top: 6px; }}
    .stat {{ display: flex; justify-content: space-between; margin: 6px 0;
             border-bottom: 1px solid #1a2a3a; padding-bottom: 4px; }}
    .stat-label {{ color: #888; font-size: 13px; }}
    .stat-value {{ color: #e0e0e0; font-weight: 600; font-size: 13px; }}
    .full-width {{ grid-column: 1 / -1; }}
    ul {{ list-style: none; padding: 0; }}
    .footer {{ color: #444; font-size: 11px; text-align: center; margin-top: 20px; }}
  </style>
</head>
<body>
  <div class="header">
    <div class="title">📡 NEXUS {stunden}h-Kurzbericht — {ziel}</div>
    <div class="subtitle">Erstellt: {jetzt} · Zeitraum: {start_ts} bis {end_ts} · {len(laeufe)} Läufe ausgewertet</div>
  </div>

  <div class="grid">
    <!-- Score Card -->
    <div class="card">
      <div class="card-title">⚡ Eskalations-Score</div>
      <div class="score-big">{score_aktuell}</div>
      <div class="score-level">{score_level}</div>
      <div class="trend">Trend: {trend}</div>
      <div style="margin-top:12px">
        <div class="stat"><span class="stat-label">Durchschnitt</span><span class="stat-value">{score_avg}/100</span></div>
        <div class="stat"><span class="stat-label">Maximum</span><span class="stat-value">{score_max}/100</span></div>
        <div class="stat"><span class="stat-label">Minimum</span><span class="stat-value">{score_min}/100</span></div>
      </div>
    </div>

    <!-- Daten-Statistiken -->
    <div class="card">
      <div class="card-title">📊 Gesammelte Daten</div>
      <div class="stat"><span class="stat-label">Flüge erfasst</span><span class="stat-value">{total_flights}</span></div>
      <div class="stat"><span class="stat-label">GDELT-Artikel</span><span class="stat-value">{total_gdelt}</span></div>
      <div class="stat"><span class="stat-label">RSS-Meldungen</span><span class="stat-value">{total_rss}</span></div>
      <div class="stat"><span class="stat-label">FIRMS Feuerpunkte</span><span class="stat-value">{total_firms}</span></div>
      <div class="stat"><span class="stat-label">Seismik-Events</span><span class="stat-value">{total_seismik}</span></div>
    </div>

    <!-- Aktive Signale -->
    <div class="card">
      <div class="card-title">🔴 Persistente Signale</div>
      <div style="margin-top:8px">{signale_html}</div>
    </div>

    <!-- Modul-Verfügbarkeit -->
    <div class="card">
      <div class="card-title">✅ Modul-Verfügbarkeit</div>
      {verf_html}
    </div>

    <!-- Top Headlines -->
    <div class="card full-width">
      <div class="card-title">📰 Top-Schlagzeilen</div>
      <ul>{headlines_html}</ul>
    </div>
  </div>

  <div class="footer">NEXUS Intelligence Platform · Automatisch generiert · {jetzt}</div>
</body>
</html>"""

    return {
        "text":         text_bericht,
        "html":         html_bericht,
        "stats": {
            "score_aktuell": score_aktuell,
            "score_avg":     score_avg,
            "score_max":     score_max,
            "score_min":     score_min,
            "level":         score_level,
            "trend":         trend,
            "laeufe":        len(laeufe),
            "signale":       signale,
        },
        "laeufe_count": len(laeufe),
    }


def speichere_brief_html(html: str, pfad: str = BRIEF_HTML):
    """Speichert HTML-Bericht."""
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    with open(pfad, "w", encoding="utf-8") as f:
        f.write(html)
    return pfad


def sende_email_brief(text: str, html: str, region: str, stunden: int):
    """Versucht Brief per Email zu senden (via nexus_daily.py Infrastruktur)."""
    try:
        from nexus_daily import sende_email  # type: ignore
        betreff = f"NEXUS {stunden}h-Kurzbericht: {region} — {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M UTC')}"
        sende_email(betreff, html, is_html=True)
        return True
    except Exception as e:
        print(f"[Brief] Email-Versand fehlgeschlagen: {e}", file=sys.stderr)
        return False


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NEXUS 6h-Kurzbericht")
    parser.add_argument("--stunden",  type=int,  default=6,    help="Zeitraum in Stunden (default: 6)")
    parser.add_argument("--region",   type=str,  default="",   help="Region filtern (z.B. Iran)")
    parser.add_argument("--email",    action="store_true",     help="Auch per Email senden")
    parser.add_argument("--nur-text", action="store_true",     help="Nur Text-Ausgabe, kein HTML")
    args = parser.parse_args()

    print(f"[NEXUS Brief] Generiere {args.stunden}h-Kurzbericht...", flush=True)
    bericht = generiere_brief(args.stunden, args.region)

    print("\n" + "═" * 60)
    print(bericht["text"])
    print("═" * 60)

    if not args.nur_text:
        pfad = speichere_brief_html(bericht["html"])
        print(f"\n[Brief] HTML gespeichert: {pfad}")
        # Versuche Browser zu öffnen
        try:
            import webbrowser
            webbrowser.open(f"file:///{pfad.replace(os.sep, '/')}")
        except Exception:
            pass

    if args.email:
        ok = sende_email_brief(bericht["text"], bericht["html"], args.region, args.stunden)
        print(f"[Brief] Email: {'✓ gesendet' if ok else '✗ fehlgeschlagen'}")

    print(f"\n[Brief] {bericht['laeufe_count']} Läufe ausgewertet.")
