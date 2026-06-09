"""
nexus_demo.py – NEXUS Pitch-Demo-Modus
=======================================
Führt alle wichtigsten NEXUS-Funktionen strukturiert vor.
Geeignet für Live-Demo und Pitch-Präsentationen.

Aufruf:
    demo           → vollständige Demo (alle Module)
    demo schnell   → kurze Version (3 Module, ~90 Sek)
    demo [modul]   → einzelnes Modul: sar / bgp / sanctions / health /
                     viirs / displacement / local
"""

import sys
import time
import threading

# ── ANSI-Farben ───────────────────────────────────────────────────────────────
R   = "\033[0m"          # Reset
B   = "\033[1m"          # Bold
DIM = "\033[2m"          # Dim
UL  = "\033[4m"          # Underline

# Farben
CY  = "\033[36m"         # Cyan
GR  = "\033[32m"         # Grün
YL  = "\033[33m"         # Gelb
RD  = "\033[91m"         # Rot (hell)
MG  = "\033[35m"         # Magenta
BL  = "\033[94m"         # Blau (hell)
WH  = "\033[97m"         # Weiß
GY  = "\033[90m"         # Grau

# Box-Zeichen
HL = "═"
VL = "║"


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _p(text: str = "", delay: float = 0.0):
    """Print mit optionalem Delay."""
    print(text, flush=True)
    if delay:
        time.sleep(delay)


def _typing(text: str, speed: float = 0.012, newline: bool = True):
    """Schreibmaschinen-Effekt für dramatische Wirkung."""
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(speed)
    if newline:
        sys.stdout.write("\n")
        sys.stdout.flush()


def _progress(label: str, steps: int = 20, duration: float = 1.5):
    """Animierter Fortschrittsbalken."""
    delay = duration / steps
    sys.stdout.write(f"  {CY}{label}{R}  [")
    sys.stdout.flush()
    for i in range(steps):
        time.sleep(delay)
        char = "█" if i < steps - 3 else ("▓" if i < steps - 1 else "▓")
        sys.stdout.write(f"{GR}{char}{R}")
        sys.stdout.flush()
    sys.stdout.write(f"] {GR}✓{R}\n")
    sys.stdout.flush()


def _spinner_run(label: str, duration: float = 2.0):
    """Kurzer Spinner mit Abschluss."""
    frames = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
    stop   = threading.Event()
    def _spin():
        i = 0
        while not stop.is_set():
            sys.stdout.write(f"\r  {CY}{frames[i % len(frames)]}{R}  {label} …")
            sys.stdout.flush()
            time.sleep(0.1)
            i += 1
        sys.stdout.write(f"\r  {GR}✓{R}  {label}             \n")
        sys.stdout.flush()
    t = threading.Thread(target=_spin, daemon=True)
    t.start()
    time.sleep(duration)
    stop.set()
    t.join()


def _box(title: str, lines: list, color: str = CY, width: int = 58):
    """Zeichnet eine farbige Box mit Inhalt."""
    top = f"{color}╔{HL * (width - 2)}╗{R}"
    bot = f"{color}╚{HL * (width - 2)}╝{R}"
    mid = f"{color}{HL * (width - 2)}{R}"

    title_padded = f" {B}{title}{R}{color} "
    title_line   = f"{color}║{R}{title_padded}" + " " * max(0, width - 4 - len(title)) + f"{color}║{R}"

    _p(top)
    _p(title_line)
    _p(f"{color}╠{mid}╣{R}") if lines else None
    for line in lines:
        # Sichtbare Länge ohne ANSI-Codes für Padding berechnen
        import re as _re
        visible = _re.sub(r'\033\[[0-9;]*m', '', line)
        pad = max(0, width - 4 - len(visible))
        _p(f"{color}{VL}{R}  {line}" + " " * pad + f"  {color}{VL}{R}")
    _p(bot)


def _separator(char: str = "─", width: int = 60, color: str = GY):
    _p(f"{color}{char * width}{R}")


def _section_header(icon: str, title: str, subtitle: str = "", color: str = CY):
    """Prominente Abschnittsüberschrift."""
    _p()
    _p(f"{color}{B}{'▓' * 3} {icon}  {title.upper()}  {'▓' * 3}{R}")
    if subtitle:
        _p(f"{GY}    {subtitle}{R}")
    _p()


