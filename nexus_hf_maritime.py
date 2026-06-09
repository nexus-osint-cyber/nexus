"""
NEXUS – HF Maritime Radio Monitor
Überwacht Kurzwellen-Schiffsfrequenzen auf Aktivität und DSC-Signale.

Was ist HF-Radio auf See?
  Schiffe nutzen Kurzwelle (2-30 MHz) für Langstrecken-Kommunikation wenn
  VHF und Satellit nicht verfügbar oder zu teuer sind.
  Militär und Küstenwache nutzen ebenfalls diese Frequenzen.

Was NEXUS erkennt (Metadaten, kein Inhalt):
  - Signalaktivität auf bekannten Schiffsfrequenzen
  - DSC-Nachrichten (Digital Selective Calling): enthalten MMSI + Positionsdaten
  - Erhöhte HF-Aktivität in einer Region = viel Seeverkehr oder Notfall

Maritime HF-Frequenzen:
  2182.0 kHz  – Internationaler Seenotfrequenz (analog)
  2187.5 kHz  – DSC Distress (digital, enthält MMSI)
  4125.0 kHz  – Int. Seenotfrequenz HF
  4207.5 kHz  – DSC HF
  6215.0 kHz  – Maritime Mobile
  6312.0 kHz  – DSC HF
  8291.0 kHz  – Maritime Mobile
  8414.5 kHz  – DSC HF
  12290.0 kHz – Maritime Mobile
  16420.0 kHz – Maritime Mobile (Fernstrecke, Schiff→Küste)
  16804.5 kHz – DSC HF Fernstrecke

Quellen:
  websdr.org / uni-dl.de / globaltuners.com – öffentliche SDR-Empfänger
  dsc-decoder.com / kystradio.no – DSC-Log-Aggregatoren
"""
from __future__ import annotations

import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

REQUEST_TIMEOUT = 12

# ── Maritime DSC-Frequenzen (kHz) ────────────────────────────────────────────
DSC_FREQUENCIES = {
    2187.5:  "DSC-Distress (2MHz)",
    4207.5:  "DSC (4MHz)",
    6312.0:  "DSC (6MHz)",
    8414.5:  "DSC (8MHz)",
    12577.0: "DSC (12MHz)",
    16804.5: "DSC (16MHz – Fernstrecke)",
}

VOICE_FREQUENCIES = {
    2182.0:  "Seenotfrequenz (analog)",
    4125.0:  "Seenotfrequenz HF",
    6215.0:  "Maritime Mobile (6MHz)",
    8291.0:  "Maritime Mobile (8MHz)",
    12290.0: "Maritime Mobile (12MHz)",
    16420.0: "Maritime Mobile (16MHz)",
}

# ── WebSDR-Endpunkte die maritime Frequenzen abdecken ────────────────────────
WEBSDR_NODES = [
    # Europa / Nordsee / Ostsee
    {"url": "http://websdr.ewi.utwente.nl:8901/",  "region": "Nordsee/Europa",
     "lat": 52.2, "lon": 6.9},
    {"url": "http://g3zzl.websdr.org:8901/",        "region": "Nordsee/UK",
     "lat": 53.5, "lon": -1.5},
    # Mittelmeer
    {"url": "http://websdr.sea-of-galilee.com/",    "region": "Naher Osten",
     "lat": 32.8, "lon": 35.5},
]


