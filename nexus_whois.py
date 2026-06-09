"""
nexus_whois.py — Domain/IP-Attribution + Infrastruktur-Tracking
================================================================
Funktionen:
  - WHOIS-Abfragen (python-whois oder socket-Fallback)
  - DNS-Records (A, MX, NS, TXT) via dnspython oder socket
  - IP-Geolocation via ip-api.com (kostenlos, kein Key)
  - Hosting-Provider-Erkennung (Cloudflare, AWS, Rostelekom etc.)
  - Bekannte Desinformations-Infrastruktur-Fingerprinting
  - Subdomain-Enumeration (passiv via crt.sh)
  - Reverse-IP (Shodan-alternative: hackertarget.com)
  - LLM-Kontext-Formatter
"""

import sys
import json
import socket
import datetime
import urllib.request
import urllib.parse
import urllib.error
import re
import time
from typing import Optional

# ─── Optional imports ─────────────────────────────────────────────────────────

try:
    import whois as _whois_lib
    _WHOIS_OK = True
except ImportError:
    _WHOIS_OK = False

try:
    import dns.resolver as _dns_resolver
    import dns.exception
    _DNS_OK = True
except ImportError:
    _DNS_OK = False

# ─── Debug ────────────────────────────────────────────────────────────────────

def _dbg(msg: str) -> None:
    print(f"[WHOIS] {msg}", file=sys.stderr)

# ─── Known Infrastructure Fingerprints ───────────────────────────────────────

# Hosting-Provider → ASN/IP-Range-Patterns
_HOSTING_PATTERNS = {
    # CDN / Proxy
    "Cloudflare":       ["cloudflare", "AS13335", "104.16.", "172.67.", "162.158."],
    "Fastly":           ["fastly", "AS54113"],
    "Akamai":           ["akamai", "AS20940"],
    # Cloud
    "Amazon AWS":       ["amazon", "amazonaws", "AS14618", "AS16509"],
    "Google Cloud":     ["google", "AS15169", "AS396982"],
    "Microsoft Azure":  ["microsoft", "AS8075"],
    "Hetzner":          ["hetzner", "AS24940"],
    "OVH":              ["ovh", "AS16276"],
    # Russland
    "Rostelekom":       ["rostelekom", "AS12389", "AS8359"],
    "Rostelecom":       ["rostelecom"],
    "Mail.ru":          ["mail.ru", "AS47764"],
    "Yandex":           ["yandex", "AS13238"],
    "Selectel":         ["selectel", "AS49505"],
    # Belarus
    "Beltelecom":       ["beltelecom", "AS6697"],
    # Iran
    "IRINN":            ["irinn", "AS48159", "AS6736"],
    # China
    "Alibaba Cloud":    ["alibaba", "aliyun", "AS37963"],
    "Tencent":          ["tencent", "AS45090"],
    "ChinaNet":         ["chinanet", "AS4134"],
}

# Bekannte Desinformations-/Propaganda-Domains (öffentlich dokumentiert)
_DISINFO_DOMAINS = {
    # Russisch-staatlich (EU DisinfoLab, DFRLab)
    "sputniknews.com", "sputnik.md", "sputnik.ge",
    "rt.com", "rt.de", "rt.fr", "ruptly.tv",
    "tass.com", "tass.ru",
    "rianovosti.com", "ria.ru",
    "pravda.ru", "rg.ru",
    "vesti.ru", "1tv.ru",
    "anna-news.info",
    # Pro-russische Desinformationsnetzwerke (EU-gelistet)
    "inforos.ru", "newsffront.com", "antifashist.com",
    "svedomi.info", "alternativy.ru",
    # Bekannte Fake-News-Farmen
    "eu-reporter.co",    # fragwürdig
    "southfront.org",    # pro-russisch
    "globalresearch.ca", # Verschwörung/Disinfo
    "voltairenet.org",   # pro-Assad/RU
    "zerohedge.com",     # oft Disinfo-Amplifier
}

