"""
nexus_timeline.py — T140: Automatische Ereignis-Chronologie
============================================================
Baut aus allen NEXUS-Quellen eine kohärente Zeitleiste:
  - Ereignisse nach Zeit sortieren + nach Thema clustern
  - Automatische Narrativ-Generierung
  - HTML-Visualisierung (interaktive Timeline)
  - LLM-Kontext-Formatter
  - Anomalie-Detektion (Lücken, Bursts, Wendepunkte)
"""

import sys
import os
import re
import json
import datetime
import hashlib
import collections
from typing import Optional


def _dbg(msg):
    print(f"[TIMELINE] {msg}", file=sys.stderr)


def _parse_ts(ts_str):
    if not ts_str:
        return None
    ts_str = str(ts_str).strip()
    formats = [
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S", "%Y-%m-%d",
        "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
    ]
    for fmt in formats:
        try:
            return datetime.datetime.strptime(ts_str[:25], fmt).replace(tzinfo=None)
        except ValueError:
            continue
    now = datetime.datetime.utcnow()
    m = re.search(r'(\d+)\s*(hour|stunde|h)', ts_str.lower())
    if m:
        return now - datetime.timedelta(hours=int(m.group(1)))
    m = re.search(r'(\d+)\s*(min)', ts_str.lower())
    if m:
        return now - datetime.timedelta(minutes=int(m.group(1)))
    m = re.search(r'(\d+)\s*(day|tag)', ts_str.lower())
    if m:
        return now - datetime.timedelta(days=int(m.group(1)))
    return None


_TYPE_KEYWORDS = {
    "ANGRIFF":       ["attack", "strike", "angriff", "beschuss", "artillery",
                      "missile", "rocket", "bombed", "shelling", "luftangriff"],
    "EXPLOSION":     ["explosion", "blast", "detonation", "detoniert",
                      "explodiert", "drone strike", "impact"],
    "BEWEGUNG":      ["convoy", "troops", "movement", "konvoi", "advancing",
                      "withdrawing", "deployment", "vorrücken"],
    "VERLUSTE":      ["killed", "wounded", "casualties", "gefallen", "verluste"],
    "DIPLOMATISCH":  ["ceasefire", "negotiations", "talks", "waffenstillstand",
                      "verhandlungen", "summit", "agreement"],
    "INFRASTRUKTUR": ["bridge", "power", "electricity", "water", "pipeline",
                      "infrastructure", "brücke", "staudamm"],
    "SEISMISCH":     ["earthquake", "seismic", "erdbeben", "magnitude"],
    "MARITIM":       ["ship", "vessel", "naval", "schiff", "fleet", "hafen"],
    "LUFTRAUM":      ["aircraft", "drone", "flight", "airspace", "flugzeug", "drohne"],
    "POLITIK":       ["sanctions", "election", "government", "sanktionen", "wahl"],
}

_TYPE_ICONS = {
    "ANGRIFF": "ANGRIFF", "EXPLOSION": "EXPLOSION", "BEWEGUNG": "BEWEGUNG",
    "VERLUSTE": "VERLUSTE", "DIPLOMATISCH": "DIPLOMATISCH",
    "INFRASTRUKTUR": "INFRASTRUKTUR", "SEISMISCH": "SEISMISCH",
    "MARITIM": "MARITIM", "LUFTRAUM": "LUFTRAUM",
    "POLITIK": "POLITIK", "ALLGEMEIN": "ALLGEMEIN",
}

_EMOJI = {
    "ANGRIFF": "💥", "EXPLOSION": "💣", "BEWEGUNG": "🚗",
    "VERLUSTE": "🔴", "DIPLOMATISCH": "🤝", "INFRASTRUKTUR": "🏗",
    "SEISMISCH": "🌍", "MARITIM": "⚓", "LUFTRAUM": "✈",
    "POLITIK": "🏛", "ALLGEMEIN": "📰",
}

