"""
NEXUS – Login-Seite
Wird vom Server angezeigt wenn NEXUS_TOKEN gesetzt und kein Cookie vorhanden.
"""
from __future__ import annotations


def build_login_html(next_path: str = "/livemap", error: bool = False) -> str:
    err_msg = "<div class='err'>❌ Falsches Passwort. Nochmal versuchen.</div>" if error else ""
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#00d4ff">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>NEXUS – Zugang</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{height:100%;background:#060b10;color:#c8d6e0;
          font-family:'Courier New',monospace;
          display:flex;align-items:center;justify-content:center;
          padding:20px}}
.card{{background:#071a2e;border:1px solid #00d4ff;border-radius:8px;
      padding:40px 32px;width:100%;max-width:380px;
      box-shadow:0 0 40px rgba(0,212,255,0.15)}}
.logo{{color:#00d4ff;font-size:22px;font-weight:bold;letter-spacing:6px;
      text-align:center;margin-bottom:8px}}
.sub{{color:#4a8090;font-size:11px;text-align:center;
     letter-spacing:2px;margin-bottom:32px}}
label{{font-size:11px;color:#4a8090;letter-spacing:1px;display:block;margin-bottom:6px}}
input[type=password]{{
  width:100%;padding:12px 14px;
  background:#04111e;border:1px solid #1e3a4a;
  color:#c8d6e0;font-family:'Courier New',monospace;font-size:14px;
  border-radius:4px;outline:none;
  transition:border-color 0.2s;
}}
input[type=password]:focus{{border-color:#00d4ff}}
button{{
  width:100%;margin-top:16px;padding:13px;
  background:#00d4ff;border:none;border-radius:4px;
  color:#060b10;font-family:'Courier New',monospace;
  font-size:13px;font-weight:bold;letter-spacing:2px;
  cursor:pointer;transition:opacity 0.2s;
}}
button:hover{{opacity:0.85}}
button:active{{opacity:0.7}}
.err{{color:#ff4466;font-size:11px;text-align:center;
     margin-top:12px;padding:8px;
     background:rgba(255,68,102,0.1);border-radius:4px}}
.lock{{text-align:center;font-size:40px;margin-bottom:20px;opacity:0.6}}
</style>
</head>
<body>
<div class="card">
  <div class="lock">🔒</div>
  <div class="logo">◈ NEXUS</div>
  <div class="sub">ZUGANGS-KONTROLLE</div>
  <form method="POST" action="/login?next={next_path}">
    <label>ZUGANGSPASSWORT</label>
    <input type="password" name="token" placeholder="••••••••••••"
           autofocus autocomplete="current-password">
    <button type="submit">ZUGANG GEWÄHREN ▶</button>
    {err_msg}
  </form>
</div>
</body>
</html>"""
