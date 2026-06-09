"""
nexus_video.py  –  Video-Keyframe-Analyse fuer OSINT

Problem:
  Telegram-Kanaele posten taeglich Dutzende Kriegsvideos.
  NEXUS ignoriert sie komplett — dabei enthalten sie:
  - Fahrzeugtypen (T-72 vs Leopard 2)
  - Uniformen (russisch/ukrainisch/Wagner)
  - Waffensysteme im Einsatz
  - Schadensbewertung (zerstoert / beschaedigt / intakt)
  - Geolokalisierungshinweise (Gebaeude, Landschaft, Schilder)

Loesung:
  1. ffmpeg extrahiert Keyframes (1 Frame pro Sekunde, max. 10 Frames)
  2. LLaVA (via nexus_vision.py / Ollama) analysiert jeden Frame
  3. Ergebnisse werden konsolidiert + fuer LLM-Kontext formatiert

Ohne ffmpeg / LLaVA:
  Fallback-Analyse via Dateiname, URL-Keywords, Dateigroesse-Heuristik.
  Liefert weniger, aber crasht nicht.

Integration:
  - nexus_telegram.py: Videos aus Telegram-Posts extrahieren
  - main.py: 'v <url>' Befehl fuer Video-Analyse
  - nexus_live_server.py: video_analysis in Pipeline-Result

Abhaengigkeiten (optional, graceful fallback):
  - ffmpeg (muss im PATH sein)
  - nexus_vision.py (Ollama LLaVA)
  - Pillow (PIL) fuer Frame-Groessen-Check
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime
from typing import Optional


# ── Konfiguration ─────────────────────────────────────────────────────────────
_MAX_FRAMES     = 10       # Max Frames pro Video
_FRAME_INTERVAL = 1.0      # Sekunden zwischen Frames
_MAX_DURATION   = 120      # Videos laenger als 2min werden gekuerzt
_MIN_FILESIZE   = 50_000   # < 50KB = kein echtes Video

# ── Bekannte Militaer-Keywords in Dateinamen/URLs ─────────────────────────────
_MILITARY_KEYWORDS = [
    # Fahrzeuge
    "tank", "panzer", "t-72", "t-90", "leopard", "abrams", "bradley",
    "bmp", "btr", "ifv", "apc", "armored",
    # Artillerie
    "artillery", "howitzer", "grad", "mlrs", "himars", "rocket",
    # Luftfahrt
    "drone", "uav", "fpv", "shahed", "lancet", "helicopter", "ka-52",
    # Allgemein Konflikt
    "strike", "attack", "destroyed", "hit", "explosion", "combat",
    "war", "ukraine", "russia", "front", "battle", "assault",
    # Russisch transliteriert
    "podryv", "vzryv", "ataka", "udar",
]

# ── Fahrzeug-Erkennungs-Patterns fuer LLaVA-Prompts ─────────────────────────
_VEHICLE_TYPES = {
    "T-72": ["T-72", "T72"],
    "T-80": ["T-80", "T80"],
    "T-90": ["T-90", "T90"],
    "T-64": ["T-64", "T64"],
    "Leopard 2": ["Leopard", "Leo 2"],
    "M1 Abrams": ["Abrams", "M1A2"],
    "Bradley": ["Bradley", "IFV Bradley"],
    "BMP-1/2/3": ["BMP"],
    "BTR": ["BTR"],
    "Marder": ["Marder"],
    "MaxxPro/MRAP": ["MaxxPro", "MRAP", "Humvee"],
    "Grad/MLRS": ["Grad", "BM-21", "MLRS"],
    "Howitzer": ["howitzer", "D-30", "D-20", "2S1", "2S3", "M777", "PzH"],
    "Helicopter": ["helicopter", "Mi-8", "Mi-24", "Ka-52", "Mi-28"],
    "Drone/UAV": ["drone", "UAV", "Shahed", "Lancet", "Orlan", "FPV"],
}


# ============================================================
# FFMPEG-INTERFACE
# ============================================================

def _has_ffmpeg() -> bool:
    """Prueft ob ffmpeg im PATH verfuegbar ist."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _get_video_duration(path: str) -> float:
    """Gibt Video-Laenge in Sekunden zurueck. -1 bei Fehler."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", path],
            capture_output=True, timeout=15,
        )
        if r.returncode != 0:
            return -1.0
        info = json.loads(r.stdout)
        return float(info.get("format", {}).get("duration", -1))
    except Exception:
        return -1.0


def extract_keyframes(video_path: str,
                      max_frames: int = _MAX_FRAMES,
                      interval: float = _FRAME_INTERVAL,
                      output_dir: Optional[str] = None) -> list[str]:
    """
    Extrahiert Keyframes aus Video via ffmpeg.

    Returns:
        Liste von Pfaden zu den extrahierten Frame-JPEGs.
        Leere Liste wenn ffmpeg nicht verfuegbar oder Fehler.
    """
    if not os.path.exists(video_path):
        return []
    if os.path.getsize(video_path) < _MIN_FILESIZE:
        return []
    if not _has_ffmpeg():
        return []

    # Video-Laenge bestimmen
    duration = _get_video_duration(video_path)
    if duration <= 0:
        duration = 60.0  # Fallback: 60s annehmen

    # Tatsaechliches Intervall berechnen
    if duration > _MAX_DURATION:
        actual_interval = _MAX_DURATION / max_frames
    else:
        actual_interval = max(interval, duration / max_frames)

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="nexus_frames_")

    frame_pattern = os.path.join(output_dir, "frame_%04d.jpg")

    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"fps=1/{actual_interval:.1f}",
            "-frames:v", str(max_frames),
            "-q:v", "3",       # JPEG-Qualitaet (1=beste, 31=schlechteste)
            "-vf", f"scale=640:-1,fps=1/{actual_interval:.1f}",  # 640px breit
            frame_pattern,
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        if r.returncode != 0:
            return []

        frames = sorted([
            os.path.join(output_dir, f)
            for f in os.listdir(output_dir)
            if f.startswith("frame_") and f.endswith(".jpg")
        ])
        return frames[:max_frames]
    except Exception:
        return []


# ============================================================
# LLaVA-ANALYSE
# ============================================================

_LLAVA_PROMPT = """Analyze this military/conflict video frame for OSINT purposes.
Identify and describe:
1. Military vehicles (type, nation if identifiable, condition: intact/damaged/destroyed)
2. Weapons/equipment visible
3. Personnel (military/civilian, uniform type if visible)
4. Geographic/environmental clues (terrain, buildings, signs, season)
5. Damage assessment if applicable
6. Any text, markings, or insignia visible

