"""
NEXUS – Vision Intelligence  (Ebene 4 / Modul 4.7)
===================================================
Analysiert Bilder aus OSINT-Quellen (Telegram, Social Media, Web)
mit lokalem LLaVA-Vision-Modell via Ollama (kein API-Key, kein Cloud).

Extrahiert:
  • Fahrzeugtypen    (T-72, Bradley, BMP, LKW, Pickup, ...)
  • Einheitsmarkierungen (Z/V/O-Symbole, Nummern, NATO-Taktikzeichen)
  • Waffensysteme    (Lancet, Shahed, Artillerie, Raketen, ...)
  • Schadensgrad     (intakt / beschädigt / zerstört / Brand)
  • Geländetyp       (Stadt, Feld, Wald, Küste, Wüste)
  • Personalanzahl   (grob geschätzt)
  • Verdachts-GPS    aus Bildkontext (Schilder, Gebäude)

Öffentliche API:
  analyze_image(url_or_path, region)  → VisionHit | None
  vision_for_map(articles, region)    → list[dict]  (Karten-Marker)
  vision_summary(hits)                → str          (LLM-Kontext)

Abhängigkeiten (alle optional, graceful degradation):
  pip install ollama pillow requests imagehash
  Ollama: ollama pull llava:7b
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse


# ─────────────────────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────────────────────

OLLAMA_URL    = "http://localhost:11434"
VISION_MODEL  = "llava:7b"         # Alternativ: llava:13b, llava-phi3, moondream
TIMEOUT_S     = 45
MAX_IMG_BYTES = 8 * 1024 * 1024    # 8 MB Limit

# Bilder-Cache (Hash → VisionHit) – vermeidet Dopple-Analysen
_vision_cache: dict[str, dict] = {}

# ─────────────────────────────────────────────────────────────────────────────
# Datenklasse
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class VisionHit:
    url:            str
    vehicles:       list[str]   = field(default_factory=list)   # ["T-72", "BMP-2"]
    unit_markings:  list[str]   = field(default_factory=list)   # ["Z", "47", ...]
    weapons:        list[str]   = field(default_factory=list)   # ["Lancet", "Artillerie"]
    weapon_cat:     str         = ""                             # drone/missile/arty/armor
    damage:         str         = "unbekannt"                   # intakt/beschädigt/zerstört
    terrain:        str         = ""                             # Stadt/Feld/Wald
    personnel:      int         = 0                              # geschätzte Anzahl
    geo_hint:       str         = ""                             # Ort-Hinweis aus Bild
    summary:        str         = ""                             # LLM-Kurzfassung (1 Satz)
    confidence:     float       = 0.5
    military:       bool        = False                          # Militärrelevant?
    img_hash:       str         = ""
    source:         str         = ""
    ts:             float       = field(default_factory=time.time)

    # Geo (wenn durch nexus_geolocate befüllt)
    lat:            Optional[float] = None
    lon:            Optional[float] = None
    coord_method:   str         = ""


# ─────────────────────────────────────────────────────────────────────────────
# LLaVA-Prompt
# ─────────────────────────────────────────────────────────────────────────────

_PROMPT = """You are a military OSINT analyst. Analyze this image and respond ONLY with valid JSON.

Identify and return:
{
  "military": true/false,
  "vehicles": ["list of vehicle types, e.g. T-72, BMP, truck, pickup"],
  "unit_markings": ["visible symbols/numbers on vehicles, e.g. Z, V, 47, white cross"],
  "weapons": ["visible weapon systems, e.g. Lancet drone, D-30 howitzer, MANPADS"],
  "weapon_cat": "drone|missile|arty|armor|infantry|air_def|unknown",
  "damage": "intact|damaged|destroyed|burning|unknown",
  "terrain": "urban|rural|forest|coastal|desert|industrial|unknown",
  "personnel": 0,
  "geo_hint": "any visible location clues: street signs, buildings, landmarks (or empty string)",
  "summary": "one sentence max 20 words describing what is visible",
  "confidence": 0.0-1.0
}

If no military content visible: set military=false, confidence<0.3.
Return ONLY the JSON object, no other text."""


# ─────────────────────────────────────────────────────────────────────────────
# Bild laden + hashen
# ─────────────────────────────────────────────────────────────────────────────

def _load_image_b64(url_or_path: str) -> Optional[tuple[str, str]]:
    """
    Lädt Bild von URL oder Pfad, gibt (base64_string, sha256_hash) zurück.
    Respektiert MAX_IMG_BYTES Limit.
    """
    try:
        if url_or_path.startswith(("http://", "https://")):
            req = urllib.request.Request(
                url_or_path,
                headers={"User-Agent": "Mozilla/5.0 NEXUS-OSINT/1.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if not any(t in content_type for t in ["image", "jpeg", "png", "webp"]):
                    return None
                data = resp.read(MAX_IMG_BYTES)
        else:
            with open(url_or_path, "rb") as f:
                data = f.read(MAX_IMG_BYTES)

        if len(data) < 1000:  # Zu klein = kein echtes Bild
            return None

        img_hash = hashlib.sha256(data).hexdigest()[:16]
        b64 = base64.b64encode(data).decode("ascii")
        return b64, img_hash

    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Ollama LLaVA Aufruf
# ─────────────────────────────────────────────────────────────────────────────

def _ollama_vision(b64_image: str) -> Optional[dict]:
    """Ruft Ollama LLaVA auf und gibt geparsten JSON-Dict zurück."""
    payload = json.dumps({
        "model":  VISION_MODEL,
        "prompt": _PROMPT,
        "images": [b64_image],
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 512},
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = json.loads(resp.read())
            response_text = body.get("response", "")

            # JSON aus Antwort extrahieren
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
    except Exception:
        pass
    return None


def _ollama_available() -> bool:
    """Prüft ob Ollama läuft und LLaVA verfügbar ist."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            models = [m.get("name", "") for m in (data.get("models") or [])]
            return any("llava" in m or "moondream" in m for m in models)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Waffen-Kategorisierung