_TYPE_COLORS = {
    "ANGRIFF": "#e74c3c", "EXPLOSION": "#e67e22", "BEWEGUNG": "#3498db",
    "VERLUSTE": "#c0392b", "DIPLOMATISCH": "#27ae60", "INFRASTRUKTUR": "#9b59b6",
    "SEISMISCH": "#1abc9c", "MARITIM": "#2980b9", "LUFTRAUM": "#8e44ad",
    "POLITIK": "#f39c12", "ALLGEMEIN": "#7f8c8d",
}


def _classify_event_type(text):
    text = text.lower()
    for etype, keywords in _TYPE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return etype
    return "ALLGEMEIN"


def normalize_events(articles):
    events = []
    for i, art in enumerate(articles):
        ts_str = (art.get("published") or art.get("timestamp") or
                  art.get("date") or art.get("created") or "")
        dt = _parse_ts(ts_str)
        if dt is None:
            dt = datetime.datetime.utcnow() - datetime.timedelta(minutes=i)

        title   = str(art.get("title", "") or "")
        content = str(art.get("content", "") or art.get("summary", "") or "")
        text    = title + " " + content
        etype   = _classify_event_type(text)

        tags = []
        for ent in (art.get("ner_entities") or [])[:3]:
            if isinstance(ent, dict):
                tags.append(ent.get("text", ""))
            else:
                tags.append(str(ent))
        tags = [t for t in tags if t]

        events.append({
            "id":          hashlib.md5(f"{ts_str}{title}".encode()).hexdigest()[:8],
            "timestamp":   ts_str,
            "dt":          dt,
            "source":      str(art.get("source", "?"))[:50],
            "title":       title[:200],
            "content":     content[:400],
            "url":         str(art.get("url", "") or ""),
            "lat":         art.get("lat"),
            "lon":         art.get("lon"),
            "event_type":  etype,
            "tags":        tags,
            "credibility": float(art.get("credibility_score", 0.5) or 0.5),
        })
    events.sort(key=lambda e: e["dt"])
    return events


def build_timeline(events, resolution="hour"):
    if not events:
        return {"periods": [], "total_events": 0, "time_span": "–",
                "anomalies": [], "turning_points": [], "type_distribution": {},
                "start": "?", "end": "?"}

    t_start = events[0]["dt"]
    t_end   = events[-1]["dt"]
    span    = t_end - t_start

    if resolution == "auto":
        if span.total_seconds() < 7200:
            period_fmt = "%Y-%m-%d %H:%M"
        elif span.total_seconds() < 259200:
            period_fmt = "%Y-%m-%d %H:00"
        else:
            period_fmt = "%Y-%m-%d"
    elif resolution == "minute":
        period_fmt = "%Y-%m-%d %H:%M"
    elif resolution == "hour":
        period_fmt = "%Y-%m-%d %H:00"
    else:
        period_fmt = "%Y-%m-%d"

    periods_dict = collections.defaultdict(list)
    for ev in events:
        periods_dict[ev["dt"].strftime(period_fmt)].append(ev)

    periods = []
    for label in sorted(periods_dict.keys()):
        pe = periods_dict[label]
        tc = collections.Counter(e["event_type"] for e in pe)
        dominant = tc.most_common(1)[0][0] if tc else "ALLGEMEIN"
        periods.append({
            "label": label, "events": pe,
            "event_count": len(pe), "dominant_type": dominant,
            "type_counts": dict(tc),
        })

    anomalies = []
    if periods:
        avg = sum(p["event_count"] for p in periods) / len(periods)
        for p in periods:
            if p["event_count"] >= max(3, avg * 3):
                anomalies.append(
                    f"Nachrichten-Burst: {p['event_count']} Ereignisse um {p['label']}")

    prev_dt = None
    for ev in events:
        if prev_dt is not None:
            gap = (ev["dt"] - prev_dt).total_seconds() / 3600
            if gap > 6:
                anomalies.append(
                    f"Informationsluecke: {gap:.0f}h zwischen "
                    f"{prev_dt.strftime('%H:%M')} und {ev['dt'].strftime('%H:%M %d.%m')}")
        prev_dt = ev["dt"]

    turning_points = []
    _ESCALATIONS = {
        ("DIPLOMATISCH", "ANGRIFF"):    "Eskalation: Diplomatie → Angriff",
        ("DIPLOMATISCH", "EXPLOSION"):  "Eskalation: Diplomatie → Explosion",
        ("BEWEGUNG", "ANGRIFF"):        "Truppenbewegung → Angriff",
        ("ANGRIFF", "DIPLOMATISCH"):    "De-Eskalation: Angriff → Diplomatie",
        ("ANGRIFF", "VERLUSTE"):        "Angriff → Verlustzaehlung",
    }
    prev_type = None
    for p in periods:
        if prev_type and prev_type != p["dominant_type"]:
            pair = (prev_type, p["dominant_type"])
            if pair in _ESCALATIONS:
                turning_points.append({
                    "period": p["label"],
                    "from_type": prev_type,
                    "to_type": p["dominant_type"],
                    "label": _ESCALATIONS[pair],
                })
        prev_type = p["dominant_type"]

    type_dist = dict(collections.Counter(e["event_type"] for e in events).most_common())

    if span.days > 0:
        span_str = f"{span.days} Tage {span.seconds // 3600}h"
    else:
        span_str = f"{span.seconds // 3600}h {(span.seconds % 3600) // 60}min"

    return {
        "periods": periods, "total_events": len(events),
        "time_span": span_str,
        "start": t_start.strftime("%Y-%m-%d %H:%M"),
        "end":   t_end.strftime("%Y-%m-%d %H:%M"),
        "anomalies": anomalies[:10],
        "turning_points": turning_points[:5],
        "type_distribution": type_dist,
    }


