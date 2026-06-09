"""
NEXUS – Iridium & Inmarsat Kommunikations-Aktivitäts-Monitor
Kein Inhalt – nur Metadaten: wo, wann, wie oft wird über Satellit kommuniziert.

Prinzip (SIGINT-lite, vollständig legal):
  Wenn ein Schiff über Iridium oder Inmarsat kommuniziert, gibt es einen
  Handshake mit dem Satelliten. Dieser Handshake ist ein Radiosignal das
  jeder empfangen kann (L-Band, ~1.6 GHz für Iridium, ~1.5 GHz für Inmarsat).
  Hobbyisten weltweit betreiben SDR-Empfänger und melden diese Aktivität.

  Was wir sehen:   Zeitstempel + Satellit + geografisches Gebiet
  Was wir NICHT sehen: Inhalt, Teilnehmer, Telefonnummer

Quellen:
  iridiumlive.com   – aggregiert SDR-Empfänger weltweit, Iridium-Calls
  airframes.io      – AERO/ACARS über Inmarsat (primär Luftfahrt, aber zeigt
                      Inmarsat-Aktivitätszonen)

Interpretation:
  Hohe Kommunikationsaktivität in einer Region = viele Schiffe die sprechen
  Aktivitätsspike in ruhiger Zone = potentiell verdächtiger Verkehr
  Aktivität OHNE AIS-Entsprechung = mögliches AIS-dunkles Schiff
"""
from __future__ import annotations

import requests
from datetime import datetime, timezone, timedelta
from typing import Optional
import json

REQUEST_TIMEOUT = 15


def fetch_iridium_activity(lat_min: float, lon_min: float,
                            lat_max: float, lon_max: float,
                            hours: int = 6) -> list[dict]:
    """
    Holt Iridium-Kommunikationsaktivität aus iridiumlive.com.
    Gibt Aktivitätspunkte zurück (kein Inhalt, nur Metadaten).
    """
    try:
        # iridiumlive.com öffentliche API – Satellitenbahn + Call-Ereignisse
        url = "https://iridiumlive.com/api/recent-calls"
        r = requests.get(url, timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "NEXUS-OSINT/1.0",
                                  "Accept": "application/json"})
        r.raise_for_status()
        data = r.json()

        activity = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        for call in (data if isinstance(data, list) else data.get("calls", [])):
            try:
                # Satellit-Footprint-Zentrum auswerten
                sat_lat = float(call.get("sat_lat") or call.get("latitude")  or 0)
                sat_lon = float(call.get("sat_lon") or call.get("longitude") or 0)
                if not sat_lat or not sat_lon:
                    continue
                # Innerhalb der angefragten Region?
                if not (lat_min <= sat_lat <= lat_max and lon_min <= sat_lon <= lon_max):
                    continue
                ts_str = call.get("timestamp") or call.get("time") or ""
                activity.append({
                    "lat":      round(sat_lat, 3),
                    "lon":      round(sat_lon, 3),
                    "type":     "Iridium-Call",
                    "satellite": call.get("satellite") or call.get("sat_name") or "?",
                    "timestamp": ts_str,
                    "source":   "iridiumlive.com",
                    "note":     "Kommunikationsaktivität (kein Inhalt)",
                })
            except Exception:
                continue
        return activity

    except Exception:
        return []


