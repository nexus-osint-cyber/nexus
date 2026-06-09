"""
NEXUS - PDF-Export Modul
Exportiert Tagesberichte und Lagebilder als druckfähige PDF-Dokumente.

Benötigt: pip install reportlab --break-system-packages

Nutzung:
  from nexus_pdf_export import export_daily_brief_pdf, export_report_pdf
  path = export_daily_brief_pdf(regions=["Ukraine", "Naher Osten"])
  path = export_report_pdf(topic="Ukraine", text=analyse_text, articles=articles)
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Ausgabe-Verzeichnis ───────────────────────────────────────────────────────
REPORT_DIR = Path(__file__).parent / "nexus_reports"

# ── ReportLab prüfen / installieren ──────────────────────────────────────────

def _ensure_reportlab() -> bool:
    try:
        from reportlab.lib.pagesizes import A4  # noqa: F401
        return True
    except ImportError:
        print("[NEXUS PDF] Installiere reportlab...", flush=True)
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "reportlab",
                 "--break-system-packages", "-q"],
                check=True
            )
            return True
        except Exception as e:
            print(f"[NEXUS PDF] Fehler: {e}", flush=True)
            return False


# ── Farben & Stile (NEXUS Dark Theme) ────────────────────────────────────────

def _styles():
    """Gibt ReportLab-Styles zurück (lazy import)."""
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

    base = getSampleStyleSheet()

    # Hintergrundfarben
    BG_DARK   = colors.HexColor("#0a0e14")
    BG_MED    = colors.HexColor("#111820")
    CYAN      = colors.HexColor("#00d4ff")
    GREEN     = colors.HexColor("#00ff88")
    RED       = colors.HexColor("#ff4444")
    ORANGE    = colors.HexColor("#ff8800")
    GRAY      = colors.HexColor("#8aa0b0")
    LIGHTGRAY = colors.HexColor("#c8d6e0")
    DARKGRAY  = colors.HexColor("#4a6070")

    styles = {
        "title": ParagraphStyle(
            "NexusTitle",
            fontSize=20, textColor=CYAN,
            fontName="Courier-Bold", spaceAfter=4,
            alignment=TA_LEFT, letterSpacing=3,
        ),
        "subtitle": ParagraphStyle(
            "NexusSub",
            fontSize=8, textColor=DARKGRAY,
            fontName="Courier", spaceAfter=10,
            alignment=TA_LEFT, letterSpacing=2,
        ),
        "region_header": ParagraphStyle(
            "NexusRegion",
            fontSize=11, textColor=CYAN,
            fontName="Courier-Bold", spaceAfter=4, spaceBefore=8,
            leftIndent=0, letterSpacing=2,
        ),
        "alert_ok": ParagraphStyle(
            "NexusAlertOK",
            fontSize=9, textColor=GREEN,
            fontName="Courier", spaceAfter=2,
        ),
        "alert_warn": ParagraphStyle(
            "NexusAlertWarn",
            fontSize=9, textColor=RED,
            fontName="Courier", spaceAfter=2,
        ),
        "meta": ParagraphStyle(
            "NexusMeta",
            fontSize=8, textColor=GRAY,
            fontName="Courier", spaceAfter=2,
        ),
        "article": ParagraphStyle(
            "NexusArt",
            fontSize=8, textColor=LIGHTGRAY,
            fontName="Courier", spaceAfter=2, leftIndent=10,
        ),
        "article_meta": ParagraphStyle(
            "NexusArtMeta",
            fontSize=7, textColor=DARKGRAY,
            fontName="Courier", spaceAfter=4, leftIndent=10,
        ),
        "section_text": ParagraphStyle(
            "NexusText",
            fontSize=9, textColor=LIGHTGRAY,
            fontName="Courier", spaceAfter=3, leading=12,
        ),
        "footer": ParagraphStyle(
            "NexusFooter",
            fontSize=7, textColor=DARKGRAY,
            fontName="Courier", alignment=TA_CENTER,
        ),
        "heading": ParagraphStyle(
            "NexusH",
            fontSize=10, textColor=CYAN,
            fontName="Courier-Bold", spaceAfter=4, spaceBefore=6,
        ),
        # Farben
        "BG_DARK": BG_DARK,
        "BG_MED": BG_MED,
        "CYAN": CYAN,
        "GREEN": GREEN,
        "RED": RED,
        "ORANGE": ORANGE,
        "GRAY": GRAY,
        "LIGHTGRAY": LIGHTGRAY,
        "DARKGRAY": DARKGRAY,
    }
    return styles


# ── Seiten-Template (Hintergrund + Rahmen) ────────────────────────────────────

def _page_template(canvas, doc, ts: str):
    """Zeichnet auf jeder Seite: dunkler Hintergrund + Header + Footer."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors

    W, H = A4
    S = _styles()

    # Dunkler Hintergrund
    canvas.saveState()
    canvas.setFillColor(S["BG_DARK"])
    canvas.rect(0, 0, W, H, fill=1, stroke=0)

    # Header-Balken
    canvas.setFillColor(colors.HexColor("#0a1a2e"))
    canvas.rect(0, H - 50, W, 50, fill=1, stroke=0)

    # Cyan-Linie unter Header
    canvas.setStrokeColor(S["CYAN"])
    canvas.setLineWidth(1)
    canvas.line(0, H - 50, W, H - 50)

    # NEXUS-Titel
    canvas.setFillColor(S["CYAN"])
    canvas.setFont("Courier-Bold", 14)
    canvas.drawString(20, H - 32, "◈ NEXUS OSINT")

    # Zeitstempel rechts
    canvas.setFillColor(S["GREEN"])
    canvas.setFont("Courier", 8)
    canvas.drawRightString(W - 20, H - 28, ts)

    # Seiten-Label links
    canvas.setFillColor(S["DARKGRAY"])
    canvas.setFont("Courier", 7)
    canvas.drawString(20, H - 44, "INTELLIGENCE LAGEBERICHT — NUR FÜR INFORMATORISCHE ZWECKE")

    # Footer-Linie
    canvas.setStrokeColor(colors.HexColor("#1e3a4a"))
    canvas.setLineWidth(0.5)
    canvas.line(20, 25, W - 20, 25)

    # Footer-Text
    canvas.setFillColor(S["DARKGRAY"])
    canvas.setFont("Courier", 7)
    canvas.drawCentredString(W / 2, 12, f"NEXUS v0.7  |  Seite {doc.page}")

    canvas.restoreState()


