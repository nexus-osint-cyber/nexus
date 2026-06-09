"""
NEXUS - Bild-Verifikations-Modul
Prüft ob ein Bild echt ist, woher es stammt und ob es manipuliert wurde.

Drei Methoden:
  1. EXIF-Metadaten  – GPS, Datum, Kamera, Software
  2. Reverse Search  – TinEye + Bing Visual Search (wo tauchte das Bild zuerst auf?)
  3. ELA-Analyse     – Error Level Analysis (Komprimierungs-Inkonsistenz = Manipulation)
"""

from __future__ import annotations

import base64
import hashlib
import io
import math
import os
import re
import tempfile
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests


# ======================================================
# 1. EXIF-METADATEN
# ======================================================

def _gps_to_decimal(gps_value, ref: str) -> Optional[float]:
    """Konvertiert EXIF-GPS (Grad/Min/Sek) zu Dezimalgrad."""
    try:
        if hasattr(gps_value, "__iter__") and len(gps_value) == 3:
            d, m, s = gps_value
            # Pillow gibt IFDRational zurück – in float umwandeln
            d = float(d); m = float(m); s = float(s)
            decimal = d + m / 60 + s / 3600
            if ref in ("S", "W"):
                decimal = -decimal
            return round(decimal, 6)
    except Exception:
        pass
    return None


def extract_exif(image_path: str) -> dict:
    """
    Liest EXIF-Daten aus einer Bilddatei.
    Gibt strukturiertes Dict zurück.
    """
    result = {
        "file": os.path.basename(image_path),
        "datetime_original": None,
        "datetime_digitized": None,
        "gps_lat": None,
        "gps_lon": None,
        "gps_altitude_m": None,
        "gps_maps_url": None,
        "camera_make": None,
        "camera_model": None,
        "software": None,
        "image_width": None,
        "image_height": None,
        "warnings": [],
        "raw": {},
    }

    try:
        from PIL import Image
        from PIL.ExifTags import TAGS, GPSTAGS
    except ImportError:
        result["warnings"].append("Pillow nicht installiert – pip install Pillow")
        return result

    try:
        img = Image.open(image_path)
    except Exception as exc:
        result["warnings"].append(f"Bild konnte nicht geöffnet werden: {exc}")
        return result

    result["image_width"], result["image_height"] = img.size

    # Basis-EXIF
    raw_exif = {}
    try:
        exif_data = img._getexif()
        if exif_data:
            for tag_id, value in exif_data.items():
                tag = TAGS.get(tag_id, str(tag_id))
                raw_exif[tag] = value
    except Exception:
        pass

    if not raw_exif:
        result["warnings"].append("Keine EXIF-Daten vorhanden (möglicherweise entfernt oder niemals enthalten)")
        return result

    result["raw"] = {k: str(v)[:100] for k, v in raw_exif.items()}

    result["datetime_original"] = str(raw_exif.get("DateTimeOriginal", "")).strip() or None
    result["datetime_digitized"] = str(raw_exif.get("DateTimeDigitized", "")).strip() or None
    result["camera_make"]  = str(raw_exif.get("Make", "")).strip() or None
    result["camera_model"] = str(raw_exif.get("Model", "")).strip() or None
    result["software"]     = str(raw_exif.get("Software", "")).strip() or None

    # Software-Warnung: KI-Generierungshinweise
    if result["software"]:
        ai_hints = ["stable diffusion", "midjourney", "dall-e", "firefly",
                    "photoshop", "gimp", "pixelmator", "affinity"]
        sw_lower = result["software"].lower()
        for hint in ai_hints:
            if hint in sw_lower:
                result["warnings"].append(
                    f"⚠ Software-Feld enthält '{result['software']}' – "
                    f"mögliche Bildbearbeitung oder KI-Generierung"
                )

    # GPS
    gps_info_raw = raw_exif.get("GPSInfo")
    if gps_info_raw and isinstance(gps_info_raw, dict):
        gps_decoded = {}
        for k, v in gps_info_raw.items():
            gps_tag = GPSTAGS.get(k, str(k))
            gps_decoded[gps_tag] = v

        lat_val = gps_decoded.get("GPSLatitude")
        lat_ref = gps_decoded.get("GPSLatitudeRef", "N")
        lon_val = gps_decoded.get("GPSLongitude")
        lon_ref = gps_decoded.get("GPSLongitudeRef", "E")
        alt_val = gps_decoded.get("GPSAltitude")

        if lat_val and lon_val:
            result["gps_lat"] = _gps_to_decimal(lat_val, lat_ref)
            result["gps_lon"] = _gps_to_decimal(lon_val, lon_ref)
            if result["gps_lat"] and result["gps_lon"]:
                result["gps_maps_url"] = (
                    f"https://maps.google.com/maps?q="
                    f"{result['gps_lat']},{result['gps_lon']}"
                )
        if alt_val:
            try:
                result["gps_altitude_m"] = round(float(alt_val), 1)
            except Exception:
                pass

    # Datum-Plausibilitäts-Warnung
    if result["datetime_original"]:
        try:
            dt = datetime.strptime(result["datetime_original"], "%Y:%m:%d %H:%M:%S")
            age_days = (datetime.now() - dt).days
            if age_days > 730:
                result["warnings"].append(
                    f"⚠ Aufnahmedatum {result['datetime_original']} ist "
                    f"{age_days // 365} Jahre alt – möglicherweise recyceltes Bild"
                )
        except ValueError:
            pass

    return result