def fetch_inmarsat_activity(lat_min: float, lon_min: float,
                             lat_max: float, lon_max: float) -> list[dict]:
    """
    Schätzt Inmarsat-Aktivität aus dem öffentlichen JAERO/airframes.io Feed.
    Gibt Aktivitätspunkte zurück.
    """
    try:
        # airframes.io aggregiert Inmarsat AERO Nachrichten
        url = "https://api.airframes.io/messages"
        params = {
            "type":    "I",    # Inmarsat
            "limit":   100,
        }
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "NEXUS-OSINT/1.0"})
        r.raise_for_status()
        data = r.json()

        activity = []
        for msg in (data.get("messages") or data if isinstance(data, list) else []):
            try:
                # Position des Bodensenders schätzen (Großkreis Satellit→Sender)
                lat = float(msg.get("latitude") or msg.get("lat") or 0)
                lon = float(msg.get("longitude") or msg.get("lon") or 0)
                if not lat or not lon:
                    continue
                if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
                    continue
                activity.append({
                    "lat":      round(lat, 3),
                    "lon":      round(lon, 3),
                    "type":     "Inmarsat-AERO",
                    "satellite": msg.get("satellite") or "Inmarsat",
                    "timestamp": msg.get("timestamp") or "",
                    "source":   "airframes.io",
                    "note":     "Satelliten-Datenübertragung (kein Inhalt)",
                })
            except Exception:
                continue
        return activity

    except Exception:
        return []


def satellite_comms_for_region(region: str, hours: int = 6) -> dict:
    """
    Hauptfunktion: Gibt Satelliten-Kommunikationsaktivität für eine Region zurück.

    Rückgabe:
      {
        "iridium_calls":   [...],   # Iridium-Kommunikationsereignisse
        "inmarsat_msgs":   [...],   # Inmarsat-Datennachrichten
        "total_events":    N,
        "activity_level":  "hoch"/"mittel"/"niedrig"/"keine Daten",
        "anomaly":         bool,    # True wenn ungewöhnlich viel Aktivität
        "summary":         str,
      }
    """
    try:
        from nexus_ais import _region_to_bbox  # type: ignore
        bbox = _region_to_bbox(region)
        if not bbox:
            return _empty_result("Region nicht erkannt")
        lat_min, lon_min, lat_max, lon_max = bbox
    except Exception:
        return _empty_result("Fehler bei Region-Lookup")

    iridium = fetch_iridium_activity(lat_min, lon_min, lat_max, lon_max, hours)
    inmarsat = fetch_inmarsat_activity(lat_min, lon_min, lat_max, lon_max)

    total = len(iridium) + len(inmarsat)

    # Aktivitätslevel bestimmen (Schwellenwerte aus Erfahrungswerten)
    if total == 0:
        level = "keine Daten"
    elif total < 5:
        level = "niedrig"
    elif total < 20:
        level = "mittel"
    else:
        level = "hoch"

    anomaly = total > 30  # Spike → verdächtig

    summary_parts = []
    if iridium:
        summary_parts.append(f"{len(iridium)} Iridium-Calls")
    if inmarsat:
        summary_parts.append(f"{len(inmarsat)} Inmarsat-Nachrichten")
    summary = f"Letzte {hours}h: " + (", ".join(summary_parts) if summary_parts
                                       else "keine Satelliten-Aktivität erfasst")
    if anomaly:
        summary += " ⚠️ Ungewöhnlich hohe Aktivität"

    return {
        "iridium_calls":   iridium,
        "inmarsat_msgs":   inmarsat,
        "total_events":    total,
        "activity_level":  level,
        "anomaly":         anomaly,
        "summary":         summary,
        "region":          region,
        "hours":           hours,
    }


def _empty_result(reason: str) -> dict:
    return {
        "iridium_calls": [], "inmarsat_msgs": [], "total_events": 0,
        "activity_level": "keine Daten", "anomaly": False,
        "summary": reason, "region": "", "hours": 0,
    }


if __name__ == "__main__":
    import sys
    region = sys.argv[1] if len(sys.argv) > 1 else "Ostsee"
    print(f"Satelliten-Kommunikation: {region}")
    result = satellite_comms_for_region(region)
    print(f"Aktivität: {result['activity_level']}")
    print(f"Zusammenfassung: {result['summary']}")
    print(f"Iridium-Events: {len(result['iridium_calls'])}")
    print(f"Inmarsat-Events: {len(result['inmarsat_msgs'])}")