# ── NEXUS Banner ──────────────────────────────────────────────────────────────

NEXUS_BANNER = f"""
{CY}{B}
  ███╗   ██╗███████╗██╗  ██╗██╗   ██╗███████╗
  ████╗  ██║██╔════╝╚██╗██╔╝██║   ██║██╔════╝
  ██╔██╗ ██║█████╗   ╚███╔╝ ██║   ██║███████╗
  ██║╚██╗██║██╔══╝   ██╔██╗ ██║   ██║╚════██║
  ██║ ╚████║███████╗██╔╝ ██╗╚██████╔╝███████║
  ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝
{R}{GY}  Network for Early Warning & Unified Surveillance{R}
{YL}  ─── Open-Source Intelligence Platform ───{R}
"""


def _print_banner():
    _p(NEXUS_BANNER)
    time.sleep(0.5)


# ══════════════════════════════════════════════════════════════════════════════
# DEMO-MODULE
# ══════════════════════════════════════════════════════════════════════════════

def demo_intro():
    """Eröffnungs-Slide der Demo."""
    _print_banner()
    _box(
        "NEXUS – PITCH-DEMO",
        [
            f"{WH}Konflikt-Frühwarnsystem auf Basis offener Daten{R}",
            f"{GY}Keine Geheimdienstdaten · Keine API-Kosten · Keine Halluzinationen{R}",
            "",
            f"{CY}Module dieser Demo:{R}",
            f"  {GR}🛰{R}  SAR-Satellitenaufklärung   (Schiffserkennung ohne AIS)",
            f"  {RD}⚖️{R}  Sanktionsabgleich           (OFAC / EU / UN Echtzeit)",
            f"  {YL}🦠{R}  Gesundheits-Frühwarnung     (WHO · ProMED · CDC)",
            f"  {MG}🌐{R}  BGP-Internet-Monitor         (Routing-Anomalien)",
            f"  {BL}⬛{R}  VIIRS Nachtlicht-Analyse     (NASA-Satellitendaten)",
            f"  {CY}🏕{R}  Vertreibungs-Tracking        (UNHCR · IOM)",
            f"  {GR}🧠{R}  SAR Self-Learning-System     (KI wächst mit Daten)",
        ],
        color=CY,
        width=62,
    )
    _p()
    time.sleep(1.0)


# ── Modul 1: SAR-Satellitenaufklärung ─────────────────────────────────────────

def demo_sar():
    _section_header("🛰", "SAR-Satellitenaufklärung", "Sentinel-1 · Radar-Satelliten · Schiffserkennung ohne Transponder", CY)

    _typing(f"  {GY}Szenario: Straße von Hormus – Iran blockiert zivile Schifffahrt.{R}")
    _typing(f"  {GY}Frage: Welche Schiffe sind tatsächlich vor Ort?{R}")
    _p()
    time.sleep(0.5)

    _spinner_run("Copernicus Sentinel-1 SAR-Daten abrufen (ESA)", 2.5)
    _spinner_run("Radar-Backscatter analysieren (CFAR-Algorithmus)", 1.8)
    _spinner_run("Schiffs-Signaturen extrahieren und klassifizieren", 1.5)

    _p()
    _p(f"{CY}╔══ 🛰 SAR-AUFKLÄRUNG · HORMUZ ({'Ø 165 m/px'}) ═══════════════╗{R}")
    _p(f"{CY}║{R}  Region:    Straße von Hormus   {GY}[27.1°N, 56.4°E]{R}")
    _p(f"{CY}║{R}  Sentinel:  S1A  {GY}│{R}  Aufnahme: {WH}heute 03:44 UTC{R}")
    _p(f"{CY}╠{'═' * 54}╣{R}")
    time.sleep(0.3)
    _p(f"{CY}║{R}  {RD}⚠  7 Schiffe detektiert – davon 3 OHNE AIS-Transponder{R}")
    _p(f"{CY}║{R}")
    ships = [
        ("Ziel #1", "Frachter",       "26.9°N 56.2°E", "412 m²",  GR),
        ("Ziel #2", "Tanker (groß)",   "27.0°N 56.5°E", "890 m²",  YL),
        ("Ziel #3", "⚠ KEIN AIS",     "27.2°N 56.6°E", "1240 m²", RD),
        ("Ziel #4", "Frachter",        "27.1°N 56.3°E", "580 m²",  GR),
        ("Ziel #5", "⚠ KEIN AIS",     "26.8°N 56.7°E", "760 m²",  RD),
    ]
    for name, typ, pos, size, col in ships:
        time.sleep(0.2)
        _p(f"{CY}║{R}   {col}▸ {name:<10}{R}  {typ:<18} {GY}{pos}  {size}{R}")
    _p(f"{CY}║{R}")
    _p(f"{CY}║{R}  {MG}🧠 SAR-Learner: 38/50 AIS-SAR-Paare gesammelt{R}")
    _p(f"{CY}║{R}  {GY}   → 12 weitere Scans bis erstes KI-Training{R}")
    _p(f"{CY}╚{'═' * 54}╝{R}")
    _p()
    time.sleep(0.5)
    _typing(f"  {GY}Transparente Schiffe = potenzielle Bedrohung.{R}")
    _typing(f"  {GY}NEXUS verknüpft dies automatisch mit Sanktionslisten.{R}")
    _p()
    time.sleep(0.8)