def generate_narrative(timeline, max_events=15):
    if not timeline.get("periods"):
        return "Keine Ereignisse fuer Chronologie."

    lines = [
        f"## Ereignischronologie ({timeline.get('time_span', '?')})",
        f"Zeitraum: {timeline.get('start', '?')} – {timeline.get('end', '?')}",
        f"Gesamt: {timeline['total_events']} Ereignisse",
        "",
    ]

    if timeline.get("turning_points"):
        lines.append("**Wendepunkte:**")
        for tp in timeline["turning_points"]:
            lines.append(f"  * {tp['label']} ({tp['period']})")
        lines.append("")

    if timeline.get("anomalies"):
        lines.append("**Anomalien:**")
        for a in timeline["anomalies"][:3]:
            lines.append(f"  * {a}")
        lines.append("")

    lines.append("**Chronologie:**")
    event_count = 0
    for period in timeline["periods"]:
        if event_count >= max_events:
            remaining = timeline["total_events"] - event_count
            if remaining > 0:
                lines.append(f"  ... (+{remaining} weitere Ereignisse)")
            break
        period_events = sorted(period["events"],
                                key=lambda e: e.get("credibility", 0.5), reverse=True)[:3]
        for ev in period_events:
            if event_count >= max_events:
                break
            icon  = _EMOJI.get(ev.get("event_type", "ALLGEMEIN"), "📰")
            tstr  = ev["dt"].strftime("%d.%m %H:%M")
            src   = ev.get("source", "?")[:20]
            title = ev.get("title", "-")[:100]
            lines.append(f"  {icon} [{tstr}] {title}  [{src}]")
            event_count += 1

    if timeline.get("type_distribution"):
        top = list(timeline["type_distribution"].items())[:5]
        dist = " | ".join(f"{t}: {c}" for t, c in top)
        lines.append(f"\n**Ereignis-Typen:** {dist}")

    return "\n".join(lines)


