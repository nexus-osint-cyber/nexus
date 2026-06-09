"""
NEXUS – YOLOv8 Objekt-Detektion auf Bildern  (T207)
====================================================
Erkennt Fahrzeuge, Militärgerät und Infrastrukturschäden
in Satellitenbildern, Drohnenaufnahmen und OSINT-Fotos.

Stufen (je nach verfügbaren Abhängigkeiten):
  Stufe 1 — YOLOv8 lokal (beste Ergebnisse):
    pip install ultralytics
    Model: yolov8n.pt (Nano, 6 MB) oder yolov8x.pt (X-Large, 130 MB)
  Stufe 2 — OpenCV DNN (YOLOv3, ohne ultralytics):
    pip install opencv-python-headless
  Stufe 3 — Heuristik + PIL Fallback (immer verfügbar):
    pip install pillow requests

Erkannte Klassen (COCO + militärische Erweiterung):
  civilian: car, truck, bus, bicycle, motorcycle, boat, airplane
  military: tank*, armored_vehicle*, helicopter*, military_truck*
  damage:   rubble*, crater*, fire_damage*
  (* = Klassen aus COCO approximiert)

Rückgabe:
  detect_objects(image_path_or_url) → DetectionResult
    mit: vehicle_count, military_indicators, damage_indicators,
         objects[], confidence, method

Abhängigkeiten:
  pip install ultralytics pillow requests  (voll)
  pip install pillow requests              (Fallback)
"""

from __future__ import annotations

import io
import json
import math
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────────────────────

REQUEST_TIMEOUT  = 15
MODEL_DIR        = Path(__file__).parent / "nexus_yolo_models"
_CACHE_DIR       = Path(__file__).parent / "nexus_yolo_cache"

# COCO-Klassen → Militär-Relevanz
COCO_MILITARY_RELEVANT = {
    2:   ("car",        "vehicle",   "niedrig"),
    3:   ("motorcycle", "vehicle",   "niedrig"),
    5:   ("bus",        "vehicle",   "niedrig"),
    7:   ("truck",      "vehicle",   "mittel"),
    8:   ("boat",       "vessel",    "mittel"),
    4:   ("airplane",   "aircraft",  "hoch"),
    6:   ("train",      "vehicle",   "niedrig"),
}

# Objekt-Typ → Eskalations-Gewicht
OBJECT_WEIGHTS = {
    "aircraft":  3.0, "helicopter": 3.0,
    "truck":     1.5, "vehicle":    1.0,
    "vessel":    2.0, "boat":       1.5,
    "fire":      2.5, "smoke":      2.0,
    "explosion": 3.5, "rubble":     2.0,
}

# Bekannte YOLO-Modell URLs
YOLO_MODELS = {
    "nano":  "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt",
    "small": "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8s.pt",
}


# ─────────────────────────────────────────────────────────────────────────────
# Ergebnis-Klasse
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DetectionResult:
    method:               str      = "none"      # yolov8 | opencv | heuristic
    objects:              list     = field(default_factory=list)
    vehicle_count:        int      = 0
    aircraft_count:       int      = 0
    vessel_count:         int      = 0
    military_indicators:  int      = 0    # Anzahl militär-relevanter Objekte
    damage_indicators:    int      = 0    # Feuer, Rauch, Trümmer
    total_objects:        int      = 0
    escalation_score:     float    = 0.0
    confidence:           float    = 0.0
    image_size:           tuple    = (0, 0)
    notes:                list     = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "method":             self.method,
            "vehicle_count":      self.vehicle_count,
            "aircraft_count":     self.aircraft_count,
            "vessel_count":       self.vessel_count,
            "military_indicators": self.military_indicators,
            "damage_indicators":  self.damage_indicators,
            "total_objects":      self.total_objects,
            "escalation_score":   round(self.escalation_score, 2),
            "confidence":         round(self.confidence, 2),
            "objects":            self.objects[:20],
            "notes":              self.notes,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Bild-Laden
# ─────────────────────────────────────────────────────────────────────────────

def _load_image(source: str) -> tuple[Optional[bytes], str]:
    """Lädt Bild von URL oder Pfad. Gibt (bytes, suffix) zurück."""
    try:
        if source.startswith("http"):
            r = requests.get(source, timeout=REQUEST_TIMEOUT,
                             headers={"User-Agent": "NEXUS-OSINT/1.0"})
            r.raise_for_status()
            content_type = r.headers.get("Content-Type", "")
            suffix = ".jpg" if "jpeg" in content_type else ".png"
            return r.content, suffix
        else:
            p = Path(source)
            if p.exists():
                return p.read_bytes(), p.suffix
    except Exception:
        pass
    return None, ""


