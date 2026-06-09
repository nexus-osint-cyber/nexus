"""
NEXUS – SAR Target Classification  (Modul 4.11b)
=================================================
Klassifiziert SAR-Radar-Reflexionen nach Schiffstyp.

Physikalische Grundlage:
  Sentinel-1 GRD: ~10m Bodenauflösung (IW-Modus)
  1 Pixel = 10×10m Bodenfläche
  Radar Cross Section (RCS): Metallmasse und Form bestimmen Helligkeit

Klassifikationsmethode:
  1. Bounding-Box-Analyse: Länge / Breite aus Cluster-Ausdehnung
  2. Elongation:  Länge/Breite-Verhältnis → Schiff vs. Plattform vs. Insel
  3. Kompaktheit: Kreisförmig (Öltank, Plattform) vs. Langgestreckt (Schiff, U-Boot)
  4. Helligkeit:  Sehr hell (Metallreflexion) vs. mittel (teilorganisch) vs. schwach (Wasser)
  5. Größenabgleich: Pixel-Fläche → geschätzte Länge in Metern → Kategorie

Bekannte Schiffsklassen-Datenbank (Länge × Breite in Metern):
  US Navy, Royal Navy, PLA Navy, Bundesmarine + Zivilschiffe

Ausgabe je Cluster:
  category:         "Frachter" | "Fischerboot" | "Kriegsschiff" | ...
  subcategory:      "VLCC" | "Fregatte" | "Drohne/USV" | ...
  confidence:       0.0 – 1.0
  length_m:         geschätzte Länge
  width_m:          geschätzte Breite
  aspect_ratio:     Länge / Breite
  rcs_class:        "sehr hoch" | "hoch" | "mittel" | "niedrig"
  possible_classes: [{class, match_score, note}, ...]  # optional Datenbank-Vergleich
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Schiffsklassen-Datenbank
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ShipClass:
    name:        str
    length_m:    float       # Gesamtlänge
    width_m:     float       # Breite (Beam)
    category:    str         # "Träger" | "Zerstörer" | "U-Boot" | "Frachter" | ...
    nation:      str         # "USA" | "RUS" | "CHN" | "Zivil" | ...
    note:        str = ""

SHIP_DATABASE: list[ShipClass] = [
    # ── Flugzeugträger ────────────────────────────────────────────────────────
    ShipClass("Gerald R. Ford (CVN-78)",   337, 78,  "Flugzeugträger", "USA",
              "Ford-Klasse, nuklear, ~100.000t, 75 Flugzeuge"),
    ShipClass("Nimitz-Klasse (CVN-68ff)",  333, 78,  "Flugzeugträger", "USA",
              "Älteste aktive Trägerklasse der US Navy"),
    ShipClass("Queen Elizabeth (R08)",     284, 73,  "Flugzeugträger", "GBR",
              "Größter britischer Kriegsschiff, F-35B),"),
    ShipClass("Kusnezow (Admiral)",        306, 72,  "Flugzeugträger", "RUS",
              "Einziger russischer Träger, oft in Reparatur"),
    ShipClass("Liaoning (CV-16)",          304, 75,  "Flugzeugträger", "CHN",
              "Chinas erster Träger, ex-Warjag"),
    ShipClass("Fujian (CV-18)",            316, 76,  "Flugzeugträger", "CHN",
              "CATOBAR Elektromagnetischer Katapult"),
    ShipClass("Charles de Gaulle (R91)",   261, 64,  "Flugzeugträger", "FRA",
              "Einziger nuklearer nicht-US Träger"),
    ShipClass("INS Vikrant (IAC-1)",       262, 62,  "Flugzeugträger", "IND",
              "Erster in Indien gebauter Träger"),

    # ── Kreuzer / Zerstörer / Fregatten ──────────────────────────────────────
    ShipClass("Arleigh Burke (DDG)",       155, 20,  "Zerstörer",      "USA",
              "Häufigster US Zerstörer, Aegis-System"),
    ShipClass("Ticonderoga-Klasse (CG)",   173, 17,  "Kreuzer",        "USA",
              "Aegis-Kreuzer, wird ausgemustert"),
    ShipClass("Zumwalt-Klasse (DDG-1000)", 183, 25,  "Zerstörer",      "USA",
              "Stealth-Zerstörer, sehr geringe RCS"),
    ShipClass("Type 055 (Renhai)",         180, 21,  "Kreuzer",        "CHN",
              "Größter chinesischer Überwasserkampfschiff"),
    ShipClass("Type 052D (Luyang III)",    157, 18,  "Zerstörer",      "CHN",
              "Chinas Standardzerstörer"),
    ShipClass("Slava-Klasse (Moskwa)",     186, 21,  "Kreuzer",        "RUS",
              "Moskwa 2022 versenkt, Klasse 3 Schiffe"),
    ShipClass("Kirov-Klasse (Peter d.G.)", 252, 28,  "Schlachtkreuzer","RUS",
              "Größtes aktives Kriegsschiff außer Trägern, nuklear"),
    ShipClass("Type 23 Duke-Klasse",       133, 16,  "Fregatte",       "GBR",
              "Britische ASW-Fregatte"),
    ShipClass("FREMM-Fregatte",            142, 20,  "Fregatte",       "FRA/ITA",
              "Europäische Mehrzweckfregatte"),
    ShipClass("F125 Baden-Württemberg",    149, 19,  "Fregatte",       "DEU",
              "Deutsche Fregatte, Bundeswehr"),
    ShipClass("SIGMA 10514 (Holland)",     105, 14,  "Patrouillenschiff","NLD",
              "Niederländisches Offshore-Patrouillenschiff"),

    # ── U-Boote (aufgetaucht / Periskoptiefe) ────────────────────────────────
    ShipClass("Virginia-Klasse (SSN-774)", 115, 10,  "U-Boot",         "USA",
              "US Angriffs-U-Boot, nuklear, extrem elongiert"),
    ShipClass("Ohio-Klasse (SSBN)",        171, 13,  "U-Boot",         "USA",
              "Strategisches Raketen-U-Boot, 24× Trident II"),
    ShipClass("Seawolf-Klasse (SSN-21)",   108, 13,  "U-Boot",         "USA",
              "Lautestes Kampf-U-Boot, sehr breiter Rumpf"),
    ShipClass("Kilo-Klasse (Varshavyanka)",74,  10,  "U-Boot",         "RUS",
              "Im Schwarzen Meer und Mittelmeer aktiv"),
    ShipClass("Oscar II (Kursk-Klasse)",   155, 18,  "U-Boot",         "RUS",
              "Größtes Jagd-U-Boot, Granit-Raketen"),
    ShipClass("Borei-Klasse (Dolgorukiy)", 170, 13,  "U-Boot",         "RUS",
              "Neue russische SSBN-Generation"),
    ShipClass("Type 093 (Shang)",          107, 11,  "U-Boot",         "CHN",
              "Chinas Angriffs-U-Boot"),
    ShipClass("Type 094 (Jin-Klasse)",     135, 13,  "U-Boot",         "CHN",
              "Chinas SSBN, JL-2/3 Raketen"),
    ShipClass("U212A (Deutschland)",        57,  7,  "U-Boot",         "DEU",
              "Deutsche Brennstoffzellen-U-Boote"),
    ShipClass("Dolphin-Klasse (Israel)",    57,  7,  "U-Boot",         "ISR",
              "Nuklear-bestückt vermutet"),

    # ── Amphibische Schiffe / Landungsschiffe ─────────────────────────────────
    ShipClass("Wasp-Klasse (LHD)",         254, 42,  "Landungsschiff", "USA",
              "Amphibisches Angriffsschiff, Harrier/F-35B"),
    ShipClass("America-Klasse (LHA-6)",    257, 43,  "Landungsschiff", "USA",
              "Ohne Dock-Well, reines Luftbetrieb"),
    ShipClass("San Antonio (LPD-17)",      208, 32,  "Landungsschiff", "USA",
              "Amphibisches Transportschiff"),
    ShipClass("Ivan Gren (Projekt 11711)", 120, 16,  "Landungsschiff", "RUS",
              "Russisches Landungsschiff, Ostsee/Schwarzes Meer"),

    # ── Tanker / Versorger ────────────────────────────────────────────────────
    ShipClass("Henry J. Kaiser (T-AO)",    206, 30,  "Versorger",      "USA",
              "US Navy Tanker/Versorger"),

    # ── Zivil: Tanker ─────────────────────────────────────────────────────────
    ShipClass("VLCC Tanker",               330, 60,  "Tanker",         "Zivil",
              "Very Large Crude Carrier, 2-3 Mio Barrel Öl"),
    ShipClass("Suezmax Tanker",            275, 50,  "Tanker",         "Zivil",
              "Max Breite für Suezkanal, ~1 Mio Barrel"),
    ShipClass("Aframax Tanker",            245, 42,  "Tanker",         "Zivil",
              "Mittlerer Tanker, Nordsee/Ostsee häufig"),
    ShipClass("LNG Carrier",               295, 46,  "LNG-Frachter",   "Zivil",
              "Flüssiggas-Tanker, kugelförmige Tanks erkennbar"),
    ShipClass("LPG Carrier",               230, 37,  "LPG-Frachter",   "Zivil",
              "Flüssiggas-Propan/Butan"),

    # ── Zivil: Containerschiffe ───────────────────────────────────────────────
    ShipClass("Emma Mærsk / Triple-E",     400, 59,  "Containerschiff","Zivil",
              "Größte Containerschiffe, ~24.000 TEU"),
    ShipClass("Post-Panamax (Neo-)",       366, 51,  "Containerschiff","Zivil",
              "Zu breit für alten Panamakanal"),
    ShipClass("Panamax Containerschiff",   294, 32,  "Containerschiff","Zivil",
              "Passt gerade durch Panamakanal alt"),
    ShipClass("Feeder Containerschiff",    180, 28,  "Containerschiff","Zivil",
              "Regionale Verteilung, Ostsee/Mittelmeer"),

    # ── Zivil: Massengut / Bulker ─────────────────────────────────────────────
    ShipClass("Capesize Bulker",           295, 46,  "Bulkfrachter",   "Zivil",
              "Erz/Kohle, zu groß für Panamakanal alt"),
    ShipClass("Handymax Bulker",           185, 30,  "Bulkfrachter",   "Zivil",
              "Häufigster Bulker weltweit"),

    # ── Kreuzfahrt ────────────────────────────────────────────────────────────
    ShipClass("Wonder of the Seas (Royal)", 362, 64,  "Kreuzfahrtschiff","Zivil",
              "Größtes Kreuzfahrtschiff, 7.000 Passagiere"),
    ShipClass("Harmony/Symphony-Klasse",   362, 66,  "Kreuzfahrtschiff","Zivil",
              "Oasis-Klasse Royal Caribbean"),

    # ── Kleine / Patrouille ───────────────────────────────────────────────────
    ShipClass("Coastguard / Patrouille",    60, 10,  "Patrouillenschiff","Zivil/MIL",
              "Küstenwache, Zoll, Grenzschutz"),
    ShipClass("Fischereifahrzeug (groß)",   45,  8,  "Fischerboot",    "Zivil",
              "Industrieller Fischfang, Trawler"),
    ShipClass("Fischereifahrzeug (klein)",  18,  5,  "Fischerboot",    "Zivil",
              "Küstenfischer, sehr häufig"),
    ShipClass("Sportboot / Segelyacht",     15,  4,  "Segelschiff",    "Zivil",
              "Privatboot, sehr geringe RCS"),

    # ── USV / Drohnen ─────────────────────────────────────────────────────────
    ShipClass("Sea Hunter (ACTUV)",         40,  6,  "USV/Drohne",     "USA",
              "DARPA autonomes Drohnen-Schiff, Anti-U-Boot"),
    ShipClass("Sea Drone (Ukraine)",         5,  1,  "USV/Drohne",     "UKR",
              "Ukrainische maritime Angriffsdrohne, Schwarzes Meer"),
    ShipClass("Shahed-136 (Maritim)",        4,  2,  "USV/Drohne",     "IRN",
              "Iranische maritime Drohne, Houthi Einsatz"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Shape-Analyse aus Cluster-Pixels
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ClusterShape:
    pixel_count:  int
    bbox_rows:    int          # Bounding-Box Höhe in Pixeln
    bbox_cols:    int          # Bounding-Box Breite in Pixeln
    aspect_ratio: float        # max(rows,cols) / min(rows,cols)
    elongation:   float        # 0=quadratisch, 1=sehr lang und schmal
    compactness:  float        # Fläche / (Umfang²) → 0=zerfranst, 1=kreisförmig
    fill_ratio:   float        # pixel_count / (bbox_rows * bbox_cols)


def analyse_cluster_shape(pixels: list[int], img_width: int) -> ClusterShape:
    """
    Berechnet Formmerkmale eines Pixel-Clusters für Objektklassifikation.

    pixels: Liste von Pixel-Indizes im Bild (row * width + col)
    img_width: Breite des Bildes in Pixeln
    """
    if not pixels:
        return ClusterShape(0, 1, 1, 1.0, 0.0, 0.0, 0.0)

    rows = [p // img_width for p in pixels]
    cols = [p %  img_width for p in pixels]
    r_min, r_max = min(rows), max(rows)
    c_min, c_max = min(cols), max(cols)

    bbox_rows = max(r_max - r_min + 1, 1)
    bbox_cols = max(c_max - c_min + 1, 1)

    # Seitenverhältnis: immer >= 1
    aspect_ratio = max(bbox_rows, bbox_cols) / min(bbox_rows, bbox_cols)

    # Elongation: 0 = quadratisch, nahe 1 = sehr schmal
    elongation = 1.0 - min(bbox_rows, bbox_cols) / max(bbox_rows, bbox_cols)

    # Fill-Ratio: wie dicht ist der Cluster in seiner Bounding-Box?
    bbox_area = bbox_rows * bbox_cols
    fill_ratio = len(pixels) / bbox_area

    # Einfache Kompaktheit: Kreisförmigkeit
    # Näherung: Umfang ≈ 2*(rows+cols) des Bounding-Box
    perimeter_approx = 2 * (bbox_rows + bbox_cols)
    compactness = (4 * math.pi * len(pixels)) / (perimeter_approx ** 2) if perimeter_approx else 0.0
    compactness = min(compactness, 1.0)

    return ClusterShape(
        pixel_count  = len(pixels),
        bbox_rows    = bbox_rows,
        bbox_cols    = bbox_cols,
        aspect_ratio = round(aspect_ratio, 2),
        elongation   = round(elongation, 2),
        compactness  = round(compactness, 2),
        fill_ratio   = round(fill_ratio, 2),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Größenschätzung aus SAR-Pixeln
# ─────────────────────────────────────────────────────────────────────────────

SAR_RESOLUTION_M = 10.0   # Sentinel-1 IW GRD: ~10m/Pixel

def estimate_real_size(shape: ClusterShape,
                       image_width_px: int  = 256,
                       image_lon_span: float = 2.0,
                       image_lat_span: float = 2.0) -> tuple[float, float]:
    """
    Schätzt reale Länge und Breite des Objekts in Metern.
    Basis: Bounding-Box in Pixeln × Meter/Pixel

    image_width_px:  Breite des heruntergeladenen Kachel-Bildes
    image_lon_span:  Lon-Ausdehnung des Bildausschnitts in Grad
    image_lat_span:  Lat-Ausdehnung

    Rückgabe: (laenge_m, breite_m) → Längste Seite zuerst
    """
    # Meter pro Pixel basierend auf Bildgröße und geographischem Ausschnitt
    m_per_px_lon = (image_lon_span * 111_320) / image_width_px
    m_per_px_lat = (image_lat_span * 111_320) / image_width_px  # vereinfacht

    # Bounding-Box in Metern
    dim_row_m = shape.bbox_rows * m_per_px_lat
    dim_col_m = shape.bbox_cols * m_per_px_lon

    laenge_m = max(dim_row_m, dim_col_m)
    breite_m = min(dim_row_m, dim_col_m)
    return round(laenge_m, 0), round(breite_m, 0)


# ─────────────────────────────────────────────────────────────────────────────
# Regelbasierte Klassifikation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SarClassification:
    category:        str        # Hauptkategorie
    subcategory:     str        # Unterkategorie
    confidence:      float      # 0.0 – 1.0
    length_m:        float      # Geschätzte Länge
    width_m:         float      # Geschätzte Breite
    aspect_ratio:    float
    rcs_class:       str        # "sehr hoch" | "hoch" | "mittel" | "niedrig"
    shape_note:      str        # Kurze Beschreibung des Musters
    possible_classes: list[dict] = field(default_factory=list)  # DB-Abgleich


def classify_sar_target(
    pixel_count:  int,
    brightness:   float,     # 0-255
    shape:        ClusterShape,
    length_m:     float,
    width_m:      float,
) -> SarClassification:
    """
    Klassifiziert ein SAR-Ziel aus Formparametern und Größe.
    """
    ar  = shape.aspect_ratio
    elo = shape.elongation
    fill = shape.fill_ratio

    # Helligkeit → RCS-Klasse
    if brightness >= 240:
        rcs = "sehr hoch"     # metallreiche Strukturen (Kräne, Aufbauten)
    elif brightness >= 210:
        rcs = "hoch"          # typisch Kriegsschiff / großer Frachter
    elif brightness >= 185:
        rcs = "mittel"        # Frachter / Fischerboot
    else:
        rcs = "niedrig"       # Holzboot / Wal / Meeresrauschen

    # ── Sehr klein (1-3 Pixel, < 30m) ────────────────────────────────────────
    if length_m < 30:
        if rcs in ("sehr hoch", "hoch") and pixel_count <= 3:
            return SarClassification(
                "Drohne/USV", "Maritime Drohne / Klein-USV",
                0.55, length_m, width_m, ar, rcs,
                "Sehr kleine, helle Punkt-Reflexion – typisch für kleine Metalldrohne",
            )
        if rcs == "niedrig":
            return SarClassification(
                "Organisch", "Wal / Haifinne / Meeresrauschen",
                0.30, length_m, width_m, ar, rcs,
                "Schwache diffuse Reflexion ohne klare Metallstruktur",
            )
        return SarClassification(
            "Segelschiff/Sportboot", "Kleines Segelboot / RIB",
            0.50, length_m, width_m, ar, rcs,
            "Klein und wenig Metall – Sportboot oder Segelschiff",
        )

    # ── Klein (30-80m) ────────────────────────────────────────────────────────
    if length_m < 80:
        if ar >= 4.0 and elo > 0.6 and rcs in ("sehr hoch", "hoch"):
            return SarClassification(
                "U-Boot", "Klein-U-Boot (getaucht/aufgetaucht)",
                0.65, length_m, width_m, ar, rcs,
                f"Elongiert (AR={ar:.1f}), hell – mögliches U-Boot (Periskoptiefe/aufgetaucht)",
            )
        if ar < 2.0 and rcs == "mittel":
            return SarClassification(
                "Fischerboot", "Kleiner Kutter / Küstenfischer",
                0.70, length_m, width_m, ar, rcs,
                "Kompakt, mittlere Helligkeit – typischer kleiner Fischereibetrieb",
            )
        return SarClassification(
            "Fischerboot", "Mittelgroßes Fischereifahrzeug / Patrouille",
            0.60, length_m, width_m, ar, rcs,
            f"Länge {length_m:.0f}m, AR={ar:.1f} – Trawler oder Küstenwachboot",
        )

    # ── Mittel (80-160m) ──────────────────────────────────────────────────────
    if length_m < 160:
        if ar >= 8.0 and fill < 0.4:
            return SarClassification(
                "U-Boot", "Mittleres U-Boot (aufgetaucht)",
                0.72, length_m, width_m, ar, rcs,
                f"Stark elongiert (AR={ar:.1f}), schmaler Rumpf – U-Boot-Signatur",
            )
        if ar >= 4.0 and rcs in ("sehr hoch", "hoch"):
            return SarClassification(
                "Kriegsschiff", "Zerstörer / Fregatte",
                0.65, length_m, width_m, ar, rcs,
                f"Länge {length_m:.0f}m, AR={ar:.1f} – Größe passt zu Fregatte/Zerstörer",
            )
        if rcs == "mittel" and ar < 3.5:
            return SarClassification(
                "Frachter", "Handymax / Feeder",
                0.60, length_m, width_m, ar, rcs,
                f"Mittlere Größe {length_m:.0f}m, kompakter Rumpf – Feeder/Handymax",
            )
        return SarClassification(
            "Kriegsschiff/Frachter", "Fregatte / kleiner Frachter",
            0.50, length_m, width_m, ar, rcs,
            f"Länge {length_m:.0f}m – Fregatte oder kleiner Frachter möglich",
        )

    # ── Groß (160-260m) ───────────────────────────────────────────────────────
    if length_m < 260:
        if ar >= 8.0 and rcs in ("sehr hoch", "hoch"):
            return SarClassification(
                "U-Boot", "Großes strategisches U-Boot (SSBN)",
                0.60, length_m, width_m, ar, rcs,
                f"Sehr elongiert (AR={ar:.1f}) – mögliche SSBN wie Ohio/Borei",
            )
        if ar >= 4.5 and rcs == "sehr hoch":
            return SarClassification(
                "Kriegsschiff", "Kreuzer / großer Zerstörer",
                0.65, length_m, width_m, ar, rcs,
                f"Länge {length_m:.0f}m, AR={ar:.1f} – Slava/Kirov/Ticonderoga?",
            )
        if rcs in ("sehr hoch", "hoch") and ar < 4.0:
            return SarClassification(
                "Frachter", "Aframax Tanker / Panamax Containerschiff",
                0.68, length_m, width_m, ar, rcs,
                f"Groß, helle Aufbauten – Tanker oder großer Containerfrachter",
            )
        return SarClassification(
            "Großschiff", "Tanker / Frachter / Kreuzer",
            0.52, length_m, width_m, ar, rcs,
            f"Länge {length_m:.0f}m – groß, aber Typ unklar",
        )

    # ── Sehr groß (260-350m) ──────────────────────────────────────────────────
    if length_m < 360:
        if ar >= 3.0 and rcs in ("sehr hoch", "hoch") and width_m >= 50:
            return SarClassification(
                "Flugzeugträger", "Flugzeugträger (Nimitz / QE / de Gaulle)",
                0.75, length_m, width_m, ar, rcs,
                f"Sehr groß, breiter Rumpf ({width_m:.0f}m) + Flugdeck-Reflexion",
            )
        if ar >= 5.0:
            return SarClassification(
                "Tanker", "VLCC / Suezmax Tanker",
                0.70, length_m, width_m, ar, rcs,
                f"Sehr groß und elongiert – VLCC oder Suezmax Rohöltanker",
            )
        if rcs == "sehr hoch" and ar < 3.0:
            return SarClassification(
                "Kreuzfahrtschiff", "Großes Kreuzfahrtschiff",
                0.65, length_m, width_m, ar, rcs,
                f"Breiter Rumpf, sehr helle Aufbauten – Kreuzfahrtschiff",
            )
        return SarClassification(
            "Großfrachter", "Post-Panamax / VLCC",
            0.58, length_m, width_m, ar, rcs,
            f"Länge {length_m:.0f}m – großes Handelsschiff oder Träger",
        )

    # ── Riesig (>360m) ────────────────────────────────────────────────────────
    if width_m >= 55:
        return SarClassification(
            "Flugzeugträger", "Superträger (Ford / Nimitz / Triple-E)",
            0.80, length_m, width_m, ar, rcs,
            f"Länge {length_m:.0f}m / Breite {width_m:.0f}m – Ford-Klasse oder Emma-Maersk?",
        )
    return SarClassification(
        "Megatanker", "Ultra Large Crude Carrier (ULCC)",
        0.68, length_m, width_m, ar, rcs,
        f"Außergewöhnlich groß ({length_m:.0f}m) – Supertanker",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Optionaler Datenbankabgleich
# ─────────────────────────────────────────────────────────────────────────────

def compare_to_ship_database(
    length_m:  float,
    width_m:   float,
    category_hint: str = "",
    top_n: int = 5,
) -> list[dict]:
    """
    Vergleicht geschätzte Maße mit der Schiffsklassen-Datenbank.
    Gibt top_n beste Treffer sortiert nach Match-Score zurück.

    Match-Score berechnet sich aus:
      - Längen-Abweichung (Hauptkriterium, 60%)
      - Breiten-Abweichung (Sekundär, 40%)
    Sonderfall: Wenn width_m ≈ length_m (1-Pixel-Cluster, Breite = Pixelgröße),
      wird nur die Länge verwendet (100%), da die Breite keine echte Info enthält.
    Bonus: Kategorie-Übereinstimmung + 0.12
    """
    # Wenn Breite ≈ Länge (1-Pixel-Cluster: Breite = Pixelgröße = unbekannt),
    # nur Länge verwenden – Breite ist in diesem Fall kein echtes Maß.
    width_unknown = abs(width_m - length_m) / max(length_m, 1) < 0.20

    results = []
    for sc in SHIP_DATABASE:
        # Relative Abweichung in Länge und Breite
        len_err = abs(length_m - sc.length_m) / max(sc.length_m, 1)
        if width_unknown:
            # Nur Längenvergleich (Breite ist Pixelgröße, nicht echte Schiffsbreite)
            size_score = 1.0 - min(len_err, 1.0)
        else:
            wid_err    = abs(width_m - sc.width_m) / max(sc.width_m, 1)
            size_score = 1.0 - min((0.6 * len_err + 0.4 * wid_err), 1.0)

        # Kategorie-Bonus
        cat_bonus = 0.0
        if category_hint and (
            category_hint.lower() in sc.category.lower() or
            sc.category.lower() in category_hint.lower()
        ):
            cat_bonus = 0.12

        match = min(size_score + cat_bonus, 1.0)

        # Günstigkeits-Note
        len_pct = (length_m / sc.length_m - 1) * 100
        if abs(len_pct) < 5:
            note = "Länge exakt"
        elif len_pct > 0:
            note = f"Ziel {abs(len_pct):.0f}% länger als Klasse"
        else:
            note = f"Ziel {abs(len_pct):.0f}% kürzer als Klasse"

        results.append({
            "class":      sc.name,
            "category":   sc.category,
            "nation":     sc.nation,
            "class_len":  sc.length_m,
            "class_wid":  sc.width_m,
            "match":      round(match, 2),
            "note":       note,
            "info":       sc.note,
        })

    results.sort(key=lambda x: x["match"], reverse=True)
    return results[:top_n]


# ─────────────────────────────────────────────────────────────────────────────
# Vollständige Klassifikation (Haupt-API)
# ─────────────────────────────────────────────────────────────────────────────

def full_classify(
    pixels:          list[int],
    brightness:      float,
    img_width:       int   = 256,
    img_lon_span:    float = 2.0,
    img_lat_span:    float = 2.0,
    with_db_compare: bool  = True,
    db_top_n:        int   = 5,
) -> SarClassification:
    """
    Führt vollständige SAR-Klassifikation eines Pixel-Clusters durch.

    Args:
      pixels:          Liste aller Pixel-Indizes des Clusters
      brightness:      Mittlere Helligkeit (0-255)
      img_width:       Breite des SAR-Bildes in Pixeln
      img_lon_span:    Longitudinale Ausdehnung des Bildausschnitts (Grad)
      img_lat_span:    Latitudinale Ausdehnung des Bildausschnitts (Grad)
      with_db_compare: True → Datenbankabgleich mit möglichen Schiffsklassen
      db_top_n:        Anzahl der zurückgegebenen DB-Treffer

    Returns:
      SarClassification mit category, subcategory, confidence,
      length_m, width_m, possible_classes (wenn with_db_compare=True)
    """
    # ── Auflösungsprüfung: Bei >200 m/px ist Größenbestimmung physikalisch unmöglich ──
    # Bei 870 m/px (Hormuz 2°×2° / 256px) wäre jedes 1-Pixel-Objekt = 870m → Flugzeugträger.
    # Korrekte Lösung: Nur RCS (Helligkeit) auswerten, keine Größe/Typ-Klassifikation.
    m_per_px = max(img_lon_span, img_lat_span) * 111_320 / img_width
    if m_per_px > 200:
        return classify_from_metrics(
            size_px      = len(pixels),
            brightness   = brightness,
            img_width    = img_width,
            img_lon_span = img_lon_span,
            img_lat_span = img_lat_span,
            with_db_compare = False,   # Kein DB-Abgleich sinnlos bei unbekannter Größe
            db_top_n     = 0,
        )

    # ── Feine Auflösung (<200 m/px): Vollständige Form- und Größenanalyse ────────
    shape    = analyse_cluster_shape(pixels, img_width)
    length_m, width_m = estimate_real_size(
        shape, img_width, img_lon_span, img_lat_span
    )
    clf = classify_sar_target(
        len(pixels), brightness, shape, length_m, width_m
    )

    if with_db_compare:
        clf.possible_classes = compare_to_ship_database(
            length_m, width_m,
            category_hint=clf.category,
            top_n=db_top_n,
        )

    return clf


# ─────────────────────────────────────────────────────────────────────────────
# Schnell-Klassifikation ohne Pixel-Liste (aus vorberechneten Werten)
# ─────────────────────────────────────────────────────────────────────────────

def classify_from_metrics(
    size_px:         int,
    brightness:      float,
    img_width:       int   = 256,
    img_lon_span:    float = 2.0,
    img_lat_span:    float = 2.0,
    with_db_compare: bool  = True,
    db_top_n:        int   = 3,
) -> SarClassification:
    """
    Klassifiziert aus bereits berechneten Metriken (ohne Pixel-Liste).
    Resolution-aware: Bei grober Auflösung (>200m/px) nur RCS-Klassifikation.
    """
    m_per_px = (max(img_lon_span, img_lat_span) * 111_320) / img_width

    # Helligkeit → RCS-Klasse (funktioniert bei jeder Auflösung)
    if brightness >= 240:
        rcs = "sehr hoch"
    elif brightness >= 210:
        rcs = "hoch"
    elif brightness >= 185:
        rcs = "mittel"
    else:
        rcs = "niedrig"

    # ── Grobe Auflösung: Größenbestimmung unmöglich ───────────────────────────
    if m_per_px > 200:
        # Schiffe sind sub-pixel → nur Helligkeit (RCS) auswertbar, kein Typ bestimmbar.
        # dB-Werte bei dieser Auflösung: sehr hoch ≥ -3dB, hoch -6dB, mittel -9dB
        if rcs == "sehr hoch":
            cat  = "Starke Metallreflexion"
            sub  = "Großes Schiff / Plattform / Tanker (RCS sehr stark)"
            conf = 0.55
        elif rcs == "hoch":
            cat  = "Schiff (vermutet)"
            sub  = "Metallreflexion – Typ unklar (Auflösung zu grob)"
            conf = 0.45
        elif rcs == "mittel":
            cat  = "Schiff (vermutet)"
            sub  = "Metallreflexion – Typ und Größe unbekannt (Auflösung zu grob)"
            conf = 0.35
        else:
            cat  = "Unbekannt / Rauschen"
            sub  = "Zu schwache Reflexion – kein Schiff klassifizierbar"
            conf = 0.20

        return SarClassification(
            category     = cat,
            subcategory  = sub,
            confidence   = conf,
            length_m     = 0,
            width_m      = 0,
            aspect_ratio = 1.0,
            rcs_class    = rcs,
            shape_note   = (f"Auflösung: {m_per_px:.0f}m/px – "
                            "Zoom auf <0.2° für Größen-/Typ-Bestimmung erforderlich"),
            possible_classes = [],
        )

    # ── Feine Auflösung: Normale Klassifikation ───────────────────────────────
    side_px = max(int(math.sqrt(size_px)), 1)
    shape = ClusterShape(
        pixel_count  = size_px,
        bbox_rows    = side_px,
        bbox_cols    = side_px,
        aspect_ratio = 1.0,
        elongation   = 0.0,
        compactness  = 0.8,
        fill_ratio   = 0.9,
    )
    length_m = round(side_px * m_per_px, 0)
    width_m  = round(side_px * m_per_px, 0)

    clf = classify_sar_target(size_px, brightness, shape, length_m, width_m)
    if with_db_compare:
        clf.possible_classes = compare_to_ship_database(
            length_m, width_m,
            category_hint=clf.category,
            top_n=db_top_n,
        )
    return clf