# ── Tagesbericht als PDF ──────────────────────────────────────────────────────

def export_daily_brief_pdf(
    regions: list[str] = None,
    auto_open: bool = True,
) -> str:
    """
    Exportiert den Tagesbericht als PDF.
    Gibt Dateipfad zurück.
    """
    if not _ensure_reportlab():
        raise RuntimeError("reportlab nicht installierbar")

    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, HRFlowable, PageBreak
    )
    from reportlab.lib import colors

    S = _styles()
    W, H = A4

    # Regionen laden
    if not regions:
        try:
            from nexus_memory import wl_list  # type: ignore
            wl = wl_list()
            regions = [e["term"] for e in wl] if wl else ["Naher Osten", "Ukraine"]
        except Exception:
            regions = ["Naher Osten", "Ukraine", "Hormuz-Strasse"]

    ts     = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    ts_fn  = datetime.now().strftime("%Y%m%d_%H%M")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = str(REPORT_DIR / f"nexus_daily_{ts_fn}.pdf")

    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        topMargin=60, bottomMargin=40,
        leftMargin=25, rightMargin=25,
    )

    story = []

    # ── Deckblatt-Bereich ──────────────────────────────────────────────────
    story.append(Spacer(1, 10))
    story.append(Paragraph("NEXUS TAGESBERICHT", S["title"]))
    story.append(Paragraph(f"AUTOMATISCHER OSINT-LAGEBERICHT  |  {ts}", S["subtitle"]))
    story.append(Paragraph(f"Analysierte Regionen: {len(regions)}", S["meta"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=S["CYAN"], spaceAfter=10))

    # ── Regionen ───────────────────────────────────────────────────────────
    total_alerts = 0

    for region in regions:
        try:
            from nexus_daily import _build_region_section  # type: ignore
            sec = _build_region_section(region)
        except Exception:
            sec = {"region": region, "flights": None, "weather": None,
                   "maritime": None, "seismic": [], "articles": [], "gdelt": []}

        fd      = sec.get("flights")
        wd      = sec.get("weather")
        md      = sec.get("maritime")
        seismic = sec.get("seismic") or []
        arts    = (sec.get("articles") or []) + (sec.get("gdelt") or [])

        # Alarm-Auswertung
        alert_lines = []
        if fd and fd.get("suspicious"):
            n = len(fd["suspicious"])
            total_alerts += n
            alert_lines.append(f"✈ {n} auffällige Flugzeuge")
        if md and md.get("alert_count", 0) > 0:
            n = md["alert_count"]
            total_alerts += n
            alert_lines.append(f"⚓ {n} Maritime-Alarme")
        big_quakes = [q for q in seismic if q.get("mag", 0) >= 4.5]
        if big_quakes:
            alert_lines.append(f"🌍 {len(big_quakes)} Erdbeben >=M4.5")

        # Region-Header
        story.append(Spacer(1, 6))
        story.append(Paragraph(f"▶ {region.upper()}", S["region_header"]))

        if alert_lines:
            story.append(Paragraph("⚠ " + " | ".join(alert_lines), S["alert_warn"]))
        else:
            story.append(Paragraph("✅ Keine kritischen Alarme", S["alert_ok"]))

        # Wetter
        if wd:
            story.append(Paragraph(
                f"⛅ Wetter: {wd.get('weather_desc','?')} | "
                f"{wd.get('temperature_c','?')}°C | "
                f"{wd.get('wind_kmh','?')} km/h Wind",
                S["meta"]
            ))

        # Flugdaten
        if fd:
            susp = len(fd.get("suspicious", []))
            story.append(Paragraph(
                f"✈ Flugdaten: {fd.get('total',0)} Transponder | "
                f"{fd.get('airborne',0)} airborne | {susp} auffällig",
                S["meta"]
            ))
            # Auffällige Flieger auflisten
            for ac in (fd.get("suspicious") or [])[:3]:
                story.append(Paragraph(
                    f"  ⚠ {ac.get('callsign','?')} — {(ac.get('suspicious') or ac.get('osint',''))[:70]}",
                    S["article_meta"]
                ))

        # Erdbeben
        for q in big_quakes[:3]:
            story.append(Paragraph(
                f"🌍 M{q.get('mag','?')} | {q.get('place','')[:60]}"
                + (f" | {q.get('osint_hint','')[:40]}" if q.get("osint_hint") else ""),
                S["meta"]
            ))

        # Top-Artikel
        if arts:
            story.append(Paragraph("Aktuelle Meldungen:", S["meta"]))
            for a in sorted(arts, key=lambda x: x.get("age_min", 9999))[:5]:
                title = (a.get("title") or "")[:90]
                source = a.get("source", "")
                age = a.get("age_min", 9999)
                age_s = f"{age}min" if age < 120 else f"{age//60}h"
                story.append(Paragraph(f"• {title}", S["article"]))
                story.append(Paragraph(f"  {source} · vor {age_s}", S["article_meta"]))

        story.append(HRFlowable(
            width="100%", thickness=0.3,
            color=colors.HexColor("#1e3a4a"), spaceAfter=4
        ))

    # ── Zusammenfassung ────────────────────────────────────────────────────
    story.append(Spacer(1, 10))
    status_text = f"{total_alerts} aktive Alarme" if total_alerts else "Keine kritischen Alarme"
    status_style = S["alert_warn"] if total_alerts else S["alert_ok"]
    story.append(HRFlowable(width="100%", thickness=0.5, color=S["CYAN"], spaceBefore=6, spaceAfter=6))
    story.append(Paragraph(f"GESAMTLAGE: {status_text}", status_style))
    story.append(Paragraph(f"Erstellt: {ts}  |  NEXUS OSINT v0.7", S["footer"]))

    # ── PDF bauen ──────────────────────────────────────────────────────────
    def _on_page(canvas, doc, _ts=ts):
        _page_template(canvas, doc, _ts)

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)

    if auto_open:
        try:
            os.startfile(out_path)
        except Exception:
            try:
                subprocess.Popen(["start", "", out_path], shell=True)
            except Exception:
                pass

    return out_path


