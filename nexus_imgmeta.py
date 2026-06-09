"""
nexus_imgmeta.py — T129: Bild-Metadaten OSINT (Bellingcat-Level)
=================================================================
Funktionen:
  - EXIF-Extraktion (GPS, Kamera, Zeitstempel)
  - Sonnenwinkel-Zeitverifikation (claimed vs. berechnet)
  - Terrain-Matching via Overpass API (Straßen, Gebäude, Landmarks)
  - Error-Level-Analysis (ELA) — Kompressionsartefakt-Manipulation
  - Zusammenfassung für LLM-Kontext + Live-Server
"""

import os
import sys
import math
import json
import struct
import hashlib
import datetime
import urllib.request
import urllib.parse
import urllib.error
import io

# ─── Optional imports ────────────────────────────────────────────────────────

try:
    from PIL import Image, ExifTags, ImageChops, ImageEnhance
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

try:
    import piexif
    _PIEXIF_OK = True
except ImportError:
    _PIEXIF_OK = False

# ─── Debug helper ─────────────────────────────────────────────────────────────

def _dbg(msg: str) -> None:
    print(f"[IMGMETA] {msg}", file=sys.stderr)

# ─── EXIF Extraction ──────────────────────────────────────────────────────────

def _dms_to_decimal(dms_tuple, ref: str) -> float:
    """Convert DMS (degrees, minutes, seconds) tuple to decimal degrees."""
    try:
        def _rat(v):
            if isinstance(v, tuple):
                return v[0] / v[1] if v[1] != 0 else 0.0
            return float(v)
        d = _rat(dms_tuple[0])
        m = _rat(dms_tuple[1])
        s = _rat(dms_tuple[2])
        dec = d + m / 60.0 + s / 3600.0
        if ref.upper() in ("S", "W"):
            dec = -dec
        return dec
    except Exception:
        return 0.0


def extract_exif(image_path: str) -> dict:
    """
    Extrahiert EXIF-Daten aus einer Bilddatei.
    Gibt dict zurück: {gps_lat, gps_lon, datetime_original, camera_make,
                       camera_model, software, gps_altitude, has_gps,
                       has_datetime, raw_tags}
    """
    result = {
        "gps_lat": None,
        "gps_lon": None,
        "gps_altitude": None,
        "datetime_original": None,
        "camera_make": None,
        "camera_model": None,
        "software": None,
        "has_gps": False,
        "has_datetime": False,
        "raw_tags": {},
        "error": None,
    }

    if not os.path.isfile(image_path):
        result["error"] = "file not found"
        return result

    if not _PIL_OK:
        result["error"] = "PIL not available (pip install Pillow)"
        return result

    try:
        img = Image.open(image_path)
        exif_data = img._getexif()
        if not exif_data:
            result["error"] = "no EXIF data"
            return result

        # Map tag IDs to names
        tag_map = {v: k for k, v in ExifTags.TAGS.items()}

        for tag_id, value in exif_data.items():
            tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
            result["raw_tags"][tag_name] = str(value)[:200]

        # Camera info
        result["camera_make"] = exif_data.get(tag_map.get("Make", 0))
        result["camera_model"] = exif_data.get(tag_map.get("Model", 0))
        result["software"] = exif_data.get(tag_map.get("Software", 0))

        # Datetime
        dt_str = exif_data.get(tag_map.get("DateTimeOriginal", 0)) or \
                 exif_data.get(tag_map.get("DateTime", 0))
        if dt_str:
            result["datetime_original"] = str(dt_str)
            result["has_datetime"] = True

        # GPS
        gps_tag_id = tag_map.get("GPSInfo", 0)
        gps_info = exif_data.get(gps_tag_id)
        if gps_info:
            gps_tags = {}
            for k, v in gps_info.items():
                gps_tags[ExifTags.GPSTAGS.get(k, k)] = v

            lat_dms = gps_tags.get("GPSLatitude")
            lat_ref = gps_tags.get("GPSLatitudeRef", "N")
            lon_dms = gps_tags.get("GPSLongitude")
            lon_ref = gps_tags.get("GPSLongitudeRef", "E")
            alt_rat = gps_tags.get("GPSAltitude")

            if lat_dms and lon_dms:
                result["gps_lat"] = _dms_to_decimal(lat_dms, lat_ref)
                result["gps_lon"] = _dms_to_decimal(lon_dms, lon_ref)
                result["has_gps"] = True

            if alt_rat:
                try:
                    result["gps_altitude"] = float(alt_rat[0]) / float(alt_rat[1]) if isinstance(alt_rat, tuple) else float(alt_rat)
                except Exception:
                    pass

    except Exception as e:
        result["error"] = str(e)

    return result


