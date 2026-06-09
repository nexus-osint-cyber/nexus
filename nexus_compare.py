"""
nexus_compare.py — NEXUS Zeitraum-Vergleich
=============================================
Vergleicht NEXUS-Daten zweier Zeiträume automatisch.
Standard: "heute vs. vor 7 Tagen"

Erkennt Trends wie:
  "+40% mehr GDELT-Artikel als letzte Woche"
  "Score von 15 auf 23 gestiegen (+53%)"
  "GPS-Jamming neu aufgetreten (war letzte Woche 0x)"

Aufruf:
  python nexus_compare.py                     → heute vs. gestern
  python nexus_compare.py --tage 7            → heute vs. vor 7 Tagen
  python nexus_compare.py --region Iran       → nur Iran-Läufe
  python nexus_compare.py --html              → HTML-Bericht öffnen
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

BASIS_DIR    = os.path.dirname(os.path.abspath(__file__))
DATEN_DIR    = os.path.join(BASIS_DIR, "nexus_longtest_daten")
COMPARE_HTML = os.path.join(DATEN_DIR, "nexus_compare.html")


# ── Daten laden ───────────────────────────────────────────────────────────────

def _lade_fenster(von: datetime, bis: datetime, region: str = "") -> list[dict]:
    """Lädt alle Läufe im Zeitfenster [von, bis]."""
    if not os.path.isdir(DATEN_DIR):
        return []
    laeufe = []
    for fname in sorted(os.listdir(DATEN_DIR)):
        if not (fname.startswith("lauf_") and fname.endswith(".json")):
            continue
        try:
            with open(os.path.join(DATEN_DIR, fname), encoding="utf-8") as f:
                data = json.load(f)
            ts = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
            if not (von <= ts <= bis):
                continue
            if region and data.get("ziel", "").lower() != region.lower():
                continue
            laeufe.append(data)
        except Exception:
            continue
    return laeufe


def _aggregiere(laeufe: list[dict]) -> dict:
    """Aggregiert Statistiken aus einer Liste von Läufen."""
    if not laeufe:
        return {}

    scores   = [l["quellen"].get("eskalation", {}).get("score", 0) or 0 for l in laeufe]
    signale: dict[str, int] = {}
    for l in laeufe:
        for s in l["quellen"].get("eskalation", {}).get("signale", []):
            signale[s] = signale.get(s, 0) + 1

    return {
        "laeufe":         len(laeufe),
        "score_avg":      round(sum(scores) / len(scores), 1) if scores else 0,
        "score_max":      max(scores) if scores else 0,
        "score_min":      min(scores) if scores else 0,
        "score_letzte":   scores[-1] if scores else 0,
        "level_letzte":   laeufe[-1]["quellen"].get("eskalation", {}).get("level", "?"),
        "flights_sum":    sum(l["quellen"].get("flights",   {}).get("count", 0) or 0 for l in laeufe),
        "gdelt_sum":      sum(l["quellen"].get("gdelt",     {}).get("count", 0) or 0 for l in laeufe),
        "rss_sum":        sum(l["quellen"].get("rss",       {}).get("count", 0) or 0 for l in laeufe),
        "firms_sum":      sum(l["quellen"].get("firms",     {}).get("count", 0) or 0 for l in laeufe),
        "seismik_sum":    sum(l["quellen"].get("seismik",   {}).get("count", 0) or 0 for l in laeufe),
        "signale":        signale,
    }


def _delta_str(alt: float, neu: float, einheit: str = "") -> str:
    """Formatiert einen Delta-Wert leserlich."""
    if alt == 0 and neu == 0:
        return "unverändert (0)"
    if alt == 0:
        return f"{neu}{einheit} (neu)"
    delta = neu - alt
    pct   = round((delta / alt) * 100) if alt != 0 else 0
    pfeil = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
    farbe_prefix = "+" if delta > 0 else ""
    return f"{neu}{einheit} ({farbe_prefix}{delta:+.1f}, {pfeil}{abs(pct)}%)"


# ── Vergleich durchführen ─────────────────────────────────────────────────────

def vergleiche(tage: int = 1, region: str = "") -> dict:
    """
    Vergleicht aktuelle Periode (letzte 24h) mit Referenz-Periode (vor N Tagen).
    Gibt strukturiertes Vergleichs-Dict zurück.
    """
    jetzt  = datetime.now(timezone.utc)

    # Aktuelle Periode: letzte 24h
    aktuell_bis = jetzt
    aktuell_von = jetzt - timedelta(hours=24)

    # Referenz-Periode: gleicher Zeitraum, N Tage früher
    ref_bis = jetzt - timedelta(days=tage)
    ref_von = ref_bis - timedelta(hours=24)

    laeufe_aktuell = _lade_fenster(aktuell_von, aktuell_bis, region)
    laeufe_ref     = _lade_fenster(ref_von, ref_bis, region)

    stats_a = _aggregiere(laeufe_aktuell)
    stats_r = _aggregiere(laeufe_ref)

    # Delta-Analyse
    deltas = {}
    if stats_a and stats_r:
        for key in ["score_avg", "score_max", "flights_sum", "gdelt_sum",
                    "rss_sum", "firms_sum", "seismik_sum"]:
            a = stats_a.get(key, 0)
            r = stats_r.get(key, 0)
            deltas[key] = {
                "alt":   r,
                "neu":   a,
                "delta": round(a - r, 1),
                "pct":   round(((a - r) / r * 100) if r != 0 else 0),
            }

        # Neue Signale die vorher nicht da waren
        neue_signale = [s for s in stats_a.get("signale", {})
                        if s not in stats_r.get("signale", {})]
        weggefallene = [s for s in stats_r.get("signale", {})
                        if s not in stats_a.get("signale", {})]
        deltas["neue_signale"]       = neue_signale
        deltas["weggefallene_signale"] = weggefallene

    return {
        "region":          region or "Alle",
        "vergleich_tage":  tage,
        "periode_aktuell": {
            "von": aktuell_von.strftime("%d.%m.%Y %H:%M UTC"),
            "bis": aktuell_bis.strftime("%d.%m.%Y %H:%M UTC"),
        },
        "periode_referenz": {
            "von": ref_von.strftime("%d.%m.%Y %H:%M UTC"),
            "bis": ref_bis.strftime("%d.%m.%Y %H:%M UTC"),
        },
        "aktuell":  stats_a,
        "referenz": stats_r,
        "deltas":   deltas,
        "hat_daten": bool(stats_a or stats_r),
    }


def formatiere_text(vgl: dict) -> str:
    """Gibt einen lesbaren Text-Vergleichsbericht zurück."""
    if not vgl["hat_daten"]:
        return f"[VERGLEICH] Keine Daten für Region '{vgl['region']}' verfügbar."

    region = vgl["region"]
    tage   = vgl["vergleich_tage"]
    a      = vgl["aktuell"]
    r      = vgl["referenz"]
    d      = vgl["deltas"]

    zeilen = [
        f"╔══ NEXUS ZEITVERGLEICH — {region} ══╗",
        f"  Aktuell:  {vgl['periode_aktuell']['von']} – {vgl['periode_aktuell']['bis']}",
        f"  Referenz: {vgl['periode_referenz']['von']} – {vgl['periode_referenz']['bis']} (vor {tage} Tag(en))",
        "",
    ]

    if a:
        zeilen.append(f"  🎯 Score aktuell:  {a.get('score_letzte',0)}/100 [{a.get('level_letzte','?')}]")
        if r:
            score_d = d.get("score_avg", {})
            pfeil = "↑" if score_d.get("delta", 0) > 0 else "↓"
            zeilen.append(f"  📈 Score-Änderung: {r.get('score_avg',0)} → {a.get('score_avg',0)} Ø ({pfeil}{abs(score_d.get('pct',0))}%)")
        zeilen.append("")

        # Daten-Vergleich
        metrics = [
            ("flights_sum",  "Flüge",         ""),
            ("gdelt_sum",    "GDELT-Artikel",  ""),
            ("rss_sum",      "RSS-Meldungen",  ""),
            ("firms_sum",    "FIRMS-Punkte",   ""),
            ("seismik_sum",  "Seismik-Events", ""),
        ]
        for key, label, einheit in metrics:
            if a.get(key, 0) or r.get(key, 0):
                zeilen.append(f"  {label:16s}: {_delta_str(r.get(key,0), a.get(key,0), einheit)}")

    # Neue/weggefallene Signale
    neue = d.get("neue_signale", [])
    weg  = d.get("weggefallene_signale", [])
    if neue:
        zeilen.append(f"\n  ⚠️  Neu aufgetretene Signale: {', '.join(neue)}")
    if weg:
        zeilen.append(f"  ✅ Nicht mehr aktiv: {', '.join(weg)}")

    zeilen.append("\n╚═══════════════════════════════════╝")
    return "\n".join(zeilen)


def generiere_html(vgl: dict) -> str:
    """Erzeugt einen HTML-Vergleichsbericht."""
    region = vgl["region"]
    tage   = vgl["vergleich_tage"]
    a      = vgl.get("aktuell",  {})
    r      = vgl.get("referenz", {})
    d      = vgl.get("deltas",   {})
    jetzt  = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    def delta_color(val: float) -> str:
        if val > 0:  return "#d32f2f"   # rot = mehr Eskalation
        if val < 0:  return "#00c853"   # grün = weniger
        return "#888"

    def delta_html(key: str, label: str) -> str:
        dv = d.get(key, {})
        delta = dv.get("delta", 0)
        pct   = dv.get("pct", 0)
        alt   = dv.get("alt", 0)
        neu   = dv.get("neu", 0)
        color = delta_color(delta)
        pfeil = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
        return f"""
        <tr>
          <td style="color:#aaa;padding:6px 8px">{label}</td>
          <td style="color:#888;text-align:right;padding:6px 8px">{alt}</td>
          <td style="color:#e0e0e0;text-align:right;padding:6px 8px;font-weight:600">{neu}</td>
          <td style="color:{color};text-align:right;padding:6px 8px">{pfeil}{abs(pct)}%</td>
        </tr>"""

    neue_sig = d.get("neue_signale", [])
    weg_sig  = d.get("weggefallene_signale", [])

    neue_html = "".join(
        f'<span style="background:#4a1a1a;color:#ff5252;padding:2px 8px;border-radius:10px;font-size:12px;margin:2px">{s}</span>'
        for s in neue_sig
    ) or '<span style="color:#666">keine</span>'

    weg_html = "".join(
        f'<span style="background:#1a4a1a;color:#69f0ae;padding:2px 8px;border-radius:10px;font-size:12px;margin:2px">{s}</span>'
        for s in weg_sig
    ) or '<span style="color:#666">keine</span>'

    level_a = a.get("level_letzte", "?")
    level_r = r.get("level_letzte", "?") if r else "–"
    score_a = a.get("score_letzte", 0)
    score_r = r.get("score_letzte", 0) if r else 0
    score_delta = score_a - score_r
    score_color = delta_color(score_delta)

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>NEXUS Zeitvergleich — {region}</title>
  <style>
    * {{ box-sizing:border-box; margin:0; padding:0 }}
    body {{ background:#0a0a1a; color:#e0e0e0; font-family:'Segoe UI',sans-serif; padding:20px; max-width:900px; margin:0 auto }}
    .header {{ background:linear-gradient(135deg,#0d1b2a,#1a2a4a); border:1px solid #1e3a5f; border-radius:12px; padding:20px; margin-bottom:20px }}
    .title {{ font-size:22px; font-weight:700; color:#4fc3f7 }}
    .sub {{ color:#888; font-size:13px; margin-top:4px }}
    .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:20px }}
    .card {{ background:#0d1b2a; border:1px solid #1e3a5f; border-radius:10px; padding:16px }}
    .card-title {{ font-size:11px; color:#4fc3f7; text-transform:uppercase; letter-spacing:1px; margin-bottom:12px }}
    .score {{ font-size:42px; font-weight:900; line-height:1 }}
    table {{ width:100%; border-collapse:collapse }}
    tr:nth-child(even) {{ background:#0a1520 }}
    th {{ color:#4fc3f7; font-size:11px; text-transform:uppercase; padding:6px 8px; text-align:left; border-bottom:1px solid #1e3a5f }}
    .full {{ grid-column:1/-1 }}
    .footer {{ color:#444; font-size:11px; text-align:center; margin-top:20px }}
  </style>
</head>
<body>
  <div class="header">
    <div class="title">📊 NEXUS Zeitvergleich — {region}</div>
    <div class="sub">Aktuell vs. vor {tage} Tag(en) · Erstellt: {jetzt}</div>
    <div class="sub">Aktuell: {vgl['periode_aktuell']['von']} – {vgl['periode_aktuell']['bis']}</div>
    <div class="sub">Referenz: {vgl['periode_referenz']['von']} – {vgl['periode_referenz']['bis']}</div>
  </div>

  <div class="grid">
    <div class="card">
      <div class="card-title">⚡ Score Aktuell</div>
      <div class="score" style="color:#ffd600">{score_a}</div>
      <div style="color:#aaa;margin-top:4px">{level_a}</div>
      <div style="color:{score_color};margin-top:8px;font-size:14px">
        Δ vs. Referenz: {"+" if score_delta>0 else ""}{score_delta:+.1f} Punkte
      </div>
    </div>
    <div class="card">
      <div class="card-title">📅 Score Referenz</div>
      <div class="score" style="color:#888">{score_r}</div>
      <div style="color:#666;margin-top:4px">{level_r}</div>
      <div style="color:#555;margin-top:8px;font-size:13px">{vgl['periode_referenz']['von'][:10]}</div>
    </div>

    <div class="card full">
      <div class="card-title">📈 Daten-Vergleich</div>
      <table>
        <tr>
          <th>Metrik</th><th style="text-align:right">Referenz</th>
          <th style="text-align:right">Aktuell</th><th style="text-align:right">Änderung</th>
        </tr>
        {delta_html("score_avg",   "Score Ø")}
        {delta_html("score_max",   "Score Max")}
        {delta_html("flights_sum", "Flüge")}
        {delta_html("gdelt_sum",   "GDELT-Artikel")}
        {delta_html("rss_sum",     "RSS-Meldungen")}
        {delta_html("firms_sum",   "FIRMS-Punkte")}
        {delta_html("seismik_sum", "Seismik-Events")}
      </table>
    </div>

    <div class="card">
      <div class="card-title">⚠️ Neu aufgetretene Signale</div>
      <div style="margin-top:8px">{neue_html}</div>
    </div>
    <div class="card">
      <div class="card-title">✅ Nicht mehr aktive Signale</div>
      <div style="margin-top:8px">{weg_html}</div>
    </div>
  </div>

  <div class="footer">NEXUS Intelligence Platform · Zeitvergleich automatisch generiert · {jetzt}</div>
</body>
</html>"""


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NEXUS Zeitraum-Vergleich")
    parser.add_argument("--tage",   type=int, default=1,  help="Vergleich mit vor N Tagen (default: 1)")
    parser.add_argument("--region", type=str, default="", help="Region (z.B. Iran)")
    parser.add_argument("--html",   action="store_true",  help="HTML-Bericht öffnen")
    args = parser.parse_args()

    vgl = vergleiche(args.tage, args.region)
    print(formatiere_text(vgl))

    if args.html:
        html = generiere_html(vgl)
        os.makedirs(DATEN_DIR, exist_ok=True)
        with open(COMPARE_HTML, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\n[Vergleich] HTML gespeichert: {COMPARE_HTML}")
        try:
            import webbrowser
            webbrowser.open(f"file:///{COMPARE_HTML.replace(os.sep, '/')}")
        except Exception:
            pass