# TLD-Risikoklassifikation
_TLD_RISK = {
    # Hoch
    ".ru": "RU-staatlich (hohes Risiko)",
    ".by": "BY-staatlich (hohes Risiko)",
    ".ir": "IR-staatlich (hohes Risiko)",
    ".cn": "CN-staatlich (erhöhtes Risiko)",
    # Niedrig
    ".gov": "US-Regierung (vertrauenswürdig)",
    ".mil": "US-Militär (vertrauenswürdig)",
    ".edu": "Bildungseinrichtung",
    ".org": "Nonprofit (prüfen)",
    ".int": "Internationale Organisation",
}

# ─── WHOIS ────────────────────────────────────────────────────────────────────

def whois_lookup(domain: str) -> dict:
    """
    WHOIS-Abfrage für eine Domain.
    Returns: {registrar, registered, expires, updated, name_servers,
              registrant_country, privacy_protected, raw, error}
    """
    result = {
        "domain": domain,
        "registrar": None,
        "registered": None,
        "expires": None,
        "updated": None,
        "name_servers": [],
        "registrant_country": None,
        "privacy_protected": False,
        "raw": "",
        "error": None,
    }

    domain = domain.lower().strip().replace("https://", "").replace("http://", "").split("/")[0]

    if _WHOIS_OK:
        try:
            w = _whois_lib.whois(domain)
            result["registrar"] = str(w.registrar or "")[:100]
            result["registrant_country"] = str(w.country or "")[:10]
            result["name_servers"] = [str(ns).lower() for ns in (w.name_servers or [])][:6]

            # Dates
            def _fmt_date(d):
                if isinstance(d, list):
                    d = d[0]
                if isinstance(d, datetime.datetime):
                    return d.strftime("%Y-%m-%d")
                return str(d)[:10] if d else None

            result["registered"] = _fmt_date(w.creation_date)
            result["expires"] = _fmt_date(w.expiration_date)
            result["updated"] = _fmt_date(w.updated_date)

            # Privacy check
            raw_text = str(w.text or "").lower()
            result["raw"] = raw_text[:500]
            privacy_keywords = ["privacy", "redacted", "protected", "withheld",
                                 "whoisguard", "perfect privacy", "domains by proxy"]
            result["privacy_protected"] = any(kw in raw_text for kw in privacy_keywords)

        except Exception as e:
            result["error"] = f"whois-lib: {e}"
    else:
        # Socket-Fallback: roher WHOIS über Port 43
        try:
            tld = "." + domain.split(".")[-1]
            whois_server = _get_whois_server(tld)
            if whois_server:
                raw = _raw_whois(domain, whois_server)
                result["raw"] = raw[:500]
                # Minimal-Parsing
                for line in raw.split("\n"):
                    line = line.strip()
                    if ":" not in line:
                        continue
                    k, _, v = line.partition(":")
                    k, v = k.strip().lower(), v.strip()
                    if not v:
                        continue
                    if "registrar" in k and not result["registrar"]:
                        result["registrar"] = v[:100]
                    elif "creation" in k or "created" in k:
                        result["registered"] = v[:10]
                    elif "expir" in k:
                        result["expires"] = v[:10]
                    elif "name server" in k or "nameserver" in k:
                        result["name_servers"].append(v.lower().split()[0])
                    elif "country" in k:
                        result["registrant_country"] = v[:10]
                privacy_kw = ["privacy", "redacted", "protected", "withheld"]
                result["privacy_protected"] = any(kw in result["raw"].lower() for kw in privacy_kw)
            else:
                result["error"] = "Kein WHOIS-Server bekannt für diese TLD"
        except Exception as e:
            result["error"] = f"socket-whois: {e}"

    return result


def _get_whois_server(tld: str) -> Optional[str]:
    """Gibt WHOIS-Server für bekannte TLDs zurück."""
    servers = {
        ".com": "whois.verisign-grs.com",
        ".net": "whois.verisign-grs.com",
        ".org": "whois.pir.org",
        ".de":  "whois.denic.de",
        ".ru":  "whois.tcinet.ru",
        ".uk":  "whois.nic.uk",
        ".info": "whois.afilias.net",
        ".io":  "whois.nic.io",
        ".cn":  "whois.cnnic.cn",
        ".by":  "whois.cctld.by",
        ".ua":  "whois.ua",
        ".ir":  "whois.nic.ir",
    }
    return servers.get(tld)