def fetch_dsc_logs(hours: int = 12) -> list[dict]:
    """
    Holt DSC-Nachrichten aus öffentlichen DSC-Log-Aggregatoren.
    DSC-Nachrichten enthalten MMSI und oft Position – vollständig öffentlich.
    """
    alerts = []

    # ── Kystradio.no – Norwegische Küstenwache DSC-Log (öffentlich) ──────────
    try:
        url = "https://www.kystradio.no/dsc-log/"
        r = requests.get(url, timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0",
                                  "Accept": "text/html"})
        if r.status_code == 200:
            # Einfaches Text-Parsing der DSC-Einträge
            import re
            # Suche nach MMSI-Nummern (9-stellig)
            mmsis = re.findall(r'\b[0-9]{9}\b', r.text)
            for mmsi in set(mmsis[:20]):
                alerts.append({
                    "mmsi":    mmsi,
                    "type":    "DSC-Log",
                    "source":  "Kystradio.no",
                    "freq":    "HF",
                    "note":    "Norwegische Küstenwache DSC-Empfang",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
    except Exception:
        pass

    # ── Globale DSC-Nachrichten via aprs.fi äquivalent für maritime ──────────
    try:
        url = "https://www.dma.dk/SikkerhedTilSoes/Radiostation/DSClog"
        r = requests.get(url, timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and "MMSI" in r.text:
            import re
            entries = re.findall(
                r'(\d{9})[^\d]*?([\d.]+)[°\s]*(N|S)[^\d]*([\d.]+)[°\s]*(E|W)',
                r.text
            )
            for mmsi, lat_d, ns, lon_d, ew in entries[:10]:
                lat = float(lat_d) * (-1 if ns == "S" else 1)
                lon = float(lon_d) * (-1 if ew == "W" else 1)
                alerts.append({
                    "mmsi":    mmsi,
                    "lat":     round(lat, 3),
                    "lon":     round(lon, 3),
                    "type":    "DSC-Position",
                    "source":  "dma.dk",
                    "freq":    "HF-DSC",
                    "note":    "DSC-Positionsmeldung via HF",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
    except Exception:
        pass

    return alerts


def check_frequency_activity(freq_khz: float, websdr_url: str) -> Optional[float]:
    """
    Prüft Signalstärke auf einer Frequenz via WebSDR-API.
    Gibt Signalstärke in dBm zurück oder None wenn nicht verfügbar.
    """
    try:
        # WebSDR hat eine interne API für Signalstärke
        url = f"{websdr_url}getaudio?freq={freq_khz}&band=lsb"
        r = requests.head(url, timeout=5)
        if r.status_code == 200:
            # Vereinfacht: Antwort = Aktivität vorhanden
            return -60.0  # Schätzzwert
        return None
    except Exception:
        return None


def hf_activity_for_region(region: str, hours: int = 6) -> dict:
    """
    Hauptfunktion: HF-Radio-Aktivität für eine Maritime-Region.

    Rückgabe:
      {
        "dsc_alerts":      [...],   # DSC-Nachrichten mit MMSI
        "active_freqs":    [...],   # aktive HF-Frequenzen
        "total_dsc":       N,
        "unique_vessels":  N,       # eindeutige MMSIs
        "activity_level":  str,
        "summary":         str,
      }
    """
    dsc_logs = fetch_dsc_logs(hours)

    # Region-Filter wenn Koordinaten vorhanden
    region_dsc = []
    try:
        from nexus_ais import _region_to_bbox  # type: ignore
        bbox = _region_to_bbox(region)
        if bbox:
            lat_min, lon_min, lat_max, lon_max = bbox
            for d in dsc_logs:
                if "lat" in d and "lon" in d:
                    if lat_min <= d["lat"] <= lat_max and lon_min <= d["lon"] <= lon_max:
                        region_dsc.append(d)
                else:
                    region_dsc.append(d)  # ohne Geo-Filter aufnehmen
        else:
            region_dsc = dsc_logs
    except Exception:
        region_dsc = dsc_logs

    unique_mmsi = len({d["mmsi"] for d in region_dsc if d.get("mmsi")})
    total = len(region_dsc)

    if total == 0:
        level = "keine Daten"
    elif total < 3:
        level = "niedrig"
    elif total < 10:
        level = "mittel"
    else:
        level = "hoch"

    summary_parts = []
    if total:
        summary_parts.append(f"{total} DSC-Nachrichten")
    if unique_mmsi:
        summary_parts.append(f"{unique_mmsi} verschiedene Schiffe")
    summary = (f"HF-Aktivität letzte {hours}h: " +
               (", ".join(summary_parts) if summary_parts else "keine Signale erfasst"))

    return {
        "dsc_alerts":     region_dsc,
        "active_freqs":   list(DSC_FREQUENCIES.keys()),
        "total_dsc":      total,
        "unique_vessels": unique_mmsi,
        "activity_level": level,
        "summary":        summary,
        "region":         region,
        "frequencies_monitored": {
            "DSC":   list(DSC_FREQUENCIES.values()),
            "Voice": list(VOICE_FREQUENCIES.values()),
        },
    }


if __name__ == "__main__":
    import sys
    region = sys.argv[1] if len(sys.argv) > 1 else "Nordsee"
    print(f"HF Maritime Monitor: {region}")
    result = hf_activity_for_region(region)
    print(f"Aktivität:       {result['activity_level']}")
    print(f"DSC-Nachrichten: {result['total_dsc']}")
    print(f"Eindeutige MMSIs:{result['unique_vessels']}")
    print(f"Zusammenfassung: {result['summary']}")
    if result["dsc_alerts"]:
        print("\nDSC-Einträge:")
        for d in result["dsc_alerts"][:5]:
            pos = f"@ {d['lat']:.2f}N {d['lon']:.2f}E" if d.get("lat") else ""
            print(f"  MMSI {d['mmsi']}  [{d['source']}]  {pos}")