# ======================================================
# 2. ERROR LEVEL ANALYSIS (ELA)
# ======================================================

def ela_analysis(image_path: str, quality: int = 90) -> dict:
    """
    Error Level Analysis: Speichert Bild mit fester JPEG-Qualität neu,
    vergleicht Unterschied pixelweise. Bearbeitete Bereiche leuchten heller.

    Gibt Dict zurück mit ELA-Score und gespeichertem ELA-Bild-Pfad.
    """
    result = {
        "ela_score": None,       # 0-100, höher = verdächtiger
        "ela_image_path": None,
        "assessment": "Analyse nicht durchgeführt",
        "suspicious_regions": 0,
    }

    try:
        from PIL import Image, ImageChops, ImageEnhance
        import numpy as np
    except ImportError:
        result["assessment"] = "Pillow/numpy nicht installiert – pip install Pillow numpy"
        return result

    try:
        original = Image.open(image_path).convert("RGB")
    except Exception as exc:
        result["assessment"] = f"Bild konnte nicht geöffnet werden: {exc}"
        return result

    # Bild mit definierter Qualität neu komprimieren
    tmp_buf = io.BytesIO()
    original.save(tmp_buf, format="JPEG", quality=quality)
    tmp_buf.seek(0)
    recompressed = Image.open(tmp_buf).convert("RGB")

    # Differenz berechnen
    diff = ImageChops.difference(original, recompressed)

    # Differenz-Pixel als numpy-Array
    try:
        diff_array = list(diff.getdata())
        pixels = [max(r, g, b) for r, g, b in diff_array]
    except Exception:
        result["assessment"] = "ELA-Berechnung fehlgeschlagen"
        return result

    if not pixels:
        result["assessment"] = "Kein Pixel-Inhalt"
        return result

    avg = sum(pixels) / len(pixels)
    max_val = max(pixels)

    # ELA-Score: normalisiert auf 0-100
    # Bei unverändertem JPEG: avg typischerweise < 5
    # Bei manipuliertem Bild: avg oft > 15, max > 100
    ela_score = min(100, int(avg * 5))
    suspicious = sum(1 for p in pixels if p > 50)

    result["ela_score"] = ela_score
    result["suspicious_regions"] = suspicious

    # Bewertung
    if ela_score < 10:
        result["assessment"] = "✅ Unauffällig – keine signifikanten Komprimierungs-Inkonsistenzen"
    elif ela_score < 25:
        result["assessment"] = "⚠ Leicht erhöht – mögliche kleinere Bearbeitung oder Qualitätsverlust"
    elif ela_score < 50:
        result["assessment"] = "⚠⚠ Erhöhte ELA – deutliche Inkonsistenzen, wahrscheinlich bearbeitet"
    else:
        result["assessment"] = "🚨 Stark erhöhte ELA – sehr wahrscheinlich manipuliert oder KI-generiert"

    # ELA-Bild speichern (aufgehellt für Sichtbarkeit)
    try:
        enhanced_diff = ImageEnhance.Brightness(diff).enhance(10.0)
        ela_path = image_path.rsplit(".", 1)[0] + "_ELA.jpg"
        enhanced_diff.save(ela_path, format="JPEG")
        result["ela_image_path"] = ela_path
    except Exception:
        pass

    return result


# ======================================================
# 3. REVERSE IMAGE SEARCH
# ======================================================