def generate_html_timeline(timeline, title="NEXUS Timeline"):
    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    all_events = []
    for period in timeline.get("periods", []):
        for ev in period["events"]:
            all_events.append({
                "id": ev["id"], "time": ev["dt"].strftime("%Y-%m-%dT%H:%M"),
                "label": ev["dt"].strftime("%d.%m %H:%M"),
                "title": ev["title"][:120], "source": ev["source"][:30],
                "type": ev["event_type"],
                "color": _TYPE_COLORS.get(ev["event_type"], "#7f8c8d"),
                "icon": _EMOJI.get(ev["event_type"], "📰"),
                "url": ev.get("url", ""),
                "lat": ev.get("lat"), "lon": ev.get("lon"),
            })

    events_json = json.dumps(all_events, ensure_ascii=False)
    colors_json = json.dumps(_TYPE_COLORS, ensure_ascii=False)

    periods_html = ""
    for period in timeline.get("periods", []):
        periods_html += f'<div class="period-group">\n'
        periods_html += f'<div class="period-label">{period["label"]} <span class="period-count">{period["event_count"]}</span></div>\n'
        for ev in period["events"]:
            color = _TYPE_COLORS.get(ev["event_type"], "#7f8c8d")
            icon  = _EMOJI.get(ev["event_type"], "📰")
            url   = ev.get("url", "")
            title_html = (f'<a href="{url}" target="_blank">{ev["title"][:120]}</a>'
                          if url else ev["title"][:120])
            gps_str = (f'<span>GPS: {ev["lat"]:.3f}, {ev["lon"]:.3f}</span>'
                       if ev.get("lat") and ev.get("lon") else "")
            periods_html += f'''<div class="event-card" style="--type-color:{color}" data-type="{ev["event_type"]}" data-title="{ev["title"].lower()}">
  <span class="event-icon">{icon}</span>
  <div class="event-body">
    <div class="event-title">{title_html}</div>
    <div class="event-meta">
      <span>{ev["dt"].strftime("%H:%M")}</span>
      <span>{ev["source"]}</span>
      <span class="event-type" style="background:{color}">{ev["event_type"]}</span>
      {gps_str}
    </div>
  </div>
</div>\n'''
        periods_html += '</div>\n'

    anomaly_html = ""
    if timeline.get("anomalies"):
        anomaly_html = '<div class="anomaly-box"><h3>Erkannte Anomalien</h3><ul>\n'
        for a in timeline["anomalies"]:
            anomaly_html += f'<li>{a}</li>\n'
        anomaly_html += '</ul></div>\n'

    tp_html = ""
    for tp in timeline.get("turning_points", []):
        tp_html += f'<div class="turning-point">{tp["label"]} — {tp["period"]}</div>\n'

    stats = timeline
    html = f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',sans-serif;background:#0d1117;color:#c9d1d9;min-height:100vh}}
