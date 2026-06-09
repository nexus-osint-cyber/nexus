"""
nexus_sitrep.py — Automatischer NATO-Style Situation Report Generator
NEXUS Intelligence Brief: BLUF + Key Developments + Threat Assessment + Intel Gaps

Format orientiert an NATO INTSUM / US Army SITREP-Struktur.
Vollständig offline. Kein API-Key benötigt.
"""

from __future__ import annotations
import datetime
import os
from typing import Optional

# ── Hilfsfunktionen ──────────────────────────────────────────────────────────

def _utc_now() -> str:
    return datetime.datetime.utcnow().strftime("%d%H%MZ %b %Y").upper()

def _classification_banner(level: str = "UNCLASSIFIED") -> str:
    return f"//NEXUS//{level}//NOFORN//"

def _trend_arrow(scores: list) -> str:
    if len(scores) < 2:
        return "→ STABIL"
    delta = scores[-1] - scores[-2]
    if delta > 5:
        return "↑ ESKALIEREND"
    elif delta < -5:
        return "↓ DE-ESKALIEREND"
    return "→ STABIL"

def _confidence_label(score: int) -> str:
    if score >= 75:
        return "HOCH"
    elif score >= 50:
        return "MITTEL"
    elif score >= 25:
        return "NIEDRIG"
    return "UNZUREICHEND"

def _level_color(level: str) -> str:
    colors = {
        "KRITISCH": "#ff2222",
        "HOCH":     "#ff8800",
        "ERHÖHT":   "#ffdd00",
        "MITTEL":   "#88cc00",
        "GRUEN":    "#44bb44",
        "NIEDRIG":  "#44bb44",
    }
    return colors.get(level.upper(), "#aaaaaa")

# ── Kern-Generierung ─────────────────────────────────────────────────────────

