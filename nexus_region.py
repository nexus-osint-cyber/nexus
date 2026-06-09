"""
NEXUS Region Registry — Hierarchische Regionsaufloesung
=======================================================
Einheitliche Geobasis fuer ALLE NEXUS-Module (FIRMS, VIIRS, Seismik, ACLED, ...).

Logik:
  Stadt → Land → Subregion → Grossregion → Kontinent → global

Wenn "Kharkiv" keine Daten hat → probiere "Ukraine" → "Osteuropa" → "Europa" → "global"
Wenn "Bandar Abbas" keine Daten hat → probiere "Iran" → "Naher Osten" → "global"

Verwendung:
  from nexus_region import get_bbox_with_fallback, get_countries_with_fallback, resolve_chain

  # BBox fuer FIRMS/VIIRS/Seismik:
  bbox, resolved = get_bbox_with_fallback("Kharkiv")
  # → bbox=(35.5, 49.5, 37.5, 51.0), resolved="Kharkiv"
  # Falls leer: probiert "Ukraine" → (22.0, 44.0, 42.0, 55.0)

  # Laenderliste fuer ACLED:
  countries, resolved = get_countries_with_fallback("Kharkiv")
  # → "Ukraine", "Kharkiv"

  # Vollstaendige Fallback-Kette:
  chain = resolve_chain("Natanz")
  # → ["natanz", "iran", "naher osten", "global"]
"""

from __future__ import annotations
from typing import Optional

# ══════════════════════════════════════════════════════════════════════════════
# BBOX-REGISTRY (lon_min, lat_min, lon_max, lat_max) – WGS84
# ══════════════════════════════════════════════════════════════════════════════

