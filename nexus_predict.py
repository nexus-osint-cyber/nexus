"""
NEXUS – Predictive Analytics  (Ebene 4 / Modul 4.2)
====================================================
Speichert den Eskalations-Score pro Region + Zeitpunkt in SQLite
und berechnet daraus:

  • Trend (steigend / fallend / stabil)
  • Score-Vorschau für +24h / +48h (lineare + exponentielle Extrapolation)
  • Anomalie-Erkennung (Spikes > ±2σ)
  • Sparkline-Daten für Chart.js im HTML-Report

Datenbank:  nexus_world.db  (gleiche wie nexus_delta.py)
Tabelle:    escalation_history
  Spalten:  id, region, ts (REAL/Unix), score (REAL), level (TEXT),
            signal_count (INT), signal_json (TEXT), source (TEXT)

Öffentliche API:
  record_score(region, score, level, signal_details, source)
  get_history(region, hours_back)  → list[dict]
  predict(region, hours_ahead)     → dict
  get_sparkline(region, hours_back, n_points) → dict   (für Chart.js)
  detect_anomalies(region, hours_back)        → list[dict]
  get_trend_summary(region)                   → str
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "nexus_world.db"

# ─────────────────────────────────────────────────────────────────────────────
# Datenbank
# ─────────────────────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_predict_db() -> None:
    """Legt die escalation_history-Tabelle an falls nicht vorhanden."""
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS escalation_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                region       TEXT    NOT NULL,
                ts           REAL    NOT NULL,
                score        REAL    NOT NULL DEFAULT 0,
                level        TEXT    NOT NULL DEFAULT 'GRUEN',
                signal_count INTEGER NOT NULL DEFAULT 0,
                signal_json  TEXT,
                source       TEXT    DEFAULT 'live_server'
            );
            CREATE INDEX IF NOT EXISTS idx_esc_history_region_ts
                ON escalation_history(region, ts);
        """)
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Schreiben
# ─────────────────────────────────────────────────────────────────────────────

# Minimales Intervall zwischen zwei Records pro Region (verhindert Duplikate)
_MIN_RECORD_INTERVAL_S = 55   # etwas unter 60s (Live-Server-Polling)
_last_record: dict[str, float] = {}


def record_score(
    region:         str,
    score:          float,
    level:          str          = "GRUEN",
    signal_details: list[dict]   = None,
    source:         str          = "live_server",
) -> bool:
    """
    Speichert einen Eskalations-Score-Datenpunkt.
    Gibt False zurück wenn der letzte Record für diese Region zu frisch ist.
    """
    region = region.strip()
    if not region:
        return False

    now = time.time()
    last = _last_record.get(region, 0)
    if now - last < _MIN_RECORD_INTERVAL_S:
        return False

    init_predict_db()
    sig_json = json.dumps(
        [{"s": d.get("signal",""), "p": round(d.get("points",0),1)} for d in (signal_details or [])[:12]],
        ensure_ascii=False
    )
    with _db() as conn:
        conn.execute(
            "INSERT INTO escalation_history (region, ts, score, level, signal_count, signal_json, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (region, now, float(score), level, len(signal_details or []), sig_json, source),
        )
        conn.commit()
    _last_record[region] = now
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Lesen
# ─────────────────────────────────────────────────────────────────────────────

def get_history(region: str, hours_back: int = 72) -> list[dict]:
    """
    Gibt Score-Verlauf der letzten `hours_back` Stunden zurück.
    Sortiert nach ts aufsteigend (älteste zuerst).
    """
    init_predict_db()
    cutoff = time.time() - hours_back * 3600
    with _db() as conn:
        rows = conn.execute(
            "SELECT ts, score, level, signal_count FROM escalation_history "
            "WHERE region=? AND ts>=? ORDER BY ts ASC",
            (region.strip(), cutoff),
        ).fetchall()
    return [{"ts": r["ts"], "score": r["score"], "level": r["level"],
             "signal_count": r["signal_count"]} for r in rows]