def generate_sitrep(
    pipeline_result: dict,
    query: str,
    llm_context: str = "",
    classification: str = "UNCLASSIFIED",
) -> dict:
    """
    Erstellt einen vollständigen NEXUS Intelligence Brief aus dem Pipeline-Ergebnis.
    pipeline_result: dict wie von nexus_live_server.py /analyze zurückgegeben.
    Gibt strukturierten dict zurück, der in text/html gerendert werden kann.
    """
    now = _utc_now()
    articles     = pipeline_result.get("articles", [])
    esc          = pipeline_result.get("escalation", {})
    predict      = pipeline_result.get("prediction", {})
    flights      = pipeline_result.get("flights", {})
    maritime     = pipeline_result.get("maritime", {})
    seismic      = pipeline_result.get("seismic", [])
    firms        = pipeline_result.get("firms", [])
    acled        = pipeline_result.get("acled", [])
    netprop      = pipeline_result.get("netprop", {})
    whois        = pipeline_result.get("whois", {})
    knowledge    = pipeline_result.get("knowledge_map", {})
    displacement = pipeline_result.get("displacement", [])
    health       = pipeline_result.get("health_alerts", [])
    economics    = pipeline_result.get("economics", {})
    lightning    = pipeline_result.get("lightning", [])
    gpsjam       = pipeline_result.get("gpsjam", [])
    bgp          = pipeline_result.get("bgp_anomalies", [])
    sanctions    = pipeline_result.get("sanctions_hits", [])
    timeline_ctx = pipeline_result.get("timeline_context", "")

    # ── 1. Eskalations-Score + Level ─────────────────────────────────────────
    esc_score  = esc.get("score", 0) if isinstance(esc, dict) else 0
    esc_level  = esc.get("level", "UNBEKANNT") if isinstance(esc, dict) else "UNBEKANNT"
    esc_signals = esc.get("signal_details", []) if isinstance(esc, dict) else []

    # Trendberechnung aus Predict-History
    predict_history = predict.get("score_history", []) if isinstance(predict, dict) else []
    trend = _trend_arrow(predict_history)

    # ── 2. Top-Ereignisse mit Konfidenz ──────────────────────────────────────
    canonical = [a for a in articles if a.get("is_canonical", True)]
    top_events = sorted(
        canonical,
        key=lambda a: (a.get("cluster_size", 1), -a.get("age_min", 9999)),
        reverse=True,
    )[:8]

    key_developments = []
    for ev in top_events:
        conf = ev.get("confidence", "EINZELMELDUNG")
        cred = ev.get("credibility_label", "")
        cluster = ev.get("cluster_size", 1)
        key_developments.append({
            "title":       ev.get("title", "")[:120],
            "source":      ev.get("source", ""),
            "date":        ev.get("date", ""),
            "confidence":  conf,
            "credibility": cred,
            "cluster":     cluster,
            "url":         ev.get("url", "#"),
        })

    # ── 3. SIGINT/MASINT-Zusammenfassung ─────────────────────────────────────
    flight_count    = len(flights.get("aircraft", [])) if isinstance(flights, dict) else 0
    isr_count       = len([f for f in flights.get("aircraft", [])
                            if f.get("is_isr") or f.get("callsign_type") == "ISR"]) \
                      if isinstance(flights, dict) else 0
    ship_count      = len(maritime.get("ships", [])) if isinstance(maritime, dict) else 0
    seismic_count   = len(seismic) if isinstance(seismic, list) else 0
    firms_count     = len(firms)   if isinstance(firms, list) else 0
    acled_count     = len(acled)   if isinstance(acled, list) else 0
    lightning_count = len(lightning) if isinstance(lightning, list) else 0
    gpsjam_count    = len(gpsjam)    if isinstance(gpsjam, list) else 0
    bgp_count       = len(bgp)       if isinstance(bgp, list) else 0

    sigint = {
        "flights":    flight_count,
        "isr":        isr_count,
        "ships":      ship_count,
        "seismic":    seismic_count,
        "firms":      firms_count,
        "acled":      acled_count,
        "lightning":  lightning_count,
        "gpsjam":     gpsjam_count,
        "bgp":        bgp_count,
    }

    # ── 4. Disinfo-Bewertung ──────────────────────────────────────────────────
    coord_alerts   = netprop.get("coordination_alerts", []) if isinstance(netprop, dict) else []
    state_amps     = netprop.get("state_amplification_events", []) if isinstance(netprop, dict) else []
    disinfo_count  = whois.get("disinfo_count", 0) if isinstance(whois, dict) else 0

    disinfo = {
        "coordination_alerts":  len(coord_alerts),
        "state_amplification":  len(state_amps),
        "high_risk_domains":    disinfo_count,
        "top_alerts":           coord_alerts[:3],
    }

    # ── 5. Quellenqualität ────────────────────────────────────────────────────
    total = len(articles)
    bestaetigt = sum(1 for a in articles if a.get("confidence") == "BESTÄTIGT")
    wahrscheinlich = sum(1 for a in articles if a.get("confidence") == "WAHRSCHEINLICH")
    einzelmeldung  = sum(1 for a in articles if a.get("confidence") == "EINZELMELDUNG")
    unbestaetigt   = sum(1 for a in articles if a.get("confidence") == "UNBESTÄTIGT")

    source_quality = {
        "total":         total,
        "bestaetigt":    bestaetigt,
        "wahrscheinlich":wahrscheinlich,
        "einzelmeldung": einzelmeldung,
        "unbestaetigt":  unbestaetigt,
        "confidence_pct": round((bestaetigt + wahrscheinlich) / max(total, 1) * 100),
    }

    # ── 6. Intelligence Gaps ─────────────────────────────────────────────────
    gaps = []
    if flight_count == 0:
        gaps.append("Keine Flugdaten verfügbar — ADS-B/OpenSky nicht erreichbar")
    if ship_count == 0:
        gaps.append("Keine AIS-Schiffsdaten — AISSTREAM_KEY fehlt oder API-Limit")
    if acled_count == 0:
        gaps.append("Kein ACLED-Datenfeed — ACLED_EMAIL/ACLED_PASSWORD prüfen")
    if seismic_count == 0:
        gaps.append("Keine seismischen Daten — USGS-API nicht erreichbar")
    if total < 5:
        gaps.append("Sehr wenige Artikel (<5) — Quellenabdeckung unzureichend")
    if disinfo_count > 3:
        gaps.append(f"WARNUNG: {disinfo_count} Quellen mit Disinfo-Risiko — Verifizierung erforderlich")
    if not gaps:
        gaps.append("Alle Hauptquellen verfügbar — Quellenabdeckung ausreichend")

    # ── 7. 48h-Prognose ───────────────────────────────────────────────────────
    forecast_48h = ""
    if isinstance(predict, dict):
        trend_label = predict.get("trend", "")
        scenarios   = predict.get("scenarios", [])
        if scenarios:
            top_sc = scenarios[0]
            forecast_48h = "{} (P={:.0%}) — {}".format(
                top_sc.get("label", ""),
                top_sc.get("probability", 0),
                top_sc.get("description", ""),
            )
        elif trend_label:
            forecast_48h = trend_label

    # ── 8. Humanitäre Lage ────────────────────────────────────────────────────
    displaced = sum(d.get("total_displaced", 0) for d in displacement
                    if isinstance(d, dict)) if displacement else 0
    health_count = len(health) if isinstance(health, list) else 0

    # ── 9. Wirtschafts-Signale ────────────────────────────────────────────────
    econ_signals = []
    if isinstance(economics, dict):
        for k, v in economics.items():
            if isinstance(v, dict) and v.get("alert"):
                econ_signals.append(v.get("alert"))
    econ_signals = econ_signals[:3]

    # ── Sanktionen ────────────────────────────────────────────────────────────
    sanctions_count = len(sanctions) if isinstance(sanctions, list) else 0

    # ── BLUF (Bottom Line Up Front) ───────────────────────────────────────────
    bluf_parts = []
    bluf_parts.append("Eskalationslevel {}: {}".format(esc_level, trend))
    if acled_count > 0:
        bluf_parts.append("{} Konfliktereignisse via ACLED".format(acled_count))
    if isr_count > 0:
        bluf_parts.append("{} ISR-Flüge detektiert".format(isr_count))
    if disinfo.get("coordination_alerts", 0) > 0:
        bluf_parts.append("KOORDINIERTE PROPAGANDA DETEKTIERT")
    if sanctions_count > 0:
        bluf_parts.append("{} Sanktions-Treffer".format(sanctions_count))
    bluf = " | ".join(bluf_parts) if bluf_parts else "Keine signifikanten Ereignisse."

    return {
        "classification":  classification,
        "dtg":             now,
        "region":          query,
        "bluf":            bluf,
        "esc_score":       esc_score,
        "esc_level":       esc_level,
        "esc_signals":     esc_signals[:5],
        "trend":           trend,
        "key_developments":key_developments,
        "sigint":          sigint,
        "disinfo":         disinfo,
        "source_quality":  source_quality,
        "gaps":            gaps,
        "forecast_48h":    forecast_48h,
        "displaced":       displaced,
        "health_alerts":   health_count,
        "econ_signals":    econ_signals,
        "sanctions_hits":  sanctions_count,
        "timeline_context":timeline_ctx[:500] if timeline_ctx else "",
        "articles_total":  total,
    }


