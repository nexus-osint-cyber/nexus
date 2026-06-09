# NEXUS OSINT Platform

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Modules](https://img.shields.io/badge/modules-190%2B-orange.svg)]()
[![Sources](https://img.shields.io/badge/data%20sources-40%2B-yellow.svg)]()
[![Offline](https://img.shields.io/badge/offline-capable-brightgreen.svg)]()
[![Local AI](https://img.shields.io/badge/AI-local%20%28Ollama%29-purple.svg)]()

> **Real-time, multi-source OSINT platform for geopolitical situational awareness.**  
> 190+ Python modules · 40+ free data sources · fully local · no cloud required.

---

## Overview

NEXUS aggregates, correlates and fuses open-source intelligence from maritime tracking, military aviation, conflict data, social media, satellite imagery, seismic monitoring and financial intelligence into a unified live map dashboard — all running on your own hardware, with no licence fees and no data leaving your machine.

**Built as an independent research project (2024–2025)** demonstrating real-world OSINT tradecraft comparable to commercial platforms like Palantir Gotham, at zero cost.

---

## Key Capabilities

| Domain | What NEXUS does |
|--------|----------------|
| 🌊 **Maritime** | Real-time AIS tracking, shadow fleet detection, MMSI anomalies, SAR satellite overlay, sanctions cross-check |
| ✈️ **Air/ISR** | Unfiltered ADS-B (ADS-B Exchange), auto-classification of P-8A, RC-135, AWACS, RQ-4, E-3 |
| 📡 **Signal Fusion** | Cross-correlates fires (NASA FIRMS), seismic (USGS), GPS jamming, ACLED conflict, lightning |
| 🌐 **Multilingual NLP** | Auto-detect & translate AR/FA/RU/ZH → EN, spaCy NER, military keyword extraction |
| 💰 **FININT** | OFAC SDN + EU sanctions list, blockchain wallet analysis (BTC/ETH), shell company detection |
| 🔍 **Pattern-of-Life** | 7-day behavioral baselines, MAD-based anomaly detection for vessels and aircraft |
| 🤖 **Local AI** | Ollama bridge (Llama3/Mistral/LLaVA), SAR image analysis, video keyframe extraction |
| 📊 **Time-Series DB** | SQLite WAL-mode signal history, delta tracking, source calibration feedback loop |
| 🌑 **Dark Web (optional)** | Passive Tor .onion monitoring of ransomware leak sites (requires Tor, opt-in only) |
| 🗺️ **Live Dashboard** | Leaflet.js map with 25+ layer toggles, heatmap, cluster view, satellite overlay |

---

## Architecture

```
nexus_live_server.py          ← HTTP server (port 11430), WebSocket push
├── nexus_ais.py              ← AIS maritime tracking
├── nexus_adsb.py             ← ADS-B military flight tracking
├── nexus_acled.py            ← Armed conflict events (ACLED)
├── nexus_firms.py            ← NASA fire/thermal anomalies
├── nexus_gdelt.py            ← GDELT news event stream
├── nexus_telegram.py         ← Telegram channel monitoring
├── nexus_timeseries.py       ← SQLite WAL time-series database   [v2]
├── nexus_patrol.py           ← Pattern-of-Life anomaly engine    [v2]
├── nexus_nlp.py              ← Multi-language NLP + NER          [v2]
├── nexus_darkweb.py          ← Tor .onion passive monitoring      [v2]
├── nexus_finint.py           ← FININT / sanctions intelligence    [v2]
├── nexus_calibration.py      ← Source calibration feedback loop  [v2]
├── nexus_llm.py              ← Ollama LLM bridge
├── nexus_sar.py              ← Sentinel-1 SAR satellite imagery
├── nexus_escalation.py       ← Multi-signal escalation scoring
└── nexus_livemap.py          ← Leaflet.js live dashboard
    ... + 175 additional modules
```

---

## Quickstart

### Requirements

- Python 3.10+
- [Ollama](https://ollama.ai) (optional, for local AI features)
- Tor (optional, for dark web monitoring)

### Install

```bash
git clone https://github.com/YOUR_USERNAME/nexus-osint.git
cd nexus-osint
pip install -r requirements.txt
```

### Run

```bash
python nexus_live_server.py
# Open http://localhost:11430 in your browser
```

### Docker

```bash
docker compose up
# Open http://localhost:11430
```

---

## Data Sources (selection)

**Maritime & Aviation**
- AISHub (anonymous public feed)
- ADS-B Exchange (unfiltered military)
- OpenSky Network
- NOTAM (aviationweather.gov)

**Conflict & Events**
- ACLED Armed Conflict Location & Event Data
- GDELT Global Event Database (100,000+ news sources)
- UCDP Uppsala Conflict Data Program
- DeepStateMap GeoJSON frontlines

**Satellite & Environmental**
- NASA FIRMS (fires / thermal anomalies)
- NASA EONET (natural events)
- Copernicus Sentinel-1 SAR (radar imagery)
- USGS Seismic Network
- GPS Jam (gpsjam.org)
- EPA RadNet / IAEA radiation monitoring

**Social Media & OSINT**
- Telegram channels (60+ curated)
- Reddit OSINT communities
- Bellingcat, ISW, Kyiv Independent, Meduza, Rybar

**Financial Intelligence**
- OFAC SDN List (US Treasury, ~15,000 entries)
- EU Consolidated Sanctions List
- OpenCorporates company registry
- Blockchair API (BTC/ETH blockchain)

---

## Module Highlights

### `nexus_timeseries.py` — Time-Series Database
SQLite with WAL-mode for concurrent reads. Stores all signal history, entity positions, alerts and analyst outcomes. Supports delta computation, trend queries and 7-day rolling baselines.

### `nexus_patrol.py` — Pattern-of-Life Engine
Computes 7-day behavioral baselines (median speed, dominant region, typical state) per entity using Median Absolute Deviation (MAD) — robust to GPS outliers. Detects: `REGION_CHANGE`, `SPEED_ANOMALY`, `ANCHOR_DRIFT`, `SPEED_STOP`, `NEW_ENTITY`.

### `nexus_nlp.py` — Multilingual NLP
Unicode-range script detection (Arabic, Cyrillic, CJK, Hebrew) before statistical fallback. Four-tier translation chain: Ollama → googletrans → Google API → passthrough. spaCy NER + regex for callsigns, coordinates, ship names.

### `nexus_finint.py` — Financial Intelligence
OFAC SDN XML downloaded and cached in SQLite (24h TTL). Every AIS vessel is cross-checked against sanctions on each refresh. Shell company scoring via offshore jurisdiction patterns. BTC/ETH wallet analysis via Blockchair.

### `nexus_calibration.py` — Source Calibration
Precision/Recall/F1 computed per alert threshold over a rolling 90-day window. Calibration factors (0.85 / 1.0 / 1.15) automatically adjust raw escalation scores based on historical analyst feedback.

---

## Escalation Scoring

NEXUS computes a composite escalation score (0–100) per region by fusing:

- ISR aircraft presence and flight patterns
- AIS vessel anomalies and disappearances
- USNS naval logistics movements
- ACLED conflict event spikes
- Fire/thermal anomaly clusters
- Seismic signatures
- Social media sentiment (multilingual)
- Sanctions hits in active area

Scores are automatically calibrated using analyst feedback from the time-series database.

---

## Comparison

| Feature | NEXUS | Palantir Gotham | Maltego |
|---------|-------|----------------|---------|
| Offline / Air-gap | ✅ Yes | ⚠️ Limited | ❌ No |
| Local AI (no cloud transfer) | ✅ Yes | ❌ No | ❌ No |
| Telegram / Social OSINT | ✅ Yes | ⚠️ Limited | ⚠️ Plugin |
| SAR satellite analysis | ✅ Free | ✅ Licensed | ❌ No |
| Sanctions list cross-check | ✅ OFAC + EU | ✅ Yes | ⚠️ Plugin |
| Open-source algorithm | ✅ Auditable | ❌ Black box | ❌ No |
| Cost | ✅ €0 | ❌ €10–100M+ | ❌ €5,000+/yr |
| Deployment time | ✅ Minutes | ❌ 6–18 months | ⚠️ Hours |

---

## Use Cases

- **Security research** — track maritime incidents, conflict escalation, information operations
- **Academic OSINT** — reproducible, open methodology for geopolitical analysis
- **Due diligence** — automated sanctions screening, corporate risk assessment
- **Threat intelligence** — ransomware leak monitoring, dark web surface coverage
- **Education** — hands-on OSINT tradecraft with real data

---

## Legal & Ethics

NEXUS operates exclusively on **publicly available, open-source data**.

- No scraping of paywalled content
- No access to non-public government systems
- Dark web monitoring is **opt-in**, **passive only**, limited to known public security research targets
- No personal data is stored beyond what is publicly broadcast (AIS, ADS-B)
- All AI inference runs locally — no data is sent to external services

This project is intended for **defensive, research and educational purposes**.

---

## Contributing

Pull requests welcome. Please open an issue first to discuss major changes.

Areas where contributions are especially valuable:
- Additional data source integrations
- Multi-language NLP improvements (Persian, Chinese, Turkish)
- Pattern-of-Life algorithm refinements
- Documentation and tutorials

---

## License

[MIT License](LICENSE) — free to use, modify and distribute with attribution.

---

## Author

Independent research project.  
Contact: kontaktnexus-osint@proton.me

> *"The best OSINT platform is the one you can actually run."*