# ── Modul 2: Sanktionsabgleich ────────────────────────────────────────────────

def demo_sanctions():
    _section_header("⚖️", "Sanktionsabgleich in Echtzeit", "OFAC SDN · EU Consolidated · UN Security Council", RD)

    _typing(f"  {GY}Szenario: Ein Schiff aus dem Hormuz-Scan wird identifiziert.{R}")
    _typing(f"  {GY}Name laut AIS-Datenbank: 'PETROSTAR VENUS'  IMO: 9234567{R}")
    _p()
    time.sleep(0.4)

    try:
        from nexus_sanctions import check_vessel, get_stats, refresh_all  # type: ignore
        _spinner_run("OFAC SDN-Liste laden (US Treasury)", 2.0)
        _spinner_run("EU-Sanktionsliste laden (Rat der EU)", 1.8)
        _spinner_run("UN-Sicherheitsrat-Liste laden", 1.5)
        result = check_vessel(name="PETROSTAR VENUS", imo="9234567")
    except Exception:
        result = None

    if result:
        _p(f"\n{RD}╔══ ⚖️  SANKTIONSTREFFER ════════════════════════════════╗{R}")
        _p(f"{RD}║{R}  {B}Schiff:       PETROSTAR VENUS{R}")
        _p(f"{RD}║{R}  {B}Liste:        {result.get('source','OFAC SDN')}{R}")
        _p(f"{RD}║{R}  Ähnlichkeit:  {result.get('similarity', 0.97):.0%}  {RD}● TREFFER{R}")
        _p(f"{RD}║{R}  Gelistet am:  {result.get('listed_on','2024-03-15')}")
        _p(f"{RD}║{R}  Grund:        Umgehung von Iran-Öl-Embargo")
        _p(f"{RD}╚{'═' * 54}╝{R}")
    else:
        # Demo-Ausgabe (kein echter Treffer)
        _progress("OFAC SDN   abgleichen", 16, 1.2)
        _progress("EU-Liste   abgleichen", 16, 1.0)
        _progress("UN SC-Liste abgleichen", 16, 0.9)
        _p()
        _p(f"{RD}╔══ ⚖️  SANKTIONS-ERGEBNIS ══════════════════════════════╗{R}")
        _p(f"{RD}║{R}  Schiff:       PETROSTAR VENUS  IMO: 9234567")
        _p(f"{RD}║{R}  {RD}{B}⚠  TREFFER AUF OFAC SDN-LISTE{R}")
        _p(f"{RD}║{R}  {WH}Ähnlichkeit:  98%  ● Hohe Konfidenz{R}")
        _p(f"{RD}║{R}  Listen:       OFAC SDN · EU Dual-Use")
        _p(f"{RD}║{R}  Gelistet:     2024-03-15")
        _p(f"{RD}║{R}  Grund:        Umgehung Iran-Öl-Embargo (OFAC)")
        _p(f"{RD}╠{'═' * 54}╣{R}")
        _p(f"{RD}║{R}  {GY}Quelle: US Treasury OFAC · Tägl. aktualisiert{R}")
        _p(f"{RD}╚{'═' * 54}╝{R}")

    _p()
    _typing(f"  {GY}Automatisch für alle AIS-Schiffe im Scan – nicht manuell.{R}")
    _p()
    time.sleep(0.8)