Be specific and factual. Use military terminology. If uncertain, say so.
Format: brief bullet points only."""


def _analyze_frame_llava(frame_path: str) -> str:
    """Analysiert einen Frame mit LLaVA via nexus_vision.py."""
    try:
        from nexus_vision import analyze_image  # type: ignore
        result = analyze_image(frame_path, prompt=_LLAVA_PROMPT)
        return result or ""
    except Exception:
        return ""


def _analyze_frame_fallback(frame_path: str) -> str:
    """
    Fallback-Analyse ohne LLaVA: nur Dateigroesse + Basisdaten.
    Liefert wenig, crasht aber nicht.
    """
    try:
        size = os.path.getsize(frame_path)
        return f"[Frame gespeichert, {size//1024}KB — LLaVA nicht verfuegbar]"
    except Exception:
        return "[Frame-Analyse nicht moeglich]"


# ============================================================
# KEYWORD-ANALYSE (ohne ffmpeg/LLaVA)
# ============================================================

def _analyze_url_keywords(url: str, filename: str = "") -> dict:
    """
    Analysiert URL und Dateiname auf Militaer-Keywords.
    Schnelle Heuristik wenn kein ffmpeg verfuegbar.
    """
    combined = (url + " " + filename).lower()
    found_keywords = [kw for kw in _MILITARY_KEYWORDS if kw in combined]

    result = {
        "method":        "keyword_heuristic",
        "military_relevant": len(found_keywords) > 0,
        "keywords_found": found_keywords,
        "confidence":    "niedrig",
        "note":          "Analyse nur auf URL/Dateiname-Basis (kein ffmpeg)",
    }

    # Fahrzeugtypen aus Keywords ableiten
    vehicles = []
    for vtype, patterns in _VEHICLE_TYPES.items():
        if any(p.lower() in combined for p in patterns):
            vehicles.append(vtype)
    if vehicles:
        result["vehicles_detected"] = vehicles

    return result


# ============================================================
# HAUPT-ANALYSE
# ============================================================

def analyze_video(video_path: str,
                  url: str = "",
                  use_llava: bool = True,
                  cleanup_frames: bool = True) -> dict:
    """
    Vollstaendige Video-OSINT-Analyse.

    Args:
        video_path:     Lokaler Pfad zum Video
        url:            Ursprungs-URL (fuer Keyword-Analyse)
        use_llava:      LLaVA verwenden wenn verfuegbar
        cleanup_frames: Extrahierte Frames nach Analyse loeschen

    Returns:
        dict mit Analyse-Ergebnissen:
          method, frames_analyzed, vehicles, personnel, damage,
          geo_clues, text_visible, summary, confidence, raw_results
    """
    start = time.time()
    filename = os.path.basename(video_path)

    result = {
        "video":          filename,
        "url":            url,
        "method":         "none",
        "analyzed_at":    datetime.utcnow().isoformat(),
        "duration_s":     -1,
        "frames_analyzed": 0,
        "vehicles":       [],
        "weapons":        [],
        "personnel":      [],
        "damage":         [],
        "geo_clues":      [],
        "text_visible":   [],
        "summary":        "",
        "confidence":     "niedrig",
        "raw_frames":     [],
        "analysis_time_s": 0,
    }

    # ── Schritt 1: Keyword-Heuristik (immer) ─────────────────────────────────
    kw_result = _analyze_url_keywords(url, filename)
    result["keyword_analysis"] = kw_result

    # ── Schritt 2: ffmpeg Keyframe-Extraktion ─────────────────────────────────
    if not os.path.exists(video_path):
        result["error"] = "Video nicht gefunden"
        result["summary"] = "[VIDEO] Datei nicht gefunden: " + filename
        return result

    if os.path.getsize(video_path) < _MIN_FILESIZE:
        result["error"] = "Datei zu klein"
        result["summary"] = f"[VIDEO] Datei zu klein ({os.path.getsize(video_path)} bytes)"
        return result

    duration = _get_video_duration(video_path)
    result["duration_s"] = round(duration, 1)

    frame_dir  = tempfile.mkdtemp(prefix="nexus_frames_")
    frames     = extract_keyframes(video_path, output_dir=frame_dir)

    if not frames:
        # Kein ffmpeg — Keyword-only-Ergebnis zurueckgeben
        result["method"]  = "keyword_only"
        result["summary"] = _build_keyword_summary(kw_result, filename)
        result["analysis_time_s"] = round(time.time() - start, 1)
        return result

    result["frames_analyzed"] = len(frames)

    # ── Schritt 3: Frame-Analyse ──────────────────────────────────────────────
    frame_results = []
    has_llava = use_llava

    for i, fp in enumerate(frames):
        if use_llava:
            analysis = _analyze_frame_llava(fp)
            if not analysis:
                has_llava = False
                analysis = _analyze_frame_fallback(fp)
        else:
            analysis = _analyze_frame_fallback(fp)

        frame_results.append({
            "frame":    i + 1,
            "path":     fp,
            "analysis": analysis,
        })

    result["method"]     = "llava_frames" if has_llava else "frames_no_llava"
    result["raw_frames"] = [
        {"frame": r["frame"], "analysis": r["analysis"]}
        for r in frame_results
    ]

    # ── Schritt 4: Konsolidierung ─────────────────────────────────────────────
    all_text = " ".join(r["analysis"] for r in frame_results).lower()

    # Fahrzeuge
    for vtype, patterns in _VEHICLE_TYPES.items():
        if any(p.lower() in all_text for p in patterns):
            result["vehicles"].append(vtype)

    # Schaden
    damage_kw = ["destroyed", "burning", "hit", "knocked out", "immobilized",
                 "zerstoert", "brennt", "getroffen", "damaged"]
    for kw in damage_kw:
        if kw in all_text and kw not in result["damage"]:
            result["damage"].append(kw)

    # Geo-Hinweise
    geo_kw = ["urban", "rural", "forest", "field", "road", "building",
              "sign", "snow", "summer", "winter", "mud", "wheat",
              "steppe", "industrial"]
    for kw in geo_kw:
        if kw in all_text:
            result["geo_clues"].append(kw)

    # Konfidenz
    if has_llava and len(frames) >= 3:
        result["confidence"] = "hoch"
    elif has_llava or len(frames) >= 5:
        result["confidence"] = "mittel"

    # Summary
    result["summary"] = _build_llava_summary(result, filename, duration)
    result["analysis_time_s"] = round(time.time() - start, 1)

    # Cleanup
    if cleanup_frames:
        try:
            import shutil
            shutil.rmtree(frame_dir, ignore_errors=True)
        except Exception:
            pass

    return result


def _build_keyword_summary(kw: dict, filename: str) -> str:
    """Summary ohne ffmpeg."""
    if kw.get("military_relevant"):
        kws = ", ".join(kw.get("keywords_found", [])[:5])
        vehs = ", ".join(kw.get("vehicles_detected", []))
        parts = [f"[VIDEO] {filename}"]
        if vehs:
            parts.append(f"Fahrzeuge (Heuristik): {vehs}")
        if kws:
            parts.append(f"Keywords: {kws}")
        parts.append("(Analyse ohne ffmpeg — nur URL/Dateiname)")
        return " | ".join(parts)
    return f"[VIDEO] {filename} — keine Militaer-Keywords erkannt"


def _build_llava_summary(result: dict, filename: str, duration: float) -> str:
    """Summary aus LLaVA-Analyse."""
    parts = [f"[VIDEO] {filename}"]
    dur_s = f"{int(duration)}s" if duration > 0 else "?"
    parts.append(f"{result['frames_analyzed']} Frames/{dur_s}")

    if result["vehicles"]:
        parts.append("Fahrzeuge: " + ", ".join(result["vehicles"][:4]))
    if result["damage"]:
        parts.append("Schaden erkannt")
    if result["geo_clues"]:
        parts.append("Umgebung: " + ", ".join(result["geo_clues"][:3]))

    parts.append(f"Konfidenz: {result['confidence']}")
    return " | ".join(parts)


# ============================================================
# DOWNLOAD-HILFSFUNKTION
# ============================================================

def download_video(url: str, output_dir: Optional[str] = None,
                   timeout: int = 30) -> Optional[str]:
    """
    Laedt ein Video von einer URL herunter.
    Unterstuetzt: direkte MP4/WebM-Links, Telegram CDN-Links.

    Returns:
        Lokaler Dateipfad oder None bei Fehler.
    """
    import requests as _req

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="nexus_video_")

    # Dateiname aus URL ableiten
    fname = url.split("/")[-1].split("?")[0]
    if not fname.lower().endswith((".mp4", ".webm", ".mkv", ".avi", ".mov")):
        fname = "video.mp4"
    fpath = os.path.join(output_dir, fname)

    try:
        headers = {"User-Agent": "NEXUS-OSINT/1.0 (educational)"}
        with _req.get(url, headers=headers, timeout=timeout,
                      stream=True) as r:
            r.raise_for_status()
            content_type = r.headers.get("content-type", "")
            if "video" not in content_type and "octet" not in content_type:
                return None
            with open(fpath, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
        if os.path.getsize(fpath) < _MIN_FILESIZE:
            os.unlink(fpath)
            return None
        return fpath
    except Exception:
        return None


# ============================================================
# PIPELINE-INTEGRATION
# ============================================================

def analyze_video_url(url: str, use_llava: bool = True) -> dict:
    """
    Kombiniert Download + Analyse in einem Aufruf.
    Ideal fuer Pipeline-Nutzung.
    """
    if not url:
        return {"error": "Keine URL angegeben", "summary": ""}

    # Zuerst Keyword-Analyse (instant, kein Download)
    kw = _analyze_url_keywords(url)
    if not kw.get("military_relevant"):
        # Nicht military-relevant — kein Download
        return {
            "method":   "keyword_skip",
            "summary":  f"[VIDEO] Nicht militaer-relevant — kein Download ({url[:60]})",
            "skipped":  True,
        }

    # Download
    fpath = download_video(url)
    if not fpath:
        # Download gescheitert — nur Keyword-Ergebnis
        return {
            "method":   "keyword_only",
            "summary":  _build_keyword_summary(kw, url.split("/")[-1]),
            "keyword_analysis": kw,
        }

    # Vollanalyse
    try:
        result = analyze_video(fpath, url=url, use_llava=use_llava)
        return result
    finally:
        try:
            os.unlink(fpath)
        except Exception:
            pass


def videos_for_llm(video_results: list[dict], max_videos: int = 5) -> str:
    """
    Formatiert Video-Analyse-Ergebnisse fuer LLM-Kontext.
    """
    if not video_results:
        return ""

    lines = ["=== VIDEO-ANALYSE (OSINT Keyframe-Scan) ==="]
    relevant = [v for v in video_results if not v.get("skipped")][:max_videos]

    if not relevant:
        return ""

    for v in relevant:
        summary = v.get("summary", "")
        if summary:
            lines.append(f"  {summary}")

        # Rohe Frame-Analysen (erste 3)
        for fr in v.get("raw_frames", [])[:3]:
            analysis = fr.get("analysis", "").strip()
            if analysis and "[Frame gespeichert" not in analysis:
                lines.append(f"    Frame {fr['frame']}: {analysis[:200]}")

    if len(lines) <= 1:
        return ""

    return "\n".join(lines)


def video_summary(results: list[dict]) -> str:
    """Terminal-Ausgabe."""
    if not results:
        return "[VIDEO] Keine Videos analysiert"
    n_total    = len(results)
    n_relevant = len([r for r in results if not r.get("skipped")])
    n_frames   = sum(r.get("frames_analyzed", 0) for r in results)
    vehicles   = []
    for r in results:
        vehicles.extend(r.get("vehicles", []))
    veh_str = " | ".join(sorted(set(vehicles))[:5]) if vehicles else "keine erkannt"
    return (
        f"[VIDEO] {n_total} Videos | {n_relevant} militaer-relevant | "
        f"{n_frames} Frames analysiert | Fahrzeuge: {veh_str}"
    )


# ============================================================
# SELF-TEST
# ============================================================

if __name__ == "__main__":
    print("[TEST] nexus_video.py")

    # ffmpeg Check
    has_ff = _has_ffmpeg()
    print(f"  ffmpeg verfuegbar: {has_ff}")

    # LLaVA Check
    has_llava = False
    try:
        from nexus_vision import analyze_image  # type: ignore
        has_llava = True
    except Exception:
        pass
    print(f"  LLaVA verfuegbar: {has_llava}")

    # Keyword-Analyse Test
    test_url = "https://t.me/ukraine_now/12345/video_t72_destroyed_bakhmut.mp4"
    kw = _analyze_url_keywords(test_url)
    print(f"  Keyword-Test: military={kw['military_relevant']} | "
          f"keywords={kw['keywords_found'][:3]} | "
          f"vehicles={kw.get('vehicles_detected', [])}")

    # URL-Analyse ohne Download
    result = analyze_video_url(test_url)
    print(f"  URL-Analyse: {result.get('summary', '')[:100]}")

    # LLM-Kontext
    ctx = videos_for_llm([result])
    if ctx:
        print(f"  LLM-Kontext:\n{ctx[:300]}")

    print("\n[TEST OK]")
    print(f"\nStatus: ffmpeg={'JA' if has_ff else 'NEIN (Fallback aktiv)'}, "
          f"LLaVA={'JA' if has_llava else 'NEIN (Fallback aktiv)'}")
    print("Installation: choco install ffmpeg  ODER  winget install ffmpeg")
