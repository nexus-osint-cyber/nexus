"""
nexus_sar_learner.py – AIS-SAR Kreuzvalidierung + Self-Learning Klassifikator
===============================================================================
Lernt Schiffstypen aus SAR-Bildern, indem es AIS-bekannte Schiffe (Transponder AN)
mit SAR-Cluster-Signaturen abgleicht und einen RandomForest trainiert.

Datenfluss:
  1. SAR-Bild wird abgerufen (nexus_sar.py)
  2. AIS-Positionen zum gleichen Zeitstempel werden geholt (nexus_ais.py)
  3. Für jedes AIS-Schiff: Suche nächsten SAR-Cluster (≤ MATCH_RADIUS_M Abstand)
  4. Match → labeled example in SQLite speichern
  5. Ab MIN_EXAMPLES_TO_TRAIN Beispielen: RandomForest trainieren / updaten
  6. classify_dark_ship(): nutzt gelerntes Modell als 2. Meinung neben DB-Vergleich

Warum das funktioniert:
  - AIS Class-A sendet alle 2–10s → Positionsunsicherheit < 75m bei 15kn
  - Sentinel-1 Fine-Mode: 22m/px → 1-3px Matching-Toleranz reicht
  - Schiffstypen aus AIS (Typ 70–89) geben echte Ground-Truth-Labels
  - Nach 2-4 Wochen Betrieb: 50+ Beispiele → Modell übertrifft Datenbank-Guess
"""

import sqlite3
import json
import math
import logging
import pickle
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("nexus.sar_learner")

# ── Konfiguration ────────────────────────────────────────────────────────────
DB_PATH              = Path(__file__).parent / "nexus_sar_learning.db"
MODEL_PATH           = Path(__file__).parent / "nexus_sar_model.pkl"
MIN_EXAMPLES_TO_TRAIN = 50   # Ab 50 Paaren wird trainiert
MATCH_RADIUS_M        = 200  # Max. Abstand AIS-Position zu SAR-Cluster (Meter)
                              # 200m = ~9px bei 22m/px Fine-Mode, großzügig für Drift


# ═══════════════════════════════════════════════════════════════════════════════
# DATENBANK
# ═══════════════════════════════════════════════════════════════════════════════

def _get_db() -> sqlite3.Connection:
    """Gibt DB-Verbindung zurück, erstellt Schema falls nötig."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sar_labels (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts            TEXT    NOT NULL,
            region        TEXT    DEFAULT '',
            ship_type     TEXT    NOT NULL,
            ship_len_m    REAL,
            ship_wid_m    REAL,
            size_px       INTEGER,
            brightness    REAL,
            aspect_ratio  REAL,
            elongation    REAL,
            compactness   REAL,
            fill_ratio    REAL,
            rcs_class     TEXT,
            lat           REAL,
            lon           REAL,
            dist_m        REAL,
            mmsi          TEXT    DEFAULT '',
            imo           TEXT    DEFAULT '',
            scene_date    TEXT    DEFAULT '',
            verified      INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    return conn


# ═══════════════════════════════════════════════════════════════════════════════
# AIS ↔ SAR MATCHING
# ═══════════════════════════════════════════════════════════════════════════════

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Entfernung in Metern zwischen zwei GPS-Punkten (Haversine)."""
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(min(a, 1.0)))