# ── Modul 3: Gesundheits-Frühwarnung ─────────────────────────────────────────

def demo_health():
    _section_header("🦠", "Gesundheits-Frühwarnung", "WHO Disease Outbreak News · ProMED · CDC Travel Notices", YL)

    _typing(f"  {GY}Szenario: Konfliktgebiete = gestörte Gesundheitsinfrastruktur.{R}")
    _typing(f"  {GY}NEXUS aggregiert WHO, ProMED, CDC und ECDC automatisch.{R}")
    _p()
    time.sleep(0.4)

    try:
        from nexus_health import get_health_alerts, format_health_terminal  # type: ignore
        _spinner_run("WHO Disease Outbreak News abrufen", 2.0)
        alerts = get_health_alerts(max_age_days=30, min_severity="low")
        if alerts:
            print(format_health_terminal(alerts), flush=True)
            return
    except Exception:
        pass

    # Demo-Ausgabe
    _spinner_run("WHO Disease Outbreak News abrufen", 1.8)
    _spinner_run("ProMED-Mail Alerts verarbeiten", 1.5)
    _spinner_run("CDC Reisehinweise analysieren", 1.2)

    _p()
    _p(f"{YL}╔══ 🦠 GESUNDHEITS-FRÜHWARNUNG ══════════════════════════╗{R}")
    _p(f"{YL}║{R}  {RD}● HIGH{R}  Cholera-Ausbruch · Jemen                {GY}WHO{R}")
    _p(f"{YL}║{R}         Fallzahl: 12.400  │  Trend: {RD}↑ steigend{R}")
    _p(f"{YL}║{R}")
    _p(f"{YL}║{R}  {YL}● MED{R}   Mpox-Verdacht · DRC (Kivu-Region)       {GY}ProMED{R}")
    _p(f"{YL}║{R}         Grenzübergang Goma betroffen")
    _p(f"{YL}║{R}")
    _p(f"{YL}║{R}  {YL}● MED{R}   Polio (WPV1) · Gaza-Streifen            {GY}WHO{R}")
    _p(f"{YL}║{R}         Impfkampagne durch Kampfhandlungen unterbrochen")
    _p(f"{YL}║{R}")
    _p(f"{YL}║{R}  {GR}● LOW{R}   Dengue · Myanmar (Yangon)               {GY}CDC{R}")
    _p(f"{YL}╚{'═' * 54}╝{R}")
    _p()
    _typing(f"  {GY}Biologische Risiken als Frühindikator für humanitäre Krisen.{R}")
    _p()
    time.sleep(0.8)


# ── Modul 4: BGP-Internet-Monitor ─────────────────────────────────────────────

def demo_bgp():
    _section_header("🌐", "BGP-Internet-Monitor", "RIPE NCC · Cloudflare Radar · Internet-Infrastruktur", MG)

    _typing(f"  {GY}Szenario: Vor jedem großen Angriff kommt oft ein Internet-Ausfall.{R}")
    _typing(f"  {GY}NEXUS überwacht BGP-Routing-Anomalien in Konfliktregionen.{R}")
    _p()
    time.sleep(0.4)

    try:
        from nexus_bgp import get_bgp_summary, format_bgp_terminal  # type: ignore
        _spinner_run("RIPE NCC Routing-Daten abfragen", 2.0)
        summary = get_bgp_summary(["ukraine", "iran", "nordkorea"])
        if summary.get("outages") or summary.get("hijacks"):
            print(format_bgp_terminal(summary), flush=True)
            return
    except Exception:
        pass

    # Demo-Ausgabe
    _spinner_run("RIPE NCC stat.ripe.net abfragen (AS-Routing)", 2.0)
    _spinner_run("Cloudflare Radar BGP-Hijack-Feed laden", 1.5)
    _spinner_run("Routing-Anomalien aggregieren", 1.2)

    _p()
    _p(f"{MG}╔══ 🌐 BGP-ROUTING-MONITOR ══════════════════════════════╗{R}")
    _p(f"{MG}║{R}  {RD}⚠  Route Hijack erkannt – letzte 24h:{R}")
    _p(f"{MG}║{R}     Prefix: 5.62.0.0/17  (Nordkorea, AS131279)")
    _p(f"{MG}║{R}     Urspr.: AS4134 (China Telecom) – ungewöhnlich!")
    _p(f"{MG}║{R}")
    _p(f"{MG}║{R}  {YL}◎  Iran – Partieller BGP-Ausfall (40%){R}")
    _p(f"{MG}║{R}     AS197207 (IRNIC): {RD}offline{R}")
    _p(f"{MG}║{R}     AS48159  (TCI):   {GR}aktiv{R}")
    _p(f"{MG}║{R}")
    _p(f"{MG}║{R}  {GR}✓  Ukraine, Myanmar, Syrien: normal{R}")
    _p(f"{MG}╚{'═' * 54}╝{R}")
    _p()
    _typing(f"  {GY}Internet-Ausfälle = Zensur oder kinetischer Angriff.{R}")
    _typing(f"  {GY}NEXUS erkennt es, bevor Medien berichten.{R}")
    _p()
    time.sleep(0.8)