def reverse_search_tineye(image_path: str) -> dict:
    """
    TinEye Reverse Image Search.
    Gibt die Anzahl gefundener Treffer zurück + Link zum Ergebnis.
    """
    result = {
        "service": "TinEye",
        "matches": None,
        "result_url": None,
        "error": None,
    }

    try:
        # Bild als Multipart hochladen
        with open(image_path, "rb") as f:
            files = {"upload": (os.path.basename(image_path), f, "image/jpeg")}
            r = requests.post(
                "https://tineye.com/search",
                files=files,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=20,
                allow_redirects=True,
            )

        # TinEye leitet auf Ergebnis-URL um
        result_url = r.url
        result["result_url"] = result_url

        # Treffer-Anzahl aus HTML parsen
        match = re.search(r"(\d[\d,]*)\s+result", r.text, re.IGNORECASE)
        if match:
            result["matches"] = int(match.group(1).replace(",", ""))
        else:
            result["matches"] = 0

    except requests.Timeout:
        result["error"] = "TinEye Timeout"
    except Exception as exc:
        result["error"] = str(exc)

    return result


def reverse_search_bing(image_path: str) -> dict:
    """
    Bing Visual Search via URL-Upload.
    Gibt Seiten zurück, auf denen das Bild gefunden wurde.
    """
    result = {
        "service": "Bing Visual Search",
        "pages_found": [],
        "result_url": None,
        "error": None,
    }

    try:
        # Bild als Base64 kodieren für Bing
        with open(image_path, "rb") as f:
            img_bytes = f.read()

        img_b64 = base64.b64encode(img_bytes).decode("utf-8")
        img_hash = hashlib.md5(img_bytes).hexdigest()

        # Bing Visual Search API (kostenlose Web-Variante)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        }

        with open(image_path, "rb") as f:
            r = requests.post(
                "https://www.bing.com/images/search",
                files={"file": (os.path.basename(image_path), f, "image/jpeg")},
                params={"q": "imgurl:", "view": "detailv2", "form": "SBIHMP"},
                headers=headers,
                timeout=20,
                allow_redirects=True,
            )

        result["result_url"] = r.url

        # URLs aus der Antwort extrahieren
        urls = re.findall(r'href="(https?://[^"]+)"', r.text)
        seen = set()
        pages = []
        for url in urls:
            if "bing.com" not in url and url not in seen:
                seen.add(url)
                pages.append(url)
            if len(pages) >= 5:
                break
        result["pages_found"] = pages

    except requests.Timeout:
        result["error"] = "Bing Timeout"
    except Exception as exc:
        result["error"] = str(exc)

    return result


def google_reverse_search_url(image_path: str) -> str:
    """
    Gibt eine Google Reverse Image Search URL zurück.
    Kann nicht direkt aufgerufen werden (erfordert Browser),
    aber der Nutzer kann den Link öffnen.
    """
    # Für lokale Bilder: Base64 direkt übergeben funktioniert nicht extern.
    # Daher nur Anleitung generieren.
    return (
        "Google Bilder: https://images.google.com → Kamera-Symbol → Bild hochladen\n"
        "Oder: Bild per Drag & Drop auf https://images.google.com ziehen"
    )


# ======================================================
# GESAMT-ANALYSE
# ======================================================

# ======================================================
# 4. OCR – TEXTERKENNUNG AUS BILDERN (Stufe 3)
# ======================================================