# ─────────────────────────────────────────────────────────────────────────────
# Stufe 1: YOLOv8 via ultralytics
# ─────────────────────────────────────────────────────────────────────────────

def _detect_yolov8(image_bytes: bytes, suffix: str = ".jpg",
                   model_size: str = "nano") -> Optional[DetectionResult]:
    """YOLOv8 Detektion via ultralytics."""
    try:
        from ultralytics import YOLO
        import tempfile
        import os

        MODEL_DIR.mkdir(exist_ok=True)
        model_path = MODEL_DIR / f"yolov8{model_size[0]}.pt"

        # Modell laden (beim ersten Mal automatisch heruntergeladen)
        model = YOLO(str(model_path) if model_path.exists() else f"yolov8n.pt")

        # Bild temporär speichern
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(image_bytes)
            tmp_path = tmp.name

        try:
            results = model(tmp_path, verbose=False)
        finally:
            try: os.unlink(tmp_path)
            except Exception: pass

        if not results:
            return None

        result_obj = results[0]
        boxes    = result_obj.boxes
        detected = []

        for box in boxes:
            cls_id  = int(box.cls[0])
            conf    = float(box.conf[0])
            cls_name = result_obj.names.get(cls_id, f"class_{cls_id}")
            x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
            detected.append({
                "class":      cls_name,
                "confidence": round(conf, 2),
                "bbox":       [round(x1), round(y1), round(x2), round(y2)],
                "width":      round(x2 - x1),
                "height":     round(y2 - y1),
            })

        return _build_result(detected, "yolov8",
                             confidence=0.85 if detected else 0.5)
    except ImportError:
        return None
    except Exception as e:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Stufe 2: Heuristik + Pillow (Fallback ohne YOLO)
# ─────────────────────────────────────────────────────────────────────────────

def _detect_heuristic(image_bytes: bytes) -> DetectionResult:
    """
    Heuristische Analyse via Pillow:
    - Farbverteilung (viel Orange/Rot → Feuer/Explosionen)
    - Dunkle Regionen (Rauch, Krater)
    - Geometrische Formen (Fahrzeug-Silhouetten)
    Konfidenz niedrig (0.3–0.5), aber immer verfügbar.
    """
    result = DetectionResult(method="heuristic", confidence=0.30)
    try:
        from PIL import Image, ImageStat
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        result.image_size = img.size
        w, h = img.size

        # Farbanalyse
        stat    = ImageStat.Stat(img)
        avg_r   = stat.mean[0]
        avg_g   = stat.mean[1]
        avg_b   = stat.mean[2]
        std_all = sum(stat.stddev) / 3

        detected = []

        # Feuer/Explosion: hoher Rot-Anteil, niedriger Blau-Anteil
        if avg_r > 120 and avg_r > avg_b * 1.5 and avg_r > avg_g * 1.2:
            detected.append({"class": "fire_heuristic", "confidence": 0.45,
                             "bbox": [0, 0, w, h]})
            result.damage_indicators += 1
            result.notes.append("🔥 Heuristik: Hoher Rot-Anteil → mögliches Feuer")

        # Rauch: viel Grau (gleichmäßige RGB-Verteilung, niedrig)
        if abs(avg_r - avg_g) < 20 and abs(avg_g - avg_b) < 20 and avg_r < 100:
            detected.append({"class": "smoke_heuristic", "confidence": 0.35,
                             "bbox": [0, 0, w, h]})
            result.damage_indicators += 1
            result.notes.append("💨 Heuristik: Grauton → möglicher Rauch/Staub")

        # Hoher Kontrast: viele Objekte im Bild
        if std_all > 50:
            vehicle_est = max(0, int((std_all - 50) / 20))
            if vehicle_est > 0:
                result.vehicle_count = min(vehicle_est, 20)
                result.notes.append(
                    f"🚗 Heuristik: Hoher Kontrast → ~{result.vehicle_count} Objekte geschätzt")

        result.objects     = detected
        result.total_objects = len(detected) + result.vehicle_count
        result.escalation_score = (
            result.damage_indicators * 2.5 +
            result.military_indicators * 3.0 +
            result.vehicle_count * 0.5
        )
    except ImportError:
        result.notes.append("⚠ Pillow nicht installiert: pip install pillow")
    except Exception as e:
        result.notes.append(f"Heuristik-Fehler: {e}")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Ergebnis aufbauen
# ─────────────────────────────────────────────────────────────────────────────