def match_ais_to_sar(sar_result, ais_vessels: list) -> list:
    """
    Gleicht AIS-Schiffe mit SAR-Clustern ab.

    Args:
        sar_result:   Objekt aus nexus_sar.detect_ships() mit .ships und .scene_date
        ais_vessels:  Liste von AIS-Dicts (lat/lon/ship_type/length/width/mmsi)

    Returns:
        Liste von Matches: [{"cluster": dict, "ais": dict, "dist_m": float}, ...]

    Nur der nächstgelegene SAR-Cluster wird pro AIS-Schiff berücksichtigt.
    Ein Cluster kann von mehreren AIS-Schiffen gematcht werden (parallele Schiffe
    im gleichen Pixel werden in SAR nicht getrennt) – das ist akzeptabel.
    """
    if not sar_result or not getattr(sar_result, "ships", None) or not ais_vessels:
        return []

    sar_ships = sar_result.ships  # list[dict]: lat/lon/size_px/brightness/...
    matches   = []

    for ais in ais_vessels:
        a_lat = ais.get("lat") or ais.get("latitude")
        a_lon = ais.get("lon") or ais.get("longitude")
        if not a_lat or not a_lon:
            continue

        best_dist    = float("inf")
        best_cluster = None

        for cluster in sar_ships:
            c_lat = cluster.get("lat")
            c_lon = cluster.get("lon")
            if not c_lat or not c_lon:
                continue
            dist = _haversine_m(float(a_lat), float(a_lon), float(c_lat), float(c_lon))
            if dist < best_dist:
                best_dist    = dist
                best_cluster = cluster

        if best_cluster is not None and best_dist <= MATCH_RADIUS_M:
            ship_type = _normalize_ship_type(
                ais.get("ship_type") or ais.get("type_name") or ""
            )
            if not ship_type:
                continue  # unklassifizierter AIS-Typ → kein nutzbares Label

            matches.append({
                "cluster":   best_cluster,
                "ais":       ais,
                "dist_m":    round(best_dist, 1),
                "ship_type": ship_type,
            })
            log.debug(
                f"Match: {ship_type} MMSI={ais.get('mmsi','')} "
                f"→ SAR-Cluster {best_dist:.0f}m entfernt"
            )

    log.info(f"AIS-SAR Matching: {len(matches)} Paare aus "
             f"{len(ais_vessels)} AIS + {len(sar_ships)} SAR-Clustern.")
    return matches


def _normalize_ship_type(raw: str) -> str:
    """
    Normiert rohe AIS-Schiffstypen (englisch) auf NEXUS-Kategorien (deutsch).
    Gibt leeren String zurück bei unbekanntem Typ → Beispiel wird NICHT gespeichert.
    """
    if not raw:
        return ""
    r = raw.lower().strip()

    # Tanker-Familie
    if any(k in r for k in ["tanker", "oil", "chemical", "gas carrier",
                              "liquified", "asphalt", "bitumen"]):
        return "Tanker"

    # Frachter-Familie
    if any(k in r for k in ["cargo", "bulk", "container", "general cargo",
                              "refrigerated", "heavy load", "barge"]):
        return "Frachter"

    # Militär (AIS-Typ 35 = Military, aber auch "naval patrol" etc.)
    if any(k in r for k in ["naval", "warship", "military", "patrol vessel",
                              "coast guard", "law enforce", "anti-pollution"]):
        return "Kriegsschiff"

    # Versorger / Offshore
    if any(k in r for k in ["tug", "supply", "offshore", "anchor handling",
                              "platform", "crane", "dredging", "diving"]):
        return "Versorgungsschiff"

    # Passagier / Fähre
    if any(k in r for k in ["passenger", "cruise", "ferry", "ro-ro pax",
                              "high speed craft"]):
        return "Passagierschiff"

    # Fischerei
    if any(k in r for k in ["fishing", "trawler", "fish"]):
        return "Fischerboot"

    # Kleinfahrzeuge / Segel / Yacht
    if any(k in r for k in ["pleasure", "sailing", "yacht", "recreational"]):
        return "Kleinfahrzeug"

    # AIS numerische Codes (Type 70–79 = Cargo, 80–89 = Tanker, etc.)
    if r.isdigit():
        code = int(r)
        if 70 <= code <= 79:
            return "Frachter"
        if 80 <= code <= 89:
            return "Tanker"
        if code == 35:
            return "Kriegsschiff"
        if code in (31, 32, 52):
            return "Versorgungsschiff"
        if 60 <= code <= 69:
            return "Passagierschiff"

    return ""  # Unbekannt → nicht speichern


# ═══════════════════════════════════════════════════════════════════════════════
# BEISPIELE SPEICHERN
# ═══════════════════════════════════════════════════════════════════════════════

