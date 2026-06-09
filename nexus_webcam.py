"""
NEXUS – Öffentliche Kameras + Bewegungsdetektion  (Ebene 4 / Modul 4.10)
=========================================================================
Überwacht öffentliche IP-Kameras und Verkehrskameras auf ungewöhnliche
Aktivität. Nutzt PIL/Pillow (immer verfügbar) für Bewegungsdetektion.
Optional: OpenCV für höhere Präzision (pip install opencv-python-headless).
Kein OpenCV nötig – PIL ist der Standard!

Quellen:
  • Insecam.org  – Katalog öffentlicher IP-Kameras nach Land/Stadt
  • Eigene Kamera-URLs (NEXUS_WEBCAM_URLS in config.py)
  • Earthcam.com Public-Feeds (ausgewählte Städte)

Öffentliche API:
  check_webcam(url, label)         → WebcamAlert | None
  webcam_for_map(region)           → list[dict]   (Karten-Marker)
  webcam_summary(alerts)           → str

Config (config.py):
  NEXUS_WEBCAM_URLS = [
    {"url": "http://...", "lat": 48.1, "lon": 37.5, "label": "Checkpoint"},
  ]
  WEBCAM_MOTION_THRESHOLD = 1.5    # % veränderte Pixel (Default)
  WEBCAM_NIGHT_HOURS      = (22, 5) # Stunden in denen Bewegung verdächtiger ist
"""

from __future__ import annotations

import io
import json
import math
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────────────────────

try:
    import config
    _USER_CAMS       = getattr(config, "NEXUS_WEBCAM_URLS", [])
    _MOTION_THRESH   = float(getattr(config, "WEBCAM_MOTION_THRESHOLD", 1.5))
    _NIGHT_H         = getattr(config, "WEBCAM_NIGHT_HOURS", (22, 5))
except ImportError:
    _USER_CAMS     = []
    _MOTION_THRESH = 1.5
    _NIGHT_H       = (22, 5)

# Frame-Cache: url → (timestamp, frame_bytes)
_frame_cache: dict[str, tuple[float, bytes]] = {}
FRAME_CACHE_TTL = 180  # Sekunden


# ─────────────────────────────────────────────────────────────────────────────
# Datenklasse
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WebcamAlert:
    url:           str
    label:         str   = ""
    lat:           Optional[float] = None
    lon:           Optional[float] = None
    motion_pct:    float = 0.0    # Prozent veränderter Pixel
    night_anomaly: bool  = False  # Bewegung zu ungewöhnlicher Uhrzeit
    confidence:    float = 0.4
    snapshot_b64:  str   = ""    # Base64 aktuelles Frame (optional)
    ts:            float = field(default_factory=time.time)