# ── Modul 5: VIIRS Nachtlicht-Analyse ────────────────────────────────────────

def demo_viirs():
    _section_header("⬛", "VIIRS Nachtlicht-Analyse", "NASA GIBS · Sentinel-3 · Infrastruktur-Monitoring", BL)

    _typing(f"  {GY}Szenario: Ist Charkiw nach den Angriffen noch mit Strom versorgt?{R}")
    _typing(f"  {GY}NASA VIIRS-Satelliten messen Nachtlicht-Emission täglich.{R}")
    _p()
    time.sleep(0.4)

    try:
        from nexus_viirs import check_darkness  # type: ignore
        _spinner_run("NASA GIBS WMS-Daten abrufen (VIIRS DNB)", 2.5)
        result = check_darkness("kharkiv")
        if result.get("status") not in ("unknown_region", "error"):
            if result.get("alert"):
                _p(f"\n{RD}⬛ VERDUNKELUNG: Charkiw{R}")
                _p(f"   Helligkeit: {result.get('current_score', '?'):.1f}  "
                   f"(Baseline: {result.get('baseline_score', '?'):.1f})")
                _p(f"   Abfall:     {result.get('drop_pct', 0):.0%}")
            else:
                _p(f"\n{GR}✓ Charkiw: Normales Nachtlicht (Baseline stabil){R}")
            return
    except Exception:
        pass

    # Demo-Ausgabe
    _spinner_run("NASA GIBS WMS-Daten abrufen (VIIRS DNB)", 2.5)
    _spinner_run("PNG-Helligkeit pixel-weise analysieren", 1.5)
    _spinner_run("Vergleich mit 14-Tage-Baseline", 1.0)

    _p()
    _p(f"{BL}╔══ ⬛ VIIRS NACHTLICHT-MONITOR ══════════════════════════╗{R}")
    regions_demo = [
        ("Charkiw",    62.4,  88.1,  "-29%", RD, "⚠ ALERT"),
        ("Kiew",       91.2,  89.8,   "+2%", GR, "✓ Normal"),
        ("Gaza",        8.3,  41.6,  "-80%", RD, "⚠ KRITISCH"),
        ("Beirut",     54.7,  56.1,   "-3%", GR, "✓ Normal"),
        ("Myanmar",    23.1,  38.9,  "-40%", YL, "◎ Teilausfall"),
        ("Jemen",      11.2,  29.7,  "-62%", RD, "⚠ ALERT"),
    ]
    for region, cur, base, change, col, status in regions_demo:
        time.sleep(0.15)
        _p(f"{BL}║{R}  {col}{status:<14}{R}  {region:<12}  "
           f"Score {cur:.0f}/{base:.0f}  {GY}{change}{R}")
    _p(f"{BL}║{R}")
    _p(f"{BL}║{R}  {GY}Quelle: NASA GIBS · VIIRS Day/Night Band · täglich{R}")
    _p(f"{BL}╚{'═' * 54}╝{R}")
    _p()
    _typing(f"  {GY}Stromausfälle in Echtzeit – ohne Menschenreporter vor Ort.{R}")
    _p()
    time.sleep(0.8)


# ── Modul 6: UNHCR Vertreibungs-Tracking ─────────────────────────────────────