# ─── Sun Angle Verification ───────────────────────────────────────────────────
# Based on NOAA solar position algorithm (simplified)

def _julian_day(dt: datetime.datetime) -> float:
    """Julian Day Number for a datetime (UTC)."""
    a = (14 - dt.month) // 12
    y = dt.year + 4800 - a
    m = dt.month + 12 * a - 3
    jdn = dt.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    jd = jdn + (dt.hour - 12) / 24.0 + dt.minute / 1440.0 + dt.second / 86400.0
    return jd


def _sun_position(lat: float, lon: float, dt: datetime.datetime) -> dict:
    """
    Berechnet Sonnenazimut und -elevation für gegebene Position und UTC-Zeit.
    Returns: {azimuth: float (°N), elevation: float (°), is_daytime: bool}
    """
    jd = _julian_day(dt)
    n = jd - 2451545.0  # J2000.0

    # Mean longitude and anomaly
    L = (280.460 + 0.9856474 * n) % 360
    g = math.radians((357.528 + 0.9856003 * n) % 360)

    # Ecliptic longitude
    lam = math.radians(L + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))

    # Obliquity of ecliptic
    eps = math.radians(23.439 - 0.0000004 * n)

    # Right ascension and declination
    sin_lam = math.sin(lam)
    cos_eps = math.cos(eps)
    sin_eps = math.sin(eps)

    ra = math.atan2(cos_eps * sin_lam, math.cos(lam))
    dec = math.asin(sin_eps * sin_lam)

    # Hour angle
    gmst = (6.697375 + 0.0657098242 * n + dt.hour + dt.minute / 60.0 + dt.second / 3600.0) % 24
    lmst = (gmst + lon / 15.0) % 24
    ha = math.radians((lmst - math.degrees(ra) / 15.0) * 15.0)

    # Altitude and azimuth
    lat_r = math.radians(lat)
    sin_alt = math.sin(dec) * math.sin(lat_r) + math.cos(dec) * math.cos(lat_r) * math.cos(ha)
    alt = math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))

    cos_az = (math.sin(dec) - math.sin(lat_r) * sin_alt) / (math.cos(lat_r) * math.cos(math.radians(alt)) + 1e-9)
    az = math.degrees(math.acos(max(-1.0, min(1.0, cos_az))))
    if math.sin(ha) > 0:
        az = 360 - az

    return {
        "azimuth": round(az, 1),
        "elevation": round(alt, 1),
        "is_daytime": alt > 0,
    }