REGION_BBOXES: dict[str, tuple[float, float, float, float]] = {

    # ── IRAN ────────────────────────────────────────────────────────────────
    "iran":             (44.0, 25.0, 63.5, 40.0),
    "teheran":          (50.5, 35.0, 52.0, 36.5),
    "tehran":           (50.5, 35.0, 52.0, 36.5),
    "isfahan":          (51.0, 31.5, 52.5, 33.0),
    "natanz":           (51.5, 33.0, 52.5, 34.0),
    "fordow":           (50.5, 34.5, 51.5, 35.5),
    "qom":              (50.5, 34.0, 51.5, 35.0),
    "bandar_abbas":     (56.0, 27.0, 57.5, 28.0),
    "bandar abbas":     (56.0, 27.0, 57.5, 28.0),
    "bushehr":          (50.5, 28.5, 51.5, 29.5),
    "ahvaz":            (48.0, 31.0, 49.5, 32.5),
    "tabriz":           (45.5, 37.5, 47.5, 39.0),
    "mashhad":          (59.0, 36.0, 60.5, 37.5),
    "shiraz":           (52.0, 29.0, 53.5, 30.5),
    "arak":             (49.5, 33.5, 50.5, 34.5),

    # ── NAHER OSTEN / PERSISCHER GOLF ───────────────────────────────────────
    "naher osten":      (25.0, 12.0, 65.0, 42.0),
    "middle east":      (25.0, 12.0, 65.0, 42.0),
    "persischer golf":  (47.0, 22.0, 62.0, 30.0),
    "persian gulf":     (47.0, 22.0, 62.0, 30.0),
    "hormuz":           (55.5, 25.5, 60.0, 27.5),
    "hormuzstrasse":    (55.5, 25.5, 60.0, 27.5),
    "rotes meer":       (32.0, 10.0, 44.0, 30.0),
    "red sea":          (32.0, 10.0, 44.0, 30.0),
    "golf von aden":    (43.0,  9.0, 55.0, 14.0),

    # ── ISRAEL / PALÄSTINA / LIBANON ────────────────────────────────────────
    "israel":           (34.2, 29.5, 35.9, 33.5),
    "gaza":             (34.2, 31.2, 34.6, 31.7),
    "westjordanland":   (34.9, 31.3, 35.7, 32.6),
    "tel aviv":         (34.6, 31.8, 35.2, 32.3),
    "jerusalem":        (35.0, 31.6, 35.5, 32.0),
    "libanon":          (35.1, 33.0, 36.8, 34.7),
    "beirut":           (35.3, 33.6, 36.2, 34.1),
    "syrien":           (35.5, 32.5, 42.5, 37.5),
    "syrien/damaskus":  (36.0, 33.0, 37.0, 34.0),
    "damaskus":         (36.0, 33.0, 37.0, 34.0),
    "aleppo":           (36.5, 35.8, 37.5, 36.8),

    # ── IRAK / ARABISCHE HALBINSEL ───────────────────────────────────────────
    "irak":             (38.8, 29.0, 48.8, 38.0),
    "iraq":             (38.8, 29.0, 48.8, 38.0),
    "bagdad":           (44.0, 33.0, 45.0, 34.0),
    "bagdad/iraq":      (44.0, 33.0, 45.0, 34.0),
    "jemen":            (42.0, 12.0, 55.0, 19.0),
    "yemen":            (42.0, 12.0, 55.0, 19.0),
    "sana'a":           (44.0, 15.0, 44.7, 15.7),
    "aden":             (44.7, 12.5, 46.0, 13.5),
    "saudi arabien":    (36.0, 16.0, 55.0, 32.0),
    "saudi arabia":     (36.0, 16.0, 55.0, 32.0),
    "vae":              (51.5, 22.5, 56.5, 26.0),
    "uae":              (51.5, 22.5, 56.5, 26.0),

    # ── UKRAINE ──────────────────────────────────────────────────────────────
    "ukraine":          (22.0, 44.0, 42.0, 55.0),
    "kiew":             (29.5, 50.0, 31.5, 51.5),
    "kyiv":             (29.5, 50.0, 31.5, 51.5),
    "kharkiv":          (35.5, 49.5, 37.5, 51.0),
    "charkiw":          (35.5, 49.5, 37.5, 51.0),
    "cherson":          (32.0, 46.0, 34.0, 47.0),
    "kherson":          (32.0, 46.0, 34.0, 47.0),
    "odessa":           (30.0, 46.0, 31.5, 47.0),
    "odesa":            (30.0, 46.0, 31.5, 47.0),
    "donezk":           (37.0, 47.5, 38.5, 48.5),
    "donetsk":          (37.0, 47.5, 38.5, 48.5),
    "saporischschja":   (34.5, 47.0, 36.5, 48.5),
    "zaporizhzhia":     (34.5, 47.0, 36.5, 48.5),
    "luhansk":          (38.0, 48.0, 40.0, 49.5),
    "lwiw":             (23.5, 49.5, 25.0, 50.5),
    "lviv":             (23.5, 49.5, 25.0, 50.5),
    "mykolajiw":        (31.0, 46.5, 32.5, 47.5),
    "mariupol":         (37.0, 47.0, 38.0, 47.5),
    "bakhmut":          (37.5, 48.5, 38.5, 49.0),

    # ── RUSSLAND ────────────────────────────────────────────────────────────
    "russland":         (27.0, 41.0, 80.0, 72.0),
    "russia":           (27.0, 41.0, 80.0, 72.0),
    "moskau":           (36.5, 55.0, 38.5, 56.5),
    "st. petersburg":   (29.5, 59.5, 31.5, 60.5),
    "belgorod":         (36.0, 50.0, 37.5, 51.0),
    "kursk":            (35.5, 51.0, 37.0, 52.5),
    "bryansk":          (33.0, 52.5, 35.0, 54.0),

    # ── OSTEUROPA / BALTIKUM ─────────────────────────────────────────────────
    "osteuropa":        (14.0, 44.0, 42.0, 72.0),
    "belarus":          (23.5, 51.0, 32.5, 56.5),
    "weissrussland":    (23.5, 51.0, 32.5, 56.5),
    "moldawien":        (26.5, 45.5, 30.5, 48.5),
    "georgien":         (40.0, 41.0, 46.5, 43.5),
    "armenien":         (43.5, 38.5, 47.0, 41.5),
    "aserbaidschan":    (44.5, 38.0, 51.5, 42.0),
    "karabach":         (46.0, 39.0, 47.5, 40.5),

    # ── SCHWARZES MEER / KAUKASUS ────────────────────────────────────────────
    "schwarzes meer":   (27.0, 40.5, 42.0, 47.0),
    "kaukasus":         (38.0, 39.0, 51.0, 44.0),
    "tschechien":       (12.0, 48.5, 18.5, 51.0),

    # ── ZENTRALASIEN ────────────────────────────────────────────────────────
    "afghanistan":      (60.0, 29.0, 75.0, 39.0),
    "kabul":            (68.5, 34.0, 70.0, 35.5),
    "pakistan":         (60.5, 23.5, 77.0, 37.0),
    "tadschikistan":    (67.0, 36.5, 75.0, 41.0),
    "zentralasien":     (49.0, 35.0, 88.0, 55.0),

    # ── ASIEN-PAZIFIK ───────────────────────────────────────────────────────
    "ostasien":         (99.0, 18.0, 148.0, 53.0),
    "taiwan":           (119.0, 21.5, 122.5, 25.5),
    "taiwan-strasse":   (118.0, 21.0, 125.0, 27.0),
    "china":            (73.0, 18.0, 135.0, 53.0),
    "sudchinesisches meer": (99.0, 0.0, 122.0, 25.0),
    "south china sea":  (99.0, 0.0, 122.0, 25.0),
    "nordkorea":        (124.0, 37.5, 130.0, 43.0),
    "north korea":      (124.0, 37.5, 130.0, 43.0),
    "korea-halbinsel":  (124.0, 34.0, 130.0, 43.0),
    "japan":            (128.0, 30.0, 148.0, 46.0),
    "philippinen":      (116.0, 4.0, 128.0, 21.0),
    "myanmar":          (92.0, 10.0, 102.0, 28.5),
    "indien":           (68.0, 8.0, 97.0, 37.0),

    # ── AFRIKA ──────────────────────────────────────────────────────────────
    "sahel":            (-18.0, 10.0, 24.0, 20.0),
    "mali":             (-5.0, 10.0, 5.5, 25.0),
    "niger":            (0.0, 11.5, 16.0, 23.5),
    "burkina faso":     (-5.5, 9.5, 3.0, 15.5),
    "nigeria":          (2.5, 4.0, 15.0, 14.0),
    "nordafrika":       (-6.0, 18.0, 37.0, 38.0),
    "libyen":           (9.0, 20.0, 26.0, 33.5),
    "sudan":            (21.0, 8.0, 39.0, 24.0),
    "aethiopien":       (33.0, 3.0, 48.0, 18.0),
    "tigray":           (37.0, 12.0, 40.5, 15.5),
    "somalia":          (40.5, -2.0, 51.5, 12.0),
    "demokratische republik kongo": (12.0, -14.0, 31.0, 5.5),
    "ostafrika":        (28.0, -15.0, 52.0, 15.0),
    "westafrika":       (-18.0, 4.0, 16.0, 23.0),
    "subsahara-afrika": (-18.0, -35.0, 52.0, 15.0),
    "afrika":           (-18.0, -35.0, 52.0, 38.0),

    # ── EUROPÄISCH ──────────────────────────────────────────────────────────
    "europa":           (-10.0, 35.0, 42.0, 72.0),
    "westeuropa":       (-10.0, 35.0, 20.0, 58.0),
    "tuerkei":          (26.0, 36.0, 45.0, 42.5),
    "turkey":           (26.0, 36.0, 45.0, 42.5),

    # ── LATEINAMERIKA ────────────────────────────────────────────────────────
    "venezuela":        (-73.5, 0.5, -59.5, 13.0),
    "kolumbien":        (-79.0, -4.5, -66.5, 13.0),
    "lateinamerika":    (-82.0, -55.0, -34.0, 33.0),

    # ── NORDAMERIKA ─────────────────────────────────────────────────────────
    "usa":              (-125.0, 24.0, -66.0, 50.0),
    "kanada":           (-141.0, 42.0, -52.0, 84.0),
    "mexiko":           (-118.0, 14.5, -86.5, 33.0),

    # ── GLOBAL / KONTINENT-FALLBACK ──────────────────────────────────────────
    "global":           (-180.0, -90.0, 180.0, 90.0),
}


