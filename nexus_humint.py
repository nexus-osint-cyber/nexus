"""
NEXUS – HUMINT-Aggregator  (Ebene 4 / Modul 4.4)
=================================================
Extrahiert taktische Meldungen aus Rohtexten (Telegram, Reddit, RSS):

  • WGS84-Koordinaten im Text  (47.123, 31.456 | 47°12'N 31°45'E)
  • MGRS-Grid-Referenzen       (37U DT 1234 5678)
  • Einheitsbezeichnungen      (25-та бригада, 3rd Assault Brigade, BTG)
  • Kontaktmeldungen           (прилёт, обстрел, удар, Einschlag, strike)
  • Waffensystem-Nennungen     (Lancet, Shahed, HIMARS, Iskander …)

Öffentliche API:
  extract_humint(texts, region)   → list[HumintHit]
  humint_for_map(articles, region)→ list[dict]   (für Karten-Marker)
  humint_summary(hits)            → str            (für LLM-Kontext)
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Regex-Muster
# ─────────────────────────────────────────────────────────────────────────────

# Dezimalgradkoordinaten: "47.1234, 31.5678" oder "47.1234,31.5678"
_RE_DECIMAL = re.compile(
    r'\b(-?\d{1,3}\.\d{3,6})\s*[,;]\s*(-?\d{1,3}\.\d{3,6})\b'
)

# DMS: 47°12'34"N 31°45'12"E  oder  47°12'N 31°45'E
_RE_DMS = re.compile(
    r'(\d{1,3})°\s*(\d{1,2})\'(?:\s*(\d{1,2}(?:\.\d+)?)"?)?\s*([NS])'
    r'\s+'
    r'(\d{1,3})°\s*(\d{1,2})\'(?:\s*(\d{1,2}(?:\.\d+)?)"?)?\s*([EW])',
    re.IGNORECASE,
)

# MGRS: "37U DT 12345 67890" oder "37UDT1234567890"
_RE_MGRS = re.compile(
    r'\b(\d{1,2}[C-X])\s*([A-Z]{2})\s*(\d{2,5})\s*(\d{2,5})\b',
    re.IGNORECASE,
)

# Einheiten (UA/RU/NATO)
_RE_UNITS = re.compile(
    r'\b(?:'
    r'\d{1,3}[-\s]?(?:th|st|nd|rd|та|га|й|я|ї|го|і|ий)?\s*'
    r'(?:brigade|brig|btg|battalion|batallion|бригад[аиі]?|батальон|полк|рота|'
    r'assault|assault brigade|повітряно-десантн|десантно-штурмов|тероборон|'
    r'механізован|танков|артилерійськ|infantry|armored|mechanized)'
    r'|'
    r'(?:BTG|BDG|SAA|VDV|GRU|FSB|PMC|Wagner|Вагнер|Ахмат|SOBR|Росгвардія|'
    r'HIMARS|MLRS|M270|M777|Caesar|PzH\s*2000|Archer|Krab|Pion|Tulip|'
    r'Lancet|Shahed|Geran|Kalibr|Iskander|Kinzhal|Kh-101|Kh-55|S-300|S-400|'
    r'Tor|Buk|Pantsir|Grad|Uragan|Smerch|TOS-1|Kornet|Javelin|NLAW|ATGM|'
    r'T-72|T-80|T-90|Leopard|Abrams|Bradley|Marder|Stryker|CV-90|'
    r'дрон|БПЛА|UAV|FPV|Bayraktar|TB2|Orlan|Shahed-136|Lancet-3)'
    r')\b',
    re.IGNORECASE,
)

# Kontaktmeldungen / Kampfhandlungen
_RE_CONTACT = re.compile(
    r'\b(?:'
    r'прилёт|прильот|прилет|обстрел|удар|взрыв|вибух|пожар|пожежа|'
    r'атака|наступ|відступ|окружен|оточен|захвачен|зайнят|'
    r'strike|airstrike|shelling|explosion|fire|attack|advance|retreat|'
    r'encircled|captured|taken|occupied|destroyed|hit|impact|'
    r'Einschlag|Beschuss|Angriff|Explosion|Brand|Treffer|'
    r'casualties|потери|втрати|killed|wounded|поранен|загинул'
    r')\b',
    re.IGNORECASE,
)

# Ortsangaben mit Richtung
_RE_DIRECTION = re.compile(
    r'\b(\d+(?:\.\d+)?)\s*km\s*(?:to\s+the\s+|від\s+|von\s+)?'
    r'(north|south|east|west|northeast|northwest|southeast|southwest|'
    r'N|S|E|W|NE|NW|SE|SW|північ|південь|схід|захід)',
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Waffensystem-Kategorien
# ─────────────────────────────────────────────────────────────────────────────
_WEAPON_CATS = {
    "drone":    ["lancet","shahed","geran","fpv","бпла","uav","bayraktar","tb2","orlan","shahed-136"],
    "missile":  ["kalibr","iskander","kinzhal","kh-101","kh-55","cruise"],
    "mlrs":     ["himars","m270","mlrs","grad","uragan","smerch","tos-1"],
    "arty":     ["m777","caesar","pzh","archer","krab","pion","tulip","howitzer"],
    "armor":    ["t-72","t-80","t-90","leopard","abrams","bradley","marder"],
    "air_def":  ["s-300","s-400","tor","buk","pantsir","iris-t","nasams","patriot"],
}


# ─────────────────────────────────────────────────────────────────────────────
# Datenklasse
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class HumintHit:
    lat:         float
    lon:         float
    text:        str            # Original-Snippet (max 200 Zeichen)
    source:      str = "telegram"
    confidence:  float = 0.5    # 0.0–1.0
    contact:     bool = False   # Kampfhandlung erkannt?
    units:       list[str] = field(default_factory=list)
    weapons:     list[str] = field(default_factory=list)
    weapon_cat:  str = ""
    coord_type:  str = "decimal"  # decimal | dms | mgrs | inferred
    ts:          float = field(default_factory=time.time)


# ─────────────────────────────────────────────────────────────────────────────
# MGRS → WGS84 (vereinfachte Näherung für Zone 36/37/38)
# ─────────────────────────────────────────────────────────────────────────────

_MGRS_BAND_LAT = {
    "C":(-80,-72),"D":(-72,-64),"E":(-64,-56),"F":(-56,-48),"G":(-48,-40),
    "H":(-40,-32),"J":(-32,-24),"K":(-24,-16),"L":(-16,-8),"M":(-8,0),
    "N":(0,8),"P":(8,16),"Q":(16,24),"R":(24,32),"S":(32,40),"T":(40,48),
    "U":(48,56),"V":(56,64),"W":(64,72),"X":(72,84),
}

def _mgrs_to_latlon(zone_num: int, band: str, sq1: str, sq2: str,
                     easting: str, northing: str) -> Optional[tuple[float, float]]:
    """Grobe MGRS → WGS84 Konvertierung (Genauigkeit ±5 km, ausreichend für Karten-Marker)."""
    try:
        band = band.upper()
        if band not in _MGRS_BAND_LAT:
            return None
        lat_min, lat_max = _MGRS_BAND_LAT[band]
        lat_center = (lat_min + lat_max) / 2.0

        # Zonenmittellängengrad
        lon_center = (zone_num - 1) * 6 - 180 + 3.0

        # 100km-Gitter: Buchstabe 1 = Spalte, Buchstabe 2 = Zeile
        col_letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
        row_letters = "ABCDEFGHJKLMNPQRSTUV"
        col = col_letters.find(sq1.upper())
        row = row_letters.find(sq2.upper())
        if col < 0 or row < 0:
            return None

        # Numerischen Anteil auf 5-stellig normieren
        e_str = easting.ljust(5, '0')[:5]
        n_str = northing.ljust(5, '0')[:5]
        e_m = int(e_str)    # Meter innerhalb 100km-Quadrat
        n_m = int(n_str)

        # Sehr grobe Umrechnung (Fehler ±5 km akzeptabel)
        lon = lon_center + (col % 8 - 4) * 0.9 + e_m / 100000.0 * 0.9
        lat = lat_center + (row % 10 - 5) * 0.7 + n_m / 100000.0 * 0.7

        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return round(lat, 4), round(lon, 4)
    except Exception:
        pass
    return None


def _dms_to_dd(deg: str, mn: str, sec: str, hemi: str) -> float:
    d = float(deg or 0)
    m = float(mn or 0)
    s = float(sec or 0)
    dd = d + m / 60.0 + s / 3600.0
    if hemi.upper() in ("S", "W"):
        dd = -dd
    return round(dd, 6)


# ─────────────────────────────────────────────────────────────────────────────
# Koordinaten-Extraktion
# ─────────────────────────────────────────────────────────────────────────────

def _extract_coords(text: str) -> list[tuple[float, float, str]]:
    """
    Gibt Liste von (lat, lon, coord_type) zurück.
    Filtert unplausible Koordinaten (z.B. Jahreszahlen wie 2024.5, 1945.0).
    """
    results = []

    # 1. Dezimalgrad
    for m in _RE_DECIMAL.finditer(text):
        try:
            a, b = float(m.group(1)), float(m.group(2))
            # Plausibilitäts-Check: gültige Koordinaten, kein Jahr/Preis
            if -90 <= a <= 90 and -180 <= b <= 180 and abs(a) > 1 and abs(b) > 1:
                # Schließe Jahreszahlen aus (z.B. 2024.5, 1944.0)
                if not (1800 <= abs(a) <= 2100):
                    results.append((round(a, 5), round(b, 5), "decimal"))
        except ValueError:
            pass

    # 2. DMS-Format
    for m in _RE_DMS.finditer(text):
        try:
            lat = _dms_to_dd(m.group(1), m.group(2), m.group(3) or "0", m.group(4))
            lon = _dms_to_dd(m.group(5), m.group(6), m.group(7) or "0", m.group(8))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                results.append((lat, lon, "dms"))
        except (ValueError, AttributeError):
            pass

    # 3. MGRS
    for m in _RE_MGRS.finditer(text):
        try:
            zone_str = m.group(1)
            zone_num = int(re.match(r'\d+', zone_str).group())
            band     = re.search(r'[A-Z]', zone_str).group()
            sq       = m.group(2)
            coord = _mgrs_to_latlon(zone_num, band, sq[0], sq[1],
                                     m.group(3), m.group(4))
            if coord:
                results.append((coord[0], coord[1], "mgrs"))
        except Exception:
            pass

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Konfidenz-Berechnung
# ─────────────────────────────────────────────────────────────────────────────

def _confidence(coord_type: str, has_contact: bool,
                units: list, weapons: list, text_len: int) -> float:
    score = 0.3
    if coord_type == "mgrs":
        score += 0.3   # MGRS in Kampftexten fast immer absichtlich
    elif coord_type == "dms":
        score += 0.2
    else:
        score += 0.1
    if has_contact:
        score += 0.2
    if units:
        score += 0.1
    if weapons:
        score += 0.1
    if text_len > 100:
        score += 0.05
    return min(round(score, 2), 1.0)


def _weapon_category(weapons: list[str]) -> str:
    wl = " ".join(weapons).lower()
    for cat, kws in _WEAPON_CATS.items():
        if any(k in wl for k in kws):
            return cat
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Extraktionsfunktion
# ─────────────────────────────────────────────────────────────────────────────

def extract_humint(
    texts:  list[str | dict],
    region: str = "",
    min_confidence: float = 0.35,
) -> list[HumintHit]:
    """
    Verarbeitet Liste von Texten (str oder dict mit 'text'/'content'/'summary').
    Gibt HumintHits mit lat/lon sortiert nach Konfidenz zurück.
    """
    hits: list[HumintHit] = []
    seen_coords: set[tuple] = set()

    for item in texts:
        if isinstance(item, dict):
            raw = " ".join(filter(None, [
                item.get("title",""), item.get("text",""),
                item.get("content",""), item.get("summary",""),
            ]))
            source = item.get("source", item.get("channel", "telegram"))
        else:
            raw = str(item)
            source = "text"

        if not raw.strip():
            continue

        coords = _extract_coords(raw)
        if not coords:
            continue

        has_contact = bool(_RE_CONTACT.search(raw))
        unit_matches = _RE_UNITS.findall(raw)
        units = list({u.strip() for u in unit_matches if len(u.strip()) > 2})[:5]
        weapons = list({w.strip() for w in unit_matches
                        if any(k in w.lower() for cat in _WEAPON_CATS.values() for k in cat)})[:5]
        weapon_cat = _weapon_category(weapons or units)
        snippet = raw[:200].replace("\n", " ")

        for lat, lon, ctype in coords:
            key = (round(lat, 2), round(lon, 2))
            if key in seen_coords:
                continue
            seen_coords.add(key)

            conf = _confidence(ctype, has_contact, units, weapons, len(raw))
            if conf < min_confidence:
                continue

            hits.append(HumintHit(
                lat=lat, lon=lon,
                text=snippet,
                source=source,
                confidence=conf,
                contact=has_contact,
                units=units,
                weapons=weapons,
                weapon_cat=weapon_cat,
                coord_type=ctype,
            ))

    hits.sort(key=lambda h: h.confidence, reverse=True)
    return hits[:50]  # Max 50 Hits pro Aufruf


# ─────────────────────────────────────────────────────────────────────────────
# Karten-Marker-Output
# ─────────────────────────────────────────────────────────────────────────────

_CONF_COLOR = {
    "high":   "#ff2200",   # ≥0.75 – sehr konfident
    "medium": "#ff8800",   # ≥0.50
    "low":    "#ffcc00",   # <0.50
}

_WCAT_ICON = {
    "drone":   "🛸",
    "missile": "🚀",
    "mlrs":    "💥",
    "arty":    "💣",
    "armor":   "🛡",
    "air_def": "🔺",
    "":        "📍",
}

def humint_for_map(
    articles: list[dict],
    region:   str = "",
    min_confidence: float = 0.40,
) -> list[dict]:
    """
    Gibt Karten-Marker-Liste für nexus_report.py zurück.
    Jeder Marker: {lat, lon, title, text, confidence, color, icon, weapon_cat, source}
    """
    hits = extract_humint(articles, region, min_confidence)
    markers = []
    for h in hits:
        if h.confidence >= 0.75:
            color = _CONF_COLOR["high"]
        elif h.confidence >= 0.50:
            color = _CONF_COLOR["medium"]
        else:
            color = _CONF_COLOR["low"]

        icon = _WCAT_ICON.get(h.weapon_cat, "📍")
        title_parts = []
        if h.contact:
            title_parts.append("⚡ KONTAKT")
        if h.weapon_cat:
            title_parts.append(h.weapon_cat.upper())
        if h.units:
            title_parts.append(h.units[0][:30])
        title = " · ".join(title_parts) if title_parts else "HUMINT-Meldung"

        markers.append({
            "lat":        h.lat,
            "lon":        h.lon,
            "title":      title,
            "text":       h.text[:180],
            "confidence": h.confidence,
            "color":      color,
            "icon":       icon,
            "weapon_cat": h.weapon_cat,
            "source":     h.source,
            "coord_type": h.coord_type,
            "contact":    h.contact,
            "units":      h.units,
        })
    return markers


# ─────────────────────────────────────────────────────────────────────────────
# Text-Zusammenfassung für LLM-Kontext
# ─────────────────────────────────────────────────────────────────────────────

def humint_summary(hits: list[HumintHit], max_hits: int = 10) -> str:
    if not hits:
        return ""
    lines = [f"[HUMINT] {len(hits)} taktische Meldungen mit Koordinaten:\n"]
    for i, h in enumerate(hits[:max_hits], 1):
        conf_s = f"{h.confidence:.0%}"
        contact_s = " ⚡KONTAKT" if h.contact else ""
        unit_s = f" | Einheit: {h.units[0]}" if h.units else ""
        weap_s = f" | Waffe: {h.weapon_cat}" if h.weapon_cat else ""
        lines.append(
            f"  {i}. [{h.lat:.4f}, {h.lon:.4f}] Konfidenz {conf_s}"
            f"{contact_s}{unit_s}{weap_s}\n"
            f"     {h.text[:120]}"
        )
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# CLI-Test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_texts = [
        "Прилёт в р-не 47.8523, 35.1245. Обстрел артиллерией, BTG активен.",
        "37U DT 45230 67891 – засечена колонна Т-80, движение на север",
        "Strike near 48°12'34\"N 37°45'12\"E by Lancet drone, BTG confirmed",
        "HIMARS удар по складу 49.123, 36.456, потери подтверждены",
        "Просто текст без координат и нечего интересного",
        "Мирне 48.1234, 38.5678 – прильот БПЛА FPV, пожар",
    ]
    hits = extract_humint(test_texts)
    print(f"Gefunden: {len(hits)} HUMINT-Hits\n")
    for h in hits:
        print(f"  [{h.lat}, {h.lon}] {h.coord_type} conf={h.confidence:.0%}"
              f" contact={h.contact} weapon={h.weapon_cat}")
        print(f"    {h.text[:80]}")
    print("\n" + humint_summary(hits))