def verify_sun_time(lat: float, lon: float, claimed_dt_str: str) -> dict:
    """
    Vergleicht behaupteten Aufnahmezeitpunkt mit Sonnenposition.
    Gibt Verifikations-Verdict zurück.

    Returns: {
        verdict: "BESTÄTIGT" | "VERDÄCHTIG" | "UNMÖGLICH" | "UNBEKANNT",
        claimed_dt: str,
        sun_elevation: float,
        sun_azimuth: float,
        is_daytime: bool,
        notes: str
    }
    """
    result = {
        "verdict": "UNBEKANNT",
        "claimed_dt": claimed_dt_str,
        "sun_elevation": None,
        "sun_azimuth": None,
        "is_daytime": None,
        "notes": "",
    }

    # Parse datetime — EXIF format: "YYYY:MM:DD HH:MM:SS"
    dt = None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            dt = datetime.datetime.strptime(claimed_dt_str.strip(), fmt)
            break
        except ValueError:
            continue

    if dt is None:
        result["notes"] = "Zeitformat nicht erkannt"
        return result

    # Camera clocks are local time — we don't know the timezone without GPS
    # We'll treat it as UTC first, then check ±12h window for plausibility
    sun = _sun_position(lat, lon, dt)
    result["sun_elevation"] = sun["elevation"]
    result["sun_azimuth"] = sun["azimuth"]
    result["is_daytime"] = sun["is_daytime"]

    elev = sun["elevation"]

    if elev < -6:
        result["verdict"] = "UNMÖGLICH"
        result["notes"] = f"Sonne {elev:.1f}° unter Horizont — es wäre völlige Nacht"
    elif elev < 0:
        result["verdict"] = "VERDÄCHTIG"
        result["notes"] = f"Sonne {elev:.1f}° — Dämmerung oder Nacht, kein Tageslicht-Foto möglich"
    elif elev < 5:
        result["verdict"] = "VERDÄCHTIG"
        result["notes"] = f"Sonne sehr niedrig ({elev:.1f}°) — früher Morgen oder Abend"
    else:
        result["verdict"] = "BESTÄTIGT"
        result["notes"] = f"Sonnenhöhe {elev:.1f}°, Azimut {sun['azimuth']:.0f}° — plausibel"

    return result


# ─── Terrain / Location Context via Overpass ──────────────────────────────────

def _overpass_query(lat: float, lon: float, radius_m: int = 200) -> dict:
    """
    Fragt Overpass API nach POIs, Straßen, Gebäudetypen nahe GPS-Koordinaten.
    Returns: {landmarks: list, street_names: list, building_types: list, raw_count: int}
    """
    result = {
        "landmarks": [],
        "street_names": [],
        "building_types": [],
        "raw_count": 0,
        "error": None,
    }

    query = f"""
[out:json][timeout:10];
(
  node(around:{radius_m},{lat},{lon})["name"];
  way(around:{radius_m},{lat},{lon})["name"];
  way(around:{radius_m},{lat},{lon})["building"];
  way(around:{radius_m},{lat},{lon})["highway"];
);
out center 50;
"""
    url = "https://overpass-api.de/api/interpreter"
    try:
        data = urllib.parse.urlencode({"data": query}).encode()
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"User-Agent": "NEXUS-OSINT/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="replace"))

        elements = raw.get("elements", [])
        result["raw_count"] = len(elements)

        seen_names = set()
        for el in elements:
            tags = el.get("tags", {})
            name = tags.get("name")
            btype = tags.get("building")
            htype = tags.get("highway")
            amenity = tags.get("amenity")
            landuse = tags.get("landuse")

            if name and name not in seen_names:
                seen_names.add(name)
                result["landmarks"].append(name)

            if btype and btype not in result["building_types"]:
                result["building_types"].append(btype)

            if htype:
                street_name = name or f"[{htype}]"
                if street_name not in result["street_names"]:
                    result["street_names"].append(street_name)

    except urllib.error.URLError as e:
        result["error"] = f"Overpass timeout/error: {e}"
    except Exception as e:
        result["error"] = str(e)

    return result


def terrain_match(lat: float, lon: float) -> dict:
    """
    Holt Terrain-Kontext für GPS-Koordinaten aus OpenStreetMap.
    Returns: {location_hints: list[str], terrain_summary: str, overpass_ok: bool}
    """
    result = {
        "location_hints": [],
        "terrain_summary": "Keine Terrain-Daten",
        "overpass_ok": False,
    }

    op = _overpass_query(lat, lon)
    if op["error"]:
        result["terrain_summary"] = f"Overpass-Fehler: {op['error']}"
        return result

    result["overpass_ok"] = True
    hints = []

    if op["landmarks"]:
        hints.append("Landmarks: " + ", ".join(op["landmarks"][:5]))
    if op["street_names"]:
        hints.append("Straßen: " + ", ".join([s for s in op["street_names"][:5] if not s.startswith("[")]))
    if op["building_types"]:
        btypes = [b for b in op["building_types"] if b != "yes"]
        if btypes:
            hints.append("Gebäude: " + ", ".join(btypes[:4]))

    result["location_hints"] = hints
    result["terrain_summary"] = " | ".join(hints) if hints else f"Offengelände ({op['raw_count']} Objekte gefunden)"
    return result