# ── Länder-Mapping für ACLED ─────────────────────────────────────────────────
REGION_COUNTRIES: dict[str, str] = {
    # Iran
    "iran":             "Iran",
    "teheran":          "Iran",
    "tehran":           "Iran",
    "isfahan":          "Iran",
    "natanz":           "Iran",
    "fordow":           "Iran",
    "bandar_abbas":     "Iran",
    "bandar abbas":     "Iran",
    "bushehr":          "Iran",
    "ahvaz":            "Iran",
    "tabriz":           "Iran",
    "mashhad":          "Iran",
    "shiraz":           "Iran",
    # Israel / Palästina
    "israel":           "Israel",
    "gaza":             "Palestine",
    "westjordanland":   "Palestine",
    "tel aviv":         "Israel",
    "jerusalem":        "Israel",
    # Naher Osten
    "naher osten":      "Syria;Iraq;Lebanon;Israel;Yemen;Iran;Jordan",
    "middle east":      "Syria;Iraq;Lebanon;Israel;Yemen;Iran;Jordan",
    "syrien":           "Syria",
    "libanon":          "Lebanon",
    "irak":             "Iraq",
    "iraq":             "Iraq",
    "jemen":            "Yemen",
    "saudi arabien":    "Saudi Arabia",
    "vae":              "United Arab Emirates",
    "uae":              "United Arab Emirates",
    "persischer golf":  "Iran;Saudi Arabia;UAE;Bahrain;Kuwait;Qatar;Oman",
    "hormuz":           "Iran;Oman;UAE",
    # Ukraine
    "ukraine":          "Ukraine",
    "kiew":             "Ukraine",
    "kyiv":             "Ukraine",
    "kharkiv":          "Ukraine",
    "charkiw":          "Ukraine",
    "donezk":           "Ukraine",
    "donetsk":          "Ukraine",
    "cherson":          "Ukraine",
    "mariupol":         "Ukraine",
    "bakhmut":          "Ukraine",
    # Russland
    "russland":         "Russia",
    "russia":           "Russia",
    "belgorod":         "Russia",
    "kursk":            "Russia",
    # Kaukasus
    "armenien":         "Armenia",
    "aserbaidschan":    "Azerbaijan",
    "georgien":         "Georgia",
    "karabach":         "Azerbaijan",
    # Zentralasien
    "afghanistan":      "Afghanistan",
    "pakistan":         "Pakistan",
    # Asien-Pazifik
    "myanmar":          "Myanmar",
    "nordkorea":        "North Korea",
    "china":            "China",
    "taiwan":           "Taiwan",
    "korea-halbinsel":  "North Korea;South Korea",
    # Afrika
    "sudan":            "Sudan",
    "sahel":            "Mali;Niger;Burkina Faso;Chad;Sudan;Mauritania",
    "mali":             "Mali",
    "niger":            "Niger",
    "libyen":           "Libya",
    "somalia":          "Somalia",
    "aethiopien":       "Ethiopia",
    "tigray":           "Ethiopia",
    "nigeria":          "Nigeria",
    # Breit
    "europa":           "Ukraine;Russia;Georgia;Armenia;Azerbaijan",
    "global":           "",  # kein Filter → alles
}