def ocr_extract_text(image_path: str, lang: str = "deu+eng") -> dict:
    """
    Extrahiert Text aus einem Bild via pytesseract (Tesseract OCR).
    Nützlich für: Telegram-Screenshots, Dokument-Fotos, Karten-Beschriftungen.

    Benötigt:
      pip install pytesseract --break-system-packages
      Tesseract-OCR für Windows: https://github.com/UB-Mannheim/tesseract/wiki

    lang: Tesseract-Sprachcode, z.B. "deu+eng+rus" für Mehrsprachig
    """
    path = Path(image_path)
    if not path.exists():
        return {"error": f"Datei nicht gefunden: {image_path}"}

    # pytesseract versuchen
    try:
        import pytesseract  # type: ignore
        from PIL import Image as _PilImage  # type: ignore
    except ImportError:
        return {
            "error": "pytesseract nicht installiert",
            "install": "pip install pytesseract --break-system-packages",
            "note": "Tesseract-OCR für Windows: https://github.com/UB-Mannheim/tesseract/wiki",
        }

    # Tesseract-Pfad für Windows setzen (Standard-Installationspfad)
    import subprocess
    _tess_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        r"C:\Users\{}\AppData\Local\Programs\Tesseract-OCR\tesseract.exe".format(os.environ.get("USERNAME", "")),
    ]
    for tp in _tess_paths:
        if os.path.exists(tp):
            pytesseract.pytesseract.tesseract_cmd = tp
            break

    try:
        img = _PilImage.open(str(path))

        # Vorverarbeitung: Kontrast erhöhen für bessere OCR-Qualität
        try:
            from PIL import ImageEnhance, ImageFilter  # type: ignore
            img_proc = img.convert("L")   # Graustufen
            img_proc = ImageEnhance.Contrast(img_proc).enhance(2.0)
            img_proc = img_proc.filter(ImageFilter.SHARPEN)
        except Exception:
            img_proc = img

        # OCR mit Konfidenz-Scores
        try:
            data = pytesseract.image_to_data(
                img_proc, lang=lang,
                output_type=pytesseract.Output.DICT,
            )
            words = [
                w for w, c in zip(data["text"], data["conf"])
                if w.strip() and int(c) > 30
            ]
            confidence_scores = [int(c) for c in data["conf"] if str(c).lstrip("-").isdigit() and int(c) > 0]
            avg_conf = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0
        except Exception:
            words = []
            avg_conf = 0

        # Einfacher Text als Fallback
        raw_text = pytesseract.image_to_string(img_proc, lang=lang).strip()

        # Sprache detektieren
        detected_lang = "unbekannt"
        cyrillic_count = sum(1 for c in raw_text if 'Ѐ' <= c <= 'ӿ')
        arabic_count   = sum(1 for c in raw_text if '؀' <= c <= 'ۿ')
        if cyrillic_count > len(raw_text) * 0.2:
            detected_lang = "ru (Kyrillisch)"
        elif arabic_count > len(raw_text) * 0.1:
            detected_lang = "ar (Arabisch)"
        elif len(raw_text) > 10:
            detected_lang = "de/en (Latein)"

        return {
            "text":           raw_text,
            "word_count":     len(words),
            "confidence":     round(avg_conf, 1),
            "detected_lang":  detected_lang,
            "has_text":       len(raw_text.strip()) > 5,
            "languages_used": lang,
        }

    except Exception as e:
        return {"error": f"OCR-Fehler: {e}"}


def full_image_check(image_path: str, ocr: bool = True) -> dict:
    """
    Führt alle Analysen durch und gibt Gesamtergebnis zurück.
    ocr=True: OCR-Texterkennung einbeziehen (Stufe 3)
    """
    path = Path(image_path)
    if not path.exists():
        return {"error": f"Datei nicht gefunden: {image_path}"}

    steps = 5 if ocr else 4

    print(f"  [1/{steps}] EXIF-Metadaten lesen...", flush=True)
    exif = extract_exif(str(path))

    print(f"  [2/{steps}] Error Level Analysis...", flush=True)
    ela  = ela_analysis(str(path))

    print(f"  [3/{steps}] TinEye Reverse Search...", flush=True)
    tineye = reverse_search_tineye(str(path))

    print(f"  [4/{steps}] Bing Visual Search...", flush=True)
    bing = reverse_search_bing(str(path))

    result = {
        "file": str(path),
        "exif": exif,
        "ela": ela,
        "reverse_tineye": tineye,
        "reverse_bing": bing,
        "google_hint": google_reverse_search_url(str(path)),
    }

    if ocr:
        print(f"  [5/{steps}] OCR Texterkennung...", flush=True)
        result["ocr"] = ocr_extract_text(str(path))

    return result


