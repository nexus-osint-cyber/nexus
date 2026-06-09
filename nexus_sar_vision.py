"""
nexus_sar_vision.py — SAR-Bildverbesserung + KI-Schiffserkennung
================================================================
Kombiniert Sentinel-1 SAR-Radar mit:
  1. PIL-Bildfilter: Kontrast, Schärfe, adaptiver Threshold
  2. Mehrfach-Threshold-Analyse (verschiedene Empfindlichkeitsstufen)
  3. LLaVA Vision-KI zur visuellen Bestätigung
  4. Vergleich mit historischen Baseline-Werten

Warum SAR besser als AIS für Blockaden:
  - AIS = Schiffe melden sich freiwillig. Kriegsschiffe/blockierte Tanker: AUS
  - SAR = Radar sieht ALLE Metallkörper, unabhängig von Transponder
  - Sentinel-1: alle 6 Tage, 10m Auflösung, gratis mit Copernicus-Account

Aufruf:
  python nexus_sar_vision.py --region hormuz
  python nexus_sar_vision.py --region "bandar abbas" --enhanced
"""

from __future__ import annotations

import io
import os
import sys
import math
from datetime import datetime, timezone
from typing import Optional

BASIS_DIR = os.path.dirname(os.path.abspath(__file__))


# ── Bildverbesserung ──────────────────────────────────────────────────────────

def enhance_sar_image(png_bytes: bytes) -> dict[str, bytes]:
    """
    Wendet mehrere Bildfilter auf ein SAR-PNG an.
    Gibt Dict mit verschiedenen gefilterten Versionen zurück.

    Filter:
      original:   Rohbild
      contrast:   Kontrastverstärkung (Schiffe werden heller, Wasser dunkler)
      threshold:  Binärbild (hell=Schiff, dunkel=Wasser)
      enhanced:   Kombination: Kontrast + Schärfe + Schwellenwert
      inverted:   Invertiert (für Analyse von dunklen Zielen)
    """
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    except ImportError:
        return {"original": png_bytes}

    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("L")
        result = {"original": png_bytes}

        # 1. Kontrastverstärkung (Faktor 3.0 — stark erhöht Sichtbarkeit)
        enhancer = ImageEnhance.Contrast(img)
        high_contrast = enhancer.enhance(3.0)
        buf = io.BytesIO()
        high_contrast.save(buf, format="PNG")
        result["contrast"] = buf.getvalue()

        # 2. Schärfung + Kontrast
        sharp = img.filter(ImageFilter.SHARPEN)
        sharp = ImageEnhance.Contrast(sharp).enhance(2.5)
        sharp = ImageEnhance.Sharpness(sharp).enhance(3.0)
        buf = io.BytesIO()
        sharp.save(buf, format="PNG")
        result["sharp"] = buf.getvalue()

        # 3. Threshold-Bild (binär: hell=potentielles Schiff)
        # SAR: Wasser ~50-100px, Schiff ~150-255px
        threshold_img = img.point(lambda p: 255 if p > 130 else 0)
        buf = io.BytesIO()
        threshold_img.save(buf, format="PNG")
        result["threshold"] = buf.getvalue()

        # 4. Adaptiver Threshold mit Histogramm-Streckung
        # Normalisiert den Helligkeitsbereich auf 0-255
        import PIL.ImageOps
        equalized = PIL.ImageOps.equalize(img)
        enhancer2  = ImageEnhance.Contrast(equalized)
        enhanced   = enhancer2.enhance(2.0)
        buf = io.BytesIO()
        enhanced.save(buf, format="PNG")
        result["enhanced"] = buf.getvalue()

        # 5. Multi-Threshold: niedrig (empfindlicher, mehr False Positives)
        low_thresh = img.point(lambda p: 255 if p > 100 else 0)
        buf = io.BytesIO()
        low_thresh.save(buf, format="PNG")
        result["sensitive"] = buf.getvalue()

        return result

    except Exception as e:
        print(f"[SAR Vision] Bildfilter Fehler: {e}", file=sys.stderr)
        return {"original": png_bytes}