# ── Hierarchische Eltern-Kette ────────────────────────────────────────────────
# kind → elter: von spezifisch nach allgemein
REGION_PARENTS: dict[str, str] = {
    # Iran-Städte → Iran
    "teheran": "iran", "tehran": "iran", "isfahan": "iran",
    "natanz": "iran", "fordow": "iran", "qom": "iran",
    "bandar_abbas": "iran", "bandar abbas": "iran",
    "bushehr": "iran", "ahvaz": "iran", "tabriz": "iran",
    "mashhad": "iran", "shiraz": "iran", "arak": "iran",
    # Iran → Naher Osten
    "iran": "naher osten",
    # Israel/Gaza-Städte → Israel/Gaza
    "tel aviv": "israel", "jerusalem": "israel",
    "westjordanland": "naher osten",
    "gaza": "naher osten",
    # Naher Osten Länder → Naher Osten
    "israel": "naher osten", "syrien": "naher osten",
    "libanon": "naher osten", "irak": "naher osten",
    "iraq": "naher osten", "jemen": "naher osten",
    "saudi arabien": "naher osten", "vae": "naher osten",
    "uae": "naher osten", "hormuz": "naher osten",
    "persischer golf": "naher osten", "rotes meer": "naher osten",
    "naher osten": "global",
    "middle east": "global",
    # Ukraine-Städte → Ukraine
    "kiew": "ukraine", "kyiv": "ukraine",
    "kharkiv": "ukraine", "charkiw": "ukraine",
    "donezk": "ukraine", "donetsk": "ukraine",
    "cherson": "ukraine", "kherson": "ukraine",
    "odessa": "ukraine", "odesa": "ukraine",
    "mariupol": "ukraine", "bakhmut": "ukraine",
    "saporischschja": "ukraine", "zaporizhzhia": "ukraine",
    "luhansk": "ukraine", "lwiw": "ukraine", "lviv": "ukraine",
    "mykolajiw": "ukraine",
    # Russland-Städte → Russland
    "moskau": "russland", "st. petersburg": "russland",
    "belgorod": "russland", "kursk": "russland", "bryansk": "russland",
    # Ukraine/Russland → Osteuropa
    "ukraine": "osteuropa", "russland": "osteuropa",
    "russia": "osteuropa", "belarus": "osteuropa",
    "weissrussland": "osteuropa", "moldawien": "osteuropa",
    "schwarzes meer": "osteuropa",
    "osteuropa": "global",
    # Kaukasus → Naher Osten / Global
    "georgien": "naher osten", "armenien": "naher osten",
    "aserbaidschan": "naher osten", "karabach": "naher osten",
    "kaukasus": "naher osten",
    # Zentralasien
    "afghanistan": "zentralasien", "pakistan": "zentralasien",
    "tadschikistan": "zentralasien", "kabul": "afghanistan",
    "zentralasien": "global",
    # Asien-Pazifik Städte → Länder
    "taiwan": "ostasien", "nordkorea": "ostasien",
    "north korea": "ostasien", "japan": "ostasien",
    "china": "ostasien", "philippinen": "ostasien",
    "myanmar": "ostasien", "indien": "ostasien",
    "korea-halbinsel": "ostasien",
    "ostasien": "global",
    # Afrika
    "mali": "sahel", "niger": "sahel", "burkina faso": "sahel",
    "nigeria": "westafrika", "westafrika": "subsahara-afrika",
    "sudan": "nordafrika", "libyen": "nordafrika",
    "aethiopien": "ostafrika", "tigray": "aethiopien",
    "somalia": "ostafrika", "ostafrika": "subsahara-afrika",
    "sahel": "subsahara-afrika", "nordafrika": "afrika",
    "subsahara-afrika": "afrika", "westafrika": "subsahara-afrika",
    "afrika": "global",
    # Europa
    "tuerkei": "global", "turkey": "global",
    "europa": "global", "westeuropa": "europa",
    # Nahe Osten → Global
    "golf von aden": "naher osten",
    "taiwan-strasse": "ostasien",
    "sudchinesisches meer": "ostasien",
    "south china sea": "ostasien",
    # Alles → Global
    "lateinamerika": "global", "nordamerika": "global",
    "usa": "global", "global": "",
}