def format_check_for_terminal(result: dict) -> str:
    """Formatiert das Ergebnis für die Terminal-Ausgabe."""
    if "error" in result:
        return f"[BILD-CHECK] Fehler: {result['error']}"

    lines = ["━" * 56, "  NEXUS BILD-VERIFIKATION", "━" * 56]
    lines.append(f"  Datei: {os.path.basename(result['file'])}\n")

    # EXIF
    exif = result.get("exif", {})
    lines.append("── EXIF-METADATEN ──")
    if exif.get("datetime_original"):
        lines.append(f"  📅 Aufnahmedatum:  {exif['datetime_original']}")
    else:
        lines.append("  📅 Aufnahmedatum:  NICHT VORHANDEN")
    if exif.get("camera_make") or exif.get("camera_model"):
        lines.append(f"  📷 Kamera:         {exif.get('camera_make','')} {exif.get('camera_model','')}")
    if exif.get("software"):
        lines.append(f"  💻 Software:       {exif['software']}")
    if exif.get("gps_lat") and exif.get("gps_lon"):
        lines.append(f"  📍 GPS:            {exif['gps_lat']}, {exif['gps_lon']}")
        if exif.get("gps_maps_url"):
            lines.append(f"  🗺  Maps:           {exif['gps_maps_url']}")
    else:
        lines.append("  📍 GPS:            KEINE GPS-DATEN")
    for w in exif.get("warnings", []):
        lines.append(f"  {w}")

    # ELA
    ela = result.get("ela", {})
    lines.append("\n── ERROR LEVEL ANALYSIS ──")
    if ela.get("ela_score") is not None:
        lines.append(f"  ELA-Score:    {ela['ela_score']}/100")
        lines.append(f"  Bewertung:    {ela['assessment']}")
        if ela.get("ela_image_path"):
            lines.append(f"  ELA-Bild:     {ela['ela_image_path']}")
    else:
        lines.append(f"  {ela.get('assessment', 'Keine ELA-Daten')}")

    # Reverse Search
    lines.append("\n── REVERSE IMAGE SEARCH ──")
    te = result.get("reverse_tineye", {})
    if te.get("error"):
        lines.append(f"  TinEye:    Fehler – {te['error']}")
    elif te.get("matches") is not None:
        if te["matches"] == 0:
            lines.append("  TinEye:    ✅ Keine Treffer – Bild möglicherweise erstmalig online")
        else:
            lines.append(f"  TinEye:    ⚠ {te['matches']} Treffer gefunden!")
        if te.get("result_url"):
            lines.append(f"  TinEye URL: {te['result_url']}")

    bg = result.get("reverse_bing", {})
    if bg.get("error"):
        lines.append(f"  Bing:      Fehler – {bg['error']}")
    elif bg.get("pages_found"):
        lines.append(f"  Bing:      ⚠ Bild auf {len(bg['pages_found'])} Seiten gefunden")
        for url in bg["pages_found"][:3]:
            lines.append(f"    → {url[:80]}")
    else:
        lines.append("  Bing:      ✅ Keine bekannten Seiten mit diesem Bild")

    lines.append(f"\n  Google:    {result.get('google_hint', '')}")

    # OCR-Ergebnis (Stufe 3)
    ocr = result.get("ocr")
    if ocr:
        lines.append("")
        lines.append("  ── OCR TEXTERKENNUNG ──────────────────────────")
        if ocr.get("error"):
            err = ocr["error"]
            lines.append(f"  ❌ {err}")
            if "install" in ocr:
                lines.append(f"  → {ocr['install']}")
            if "note" in ocr:
                lines.append(f"  → {ocr['note']}")
        elif ocr.get("has_text"):
            lines.append(f"  ✅ Text erkannt: {ocr.get('word_count',0)} Wörter")
            lines.append(f"  Konfidenz: {ocr.get('confidence',0):.0f}%  |  Sprache: {ocr.get('detected_lang','?')}")
            text = ocr.get("text", "").strip()
            if text:
                lines.append("")
                # Text in Zeilen aufteilen und einrücken
                for line in text[:500].split("\n"):
                    if line.strip():
                        lines.append(f"  │ {line.strip()[:70]}")
        else:
            lines.append("  ─ Kein Text im Bild erkannt")

    lines.append("━" * 56)

    return "\n".join(lines)


def format_check_for_llm(result: dict) -> str:
    """Kompaktere Version für den LLM-Kontext."""
    if "error" in result:
        return f"[BILD-CHECK Fehler]: {result['error']}"

    parts = [f"[BILD-VERIFIKATION: {os.path.basename(result['file'])}]"]

    exif = result.get("exif", {})
    if exif.get("datetime_original"):
        parts.append(f"EXIF-Datum: {exif['datetime_original']}")
    else:
        parts.append("EXIF-Datum: FEHLT (möglicherweise entfernt)")

    if exif.get("gps_lat"):
        parts.append(f"GPS-Koordinaten: {exif['gps_lat']}, {exif['gps_lon']} → {exif.get('gps_maps_url','')}")
    else:
        parts.append("GPS: KEINE DATEN")

    for w in exif.get("warnings", []):
        parts.append(w)

    ela = result.get("ela", {})
    parts.append(f"ELA: {ela.get('assessment', 'keine Analyse')}")

    te = result.get("reverse_tineye", {})
    if te.get("matches") is not None:
        parts.append(f"TinEye: {te['matches']} Treffer")

    # OCR (Stufe 3)
    ocr = result.get("ocr", {})
    if ocr and not ocr.get("error"):
        if ocr.get("has_text"):
            parts.append(f"OCR-Text ({ocr.get('word_count',0)} Wörter, "
                         f"Konfidenz {ocr.get('confidence',0)}%, "
                         f"Sprache: {ocr.get('detected_lang','?')})")
            text = ocr.get("text", "")
            if text:
                parts.append("Erkannter Text (Auszug):\n" + text[:300])

    return "\n".join(parts)


# ── Direktaufruf zum Testen ──────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Nutzung: python nexus_imgcheck.py <bildpfad>")
    else:
        result = full_image_check(sys.argv[1])
        print(format_check_for_terminal(result))