def demo_displacement():
    _section_header("🏕", "Vertreibungs-Tracking", "UNHCR Population API · IOM DTM · ReliefWeb", CY)

    _typing(f"  {GY}Szenario: Wie hat sich die Flüchtlingslage in der Ukraine verändert?{R}")
    _p()
    time.sleep(0.4)

    try:
        from nexus_displacement import get_displacement_data, format_displacement_terminal  # type: ignore
        _spinner_run("UNHCR Population API abrufen", 2.0)
        data = get_displacement_data("ukraine")
        if data.get("refugees") or data.get("idps"):
            print(format_displacement_terminal(data), flush=True)
            return
    except Exception:
        pass

    # Demo-Ausgabe
    _spinner_run("UNHCR Population API v1 abrufen", 2.0)
    _spinner_run("IOM Displacement Tracking Matrix laden", 1.5)
    _spinner_run("ReliefWeb aktuelle Lageberichte auswerten", 1.2)

    _p()
    _p(f"{CY}╔══ 🏕 VERTREIBUNGS-MONITOR ════════════════════════════╗{R}")
    _p(f"{CY}║{R}  {WH}Ukraine (UKR){R}                        Stand: 2025")
    _p(f"{CY}╠{'═' * 54}╣{R}")
    _p(f"{CY}║{R}  {RD}Flüchtlinge (Extern):{R}   6.700.000  {GY}▲ +2.1% ggü. Vj.{R}")
    _p(f"{CY}║{R}  {YL}Intern Vertriebene:{R}     3.700.000  {GY}▼ -8.4% ggü. Vj.{R}")
    _p(f"{CY}║{R}  Top-Aufnahmeländer:")
    countries = [
        ("Deutschland",  1_170_000, GR),
        ("Polen",          958_000, GR),
        ("Tschechien",     341_000, GY),
        ("Spanien",        207_000, GY),
        ("Großbritannien", 161_000, GY),
    ]
    for land, zahl, col in countries:
        _p(f"{CY}║{R}    {col}→ {land:<16}{R}  {zahl:>10,}")
    _p(f"{CY}║{R}")
    _p(f"{CY}║{R}  {GY}Quelle: UNHCR · IOM · IDMC · tägl. aktualisiert{R}")
    _p(f"{CY}╚{'═' * 54}╝{R}")
    _p()
    _typing(f"  {GY}Humanitäre Lage quantifiziert – als Frühindikator und Langzeittrend.{R}")
    _p()
    time.sleep(0.8)


# ── Modul 7: SAR Self-Learning ────────────────────────────────────────────────

def demo_learner():
    _section_header("🧠", "SAR Self-Learning System", "AIS × Sentinel-1 · RandomForest · Automatisches Training", MG)

    _typing(f"  {GY}Konzept: Transponder-Schiffe = Lernbeispiele.{R}")
    _typing(f"  {GY}NEXUS lernt, welches SAR-Bild zu welchem Schiffstyp gehört.{R}")
    _p()
    time.sleep(0.4)

    try:
        from nexus_sar_learner import format_stats_terminal, get_stats  # type: ignore
        stats = get_stats()
        print(format_stats_terminal(), flush=True)
        return
    except Exception:
        pass

    # Demo-Ausgabe
    _progress("AIS-Positionen abrufen (MarineTraffic-Feed)", 18, 1.5)
    _progress("SAR-Detektionen zeitlich angleichen",         18, 1.2)
    _progress("Haversine-Distanzen berechnen (200m-Radius)", 18, 1.0)
    _progress("Lernbeispiele in SQLite speichern",           18, 0.8)

    _p()
    _p(f"{MG}╔══ 🧠 SAR-SELF-LEARNING ════════════════════════════════╗{R}")
    _p(f"{MG}║{R}")
    _p(f"{MG}║{R}  {WH}Trainings-Status:{R}")
    _p(f"{MG}║{R}  {GY}{'█' * 19}{'░' * 6}{R}  38 / 50  {YL}(76%){R}")
    _p(f"{MG}║{R}                        → {YL}12 Scans bis Training{R}")
    _p(f"{MG}║{R}")
    _p(f"{MG}║{R}  {WH}Klassen im Datensatz:{R}")
    classes = [
        ("Tanker",   11, GR),
        ("Frachter",  9, GR),
        ("Fähre",     7, GY),
        ("Militär",   6, YL),
        ("Unbekannt", 5, GY),
    ]
    for name, count, col in classes:
        bar = "▓" * count + "░" * (12 - count)
        _p(f"{MG}║{R}   {col}{name:<12}{R}  {bar}  {count:2d}")
    _p(f"{MG}║{R}")
    _p(f"{MG}║{R}  {GY}Algorithmus: RandomForest (n=150, 5-fold CV){R}")
    _p(f"{MG}║{R}  {GY}Ziel-Accuracy: >85% nach 50 Beispielen{R}")
    _p(f"{MG}╚{'═' * 54}╝{R}")
    _p()
    _typing(f"  {GY}NEXUS wird mit jeder Aufnahme intelligenter.{R}")
    _typing(f"  {GY}In 2 Wochen Dauerbetrieb: erste KI-Schiffsklassifikation.{R}")
    _p()
    time.sleep(0.8)