# ── Text-Renderer ─────────────────────────────────────────────────────────────

def sitrep_to_text(sitrep: dict) -> str:
    """Rendert den SitRep als lesbaren ASCII-Text (Terminal / Log)."""
    lines = []
    sep  = "=" * 60
    sep2 = "-" * 60

    lines += [
        "",
        sep,
        "  NEXUS INTELLIGENCE BRIEF",
        "  {}".format(sitrep["classification"]),
        "  DTG: {}".format(sitrep["dtg"]),
        "  REGION: {}".format(sitrep["region"].upper()),
        sep,
        "",
        "BLUF: {}".format(sitrep["bluf"]),
        "",
        sep2,
        "1. ESKALATIONS-BEWERTUNG",
        sep2,
        "   Score:  {:>3}/100  Level: {}  Trend: {}".format(
            sitrep["esc_score"], sitrep["esc_level"], sitrep["trend"]),
    ]
    for s in sitrep.get("esc_signals", []):
        if isinstance(s, dict):
            lines.append("   [+{:>3}] {}".format(s.get("delta", 0), s.get("label", str(s))))
        else:
            lines.append("   - {}".format(s))

    lines += [
        "",
        sep2,
        "2. KEY DEVELOPMENTS ({} Artikel, {}% verifiziert)".format(
            sitrep["articles_total"],
            sitrep["source_quality"]["confidence_pct"]),
        sep2,
    ]
    for i, ev in enumerate(sitrep.get("key_developments", []), 1):
        conf_sym = {"BESTÄTIGT": "✓✓", "WAHRSCHEINLICH": "✓", "EINZELMELDUNG": "?", "UNBESTÄTIGT": "✗"}.get(
            ev.get("confidence", ""), "?")
        lines.append("  {}. [{}] {}".format(i, conf_sym, ev["title"]))
        lines.append("     Quelle: {} | {}  | Cluster: {}x bestätigt".format(
            ev["source"][:30], ev["date"][:16], ev["cluster"]))

    lines += [
        "",
        sep2,
        "3. SIGINT / MASINT",
        sep2,
        "   Flüge: {}  (ISR: {})  |  Schiffe: {}  |  ACLED: {}".format(
            sitrep["sigint"]["flights"], sitrep["sigint"]["isr"],
            sitrep["sigint"]["ships"], sitrep["sigint"]["acled"]),
        "   Seismik: {}  |  FIRMS-Brände: {}  |  Blitze: {}".format(
            sitrep["sigint"]["seismic"], sitrep["sigint"]["firms"],
            sitrep["sigint"]["lightning"]),
        "   GPS-Jammer: {}  |  BGP-Anomalien: {}".format(
            sitrep["sigint"]["gpsjam"], sitrep["sigint"]["bgp"]),
    ]

    lines += [
        "",
        sep2,
        "4. INFORMATIONS-UMFELD / DISINFO",
        sep2,
        "   Koordinations-Alarme:  {}".format(sitrep["disinfo"]["coordination_alerts"]),
        "   Staatliche Amplifikat: {}".format(sitrep["disinfo"]["state_amplification"]),
        "   Risiko-Domains (WHOIS):{}".format(sitrep["disinfo"]["high_risk_domains"]),
    ]
    for a in sitrep["disinfo"].get("top_alerts", []):
        if isinstance(a, dict):
            lines.append("   ⚠ [{}] {}  Score: {:.0%}".format(
                a.get("verdict", ""), a.get("topic", "")[:50], a.get("score", 0)))

    lines += [
        "",
        sep2,
        "5. QUELLEN-QUALITÄT",
        sep2,
        "   Bestätigt (3+ Quellen): {}".format(sitrep["source_quality"]["bestaetigt"]),
        "   Wahrscheinlich (2 Q.):  {}".format(sitrep["source_quality"]["wahrscheinlich"]),
        "   Einzelmeldung:          {}".format(sitrep["source_quality"]["einzelmeldung"]),
        "   Unbestätigt:            {}".format(sitrep["source_quality"]["unbestaetigt"]),
    ]

    lines += [
        "",
        sep2,
        "6. INTELLIGENCE GAPS",
        sep2,
    ]
    for g in sitrep.get("gaps", []):
        lines.append("   ⚠ {}".format(g))

    if sitrep.get("forecast_48h"):
        lines += ["", sep2, "7. 48h-PROGNOSE", sep2,
                  "   {}".format(sitrep["forecast_48h"])]

    if sitrep.get("econ_signals"):
        lines += ["", sep2, "8. WIRTSCHAFTS-SIGNALE", sep2]
        for e in sitrep["econ_signals"]:
            lines.append("   - {}".format(e))

    lines += [
        "",
        sep,
        "  {}  //  NEXUS-OSINT  //  {}".format(
            sitrep["classification"], sitrep["dtg"]),
        sep,
        "",
    ]
    return "\n".join(lines)


