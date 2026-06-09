"""
nexus_portal.py — NEXUS Web-Portal (ohne Kommandozeile)
=========================================================
Einfache Weboberfläche für NEXUS — kein Terminal nötig.
Für Journalisten, NGOs und externe Nutzer.

Features:
  - Region eingeben → sofortiger Lagebericht
  - 6h-Kurzbericht per Klick
  - Zeitvergleich per Klick
  - Eskalations-Score live
  - Login-Schutz via NEXUS_TOKEN aus config.py

Start:
  python nexus_portal.py
  → Browser öffnet http://localhost:8050

Produktiv (anderer Port):
  python nexus_portal.py --port 8080
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse, unquote

BASIS_DIR = os.path.dirname(os.path.abspath(__file__))
PORTAL_PORT = 8050

# ── Auth ──────────────────────────────────────────────────────────────────────

def _get_token() -> str:
    try:
        import config  # type: ignore
        return str(getattr(config, "NEXUS_TOKEN", "") or "")
    except ImportError:
        return ""

def _check_auth(cookie: str) -> bool:
    token = _get_token()
    if not token:
        return True  # kein Passwort = immer OK
    return f"nexus_auth={token}" in (cookie or "")


# ── Schnell-Analyse (ohne LLM, nur Daten) ────────────────────────────────────

def _schnell_lagebild(region: str) -> dict:
    """Holt aktuelle Daten für eine Region — schnell, kein LLM."""
    ergebnis = {"region": region, "ts": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")}

    # Letzten Longtest-Lauf laden wenn verfügbar
    daten_dir = os.path.join(BASIS_DIR, "nexus_longtest_daten")
    if os.path.isdir(daten_dir):
        dateien = sorted([
            f for f in os.listdir(daten_dir)
            if f.startswith("lauf_") and f.endswith(".json")
        ])
        for fname in reversed(dateien):  # neueste zuerst
            try:
                with open(os.path.join(daten_dir, fname), encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("ziel", "").lower() == region.lower():
                    esk = data["quellen"].get("eskalation", {})
                    ergebnis["score"]   = esk.get("score", 0)
                    ergebnis["level"]   = esk.get("level", "?")
                    ergebnis["signale"] = esk.get("signale", [])
                    ergebnis["details"] = esk.get("details", [])
                    ergebnis["lauf_ts"] = data["timestamp"][:16].replace("T", " ") + " UTC"
                    # Headlines
                    rss = data["quellen"].get("rss", {})
                    ergebnis["headlines"] = (rss.get("headlines") or [])[:5]
                    break
            except Exception:
                continue

    # 6h-Bericht einbinden
    try:
        from nexus_brief import generiere_brief  # type: ignore
        brief = generiere_brief(6, region)
        ergebnis["brief_text"] = brief.get("text", "")
        ergebnis["brief_stats"] = brief.get("stats", {})
    except Exception:
        ergebnis["brief_text"] = ""

    return ergebnis


# ── HTML generieren ───────────────────────────────────────────────────────────

def _login_html(fehler: bool = False) -> str:
    fehler_html = '<p style="color:#ff5252;margin-top:10px">❌ Falsches Passwort</p>' if fehler else ""
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>NEXUS — Login</title>
  <style>
    * {{ box-sizing:border-box; margin:0; padding:0 }}
    body {{ background:#0a0a1a; color:#e0e0e0; font-family:'Segoe UI',sans-serif;
            display:flex; justify-content:center; align-items:center; min-height:100vh }}
    .card {{ background:#0d1b2a; border:1px solid #1e3a5f; border-radius:16px;
             padding:40px; width:340px; text-align:center }}
    h1 {{ color:#4fc3f7; font-size:28px; letter-spacing:4px; margin-bottom:8px }}
    p {{ color:#666; font-size:13px; margin-bottom:24px }}
    input {{ width:100%; background:#0a1520; border:1px solid #1e3a5f; color:#e0e0e0;
             padding:12px; border-radius:8px; font-size:14px; margin-bottom:16px; outline:none }}
    input:focus {{ border-color:#4fc3f7 }}
    button {{ width:100%; background:linear-gradient(135deg,#1565c0,#0d47a1);
              color:#fff; border:none; padding:12px; border-radius:8px;
              font-size:14px; font-weight:600; cursor:pointer; letter-spacing:1px }}
    button:hover {{ background:linear-gradient(135deg,#1976d2,#1565c0) }}
  </style>
</head>
<body>
  <div class="card">
    <h1>NEXUS</h1>
    <p>Intelligence Platform · Bitte anmelden</p>
    <form method="POST" action="/login">
      <input type="password" name="token" placeholder="Passwort" autofocus>
      <button type="submit">ANMELDEN →</button>
    </form>
    {fehler_html}
  </div>
</body>
</html>"""