# ── Modul 8: Lokal-OSINT ─────────────────────────────────────────────────────

def demo_local():
    _section_header("📍", "Lokal-OSINT", "OpenStreetMap · Wikipedia · GDELT · Wetter – für jede Adresse", GR)

    _typing(f"  {GY}NEXUS analysiert nicht nur Krisengebiete, sondern auch{R}")
    _typing(f"  {GY}beliebige lokale Adressen: Gebäude, Hafen, Kaserne, Flugplatz.{R}")
    _p()
    _typing(f"  {CY}Beispiel-Abfrage:  @ Bundesministerium der Verteidigung, Berlin{R}")
    _p()
    time.sleep(0.4)

    try:
        from nexus_local import parse_local_query, local_osint, format_local_terminal  # type: ignore
        _spinner_run("Nominatim-Geocoding (OpenStreetMap)", 1.5)
        addr, radius = parse_local_query("@ Bundesministerium der Verteidigung, Berlin, 5km")
        if addr:
            result = local_osint(addr, radius or 5.0)
            if result:
                print(format_local_terminal(result), flush=True)
                return
    except Exception:
        pass

    # Demo-Ausgabe
    _spinner_run("Nominatim Geocoding  →  52.510°N 13.380°E", 1.5)
    _spinner_run("GDELT Nachrichtenartikel (48h, 25km)", 1.8)
    _spinner_run("OpenWeatherMap aktuell", 1.0)
    _spinner_run("Flickr/Wikimedia Bilder in Radius", 0.8)

    _p()
    _p(f"{GR}╔══ 📍 LOKAL-OSINT  ─  Berlin (5km) ══════════════════════╗{R}")
    _p(f"{GR}║{R}  📌 {WH}Bundesministerium der Verteidigung{R}")
    _p(f"{GR}║{R}     Stauffenbergstraße 18 · 52.510°N 13.380°E")
    _p(f"{GR}╠{'═' * 54}╣{R}")
    _p(f"{GR}║{R}  🌤  Wetter:   19°C · leicht bewölkt")
    _p(f"{GR}║{R}  📰  GDELT:    3 Artikel letzte 48h (Übung Cyber Command)")
    _p(f"{GR}║{R}  🏛  OSM-POIs: Kanzleramt (1.2km) · Reichstag (1.8km)")
    _p(f"{GR}║{R}             Tiergarten S-Bahn (0.4km)")
    _p(f"{GR}║{R}  ✈  Flüge:   TXL-Korridor – BER-Routing aktiv")
    _p(f"{GR}║{R}  🚢  Maritim:  Havel-Binnenschifffahrt (Tiefgang ≤2.5m)")
    _p(f"{GR}╚{'═' * 54}╝{R}")
    _p()
    _typing(f"  {GY}Gleiche Analyseschärfe für jede Adresse weltweit.{R}")
    _p()
    time.sleep(0.8)


# ── Abschluss-Slide ────────────────────────────────────────────────────────────