def count_bright_clusters(png_bytes: bytes, threshold: int = 130,
                           min_pixels: int = 4) -> dict:
    """
    Zählt helle Pixel-Cluster in einem SAR-Bild.
    Jeder Cluster = potentielles Schiff.
    Gibt Anzahl + Größenverteilung zurück.
    """
    try:
        from PIL import Image
    except ImportError:
        return {"count": 0, "error": "PIL nicht installiert"}

    try:
        img  = Image.open(io.BytesIO(png_bytes)).convert("L")
        w, h = img.size
        pix  = list(img.getdata())

        mask   = [1 if v >= threshold else 0 for v in pix]
        labels = [0] * (w * h)
        clusters: dict[int, list[int]] = {}
        current = 0

        def fill(start: int, lbl: int):
            stack = [start]
            while stack:
                idx = stack.pop()
                if idx < 0 or idx >= w*h or labels[idx] or not mask[idx]:
                    continue
                labels[idx] = lbl
                clusters.setdefault(lbl, []).append(idx)
                r, c = divmod(idx, w)
                if c > 0:     stack.append(idx-1)
                if c < w-1:   stack.append(idx+1)
                if r > 0:     stack.append(idx-w)
                if r < h-1:   stack.append(idx+w)

        for i in range(w * h):
            if mask[i] and not labels[i]:
                current += 1
                fill(i, current)

        valid = {k: v for k, v in clusters.items() if len(v) >= min_pixels}

        sizes = sorted([len(v) for v in valid.values()], reverse=True)
        small  = sum(1 for s in sizes if s <  20)
        medium = sum(1 for s in sizes if 20 <= s < 100)
        large  = sum(1 for s in sizes if s >= 100)

        return {
            "count":   len(valid),
            "small":   small,   # kleine Schiffe / Schnellboote
            "medium":  medium,  # Fregatten / Tanker
            "large":   large,   # Flugzeugträger / VLCC-Tanker
            "sizes":   sizes[:10],
            "image_size": (w, h),
        }
    except Exception as e:
        return {"count": 0, "error": str(e)}