def _portal_html(region: str = "", lagebild: dict | None = None) -> str:
    """Hauptseite des Portals."""
    ts = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    # Score-Anzeige
    if lagebild and lagebild.get("score") is not None:
        score = lagebild["score"]
        level = lagebild.get("level", "?")
        level_colors = {"GRUEN":"#00c853","GELB":"#ffd600","ORANGE":"#ff6d00","ROT":"#d32f2f","KRITISCH":"#b71c1c"}
        score_color = level_colors.get(level, "#888")
        lauf_ts = lagebild.get("lauf_ts", "")

        signale_html = " ".join(
            f'<span class="tag">{s}</span>' for s in (lagebild.get("signale") or [])[:6]
        ) or '<span style="color:#444">keine aktiven Signale</span>'

        headlines_html = "".join(
            f'<li>{h[:120]}</li>' for h in (lagebild.get("headlines") or [])
        ) or "<li style='color:#444'>Keine aktuellen Headlines</li>"

        details_html = "".join(
            f'<div class="signal-item">{d.get("icon","·")} <strong>{d.get("label","?")[:60]}</strong> +{d.get("points",0)}pt <span class="conf">[{d.get("conf","?")}]</span></div>'
            for d in (lagebild.get("details") or [])[:5]
        ) or '<div style="color:#444">Keine Signale</div>'

        brief_text = lagebild.get("brief_text", "").strip()
        brief_html = f'<pre style="white-space:pre-wrap;color:#aaa;font-size:13px;line-height:1.6">{brief_text}</pre>' if brief_text else "<p style='color:#444'>Noch keine Daten für diesen Zeitraum.</p>"

        ergebnis_html = f"""
        <div class="result-grid">
          <div class="card score-card">
            <div class="card-title">⚡ Eskalations-Score</div>
            <div style="font-size:56px;font-weight:900;color:{score_color};line-height:1">{score}</div>
            <div style="color:{score_color};font-size:18px;font-weight:600;margin-top:4px">{level}</div>
            <div style="color:#555;font-size:12px;margin-top:8px">Stand: {lauf_ts}</div>
          </div>
          <div class="card">
            <div class="card-title">🔴 Aktive Signale</div>
            <div style="margin-top:8px">{signale_html}</div>
            <div style="margin-top:16px">
              <div class="card-title">📊 Signal-Details</div>
              {details_html}
            </div>
          </div>
          <div class="card full">
            <div class="card-title">📰 Aktuelle Schlagzeilen</div>
            <ul class="headlines">{headlines_html}</ul>
          </div>
          <div class="card full">
            <div class="card-title">📋 6h-Kurzbericht</div>
            {brief_html}
          </div>
        </div>"""
    else:
        ergebnis_html = ""

    region_val = region or ""

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>NEXUS Portal</title>
  <style>
    * {{ box-sizing:border-box; margin:0; padding:0 }}
    body {{ background:#0a0a1a; color:#e0e0e0; font-family:'Segoe UI',sans-serif; min-height:100vh }}
    header {{ background:linear-gradient(135deg,#0d1b2a,#1a2a4a); border-bottom:1px solid #1e3a5f;
              padding:20px 30px; display:flex; justify-content:space-between; align-items:center }}
    .logo {{ color:#4fc3f7; font-size:24px; font-weight:900; letter-spacing:4px }}
    .logo span {{ color:#666; font-size:13px; font-weight:400; margin-left:12px }}
    .ts {{ color:#444; font-size:12px }}
    .container {{ max-width:1100px; margin:0 auto; padding:30px 20px }}
    .search-box {{ background:#0d1b2a; border:1px solid #1e3a5f; border-radius:12px;
                   padding:24px; margin-bottom:24px }}
    .search-box h2 {{ color:#4fc3f7; font-size:15px; margin-bottom:16px }}
    .search-row {{ display:flex; gap:12px; flex-wrap:wrap }}
    input[type=text] {{ flex:1; min-width:200px; background:#060d16; border:1px solid #1e3a5f;
                        color:#e0e0e0; padding:12px 16px; border-radius:8px; font-size:14px; outline:none }}
    input[type=text]:focus {{ border-color:#4fc3f7 }}
    .btn {{ padding:12px 24px; border:none; border-radius:8px; font-size:14px;
            font-weight:600; cursor:pointer; letter-spacing:0.5px }}
    .btn-primary {{ background:linear-gradient(135deg,#1565c0,#0d47a1); color:#fff }}
    .btn-primary:hover {{ background:linear-gradient(135deg,#1976d2,#1565c0) }}
    .btn-sec {{ background:#1a2a3a; color:#4fc3f7; border:1px solid #1e3a5f }}
    .btn-sec:hover {{ background:#1e3a5f }}
    .quick-btns {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:12px }}
    .quick-btn {{ background:#0a1520; border:1px solid #1e3a5f; color:#aaa;
                  padding:6px 14px; border-radius:20px; font-size:12px; cursor:pointer }}
    .quick-btn:hover {{ border-color:#4fc3f7; color:#4fc3f7 }}
    .result-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px }}
    .card {{ background:#0d1b2a; border:1px solid #1e3a5f; border-radius:10px; padding:18px }}
    .card.full {{ grid-column:1/-1 }}
    .card.score-card {{ text-align:center }}
    .card-title {{ font-size:11px; color:#4fc3f7; text-transform:uppercase; letter-spacing:1px; margin-bottom:10px }}
    .tag {{ background:#1a3a5c; color:#4fc3f7; padding:3px 10px; border-radius:12px; font-size:12px; margin:2px }}
    .headlines {{ list-style:none; }}
    .headlines li {{ color:#ccc; padding:5px 0; border-bottom:1px solid #1a2a3a; font-size:13px }}
    .signal-item {{ padding:4px 0; color:#ccc; font-size:13px; border-bottom:1px solid #111827 }}
    .conf {{ color:#555; font-size:11px }}
    .loading {{ display:none; color:#4fc3f7; font-size:13px; margin-top:8px }}
    footer {{ color:#333; font-size:11px; text-align:center; padding:20px; margin-top:40px }}
    @media(max-width:600px) {{ .result-grid {{ grid-template-columns:1fr }} }}
  </style>
</head>
<body>
  <header>
    <div class="logo">NEXUS <span>Intelligence Portal</span></div>
    <div class="ts">{ts}</div>
  </header>

  <div class="container">
    <div class="search-box">
      <h2>🔍 Region analysieren</h2>
      <form method="GET" action="/analyse">
        <div class="search-row">
          <input type="text" name="region" placeholder="Region eingeben: Iran, Ukraine, Gaza, Taiwan ..."
                 value="{region_val}" autofocus>
          <button type="submit" class="btn btn-primary">ANALYSIEREN →</button>
        </div>
      </form>
      <div class="quick-btns">
        <span style="color:#555;font-size:12px;align-self:center">Schnellwahl:</span>
        <button class="quick-btn" onclick="analyse('Iran')">Iran</button>
        <button class="quick-btn" onclick="analyse('Ukraine')">Ukraine</button>
        <button class="quick-btn" onclick="analyse('Gaza')">Gaza</button>
        <button class="quick-btn" onclick="analyse('Taiwan')">Taiwan</button>
        <button class="quick-btn" onclick="analyse('Russland')">Russland</button>
        <button class="quick-btn" onclick="analyse('Naher Osten')">Naher Osten</button>
      </div>
      <div class="quick-btns" style="margin-top:16px">
        <a href="/brief?region={region_val}" class="btn btn-sec" style="text-decoration:none">📋 6h-Bericht</a>
        <a href="/vergleich?region={region_val}&tage=1" class="btn btn-sec" style="text-decoration:none">📊 Gestern vergleichen</a>
        <a href="/vergleich?region={region_val}&tage=7" class="btn btn-sec" style="text-decoration:none">📈 7-Tage-Vergleich</a>
        <a href="/logout" class="btn btn-sec" style="text-decoration:none">🚪 Abmelden</a>
      </div>
    </div>

    {ergebnis_html}
  </div>

  <footer>NEXUS Intelligence Platform · {ts}</footer>

  <script>
    function analyse(region) {{
      window.location.href = '/analyse?region=' + encodeURIComponent(region);
    }}
  </script>
</body>
</html>"""


# ── HTTP Handler ──────────────────────────────────────────────────────────────

class PortalHandler(BaseHTTPRequestHandler):

    def _cookie(self) -> str:
        return self.headers.get("Cookie", "")

    def _send_html(self, html: str, status: int = 200, extra_headers: list | None = None):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        if extra_headers:
            for k, v in extra_headers:
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, set_cookie: str | None = None):
        self.send_response(302)
        self.send_header("Location", location)
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        params = parse_qs(parsed.query)

        # Login-Check
        token = _get_token()
        if token and not _check_auth(self._cookie()):
            if path != "/login":
                self._send_html(_login_html())
                return

        if path in ("/", "/index"):
            self._send_html(_portal_html())

        elif path == "/analyse":
            region = unquote(params.get("region", [""])[0]).strip()
            if region:
                lagebild = _schnell_lagebild(region)
                self._send_html(_portal_html(region, lagebild))
            else:
                self._redirect("/")

        elif path == "/brief":
            region = unquote(params.get("region", [""])[0]).strip()
            try:
                from nexus_brief import generiere_brief, speichere_brief_html  # type: ignore
                bericht = generiere_brief(6, region)
                pfad = speichere_brief_html(bericht["html"])
                with open(pfad, encoding="utf-8") as f:
                    self._send_html(f.read())
            except Exception as e:
                self._send_html(f"<p>Fehler: {e}</p>")

        elif path == "/vergleich":
            region = unquote(params.get("region", [""])[0]).strip()
            tage   = int(params.get("tage", ["1"])[0])
            try:
                from nexus_compare import vergleiche, generiere_html  # type: ignore
                vgl  = vergleiche(tage, region)
                html = generiere_html(vgl)
                self._send_html(html)
            except Exception as e:
                self._send_html(f"<p>Fehler: {e}</p>")

        elif path == "/logout":
            self._redirect("/", set_cookie="nexus_auth=; Max-Age=0; Path=/")

        else:
            self._send_html("<p>404 Not Found</p>", 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length).decode("utf-8")
            params = parse_qs(body)
            eingabe = params.get("token", [""])[0]
            token   = _get_token()
            if eingabe == token:
                self._redirect("/", set_cookie=f"nexus_auth={token}; Path=/; Max-Age=86400")
            else:
                self._send_html(_login_html(fehler=True))
        else:
            self._send_html("<p>405</p>", 405)

    def log_message(self, fmt, *args):
        pass  # Kein Log-Spam


# ── Main ──────────────────────────────────────────────────────────────────────

def starte_portal(port: int = PORTAL_PORT):
    server = HTTPServer(("0.0.0.0", port), PortalHandler)
    print(f"""
╔══════════════════════════════════════════════════════╗
║           NEXUS Web-Portal gestartet                 ║
╠══════════════════════════════════════════════════════╣
║                                                      ║
║  Lokal:   http://localhost:{port}                     ║
║  Handy:   http://<deine-IP>:{port}                    ║
║                                                      ║
║  Beenden: Strg+C                                     ║
╚══════════════════════════════════════════════════════╝
""")
    # Browser automatisch öffnen
    def _open():
        time.sleep(1)
        try:
            import webbrowser
            webbrowser.open(f"http://localhost:{port}")
        except Exception:
            pass
    threading.Thread(target=_open, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Portal] Beendet.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NEXUS Web-Portal")
    parser.add_argument("--port", type=int, default=PORTAL_PORT, help=f"Port (Standard: {PORTAL_PORT})")
    args = parser.parse_args()
    starte_portal(args.port)
         