# ══════════════════════════════════════════════════════════════════════════════
# HILFSFUNKTIONEN
# ══════════════════════════════════════════════════════════════════════════════

def normalize(name: str) -> str:
    """Normalisiert Regionsnamen: lowercase, stripped, Umlaute behalten."""
    return name.lower().strip()


def resolve_chain(region: str) -> list[str]:
    """
    Gibt Fallback-Kette von spezifisch nach allgemein zurueck.
    Beispiel: "Natanz" → ["natanz", "iran", "naher osten", "global"]
    """
    r = normalize(region)
    chain = [r]
    visited: set[str] = {r}
    current = r
    while True:
        parent = REGION_PARENTS.get(current, "")
        if not parent or parent in visited:
            break
        visited.add(parent)
        chain.append(parent)
        current = parent
    if not chain or chain[-1] != "global":
        chain.append("global")
    return chain


def get_bbox(region: str) -> Optional[tuple[float, float, float, float]]:
    """
    Gibt BBox fuer exakt diese Region zurueck (kein Fallback).
    Format: (lon_min, lat_min, lon_max, lat_max)
    """
    r = normalize(region)
    return REGION_BBOXES.get(r)


def get_bbox_with_fallback(region: str) -> tuple[
        Optional[tuple[float, float, float, float]], str]:
    """
    Sucht BBox mit automatischem Fallback durch die Hierarchie.

    Gibt (bbox, aufgeloester_name) zurueck.
    bbox ist None nur wenn wirklich nichts gefunden wurde (sollte nicht passieren
    da "global" immer vorhanden ist).

    Beispiele:
      get_bbox_with_fallback("Natanz")
        → ((51.5, 33.0, 52.5, 34.0), "natanz")

      get_bbox_with_fallback("Bagdad")
        → ((44.0, 33.0, 45.0, 34.0), "bagdad")   # direkt gefunden

      get_bbox_with_fallback("Basra")
        → ((38.8, 29.0, 48.8, 38.0), "irak")     # Fallback auf Irak
    """
    for candidate in resolve_chain(region):
        bbox = REGION_BBOXES.get(candidate)
        if bbox:
            return bbox, candidate
    return None, region