# ─────────────────────────────────────────────────────────────────────────────

_WCAT_KEYS = {
    "drone":    ["lancet", "shahed", "fpv", "uav", "бпла", "bayraktar", "orlan", "geran"],
    "missile":  ["missile", "rocket", "kalibr", "iskander", "kinzhal", "kh-"],
    "arty":     ["howitzer", "artillery", "cannon", "d-30", "d-20", "pzh", "caesar", "mlrs",
                 "grad", "himars", "uragan", "smerch"],
    "armor":    ["t-72", "t-80", "t-90", "bmp", "btr", "leopard", "abrams", "bradley",
                 "marder", "apc", "ifv", "tank"],
    "air_def":  ["s-300", "s-400", "buk", "tor", "pantsir", "patriot", "nasams", "iris-t"],
    "infantry": ["soldier", "troops", "personnel", "infantry", "special forces"],
}

def _categorize_weapons(weapons: list[str], vehicles: list[str]) -> str:
    combined = " ".join(weapons + vehicles).lower()
    for cat, keys in _WCAT_KEYS.items():
        if any(k in combined for k in keys):
            return cat
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Analyse-Funktion
# ─────────────────────────────────────────────────────────────────────────────

def analyze_image(
    url_or_path: str,
    region:      str = "",
    source:      str = "unknown",
) -> Optional[VisionHit]:
    """
    Analysiert ein einzelnes Bild mit LLaVA.
    Gibt VisionHit zurück oder None wenn nicht militärrelevant / Fehler.
    """
    if not url_or_path:
        return None

    # Cache-Check
    cache_key = hashlib.md5(url_or_path.encode()).hexdigest()[:12]
    if cache_key in _vision_cache:
        cached = _vision_cache[cache_key]
        return VisionHit(**cached) if cached else None

    # Bild laden
    result = _load_image_b64(url_or_path)
    if not result:
        _vision_cache[cache_key] = None
        return None
    b64, img_hash = result

    # Ollama analysieren
    parsed = _ollama_vision(b64)
    if not parsed:
        _vision_cache[cache_key] = None
        return None

    # Nicht-militärische Bilder filtern
    if not parsed.get("military", False):
        conf = float(parsed.get("confidence", 0))
        if conf < 0.3:
            _vision_cache[cache_key] = None
            return None

    vehicles      = parsed.get("vehicles", []) or []
    weapons       = parsed.get("weapons", []) or []
    unit_markings = parsed.get("unit_markings", []) or []
    weapon_cat    = parsed.get("weapon_cat", "") or _categorize_weapons(weapons, vehicles)

    hit = VisionHit(
        url           = url_or_path,
        vehicles      = [str(v)[:40] for v in vehicles[:6]],
        unit_markings = [str(m)[:20] for m in unit_markings[:5]],
        weapons       = [str(w)[:40] for w in weapons[:6]],
        weapon_cat    = weapon_cat,
        damage        = str(parsed.get("damage", "unknown")),
        terrain       = str(parsed.get("terrain", "unknown")),
        personnel     = int(parsed.get("personnel", 0) or 0),
        geo_hint      = str(parsed.get("geo_hint", "") or "")[:100],
        summary       = str(parsed.get("summary", "") or "")[:120],
        confidence    = min(1.0, max(0.0, float(parsed.get("confidence", 0.5) or 0.5))),
        military      = bool(parsed.get("military", False)),
        img_hash      = img_hash,
        source        = source,
    )

    # Cache befüllen
    _vision_cache[cache_key] = hit.__dict__.copy()
    return hit


# ─────────────────────────────────────────────────────────────────────────────
# Batch-Analyse aus Artikel-Liste
# ─────────────────────────────────────────────────────────────────────────────

def _extract_image_urls(article: dict) -> list[str]:
    """Extrahiert Bild-URLs aus einem Artikel-Dict."""
    urls = []
    for key in ("image_url", "image", "thumbnail", "img", "photo"):
        val = article.get(key, "")
        if val and isinstance(val, str) and val.startswith("http"):
            urls.append(val)
    # Telegram-spezifisch
    for key in ("images", "photos", "media"):
        val = article.get(key, [])
        if isinstance(val, list):
            for v in val[:3]:
                if isinstance(v, str) and v.startswith("http"):
                    urls.append(v)
    return list(dict.fromkeys(urls))[:4]  # Max 4 Bilder pro Artikel