def store_matches(matches: list, region: str = "", scene_date: str = "") -> int:
    """
    Speichert AIS-SAR-Matches als labeled examples in SQLite.

    Returns: Anzahl neu gespeicherter Einträge.
    """
    if not matches:
        return 0

    conn    = _get_db()
    stored  = 0
    ts      = datetime.now(timezone.utc).isoformat()

    for m in matches:
        c         = m["cluster"]
        ais       = m["ais"]
        ship_type = m.get("ship_type") or _normalize_ship_type(
            ais.get("ship_type") or ais.get("type_name") or "")
        if not ship_type:
            continue

        # Schiffsdimensionen aus AIS (verschiedene Feldnamen je nach Quelle)
        length_m = (ais.get("length") or
                    (ais.get("to_bow", 0) or 0) + (ais.get("to_stern", 0) or 0) or
                    None)
        width_m  = (ais.get("width") or
                    (ais.get("to_port", 0) or 0) + (ais.get("to_starboard", 0) or 0) or
                    None)

        try:
            conn.execute("""
                INSERT INTO sar_labels
                    (ts, region, ship_type, ship_len_m, ship_wid_m,
                     size_px, brightness, aspect_ratio, elongation,
                     compactness, fill_ratio, rcs_class,
                     lat, lon, dist_m, mmsi, imo, scene_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                ts,
                region,
                ship_type,
                float(length_m) if length_m else None,
                float(width_m)  if width_m  else None,
                c.get("size_px"),
                c.get("brightness"),
                c.get("aspect_ratio"),
                c.get("elongation"),
                c.get("compactness"),
                c.get("fill_ratio"),
                c.get("rcs_class"),
                c.get("lat"),
                c.get("lon"),
                m.get("dist_m"),
                str(ais.get("mmsi", "")),
                str(ais.get("imo",  "")),
                scene_date,
            ))
            stored += 1
        except Exception as e:
            log.warning(f"store_matches: Fehler beim Speichern: {e}")

    conn.commit()
    conn.close()

    if stored:
        log.info(f"SAR-Learner: {stored} neue labeled examples gespeichert "
                 f"(Region={region}, Datum={scene_date}).")
    return stored


# ═══════════════════════════════════════════════════════════════════════════════
# STATISTIKEN
# ═══════════════════════════════════════════════════════════════════════════════

def get_stats() -> dict:
    """
    Gibt Lernstatistiken zurück.

    Returns dict mit:
      total_examples, by_type, last_match,
      model_accuracy, model_ready, examples_needed,
      model_classes, trained_at
    """
    conn = _get_db()

    total   = conn.execute("SELECT COUNT(*) FROM sar_labels").fetchone()[0]
    by_type = dict(conn.execute(
        "SELECT ship_type, COUNT(*) FROM sar_labels GROUP BY ship_type ORDER BY 2 DESC"
    ).fetchall())
    last_ts = conn.execute(
        "SELECT MAX(ts) FROM sar_labels"
    ).fetchone()[0]

    def _meta(key):
        row = conn.execute(
            "SELECT value FROM model_meta WHERE key=?", (key,)
        ).fetchone()
        return row[0] if row else None

    accuracy_str  = _meta("accuracy")
    classes_str   = _meta("classes")
    trained_at_str = _meta("trained_at")

    conn.close()

    return {
        "total_examples":  total,
        "by_type":         by_type,
        "last_match":      last_ts,
        "model_accuracy":  float(accuracy_str) if accuracy_str else None,
        "model_ready":     total >= MIN_EXAMPLES_TO_TRAIN and MODEL_PATH.exists(),
        "examples_needed": max(0, MIN_EXAMPLES_TO_TRAIN - total),
        "model_classes":   json.loads(classes_str) if classes_str else [],
        "trained_at":      trained_at_str,
    }


def format_stats_terminal() -> str:
    """Gibt formatierte Lernstatistiken für Terminal-Ausgabe zurück."""
    s = get_stats()
    lines = [
        "╔══ SAR-Learner: Lernstatus ══════════════════════════╗",
        f"║  Gespeicherte Beispiele : {s['total_examples']:>4}",
    ]
    if s["examples_needed"] > 0:
        lines.append(f"║  Noch benötigt          : {s['examples_needed']:>4} bis erstes Training")
    else:
        acc = s["model_accuracy"]
        lines.append(f"║  Modell-Genauigkeit     : {acc:.1%}" if acc else "║  Modell: trainiert")
        lines.append(f"║  Modell-Klassen         : {', '.join(s['model_classes'])}")
    if s["by_type"]:
        lines.append("║  Verteilung:")
        for t, n in list(s["by_type"].items())[:5]:
            bar = "█" * min(n, 20)
            lines.append(f"║    {t:<22} {n:>3}x {bar}")
    if s["last_match"]:
        lines.append(f"║  Letzter Match          : {s['last_match'][:19]}")
    lines.append("╚═════════════════════════════════════════════════════╝")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# TRAINING
# ═══════════════════════════════════════════════════════════════════════════════

def retrain_if_ready() -> Optional[float]:
    """
    Trainiert / aktualisiert RandomForest wenn >= MIN_EXAMPLES_TO_TRAIN Beispiele.

    Features: size_px, brightness, aspect_ratio, elongation, compactness
    Labels:   ship_type (normiert)

    Returns: Cross-Validation Accuracy (float 0–1) oder None wenn zu wenig Daten.
    """
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import cross_val_score
        from sklearn.preprocessing import LabelEncoder
        import numpy as np
    except ImportError:
        log.warning("scikit-learn nicht installiert – Training übersprungen.")
        return None

    conn = _get_db()
    rows = conn.execute("""
        SELECT ship_type, size_px, brightness, aspect_ratio, elongation, compactness
        FROM sar_labels
        WHERE size_px IS NOT NULL
          AND brightness IS NOT NULL
          AND aspect_ratio IS NOT NULL
          AND ship_type != ''
    """).fetchall()
    conn.close()

    if len(rows) < MIN_EXAMPLES_TO_TRAIN:
        log.info(f"SAR-Learner: {len(rows)}/{MIN_EXAMPLES_TO_TRAIN} – noch nicht genug.")
        return None

    import numpy as np

    labels   = [r[0] for r in rows]
    features = [
        [r[1] or 1, r[2] or 0, r[3] or 1.0, r[4] or 0.0, r[5] or 0.5]
        for r in rows
    ]

    le = LabelEncoder()
    y  = le.fit_transform(labels)
    X  = np.array(features, dtype=float)

    clf = RandomForestClassifier(
        n_estimators=150,
        random_state=42,
        class_weight="balanced",
        max_depth=10,
        min_samples_leaf=2,
    )

    # Cross-Validation nur wenn genug Klassen + Beispiele
    n_classes = len(set(labels))
    if n_classes >= 2 and len(rows) >= 20:
        cv_folds = min(5, min(len(rows) // n_classes, 5))
        cv_folds = max(cv_folds, 2)
        scores   = cross_val_score(clf, X, y, cv=cv_folds, scoring="accuracy")
        accuracy = float(scores.mean())
    else:
        accuracy = 0.0

    clf.fit(X, y)

    # Modell + Encoder speichern
    model_data = {"clf": clf, "le": le, "feature_names": [
        "size_px", "brightness", "aspect_ratio", "elongation", "compactness"
    ]}
    MODEL_PATH.write_bytes(pickle.dumps(model_data))

    # Metadaten in DB schreiben
    conn = _get_db()
    metas = [
        ("accuracy",   str(accuracy)),
        ("trained_at", datetime.now(timezone.utc).isoformat()),
        ("n_examples", str(len(rows))),
        ("n_classes",  str(n_classes)),
        ("classes",    json.dumps(list(le.classes_))),
    ]
    for k, v in metas:
        conn.execute("INSERT OR REPLACE INTO model_meta VALUES (?,?)", (k, v))
    conn.commit()
    conn.close()

    log.info(
        f"SAR-Learner: Modell trainiert – {len(rows)} Beispiele, "
        f"{n_classes} Klassen, CV-Accuracy={accuracy:.1%}"
    )
    return accuracy


# ═══════════════════════════════════════════════════════════════════════════════
# KLASSIFIKATION DUNKLER SCHIFFE
# ═══════════════════════════════════════════════════════════════════════════════

def classify_dark_ship(
    size_px:      float,
    brightness:   float,
    aspect_ratio: float = 1.0,
    elongation:   float = 0.0,
    compactness:  float = 0.5,
) -> Optional[dict]:
    """
    Klassifiziert ein dunkles Schiff (kein AIS) mit dem gelernten Modell.

    Args:
        size_px:      Cluster-Größe in Pixeln
        brightness:   Mittlere SAR-Helligkeit (0–255)
        aspect_ratio: Länge/Breite-Verhältnis
        elongation:   Elongations-Score (0=rund, 1=sehr länglich)
        compactness:  Kompaktheitsscore (0–1)

    Returns:
        {
          "ship_type": "Tanker",          # wahrscheinlichste Klasse
          "confidence": 0.73,             # Konfidenz
          "top3": [...],                  # top-3 Klassen mit Konfidenz
          "source": "SAR-Learner",
        }
        oder None wenn kein Modell vorhanden.
    """
    if not MODEL_PATH.exists():
        return None

    try:
        import numpy as np
        model_data = pickle.loads(MODEL_PATH.read_bytes())
        clf = model_data["clf"]
        le  = model_data["le"]

        X     = np.array([[size_px, brightness, aspect_ratio, elongation, compactness]])
        proba = clf.predict_proba(X)[0]
        top_idx = proba.argsort()[::-1][:3]

        top3 = [
            {"ship_type": le.classes_[i], "confidence": round(float(proba[i]), 3)}
            for i in top_idx
            if proba[i] > 0.05
        ]
        if not top3:
            return None

        return {
            "ship_type":  top3[0]["ship_type"],
            "confidence": top3[0]["confidence"],
            "top3":       top3,
            "source":     "SAR-Learner (AIS-trainiert)",
        }
    except Exception as e:
        log.warning(f"classify_dark_ship: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-PIPELINE (wird vom Live-Server aufgerufen)
# ═══════════════════════════════════════════════════════════════════════════════

def run_learning_cycle(region: str = "Nordsee") -> dict:
    """
    Vollständiger Lernzyklus:
      SAR abrufen → AIS matchen → speichern → ggf. Modell trainieren.

    Wird in nexus_live_server.py beim SAR-Refresh aufgerufen.
    Schlägt still fehl wenn Module nicht verfügbar (graceful degradation).

    Returns: Zusammenfassung als dict.
    """
    summary = {
        "region":         region,
        "sar_ships":      0,
        "ais_vessels":    0,
        "matches":        0,
        "total_examples": 0,
        "retrained":      False,
        "accuracy":       None,
        "error":          None,
    }

    try:
        from nexus_sar import detect_ships        # type: ignore
        from nexus_ais import get_ais_vessels     # type: ignore

        sar_result  = detect_ships(region)
        if not sar_result or not getattr(sar_result, "ships", None):
            summary["error"] = "Kein SAR-Ergebnis"
            return summary

        summary["sar_ships"] = len(sar_result.ships)
        scene_date = getattr(sar_result, "scene_date", "")

        ais_vessels = get_ais_vessels(region)
        if not ais_vessels:
            summary["error"] = "Keine AIS-Daten"
            return summary

        summary["ais_vessels"] = len(ais_vessels)

        matches = match_ais_to_sar(sar_result, ais_vessels)
        stored  = store_matches(matches, region=region, scene_date=scene_date)
        summary["matches"] = stored

        stats = get_stats()
        summary["total_examples"] = stats["total_examples"]

        # Modell nach jedem Batch neu trainieren
        if stats["total_examples"] >= MIN_EXAMPLES_TO_TRAIN:
            acc = retrain_if_ready()
            if acc is not None:
                summary["retrained"] = True
                summary["accuracy"]  = round(acc, 3)

    except ImportError as e:
        summary["error"] = f"Import-Fehler: {e}"
    except Exception as e:
        log.error(f"run_learning_cycle: {e}", exc_info=True)
        summary["error"] = str(e)

    return summary


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE TEST
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print("=== SAR-Learner Status ===")
    print(format_stats_terminal())

    # Test: classify mit Dummy-Daten (falls Modell vorhanden)
    result = classify_dark_ship(size_px=8, brightness=180,
                                 aspect_ratio=3.2, elongation=0.6)
    if result:
        print(f"\nTest-Klassifikation (8px, 180 brightness, AR=3.2):")
        print(f"  Typ: {result['ship_type']} ({result['confidence']:.0%})")
        for t in result["top3"]:
            print(f"  {t['confidence']:.0%} {t['ship_type']}")
    else:
        s = get_stats()
        print(f"\nKein Modell vorhanden. Noch {s['examples_needed']} Beispiele nötig.")
        print("Das Modell trainiert automatisch sobald NEXUS läuft und")
        print(f"Sentinel-1 + AIS-Daten aus derselben Region vorliegen.")