def _raw_whois(domain: str, server: str, port: int = 43) -> str:
    """Rohe WHOIS-Abfrage über TCP."""
    try:
        s = socket.create_connection((server, port), timeout=10)
        s.sendall(f"{domain}\r\n".encode())
        chunks = []
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        s.close()
        return b"".join(chunks).decode("utf-8", errors="replace")
    except Exception as e:
        return f"[WHOIS-Fehler: {e}]"


# ─── DNS Records ──────────────────────────────────────────────────────────────

def dns_lookup(domain: str) -> dict:
    """
    DNS-Records abfragen: A, MX, NS, TXT.
    Returns: {a_records, mx_records, ns_records, txt_records, error}
    """
    result = {
        "domain": domain,
        "a_records": [],
        "mx_records": [],
        "ns_records": [],
        "txt_records": [],
        "error": None,
    }

    domain = domain.lower().strip().replace("https://", "").replace("http://", "").split("/")[0]

    if _DNS_OK:
        for rtype, key in [("A", "a_records"), ("MX", "mx_records"),
                            ("NS", "ns_records"), ("TXT", "txt_records")]:
            try:
                answers = _dns_resolver.resolve(domain, rtype, lifetime=5)
                for rdata in answers:
                    result[key].append(str(rdata)[:100])
            except Exception:
                pass
    else:
        # socket-Fallback: nur A-Record
        try:
            addrs = socket.getaddrinfo(domain, None)
            result["a_records"] = list({a[4][0] for a in addrs})[:5]
        except Exception as e:
            result["error"] = f"socket-dns: {e}"

    return result


# ─── IP Geolocation ───────────────────────────────────────────────────────────