def get_countries_with_fallback(region: str) -> tuple[str, str]:
    """
    Sucht ACLED-Laenderliste mit Fallback.

    Gibt (laender_string, aufgeloester_name) zurueck.
    laender_string: "Iran;Iraq;..." oder "" (kein Filter = global)

    Beispiele:
      get_countries_with_fallback("Natanz")   → ("Iran", "natanz")
      get_countries_with_fallback("Irak")     → ("Iraq", "irak")
      get_countries_with_fallback("Basra")    → ("Iraq", "irak")  # Fallback
    """
    for candidate in resolve_chain(region):
        countries = REGION_COUNTRIES.get(candidate)
        if countries is not None:
            return countries, candidate
    return "", region


def get_bbox_center(region: str) -> tuple[float, float]:
    """
    Gibt Mittelpunkt der Region zurueck (lat, lon).
    Nuetzlich fuer radius-basierte APIs (z.B. ADS-B Exchange).
    """
    bbox, _ = get_bbox_with_fallback(region)
    if not bbox:
        return 0.0, 0.0
    lon_min, lat_min, lon_max, lat_max = bbox
    return (lat_min + lat_max) / 2.0, (lon_min + lon_max) / 2.0


def list_known_regions() -> list[str]:
    """Gibt alle bekannten Regionsnamen zurueck (alphabetisch)."""
    return sorted(REGION_BBOXES.keys())


def describe_region(region: str) -> str:
    """Debugging-Hilfe: beschreibt Region + Fallback-Kette."""
    bbox, resolved = get_bbox_with_fallback(region)
    countries, _ = get_countries_with_fallback(region)
    chain = resolve_chain(region)
    lines = [
        f"Region: {region}",
        f"  Normalisiert:   {normalize(region)}",
        f"  Fallback-Kette: {' → '.join(chain)}",
        f"  Aufgeloest als: {resolved}",
        f"  BBox (L,B,R,T): {bbox}",
        f"  ACLED-Laender:  {countries or '(alle)'}",
    ]
    if bbox:
        lat_c, lon_c = get_bbox_center(region)
        lines.append(f"  Zentrum:        {lat_c:.2f}°N, {lon_c:.2f}°E")
    return "\n".join(lines)


# ── Direktaufruf / Test ───────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    regions = sys.argv[1:] if len(sys.argv) > 1 else [
        "Natanz", "Basra", "Donetsk", "Tigray", "Basra", "Seoul", "unbekannt"
    ]
    print("NEXUS Region Registry – Test\n" + "="*50)
    for r in regions:
        print()
        print(describe_region(r))
