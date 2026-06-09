"""
Gibt die VPN-IP des Rechners zurueck (oder "localhost" falls kein VPN aktiv).
Unterstuetzt: Tailscale (100.64-127.x), WireGuard (10.x / 172.16-31.x), ZeroTier.

Reihenfolge der Praeferenz:
  1. NEXUS_HOST in config.py gesetzt  ->  diesen Wert direkt nutzen
  2. Tailscale-IP   (100.64.x.x - 100.127.x.x)
  3. WireGuard/VPN  (10.x.x.x, 172.16-31.x.x)
  4. Fallback:      0.0.0.0  (bindet auf ALLEN Interfaces - maximale Kompatibilitaet)
"""
import socket

# -- 1. config.py-Ueberschreibung pruefen ------------------------------------
try:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import config as _cfg
    _forced = str(getattr(_cfg, "NEXUS_HOST", "")).strip()
    if _forced and _forced not in ("localhost", "127.0.0.1"):
        print(_forced)
        raise SystemExit
except (ImportError, SystemExit):
    pass

# -- 2. Alle lokalen IPs sammeln ----------------------------------------------
try:
    all_ips = []
    for info in socket.getaddrinfo(socket.gethostname(), None):
        ip = info[4][0]
        if ":" in ip or ip.startswith("127.") or ip == "localhost":
            continue
        all_ips.append(ip)

    def _octet(ip, n):
        try: return int(ip.split(".")[n])
        except: return -1

    # Tailscale: 100.64.0.0/10
    tailscale = [ip for ip in all_ips
                 if ip.startswith("100.") and 64 <= _octet(ip, 1) <= 127]

    # WireGuard / OpenVPN: 10.x.x.x
    wg10 = [ip for ip in all_ips if ip.startswith("10.")]

    # ZeroTier / OpenVPN: 172.16-31.x.x
    wg172 = [ip for ip in all_ips
             if ip.startswith("172.") and 16 <= _octet(ip, 1) <= 31]

    vpn_ip = (
        tailscale[0] if tailscale else
        wg10[0]      if wg10      else
        wg172[0]     if wg172     else
        None
    )

    # VPN gefunden -> auf dieser IP binden (sicherer als 0.0.0.0)
    # Kein VPN -> 0.0.0.0 damit Handy im selben WLAN auch drauf kommt
    print(vpn_ip if vpn_ip else "0.0.0.0")

except Exception:
    print("0.0.0.0")