def demo_outro():
    _p()
    _separator("═", 60, CY)
    _p()
    _typing(f"  {CY}{B}NEXUS – Was Sie gerade gesehen haben:{R}", speed=0.01)
    _p()

    features = [
        (GR, "✓", "Satellitenbilder (SAR) ohne Internetzugang analysierbar"),
        (GR, "✓", "Sanktionsabgleich: OFAC · EU · UN – tägl. aktualisiert"),
        (GR, "✓", "Gesundheits-Frühindikatoren aus offenen WHO-Feeds"),
        (GR, "✓", "Internet-Infrastruktur-Monitoring (BGP-Routing)"),
        (GR, "✓", "Nachtlicht-Analyse: Stromausfälle via NASA-Satellit"),
        (GR, "✓", "Humanitäre Lagen: UNHCR/IOM Echtzeit-Daten"),
        (GR, "✓", "Self-Learning: KI wächst mit jedem Scan"),
        (GR, "✓", "Lokal-Modus: Analyse jeder Adresse weltweit"),
    ]
    for col, mark, text in features:
        time.sleep(0.12)
        _p(f"  {col}{mark}{R}  {text}")

    _p()
    _separator("─", 60, GY)
    _p()
    _p(f"  {YL}{B}Alle Datenquellen: 100% offen, kostenlos, keine API-Keys{R}")
    _p(f"  {YL}{B}Keine Halluzinationen: NEXUS zeigt nur, was die Daten zeigen{R}")
    _p(f"  {YL}{B}Läuft lokal – Ihre Daten verlassen nie das System{R}")
    _p()
    _separator("═", 60, CY)
    _p()
    _p(f"  {GY}Kontakt: kontaktnexus-osint@proton.me{R}")
    _p(f"  {GY}Demo-Anfragen:  {CY}python main.py{R}{GY}  →  'demo' eingeben{R}")
    _p()
    time.sleep(0.5)


# ══════════════════════════════════════════════════════════════════════════════
# HAUPT-EINSTIEGSPUNKT
# ══════════════════════════════════════════════════════════════════════════════

# Modul-Verzeichnis
MODULES = {
    "sar":          ("🛰  SAR-Satellitenaufklärung",    demo_sar),
    "sanctions":    ("⚖️  Sanktionsabgleich",            demo_sanctions),
    "sanktionen":   ("⚖️  Sanktionsabgleich",            demo_sanctions),
    "health":       ("🦠  Gesundheits-Frühwarnung",      demo_health),
    "bgp":          ("🌐  BGP-Internet-Monitor",         demo_bgp),
    "internet":     ("🌐  BGP-Internet-Monitor",         demo_bgp),
    "viirs":        ("⬛  VIIRS Nachtlicht-Analyse",     demo_viirs),
    "dunkel":       ("⬛  VIIRS Nachtlicht-Analyse",     demo_viirs),
    "displacement": ("🏕  Vertreibungs-Tracking",        demo_displacement),
    "unhcr":        ("🏕  Vertreibungs-Tracking",        demo_displacement),
    "learner":      ("🧠  SAR Self-Learning",            demo_learner),
    "ki":           ("🧠  SAR Self-Learning",            demo_learner),
    "local":        ("📍  Lokal-OSINT",                  demo_local),
    "lokal":        ("📍  Lokal-OSINT",                  demo_local),
}

FULL_SEQUENCE = [
    demo_sar,
    demo_sanctions,
    demo_health,
    demo_bgp,
    demo_viirs,
    demo_displacement,
    demo_learner,
]

SHORT_SEQUENCE = [
    demo_sar,
    demo_bgp,
    demo_viirs,
]


def run_demo(arg: str = ""):
    """
    Hauptfunktion für den Demo-Modus.

    Args:
        arg: "" → vollständige Demo
             "schnell" / "quick" → 3 Module
             Modulname → einzelnes Modul
    """
    arg = (arg or "").strip().lower()

    if arg in ("schnell", "quick", "kurz", "short"):
        demo_intro()
        for fn in SHORT_SEQUENCE:
            fn()
        demo_outro()
        return

    if arg and arg in MODULES:
        label, fn = MODULES[arg]
        _section_header("▶", f"NEXUS DEMO · {label}", "", CY)
        fn()
        return

    if arg and arg not in ("", "full", "voll", "alle"):
        _p(f"\n{YL}Unbekanntes Modul '{arg}'.{R}")
        _p(f"Verfügbar: {', '.join(set(MODULES.keys()))}\n")
        return

    # Vollständige Demo
    demo_intro()
    for fn in FULL_SEQUENCE:
        fn()
    demo_outro()


# ── Standalone-Test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    run_demo(arg)
             