# ── HTML-Renderer ─────────────────────────────────────────────────────────────

def sitrep_to_html(sitrep: dict, save_path: Optional[str] = None) -> str:
    """Rendert den SitRep als Dark-Mode HTML-Seite."""
    esc_col = _level_color(sitrep["esc_level"])
    sq      = sitrep["source_quality"]
    sig     = sitrep["sigint"]
    dis     = sitrep["disinfo"]

    # Konfidenz-Balken (5 Segmente = 100%)
    total_art = max(sitrep["articles_total"], 1)
    pct_best  = round(sq["bestaetigt"]    / total_art * 100)
    pct_wahr  = round(sq["wahrscheinlich"]/ total_art * 100)
    pct_einz  = round(sq["einzelmeldung"] / total_art * 100)
    pct_unb   = round(sq["unbestaetigt"]  / total_art * 100)

    dev_rows = ""
    conf_icons = {"BESTÄTIGT": "✓✓", "WAHRSCHEINLICH": "✓",
                  "EINZELMELDUNG": "?", "UNBESTÄTIGT": "✗"}
    conf_colors= {"BESTÄTIGT": "#44bb44", "WAHRSCHEINLICH": "#88cc00",
                  "EINZELMELDUNG": "#ffdd00", "UNBESTÄTIGT": "#ff4444"}
    for ev in sitrep.get("key_developments", []):
        c = ev.get("confidence", "EINZELMELDUNG")
        col = conf_colors.get(c, "#888")
        ico = conf_icons.get(c, "?")
        dev_rows += (
            '<tr><td style="color:{col};font-weight:bold">{ico}</td>'
            '<td><a href="{url}" target="_blank" style="color:#cce">{title}</a></td>'
            '<td style="color:#999;font-size:11px">{src}</td>'
            '<td style="color:#aaa;font-size:11px">{date}</td>'
            '<td style="color:#88cc00">{clust}x</td></tr>\n'
        ).format(col=col, ico=ico, title=ev["title"][:90],
                 url=ev["url"], src=ev["source"][:25],
                 date=ev["date"][:16], clust=ev["cluster"])

    gap_rows = "".join(
        '<li style="color:#ffaa44">⚠ {}</li>'.format(g)
        for g in sitrep.get("gaps", [])
    )

    disinfo_rows = ""
    for a in dis.get("top_alerts", []):
        if isinstance(a, dict):
            disinfo_rows += (
                '<li><span style="color:#ff6644">[{}]</span> {} '
                '<span style="color:#ffaa00">({:.0%})</span></li>'
            ).format(a.get("verdict",""), a.get("topic","")[:60], a.get("score",0))

    html = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NEXUS SitRep — {region}</title>