def ip_geolocate(ip: str) -> dict:
    """
    IP-Geolocation via ip-api.com (kostenlos, kein Key, 45 req/min).
    Returns: {country, country_code, region, city, isp, org, as_number, lat, lon}
    """
    result = {
        "ip": ip,
        "country": None,
        "country_code": None,
        "region": None,
        "city": None,
        "isp": None,
        "org": None,
        "as_number": None,
        "lat": None,
        "lon": None,
        "error": None,
    }

    try:
        url = f"http://ip-api.com/json/{ip}?fields=status,country,countryCode,region,city,isp,org,as,lat,lon"
        req = urllib.request.Request(url, headers={"User-Agent": "NEXUS-OSINT/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        if data.get("status") == "success":
            result["country"] = data.get("country")
            result["country_code"] = data.get("countryCode")
            result["region"] = data.get("region")
            result["city"] = data.get("city")
            result["isp"] = data.get("isp")
            result["org"] = data.get("org")
            result["as_number"] = data.get("as")
            result["lat"] = data.get("lat")
            result["lon"] = data.get("lon")
        else:
            result["error"] = "ip-api: status != success"
    except Exception as e:
        result["error"] = str(e)

    return result


# ─── Hosting Provider Detection ───────────────────────────────────────────────

def detect_hosting(ip_geo: dict, dns_data: dict, whois_data: dict) -> dict:
    """
    Erkennt Hosting-Provider aus IP-Geo + DNS + WHOIS-Daten.
    Returns: {provider: str, risk_level: str, notes: list}
    """
    result = {
        "provider": "Unbekannt",
        "risk_level": "UNBEKANNT",
        "notes": [],
    }

    # Kombinierten Text aus allen Quellen bauen
    combined = " ".join([
        str(ip_geo.get("isp", "")),
        str(ip_geo.get("org", "")),
        str(ip_geo.get("as_number", "")),
        str(ip_geo.get("country_code", "")),
        str(whois_data.get("registrar", "")),
        " ".join(whois_data.get("name_servers", [])),
        str(whois_data.get("raw", "")),
    ]).lower()

    # Provider-Match
    for provider, patterns in _HOSTING_PATTERNS.items():
        if any(p.lower() in combined for p in patterns):
            result["provider"] = provider
            break

    # Risikobewertung
    country = (ip_geo.get("country_code") or "").upper()
    if country in ("RU", "BY"):
        result["risk_level"] = "HOCH"
        result["notes"].append(f"Hosting in {country} — erhöhtes Risiko")
    elif country in ("IR", "CN", "KP"):
        result["risk_level"] = "HOCH"
        result["notes"].append(f"Hosting in {country} — staatlich kontrolliertes Netz")
    elif country in ("US", "DE", "NL", "FR", "GB", "SE"):
        result["risk_level"] = "NIEDRIG"
    else:
        result["risk_level"] = "MITTEL"

    # Privacy protection = Verschleierung
    if whois_data.get("privacy_protected"):
        result["notes"].append("WHOIS-Daten verschleiert (Privacy Protection)")

    return result


# ─── Subdomain Enumeration (passiv via crt.sh) ────────────────────────────────

def enumerate_subdomains(domain: str, max_results: int = 20) -> list:
    """
    Passive Subdomain-Enumeration via crt.sh (Certificate Transparency Logs).
    Kein API-Key nötig.
    Returns: list of subdomain strings
    """
    domain = domain.lower().strip().replace("https://", "").replace("http://", "").split("/")[0]
    subdomains = set()

    try:
        url = f"https://crt.sh/?q=%.{domain}&output=json"
        req = urllib.request.Request(url, headers={"User-Agent": "NEXUS-OSINT/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        for entry in data:
            name = entry.get("name_value", "")
            for sub in name.split("\n"):
                sub = sub.strip().lstrip("*.")
                if sub.endswith(domain) and sub != domain:
                    subdomains.add(sub)

    except Exception as e:
        _dbg(f"crt.sh Fehler: {e}")

    return sorted(subdomains)[:max_results]


# ─── Disinfo Check ────────────────────────────────────────────────────────────

def check_disinfo(domain: str) -> dict:
    """
    Prüft ob eine Domain in bekannten Desinformations-Listen steht.
    Returns: {is_disinfo: bool, reason: str, tld_risk: str}
    """
    domain = domain.lower().strip().replace("https://", "").replace("http://", "").split("/")[0]
    # Remove www.
    if domain.startswith("www."):
        domain = domain[4:]

    result = {
        "domain": domain,
        "is_disinfo": False,
        "reason": "",
        "tld_risk": None,
    }

    # Direkter Match
    if domain in _DISINFO_DOMAINS:
        result["is_disinfo"] = True
        result["reason"] = "Bekannte Desinformationsquelle (EU DisinfoLab / DFRLab)"
        return result

    # Subdomain-Match
    for disinfo_domain in _DISINFO_DOMAINS:
        if domain.endswith("." + disinfo_domain):
            result["is_disinfo"] = True
            result["reason"] = f"Subdomain von bekannter Desinformationsquelle: {disinfo_domain}"
            return result

    # TLD-Risiko
    for tld, risk in _TLD_RISK.items():
        if domain.endswith(tld):
            result["tld_risk"] = risk
            break

    return result


# ─── Full Domain OSINT Pipeline ───────────────────────────────────────────────

def analyze_domain(domain: str, deep: bool = True) -> dict:
    """
    Vollständige Domain-OSINT-Analyse.

    Returns: {
        domain, whois, dns, ip_geo, hosting, disinfo,
        subdomains, summary, risk_score, flags
    }
    """
    # Normalize
    domain = domain.lower().strip()
    domain = re.sub(r'^https?://', '', domain)
    domain = domain.split("/")[0].split("?")[0]

    _dbg(f"Analysiere: {domain}")

    result = {
        "domain": domain,
        "whois": {},
        "dns": {},
        "ip_geo": {},
        "hosting": {},
        "disinfo": {},
        "subdomains": [],
        "summary": "",
        "risk_score": 0,
        "flags": [],
    }

    flags = []

    # 1. Disinfo-Check (instant)
    disinfo = check_disinfo(domain)
    result["disinfo"] = disinfo
    if disinfo["is_disinfo"]:
        flags.append(f"⚠️ DESINFORMATION: {disinfo['reason']}")
    if disinfo.get("tld_risk"):
        flags.append(f"TLD-Risiko: {disinfo['tld_risk']}")

    # 2. DNS
    dns = dns_lookup(domain)
    result["dns"] = dns

    # 3. IP-Geolocation (erste A-Record IP)
    ip_geo = {}
    if dns["a_records"]:
        first_ip = dns["a_records"][0].split()[0]  # MX hat Priority prefix
        # Für MX: nur IP-Teil
        first_ip = re.sub(r'^\d+\s+', '', first_ip)
        try:
            socket.inet_aton(first_ip)  # Validierung
            ip_geo = ip_geolocate(first_ip)
            result["ip_geo"] = ip_geo
        except Exception:
            pass

    # 4. WHOIS
    whois = whois_lookup(domain)
    result["whois"] = whois
    if whois.get("privacy_protected"):
        flags.append("WHOIS verschleiert")
    if whois.get("registrant_country"):
        flags.append(f"Registriert in: {whois['registrant_country']}")
    if whois.get("registered"):
        flags.append(f"Registriert: {whois['registered']}")

    # 5. Hosting
    hosting = detect_hosting(ip_geo, whois, whois)
    result["hosting"] = hosting
    if hosting["provider"] != "Unbekannt":
        flags.append(f"Hosting: {hosting['provider']}")
    for note in hosting.get("notes", []):
        flags.append(note)

    # 6. Subdomains (nur wenn deep=True)
    if deep:
        subs = enumerate_subdomains(domain, max_results=15)
        result["subdomains"] = subs
        if subs:
            flags.append(f"Subdomains gefunden: {len(subs)} (via crt.sh)")

    # 7. Risk Score
    score = 0
    if disinfo["is_disinfo"]:
        score += 50
    if hosting.get("risk_level") == "HOCH":
        score += 30
    elif hosting.get("risk_level") == "MITTEL":
        score += 10
    if whois.get("privacy_protected"):
        score += 10
    cc = (ip_geo.get("country_code") or whois.get("registrant_country") or "").upper()
    if cc in ("RU", "BY", "IR", "KP"):
        score += 20
    elif cc in ("CN",):
        score += 10
    result["risk_score"] = min(100, score)

    # Risk label
    if score >= 70:
        risk_label = "KRITISCH"
    elif score >= 40:
        risk_label = "HOCH"
    elif score >= 20:
        risk_label = "MITTEL"
    else:
        risk_label = "NIEDRIG"

    result["flags"] = flags
    result["risk_label"] = risk_label

    # 8. Summary
    lines = [f"Domain: {domain}"]
    lines.append(f"  Risiko: {risk_label} ({score}/100)")
    if ip_geo.get("country"):
        lines.append(f"  IP-Land: {ip_geo['country']} ({ip_geo.get('isp', '?')})")
    if hosting["provider"] != "Unbekannt":
        lines.append(f"  Hosting: {hosting['provider']}")
    for f in flags:
        lines.append(f"  • {f}")
    result["summary"] = "\n".join(lines)

    return result


def analyze_url(url: str) -> dict:
    """Convenience-Wrapper: URL → Domain-Analyse."""
    domain = re.sub(r'^https?://', '', url).split("/")[0].split("?")[0]
    return analyze_domain(domain)


# ─── Bulk Analysis (für Artikel-Feed) ────────────────────────────────────────

def analyze_article_sources(articles: list, max_domains: int = 10) -> dict:
    """
    Analysiert alle einzigartigen Domains aus einem Artikel-Feed.
    Returns: {domains: dict[domain -> analysis], high_risk: list, disinfo_found: list}
    """
    result = {
        "domains": {},
        "high_risk": [],
        "disinfo_found": [],
        "total_analyzed": 0,
    }

    # Domains extrahieren
    seen = set()
    for art in articles:
        url = art.get("url", "")
        if not url:
            continue
        domain = re.sub(r'^https?://', '', url).split("/")[0].split("?")[0].lower()
        if domain and domain not in seen:
            seen.add(domain)

    domains_to_check = list(seen)[:max_domains]

    for domain in domains_to_check:
        try:
            analysis = analyze_domain(domain, deep=False)
            result["domains"][domain] = analysis
            result["total_analyzed"] += 1

            if analysis["disinfo"]["is_disinfo"]:
                result["disinfo_found"].append(domain)
            if analysis.get("risk_label") in ("HOCH", "KRITISCH"):
                result["high_risk"].append(domain)

            time.sleep(0.5)  # Rate limiting für ip-api.com
        except Exception as e:
            _dbg(f"Fehler bei {domain}: {e}")

    return result


# ─── LLM Context Formatter ───────────────────────────────────────────────────

def whois_for_llm(analyses: list | dict) -> str:
    """
    Formatiert Domain-OSINT-Ergebnisse für LLM-Kontext.
    analyses: Liste von analyze_domain()-Ergebnissen ODER bulk-Result.
    """
    if not analyses:
        return ""

    lines = ["## Domain/Infrastruktur-OSINT", ""]

    # Bulk-Format
    if isinstance(analyses, dict) and "domains" in analyses:
        if analyses.get("disinfo_found"):
            lines.append(f"**⚠️ Desinformationsquellen erkannt:** {', '.join(analyses['disinfo_found'])}")
        if analyses.get("high_risk"):
            lines.append(f"**Hochrisiko-Domains:** {', '.join(analyses['high_risk'])}")
        lines.append(f"**Analysiert:** {analyses['total_analyzed']} Domains")
        for domain, a in list(analyses["domains"].items())[:5]:
            lines.append(f"\n**{domain}** — Risiko: {a.get('risk_label', '?')}")
            for flag in a.get("flags", [])[:4]:
                lines.append(f"  • {flag}")
        return "\n".join(lines)

    # Liste von einzelnen Analysen
    if isinstance(analyses, dict):
        analyses = [analyses]

    for a in analyses[:5]:
        lines.append(f"**{a.get('domain', '?')}** — Risiko: {a.get('risk_label', '?')} ({a.get('risk_score', 0)}/100)")
        for flag in a.get("flags", [])[:5]:
            lines.append(f"  • {flag}")
        lines.append("")

    return "\n".join(lines)


# ─── Self-Test ────────────────────────────────────────────────────────────────

def _self_test():
    print("=== nexus_whois.py Selbsttest ===")

    # Test 1: Disinfo-Check
    print("\n[1] Desinformations-Check")
    tests = [
        ("rt.com",          True),
        ("sputniknews.com",  True),
        ("reuters.com",      False),
        ("bellingcat.com",   False),
        ("southfront.org",   True),
    ]
    for domain, expected_disinfo in tests:
        r = check_disinfo(domain)
        status = "✓" if r["is_disinfo"] == expected_disinfo else "✗"
        print(f"  {status} {domain}: disinfo={r['is_disinfo']}  tld_risk={r.get('tld_risk','–')}")

    # Test 2: IP-Geolocation
    print("\n[2] IP-Geolocation")
    geo = ip_geolocate("8.8.8.8")
    if geo.get("country"):
        print(f"  8.8.8.8 → {geo['country']} / {geo['isp']} / AS: {geo['as_number']}")
    else:
        print(f"  Fehler: {geo.get('error')}")

    # Test 3: DNS
    print("\n[3] DNS-Abfrage")
    dns = dns_lookup("reuters.com")
    print(f"  reuters.com A-Records: {dns['a_records'][:2]}")
    print(f"  reuters.com NS: {dns['ns_records'][:2]}")

    # Test 4: Vollanalyse
    print("\n[4] Vollanalyse rt.com")
    analysis = analyze_domain("rt.com", deep=False)
    print(f"  Risiko: {analysis['risk_label']} ({analysis['risk_score']}/100)")
    for flag in analysis["flags"][:5]:
        print(f"  • {flag}")

    # Test 5: WHOIS-Lib Check
    print(f"\n[5] python-whois: {'verfügbar ✓' if _WHOIS_OK else 'nicht installiert (pip install python-whois)'}")
    print(f"    dnspython:    {'verfügbar ✓' if _DNS_OK else 'nicht installiert (pip install dnspython)'}")

    print("\n=== Selbsttest abgeschlossen ===")


if __name__ == "__main__":
    _self_test()