def vision_batch(
    articles:     list[dict],
    region:       str = "",
    max_images:   int = 20,
    min_conf:     float = 0.35,
) -> list[VisionHit]:
    """
    Analysiert Bilder aus einer Liste von Artikeln.
    Respektiert max_images Limit um API nicht zu überlasten.
    """
    if not _ollama_available():
        return []

    hits: list[VisionHit] = []
    processed = 0

    for article in articles:
        if processed >= max_images:
            break
        urls = _extract_image_urls(article)
        source = article.get("source", article.get("channel", "unknown"))

        for url in urls:
            if processed >= max_images:
                break
            hit = analyze_image(url, region, source)
            processed += 1
            if hit and hit.confidence >= min_conf and hit.military:
                hits.append(hit)
            time.sleep(0.1)  # Kurze Pause zwischen Anfragen

    hits.sort(key=lambda h: h.confidence, reverse=True)
    return hits[:30]


# ─────────────────────────────────────────────────────────────────────────────
# Karten-Marker-Output
# ─────────────────────────────────────────────────────────────────────────────

_DAMAGE_COLOR = {
    "intact":    "#00ff88",
    "damaged":   "#ff8800",
    "destroyed": "#ff0044",
    "burning":   "#ff2200",
    "unknown":   "#4466aa",
}

_WCAT_ICON = {
    "drone":    "🛸",
    "missile":  "🚀",
    "arty":     "💣",
    "armor":    "🛡",
    "air_def":  "🔺",
    "infantry": "🪖",
    "unknown":  "📸",
}

def vision_for_map(
    articles:   list[dict],
    region:     str = "",
    max_images: int = 20,
) -> list[dict]:
    """
    Gibt Karten-Marker für Vision-Hits zurück.
    Nur Hits mit lat/lon (durch nexus_geolocate befüllt) oder geo_hint.
    """
    hits = vision_batch(articles, region, max_images)
    markers = []

    for h in hits:
        color = _DAMAGE_COLOR.get(h.damage, "#4466aa")
        icon  = _WCAT_ICON.get(h.weapon_cat, "📸")

        title_parts = []
        if h.vehicles:
            title_parts.append(h.vehicles[0])
        if h.weapon_cat and h.weapon_cat != "unknown":
            title_parts.append(h.weapon_cat.upper())
        if h.damage not in ("unknown", "intact"):
            title_parts.append(h.damage.upper())
        title = " · ".join(title_parts) if title_parts else "Vision-Fund"

        marker = {
            "lat":          h.lat,
            "lon":          h.lon,
            "title":        title,
            "text":         h.summary,
            "vehicles":     h.vehicles,
            "weapons":      h.weapons,
            "unit_markings":h.unit_markings,
            "weapon_cat":   h.weapon_cat,
            "damage":       h.damage,
            "terrain":      h.terrain,
            "personnel":    h.personnel,
            "geo_hint":     h.geo_hint,
            "confidence":   h.confidence,
            "color":        color,
            "icon":         icon,
            "source":       h.source,
            "image_url":    h.url,
            "img_hash":     h.img_hash,
        }
        markers.append(marker)

    return markers


# ─────────────────────────────────────────────────────────────────────────────
# Text-Zusammenfassung
# ─────────────────────────────────────────────────────────────────────────────

def vision_summary(hits: list[VisionHit], max_hits: int = 8) -> str:
    if not hits:
        return ""
    mil_hits = [h for h in hits if h.military]
    if not mil_hits:
        return ""
    lines = [f"[VISION] {len(mil_hits)} militärrelevante Bild-Analysen:\n"]
    for i, h in enumerate(mil_hits[:max_hits], 1):
        veh_s  = ", ".join(h.vehicles[:3])  if h.vehicles  else "?"
        mark_s = ", ".join(h.unit_markings) if h.unit_markings else ""
        lines.append(
            f"  {i}. [{h.weapon_cat}] {veh_s}"
            f"{' Mark:' + mark_s if mark_s else ''}"
            f" Schaden:{h.damage} Konf:{h.confidence:.0%}"
            f"\n     {h.summary}"
        )
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# CLI-Test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Ollama verfügbar: {_ollama_available()}")
    test_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/6/65/T-72B3_tank.jpg/640px-T-72B3_tank.jpg"
    print(f"Analysiere: {test_url}")
    hit = analyze_image(test_url, "Ukraine")
    if hit:
        print(f"  Fahrzeuge:  {hit.vehicles}")
        print(f"  Markierung: {hit.unit_markings}")
        print(f"  Waffe:      {hit.weapon_cat}")
        print(f"  Schaden:    {hit.damage}")
        print(f"  Konfidenz:  {hit.confidence:.0%}")
        print(f"  Zusammenfassung: {hit.summary}")
    else:
        print("  Kein Treffer (Ollama offline oder kein LLaVA-Modell?)")