def get_all_regions() -> list[str]:
    """Gibt alle Regionen mit mindestens einem Eintrag zurück."""
    init_predict_db()
    with _db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT region FROM escalation_history ORDER BY region"
        ).fetchall()
    return [r["region"] for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Statistik-Hilfsfunktionen
# ─────────────────────────────────────────────────────────────────────────────

def _linear_regression(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Einfache lineare Regression. Gibt (slope, intercept) zurück."""
    n = len(xs)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num    = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den    = sum((x - mean_x) ** 2 for x in xs)
    slope  = num / den if den != 0 else 0.0
    return slope, mean_y - slope * mean_x


def _exp_smoothing(ys: list[float], alpha: float = 0.3) -> float:
    """Exponentielles Glätten → gibt letzten geglätteten Wert zurück."""
    if not ys:
        return 0.0
    val = ys[0]
    for y in ys[1:]:
        val = alpha * y + (1 - alpha) * val
    return val


def _detect_trend(scores: list[float]) -> str:
    """Gibt 'steigend' / 'fallend' / 'stabil' zurück."""
    if len(scores) < 4:
        return "stabil"
    # Vergleiche letztes Drittel mit erstem Drittel
    n     = len(scores)
    third = max(1, n // 3)
    early = sum(scores[:third]) / third
    late  = sum(scores[-third:]) / third
    delta = late - early
    if delta > 5:
        return "steigend"
    if delta < -5:
        return "fallend"
    return "stabil"


# ─────────────────────────────────────────────────────────────────────────────
# Vorhersage
# ─────────────────────────────────────────────────────────────────────────────

def predict(
    region:      str,
    hours_ahead: int  = 48,
    hours_back:  int  = 72,
) -> dict:
    """
    Berechnet Score-Vorhersage für +24h und +48h.

    Rückgabe:
      {
        data_points:    int,    # Anzahl historischer Datenpunkte
        trend:          str,    # steigend / fallend / stabil
        current_score:  float,
        current_level:  str,
        mean_72h:       float,
        std_72h:        float,
        pred_24h:       float,  # Vorhersage +24h (linear)
        pred_48h:       float,  # Vorhersage +48h (linear)
        pred_24h_exp:   float,  # Vorhersage +24h (exponentiell)
        confidence:     str,    # hoch / mittel / niedrig (Datenbasis)
        slope_per_hour: float,  # Score-Änderung pro Stunde
        message:        str,    # Kurz-Text für UI
        insufficient_data: bool,
      }
    """
    history = get_history(region, hours_back)

    if len(history) < 3:
        return {
            "data_points": len(history),
            "trend": "unbekannt",
            "current_score": 0,
            "current_level": "GRUEN",
            "mean_72h": 0, "std_72h": 0,
            "pred_24h": 0, "pred_48h": 0,
            "pred_24h_exp": 0,
            "confidence": "niedrig",
            "slope_per_hour": 0,
            "message": f"Nicht genug Daten ({len(history)} Punkte). Mindestens 3 benötigt.",
            "insufficient_data": True,
        }

    now    = time.time()
    scores = [h["score"] for h in history]
    ts_rel = [(h["ts"] - now) / 3600 for h in history]  # Stunden relativ zu jetzt

    mean_s  = statistics.mean(scores)
    std_s   = statistics.stdev(scores) if len(scores) > 1 else 0

    # Lineare Regression über Zeit → Score
    slope, intercept = _linear_regression(ts_rel, scores)

    # Vorhersagen (clamp 0–100)
    pred_lin_24 = max(0, min(100, slope * 24 + (scores[-1])))
    pred_lin_48 = max(0, min(100, slope * 48 + (scores[-1])))

    # Exponentielle Glättung → letzter geglätteter Wert + Trend-Extrapolation
    ema  = _exp_smoothing(scores, alpha=0.3)
    pred_exp_24 = max(0, min(100, ema + slope * 24))

    trend = _detect_trend(scores)

    # Konfidenz basierend auf Datenmenge
    if len(history) >= 48:
        confidence = "hoch"
    elif len(history) >= 12:
        confidence = "mittel"
    else:
        confidence = "niedrig"

    # Trend-Icons
    trend_icons = {"steigend": "📈", "fallend": "📉", "stabil": "➡"}
    t_icon = trend_icons.get(trend, "➡")

    current = history[-1]
    pred_level_24 = _score_to_level(pred_lin_24)
    pred_level_48 = _score_to_level(pred_lin_48)

    if abs(slope) < 0.1:
        msg = f"Lage stabil ➡ · Score bleibt bei ~{current['score']:.0f}/100"
    elif slope > 0:
        msg = (f"Trend {t_icon} steigend (+{slope:.1f}/h) · "
               f"Vorschau: {pred_lin_24:.0f}/100 in 24h ({pred_level_24})")
    else:
        msg = (f"Trend {t_icon} fallend ({slope:.1f}/h) · "
               f"Vorschau: {pred_lin_24:.0f}/100 in 24h ({pred_level_24})")

    return {
        "data_points":    len(history),
        "trend":          trend,
        "current_score":  current["score"],
        "current_level":  current["level"],
        "mean_72h":       round(mean_s, 1),
        "std_72h":        round(std_s, 1),
        "pred_24h":       round(pred_lin_24, 1),
        "pred_48h":       round(pred_lin_48, 1),
        "pred_24h_exp":   round(pred_exp_24, 1),
        "pred_level_24h": pred_level_24,
        "pred_level_48h": pred_level_48,
        "confidence":     confidence,
        "slope_per_hour": round(slope, 3),
        "message":        msg,
        "insufficient_data": False,
    }


def _score_to_level(score: float) -> str:
    if score >= 81: return "KRITISCH"
    if score >= 61: return "HOCH"
    if score >= 41: return "MITTEL"
    if score >= 21: return "NIEDRIG"
    return "GRUEN"

# ─────────────────────────────────────────────────────────────────────────────
# ML-Modul – scikit-learn RandomForest (optional, kein Hard-Dep)
# Installation: pip install scikit-learn --break-system-packages
# ─────────────────────────────────────────────────────────────────────────────

_ml_models: dict = {}   # region → trainiertes Modell


def _build_features(history: list[dict]) -> "list[list[float]]":
    """Feature-Engineering aus historischen Eskalations-Scores."""
    import statistics as _stats
    features = []
    for i in range(4, len(history)):
        window = history[max(0, i-12):i]
        scores = [h["score"] for h in window]
        counts = [h.get("signal_count", 0) for h in window]

        # Laufende Statistiken
        mean_s  = _stats.mean(scores) if scores else 0
        std_s   = _stats.stdev(scores) if len(scores) > 1 else 0
        max_s   = max(scores) if scores else 0
        min_s   = min(scores) if scores else 0
        last4   = scores[-4:]  if len(scores) >= 4 else scores
        slope4  = (last4[-1] - last4[0]) / len(last4) if len(last4) > 1 else 0

        # Zeitliche Features
        dt      = history[i]
        ts      = float(dt.get("ts", 0))
        import time as _time
        hour_of_day = (_time.gmtime(ts).tm_hour / 24.0) if ts else 0

        features.append([
            mean_s, std_s, max_s, min_s, slope4,
            scores[-1] if scores else 0,     # letzter Score
            scores[-2] if len(scores)>1 else 0,
            scores[-3] if len(scores)>2 else 0,
            _stats.mean(counts) if counts else 0,  # durchschn. Signalanzahl
            hour_of_day,
        ])
    return features


def train_ml_model(region: str, hours_back: int = 168) -> bool:
    """
    Trainiert RandomForest-Modell auf eigenen historischen Daten.
    Mindestens 50 Datenpunkte nötig.
    Gibt True zurück wenn Training erfolgreich war.
    """
    try:
        from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
        from sklearn.model_selection import cross_val_score
        import numpy as np
    except ImportError:
        return False

    history = get_history(region, hours_back=hours_back)
    if len(history) < 50:
        return False

    X = _build_features(history)
    # Target: Score 4 Punkte in der Zukunft (ca. 4 Stunden wenn stündliche Aufzeichnung)
    y = [history[i + 4]["score"] for i in range(len(X))]
    if len(X) < 20:
        return False

    X_arr = np.array(X)
    y_arr = np.array(y)

    # Ensemble: RandomForest + GradientBoosting
    rf  = RandomForestRegressor(n_estimators=80, max_depth=6,
                                random_state=42, n_jobs=-1)
    gb  = GradientBoostingRegressor(n_estimators=60, max_depth=4,
                                     learning_rate=0.1, random_state=42)

    # Cross-Validation für Qualitätsprüfung
    try:
        cv_scores = cross_val_score(rf, X_arr, y_arr, cv=3,
                                    scoring="neg_mean_absolute_error")
        mae = -cv_scores.mean()
    except Exception:
        mae = 99.0

    rf.fit(X_arr, y_arr)
    gb.fit(X_arr, y_arr)

    _ml_models[region] = {
        "rf":          rf,
        "gb":          gb,
        "mae":         round(mae, 2),
        "trained_on":  len(history),
        "trained_at":  __import__("time").time(),
    }
    return True


def ml_predict(region: str, hours_ahead: int = 24) -> dict:
    """
    ML-basierte Vorhersage (besser als lineare Extrapolation).
    Fällt automatisch auf klassische Methode zurück wenn kein Modell.
    """
    model_data = _ml_models.get(region)

    # Modell nicht vorhanden oder älter als 6h → neu trainieren
    import time as _time
    if (not model_data or
            _time.time() - model_data.get("trained_at", 0) > 21600):
        success = train_ml_model(region)
        model_data = _ml_models.get(region) if success else None

    if not model_data:
        # Kein Modell möglich → klassische Vorhersage
        return {"ml_available": False, "method": "linear"}

    history = get_history(region, hours_back=24)
    if len(history) < 6:
        return {"ml_available": False, "method": "linear"}

    try:
        import numpy as np
        X = _build_features(history)
        if not X:
            return {"ml_available": False, "method": "linear"}

        feat = np.array([X[-1]])
        pred_rf = float(model_data["rf"].predict(feat)[0])
        pred_gb = float(model_data["gb"].predict(feat)[0])
        # Ensemble-Durchschnitt
        pred_ensemble = (pred_rf * 0.55 + pred_gb * 0.45)
        pred_ensemble = max(0, min(100, pred_ensemble))

        return {
            "ml_available":   True,
            "method":         "ensemble_rf_gb",
            "pred_ensemble":  round(pred_ensemble, 1),
            "pred_rf":        round(pred_rf, 1),
            "pred_gb":        round(pred_gb, 1),
            "mae":            model_data["mae"],
            "trained_on":     model_data["trained_on"],
            "level":          _score_to_level(pred_ensemble),
        }
    except Exception:
        return {"ml_available": False, "method": "linear"}


def predict_enhanced(region: str, hours_ahead: int = 48) -> dict:
    """
    Kombinierte Vorhersage: klassisch + ML (falls verfügbar).
    Das ist die empfohlene Funktion für nexus_report.py.
    """
    base    = predict(region, hours_ahead=hours_ahead)
    ml_data = ml_predict(region, hours_ahead=hours_ahead)

    base["ml"] = ml_data
    if ml_data.get("ml_available"):
        # ML-Vorhersage ergänzt die lineare
        base["pred_24h_ml"]    = ml_data["pred_ensemble"]
        base["pred_level_ml"]  = ml_data["level"]
        base["pred_mae"]       = ml_data["mae"]
        base["method"]         = "ML-Ensemble (RF + GB) + Linear"
        base["message"] += (
            f" | ML-Vorhersage: {ml_data['pred_ensemble']:.0f}/100 "
            f"({ml_data['level']}, MAE±{ml_data['mae']:.1f})"
        )
    else:
        base["method"] = "Linear + Exponential Smoothing"

    return base



    if score >= 81: return "KRITISCH"
    if score >= 61: return "ROT"
    if score >= 41: return "ORANGE"
    if score >= 21: return "GELB"
    return "GRUEN"


# ─────────────────────────────────────────────────────────────────────────────
# Sparkline-Daten (für Chart.js)
# ─────────────────────────────────────────────────────────────────────────────

def get_sparkline(
    region:      str,
    hours_back:  int = 72,
    n_points:    int = 48,
) -> dict:
    """
    Gibt reduzierte Zeitreihe für einen Chart.js-Sparkline zurück.

    Rückgabe:
      {
        labels:       list[str],   # Zeitachse ("–72h", "–48h", ..., "Jetzt", "+24h", "+48h")
        scores:       list[float], # historische Scores
        pred_scores:  list[float], # Vorhersage-Scores (None für historische Punkte)
        anomalies:    list[int],   # Indizes mit Anomalien
        level_colors: list[str],   # Farbe pro Punkt
        region:       str,
        data_points:  int,
        prediction:   dict,        # Rückgabe von predict()
      }
    """
    history = get_history(region, hours_back)
    pred    = predict(region, hours_back=hours_back)

    # Resample auf n_points
    if len(history) > n_points:
        step    = len(history) / n_points
        history = [history[int(i * step)] for i in range(n_points)]

    now_ts = time.time()
    labels: list[str]   = []
    scores: list[float] = []
    colors: list[str]   = []

    for h in history:
        age_h = (now_ts - h["ts"]) / 3600
        if age_h < 1:
            lbl = "Jetzt"
        elif age_h < 2:
            lbl = f"–{age_h*60:.0f}min"
        else:
            lbl = f"–{age_h:.0f}h"
        labels.append(lbl)
        scores.append(round(h["score"], 1))
        colors.append(_level_color(h["level"]))

    # Vorhersage-Punkte anhängen
    pred_scores: list[Optional[float]] = [None] * len(scores)
    if not pred["insufficient_data"]:
        labels.append("+24h")
        labels.append("+48h")
        scores.append(None)
        scores.append(None)
        pred_scores.append(round(pred["pred_24h"], 1))
        pred_scores.append(round(pred["pred_48h"], 1))
        colors.append(_level_color(pred["pred_level_24h"]))
        colors.append(_level_color(pred["pred_level_48h"]))

    # Anomalie-Indizes
    anom = detect_anomalies(region, hours_back)
    anom_ts = {a["ts"] for a in anom}
    anomaly_indices = [i for i, h in enumerate(get_history(region, hours_back))
                       if h["ts"] in anom_ts]

    return {
        "labels":       labels,
        "scores":       scores,
        "pred_scores":  pred_scores,
        "anomalies":    anomaly_indices,
        "level_colors": colors,
        "region":       region,
        "data_points":  len(history),
        "prediction":   pred,
    }


def _level_color(level: str) -> str:
    return {
        "KRITISCH": "#ff0044",
        "ROT":      "#ff2200",
        "ORANGE":   "#ff8800",
        "GELB":     "#ffcc00",
        "GRUEN":    "#00ff88",
    }.get(level, "#3b82f6")


# ─────────────────────────────────────────────────────────────────────────────
# Anomalie-Erkennung
# ─────────────────────────────────────────────────────────────────────────────

def detect_anomalies(
    region:     str,
    hours_back: int   = 72,
    sigma:      float = 2.0,
) -> list[dict]:
    """
    Findet Score-Spikes > ±sigma Standardabweichungen vom Mittelwert.

    Rückgabe: Liste von Anomalie-Dicts:
      { ts, score, level, deviation, severity }
    """
    history = get_history(region, hours_back)
    if len(history) < 5:
        return []

    scores = [h["score"] for h in history]
    mean_s = statistics.mean(scores)
    std_s  = statistics.stdev(scores) if len(scores) > 1 else 0

    if std_s < 1:   # Zu flach → keine sinnvollen Anomalien
        return []

    anomalies = []
    for h in history:
        z = (h["score"] - mean_s) / std_s
        if abs(z) >= sigma:
            severity = "KRITISCH" if abs(z) >= 3 else ("HOCH" if abs(z) >= 2.5 else "MITTEL")
            anomalies.append({
                "ts":        h["ts"],
                "score":     h["score"],
                "level":     h["level"],
                "deviation": round(z, 2),
                "severity":  severity,
                "dt":        datetime.fromtimestamp(h["ts"], tz=timezone.utc).strftime("%d.%m %H:%M UTC"),
            })
    return anomalies


# ─────────────────────────────────────────────────────────────────────────────
# Trend-Zusammenfassung (für Report/Terminal)
# ─────────────────────────────────────────────────────────────────────────────

def get_trend_summary(region: str) -> str:
    """Gibt einen einzeiligen Trend-Text zurück."""
    p = predict(region)
    if p["insufficient_data"]:
        return p["message"]

    trend_sym = {"steigend": "📈", "fallend": "📉", "stabil": "➡"}.get(p["trend"], "➡")
    anom = detect_anomalies(region)
    anom_str = f" · ⚠ {len(anom)} Anomalie(n)" if anom else ""

    return (
        f"{trend_sym} {region}: Score {p['current_score']:.0f}/100 ({p['current_level']}) · "
        f"Trend: {p['trend']} ({p['slope_per_hour']:+.2f}/h) · "
        f"24h: {p['pred_24h']:.0f} ({p['pred_level_24h']}) · "
        f"48h: {p['pred_48h']:.0f} ({p['pred_level_48h']}) · "
        f"Konfidenz: {p['confidence']}{anom_str}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Integration in nexus_live_server.py / nexus_escalation.py
# ─────────────────────────────────────────────────────────────────────────────

def auto_record_from_escalation(esc_result: dict) -> None:
    """
    Wird von nexus_live_server nach compute_escalation_with_llm() aufgerufen.
    Speichert den Score automatisch in die History.
    """
    region  = esc_result.get("region", "")
    score   = esc_result.get("score", 0)
    level   = esc_result.get("level", "GRUEN")
    details = esc_result.get("signal_details", [])
    if region:
        record_score(region, score, level, details, source="live_server")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    args = sys.argv[1:]

    if "--seed" in args:
        # Testdaten generieren (48 Stunden mit Trend)
        import math, random
        region = "Ukraine"
        print(f"Erzeuge Test-Daten für '{region}'...")
        now = time.time()
        random.seed(42)
        for i in range(96):   # alle 30min über 48h
            ts_offset = -(96 - i) * 1800
            t = now + ts_offset
            # Simulierter Score: Start 20, steigt auf 65, dann fällt auf 40
            progress = i / 96
            if progress < 0.5:
                base = 20 + progress * 90    # 20 → 65
            else:
                base = 65 - (progress - 0.5) * 50   # 65 → 40
            score = max(0, min(100, base + random.gauss(0, 5)))
            # Anomalie bei i=30 (spike)
            if i == 30:
                score = min(100, score + 25)
            level = _score_to_level(score)
            init_predict_db()
            _last_record[region] = 0   # Override cooldown für Test
            record_score(region, score, level, [], "seed")
            # Direkt in DB schreiben mit korrektem Timestamp
            with _db() as conn:
                conn.execute(
                    "UPDATE escalation_history SET ts=? WHERE region=? AND source='seed' AND id=("
                    "SELECT id FROM escalation_history WHERE region=? AND source='seed' ORDER BY id DESC LIMIT 1)",
                    (t, region, region)
                )
                conn.commit()
        print(f"✓ 96 Testpunkte gespeichert.")

    region = args[0] if args and not args[0].startswith("--") else "Ukraine"
    print(f"\n── Prediction für '{region}' ──")
    p = predict(region)
    for k, v in p.items():
        print(f"  {k:<20} {v}")

    print(f"\n── Trend-Zusammenfassung ──")
    print(get_trend_summary(region))

    print(f"\n── Anomalien ──")
    anoms = detect_anomalies(region)
    if anoms:
        for a in anoms:
            sev = a['severity']
            dev = a['deviation']
            sc  = a['score']
            dt  = a['dt']
            print(f"  Anomalie: {dt}  Score={sc:.0f}  Abweich.={dev:+.1f} Sigma  [{sev}]")
    else:
        print("  Keine Anomalien erkannt.")

    hist = get_history(region, 72)
    print(f"\n── Datenpunkte: {len(hist)} (letzte 72h) ──")