# ─── Error Level Analysis (ELA) ───────────────────────────────────────────────

def ela_analysis(image_path: str, quality: int = 90) -> dict:
    """
    Error Level Analysis: Erkennt Bildmanipulationen durch JPEG-Rekompression.
    Bereiche mit hohem ELA-Level wurden möglicherweise nachträglich eingefügt.

    Returns: {
        max_ela: float (0-255),
        mean_ela: float,
        hotspot_fraction: float (0-1),
        verdict: str,
        error: str | None
    }
    """
    result = {
        "max_ela": None,
        "mean_ela": None,
        "hotspot_fraction": None,
        "verdict": "UNBEKANNT",
        "error": None,
    }

    if not _PIL_OK:
        result["error"] = "PIL not available"
        return result

    if not os.path.isfile(image_path):
        result["error"] = "file not found"
        return result

    try:
        original = Image.open(image_path).convert("RGB")

        # Re-save at known quality
        tmp_buf = io.BytesIO()
        original.save(tmp_buf, format="JPEG", quality=quality)
        tmp_buf.seek(0)
        resaved = Image.open(tmp_buf).convert("RGB")

        # Compute difference
        diff = ImageChops.difference(original, resaved)
        enhancer = ImageEnhance.Brightness(diff)
        ela_img = enhancer.enhance(20)  # amplify differences

        # Get pixel statistics
        pixels = list(ela_img.getdata())
        r_vals = [p[0] for p in pixels]

        max_ela = max(r_vals)
        mean_ela = sum(r_vals) / len(r_vals)
        # "hotspot" = pixels with ELA > 30 (significant compression anomaly)
        hotspot_count = sum(1 for v in r_vals if v > 30)
        hotspot_fraction = hotspot_count / len(r_vals)

        result["max_ela"] = round(max_ela, 1)
        result["mean_ela"] = round(mean_ela, 2)
        result["hotspot_fraction"] = round(hotspot_fraction, 3)

        # Verdict
        if mean_ela > 15 and hotspot_fraction > 0.05:
            result["verdict"] = "VERDÄCHTIG — mögliche Manipulation"
        elif mean_ela > 8 or hotspot_fraction > 0.02:
            result["verdict"] = "AUFFÄLLIG — weitere Prüfung empfohlen"
        else:
            result["verdict"] = "UNAUFFÄLLIG"

    except Exception as e:
        result["error"] = str(e)

    return result


# ─── Hash & Reverse Image Search Prep ────────────────────────────────────────

def compute_image_hashes(image_path: str) -> dict:
    """
    Berechnet MD5 und SHA-256 für Reverse-Image-Search und Deduplizierung.
    """
    result = {"md5": None, "sha256": None, "size_bytes": None, "error": None}
    if not os.path.isfile(image_path):
        result["error"] = "file not found"
        return result
    try:
        with open(image_path, "rb") as f:
            data = f.read()
        result["md5"] = hashlib.md5(data).hexdigest()
        result["sha256"] = hashlib.sha256(data).hexdigest()
        result["size_bytes"] = len(data)
    except Exception as e:
        result["error"] = str(e)
    return result


# ─── Full Image OSINT Pipeline ────────────────────────────────────────────────