# ─────────────────────────────────────────────────────────────────────────────
# Frame laden
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_frame(url: str, timeout: int = 8) -> Optional[bytes]:
    """Lädt ein JPEG-Frame von einer Kamera-URL."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; NEXUS-OSINT/1.0)",
                "Accept":     "image/jpeg,image/*",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ct = resp.headers.get("Content-Type", "")
            data = resp.read(2 * 1024 * 1024)  # Max 2MB
            if len(data) < 500:
                return None
            return data
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Bewegungsdetektion (ohne OpenCV – fallback zu PIL)
# ─────────────────────────────────────────────────────────────────────────────

def _motion_opencv(frame1: bytes, frame2: bytes, threshold: float) -> float:
    """OpenCV-basierte Bewegungsdetektion. Gibt % veränderte Pixel zurück."""
    try:
        import cv2
        import numpy as np

        img1 = cv2.imdecode(np.frombuffer(frame1, np.uint8), cv2.IMREAD_GRAYSCALE)
        img2 = cv2.imdecode(np.frombuffer(frame2, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img1 is None or img2 is None:
            return 0.0

        # Auf gleiche Größe skalieren
        h1, w1 = img1.shape
        h2, w2 = img2.shape
        if (h1, w1) != (h2, w2):
            img2 = cv2.resize(img2, (w1, h1))

        # Differenz
        diff = cv2.absdiff(img1, img2)
        _, mask = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)

        # Rauschen entfernen
        kernel = np.ones((3,3), np.uint8)
        mask   = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        changed_pct = (np.sum(mask > 0) / mask.size) * 100
        return round(changed_pct, 2)
    except ImportError:
        return _motion_pil(frame1, frame2)
    except Exception:
        return 0.0


def _motion_pil(frame1: bytes, frame2: bytes) -> float:
    """PIL-Fallback für Bewegungsdetektion (weniger präzise)."""
    try:
        from PIL import Image, ImageChops
        import struct

        img1 = Image.open(io.BytesIO(frame1)).convert("L").resize((160, 120))
        img2 = Image.open(io.BytesIO(frame2)).convert("L").resize((160, 120))
        diff = ImageChops.difference(img1, img2)

        pixels = list(diff.getdata())
        changed = sum(1 for p in pixels if p > 25)
        return round((changed / len(pixels)) * 100, 2)
    except Exception:
        return 0.0


def _compute_motion(frame1: bytes, frame2: bytes, threshold: float) -> float:
    """
    Bewegungsdetektion – PIL ist primär (immer verfügbar).
    OpenCV wird als optionaler Boost genutzt wenn installiert.
    Installation: pip install opencv-python-headless --break-system-packages
    """
    try:
        import cv2  # noqa: F401
        # OpenCV verfügbar → präziser
        return _motion_opencv(frame1, frame2, threshold)
    except ImportError:
        # PIL Fallback – kein extra Dep nötig
        return _motion_pil(frame1, frame2)


# ─────────────────────────────────────────────────────────────────────────────
# Nacht-Anomalie-Check
# ─────────────────────────────────────────────────────────────────────────────

def _is_night_anomaly() -> bool:
    """Gibt True zurück wenn aktuelle Uhrzeit in konfigurierten Nachtstunden liegt."""
    hour = time.gmtime().tm_hour
    start, end = _NIGHT_H
    if start > end:  # z.B. 22-05: übernacht
        return hour >= start or hour < end
    return start <= hour < end


# ─────────────────────────────────────────────────────────────────────────────
# Einzelne Kamera prüfen
# ─────────────────────────────────────────────────────────────────────────────

def check_webcam(
    url:   str,
    label: str   = "",
    lat:   Optional[float] = None,
    lon:   Optional[float] = None,
) -> Optional[WebcamAlert]:
    """
    Prüft eine einzelne Kamera auf Bewegung.
    Vergleicht aktuelles Frame mit gecachtem Referenz-Frame.
    """
    if not url:
        return None

    now = time.time()
    cache_entry = _frame_cache.get(url)

    # Neues Frame laden
    new_frame = _fetch_frame(url)
    if not new_frame:
        return None

    if cache_entry is None or (now - cache_entry[0]) > FRAME_CACHE_TTL:
        # Kein Referenz-Frame → speichern und abwarten
        _frame_cache[url] = (now, new_frame)
        return None

    prev_ts, prev_frame = cache_entry

    # Bewegung berechnen
    motion_pct = _compute_motion(prev_frame, new_frame, _MOTION_THRESH)

    # Neues Frame als Referenz speichern
    _frame_cache[url] = (now, new_frame)

    if motion_pct < _MOTION_THRESH:
        return None  # Keine signifikante Bewegung

    is_night = _is_night_anomaly()
    # Konfidenz-Score
    conf = min(0.90, 0.3 + (motion_pct / 100.0) * 0.5 + (0.2 if is_night else 0.0))

    import base64
    snap_b64 = base64.b64encode(new_frame[:100_000]).decode("ascii")  # Max 100KB

    return WebcamAlert(
        url           = url,
        label         = label or f"Kamera {url[:30]}",
        lat           = lat,
        lon           = lon,
        motion_pct    = motion_pct,
        night_anomaly = is_night,
        confidence    = round(conf, 2),
        snapshot_b64  = snap_b64,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Insecam-Suche (Region → bekannte öffentliche Kameras)
# ─────────────────────────────────────────────────────────────────────────────

_INSECAM_COUNTRY = {
    "ukraine":     "UA",
    "russland":    "RU",
    "russia":      "RU",
    "israel":      "IL",
    "syrien":      "SY",
    "lebanon":     "LB",
    "libanon":     "LB",
    "iran":        "IR",
    "jemen":       "YE",
    "yemen":       "YE",
}

def _get_insecam_urls(region: str, max_cams: int = 10) -> list[dict]:
    """
    Versucht Kamera-URLs von insecam.org zu laden.
    Gibt Liste mit {url, lat, lon, label} zurück.
    """
    country = None
    for key, code in _INSECAM_COUNTRY.items():
        if key in region.lower():
            country = code
            break
    if not country:
        return []

    try:
        url = f"https://www.insecam.org/en/bycountry/{country}/?page=1"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read(100_000).decode("utf-8", errors="ignore")

        # Kamera-URLs aus HTML extrahieren
        import re
        img_urls = re.findall(
            r'src=["\']?(http://\d+\.\d+\.\d+\.\d+(?::\d+)?/(?:[\w/\-.?=&]*)?)["\']?',
            html
        )
        cams = []
        for u in list(dict.fromkeys(img_urls))[:max_cams]:
            cams.append({"url": u, "lat": None, "lon": None, "label": f"Insecam ({country})"})
        return cams
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Funktion
# ─────────────────────────────────────────────────────────────────────────────

def webcam_for_map(region: str = "") -> list[dict]:
    """
    Prüft alle konfigurierten + automatisch gefundenen Kameras.
    Gibt Karten-Marker für Bewegungs-Alerts zurück.
    """
    # Kamera-Quellen zusammenstellen
    cams = list(_USER_CAMS)  # User-konfigurierte Kameras aus config.py
    if region:
        cams.extend(_get_insecam_urls(region, max_cams=8))

    if not cams:
        return []

    alerts = []
    for cam in cams[:20]:  # Max 20 Kameras prüfen
        try:
            alert = check_webcam(
                url   = cam.get("url", ""),
                label = cam.get("label", ""),
                lat   = cam.get("lat"),
                lon   = cam.get("lon"),
            )
            if alert:
                alerts.append(alert)
        except Exception:
            pass
        time.sleep(0.3)

    markers = []
    for a in alerts:
        col  = "#ff0044" if a.night_anomaly else "#ff8800"
        icon = "🔴" if a.night_anomaly else "📹"
        title = f"{icon} Bewegung: {a.label[:30]}"
        if a.night_anomaly:
            title += " ⚠ NACHTS"
        markers.append({
            "lat":          a.lat,
            "lon":          a.lon,
            "title":        title,
            "text":         f"Bewegung: {a.motion_pct:.1f}% Pixel verändert",
            "motion_pct":   a.motion_pct,
            "night_anomaly":a.night_anomaly,
            "confidence":   a.confidence,
            "color":        col,
            "icon":         icon,
            "source":       "webcam",
            "image_b64":    a.snapshot_b64[:500] if a.snapshot_b64 else "",
            "cam_url":      a.url,
        })
    return markers


def webcam_summary(alerts: list[WebcamAlert]) -> str:
    if not alerts:
        return ""
    night_n = sum(1 for a in alerts if a.night_anomaly)
    lines = [f"[WEBCAM] {len(alerts)} Bewegungs-Alerts ({night_n} nachts):\n"]
    for i, a in enumerate(alerts[:6], 1):
        night_s = " ⚠ NACHT" if a.night_anomaly else ""
        lines.append(
            f"  {i}. {a.label[:30]} Motion:{a.motion_pct:.1f}%"
            f"{night_s} Konf:{a.confidence:.0%}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    print("nexus_webcam.py – Teste mit User-Kameras aus config.py")
    print(f"  Konfigurierte Kameras: {len(_USER_CAMS)}")
    if _USER_CAMS:
        cam = _USER_CAMS[0]
        print(f"  Teste: {cam.get('url','?')[:60]}")
        alert = check_webcam(cam.get("url",""), cam.get("label","Test"))
        if alert:
            print(f"  Bewegung erkannt: {alert.motion_pct:.1f}%")
        else:
            print("  Kein Frame-Vergleich möglich (erstes Check-in, braucht 2 Polls)")
    else:
        print("  Keine Kameras konfiguriert.")
        print("  Tipp: NEXUS_WEBCAM_URLS in config.py setzen:")
        print('  NEXUS_WEBCAM_URLS = [{"url":"http://IP/snapshot.jpg","lat":48.1,"lon":37.5,"label":"Test"}]')
