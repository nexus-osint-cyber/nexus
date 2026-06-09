# ============================================================
# NEXUS OSINT – Docker Image
# ============================================================
# Basis: Python 3.11 slim (kein Audio/GUI benötigt)
# Port:  5050  (nexus_live_server.py)
#
# Build:   docker build -t nexus-osint .
# Run:     docker run -p 5050:5050 --env-file .env nexus-osint
#          → Browser: http://localhost:5050/?q=Ukraine
# ============================================================

FROM python:3.11-slim

# ── System-Pakete ─────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Netzwerk/TLS
    ca-certificates \
    curl \
    # Für lxml
    libxml2-dev \
    libxslt-dev \
    # Für Kompilierung einiger Wheels
    gcc \
    g++ \
    # Für reportlab (PDF)
    libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

# ── Python-Abhängigkeiten ─────────────────────────────────
WORKDIR /app
COPY requirements-docker.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-docker.txt

# ── Quellcode kopieren ────────────────────────────────────
# Nur die relevanten Dateien (kein Audio, kein GUI)
COPY config.py              .
COPY nexus_live_server.py   .
COPY nexus_report.py        .
COPY nexus_flights.py       .
COPY nexus_maritime.py      .
COPY nexus_weather.py       .
COPY nexus_seismic.py       .
COPY nexus_gdelt.py         .
COPY nexus_rss.py           .
COPY nexus_telegram.py      .
COPY nexus_reddit.py        .
COPY nexus_acled.py         .
COPY nexus_ais.py           .
COPY nexus_notam.py         .
COPY nexus_correlate.py     .
COPY nexus_credibility.py   .
COPY nexus_escalation.py    .
COPY nexus_gpsjam.py        .
COPY nexus_lightning.py     .
COPY nexus_draught.py       .
COPY nexus_satellite_timing.py .
COPY nexus_firms.py         .
COPY nexus_eonet.py         .
COPY nexus_economics.py     .
COPY nexus_wiki.py          .
COPY nexus_memory.py        .
COPY nexus_delta.py         .
COPY nexus_watchlist.py     .
COPY nexus_daily.py         .
COPY nexus_pdf_export.py    .
COPY nexus_search.py        .
COPY nexus_translate.py     .

# ── Persistenz-Verzeichnis für SQLite-DBs ────────────────
RUN mkdir -p /data
VOLUME /data

# ── Port freigeben ────────────────────────────────────────
EXPOSE 5050

# ── Health-Check ─────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:5050/health || exit 1

# ── Umgebungsvariablen (Defaults – per .env oder docker-compose überschreiben) ──
ENV NEXUS_PORT=5050
ENV NEXUS_HOST=0.0.0.0
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# ── Startbefehl ───────────────────────────────────────────
CMD ["python", "nexus_live_server.py"]