<style>
  body{{background:#0d0d0d;color:#ccc;font-family:'Courier New',monospace;
        margin:0;padding:20px;line-height:1.5}}
  .banner{{background:#1a1a2e;border:1px solid #333;padding:12px 20px;
           margin-bottom:16px;display:flex;justify-content:space-between;align-items:center}}
  .classif{{color:#ff8800;font-size:11px;font-weight:bold;letter-spacing:2px}}
  .dtg{{color:#888;font-size:11px}}
  h1{{color:#4af;font-size:18px;margin:0}}
  .region{{color:#88f;font-size:14px}}
  .bluf{{background:#111;border-left:4px solid #4af;padding:10px 16px;
         margin:12px 0;font-size:14px;color:#eee}}
  .section{{margin:16px 0}}
  .section h2{{font-size:13px;color:#4af;border-bottom:1px solid #333;
               padding-bottom:4px;margin-bottom:8px;letter-spacing:1px}}
  .esc-box{{display:inline-block;padding:6px 16px;border:2px solid {esc_col};
            color:{esc_col};font-size:20px;font-weight:bold;margin-right:12px}}
  .score-bar{{display:flex;height:16px;width:100%;max-width:400px;
              border:1px solid #333;overflow:hidden;border-radius:3px}}
  .sigint-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;
                max-width:600px}}
  .sig-cell{{background:#111;border:1px solid #333;padding:8px;text-align:center}}
  .sig-val{{font-size:24px;color:#4af;font-weight:bold}}
  .sig-lbl{{font-size:10px;color:#666}}
  table{{width:100%;border-collapse:collapse;font-size:12px}}
  th{{background:#1a1a1a;color:#888;text-align:left;padding:4px 8px;
      border-bottom:1px solid #333}}
  td{{padding:4px 8px;border-bottom:1px solid #1a1a1a}}
  ul{{margin:4px 0;padding-left:20px}}
  .footer{{color:#555;font-size:10px;text-align:center;margin-top:24px;
           border-top:1px solid #222;padding-top:8px}}
</style>
</head>
<body>

<div class="banner">
  <div>
    <div class="classif">{classif}</div>
    <h1>NEXUS INTELLIGENCE BRIEF</h1>
    <div class="region">AOR: {region}</div>
  </div>
  <div class="dtg">DTG: {dtg}</div>
</div>

<div class="bluf"><strong>BLUF:</strong> {bluf}</div>

<div class="section">
  <h2>1. ESKALATIONS-BEWERTUNG</h2>
  <span class="esc-box">{esc_level}</span>
  <span style="font-size:28px;font-weight:bold;color:{esc_col}">{esc_score}/100</span>
  &nbsp; <span style="color:#aaa">{trend}</span>
  <div style="margin-top:8px">
    {esc_signals_html}
  </div>
</div>

<div class="section">
  <h2>2. KEY DEVELOPMENTS ({total_art} Artikel &mdash; {conf_pct}% verifiziert)</h2>
  <div class="score-bar">
    <div style="width:{pct_best}%;background:#44bb44" title="Bestätigt"></div>
    <div style="width:{pct_wahr}%;background:#88cc00" title="Wahrscheinlich"></div>
    <div style="width:{pct_einz}%;background:#ffdd00" title="Einzelmeldung"></div>
    <div style="width:{pct_unb}%;background:#ff4444" title="Unbestätigt"></div>
  </div>
  <div style="font-size:10px;color:#666;margin-top:2px">
    &#9646; Bestätigt ({bestaetigt}) &nbsp; &#9646; Wahrscheinlich ({wahrscheinlich})
    &nbsp; &#9646; Einzel ({einzelmeldung}) &nbsp; &#9646; Unbestätigt ({unbestaetigt})
  </div>
  <table style="margin-top:8px">
    <tr><th>Konf.</th><th>Schlagzeile</th><th>Quelle</th><th>Zeit</th><th>Belege</th></tr>
    {dev_rows}
  </table>
</div>

<div class="section">
  <h2>3. SIGINT / MASINT</h2>
  <div class="sigint-grid">
    <div class="sig-cell"><div class="sig-val">{fl}</div><div class="sig-lbl">FLÜGE (ISR: {isr})</div></div>
    <div class="sig-cell"><div class="sig-val">{sh}</div><div class="sig-lbl">SCHIFFE (AIS)</div></div>
    <div class="sig-cell"><div class="sig-val">{ac}</div><div class="sig-lbl">ACLED EVENTS</div></div>
    <div class="sig-cell"><div class="sig-val">{se}</div><div class="sig-lbl">SEISMIK</div></div>
    <div class="sig-cell"><div class="sig-val">{fi}</div><div class="sig-lbl">FIRMS BRÄNDE</div></div>
    <div class="sig-cell"><div class="sig-val">{li}</div><div class="sig-lbl">BLITZE</div></div>
    <div class="sig-cell"><div class="sig-val">{gj}</div><div class="sig-lbl">GPS-JAMMER</div></div>
    <div class="sig-cell"><div class="sig-val">{bg}</div><div class="sig-lbl">BGP-ANOMALIEN</div></div>
  </div>
</div>

<div class="section">
  <h2>4. INFORMATIONS-UMFELD</h2>
  <p>Koordinations-Alarme: <strong style="color:#ff6644">{coord}</strong> &nbsp;|&nbsp;
     Staatl. Amplifikation: <strong style="color:#ff8800">{stateamp}</strong> &nbsp;|&nbsp;
     Risiko-Domains: <strong style="color:#ffaa00">{riskdom}</strong></p>
  {disinfo_html}
</div>

<div class="section">
  <h2>5. INTELLIGENCE GAPS</h2>
  <ul>{gap_rows}</ul>
</div>

{forecast_html}

{econ_html}

<div class="footer">
  {classif} // NEXUS-OSINT // {dtg} // NOT FOR DISTRIBUTION
</div>
</body>
</html>""".format(
        region    = sitrep["region"],
        classif   = sitrep["classification"],
        dtg       = sitrep["dtg"],
        bluf      = sitrep["bluf"],
        esc_level = sitrep["esc_level"],
        esc_score = sitrep["esc_score"],
        esc_col   = esc_col,
        trend     = sitrep["trend"],
        esc_signals_html = "".join(
            '<div style="color:#aaa;font-size:12px">[+{delta}] {lbl}</div>'.format(
                delta=s.get("delta",0), lbl=s.get("label","")) if isinstance(s,dict)
            else '<div style="color:#aaa;font-size:12px">- {}</div>'.format(s)
            for s in sitrep.get("esc_signals", [])
        ),
        total_art = sitrep["articles_total"],
        conf_pct  = sq["confidence_pct"],
        pct_best  = pct_best, pct_wahr = pct_wahr,
        pct_einz  = pct_einz, pct_unb  = pct_unb,
        bestaetigt    = sq["bestaetigt"],
        wahrscheinlich= sq["wahrscheinlich"],
        einzelmeldung = sq["einzelmeldung"],
        unbestaetigt  = sq["unbestaetigt"],
        dev_rows  = dev_rows or '<tr><td colspan="5" style="color:#555">Keine Ereignisse</td></tr>',
        fl=sig["flights"], isr=sig["isr"], sh=sig["ships"], ac=sig["acled"],
        se=sig["seismic"], fi=sig["firms"], li=sig["lightning"],
        gj=sig["gpsjam"], bg=sig["bgp"],
        coord    = dis["coordination_alerts"],
        stateamp = dis["state_amplification"],
        riskdom  = dis["high_risk_domains"],
        disinfo_html = ('<ul>{}</ul>'.format(disinfo_rows)) if disinfo_rows else
                       '<p style="color:#555">Keine Koordinations-Alarme.</p>',
        gap_rows  = gap_rows,
        forecast_html = (
            '<div class="section"><h2>7. 48H-PROGNOSE</h2>'
            '<p style="color:#aaf">{}</p></div>'.format(sitrep["forecast_48h"])
        ) if sitrep.get("forecast_48h") else "",
        econ_html = (
            '<div class="section"><h2>8. WIRTSCHAFTS-SIGNALE</h2><ul>{}</ul></div>'.format(
                "".join('<li>{}</li>'.format(e) for e in sitrep["econ_signals"]))
        ) if sitrep.get("econ_signals") else "",
    )

    if save_path:
        try:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
        except Exception:
            pass
        with open(save_path, "w", encoding="utf-8") as fh:
            fh.write(html)

    return html


# ── LLM-Kontext ───────────────────────────────────────────────────────────────

def sitrep_for_llm(pipeline_result: dict, query: str) -> str:
    """Kompakter SitRep-Text für den LLM-Kontext (max ~600 Zeichen)."""
    sr = generate_sitrep(pipeline_result, query)
    lines = [
        "[NEXUS SITREP — {}]".format(query.upper()),
        "BLUF: {}".format(sr["bluf"]),
        "Eskalation: {}/100 ({}) {}".format(sr["esc_score"], sr["esc_level"], sr["trend"]),
        "Quellen: {} Artikel ({}% verifiziert)".format(sr["articles_total"], sr["source_quality"]["confidence_pct"]),
    ]
    if sr["disinfo"]["coordination_alerts"]:
        lines.append("⚠ PROPAGANDALARM: {} koordinierte Themen".format(sr["disinfo"]["coordination_alerts"]))
    if sr["gaps"] and "alle" not in sr["gaps"][0].lower():
        lines.append("Gaps: {}".format(sr["gaps"][0]))
    return "\n".join(lines)


# ── Self-Test ─────────────────────────────────────────────────────────────────

def _self_test() -> None:
    test_result = {
        "articles": [
            {"title":"Angriff auf Odessa gemeldet","source":"ISW","date":"2026-05-27T08:00Z",
             "confidence":"BESTÄTIGT","credibility_label":"SEHR HOCH",
             "cluster_size":3,"is_canonical":True,"url":"https://isw.example.com","age_min":120},
            {"title":"Artilleriebeschuss in Cherson","source":"Kyiv Independent","date":"2026-05-27T09:00Z",
             "confidence":"WAHRSCHEINLICH","credibility_label":"HOCH",
             "cluster_size":2,"is_canonical":True,"url":"https://kyivindep.example.com","age_min":90},
            {"title":"Unbestätigte Berichte über Gegenangriff","source":"Telegram","date":"2026-05-27T10:00Z",
             "confidence":"UNBESTÄTIGT","credibility_label":"UNBEKANNT",
             "cluster_size":1,"is_canonical":True,"url":"t.me/example","age_min":60},
        ],
        "escalation": {"score": 72, "level": "HOCH",
                       "signal_details": [{"label":"ACLED-Ereignisse","delta":15},
                                           {"label":"ISR-Flüge aktiv","delta":10}]},
        "prediction":  {"score_history": [65, 68, 72], "trend": "↑ ESKALIEREND",
                        "scenarios": [{"label":"Weitere Eskalation","probability":0.65,
                                       "description":"Angriffe nehmen zu"}]},
        "netprop": {"coordination_alerts": [{"verdict":"KOORDINIERT",
                                              "topic":"Verluste ukrainischer Kräfte",
                                              "score":0.82}],
                    "state_amplification_events": ["rybar"]},
        "whois": {"disinfo_count": 2},
        "flights": {"aircraft": [{"callsign":"FORTE10","is_isr":True},
                                  {"callsign":"AUA100","is_isr":False}]},
        "maritime": {"ships": [{"name":"SHIP_A"},{"name":"SHIP_B"}]},
        "acled": [{"event_type":"Explosions"},{"event_type":"Battles"}],
        "seismic": [{"magnitude":2.1}],
        "firms": [{"lat":46.5,"lon":31.0}],
        "lightning": [],
        "gpsjam": [],
        "bgp_anomalies": [],
        "displacement": [],
        "health_alerts": [],
        "economics": {},
        "sanctions_hits": [],
        "timeline_context": "",
    }
    sr = generate_sitrep(test_result, "Ukraine")
    print("[SitRep Self-Test]")
    print("BLUF:", sr["bluf"])
    print("Eskalation:", sr["esc_score"], sr["esc_level"], sr["trend"])
    print("Key Devs:", len(sr["key_developments"]))
    print("Koordination:", sr["disinfo"]["coordination_alerts"])
    print("Source Quality:", sr["source_quality"])
    print("Gaps:", sr["gaps"])
    print("Forecast:", sr["forecast_48h"])
    txt = sitrep_to_text(sr)
    assert "BLUF:" in txt
    assert "SIGINT" in txt
    print("Text-Render: OK ({} Zeichen)".format(len(txt)))
    html = sitrep_to_html(sr)
    assert "<html" in html
    assert "NEXUS INTELLIGENCE BRIEF" in html
    print("HTML-Render: OK ({} Zeichen)".format(len(html)))
    llm = sitrep_for_llm(test_result, "Ukraine")
    print("LLM-Kontext:", llm[:120])
    print("[SitRep] Alle Tests bestanden.")


if __name__ == "__main__":
    _self_test()