def multi_threshold_analysis(png_bytes: bytes) -> dict:
    """
    Analysiert dasselbe Bild mit verschiedenen Threshold-Werten.
    Gibt Bandbreite: pessimistisch → optimistisch zurück.

    Niedrig (100): empfindlicher, mehr False Positives
    Mittel  (130): balanced (Standardwert)
    Hoch    (160): konservativ, nur sehr starke Reflexionen
    """
    results = {}
    for name, thresh in [("niedrig_100", 100), ("standard_130", 130),
                          ("hoch_160", 160), ("sehr_hoch_190", 190)]:
        r = count_bright_clusters(png_bytes, threshold=thresh)
        results[name] = r.get("count", 0)

    # Konsensus-Schätzung
    counts = list(results.values())
    results["min"]      = min(counts)
    results["max"]      = max(counts)
    results["consensus"] = sorted(counts)[len(counts)//2]  # Median
    return results


# ── LLaVA Vision-Analyse ──────────────────────────────────────────────────────

def llava_analyse_sar(png_bytes: bytes, region: str = "",
                      context: str = "") -> Optional[str]:
    """
    Lässt LLaVA das SAR-Bild analysieren.
    Fragt nach: Anzahl Schiffe, ungewöhnliche Muster, Cluster.
    """
    try:
        from nexus_vision import analyse_image_bytes  # type: ignore
    except ImportError:
        try:
            from nexus_llm import ask_llm  # type: ignore
            # Fallback: nur Text-Beschreibung
            return None
        except Exception:
            return None

    prompt = (
        f"Analysiere dieses Sentinel-1 SAR-Radar-Satellitenbild der Region {region or 'unbekannt'}. "
        "SAR-Bilder zeigen Radar-Reflexionen: helle Punkte/Flecken sind Metallstrukturen (Schiffe, "
        "Installationen), dunkle Bereiche sind Wasser oder Land. "
        "Beantworte: "
        "1. Wie viele helle Punkte/Cluster siehst du die Schiffe sein könnten? "
        "2. Siehst du ungewöhnliche Muster (Schiffsreihen, Konvoi-Formationen, ankernde Cluster)? "
        "3. Gibt es Bereiche mit auffällig WENIG oder VIEL Aktivität im Vergleich zu normalem Seeverkehr? "
        f"{('Kontext: ' + context) if context else ''}"
    )

    try:
        result = analyse_image_bytes(png_bytes, prompt)
        return result
    except Exception as e:
        print(f"[SAR Vision] LLaVA Fehler: {e}", file=sys.stderr)
        return None


# ── Strategische Scan-Zonen ───────────────────────────────────────────────────
# Jede Zone ist 250×250km — eine Sentinel-1 Szene
# Format: name → (lat_min, lon_min, lat_max, lon_max, beschreibung, wasser_anteil)

SCAN_ZONES: dict[str, list[dict]] = {
    "hormuz": [
        {"name": "Hormuz-West-Einfahrt",
         "bbox": (26.2, 55.8, 27.2, 57.0),
         "desc": "Hauptfahrwasser Westeingang — hier passieren alle Tanker",
         "water_bbox": (26.3, 56.0, 27.0, 56.9)},   # reines Wasser-Gebiet
        {"name": "Hormuz-Ost-Ausfahrt",
         "bbox": (23.5, 57.5, 25.5, 59.5),
         "desc": "Golf von Oman — Ausfahrt Richtung Indik",
         "water_bbox": (23.8, 57.8, 25.2, 59.2)},
        {"name": "Bandar-Abbas-Hafen",
         "bbox": (26.8, 55.8, 27.6, 57.0),
         "desc": "Irans größter Hafen — Blockade direkt messbar",
         "water_bbox": (26.9, 55.9, 27.4, 56.8)},
        {"name": "Hormuz-Wartegebiet",
         "bbox": (24.5, 57.0, 26.5, 59.0),
         "desc": "Ankerfläche wo Schiffe auf Einfahrt warten",
         "water_bbox": (24.6, 57.2, 26.3, 58.8)},
    ],
    "rotes meer": [
        {"name": "Bab-el-Mandeb-Nord",
         "bbox": (12.0, 42.5, 14.0, 44.5),
         "desc": "Nordseite Bab-el-Mandeb — Jemen/Houthi-Gebiet",
         "water_bbox": (12.2, 42.8, 13.8, 44.2)},
        {"name": "Bab-el-Mandeb-Sued",
         "bbox": (11.0, 43.0, 13.0, 45.0),
         "desc": "Südseite — Dschibuti/Durchfahrt",
         "water_bbox": (11.2, 43.2, 12.8, 44.8)},
    ],
    "schwarzes meer": [
        {"name": "Odessa-Hafen",
         "bbox": (46.0, 30.2, 47.2, 31.5),
         "desc": "Ukrainischer Haupthafen",
         "water_bbox": (46.2, 30.5, 47.0, 31.2)},
        {"name": "Bosphorus-Einfahrt",
         "bbox": (40.8, 28.8, 41.5, 29.5),
         "desc": "Türkische Meerenge — Choke Point",
         "water_bbox": (40.9, 29.0, 41.4, 29.4)},
    ],
}


def build_water_mask(png_bytes: bytes, water_bbox_frac: tuple | None = None) -> list[bool]:
    """
    Erstellt eine Wasser-Maske für ein SAR-Bild.

    Methode 1 (mit water_bbox_frac): Definiertes Wasser-Rechteck im Bild
    Methode 2 (adaptiv): Dunkle Bereiche = Wasser (automatisch)

    Gibt bool-Liste zurück (True = Wasser, False = Land)
    """
    try:
        from PIL import Image
        import statistics

        img  = Image.open(io.BytesIO(png_bytes)).convert("L")
        w, h = img.size
        pix  = list(img.getdata())

        if water_bbox_frac:
            # Methode 1: Bekanntes Wasser-Rechteck (als Bildanteil 0.0-1.0)
            x0, y0, x1, y1 = water_bbox_frac
            mask = []
            for i in range(w * h):
                row, col = divmod(i, w)
                in_water = (x0*w <= col <= x1*w) and (y0*h <= row <= y1*h)
                mask.append(in_water)
            return mask

        # Methode 2: Adaptiv — untere 40% der Helligkeitswerte = Wasser
        # SAR: Wasser ist dunkel, Land ist hell
        sorted_vals = sorted(pix)
        water_threshold = sorted_vals[int(len(sorted_vals) * 0.40)]

        # Aber nur große zusammenhängende dunkle Bereiche = echtes Wasser
        # Kleine dunkle Stellen = Schatten/Artefakte
        water_mask = [v <= water_threshold for v in pix]

        # Morphologisches Opening: kleine isolierte dunkle Punkte entfernen
        # (vereinfacht: Nachbarschaft prüfen)
        refined = []
        for i, is_dark in enumerate(water_mask):
            if not is_dark:
                refined.append(False)
                continue
            row, col = divmod(i, w)
            # Mindestens 3 von 4 Nachbarn müssen auch dunkel sein
            neighbors = 0
            if row > 0   and water_mask[i - w]: neighbors += 1
            if row < h-1 and water_mask[i + w]: neighbors += 1
            if col > 0   and water_mask[i - 1]: neighbors += 1
            if col < w-1 and water_mask[i + 1]: neighbors += 1
            refined.append(neighbors >= 2)

        return refined

    except Exception:
        # Fallback: alles als Wasser
        return [True] * (512 * 512)


def _dilate_mask(mask: list[bool], w: int, h: int, radius: int = 6) -> list[bool]:
    """
    Erweitert eine bool-Maske um N Pixel (Dilation).
    Damit werden helle Schiffspixel die direkt neben dunklem Wasser liegen
    in die Wasser-Zone eingeschlossen.
    """
    dilated = list(mask)
    for i, is_water in enumerate(mask):
        if not is_water:
            continue
        row, col = divmod(i, w)
        for dr in range(-radius, radius + 1):
            for dc in range(-radius, radius + 1):
                nr, nc = row + dr, col + dc
                if 0 <= nr < h and 0 <= nc < w:
                    dilated[nr * w + nc] = True
    return dilated


def _auto_calibrate(pix: list) -> dict:
    """Auto-Kalibrierung aus Bildstatistik — funktioniert weltweit."""
    s = sorted(pix)
    n = len(s)
    # Dunkelste 40% = Wasser-Kandidaten
    wp = s[:int(n * 0.40)]
    wm = sum(wp) / len(wp) if wp else 80
    ws = (sum((v-wm)**2 for v in wp) / len(wp))**0.5 if wp else 20
    return {
        "water_mean": wm, "water_std": ws,
        "ship_thresh": wm + 3.0 * ws,
        "water_max":   wm + 2.0 * ws,
        "p10": s[n//10], "p50": s[n//2],
        "p90": s[int(n*0.9)], "p99": s[int(n*0.99)],
    }


def count_ships_cfar(png_bytes: bytes,
                     guard_win: int = 2,
                     bg_win: int = 6,
                     cfar_k: float = 3.0,
                     min_pixels: int = 2) -> dict:
    """
    Adaptives CFAR — kalibriert sich automatisch aus jedem Bild.
    Kein fixer water_max — funktioniert weltweit.
    """
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(png_bytes)).convert("L")
        w, h = img.size
        pix = list(img.getdata())

        cal = _auto_calibrate(pix)
        ship_thresh = cal["ship_thresh"]
        water_max   = cal["water_max"]
        print(f"[CFAR] Wasser: {cal['water_mean']:.0f}±{cal['water_std']:.0f} "
              f"Schiff-Schwelle: {ship_thresh:.0f} water_max: {water_max:.0f} "
              f"p50={cal['p50']} p99={cal['p99']}", file=sys.stderr)

        total_win = guard_win + bg_win
        ship_mask = [0] * (w * h)

        for row in range(0, h, 2):
            for col in range(0, w, 2):
                i = row * w + col
                cv = pix[i]
                if cv < ship_thresh:
                    continue
                bg = []
                for dr in range(-total_win, total_win + 1):
                    for dc in range(-total_win, total_win + 1):
                        if abs(dr) <= guard_win and abs(dc) <= guard_win:
                            continue
                        nr, nc = row+dr, col+dc
                        if 0 <= nr < h and 0 <= nc < w:
                            bg.append(pix[nr*w+nc])
                if not bg:
                    continue
                bm = sum(bg) / len(bg)
                bs = (sum((v-bm)**2 for v in bg) / len(bg))**0.5
                if bm <= water_max and cv >= bm + cfar_k * max(bs, 3):
                    ship_mask[i] = 1

        labels = [0] * (w * h)
        clusters: dict[int, list] = {}
        cur = 0

        def fill(s0: int, lbl: int) -> None:
            stk = [s0]
            while stk:
                idx = stk.pop()
                if idx < 0 or idx >= w*h or labels[idx] or not ship_mask[idx]:
                    continue
                labels[idx] = lbl
                clusters.setdefault(lbl, []).append(idx)
                r, c = divmod(idx, w)
                if c > 0:     stk.append(idx-1)
                if c < w-1:   stk.append(idx+1)
                if r > 0:     stk.append(idx-w)
                if r < h-1:   stk.append(idx+w)

        for i in range(w * h):
            if ship_mask[i] and not labels[i]:
                cur += 1
                fill(i, cur)

        valid = {k: v for k, v in clusters.items() if len(v) >= min_pixels}
        sizes = sorted([len(v) for v in valid.values()], reverse=True)
        return {
            "count":  len(valid),
            "small":  sum(1 for s in sizes if s < 20),
            "medium": sum(1 for s in sizes if 20 <= s < 100),
            "large":  sum(1 for s in sizes if s >= 100),
            "cal":    cal,
        }
    except Exception as e:
        return {"count": 0, "error": str(e)}


def scan_all_zones(region: str, baseline_per_zone: int = 20) -> list[dict]:
    """
    Scannt alle strategischen Zonen einer Region.
    Gibt Liste von Ergebnissen pro Zone zurück.
    """
    zones = SCAN_ZONES.get(region.lower(), [])
    if not zones:
        # Fallback: generischer Scan
        return [{"zone": region, "error": "Keine definierten Scan-Zonen"}]

    results = []
    for zone in zones:
        print(f"[SAR Vision] Scanne Zone: {zone['name']}...", file=sys.stderr)
        r = full_sar_analysis_zone(zone, baseline_per_zone)
        results.append(r)
        import time
        time.sleep(2)  # Copernicus Rate-Limit

    return results


def full_sar_analysis_zone(zone: dict, baseline: int = 20) -> dict:
    """Analysiert eine einzelne Scan-Zone mit Wasser-Maske."""
    from nexus_sar import _process_sar_tile, sh_available  # type: ignore

    result = {
        "zone":           zone["name"],
        "desc":           zone.get("desc", ""),
        "sar_count":      0,
        "water_count":    0,
        "blockade_signal": "UNBEKANNT",
        "confidence":     0.0,
        "details":        [],
    }

    if not sh_available():
        result["details"].append("❌ Sentinel Hub nicht verfügbar")
        return result

    lat_min, lon_min, lat_max, lon_max = zone["bbox"]

    try:
        png_bytes = _process_sar_tile(lat_min, lon_min, lat_max, lon_max, size=512)
        if not png_bytes:
            result["details"].append("❌ SAR-Bild nicht abrufbar")
            return result

        # Standard-Zählung (ohne Maske, zum Vergleich)
        standard = count_bright_clusters(png_bytes, threshold=150)
        result["sar_count"] = standard.get("count", 0)

        # CFAR-Schiffserkennung (echter Radar-Standard)
        cfar = count_ships_cfar(png_bytes, cfar_factor=2.5, water_max=90)
        result["water_count"] = cfar.get("count", 0)

        result["details"].append(
            f"📡 SAR gesamt: {result['sar_count']} | "
            f"CFAR-Schiffe: {result['water_count']} "
            f"(klein:{cfar.get('small',0)} mittel:{cfar.get('medium',0)} groß:{cfar.get('large',0)})"
        )

        # Bilder speichern
        enhanced = enhance_sar_image(png_bytes)
        zone_name = zone["name"].replace(" ", "_").lower()
        _save_sar_images(enhanced, zone_name)

        # Bewertung
        count = result["water_count"]
        if count == 0:
            result["blockade_signal"] = "LEER"
            result["confidence"]      = 0.85
            result["details"].append(f"⚠️ KEINE Schiffe im Wasser — Zone leer")
        elif count < baseline * 0.3:
            result["blockade_signal"] = "STARK_REDUZIERT"
            result["confidence"]      = 0.75
            result["details"].append(f"⚠️ Nur {count} Schiffe (Basis: ~{baseline}) — {round(count/baseline*100)}%")
        elif count < baseline * 0.7:
            result["blockade_signal"] = "REDUZIERT"
            result["confidence"]      = 0.55
            result["details"].append(f"🟡 {count} Schiffe ({round(count/baseline*100)}% von ~{baseline})")
        else:
            result["blockade_signal"] = "NORMAL"
            result["confidence"]      = 0.70
            result["details"].append(f"✅ {count} Schiffe — normaler Betrieb")

    except Exception as e:
        result["details"].append(f"❌ Fehler: {e}")

    return result


# ── Kombinierte Analyse ───────────────────────────────────────────────────────

def full_sar_analysis(region: str, baseline_ships: int = 0) -> dict:
    """
    Vollständige SAR-Analyse mit Bildverbesserung + Multi-Threshold + LLaVA.

    baseline_ships: Erwarteter Normwert (z.B. 80 für Hormuz)
    """
    from nexus_sar import detect_ships, _region_bbox, _process_sar_tile, sh_available  # type: ignore

    result = {
        "region":          region,
        "timestamp":       datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
        "sar_count":       0,
        "multi_threshold": {},
        "llava_analysis":  None,
        "blockade_signal": "UNBEKANNT",
        "confidence":      0.0,
        "details":         [],
    }

    # 1. SAR-Bild holen
    if not sh_available():
        result["details"].append("❌ Kein Sentinel Hub Token — SAR nicht verfügbar")
        return result

    bbox = _region_bbox(region)
    if not bbox:
        result["details"].append(f"❌ Region '{region}' nicht in SAR-BBox-Tabelle")
        return result

    lat_min, lon_min, lat_max, lon_max = bbox

    try:
        # SAR-Bild abrufen
        png_bytes = _process_sar_tile(lat_min, lon_min, lat_max, lon_max, size=512)
        if not png_bytes:
            result["details"].append("❌ SAR-Bild konnte nicht geladen werden")
            return result

        result["png_size"] = len(png_bytes)
        result["details"].append(f"✅ SAR-Bild geladen ({len(png_bytes)//1024}KB)")

        # 2. Standard-Zählung
        standard = count_bright_clusters(png_bytes, threshold=130)
        result["sar_count"] = standard.get("count", 0)

        # 3. Bildverbesserung + nochmalige Zählung
        enhanced = enhance_sar_image(png_bytes)
        enhanced_count = count_bright_clusters(
            enhanced.get("enhanced", png_bytes), threshold=110
        )
        result["enhanced_count"] = enhanced_count.get("count", 0)

        # 4. Multi-Threshold
        mt = multi_threshold_analysis(png_bytes)
        result["multi_threshold"] = mt
        result["details"].append(
            f"📊 Multi-Threshold: min={mt['min']} | standard={mt.get('standard_130',0)} | "
            f"max={mt['max']} | Konsensus={mt['consensus']}"
        )

        # 5. LLaVA-Analyse (nur wenn Ollama läuft)
        context = f"Blockade-Verdacht: {baseline_ships} Schiffe erwartet, nur {result['sar_count']} gefunden" if baseline_ships else ""
        llava = llava_analyse_sar(enhanced.get("contrast", png_bytes), region, context)
        if llava:
            result["llava_analysis"] = llava
            result["details"].append(f"🤖 LLaVA: {llava[:200]}")

        # 6. Bewertung gegen Baseline
        consensus = mt.get("consensus", result["sar_count"])
        if baseline_ships > 0:
            ratio = consensus / baseline_ships
            if ratio < 0.25:
                result["blockade_signal"] = "BLOCKADE_STARK"
                result["confidence"]      = 0.80
                result["details"].append(
                    f"⚠️ Nur {consensus} Schiffe ({ratio*100:.0f}% von normal {baseline_ships}) — STARKES Blockade-Signal"
                )
            elif ratio < 0.50:
                result["blockade_signal"] = "BLOCKADE_MÖGLICH"
                result["confidence"]      = 0.55
                result["details"].append(
                    f"🟡 {consensus} Schiffe ({ratio*100:.0f}% von {baseline_ships}) — deutlicher Rückgang"
                )
            elif ratio < 0.80:
                result["blockade_signal"] = "LEICHTER_RÜCKGANG"
                result["confidence"]      = 0.30
                result["details"].append(f"🟡 {consensus} Schiffe ({ratio*100:.0f}%) — moderater Rückgang")
            else:
                result["blockade_signal"] = "NORMALBETRIEB"
                result["confidence"]      = 0.75
                result["details"].append(f"✅ {consensus} Schiffe — normaler Verkehr")

        # Bild speichern für manuelle Inspektion
        _save_sar_images(enhanced, region)

    except Exception as e:
        result["details"].append(f"❌ Fehler: {e}")

    return result


def _save_sar_images(images: dict[str, bytes], region: str):
    """Speichert gefilterte SAR-Bilder für manuelle Inspektion."""
    out_dir = os.path.join(BASIS_DIR, "nexus_longtest_daten", "sar_images")
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    reg = region.replace(" ", "_").lower()

    saved = []
    for filter_name, data in images.items():
        if data:
            path = os.path.join(out_dir, f"sar_{reg}_{ts}_{filter_name}.png")
            with open(path, "wb") as f:
                f.write(data)
            saved.append(path)

    if saved:
        print(f"[SAR Vision] {len(saved)} gefilterte Bilder gespeichert: {out_dir}", file=sys.stderr)
    return saved


# ── USNS Militär-Versorgungsschiff Tracking ───────────────────────────────────

# USNS = US Navy Service Ships (MSC) — oft mit AIS, da zivil betrieben
_USNS_IDENTIFIERS = [
    "USNS", "MSC ", "T-AO", "T-AKE", "T-AFS",  # Versorger-Klassen
    "SUPPLY", "REPLENISHMENT", "LEWIS AND CLARK",
    "HENRY KAISER", "JOHN LEWIS",  # Bekannte Tanker-Klassen
]

_HORMUZ_ANCHOR_ZONE = {
    # Gebiet wo Schiffe VOR Hormuz ankern wenn sie warten
    "lat_min": 25.0, "lat_max": 27.5,
    "lon_min": 55.0, "lon_max": 58.0,
}


def check_usns_near_hormuz() -> dict:
    """
    Prüft ob USNS-Versorgungsschiffe nahe Hormuz sichtbar sind.
    USNS-Präsenz = US Navy Blockade wird versorgt.
    """
    result = {"usns_count": 0, "anchor_cluster": 0, "details": []}

    try:
        # AISStream via nexus_ais.py für Hormuz-Gebiet
        from nexus_ais import _fetch_aisstream, _fetch_globalfishingwatch  # type: ignore
        vessels = _fetch_aisstream(25.0, 55.0, 27.5, 58.0) or []
        if not vessels:
            vessels = _fetch_globalfishingwatch(25.0, 55.0, 27.5, 58.0) or []
    except Exception:
        vessels = []

    try:
        # Fallback: AISHub direkt
        if not vessels:
            import requests as _req
            r = _req.get(
                "https://data.aishub.net/ws.php",
                params={"username": "AH_ANONYMOUS_USER", "format": 1,
                        "output": "json", "compress": 0,
                        "latmin": 25.0, "latmax": 27.5,
                        "lonmin": 55.0, "lonmax": 58.0},
                timeout=10,
            )
            if r.ok:
                data = r.json()
                vessels = data[1] if isinstance(data, list) and len(data) > 1 else []
    except Exception:
        pass

    zone = _HORMUZ_ANCHOR_ZONE
    usns_found = []
    anchoring  = []

    for v in (vessels or []):
        name  = str(v.get("NAME", "") or v.get("name", "") or "").upper()
        speed = float(v.get("SOG", 99) or v.get("speed", 99) or 99)
        lat   = float(v.get("LATITUDE", 0) or v.get("lat", 0) or 0)
        lon   = float(v.get("LONGITUDE", 0) or v.get("lon", 0) or 0)

        if any(ident in name for ident in _USNS_IDENTIFIERS):
            usns_found.append(name)

        if (zone["lat_min"] <= lat <= zone["lat_max"] and
            zone["lon_min"] <= lon <= zone["lon_max"] and
            speed < 1.0):
            anchoring.append({"name": name or "unbekannt", "lat": lat, "lon": lon})

    result["usns_count"]     = len(usns_found)
    result["anchor_cluster"] = len(anchoring)
    result["total_vessels"]  = len(vessels)

    if not vessels:
        result["details"].append("❓ Keine AIS-Daten verfügbar — Hormuz-Gebiet nicht abfragbar")
    else:
        result["details"].append(f"📡 AIS: {len(vessels)} Schiffe im Hormuz-Gebiet sichtbar")
        if usns_found:
            result["details"].append(
                f"⚠️ {len(usns_found)} USNS-Versorgungsschiffe: {', '.join(usns_found[:3])}"
            )
        if len(anchoring) > 10:
            result["details"].append(
                f"⚠️ {len(anchoring)} Schiffe ankern vor Hormuz — ungewöhnlich viele (Blockade-Warteschlange?)"
            )
        elif len(anchoring) > 3:
            result["details"].append(f"🟡 {len(anchoring)} ankernde Schiffe im Wartegebiet")
        else:
            result["details"].append(f"✅ {len(anchoring)} ankernde Schiffe — normal")

    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NEXUS SAR Vision Analyse")
    parser.add_argument("--zonen",    action="store_true",
                        help="Alle Zonen einer Region scannen (z.B. alle 4 Hormuz-Zonen)")
    parser.add_argument("--region",   type=str,  default="hormuz",
                        help="SAR-Region (hormuz, bandar-abbas, rotes-meer...)")
    parser.add_argument("--baseline", type=int,  default=80,
                        help="Erwartete Schiffsanzahl (Normal-Wert, default: 80 für Hormuz)")
    parser.add_argument("--usns",     action="store_true",
                        help="Nur USNS-Versorgungsschiff-Check")
    args = parser.parse_args()

    if args.zonen:
        print(f"\n[NEXUS SAR Vision] Scanne alle {args.region}-Zonen...\n")
        results = scan_all_zones(args.region, baseline_per_zone=20)
        for r in results:
            icon = "⚠️" if "STARK" in r.get("blockade_signal","") or "LEER" in r.get("blockade_signal","") else "✅"
            print(f"  {icon} {r['zone']}: {r.get('blockade_signal','?')} ({r.get('confidence',0)*100:.0f}%) | Schiffe im Wasser: {r.get('water_count','?')}")
            for d in r.get("details", []):
                print(f"     {d}")
        sys.exit(0)

    if args.usns:
        print("\n[NEXUS SAR Vision] USNS-Check nahe Hormuz...")
        r = check_usns_near_hormuz()
        print(f"  USNS-Schiffe:      {r['usns_count']}")
        print(f"  Ankernde Schiffe:  {r['anchor_cluster']}")
        for d in r["details"]:
            print(f"  {d}")
    else:
        print(f"\n[NEXUS SAR Vision] Analysiere {args.region} (Baseline: {args.baseline} Schiffe)...")
        r = full_sar_analysis(args.region, baseline_ships=args.baseline)
        print(f"\n  SAR-Zählung:      {r['sar_count']}")
        print(f"  Enhanced-Zählung: {r.get('enhanced_count', '?')}")
        mt = r.get("multi_threshold", {})
        if mt:
            print(f"  Multi-Threshold:  min={mt.get('min','?')} / konsensus={mt.get('consensus','?')} / max={mt.get('max','?')}")
        print(f"  Blockade-Signal:  {r['blockade_signal']} ({r['confidence']*100:.0f}%)")
        print(f"\n  Details:")
        for d in r["details"]:
            print(f"    {d}")
        if r.get("llava_analysis"):
            print(f"\n  LLaVA-Analyse:")
            print(f"    {r['llava_analysis'][:400]}")
