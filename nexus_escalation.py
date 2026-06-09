"""
NEXUS - Eskalations-Score Engine
Fusioniert alle NEXUS-Signal-Quellen zu einem einzigen Score 0-100.

Score-Interpretation:
  0-20:  GRUEN  – Ruhige Lage, keine auffaelligen Signale
  21-40: GELB   – Erhoehte Aufmerksamkeit, einzelne Signale
  41-60: ORANGE – Spannungslage, mehrere Indikatoren
  61-80: ROT    – Hohe Eskalation, koordinierte Signale
  81-100:KRITISCH – Unmittelbarer Eskalationsindikator

Jeder Kanal liefert einen Beitrag mit Konfidenz-Gewichtung.
Koinzidenz-Multiplikator: >= 3 Signale gleichzeitig -> Score x1.3

Alle Quellen sind optional – fehlende Module werden ignoriert.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── EMA-Glättung (T190) ───────────────────────────────────────────────────────
# Exponential Moving Average dämpft Score-Sprünge durch kurzfristige
# Datenlücken (OpenSky-Aussetzer, Modul-Fehler).
# Formel: ema_t = α * raw_t + (1-α) * ema_{t-1}
# α = 0.4 entspricht ~4-Lauf-EMA (2/(4+1) ≈ 0.4)
_EMA_ALPHA       = 0.4
_EMA_STORE_FILE  = Path(__file__).parent / "nexus_ema_state.json"


def _load_ema_state() -> dict:
    """Lädt persistenten EMA-Zustand aus Datei."""
    try:
        if _EMA_STORE_FILE.exists():
            return json.loads(_EMA_STORE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_ema_state(state: dict) -> None:
    """Speichert EMA-Zustand auf Disk."""
    try:
        _EMA_STORE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False),
                                   encoding="utf-8")
    except Exception:
        pass


def apply_ema(raw_score: float, region: str,
              alpha: float = _EMA_ALPHA) -> dict:
    """
    Wendet EMA-Glättung auf raw_score an.

    Gibt zurück:
      smoothed_score  – geglätteter Score (0–100)
      raw_score       – ungeglätteter Score
      smoothed_level  – Level des geglätteten Scores
      ema_runs        – Anzahl gesehener Läufe seit Reset
      delta           – Differenz glatt→roh (positiv = Anstieg)
      trend           – "STEIGEND" / "FALLEND" / "STABIL"
    """
    state   = _load_ema_state()
    rstate  = state.get(region, {})
    prev    = rstate.get("ema", raw_score)   # Initialisierung = erster Wert
    runs    = rstate.get("runs", 0)

    new_ema = round(alpha * raw_score + (1.0 - alpha) * prev, 1)
    new_ema = min(100.0, max(0.0, new_ema))

    state[region] = {
        "ema":          new_ema,
        "runs":         runs + 1,
        "last_raw":     raw_score,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    _save_ema_state(state)

    delta = round(new_ema - prev, 1)
    if   delta >  2: trend = "STEIGEND"
    elif delta < -2: trend = "FALLEND"
    else:            trend = "STABIL"

    sm_label, sm_color, sm_icon = _level(new_ema)
    return {
        "smoothed_score": new_ema,
        "raw_score":      raw_score,
        "smoothed_level": sm_label,
        "smoothed_color": sm_color,
        "smoothed_icon":  sm_icon,
        "ema_runs":       runs + 1,
        "delta":          delta,
        "trend":          trend,
        "alpha":          alpha,
    }


def reset_ema(region: str) -> None:
    """Setzt EMA-Zustand für eine Region zurück (z.B. nach langem Ausfall)."""
    state = _load_ema_state()
    state.pop(region, None)
    _save_ema_state(state)

# ── Signal-Gewichte (max. Punkte pro Kanal) ──────────────────────────────────
WEIGHTS = {
    "isr_aircraft":    20,   # Aufklaerungsflugzeug ueber Region
    "transponder_off": 15,   # Transponder ausgeschaltet (Geist)
    "detonation":      18,   # Seismische Detonations-Signatur
    "telegram_surge":  12,   # Telegram Nachrichten-Spike
    "artillery_signal": 8,   # Blitzortung / Artillerie-Flash
    "gps_jamming":     10,   # GPS-Jamming aktiv
    "ais_dark":         8,   # AIS-dunkles Schiff
    "draught_delta":    7,   # Tiefgang-Delta STS/Entladung
    "notam_zone":       6,   # NOTAM-Sperrzone aktiv
    "eonet_conflict":   5,   # Naturereignis in Konfliktzone
    "econ_stress":      5,   # Wirtschaftsstress hoch
    "acled_events":     6,   # ACLED-Konfliktereignisse
}

COINC_THRESHOLD  = 3      # Ab n Signalen -> Multiplikator
COINC_MULTIPLIER = 1.35   # Koinzidenz-Boost

# ── RSS-Keyword-Sets (Modul-Level, exportierbar für nexus_longtest.py) ────────
# Tier A: Bestätigte Angriffe / aktive Kampfhandlungen → bis 50 Punkte
RSS_ATTACK_KW: frozenset[str] = frozenset({
    "attack", "strike", "missile", "bombing", "rockets", "explosion",
    "angriff", "rakete", "bomben", "einschlag", "beschuss",
    "airstrike", "air strike", "shelling", "killed", "casualties",
    "war", "invasion", "offensive", "drone attack", "ballistic",
    "intercepted", "barrage", "projectile", "volley",
    # Farsi/Arabisch (häufig in Iran-Feeds)
    "انفجار", "موشک", "حمله", "كشته", "قتيل", "صاروخ", "هجوم",
})
# Tier B: Eskalations-Keywords ohne direkten Angriff → bis 15 Punkte
RSS_MED_KW: frozenset[str] = frozenset({
    "escalation", "eskalation", "military", "troops", "warship",
    "sanction", "threat", "nuclear", "crisis", "tension",
    "warplane", "fighter jet", "submarine", "mobilization",
    "ceasefire", "retaliatory", "retaliation", "hostile",
})
# Rückwärts-Kompatibilität (intern noch _ATTACK_KW/_MED_KW möglich)
_ATTACK_KW = RSS_ATTACK_KW
_MED_KW    = RSS_MED_KW

SCORE_LEVELS = [
    (81, "KRITISCH", "#ff0044", "⛔"),
    (61, "ROT",      "#ff2200", "🔴"),
    (41, "ORANGE",   "#ff8800", "🟠"),
    (21, "GELB",     "#ffcc00", "🟡"),
    ( 0, "GRUEN",    "#00ff88", "🟢"),
]


def _level(score: float) -> tuple[str, str, str]:
    """Gibt (label, color, icon) fuer einen Score zurueck."""
    for threshold, label, color, icon in SCORE_LEVELS:
        if score >= threshold:
            return label, color, icon
    return "GRUEN", "#00ff88", "🟢"


def _conf_factor(confidence: str) -> float:
    """Konfidenz -> Gewichts-Faktor."""
    return {"high": 1.0, "medium": 0.65, "low": 0.35, "none": 0.0}.get(
        (confidence or "").lower(), 0.5
    )


def compute_escalation(live_data: dict, region: str = "") -> dict:
    """
    Berechnet den Eskalations-Score aus dem nexus_live_server-Daten-Dict.

    live_data: Rueckgabewert von _fetch_live_data() aus nexus_live_server.py
    region:    Regionname fuer Kontext-Ausgabe

    Gibt dict zurueck:
      score (0-100), level, color, icon,
      active_signals (list), signal_details (list), timestamp
    """
    signals: dict[str, float] = {}
    details: list[dict]       = []

    # T196: Vorherigen EMA-Zustand lesen (für Rapid-Escalation-Signal)
    _ema_prev_state = _load_ema_state()
    _ema_prev_entry = _ema_prev_state.get(region or "global", {})
    _prev_ema_val   = _ema_prev_entry.get("ema")         # None wenn erster Lauf
    _prev_ema_runs  = _ema_prev_entry.get("runs", 0)

    # ── 1. ISR-Aufklaerungsflugzeuge ────────────────────────────────────────
    flights = live_data.get("flights") or {}
    aircraft = flights.get("aircraft") or []
    # T191: nur ISR-Flieger die tatsächlich über der Zielregion sind
    # isr_in_target_zone=True  → von nexus_flights.py gesetzt
    # isr_in_target_zone fehlt → Legacy-Log, kein Zone-Filter (safe default)
    isr_list = [a for a in aircraft
                if a.get("is_isr") and a.get("isr_in_target_zone", True)]
    if isr_list:
        conf   = "high" if any(a.get("isr_conf") == "high" for a in isr_list) else "medium"
        pts    = WEIGHTS["isr_aircraft"] * _conf_factor(conf)
        best   = sorted(isr_list, key=lambda a: {"high":2,"medium":1}.get(a.get("isr_conf",""),0), reverse=True)[0]
        signals["isr_aircraft"] = pts
        details.append({
            "signal": "isr_aircraft",
            "label":  f"ISR-Aufklaerer: {best.get('isr_type','?')} [{best.get('isr_role','?')}]",
            "points": round(pts, 1),
            "conf":   conf,
            "icon":   "🔎",
        })

    # ── 2. Transponder AUS (Ghost-Marker) ───────────────────────────────────
    vanished_ac = live_data.get("vanished_aircraft") or []
    if vanished_ac:
        pts = WEIGHTS["transponder_off"] * min(1.0, len(vanished_ac) * 0.5)
        signals["transponder_off"] = pts
        details.append({
            "signal": "transponder_off",
            "label":  f"{len(vanished_ac)} Transponder AUS (Ghost-Track)",
            "points": round(pts, 1),
            "conf":   "medium",
            "icon":   "👻",
        })

    # ── 3. Seismische Detonations-Kandidaten ─────────────────────────────────
    earthquakes = live_data.get("earthquakes") or []
    det_quakes  = [q for q in earthquakes if q.get("det_confidence")]
    if det_quakes:
        best_conf = "high" if any(q["det_confidence"] == "high" for q in det_quakes) else                     "medium" if any(q["det_confidence"] == "medium" for q in det_quakes) else "low"
        pts = WEIGHTS["detonation"] * _conf_factor(best_conf)
        signals["detonation"] = pts
        details.append({
            "signal": "detonation",
            "label":  f"{len(det_quakes)} Detonations-Kandidat(en) (Seismik)",
            "points": round(pts, 1),
            "conf":   best_conf,
            "icon":   "💥",
        })

    # ── 4. Telegram Surge ────────────────────────────────────────────────────
    surges = live_data.get("telegram_surges") or []
    if surges:
        max_score = max(s.get("score", 0) for s in surges)
        conf = "high" if max_score >= 8 else ("medium" if max_score >= 5 else "low")
        pts  = WEIGHTS["telegram_surge"] * _conf_factor(conf) * min(1.0, max_score / 10)
        signals["telegram_surge"] = pts
        details.append({
            "signal": "telegram_surge",
            "label":  f"Telegram Surge x{max_score:.1f} ({len(surges)} Kanal/Kanaele)",
            "points": round(pts, 1),
            "conf":   conf,
            "icon":   "⚡",
        })

    # ── 5. Artillerie-Signal (Blitzortung) ───────────────────────────────────
    lightning = live_data.get("lightning_signals") or []
    if lightning:
        best_conf = max((s.get("confidence","low") for s in lightning),
                        key=lambda c: {"high":3,"medium":2,"low":1}.get(c,0))
        pts = WEIGHTS["artillery_signal"] * _conf_factor(best_conf)
        signals["artillery_signal"] = pts
        details.append({
            "signal": "artillery_signal",
            "label":  f"Artillerie-Signal [{best_conf.upper()}]",
            "points": round(pts, 1),
            "conf":   best_conf,
            "icon":   "🔊",
        })

    # ── 6. GPS-Jamming ───────────────────────────────────────────────────────
    gpsjam = live_data.get("gpsjam_zones") or []
    high_jam = [z for z in gpsjam if z.get("intensity") == "HOCH"]
    if high_jam:
        pts = WEIGHTS["gps_jamming"] * 1.0
        signals["gps_jamming"] = pts
        details.append({
            "signal": "gps_jamming",
            "label":  f"GPS-Jamming HOCH ({high_jam[0].get('zone','?')})",
            "points": round(pts, 1),
            "conf":   "high",
            "icon":   "📡",
        })
    elif gpsjam:
        pts = WEIGHTS["gps_jamming"] * 0.5
        signals["gps_jamming"] = pts
        details.append({
            "signal": "gps_jamming",
            "label":  f"GPS-Stoerung MITTEL ({gpsjam[0].get('zone','?')})",
            "points": round(pts, 1),
            "conf":   "medium",
            "icon":   "📡",
        })

    # ── 7. AIS-dunkle Schiffe ────────────────────────────────────────────────
    dark_vessels = live_data.get("vanished_vessels") or []
    if dark_vessels:
        pts = WEIGHTS["ais_dark"] * min(1.0, len(dark_vessels) * 0.4)
        signals["ais_dark"] = pts
        details.append({
            "signal": "ais_dark",
            "label":  f"{len(dark_vessels)} AIS-dunkles Schiff/Schiffe",
            "points": round(pts, 1),
            "conf":   "medium",
            "icon":   "🚢",
        })

    # ── 8. Tiefgang-Delta ────────────────────────────────────────────────────
    draught = live_data.get("draught_alerts") or []
    high_draught = [d for d in draught if d.get("confidence") == "high"]
    if high_draught:
        pts = WEIGHTS["draught_delta"] * 1.0
        signals["draught_delta"] = pts
        details.append({
            "signal": "draught_delta",
            "label":  f"Tiefgang-Delta HOCH: {high_draught[0].get('event_type','?')}",
            "points": round(pts, 1),
            "conf":   "high",
            "icon":   "⚓",
        })
    elif draught:
        pts = WEIGHTS["draught_delta"] * 0.5
        signals["draught_delta"] = pts
        details.append({
            "signal": "draught_delta",
            "label":  f"{len(draught)} Tiefgang-Aenderung(en)",
            "points": round(pts, 1),
            "conf":   "medium",
            "icon":   "⚓",
        })

    # ── 9. NOTAM-Sperrzonen ──────────────────────────────────────────────────
    notams = live_data.get("notams") or []
    mil_notams = [n for n in notams if n.get("osint") and
                  ("Milit" in str(n.get("osint","")) or "Sperrgebiet" in str(n.get("osint","")))]
    if mil_notams:
        pts = WEIGHTS["notam_zone"] * min(1.0, len(mil_notams) * 0.4)
        signals["notam_zone"] = pts
        details.append({
            "signal": "notam_zone",
            "label":  f"{len(mil_notams)} Militaer-NOTAM(s) aktiv",
            "points": round(pts, 1),
            "conf":   "medium",
            "icon":   "⛔",
        })

    # ── 10. EONET-Konfliktzonen-Event ────────────────────────────────────────
    eonet = live_data.get("eonet") or []
    conf_eonet = [e for e in eonet if e.get("conflict_zone")]
    if conf_eonet:
        pts = WEIGHTS["eonet_conflict"] * min(1.0, len(conf_eonet) * 0.5)
        signals["eonet_conflict"] = pts
        details.append({
            "signal": "eonet_conflict",
            "label":  f"{len(conf_eonet)} NASA-Ereignis in Konfliktzone",
            "points": round(pts, 1),
            "conf":   "low",
            "icon":   "🌍",
        })

    # ── 11. Wirtschaftsstress ─────────────────────────────────────────────────
    econ = live_data.get("economics") or {}
    stress = econ.get("market_stress", "")
    if stress in ("KRITISCH", "ERHOEHT", "ERHÖHT"):
        pts = WEIGHTS["econ_stress"] * (1.0 if "KRITISCH" in stress else 0.6)
        signals["econ_stress"] = pts
        details.append({
            "signal": "econ_stress",
            "label":  f"Marktstress: {stress}",
            "points": round(pts, 1),
            "conf":   "medium",
            "icon":   "📊",
        })

    # ── 12. ACLED-Ereignisse ──────────────────────────────────────────────────
    acled = live_data.get("acled") or []
    crit_acled = [a for a in acled if a.get("priority") in ("KRITISCH", "HOCH")]
    if crit_acled:
        pts = WEIGHTS["acled_events"] * min(1.0, len(crit_acled) * 0.25)
        signals["acled_events"] = pts
        details.append({
            "signal": "acled_events",
            "label":  f"{len(crit_acled)} ACLED-Hochpriorits-Ereignis(se)",
            "points": round(pts, 1),
            "conf":   "high",
            "icon":   "💥",
        })

    # ── 13. RSS-Signal (T159 + T193-Fix) ────────────────────────────────────
    # Drei Stufen:
    #   A) Bestätigter Angriff in Headlines → PRIMÄRSIGNAL (bis 50 Punkte)
    #   B) Eskalations-Keywords ohne Angriff → Sekundärsignal (bis 15 Punkte)
    #   C) Nur Fallback wenn gar keine anderen Signale → Basis (bis 20 Punkte)
    rss_kw = live_data.get("rss_keywords") or []

    kw_lower  = {k.lower() for k in rss_kw}
    attack_kw = kw_lower & _ATTACK_KW
    med_kw    = kw_lower & _MED_KW

    if attack_kw:
        # Aktiver Angriff bestätigt in Presse → PRIMÄRSIGNAL
        # Skalierung: 1 KW=15, 3 KW=25, 5 KW=35, 7+ KW=45 → Decke 50
        # (Raketenangriff-Szenarien haben typisch 5-10 Attack-KWs gleichzeitig)
        n = len(attack_kw)
        if n >= 5:
            score_rss = min(50.0, 25.0 + n * 3.5)   # Massenangriff → ORANGE
        else:
            score_rss = min(30.0, 10.0 + n * 5.0)   # Einzelereignis → oberes GELB
        signals["rss_attack_confirmed"] = score_rss
        top_kws = ", ".join(sorted(attack_kw)[:4])
        kw_count_note = f" ({n} Schlüsselwörter)" if n >= 3 else ""
        details.append({
            "signal": "rss_attack_confirmed",
            "label":  f"⚠ RSS: Angriff bestätigt – {top_kws}{kw_count_note}",
            "points": round(score_rss, 1),
            "conf":   "high",
            "icon":   "🚨",
        })
    elif med_kw or (rss_kw and not signals):
        # Eskalations-Keywords oder Fallback
        if rss_kw and not signals:
            score_rss = min(20.0, len(rss_kw) * 2.0)
            sig_key, conf = "rss_keyword_fallback", "low"
            lbl = f"RSS-Signale: {', '.join(rss_kw[:4])}"
        else:
            score_rss = min(15.0, 3.0 + len(med_kw) * 2.0 + len(rss_kw) * 0.5)
            sig_key, conf = "rss_confirmation", "medium"
            lbl = f"RSS bestätigt: {', '.join(list(med_kw)[:3] or rss_kw[:3])}"
        signals[sig_key] = score_rss
        details.append({
            "signal": sig_key,
            "label":  lbl,
            "points": round(score_rss, 1),
            "conf":   conf,
            "icon":   "📰",
        })

    # ── Score berechnen ───────────────────────────────────────────────────────
    raw_score = sum(signals.values())

    # T196: Rapid-Escalation-Signal ─────────────────────────────────────────
    # Wenn der aktuelle Raw-Score um ≥10 Punkte über dem letzten EMA liegt
    # und es nicht der erste Lauf ist → eigenständiges ⚡-Signal.
    # Logik: Score-Sprung ist eigenständige Information (nicht nur Summe der
    # Einzelsignale), weil er auf koordiniertes, simultanes Eintreten hindeutet.
    # Gewicht: min(12 Pkt, delta*0.5) — deckt maximal ORANGE-Schwelle ab.
    if _prev_ema_val is not None and _prev_ema_runs >= 2:
        _delta_raw = raw_score - _prev_ema_val
        if _delta_raw >= 10.0:
            _delta_pts = min(12.0, round(_delta_raw * 0.5, 1))
            signals["rapid_escalation"] = _delta_pts
            raw_score += _delta_pts
            details.append({
                "signal": "rapid_escalation",
                "label":  f"Score-Sprung +{_delta_raw:.0f} Pkt vs. EMA (Rapid Escalation)",
                "points": _delta_pts,
                "conf":   "high" if _delta_raw >= 20 else "medium",
                "icon":   "⚡",
            })

    # Koinzidenz-Boost: ab COINC_THRESHOLD aktive Signale
    active_count = len(signals)
    if active_count >= COINC_THRESHOLD:
        coinc_boost = COINC_MULTIPLIER ** ((active_count - COINC_THRESHOLD + 1) * 0.5)
        raw_score  *= coinc_boost
        coinc_note  = f"Koinzidenz-Boost x{coinc_boost:.2f} ({active_count} Signale gleichzeitig)"
    else:
        coinc_note = ""

    score = min(100, round(raw_score, 1))
    level, color, icon = _level(score)

    # Details nach Punkten sortieren
    details.sort(key=lambda d: -d["points"])

    # ── T190: EMA-Glättung ────────────────────────────────────────────────────
    ema_result = apply_ema(score, region or "global")

    return {
        "region":          region,
        "score":           score,
        "level":           level,
        "color":           color,
        "icon":            icon,
        "active_signals":  list(signals.keys()),
        "signal_count":    active_count,
        "signal_details":  details,
        "coinc_note":      coinc_note,
        "timestamp":       datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
        # EMA-geglätteter Score (dämpft OpenSky-Aussetzer / Modul-Fehler)
        "smoothed_score":  ema_result["smoothed_score"],
        "smoothed_level":  ema_result["smoothed_level"],
        "smoothed_color":  ema_result["smoothed_color"],
        "ema_trend":       ema_result["trend"],
        "ema_delta":       ema_result["delta"],
        "ema_runs":        ema_result["ema_runs"],
    }


def escalation_summary(live_data: dict, region: str = "") -> str:
    """Text-Zusammenfassung fuer LLM."""
    r = compute_escalation(live_data, region)
    lines = [
        f"[ESKALATIONS-SCORE – {region}]",
        f"{r['icon']} Score: {r['score']}/100 | Level: {r['level']}",
        f"Aktive Signale: {r['signal_count']}",
    ]
    if r["coinc_note"]:
        lines.append(f"  ⚡ {r['coinc_note']}")
    for d in r["signal_details"][:6]:
        lines.append(f"  {d['icon']} {d['label']} (+{d['points']}pt, {d['conf'].upper()})")
    return "\n".join(lines)


def score_explanation(result: dict) -> str:
    """
    Baut einen lesbaren Ein-Zeilen-Erklaerungsstring aus dem Ergebnis.
    Beispiel: "Score 23.2 [GELB] wegen: 📡 GPS-Jamming +10pt · 📰 RSS +8pt · 🌍 EONET +5pt"
    """
    details = result.get("signal_details") or []
    if not details:
        return f"Score {result.get('score', 0)} [{result.get('level','?')}] — keine aktiven Signale"

    top = details[:4]
    parts = [f"{d['icon']} {d['label'].split(':')[0].split('(')[0].strip()} +{d['points']}pt"
             for d in top]
    explanation = f"Score {result.get('score',0)} [{result.get('level','?')}] wegen: " + " · ".join(parts)
    if result.get("coinc_note"):
        explanation += f" ⚡ Koinzidenz-Boost"
    return explanation


def compute_escalation_with_llm(live_data: dict, region: str = "") -> dict:
    """
    Wie compute_escalation(), ergaenzt das Ergebnis aber um LLM-Felder:
      llm_explanation  – 2-Satz-Erklaerung des Scores
      llm_brief        – 3-Satz-Lagebriefing
      llm_available    – bool

    Ollama wird nur angefragt wenn score > 0 (sonst sinnlos).
    Falls Ollama offline: Felder werden leer zurueckgegeben, kein Fehler.
    """
    result = compute_escalation(live_data, region)

    try:
        from nexus_llm import enrich_escalation_result
        if result.get("score", 0) > 0:
            result = enrich_escalation_result(result)
        else:
            result["llm_available"]  = False
            result["llm_explanation"] = ""
            result["llm_brief"]       = ""
    except ImportError:
        result["llm_available"]  = False
        result["llm_explanation"] = ""
        result["llm_brief"]       = ""
    except Exception:
        result["llm_available"]  = False
        result["llm_explanation"] = ""
        result["llm_brief"]       = ""

    return result


# ═════════════════════════════════════════════════════════════════════════════
# T209: Department-Integration
# Erweitert compute_escalation() um Department-Breakdown (optional).
# Rückwärtskompatibel: ohne `use_departments=True` unverändertes Verhalten.
# ═════════════════════════════════════════════════════════════════════════════

def compute_escalation_with_departments(
    live_data: dict,
    region: str = "",
    depts: Optional[list[str]] = None,
    parallel: bool = True,
) -> dict:
    """
    Wie compute_escalation(), ergänzt das Ergebnis aber um Department-Scores.

    Gibt zurück:
      Alle Felder von compute_escalation(), plus:
        dept_scores       – dict: dept → {score, confidence, icon, sources, findings}
        dept_master       – float: gewichteter Dept-Master-Score (0–100)
        dept_level        – str:   Level des Dept-Master-Scores
        dept_top          – list:  die 3 Abteilungen mit höchstem Score
        fusion_score      – float: 0.7 × signal_score + 0.3 × dept_master
        fusion_level      – str:   Level des Fusion-Scores
        fusion_icon       – str:   Icon des Fusion-Scores
        departments_run   – bool:  True wenn Departments berechnet wurden

    Parameters
    ----------
    live_data : Rückgabewert von nexus_live_server._fetch_live_data()
    region    : Regionname
    depts     : Welche Abteilungen (None = alle 6)
    parallel  : Parallele Ausführung der Dept-Calls
    """
    # Basis-Score aus bestehenden Signal-Detektoren
    base = compute_escalation(live_data, region)

    # Dept-Scores berechnen
    try:
        from nexus_departments import compute_department_scores, DEPARTMENTS
        dept_result = compute_department_scores(
            region=region,
            depts=depts,
            parallel=parallel,
        )

        dept_summary = dept_result.get("dept_summary", {})
        dept_master  = dept_result.get("master_score", 0.0)
        dept_icon    = dept_result.get("master_icon",  "🟢")
        dept_level   = dept_result.get("master_level", "GRUEN")

        # Top-3 Abteilungen nach Score
        top3 = sorted(
            dept_summary.items(),
            key=lambda x: x[1].get("score", 0),
            reverse=True,
        )[:3]

        # Fusion: 70% Signal-Score + 30% Dept-Master
        signal_score = base.get("score", 0.0)
        fusion = min(100.0, round(0.70 * signal_score + 0.30 * dept_master, 1))

        if   fusion >= 81: fl, fi = "KRITISCH", "⛔"
        elif fusion >= 61: fl, fi = "ROT",       "🔴"
        elif fusion >= 41: fl, fi = "ORANGE",    "🟠"
        elif fusion >= 21: fl, fi = "GELB",      "🟡"
        else:              fl, fi = "GRUEN",     "🟢"

        base.update({
            "dept_scores":      {
                d: {
                    "score":      s.get("score", 0),
                    "confidence": s.get("confidence", "none"),
                    "icon":       s.get("icon", ""),
                    "label":      s.get("label", d),
                    "sources":    s.get("sources", 0),
                    "findings":   s.get("findings", 0),
                    "weight_pct": s.get("weight_pct", 0),
                }
                for d, s in dept_summary.items()
            },
            "dept_details":     dept_result.get("departments", {}),
            "dept_master":      dept_master,
            "dept_level":       dept_level,
            "dept_icon":        dept_icon,
            "dept_top":         [(d, v.get("score", 0)) for d, v in top3],
            "fusion_score":     fusion,
            "fusion_level":     fl,
            "fusion_icon":      fi,
            "departments_run":  True,
        })

    except ImportError:
        base["departments_run"] = False
        base["dept_scores"]     = {}
    except Exception as e:
        base["departments_run"] = False
        base["dept_error"]      = str(e)
        base["dept_scores"]     = {}

    return base


def department_escalation_summary(result: dict) -> str:
    """
    Erweiterte Zusammenfassung mit Department-Breakdown (wenn vorhanden).
    Fallback auf normales escalation_summary() wenn Departments nicht gelaufen.
    """
    base_line = escalation_summary(result)
    if not result.get("departments_run"):
        return base_line

    dept_line = "  Departments: "
    for d, score in result.get("dept_top", []):
        dept_meta = {}
        try:
            from nexus_departments import DEPARTMENTS
            dept_meta = DEPARTMENTS.get(d, {})
        except ImportError:
            pass
        icon = dept_meta.get("icon", "")
        dept_line += f"{icon}{d}:{score:.0f}  "

    fusion = result.get("fusion_score", result.get("score", 0))
    fusion_icon = result.get("fusion_icon", "")
    dept_master = result.get("dept_master", 0)

    return (
        f"{base_line}\n"
        f"  {fusion_icon} Fusion-Score: {fusion:.1f} "
        f"(Signal:{result.get('score',0):.0f} + Dept:{dept_master:.0f})\n"
        f"{dept_line}"
    )