.header{{background:#161b22;border-bottom:1px solid #30363d;padding:16px 24px}}
.header h1{{font-size:1.2rem;color:#58a6ff}}
.header .meta{{font-size:0.8rem;color:#8b949e}}
.stats{{display:flex;gap:12px;padding:12px 24px;background:#0d1117;border-bottom:1px solid #21262d;flex-wrap:wrap}}
.stat{{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 14px}}
.stat .n{{font-size:1.3rem;font-weight:bold;color:#58a6ff}}
.stat .l{{font-size:0.75rem;color:#8b949e}}
.filters{{padding:12px 24px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
.filter-btn{{background:#21262d;border:1px solid #30363d;border-radius:20px;padding:4px 12px;font-size:0.8rem;cursor:pointer;color:#c9d1d9}}
.filter-btn:hover,.filter-btn.active{{background:#58a6ff;color:#000;border-color:#58a6ff}}
.timeline-container{{padding:16px 24px;max-width:1200px}}
.period-group{{margin-bottom:20px}}
.period-label{{font-size:0.85rem;color:#8b949e;text-transform:uppercase;letter-spacing:0.05em;padding:4px 0;border-bottom:1px solid #21262d;margin-bottom:10px;display:flex;align-items:center;gap:8px}}
.period-count{{background:#21262d;border-radius:10px;padding:1px 8px;font-size:0.75rem}}
.event-card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 14px;margin-bottom:7px;border-left:3px solid var(--type-color);display:flex;gap:10px;align-items:flex-start}}
.event-card:hover{{border-color:#58a6ff}}
.event-icon{{font-size:1.1rem;flex-shrink:0}}
.event-body{{flex:1;min-width:0}}
.event-title{{font-size:0.9rem;color:#e6edf3;margin-bottom:3px}}
.event-title a{{color:#58a6ff;text-decoration:none}}
.event-meta{{font-size:0.75rem;color:#8b949e;display:flex;gap:10px;flex-wrap:wrap}}
.event-type{{padding:1px 7px;border-radius:10px;font-size:0.7rem;color:#000;font-weight:600}}
.anomaly-box{{background:#1c1c1c;border:1px solid #f0ad4e;border-radius:8px;padding:12px 16px;margin:12px 24px}}
.anomaly-box h3{{color:#f0ad4e;font-size:0.9rem;margin-bottom:6px}}
.anomaly-box li{{font-size:0.85rem;padding:2px 0;list-style:none}}
.turning-point{{background:#1c2430;border:1px solid #58a6ff;border-radius:8px;padding:7px 12px;margin-bottom:7px;font-size:0.85rem}}
.hidden{{display:none}}
#search{{background:#21262d;border:1px solid #30363d;border-radius:6px;padding:5px 10px;color:#c9d1d9;font-size:0.85rem;width:180px}}
</style>
</head>
<body>
<div class="header">
  <h1>📅 {title}</h1>
  <div class="meta">Generiert: {now_str} | {stats.get('start','?')} – {stats.get('end','?')}</div>
</div>
<div class="stats">
  <div class="stat"><div class="n">{stats.get('total_events',0)}</div><div class="l">Ereignisse</div></div>
  <div class="stat"><div class="n">{len(stats.get('periods',[]))}</div><div class="l">Zeitperioden</div></div>
  <div class="stat"><div class="n">{len(stats.get('turning_points',[]))}</div><div class="l">Wendepunkte</div></div>
  <div class="stat"><div class="n">{stats.get('time_span','?')}</div><div class="l">Zeitraum</div></div>
</div>
<div class="filters">
  <span style="font-size:0.8rem;color:#8b949e">Filter:</span>
  <button class="filter-btn active" onclick="filterType('ALL',this)">Alle</button>
  <button class="filter-btn" onclick="filterType('ANGRIFF',this)">💥 Angriff</button>
  <button class="filter-btn" onclick="filterType('EXPLOSION',this)">💣 Explosion</button>
  <button class="filter-btn" onclick="filterType('BEWEGUNG',this)">🚗 Bewegung</button>
  <button class="filter-btn" onclick="filterType('DIPLOMATISCH',this)">🤝 Diplomatisch</button>
  <button class="filter-btn" onclick="filterType('MARITIM',this)">⚓ Maritim</button>
  <button class="filter-btn" onclick="filterType('LUFTRAUM',this)">✈ Luftraum</button>
  <input type="text" id="search" placeholder="Suche..." oninput="filterSearch(this.value)">
</div>
{f'<div style="padding:0 24px 8px"><div style="font-size:0.85rem;color:#8b949e;margin-bottom:6px">WENDEPUNKTE</div>{tp_html}</div>' if tp_html else ''}
{anomaly_html}
<div class="timeline-container" id="timeline">
{periods_html}
</div>
<script>
var activeType='ALL';var searchTerm='';
function filterType(t,btn){{activeType=t;document.querySelectorAll('.filter-btn').forEach(function(b){{b.classList.remove('active')}});btn.classList.add('active');applyFilters();}}
function filterSearch(v){{searchTerm=v.toLowerCase();applyFilters();}}
function applyFilters(){{
  document.querySelectorAll('.event-card').forEach(function(c){{
    var tm=activeType==='ALL'||c.dataset.type===activeType;
    var sm=!searchTerm||c.dataset.title.includes(searchTerm);
    c.classList.toggle('hidden',!(tm&&sm));
  }});
  document.querySelectorAll('.period-group').forEach(function(pg){{
    var v=pg.querySelectorAll('.event-card:not(.hidden)').length;
    pg.classList.toggle('hidden',v===0);
  }});
}}
</script>
</body></html>"""
    return html


def build_and_render(articles, topic="NEXUS", save_dir=None, auto_open=False):
    events = normalize_events(articles)
    if events:
        span = events[-1]["dt"] - events[0]["dt"]
        res = "hour" if span.days < 3 else "day"
    else:
        res = "hour"
    timeline = build_timeline(events, resolution=res)
    narrative = generate_narrative(timeline)
    html = generate_html_timeline(timeline, title=f"NEXUS Timeline — {topic}")
    html_path = None
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        fname = f"nexus_timeline_{topic[:20].replace(' ', '_')}.html"
        html_path = os.path.join(save_dir, fname)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        if auto_open:
            try:
                import webbrowser
                webbrowser.open(f"file://{html_path}")
            except Exception:
                pass
    return {"timeline": timeline, "narrative": narrative, "html_path": html_path}


def timeline_for_llm(articles, max_events=20):
    events = normalize_events(articles)
    if not events:
        return ""
    timeline = build_timeline(events, resolution="hour")
    return generate_narrative(timeline, max_events=max_events)


def _self_test():
    print("=== nexus_timeline.py Selbsttest ===")
    now = datetime.datetime.utcnow()
    test_articles = [
        {"source": "isw", "title": "Russian forces attack Kharkiv oblast",
         "published": (now - datetime.timedelta(hours=6)).isoformat(), "credibility_score": 0.9},
        {"source": "kyivindependent", "title": "Ukrainian troops report heavy artillery near Kupyansk",
         "published": (now - datetime.timedelta(hours=5, minutes=30)).isoformat(),
         "credibility_score": 0.85, "lat": 49.71, "lon": 37.60},
        {"source": "reuters", "title": "Ceasefire talks resume in Istanbul",
         "published": (now - datetime.timedelta(hours=4)).isoformat(), "credibility_score": 0.9},
        {"source": "osintdefender", "title": "Large explosion reported near Zaporizhzhia",
         "published": (now - datetime.timedelta(hours=3)).isoformat(),
         "credibility_score": 0.8, "lat": 47.85, "lon": 35.11},
        {"source": "bellingcat", "title": "Geolocated: HIMARS strike confirmed Donetsk",
         "published": (now - datetime.timedelta(hours=1)).isoformat(), "credibility_score": 0.95},
        {"source": "reuters", "title": "Ceasefire talks break down after explosion",
         "published": (now - datetime.timedelta(minutes=30)).isoformat(), "credibility_score": 0.9},
    ]

    print(f"\n[1] Event-Normalisierung ({len(test_articles)} Artikel)")
    events = normalize_events(test_articles)
    print(f"  → {len(events)} Events normalisiert")
    for ev in events[:3]:
        print(f"  {ev['dt'].strftime('%H:%M')} | {ev['event_type']:15} | {ev['title'][:60]}")

    print(f"\n[2] Zeitleiste bauen")
    timeline = build_timeline(events, resolution="hour")
    print(f"  Perioden: {len(timeline['periods'])}")
    print(f"  Zeitraum: {timeline['time_span']}")
    print(f"  Anomalien: {timeline['anomalies']}")
    print(f"  Wendepunkte: {[tp['label'] for tp in timeline['turning_points']]}")
    print(f"  Typen: {timeline['type_distribution']}")

    print(f"\n[3] Narrativ")
    narrative = generate_narrative(timeline)
    print(narrative[:500])

    print(f"\n[4] HTML-Generierung")
    html = generate_html_timeline(timeline, "Test-Timeline")
    print(f"  HTML: {len(html)} Bytes")

    print(f"\n[5] LLM-Kontext")
    ctx = timeline_for_llm(test_articles, max_events=5)
    print(ctx[:300])

    print("\n=== Selbsttest abgeschlossen ===")


if __name__ == "__main__":
    _self_test()