# ── Lagebild-Report als PDF ───────────────────────────────────────────────────

def export_report_pdf(
    topic: str,
    analysis_text: str,
    articles: list = None,
    flight_data: dict = None,
    weather_data: dict = None,
    maritime_data: dict = None,
    correlation_alerts: list = None,
    auto_open: bool = True,
    save_dir: str = None,
) -> str:
    """
    Exportiert ein NEXUS-Lagebild als PDF.
    Kann direkt nach generate_report() aufgerufen werden.
    Gibt Dateipfad zurück.
    """
    if not _ensure_reportlab():
        raise RuntimeError("reportlab nicht installierbar")

    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    )
    from reportlab.lib import colors

    S  = _styles()
    ts = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    ts_fn = datetime.now().strftime("%Y%m%d_%H%M")

    save_to = Path(save_dir) if save_dir else REPORT_DIR
    save_to.mkdir(parents=True, exist_ok=True)
    out_path = str(save_to / f"nexus_report_{topic[:20].replace(' ','_')}_{ts_fn}.pdf")

    doc = SimpleDocTemplate(
        out_path, pagesize=A4,
        topMargin=60, bottomMargin=40,
        leftMargin=25, rightMargin=25,
    )

    story = []

    # ── Titel ──────────────────────────────────────────────────────────────
    story.append(Spacer(1, 10))
    story.append(Paragraph(f"LAGEBILD: {topic.upper()}", S["title"]))
    story.append(Paragraph(f"{ts}  |  NEXUS OSINT-ANALYSE", S["subtitle"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=S["CYAN"], spaceAfter=8))

    # ── Korrelations-Alerts (wenn vorhanden) ──────────────────────────────
    if correlation_alerts:
        high = [a for a in correlation_alerts if a.get("confidence") == "HOCH"]
        if high:
            story.append(Paragraph("⚡ KORRELIERTE EREIGNISSE", S["heading"]))
            for h in high[:3]:
                story.append(Paragraph(
                    f"⚡ {h.get('title','')[:80]}",
                    S["alert_warn"]
                ))
                story.append(Paragraph(
                    f"   {h.get('n_sources',0)} Quellen | {', '.join(h.get('source_types',[]))}",
                    S["article_meta"]
                ))
            story.append(Spacer(1, 6))

    # ── Flugdaten ──────────────────────────────────────────────────────────
    if flight_data and "error" not in flight_data:
        story.append(Paragraph("✈ FLUGDATEN", S["heading"]))
        story.append(Paragraph(
            f"Region: {flight_data.get('region','?')} | "
            f"Total: {flight_data.get('total',0)} | "
            f"Airborne: {flight_data.get('airborne',0)} | "
            f"Auffällig: {len(flight_data.get('suspicious',[]))}",
            S["meta"]
        ))
        for ac in (flight_data.get("suspicious") or [])[:4]:
            story.append(Paragraph(
                f"  ⚠ {ac.get('callsign','?')} "
                f"({ac.get('origin','?')}) — "
                f"{(ac.get('suspicious') or ac.get('osint',''))[:60]}",
                S["article_meta"]
            ))
        story.append(Spacer(1, 4))

    # ── Wetter ────────────────────────────────────────────────────────────
    if weather_data and "error" not in weather_data:
        ops = weather_data.get("ops", {})
        story.append(Paragraph("⛅ WETTER & OPERATIVE BEDINGUNGEN", S["heading"]))
        story.append(Paragraph(
            f"Standort: {weather_data.get('location','?')} | "
            f"{weather_data.get('weather_desc','?')} | "
            f"{weather_data.get('temperature_c','?')}°C | "
            f"{weather_data.get('wind_kmh','?')} km/h",
            S["meta"]
        ))
        overall = ops.get("overall", "gruen")
        op_color = S["alert_warn"] if overall == "rot" else (
            S["alert_ok"] if overall == "gruen" else S["meta"]
        )
        story.append(Paragraph(
            f"Operative Bewertung: {overall.upper()}",
            op_color
        ))
        story.append(Spacer(1, 4))

    # ── Maritime ──────────────────────────────────────────────────────────
    if maritime_data and "error" not in maritime_data:
        story.append(Paragraph("⚓ MARITIME LAGE", S["heading"]))
        story.append(Paragraph(
            f"Region: {maritime_data.get('region','?')} | "
            f"Alerts: {maritime_data.get('alert_count',0)}",
            S["meta"]
        ))
        for al in (maritime_data.get("alerts") or [])[:3]:
            story.append(Paragraph(
                f"  • {(al.get('title',''))[:80]}",
                S["article"]
            ))
        story.append(Spacer(1, 4))

    # ── Analyse-Text ──────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.3, color=S["CYAN"], spaceAfter=6))
    story.append(Paragraph("NEXUS ANALYSE", S["heading"]))

    # Text in Absätze aufteilen (max 500 Zeichen pro Paragraf für sauberes Layout)
    analysis_clean = (analysis_text or "").replace("[HTML-Report:", "").strip()
    for para in analysis_clean.split("\n"):
        para = para.strip()
        if not para:
            story.append(Spacer(1, 3))
            continue
        # Sonderzeichen escapen für ReportLab
        para_safe = para.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        story.append(Paragraph(para_safe[:300], S["section_text"]))

    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=0.3, color=S["CYAN"], spaceAfter=6))

    # ── Nachrichten-Quellen ────────────────────────────────────────────────
    if articles:
        story.append(Paragraph(f"QUELLEN ({len(articles)} Artikel)", S["heading"]))
        for a in sorted(articles, key=lambda x: x.get("age_min", 9999))[:15]:
            title  = (a.get("title") or "")[:90]
            source = a.get("source", "?")
            age    = a.get("age_min", 9999)
            cred   = a.get("credibility_score")
            age_s  = f"{age}min" if age < 120 else f"{age//60}h"
            cred_s = f" | ★{cred}/10" if cred else ""
            # Übersetzungs-Hinweis
            if a.get("translated"):
                orig = (a.get("title_original") or "")[:60]
                story.append(Paragraph(
                    f"• {title}",
                    S["article"]
                ))
                story.append(Paragraph(
                    f"  [übersetzt aus {a.get('lang','?').upper()}] Orig: {orig}",
                    S["article_meta"]
                ))
            else:
                story.append(Paragraph(f"• {title}", S["article"]))
            story.append(Paragraph(
                f"  {source} · vor {age_s}{cred_s}",
                S["article_meta"]
            ))

    # ── Footer ─────────────────────────────────────────────────────────────
    story.append(Spacer(1, 12))
    story.append(Paragraph(
        f"NEXUS OSINT v0.7  |  {ts}  |  Nur für informatorische Zwecke",
        S["footer"]
    ))

    # ── PDF bauen ──────────────────────────────────────────────────────────
    def _on_page(canvas, doc, _ts=ts):
        _page_template(canvas, doc, _ts)

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)

    if auto_open:
        try:
            os.startfile(out_path)
        except Exception:
            try:
                subprocess.Popen(["start", "", out_path], shell=True)
            except Exception:
                pass

    return out_path


