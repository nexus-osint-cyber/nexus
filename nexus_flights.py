"""
NEXUS - Flugdaten-Modul
Echtzeit-ADS-B-Daten via OpenSky Network (kostenlos, kein API-Key nötig).
Erkennt auffällige Flugbewegungen über konfigurierten Krisenregionen.

Hinweis (T175): get_flights() nutzt bereits eine Fallback-Kette
(ADS-B Exchange/adsb.lol → globe.adsbexchange.com → OpenSky), die exakt dem
Muster entspricht, das jetzt generisch in nexus_resilience.py verfügbar ist
(try_strategies, retry_request, TTLCache, BROWSER_HEADERS). Künftige
Quellen-Ergänzungen hier sollten dieses Toolkit nutzen statt das Retry-/
Fallback-Muster erneut von Hand zu schreiben.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

import requests

# ======================================================
# Bekannte Krisenregionen (Bounding Boxes: S, W, N, O)
# Format: (lat_min, lon_min, lat_max, lon_max)
# ======================================================
REGIONS: dict[str, tuple[float, float, float, float]] = {
    # (lat_min, lon_min, lat_max, lon_max) – bewusst großzügig für Kartendichte
    "Naher Osten":       (15.0,  25.0,  42.0,  70.0),   # Türkei bis Jemen, Ägypten bis Iran
    "Taiwan-Strasse":    (18.0, 110.0,  35.0, 130.0),   # Philippinen bis Japan-Küste
    "Rotes Meer":        ( 8.0,  27.0,  32.0,  48.0),   # Dschibuti bis Suez
    "Schwarzes Meer":    (38.0,  24.0,  50.0,  44.0),   # Türkei bis Ukraine
    "Ukraine":           (42.0,  18.0,  55.0,  42.0),   # Polen bis Russland-Grenze
    "Persischer Golf":   (18.0,  44.0,  32.0,  62.0),   # Jemen bis Pakistan-Küste
    "Hormuz-Strasse":    (18.0,  50.0,  30.0,  65.0),   # Golf von Oman + Hormuz + Golf
    "Ostsee":            (50.0,   5.0,  68.0,  32.0),   # Nordsee bis Finnland
    "Korea-Halbinsel":   (30.0, 120.0,  45.0, 135.0),   # China-Küste bis Japan
    "Sahel":             ( 5.0, -18.0,  28.0,  28.0),   # Mauretanien bis Sudan
}

OPENSKY_API = "https://opensky-network.org/api/states/all"
REQUEST_TIMEOUT = 10   # Sekunden (T156: reduziert von 15)

# T156: Harte Timeouts – (connect_timeout, read_timeout)
# connect_timeout: max Zeit bis TCP-Verbindung steht
# read_timeout: max Zeit bis erste Antwort-Bytes kommen
_CONNECT_TIMEOUT = 5   # 5s Connection-Timeout (war implizit ∞)
_READ_TIMEOUT    = 8   # 8s Read-Timeout

_HEADERS = {
    "User-Agent": "NEXUS-OSINT/2.0 (research; github.com/nexus-osint)",
    "Accept":     "application/json",
}

# ── ADS-B Exchange freie Alternativen (kein API-Key, zeigen Militärflüge) ───
# adsb.lol und airplanes.live nutzen den ADS-B Exchange Community-Feed.
# Wichtig: KEINE militärischen Transponder-Filter → bessere OSINT-Abdeckung
_ADSB_FREE_SOURCES: list[dict] = [
    {
        "name":    "adsb.lol",
        "url":     "https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{dist}",
        "timeout": (_CONNECT_TIMEOUT, _READ_TIMEOUT),   # T156: tuple (connect, read)
    },
    {
        "name":    "airplanes.live",
        "url":     "https://api.airplanes.live/v2/lat/{lat}/lon/{lon}/dist/{dist}",
        "timeout": (_CONNECT_TIMEOUT, _READ_TIMEOUT),
    },
    {
        "name":    "globe.adsbexchange.com",
        "url":     "https://globe.adsbexchange.com/re-api/?lat={lat}&lon={lon}&dist={dist}&all",
        "timeout": (_CONNECT_TIMEOUT, _READ_TIMEOUT),
    },
]

# ── ISR / Aufklärungsflugzeuge – bekannte Callsign-Muster ───────────────────
# Quelle: ADS-B Exchange, flightradar24.com, open-source OSINT
_ISR_AIRCRAFT: dict[str, dict] = {
    # Callsign-Präfix → {type, nato_role, osint_note}
    "FORTE":  {"type": "E-3 Sentry",       "role": "AWACS",     "note": "Luftraumüberwachung 360°, Radar-Reichweite 400km"},
    "JAKE":   {"type": "EP-3/P-3 Orion",   "role": "SIGINT/ASW","note": "US Navy maritime Aufklärung"},
    "JEDI":   {"type": "RC-135W/S",        "role": "SIGINT",    "note": "Rivet Joint – elektronische Aufklärung"},
    "OLIVE":  {"type": "RC-135",           "role": "SIGINT",    "note": "Strategische SIGINT-Plattform"},
    "COBRA":  {"type": "RC-135",           "role": "SIGINT",    "note": "Cobra Ball – Raketenverfolgung"},
    "TIGER":  {"type": "RC-135",           "role": "SIGINT",    "note": "Rivet Joint-Variante"},
    "MAGMA":  {"type": "RC-135",           "role": "ELINT",     "note": "USAF Spezial-ELINT"},
    "IRON":   {"type": "RC-135",           "role": "SIGINT",    "note": "USAF SIGINT-Aufklärung"},
    "POLO":   {"type": "U-2S",             "role": "IMINT",     "note": "Hochaltitudaufklärung, 70.000ft"},
    "DRAGON": {"type": "RQ-4 Global Hawk", "role": "ISR-UAV",   "note": "Langstrecken-Drohne, 30h+ Ausdauer"},
    "HAWK":   {"type": "RQ-4 Global Hawk", "role": "ISR-UAV",   "note": "HALE-Drohne – breite Flächen-IMINT"},
    "STING":  {"type": "P-8 Poseidon",     "role": "ASW/ISR",   "note": "Boeing P-8, U-Boot-Jagd + SIGINT"},
    "MANTA":  {"type": "P-8 Poseidon",     "role": "ASW/ISR",   "note": "US Navy P-8 maritime Aufklärung"},
    "SHARK":  {"type": "P-8 Poseidon",     "role": "ASW/ISR",   "note": "US Navy P-8"},
    "PEARL":  {"type": "E-8C JSTARS",      "role": "GMTI",      "note": "Bodenzielverfolgung – Truppenbewegungen detektieren"},
    "BISON":  {"type": "E-8C JSTARS",      "role": "GMTI",      "note": "Joint STARS – Bodenziel-Radar"},
    "SPAR":   {"type": "C-32/C-37",        "role": "VIP-Lift",  "note": "US Regierung / DoD VIP-Transport"},
    "SAM":    {"type": "C-32/C-40",        "role": "VIP-Lift",  "note": "Special Air Mission – US Regierung"},
    "RCH":    {"type": "C-17/C-5",         "role": "Airlift",   "note": "US Air Mobility Command – strategischer Lufttransport"},
    "NATO":   {"type": "E-3A",             "role": "AWACS",     "note": "NATO AWACS – Kooperative Luftraumkontrolle"},
    "GAF":    {"type": "Diverse",          "role": "Luftwaffe", "note": "German Air Force"},
    "RAF":    {"type": "Diverse",          "role": "Luftwaffe", "note": "Royal Air Force UK"},
    "FAF":    {"type": "Diverse",          "role": "Luftwaffe", "note": "French Air Force"},
    "BAF":    {"type": "Diverse",          "role": "Luftwaffe", "note": "Belgian Air Force"},
    "USAF":   {"type": "Diverse",          "role": "Luftwaffe", "note": "US Air Force"},
    "FANG":   {"type": "Diverse",          "role": "ANG",       "note": "US Air National Guard"},
    "WOLF":   {"type": "F-16/F-35",        "role": "Kampf",     "note": "Jagdstaffel"},
    "VIPER":  {"type": "F-16",             "role": "Kampf",     "note": "F-16 Fighting Falcon"},
    "DUKE":   {"type": "Diverse",          "role": "Kampf",     "note": "Militärische Kampfstaffel"},
    "KING":    {"type": "KC-135/KC-10",     "role": "Tanker",     "note": "Luftbetankung – verlängert Reichweite aller Flugzeuge"},
    # ── Airlift / Transport ──────────────────────────────────────────────────
    "REACH":   {"type": "C-17/C-130/C-5",  "role": "Airlift",    "note": "USAF Air Mobility Command – strategischer Transport"},
    "EVAC":    {"type": "C-17/C-130",       "role": "MedEvac",    "note": "Medizinischer Evakuierungsflug"},
    "ATLAS":   {"type": "A400M",            "role": "Airlift",    "note": "Europäischer NATO-Militärtransporter"},
    # ── Kampfjets / Escorts ──────────────────────────────────────────────────
    "EAGLE":   {"type": "F-15",             "role": "Kampf/Escort","note": "F-15 Eagle/Strike Eagle Staffel"},
    "RAPTOR":  {"type": "F-22A",            "role": "Kampf",      "note": "F-22 Raptor – Stealth Air Dominance"},
    "VIPER":   {"type": "F-16",             "role": "Kampf",      "note": "F-16 Fighting Falcon"},
    "WOLF":    {"type": "F-16/F-35",        "role": "Kampf",      "note": "Jagdstaffel"},
    "DUKE":    {"type": "Diverse",          "role": "Kampf",      "note": "Militärische Kampfstaffel"},
    "DAGGER":  {"type": "Diverse",          "role": "Kampf",      "note": "Taktischer Kampfverband"},
    # ── UAV / Drohnen ────────────────────────────────────────────────────────
    "REAPER":  {"type": "MQ-9 Reaper",      "role": "ISR-UAV",    "note": "MALE-Drohne – ISR + CAS, 27h Ausdauer"},
    "GHOST":   {"type": "RQ-170 Sentinel",  "role": "ISR-UAV",    "note": "Stealth-Drohne – extrem selten im ADS-B"},
    "ORBIT":   {"type": "Diverse",          "role": "Orbit/Relay","note": "Dauerstation / fliegendes Relais"},
    # ── Spezial ──────────────────────────────────────────────────────────────
    "TRIDENT": {"type": "E-6B Mercury",     "role": "TACAMO",     "note": "US Navy Atomar-Befehlskette (sehr selten sichtbar)"},
    "TOPAZ":   {"type": "RC-135",           "role": "SIGINT",     "note": "USAF Rivet Joint – elektronische Aufklärung"},
    "RIVET":   {"type": "RC-135W",          "role": "SIGINT",     "note": "Rivet Joint – Signalaufklärung"},
    "SCOUT":   {"type": "Diverse ISR",      "role": "ISR",        "note": "NATO Aufklärungsflug"},
    "SENTINEL": {"type": "Sentinel R1/RQ-4","role": "ISR",        "note": "UK Sentinel R1 / USAF RQ-4"},
    "AWACS":   {"type": "E-3 Sentry",       "role": "AWACS",      "note": "Airborne Warning And Control System"},
    "CAOC":    {"type": "E-3 / Command",    "role": "C2",         "note": "NATO Combined Air Operations Center"},
    # ── Nationale Luftwaffen ─────────────────────────────────────────────────
    "GAF":     {"type": "Diverse",          "role": "Luftwaffe",  "note": "German Air Force"},
    "RAF":     {"type": "Diverse",          "role": "Luftwaffe",  "note": "Royal Air Force UK"},
    "FAF":     {"type": "Diverse",          "role": "Luftwaffe",  "note": "French Air Force"},
    "BAF":     {"type": "Diverse",          "role": "Luftwaffe",  "note": "Belgian Air Force"},
    "USAF":    {"type": "Diverse",          "role": "Luftwaffe",  "note": "US Air Force"},
    "FANG":    {"type": "Diverse",          "role": "ANG",        "note": "US Air National Guard"},
    "RFF":     {"type": "Diverse RuAF",     "role": "Russland",   "note": "Russian Federation Forces"},
    "SAM":     {"type": "C-32/C-40",        "role": "VIP-Lift",   "note": "Special Air Mission – US Regierung"},
    "SPAR":    {"type": "C-32/C-37",        "role": "VIP-Lift",   "note": "US DoD VIP-Transport"},
}

# ── ICAO-Hex-Whitelist: bekannte ISR/Militär-Blöcke ─────────────────────────
# Quelle: ICAO Doc 9684, ADS-B Exchange, github.com/Mictronics/readsb
# Format: (hex_prefix_lower, match_len, type, role, note)
_ICAO_HEX_WHITELIST: list[tuple] = [
    # ── USA Militär ─────────────────────────────────────────────────────────
    ("ae",   2, "US Militär",          "Militär",     "ICAO AE####: USAF/USN/USMC reserviert"),
    ("acc9", 4, "RQ-4 Global Hawk",    "ISR-UAV",     "Bekannte USAF Global Hawk ICAO-Gruppe"),
    # ── UK Royal Air Force ──────────────────────────────────────────────────
    ("43c3", 4, "RC-135W Rivet Joint", "SIGINT",      "RAF ZZ664/ZZ665 – britische SIGINT"),
    ("43c4", 4, "Voyager KC2/KC3",     "Tanker",      "RAF Airbus A330 Tanker/Transport"),
    ("43c5", 4, "A400M Atlas",         "Airlift",     "RAF Taktischer Transport"),
    ("43c1", 4, "Typhoon FGR4",        "Kampf",       "Royal Air Force Eurofighter"),
    ("43c0", 3, "RAF Diverse",         "Militär",     "Royal Air Force ICAO-Block 43C###"),
    # ── Deutschland Luftwaffe (NUR spezifische Bundeswehr-Blöcke, NICHT 3c breit!) ──
    # WICHTIG: 3c#### ist der GESAMTE deutsche ICAO-Block inkl. Lufthansa/zivil.
    # Nur bekannte Bundeswehr-Unterblöcke eintragen:
    ("3c6416", 6, "A310 MRTT",         "Tanker",      "Luftwaffe 10+21 bis 10+27 Luftbetankung"),
    ("3c6417", 6, "A310 MRTT",         "Tanker",      "Luftwaffe A310 MedEvac/Tanker"),
    ("3c6418", 6, "A310 MRTT",         "Tanker",      "Luftwaffe A310 MedEvac/Tanker"),
    ("3c6419", 6, "A310 MRTT",         "Tanker",      "Luftwaffe A310 MedEvac/Tanker"),
    ("3c001",  5, "Eurofighter",        "Kampf",       "Luftwaffe Eurofighter Typhoon"),
    ("3c002",  5, "Eurofighter",        "Kampf",       "Luftwaffe Eurofighter Typhoon"),
    ("3c003",  5, "Tornado ECR",        "Kampf/SEAD",  "Luftwaffe Tornado"),
    ("3c657",  5, "CH-53G",             "Transport",   "Bundeswehr CH-53 Transporthubschrauber"),
    # ── Frankreich ───────────────────────────────────────────────────────────
    ("3b",   2, "Armée de l'Air",      "Militär",     "Französische Luftwaffe ICAO-Block 3B####"),
    ("3823", 4, "E-3F Sentry",         "AWACS",       "Armée de l'Air AWACS"),
    # ── NATO AWACS (Luxembourg-Reg.) ─────────────────────────────────────────
    ("4408", 4, "E-3A Sentry NATO",    "AWACS",       "NATO AWACS LX-Registrierung #1"),
    ("4409", 4, "E-3A Sentry NATO",    "AWACS",       "NATO AWACS LX-Registrierung #2"),
    # ── Russland / GUS ───────────────────────────────────────────────────────
    ("aa",   2, "VKS Russland",        "Militär",     "ICAO AA####: Russische Luft-Raumfahrt-Kräfte"),
    ("ab",   2, "VKS Russland",        "Militär",     "ICAO AB####: Russische Luftwaffe Transport"),
    ("ac",   2, "VKS Russland",        "Militär",     "ICAO AC####: Russische Bomber/Transport"),
    # ── Niederlande / Belgien ────────────────────────────────────────────────
    ("480",  3, "RNLAF",               "Militär",     "Royal Netherlands Air Force"),
    ("484",  3, "Belgian Air Comp.",   "Militär",     "Belgian Air Component"),
    # ── China ────────────────────────────────────────────────────────────────
    ("780",  3, "PLAAF",               "Militär",     "PLA Air Force ICAO-Block"),
    ("781",  3, "PLAN Aviation",       "Militär",     "PLA Navy Aviation"),
    # ── Israel (NUR bekannte IAF Hexcodes, NICHT breiten 738-Block!) ──────────
    # El Al und andere Zivile teilen den 738-Block → keine breite Erkennung
    # IAF-Flugzeuge erscheinen meist ohne Callsign oder mit Militär-CS
    ("738041", 6, "IAF F-35I Adir",    "Kampf",       "Israeli Air Force F-35I ICAO-Bereich"),
    ("738042", 6, "IAF F-35I Adir",    "Kampf",       "Israeli Air Force F-35I ICAO-Bereich"),
    # ── Türkei (NUR bekannte TurAF-Blöcke, NICHT breiten 4b8-Block!) ─────────
    ("4b8f5", 5, "TurAF KC-135",       "Tanker",      "Türkische Luftwaffe KC-135 Tanker"),
    ("4b8f6", 5, "TurAF E-7T",         "AWACS",       "Türkische Luftwaffe Boeing E-7T"),
    # ── Polen (NATO) ─────────────────────────────────────────────────────────
    ("489",  3, "Polish Air Force",    "Militär",     "Polskie Siły Powietrzne"),
]

# Klassischer Präfix-Set (Abwärtskompatibilität)
_MILITARY_ICAO_PREFIXES: set[str] = {e[0] for e in _ICAO_HEX_WHITELIST}

# Klassischer Militärhints-Set (Abwärtskompatibilität)
_MILITARY_HINTS = set(_ISR_AIRCRAFT.keys())


def _is_helicopter(callsign: str, on_ground: bool,
                   velocity_ms: float, altitude_m: float) -> bool:
    """
    Erkennt Hubschrauber anhand von Callsign-Muster, Geschwindigkeit und Höhe.
    Hubschrauber: typisch <70 m/s (252 km/h) UND <2000m Höhe.
    """
    if on_ground:
        return False
    cs = (callsign or "").strip().upper()
    # Bekannte Hubschrauber-Callsign-Muster
    _HELI_PREFIXES = (
        "CHRISTOPH",  # ADAC Rettungshubschrauber (z.B. CHRISTOPH1)
        "BOH",        # Bundespolizei Hubschrauber
        "ADAC",       # ADAC
        "DRF",        # DRF Luftrettung
        "SAR",        # Search & Rescue
        "RSCU",       # Air Ambulance
        "HEMS",       # Helicopter Emergency Medical Service
        "POLICE",     # Polizei international
        "RESCUE",     # Rettung
        "MEDIC",      # Medizinisch
        "LIFEGUARD",  # Küstenwache
        "GUARDIAN",   # US Coast Guard
    )
    if any(cs.startswith(p) for p in _HELI_PREFIXES):
        return True
    # Deutsche Zivilhubschrauber: D-H*** Kennzeichen
    if cs.startswith("D-H") or cs.startswith("DH"):
        return True
    # Physikalische Kriterien: langsam UND niedrig (Hubschrauber-Bereich)
    if velocity_ms and altitude_m is not None:
        if velocity_ms < 70 and altitude_m < 2000 and altitude_m > 0:
            return True
    return False


# Bekannte zivile IATA/ICAO-Airline-Prefixe → niemals als Militär flaggen
_CIVILIAN_CALLSIGN_PREFIXES: frozenset = frozenset([
    "DLH",  # Lufthansa
    "BAW",  # British Airways
    "AFR",  # Air France
    "KLM",  # KLM
    "UAE",  # Emirates
    "THY",  # Turkish Airlines
    "SWR",  # Swiss
    "AUA",  # Austrian
    "IBE",  # Iberia
    "RYR",  # Ryanair
    "EZY",  # EasyJet
    "WZZ",  # Wizz Air
    "AZA",  # Alitalia/ITA
    "DAL",  # Delta
    "UAL",  # United
    "AAL",  # American Airlines
    "SAS",  # Scandinavian
    "LOT",  # LOT Polish
    "CSN",  # China Southern
    "CCA",  # Air China
    "QFA",  # Qantas
    "ETH",  # Ethiopian
    "FIN",  # Finnair
    "TAP",  # TAP Portugal
    "BEL",  # Brussels Airlines
    "VOE",  # Vueling
    "EIN",  # Aer Lingus
    # Israel zivil
    "ELY",  # El Al Israel Airlines
    "AIZ",  # Arkia Israeli Airlines
    "ISR",  # Israir Airlines
    # Türkei zivil
    "THY",  # Turkish Airlines (already above, duplicate safe)
    "PGT",  # Pegasus Airlines
    "TKF",  # Turkmenistan Airlines
    # Sonstige zivil
    "QTR",  # Qatar Airways
    "ETD",  # Etihad
    "SVA",  # Saudi Arabian Airlines
    "GFA",  # Gulf Air
    "KAC",  # Kuwait Airways
    "MEA",  # Middle East Airlines
    "OMA",  # Oman Air
])


def _classify_isr(callsign: str, icao24: str = "") -> dict:
    """
    Prüft ob ein Flugzeug ein bekanntes ISR/Aufklärungsflugzeug ist.
    Gibt dict {is_isr, isr_type, isr_role, isr_note, confidence} zurück.
    """
    cs = (callsign or "").strip().upper()
    icao = (icao24 or "").strip().lower()

    # Zivile Airlines sofort ausschließen
    for prefix in _CIVILIAN_CALLSIGN_PREFIXES:
        if cs.startswith(prefix):
            return {"is_isr": False, "isr_type": "", "isr_role": "", "isr_note": "", "confidence": ""}

    # 1. Callsign-Präfix-Match (spezifischste Erkennung zuerst)
    for prefix, info in sorted(_ISR_AIRCRAFT.items(), key=lambda x: -len(x[0])):
        if cs.startswith(prefix.upper()):
            return {
                "is_isr":     True,
                "isr_type":   info["type"],
                "isr_role":   info["role"],
                "isr_note":   info["note"],
                "confidence": "high",
            }

    # 2. ICAO-Hex-Whitelist (präziseste Übereinstimmung zuerst – längster Prefix)
    if icao:
        best_match = None
        best_len   = 0
        for (prefix, min_len, itype, irole, inote) in _ICAO_HEX_WHITELIST:
            if len(prefix) >= min_len and icao.startswith(prefix) and len(prefix) > best_len:
                best_match = (itype, irole, inote)
                best_len   = len(prefix)
        if best_match:
            itype, irole, inote = best_match
            # Zivile Einträge (z.B. US N-Register) nicht als ISR markieren
            if irole.lower() != "zivil":
                return {
                    "is_isr":     True,
                    "isr_type":   itype,
                    "isr_role":   irole,
                    "isr_note":   inote,
                    "confidence": "medium" if best_len <= 2 else "high",
                }

    # 3. Nur Ziffern im Callsign = oft Militär ohne Kennung
    if cs and cs.isdigit():
        return {
            "is_isr":     True,
            "isr_type":   "Unbekannt",
            "isr_role":   "Militär (verschleiert)",
            "isr_note":   "Rein numerischer Callsign – typisch für Militärflugzeuge ohne Kennung",
            "confidence": "low",
        }

    return {"is_isr": False, "isr_type": "", "isr_role": "", "isr_note": "", "confidence": ""}


def _is_suspicious(callsign: str, on_ground: bool, velocity: float,
                   icao24: str = "") -> str:
    """
    Gibt einen Hinweis zurück wenn das Flugzeug auffällig ist.
    Leer = unauffällig. ISR-Flugzeuge werden speziell markiert.
    """
    if not callsign:
        return "kein Transponder-Callsign (mögliches Militärflugzeug)"

    # ISR-Klassifizierung
    isr = _classify_isr(callsign, icao24)
    if isr["is_isr"]:
        role_str = f"{isr['isr_type']} [{isr['isr_role']}]"
        return f"⚠ ISR/AUFKLÄRUNG: {role_str} – {isr['isr_note']}"

    # Sehr langsam + nicht am Boden + kein kommerzielles Muster → Drohne?
    if velocity and velocity < 30 and not on_ground:
        return f"ungewöhnlich langsam ({velocity:.0f} m/s) – möglicherweise Drohne/Aufklärung"
    return ""


GEOCODING_URL   = "https://geocoding-api.open-meteo.com/v1/search"


def _geocode_to_bbox(location: str, margin_deg: float = 2.0) -> Optional[tuple[float, float, float, float, float, float, str]]:
    """
    Geocodiert einen Ortsnamen und gibt eine Bounding Box zurück.
    margin_deg = halbe Kantenlänge der Box in Grad (~110km pro Grad).
    Automatische Anpassung je nach Ort-Typ:
      Stadt    → 1.5° (~165km)   = präzise, nur lokale Flüge
      Provinz  → 3.0° (~330km)
      Land     → 8.0° (~880km)
    Gibt (lat_min, lon_min, lat_max, lon_max, center_lat, center_lon, display) zurück.
    """
    try:
        r = requests.get(GEOCODING_URL, params={
            "name": location, "count": 1, "language": "de", "format": "json"
        }, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        res      = results[0]
        clat     = res["latitude"]
        clon     = res["longitude"]
        display  = res.get("name", location)
        country  = res.get("country", "")
        if country and country != display:
            display = f"{display}, {country}"
        # Margin je nach Ortstyp (feature_code aus Open-Meteo)
        fc = res.get("feature_code", "")
        if fc.startswith("PC"):          # Land
            m = 8.0
        elif fc.startswith("A"):         # Admin-Region / Provinz
            m = 3.0
        elif fc in ("PPLC", "PPLA"):     # Hauptstadt / große Stadt
            m = 2.0
        else:                            # normale Stadt / Ort
            m = 1.5
        return (clat - m, clon - m, clat + m, clon + m, clat, clon, display)
    except Exception:
        return None


def _bbox_to_radius_nm(lat_min: float, lon_min: float,
                       lat_max: float, lon_max: float) -> tuple[float, float, float, int]:
    """
    Berechnet Mittelpunkt + Radius (nautische Meilen) aus einer Bounding Box.
    Gibt (center_lat, center_lon, radius_nm, radius_nm_int) zurück.
    Begrenzt auf 250 NM (API-Maximum).
    """
    import math
    clat = (lat_min + lat_max) / 2
    clon = (lon_min + lon_max) / 2
    # Haversine-Diagonale / 2
    dlat = math.radians(lat_max - lat_min)
    dlon = math.radians(lon_max - lon_min)
    a    = math.sin(dlat/2)**2 + math.cos(math.radians(lat_min)) * math.cos(math.radians(lat_max)) * math.sin(dlon/2)**2
    dist_km = 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    radius_nm = min(int(dist_km / 1.852 / 2) + 10, 250)
    return clat, clon, radius_nm


def _fetch_adsbx(center_lat: float, center_lon: float,
                 radius_nm: int = 250) -> tuple[list, str]:
    """
    Versucht freie ADS-B Exchange-kompatible Quellen.
    Gibt (aircraft_list, source_name) zurück.
    aircraft_list ist leer wenn alle Quellen fehlschlagen.
    """
    for src in _ADSB_FREE_SOURCES:
        try:
            url = src["url"].format(
                lat=round(center_lat, 4),
                lon=round(center_lon, 4),
                dist=radius_nm,
            )
            r = requests.get(url, timeout=src["timeout"], headers=_HEADERS)
            r.raise_for_status()
            data = r.json()
            ac_list = data.get("ac") or data.get("aircraft") or []
            if ac_list:
                return ac_list, src["name"]
        except Exception:
            continue
    return [], ""


def _parse_adsbx_entry(entry: dict) -> dict:
    """
    Normalisiert einen ADS-B Exchange / adsb.lol Aircraft-Eintrag
    in das NEXUS-Standardformat (identisch mit OpenSky-Ausgabe).

    ADS-B Exchange Felder:
      hex       → icao24
      flight    → callsign (mit Leerzeichen getrimmt)
      r         → registration
      t         → type (ICAO aircraft type code)
      lat, lon  → Position
      alt_baro  → Barometrische Höhe in FEET (nicht Meter!)
      gs        → Ground Speed in Knoten
      track     → Kurs in Grad
      squawk    → Squawk-Code
      emergency → Notfall-Status
    """
    icao24   = (entry.get("hex") or "").strip().lower()
    callsign = (entry.get("flight") or "").strip()
    reg      = (entry.get("r") or "").strip()
    ac_type  = (entry.get("t") or "").strip()
    lat      = entry.get("lat")
    lon      = entry.get("lon")
    alt_ft   = entry.get("alt_baro")   # FEET
    gs_kts   = entry.get("gs")         # Knoten
    track    = entry.get("track")
    squawk   = entry.get("squawk") or ""
    emg      = str(entry.get("emergency") or "").strip()

    # Konvertierungen (NEXUS-Standard: m und km/h)
    alt_m  = round(alt_ft * 0.3048) if isinstance(alt_ft, (int, float)) else None
    vel_ms = round(gs_kts * 0.5144, 1) if isinstance(gs_kts, (int, float)) else None
    vel_kmh= round(gs_kts * 1.852)     if isinstance(gs_kts, (int, float)) else None

    # on_ground: alt_baro kann "ground" als String haben
    on_ground = (alt_ft == "ground") or (isinstance(alt_ft, (int, float)) and alt_ft < 50)

    # Quelle anreichern
    note_parts = []
    if reg:
        note_parts.append(f"Reg: {reg}")
    if ac_type:
        note_parts.append(f"Typ: {ac_type}")
    if squawk in ("7500", "7600", "7700"):
        note_parts.append(f"⚠ SQUAWK {squawk}")
    if emg and emg not in ("none", "", "0"):
        note_parts.append(f"⚠ NOTFALL: {emg}")

    return {
        "icao24":       icao24,
        "callsign":     callsign or "(kein)",
        "origin":       "",           # adsbx hat kein Herkunftsland direkt
        "registration": reg,
        "ac_type":      ac_type,
        "lat":          lat,
        "lon":          lon,
        "altitude_m":   alt_m,
        "altitude_ft":  int(alt_ft) if isinstance(alt_ft, (int, float)) else None,
        "velocity_ms":  vel_ms,
        "velocity_kmh": vel_kmh,
        "on_ground":    on_ground,
        "track":        track,
        "squawk":       squawk,
        "adsbx_note":   " | ".join(note_parts),
        "_source":      "adsbx",
    }


def get_flights(region_name: str,
                max_results: int = 50) -> Optional[dict]:
    """
    Ruft aktuelle Flugdaten für eine benannte Region oder beliebigen Ort ab.
    Bekannte Regionen: direkte BBox aus REGIONS-Dict.
    Unbekannte Orte: Geocoding via Open-Meteo → dynamische BBox.
    """
    # 1. Bekannte Krisenregionen
    if region_name in REGIONS:
        lat_min, lon_min, lat_max, lon_max = REGIONS[region_name]
        center_lat = (lat_min + lat_max) / 2
        center_lon = (lon_min + lon_max) / 2
        display_name = region_name
    else:
        # 2. Geocoding-Fallback für beliebige Orte
        geo = _geocode_to_bbox(region_name)
        if not geo:
            return {"error": f"Ort '{region_name}' nicht gefunden – OpenSky kann keine Flugdaten liefern."}
        lat_min, lon_min, lat_max, lon_max, center_lat, center_lon, display_name = geo

    # ── Datenquelle: ADS-B Exchange (primär) → OpenSky (Fallback) ──────────────
    # ADS-B Exchange (adsb.lol/airplanes.live) zeigt Militärflüge,
    # OpenSky filtert viele Militärtransponder heraus.
    radius_nm    = _bbox_to_radius_nm(lat_min, lon_min, lat_max, lon_max)
    adsbx_raw, adsbx_src = _fetch_adsbx(center_lat, center_lon, radius_nm)
    use_adsbx = bool(adsbx_raw)

    if use_adsbx:
        # ADS-B Exchange Daten verwenden
        states_normalized = []
        for entry in adsbx_raw[:max_results]:
            parsed = _parse_adsbx_entry(entry)
            # Nur Einträge mit gültiger Position
            if parsed["lat"] and parsed["lon"]:
                states_normalized.append(parsed)
        data_source = adsbx_src
    else:
        # Fallback: OpenSky
        params = {
            "lamin": lat_min,
            "lomin": lon_min,
            "lamax": lat_max,
            "lomax": lon_max,
        }
        try:
            r = requests.get(OPENSKY_API, params=params,
                             timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                             headers=_HEADERS)
            r.raise_for_status()
            data = r.json()
        except requests.Timeout:
            return {"error": "OpenSky API Timeout - Server nicht erreichbar"}
        except requests.RequestException as exc:
            return {"error": f"OpenSky API Fehler: {exc}"}
        except ValueError:
            return {"error": "OpenSky API: ungültige Antwort"}
        states = data.get("states") or []
        # OpenSky-Format in Normalized-Format konvertieren
        states_normalized = []
        for s in states[:max_results]:
            states_normalized.append({
                "icao24":       s[0] or "",
                "callsign":     (s[1] or "").strip() or "(kein)",
                "origin":       s[2] or "unbekannt",
                "registration": "",
                "ac_type":      "",
                "lat":          s[6],
                "lon":          s[5],
                "altitude_m":   s[7],
                "altitude_ft":  round(s[7] * 3.281) if s[7] else None,
                "velocity_ms":  s[9],
                "velocity_kmh": round(s[9] * 3.6) if s[9] else None,
                "on_ground":    bool(s[8]),
                "track":        s[10],
                "squawk":       "",
                "adsbx_note":   "",
                "_source":      "opensky",
            })
        data_source = "OpenSky"

    if not states_normalized:
        return {
            "region":      display_name,
            "center_lat":  center_lat,
            "center_lon":  center_lon,
            "timestamp":   datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
            "total": 0, "airborne": 0, "no_callsign": 0,
            "suspicious": [], "aircraft": [],
            "data_source": data_source,
            "summary":     f"Keine Flugzeuge über {display_name} detektiert.",
        }

    # Einheitliche Verarbeitung (egal ob ADS-B Exchange oder OpenSky)
    aircraft = []
    suspicious = []
    no_callsign = 0
    airborne = 0

    for s in states_normalized:
        icao24    = s.get("icao24", "")
        callsign  = s.get("callsign", "(kein)")
        origin    = s.get("origin", "")
        latitude  = s.get("lat")
        longitude = s.get("lon")
        altitude  = s.get("altitude_m")
        on_ground = s.get("on_ground", False)
        velocity  = s.get("velocity_ms")
        track     = s.get("track")

        if not on_ground:
            airborne += 1
        if not callsign or callsign == "(kein)":
            no_callsign += 1

        hint    = _is_suspicious(callsign, on_ground, velocity or 0, icao24)
        is_heli = _is_helicopter(callsign, on_ground, velocity or 0, altitude or 0)
        isr     = _classify_isr(callsign, icao24)

        # Squawk-Notsignal direkt als suspicious markieren
        squawk = s.get("squawk", "")
        if squawk in ("7500", "7600", "7700") and not hint:
            hint = f"SQUAWK {squawk} NOTSIGNAL"

        entry = {
            "icao24":       icao24,
            "callsign":     callsign,
            "origin":       origin,
            "registration": s.get("registration", ""),
            "ac_type":      s.get("ac_type", ""),
            "lat":          latitude,
            "lon":          longitude,
            "altitude_m":   altitude,
            "altitude_ft":  s.get("altitude_ft"),
            "velocity_ms":  velocity,
            "velocity_kmh": s.get("velocity_kmh"),
            "on_ground":    on_ground,
            "track":        track,
            "suspicious":   hint,
            "helicopter":   is_heli,
            "squawk":       squawk,
            "data_source":  data_source,
            # ISR-Felder
            "is_isr":       isr["is_isr"],
            "isr_type":     isr["isr_type"],
            "isr_role":     isr["isr_role"],
            "isr_note":     isr["isr_note"],
            "isr_conf":     isr["confidence"],
            # T191: ISR Bounding-Box – nur Flugzeuge ÜBER der Zielregion zählen
            # Verhindert False-Positives z.B. russische VKS über Saudi-Arabien
            "isr_in_target_zone": (
                lat_min <= (latitude or 0.0) <= lat_max
                and lon_min <= (longitude or 0.0) <= lon_max
            ) if (isr["is_isr"] and latitude is not None and longitude is not None)
            else (not isr["is_isr"]),   # Nicht-ISR: kein Zone-Filter nötig
        }
        aircraft.append(entry)
        if hint:
            suspicious.append(entry)

    ts = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    return {
        "region":           display_name,
        "center_lat":       center_lat,
        "center_lon":       center_lon,
        "timestamp":        ts,
        "data_source":      data_source,
        "total":            len(states_normalized),
        "airborne":         airborne,
        "no_callsign":      no_callsign,
        "suspicious_count": len(suspicious),
        "suspicious":       suspicious,
        "aircraft":         aircraft,
        "summary":          _build_summary(display_name, ts, len(states_normalized), airborne,
                                           no_callsign, suspicious, data_source),
    }


def _build_summary(region: str, ts: str, total: int, airborne: int,
                   no_callsign: int, suspicious: list,
                   data_source: str = "OpenSky") -> str:
    """Baut die menschenlesbare Zusammenfassung für den LLM-Kontext."""
    src_tag = f" [Quelle: {data_source}]" if data_source else ""
    lines = [
        f"FLUGLAGEBILD – {region}{src_tag}",
        f"Stand: {ts}",
        f"Transponder aktiv: {total} | In der Luft: {airborne} | Ohne Callsign: {no_callsign}",
    ]
    if suspicious:
        lines.append(f"\n⚠ AUFFÄLLIGE FLUGZEUGE ({len(suspicious)}):")
        for a in suspicious[:10]:
            alt = f"{a['altitude_ft']}ft" if a['altitude_ft'] else "Höhe unbekannt"
            spd = f"{a['velocity_kmh']}km/h" if a['velocity_kmh'] else ""
            lines.append(
                f"  ✈ {a['callsign']} ({a['origin']}) | {alt} {spd} | {a['suspicious']}"
            )
    else:
        lines.append("Keine auffälligen Flugzeuge detektiert.")
    return "\n".join(lines)


def flights_for_llm(region_name: str) -> str:
    """Gibt einen fertig formatierten String für den LLM-Kontext zurück."""
    result = get_flights(region_name)
    if result is None:
        return f"[FLUGDATEN] Abfrage für {region_name} fehlgeschlagen."
    if "error" in result:
        return f"[FLUGDATEN] Fehler: {result['error']}"
    return f"[FLUGDATEN – ECHTZEIT]\n{result['summary']}"


def get_all_regions_brief() -> str:
    """Schneller Überblick aller vordefinierten Krisenregionen."""
    lines = ["FLUGÜBERSICHT – Krisenregionen"]
    for rname in REGIONS:
        try:
            result = get_flights(rname)
            if result and "error" not in result:
                susp = result.get("suspicious_count", len(result.get("suspicious", [])))
                icon = " ⚠" if susp > 0 else " ✓"
                lines.append(
                    f"  {rname:<22} {result['airborne']:3} in der Luft | {susp} auffällig{icon}"
                )
            else:
                lines.append(f"  {rname:<22} [Fehler]")
        except Exception:
            lines.append(f"  {rname:<22} [nicht erreichbar]")
        time.sleep(0.3)
    return "\n".join(lines)


# ── Direktaufruf zum Testen ──────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    region = sys.argv[1] if len(sys.argv) > 1 else "Kuba"
    print(flights_for_llm(region))


def get_all_regions_brief() -> str:
    """Schneller Überblick aller vordefinierten Krisenregionen."""
    lines = ["FLUGÜBERSICHT – Krisenregionen"]
    for rname in REGIONS:
        try:
            result = get_flights(rname)
            if result and "error" not in result:
                susp = result.get("suspicious_count", len(result.get("suspicious", [])))
                icon = " ⚠" if susp > 0 else " ✓"
                lines.append(f"  {icon} {rname}: {result.get('total_aircraft',0)} AC, {susp} auffällig")
        except Exception as e:
            lines.append(f"  ✗ {rname}: Fehler – {e}")
    return "\n".join(lines)