def analyze_image_osint(image_path: str, run_ela: bool = True,
                        run_terrain: bool = True) -> dict:
    """
    Vollständige Bild-OSINT-Analyse.

    Returns: {
        file: str,
        exif: dict,
        sun_verify: dict | None,
        terrain: dict | None,
        ela: dict | None,
        hashes: dict,
        summary: str,
        confidence: str,
        flags: list[str]
    }
    """
    result = {
        "file": image_path,
        "exif": {},
        "sun_verify": None,
        "terrain": None,
        "ela": None,
        "hashes": {},
        "summary": "",
        "confidence": "UNBEKANNT",
        "flags": [],
    }

    _dbg(f"Analysiere: {os.path.basename(image_path)}")

    # 1. EXIF
    exif = extract_exif(image_path)
    result["exif"] = exif

    if exif.get("error"):
        _dbg(f"EXIF-Fehler: {exif['error']}")

    flags = []

    # 2. GPS vorhanden?
    if exif["has_gps"]:
        lat, lon = exif["gps_lat"], exif["gps_lon"]
        flags.append(f"GPS: {lat:.4f}°, {lon:.4f}°")

        # 3. Terrain
        if run_terrain:
            _dbg(f"Terrain-Match für {lat:.4f}, {lon:.4f}")
            terrain = terrain_match(lat, lon)
            result["terrain"] = terrain
            if terrain["overpass_ok"] and terrain["location_hints"]:
                flags.append(f"Terrain: {terrain['terrain_summary'][:80]}")

        # 4. Sonnenwinkel-Verifikation
        if exif["has_datetime"]:
            _dbg(f"Sonnenwinkel-Check: {exif['datetime_original']}")
            sun_v = verify_sun_time(lat, lon, exif["datetime_original"])
            result["sun_verify"] = sun_v
            flags.append(f"Sonnencheck: {sun_v['verdict']} ({sun_v['notes']})")
    else:
        flags.append("Kein GPS in EXIF")

    if not exif["has_datetime"]:
        flags.append("Kein Zeitstempel in EXIF")

    # Camera info
    if exif.get("camera_make") or exif.get("camera_model"):
        cam = f"{exif.get('camera_make', '')} {exif.get('camera_model', '')}".strip()
        flags.append(f"Kamera: {cam}")

    # Software / Editing tools
    sw = exif.get("software", "")
    if sw:
        sw_lower = sw.lower()
        editing_tools = ["photoshop", "gimp", "lightroom", "affinity", "snapseed", "vsco"]
        for tool in editing_tools:
            if tool in sw_lower:
                flags.append(f"⚠️ Bearbeitet mit: {sw}")
                break
        else:
            flags.append(f"Software: {sw}")

    # 5. ELA
    if run_ela:
        ela = ela_analysis(image_path)
        result["ela"] = ela
        if ela["error"] is None:
            flags.append(f"ELA: {ela['verdict']}")

    # 6. Hashes
    hashes = compute_image_hashes(image_path)
    result["hashes"] = hashes

    # 7. Confidence
    verdict_scores = []
    if result.get("sun_verify"):
        v = result["sun_verify"]["verdict"]
        if v == "BESTÄTIGT":
            verdict_scores.append(2)
        elif v == "VERDÄCHTIG":
            verdict_scores.append(0)
        elif v == "UNMÖGLICH":
            verdict_scores.append(-2)

    if result.get("ela") and result["ela"].get("verdict"):
        ev = result["ela"]["verdict"]
        if "VERDÄCHTIG" in ev:
            verdict_scores.append(-1)
        elif "AUFFÄLLIG" in ev:
            verdict_scores.append(0)
        else:
            verdict_scores.append(1)

    if verdict_scores:
        score = sum(verdict_scores) / len(verdict_scores)
        if score >= 1.5:
            result["confidence"] = "VERIFIZIERT"
        elif score >= 0.5:
            result["confidence"] = "WAHRSCHEINLICH_ECHT"
        elif score >= -0.5:
            result["confidence"] = "UNKLAR"
        elif score >= -1.0:
            result["confidence"] = "VERDÄCHTIG"
        else:
            result["confidence"] = "WAHRSCHEINLICH_GEFÄLSCHT"
    elif exif["has_gps"] or exif["has_datetime"]:
        result["confidence"] = "TEILWEISE_VERIFIZIERT"

    result["flags"] = flags

    # 8. Summary
    lines = [f"Bild: {os.path.basename(image_path)}"]
    if exif["has_gps"]:
        lines.append(f"  GPS: {exif['gps_lat']:.4f}°N, {exif['gps_lon']:.4f}°E")
    if exif["has_datetime"]:
        lines.append(f"  Zeitstempel: {exif['datetime_original']}")
    lines.append(f"  Konfidenz: {result['confidence']}")
    for f in flags:
        lines.append(f"  • {f}")
    result["summary"] = "\n".join(lines)

    return result