# ── Direktaufruf zum Testen ───────────────────────────────────────────────────
if __name__ == "__main__":
    print("NEXUS PDF-Export Test")
    print("─" * 40)

    # Test: Lagebild-PDF
    path = export_report_pdf(
        topic="Ukraine",
        analysis_text=(
            "Aktuelle Lagebewertung Ukraine:\n\n"
            "Die Gefechtslage im Donbas bleibt angespannt. Mehrere Quellen "
            "berichten von intensivem Artilleriebeschuss im Raum Bachmut. "
            "Satellitendaten zeigen erhöhte Thermalsignaturen in der Region.\n\n"
            "Maritime Lage im Schwarzen Meer: Keine ungewöhnlichen Aktivitäten "
            "gemeldet. Odessa-Korridor operativ.\n\n"
            "Luftlage: 3 auffällige Transponder erkannt, darunter ein Flugzeug "
            "ohne aktiven ADSB-Transponder über Charkiw."
        ),
        articles=[
            {"title": "Angriff auf Charkiw – Berichte über Explosionen",
             "source": "Telegram/Ukraine_News", "age_min": 45,
             "credibility_score": 6, "url": "#"},
            {"title": "Ukraine war update: Heavy fighting near Bakhmut",
             "source": "BBC", "age_min": 120, "credibility_score": 9, "url": "#"},
        ],
        auto_open=True,
    )
    print(f"✅ PDF erstellt: {path}")