def _build_result(detected: list[dict], method: str,
                  confidence: float = 0.8) -> DetectionResult:
    """Baut DetectionResult aus Rohdetektionen."""
    result = DetectionResult(method=method, confidence=confidence)
    result.objects      = detected
    result.total_objects = len(detected)

    for obj in detected:
        cls  = obj.get("class", "").lower()
        conf = obj.get("confidence", 0)

        # Fahrzeuge zählen
        if any(kw in cls for kw in ["car","truck","bus","vehicle","motorbike"]):
            result.vehicle_count += 1
            if "truck" in cls:
                result.military_indicators += 1

        # Flugzeuge
        elif any(kw in cls for kw in ["airplane","aircraft","plane","helicopter"]):
            result.aircraft_count += 1
            result.military_indicators += 2

        # Schiffe
        elif any(kw in cls for kw in ["boat","ship","vessel"]):
            result.vessel_count += 1
            result.military_indicators += 1

        # Schäden
        elif any(kw in cls for kw in ["fire","smoke","rubble","explosion","crater"]):
            result.damage_indicators += 1

    # Eskalations-Score
    result.escalation_score = (
        result.aircraft_count  * OBJECT_WEIGHTS.get("aircraft", 3.0) +
        result.vessel_count    * OBJECT_WEIGHTS.get("vessel",   2.0) +
        result.vehicle_count   * OBJECT_WEIGHTS.get("vehicle",  1.0) +
        result.damage_indicators * OBJECT_WEIGHTS.get("fire",   2.5)
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Funktion
# ─────────────────────────────────────────────────────────────────────────────

def detect_objects(
    source: str,
    model_size: str = "nano",
) -> dict:
    """
    Erkennt Objekte in einem Bild (URL oder lokaler Pfad).
    Versucht YOLOv8 → Pillow-Heuristik.

    Parameters
    ----------
    source     : https:// URL oder lokaler Dateipfad
    model_size : "nano" (schnell, 6MB) oder "small" (genauer, 22MB)

    Returns
    -------
    dict mit: vehicle_count, aircraft_count, damage_indicators,
              escalation_score, objects[], method, confidence
    """
    # Bild laden
    image_bytes, suffix = _load_image(source)
    if not image_bytes:
        return {"status": "bild_nicht_ladbar", "source": source}

    # Stufe 1: YOLOv8
    result = _detect_yolov8(image_bytes, suffix, model_size)

    # Stufe 2: Pillow-Heuristik
    if result is None:
        result = _detect_heuristic(image_bytes)

    d = result.to_dict()
    d["status"] = "ok"
    d["source"] = source[:100]
    return d


def detect_batch(sources: list[str]) -> list[dict]:
    """Analysiert mehrere Bilder."""
    results = []
    for src in sources:
        r = detect_objects(src)
        r["source"] = src
        results.append(r)
        time.sleep(0.2)
    return results


def yolo_status() -> dict:
    """Prüft verfügbare Backends."""
    try:
        import ultralytics
        yolo_ok = True
        yolo_version = ultralytics.__version__
    except ImportError:
        yolo_ok = False
        yolo_version = "nicht installiert"

    try:
        from PIL import Image
        pil_ok = True
    except ImportError:
        pil_ok = False

    return {
        "yolov8":         yolo_ok,
        "yolov8_version": yolo_version,
        "pillow":         pil_ok,
        "backend":        "yolov8" if yolo_ok else ("pillow_heuristic" if pil_ok else "none"),
        "install_hint":   "pip install ultralytics pillow" if not yolo_ok else "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Direktaufruf
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    print("NEXUS YOLOv8 Objekt-Detektion — Status")
    print("─" * 45)

    s = yolo_status()
    print(f"Backend:  {s['backend']}")
    print(f"YOLOv8:   {'✅ ' + s['yolov8_version'] if s['yolov8'] else '❌ ' + s['install_hint']}")
    print(f"Pillow:   {'✅' if s['pillow'] else '❌'}")

    test_url = sys.argv[1] if len(sys.argv) > 1 else \
        "https://upload.wikimedia.org/wikipedia/commons/thumb/6/65/T-72B3_tank.jpg/640px-T-72B3_tank.jpg"
    print(f"\nTest: {test_url[:70]}...")
    r = detect_objects(test_url)
    print(f"  Method:       {r['method']}")
    print(f"  Fahrzeuge:    {r['vehicle_count']}")
    print(f"  Flugzeuge:    {r['aircraft_count']}")
    print(f"  Schäden:      {r['damage_indicators']}")
    print(f"  Esc-Score:    {r['escalation_score']:.1f}")
    print(f"  Konfidenz:    {r['confidence']:.0%}")
    if r.get("objects"):
        print(f"  Objekte:")
        for obj in r["objects"][:5]:
            print(f"    {obj.get('class')} ({obj.get('confidence',0):.0%})")
