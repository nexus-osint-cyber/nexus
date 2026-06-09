"""
NEXUS – Spire Maritime Satellite-AIS
Globale Schiffsabdeckung via Satelliten-AIS (auch Hormuz, Rotes Meer, Arktis).

Warum Satelliten-AIS?
  Landbasierte AIS-Empfänger reichen nur ~40-60km von der Küste.
  Spire Maritime betreibt eigene Satelliten die AIS-Signale aus dem Orbit
  empfangen → globale Abdeckung, auch auf hoher See.

Kostenloser Developer-Key:
  → https://developers.spire.com/maritime-package/
  → Sign Up → Dashboard → API Token kopieren
  In config.py eintragen: SPIRE_MARITIME_KEY = "dein-token"

Daten:
  - Schiffsposition (Lat/Lon)
  - MMSI, Name, Flagge, Schiffstyp
  - SOG (Speed over Ground), COG (Course over Ground)
  - Zeitstempel der letzten Satelliten-Erfassung
  - Quelle: "Spire-SatAIS" (klar gekennzeichnet)
"""
from __future__ import annotations

import requests
from datetime import datetime, timezone
from typing import Optional

REQUEST_TIMEOUT = 20
SPIRE_API_BASE  = "https://api.spire.com/v2/maritime"


def _get_key() -> str:
    try:
        import config  # type: ignore
        return getattr(config, "SPIRE_MARITIME_KEY", "")
    except ImportError:
        return ""


def fetch_spire_vessels(lat_min: float, lon_min: float,
                        lat_max: float, lon_max: float,
                        max_vessels: int = 60) -> list[dict]:
    """
    Holt Schiffspositionen aus dem Spire Maritime Satellite-AIS.
    Gibt leere Liste zurück wenn kein Key oder API nicht erreichbar.
    """
    key = _get_key()
    if not key:
        return []

    try:
        headers = {
            "Authorization": f"Bearer {key}",
            "Accept":        "application/json",
        }
        params = {
            "lat_min":  lat_min,
            "lat_max":  lat_max,
            "lon_min":  lon_min,
            "lon_max":  lon_max,
            "limit":    max_vessels,
        }
        r = requests.get(
            f"{SPIRE_API_BASE}/vessels",
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()

        vessels = []
        for v in (data.get("data") or data if isinstance(data, list) else []):
            try:
                pos = v.get("last_position") or v.get("position") or {}
                lat = float(pos.get("latitude")  or v.get("lat") or 0)
                lon = float(pos.get("longitude") or v.get("lon") or 0)
                if not lat or not lon:
                    continue
                vessels.append({
                    "lat":      round(lat, 5),
                    "lon":      round(lon, 5),
                    "mmsi":     str(v.get("mmsi") or ""),
                    "name":     (v.get("name") or v.get("vessel_name") or "?").strip(),
                    "type":     str(v.get("vessel_type") or v.get("type") or ""),
                    "flag":     str(v.get("flag") or v.get("flag_code") or ""),
                    "speed_kn": round(float(pos.get("speed") or v.get("sog") or 0), 1),
                    "heading":  pos.get("heading") or v.get("cog"),
                    "source":   "Spire-SatAIS",
                    "sat_ts":   pos.get("timestamp") or v.get("updated_at") or "",
                })
            except Exception:
                continue
        return vessels

    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            pass  # Key ungültig – still fail
        return []
    except Exception:
        return []


def spire_for_region(region: str) -> list[dict]:
    """Konvenienz-Wrapper: Region → Satellite-AIS-Schiffe."""
    try:
        from nexus_ais import _region_to_bbox  # type: ignore
        bbox = _region_to_bbox(region)
        if not bbox:
            return []
        return fetch_spire_vessels(*bbox)
    except Exception:
        return []


if __name__ == "__main__":
    import sys
    region = sys.argv[1] if len(sys.argv) > 1 else "Hormuz-Strasse"
    key = _get_key()
    if not key:
        print("❌ Kein SPIRE_MARITIME_KEY in config.py")
        print("   → https://developers.spire.com/maritime-package/")
        sys.exit(1)
    vessels = spire_for_region(region)
    print(f"Region: {region}")
    print(f"Satellite-AIS Schiffe: {len(vessels)}")
    for v in vessels[:15]:
        ts = v.get("sat_ts", "")[:16]
        print(f"  ⛴  {v['name']:22s}  {v['speed_kn']:5.1f}kn  "
              f"[{v['flag']}]  @ {v['lat']:.3f}N {v['lon']:.3f}E  {ts}")