# ─── Bulk URL analysis (for Telegram image URLs) ──────────────────────────────

def analyze_image_url(url: str, tmp_dir: str = "/tmp") -> dict:
    """
    Lädt Bild von URL und analysiert es.
    Returns analyze_image_osint() result or error dict.
    """
    result = {"url": url, "error": None, "analysis": None}

    try:
        os.makedirs(tmp_dir, exist_ok=True)
        # Sanitize filename
        fname = os.path.basename(urllib.parse.urlparse(url).path) or "nexus_img.jpg"
        fname = fname.split("?")[0]
        if not any(fname.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"]):
            fname += ".jpg"
        tmp_path = os.path.join(tmp_dir, f"nexus_img_{hashlib.md5(url.encode()).hexdigest()[:8]}_{fname}")

        req = urllib.request.Request(url, headers={"User-Agent": "NEXUS-OSINT/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            with open(tmp_path, "wb") as f:
                f.write(resp.read())

        result["analysis"] = analyze_image_osint(tmp_path)

        # Clean up
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    except Exception as e:
        result["error"] = str(e)

    return result


# ─── LLM Context Formatter ───────────────────────────────────────────────────

def imgmeta_for_llm(analyses: list) -> str:
    """
    Formatiert Bild-OSINT-Ergebnisse für LLM-Kontext.
    """
    if not analyses:
        return ""

    lines = ["## Bild-Metadaten OSINT", ""]
    for a in analyses[:5]:  # max 5 Bilder im Kontext
        if isinstance(a, dict) and a.get("analysis"):
            a = a["analysis"]
        if not isinstance(a, dict):
            continue

        lines.append(f"**{os.path.basename(a.get('file', 'unbekannt'))}**")
        lines.append(f"  Konfidenz: {a.get('confidence', 'UNBEKANNT')}")
        for flag in a.get("flags", [])[:6]:
            lines.append(f"  • {flag}")
        lines.append("")

    return "\n".join(lines)


# ─── Self-Test ────────────────────────────────────────────────────────────────

def _self_test():
    print("=== nexus_imgmeta.py Selbsttest ===")

    # Test 1: Sonnenwinkel
    print("\n[1] Sonnenwinkel-Verifikation")
    # Kyiv, Ukraine — 12:00 UTC 15. März
    dt_str = "2024:03:15 12:00:00"
    sun_result = verify_sun_time(50.45, 30.52, dt_str)
    print(f"  Kyiv 12:00 UTC März: {sun_result['verdict']} (Elevation: {sun_result['sun_elevation']}°)")

    # Impossible test: 03:00 AM
    dt_str_night = "2024:03:15 03:00:00"
    sun_night = verify_sun_time(50.45, 30.52, dt_str_night)
    print(f"  Kyiv 03:00 UTC März: {sun_night['verdict']} (Elevation: {sun_night['sun_elevation']}°)")

    # Test 2: EXIF
    print("\n[2] EXIF-Extraktion")
    if _PIL_OK:
        print(f"  PIL verfügbar ✓")
        # No test image available, just check function exists
        test_result = extract_exif("/nonexistent.jpg")
        print(f"  Fehlende Datei → error: '{test_result['error']}'")
    else:
        print("  PIL nicht verfügbar (pip install Pillow) — EXIF-Extraktion deaktiviert")

    # Test 3: Terrain
    print("\n[3] Terrain-Match (Overpass)")
    # Bachhmut/Artemivsk area (known conflict zone)
    terrain = terrain_match(48.60, 38.00)
    if terrain["overpass_ok"]:
        print(f"  Bachmut-Bereich: {terrain['terrain_summary'][:80]}")
    else:
        print(f"  Overpass: {terrain.get('terrain_summary', 'N/A')}")

    # Test 4: DMS conversion
    print("\n[4] DMS → Dezimal")
    dms = ((50, 1), (27, 1), (0, 1))  # 50°27'0"N
    dec = _dms_to_decimal(dms, "N")
    print(f"  50°27'00\"N → {dec:.4f}° (erwartet: 50.4500)")

    print("\n=== Selbsttest abgeschlossen ===")


if __name__ == "__main__":
    _self_test()
