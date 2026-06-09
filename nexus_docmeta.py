"""
nexus_docmeta.py — T139: Dokument-Metadaten OSINT
==================================================
Extrahiert versteckte Metadaten aus geleakten Dokumenten:
  - PDF: Autor, Software, Erstellungsdatum, GPS (falls vorhanden), Revisionen
  - DOCX/Word: Autor, letzte Bearbeiter, versteckte Revisionen, Firmenname
  - XLSX/Excel: Autor, Berechnungshistorie
  - Allgemein: Zeitstempel-Anomalien, Timezone-Fingerprinting
  - Bellingcat-Methodik: wer hat wann auf welchem System gearbeitet?
"""

import os
import sys
import re
import json
import struct
import zipfile
import datetime
import hashlib
from typing import Optional

# ─── Optional imports ─────────────────────────────────────────────────────────

try:
    import xml.etree.ElementTree as ET
    _ET_OK = True
except ImportError:
    _ET_OK = False

try:
    from PyPDF2 import PdfReader as _PdfReader
    _PYPDF2_OK = True
except ImportError:
    _PYPDF2_OK = False

try:
    import pdfplumber as _pdfplumber
    _PDFPLUMBER_OK = True
except ImportError:
    _PDFPLUMBER_OK = False

# ─── Debug ────────────────────────────────────────────────────────────────────

def _dbg(msg: str) -> None:
    print(f"[DOCMETA] {msg}", file=sys.stderr)

# ─── Known Software Fingerprints ──────────────────────────────────────────────

# Software → mögliche Herkunft
_SOFTWARE_HINTS = {
    # Russisch/Sowjetisch
    "libreoffice": "LibreOffice (häufig RU/UA/BY-Regierung)",
    "openoffice":  "OpenOffice (ältere RU-Regierungsdokumente)",
    "мой офис":    "МойОфис (russische MS-Office-Alternative, Staatsbehörden RU)",
    "мойофис":     "МойОфис (russische MS-Office-Alternative, Staatsbehörden RU)",
    "р7-офис":     "Р7-Офис (russische MS-Office-Alternative)",
    "р7 офис":     "Р7-Офис (russische MS-Office-Alternative)",
    "аскон":       "АСКОН (russische CAD-Software)",
    # Westlich
    "microsoft word":   "Microsoft Word",
    "microsoft excel":  "Microsoft Excel",
    "microsoft powerpoint": "Microsoft PowerPoint",
    "adobe acrobat":    "Adobe Acrobat",
    "adobe indesign":   "Adobe InDesign",
    "adobe illustrator":"Adobe Illustrator",
    "google docs":      "Google Docs",
    "apple pages":      "Apple Pages (macOS)",
    "wps office":       "WPS Office (Kingsoft, CN)",
    # Speziell
    "latex":     "LaTeX (akademisch/technisch)",
    "pandoc":    "Pandoc (automatisch generiert)",
    "fpdf":      "FPDF (Python PDF-Library)",
    "reportlab": "ReportLab (Python PDF-Library)",
    "ghostscript": "Ghostscript (manipuliert/konvertiert)",
}

# Timezone → Region-Mapping
_TZ_REGIONS = {
    "UTC+3":  "Moskau / Minsk / Istanbul",
    "UTC+2":  "Kiew / Warschau / Berlin (Sommer) / Kairo",
    "UTC+1":  "Berlin (Winter) / Paris / Rom",
    "UTC+0":  "London / Lissabon / Reykjavik",
    "UTC+4":  "Baku / Dubai / Teheran+0.5",
    "UTC+5":  "Islamabad / Taschkent",
    "UTC+8":  "Peking / Singapur / Taipei",
    "UTC-5":  "New York (Winter)",
    "UTC-6":  "Chicago (Winter)",
    "UTC-8":  "Los Angeles (Winter)",
    "UTC+5:30": "Indien",
    "UTC+9":  "Tokio / Seoul",
}

# ─── PDF Metadata ─────────────────────────────────────────────────────────────

def extract_pdf_metadata(path: str) -> dict:
    """
    Extrahiert Metadaten aus einer PDF-Datei.
    Returns: {title, author, creator, producer, created, modified,
              subject, keywords, page_count, has_gps, raw_meta}
    """
    result = {
        "format": "PDF",
        "title": None,
        "author": None,
        "creator": None,    # Programm das das Dokument erstellt hat
        "producer": None,   # Programm das die PDF generiert hat
        "created": None,
        "modified": None,
        "subject": None,
        "keywords": None,
        "page_count": None,
        "has_gps": False,
        "gps_lat": None,
        "gps_lon": None,
        "raw_meta": {},
        "error": None,
    }

    if not os.path.isfile(path):
        result["error"] = "Datei nicht gefunden"
        return result

    # Methode 1: PyPDF2
    if _PYPDF2_OK:
        try:
            reader = _PdfReader(path)
            meta = reader.metadata or {}
            result["page_count"] = len(reader.pages)

            def _clean(v):
                return str(v).strip() if v else None

            result["title"]    = _clean(meta.get("/Title"))
            result["author"]   = _clean(meta.get("/Author"))
            result["creator"]  = _clean(meta.get("/Creator"))
            result["producer"] = _clean(meta.get("/Producer"))
            result["subject"]  = _clean(meta.get("/Subject"))
            result["keywords"] = _clean(meta.get("/Keywords"))

            # Dates — PDF-Format: D:YYYYMMDDHHmmSSOHH'mm'
            def _pdf_date(d_str):
                if not d_str:
                    return None
                d_str = str(d_str).strip().lstrip("D:")
                try:
                    return datetime.datetime.strptime(d_str[:14], "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    return d_str[:20]

            result["created"]  = _pdf_date(meta.get("/CreationDate"))
            result["modified"] = _pdf_date(meta.get("/ModDate"))

            # Raw
            result["raw_meta"] = {k: str(v)[:200] for k, v in meta.items()}
            return result

        except Exception as e:
            result["error"] = f"PyPDF2: {e}"

    # Methode 2: Manuelles Byte-Scanning für /Author etc.
    try:
        with open(path, "rb") as f:
            content = f.read(65536)  # Erste 64KB reichen für Metadaten

        text = content.decode("latin-1", errors="replace")

        # XMP / Info-Dictionary Patterns
        patterns = {
            "author":   [r'/Author\s*\(([^)]+)\)', r'<dc:creator[^>]*>([^<]+)</dc:creator>',
                         r'<pdf:Author>([^<]+)</pdf:Author>'],
            "creator":  [r'/Creator\s*\(([^)]+)\)', r'<xmp:CreatorTool>([^<]+)</xmp:CreatorTool>'],
            "producer": [r'/Producer\s*\(([^)]+)\)', r'<pdf:Producer>([^<]+)</pdf:Producer>'],
            "title":    [r'/Title\s*\(([^)]+)\)', r'<dc:title[^>]*>[^<]*<[^>]+>([^<]+)<'],
            "created":  [r'/CreationDate\s*\(D:(\d{14})', r'<xmp:CreateDate>([^<]+)</xmp:CreateDate>'],
            "modified": [r'/ModDate\s*\(D:(\d{14})',      r'<xmp:ModifyDate>([^<]+)</xmp:ModifyDate>'],
        }

        for field, pats in patterns.items():
            if result.get(field):
                continue
            for pat in pats:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    result[field] = m.group(1).strip()[:200]
                    break

        # Page count heuristic
        if result["page_count"] is None:
            m = re.search(r'/Count\s+(\d+)', text)
            if m:
                result["page_count"] = int(m.group(1))

    except Exception as e:
        if not result["error"]:
            result["error"] = f"Byte-Scan: {e}"

    return result


# ─── DOCX / Office XML Metadata ───────────────────────────────────────────────

def extract_docx_metadata(path: str) -> dict:
    """
    Extrahiert Metadaten aus DOCX, XLSX, PPTX (alle sind ZIP + XML).
    Returns: {format, title, author, last_author, company, created, modified,
              revision_count, revision_authors, template, manager, raw_core, raw_app}
    """
    result = {
        "format": "DOCX",
        "title": None,
        "author": None,
        "last_author": None,
        "company": None,
        "manager": None,
        "template": None,
        "created": None,
        "modified": None,
        "revision_count": None,
        "revision_authors": [],
        "word_count": None,
        "page_count": None,
        "raw_core": {},
        "raw_app": {},
        "error": None,
    }

    if not os.path.isfile(path):
        result["error"] = "Datei nicht gefunden"
        return result

    # Dateityp erkennen
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        result["format"] = "XLSX"
    elif ext in (".pptx", ".pptm"):
        result["format"] = "PPTX"
    else:
        result["format"] = "DOCX"

    try:
        with zipfile.ZipFile(path, "r") as z:
            # Core Properties (docProps/core.xml)
            if "docProps/core.xml" in z.namelist():
                with z.open("docProps/core.xml") as f:
                    tree = ET.parse(f)
                    root = tree.getroot()

                ns = {
                    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
                    "dc": "http://purl.org/dc/elements/1.1/",
                    "dcterms": "http://purl.org/dc/terms/",
                }

                def _get(tag, ns_key, ns_map):
                    el = root.find(f"{{{ns_map[ns_key]}}}{tag}")
                    return el.text.strip() if el is not None and el.text else None

                result["title"]       = _get("title", "dc", ns)
                result["author"]      = _get("creator", "dc", ns)
                result["last_author"] = root.findtext(
                    "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}lastModifiedBy"
                )
                result["created"]     = _get("created", "dcterms", ns)
                result["modified"]    = _get("modified", "dcterms", ns)

                rev_el = root.find("{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}revision")
                if rev_el is not None and rev_el.text:
                    try:
                        result["revision_count"] = int(rev_el.text.strip())
                    except ValueError:
                        pass

                # Store raw
                for child in root:
                    tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    if child.text:
                        result["raw_core"][tag] = child.text.strip()[:200]

            # App Properties (docProps/app.xml)
            if "docProps/app.xml" in z.namelist():
                with z.open("docProps/app.xml") as f:
                    tree = ET.parse(f)
                    root_app = tree.getroot()

                app_ns = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"

                def _app(tag):
                    el = root_app.find(f"{{{app_ns}}}{tag}")
                    return el.text.strip() if el is not None and el.text else None

                result["company"]    = _app("Company")
                result["manager"]    = _app("Manager")
                result["template"]   = _app("Template")
                result["word_count"] = _app("Words")
                result["page_count"] = _app("Pages")

                # AppVersion enthält manchmal Office-Version
                app_ver = _app("AppVersion")
                if app_ver:
                    result["raw_app"]["AppVersion"] = app_ver

                # Application name
                app_name = _app("Application")
                if app_name:
                    result["raw_app"]["Application"] = app_name

                for child in root_app:
                    tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    if child.text:
                        result["raw_app"][tag] = child.text.strip()[:200]

            # Revisions aus word/document.xml (versteckte Änderungen)
            rev_authors = set()
            for xml_name in z.namelist():
                if xml_name.startswith("word/") and xml_name.endswith(".xml"):
                    try:
                        with z.open(xml_name) as f:
                            content = f.read(32768).decode("utf-8", errors="replace")
                        # w:author in Änderungsnachverfolgung
                        for m in re.finditer(r'w:author="([^"]+)"', content):
                            rev_authors.add(m.group(1))
                    except Exception:
                        pass

            result["revision_authors"] = list(rev_authors)[:10]

    except zipfile.BadZipFile:
        result["error"] = "Keine gültige Office-Datei (kein ZIP)"
    except Exception as e:
        result["error"] = str(e)

    return result


# ─── Timezone Fingerprinting ──────────────────────────────────────────────────

def fingerprint_timezone(dt_str: str) -> Optional[str]:
    """
    Versucht Timezone aus Timestamp-String zu extrahieren.
    PDF: D:20230615143022+03'00' → UTC+3 → Moskau
    """
    if not dt_str:
        return None

    # PDF-Format: +03'00' oder -05'00'
    m = re.search(r'([+-]\d{2})\'(\d{2})\'?$', str(dt_str))
    if m:
        hours = int(m.group(1))
        mins  = int(m.group(2))
        if hours >= 0:
            tz_str = f"UTC+{hours}" if mins == 0 else f"UTC+{hours}:{mins:02d}"
        else:
            tz_str = f"UTC{hours}" if mins == 0 else f"UTC{hours}:{mins:02d}"
        region = _TZ_REGIONS.get(tz_str, "")
        return f"{tz_str}" + (f" ({region})" if region else "")

    # ISO-Format: 2023-06-15T14:30:22+03:00
    m = re.search(r'([+-]\d{2}):(\d{2})$', str(dt_str))
    if m:
        hours = int(m.group(1))
        mins  = int(m.group(2))
        tz_str = f"UTC+{hours}" if hours >= 0 else f"UTC{hours}"
        region = _TZ_REGIONS.get(tz_str, "")
        return f"{tz_str}" + (f" ({region})" if region else "")

    return None


# ─── Full Document OSINT Pipeline ─────────────────────────────────────────────

def analyze_document(path: str) -> dict:
    """
    Vollständige Dokument-OSINT-Analyse.

    Returns: {
        file, format, meta, timezone, software_hint,
        author_profile, flags, summary, risk_indicators
    }
    """
    result = {
        "file": path,
        "format": "UNBEKANNT",
        "meta": {},
        "timezone": None,
        "software_hint": None,
        "author_profile": {},
        "flags": [],
        "summary": "",
        "risk_indicators": [],
    }

    if not os.path.isfile(path):
        result["flags"].append("Datei nicht gefunden")
        return result

    ext = os.path.splitext(path)[1].lower()
    flags = []

    # Format erkennen + Metadaten extrahieren
    if ext == ".pdf":
        result["format"] = "PDF"
        meta = extract_pdf_metadata(path)
    elif ext in (".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm"):
        result["format"] = ext.upper().lstrip(".")
        meta = extract_docx_metadata(path)
    else:
        result["flags"].append(f"Unbekanntes Format: {ext}")
        result["summary"] = f"Format {ext} nicht unterstützt"
        return result

    result["meta"] = meta

    # ── Software-Fingerprinting ─────────────────────────────────────────────
    sw_fields = [
        meta.get("creator", ""),
        meta.get("producer", ""),
        meta.get("raw_app", {}).get("Application", ""),
        meta.get("raw_meta", {}).get("/Creator", ""),
        meta.get("raw_meta", {}).get("/Producer", ""),
    ]
    sw_combined = " ".join(str(s) for s in sw_fields if s).lower()

    for sw_key, sw_label in _SOFTWARE_HINTS.items():
        if sw_key in sw_combined:
            result["software_hint"] = sw_label
            flags.append(f"Software: {sw_label}")
            break

    if not result["software_hint"] and sw_combined.strip():
        result["software_hint"] = sw_combined.strip()[:80]
        flags.append(f"Software: {result['software_hint']}")

    # ── Timezone-Fingerprinting ─────────────────────────────────────────────
    for dt_field in ["created", "modified"]:
        tz = fingerprint_timezone(meta.get(dt_field, ""))
        if tz:
            result["timezone"] = tz
            flags.append(f"Timezone: {tz}")
            break

    # ── Autor-Profil ────────────────────────────────────────────────────────
    author_profile = {}
    if meta.get("author"):
        author_profile["primary_author"] = meta["author"]
        flags.append(f"Autor: {meta['author']}")
    if meta.get("last_author"):
        author_profile["last_editor"] = meta["last_author"]
        if meta["last_author"] != meta.get("author"):
            flags.append(f"Letzter Editor: {meta['last_author']}")
    if meta.get("company"):
        author_profile["company"] = meta["company"]
        flags.append(f"Organisation: {meta['company']}")
    if meta.get("manager"):
        author_profile["manager"] = meta["manager"]
        flags.append(f"Manager: {meta['manager']}")
    if meta.get("revision_authors"):
        author_profile["revision_authors"] = meta["revision_authors"]
        flags.append(f"Weitere Autoren (Revisionen): {', '.join(meta['revision_authors'][:3])}")

    result["author_profile"] = author_profile

    # ── Timestamps ──────────────────────────────────────────────────────────
    if meta.get("created"):
        flags.append(f"Erstellt: {str(meta['created'])[:19]}")
    if meta.get("modified"):
        flags.append(f"Geändert: {str(meta['modified'])[:19]}")

    # ── Revisions-Anomalien ─────────────────────────────────────────────────
    rev_count = meta.get("revision_count")
    if rev_count is not None:
        flags.append(f"Revisionen: {rev_count}")
        if rev_count > 100:
            flags.append("⚠️ Sehr viele Revisionen — möglicherweise redigiert/bereinigt")
        elif rev_count == 1:
            flags.append("ℹ️ Nur 1 Revision — frisch erstellt oder Metadaten bereinigt")

    # ── Risiko-Indikatoren ──────────────────────────────────────────────────
    risk = []

    # Russische/staatliche Software
    if result["software_hint"] and any(kw in result["software_hint"].lower()
                                        for kw in ["мойофис", "р7", "аскон", "libreoffice"]):
        risk.append("Russische/staatliche Office-Software erkannt")

    # Kyrillische Autoren
    author_text = " ".join([
        str(meta.get("author", "")),
        str(meta.get("last_author", "")),
        " ".join(meta.get("revision_authors", [])),
    ])
    if re.search(r'[Ѐ-ӿ]', author_text):
        risk.append("Kyrillische Zeichen in Autoren-Metadaten")

    # Timezone Moskau/Minsk
    if result["timezone"] and any(x in result["timezone"] for x in ["UTC+3", "UTC+2", "Moskau", "Minsk"]):
        risk.append(f"Timezone deutet auf RU/UA/BY: {result['timezone']}")

    # Metadaten komplett leer = Bereinigung
    meta_fields = [meta.get("author"), meta.get("creator"), meta.get("created")]
    if all(f is None for f in meta_fields):
        risk.append("⚠️ Alle Metadaten leer — möglicherweise absichtlich bereinigt")

    result["risk_indicators"] = risk
    result["flags"] = flags

    # ── Summary ─────────────────────────────────────────────────────────────
    lines = [f"Dokument: {os.path.basename(path)} ({result['format']})"]
    if author_profile.get("primary_author"):
        lines.append(f"  Autor: {author_profile['primary_author']}")
    if author_profile.get("company"):
        lines.append(f"  Organisation: {author_profile['company']}")
    if result["software_hint"]:
        lines.append(f"  Software: {result['software_hint']}")
    if result["timezone"]:
        lines.append(f"  Timezone: {result['timezone']}")
    for ri in risk:
        lines.append(f"  ⚠️ {ri}")
    result["summary"] = "\n".join(lines)

    return result


def analyze_document_url(url: str, tmp_dir: str = "/tmp") -> dict:
    """
    Lädt Dokument von URL und analysiert es.
    """
    import urllib.request
    result = {"url": url, "error": None, "analysis": None}

    ext = os.path.splitext(url.split("?")[0])[1].lower() or ".pdf"
    if ext not in (".pdf", ".docx", ".xlsx", ".pptx", ".docm", ".xlsm"):
        result["error"] = f"Unsupported format: {ext}"
        return result

    try:
        os.makedirs(tmp_dir, exist_ok=True)
        fname = f"nexus_doc_{hashlib.md5(url.encode()).hexdigest()[:8]}{ext}"
        tmp_path = os.path.join(tmp_dir, fname)

        req = urllib.request.Request(url, headers={"User-Agent": "NEXUS-OSINT/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            with open(tmp_path, "wb") as f:
                f.write(resp.read())

        result["analysis"] = analyze_document(tmp_path)

        try:
            os.remove(tmp_path)
        except Exception:
            pass

    except Exception as e:
        result["error"] = str(e)

    return result


# ─── LLM Context Formatter ───────────────────────────────────────────────────

def docmeta_for_llm(analyses: list) -> str:
    """
    Formatiert Dokument-OSINT für LLM-Kontext.
    """
    if not analyses:
        return ""

    lines = ["## Dokument-Metadaten OSINT", ""]

    for a in analyses[:5]:
        if isinstance(a, dict) and a.get("analysis"):
            a = a["analysis"]
        if not isinstance(a, dict):
            continue

        fname = os.path.basename(a.get("file", "unbekannt"))
        lines.append(f"**{fname}** ({a.get('format', '?')})")

        ap = a.get("author_profile", {})
        if ap.get("primary_author"):
            lines.append(f"  Autor: {ap['primary_author']}")
        if ap.get("company"):
            lines.append(f"  Organisation: {ap['company']}")
        if a.get("software_hint"):
            lines.append(f"  Software: {a['software_hint']}")
        if a.get("timezone"):
            lines.append(f"  Timezone: {a['timezone']}")
        for ri in a.get("risk_indicators", [])[:3]:
            lines.append(f"  ⚠️ {ri}")
        lines.append("")

    return "\n".join(lines)


# ─── Self-Test ────────────────────────────────────────────────────────────────

def _self_test():
    print("=== nexus_docmeta.py Selbsttest ===")

    # Test 1: Timezone Fingerprinting
    print("\n[1] Timezone-Fingerprinting")
    tests = [
        ("D:20230615143022+03'00'", "UTC+3"),   # Moskau
        ("2023-06-15T14:30:22+02:00", "UTC+2"), # Kiew/Berlin
        ("D:20230615143022-05'00'", "UTC-5"),   # New York
        ("D:20230615143022Z", None),             # UTC (kein Offset)
    ]
    for dt_str, expected in tests:
        result = fingerprint_timezone(dt_str)
        status = "✓" if (result and expected in result) or (result is None and expected is None) else "≈"
        print(f"  {status} '{dt_str[:25]}' → {result}")

    # Test 2: Software-Fingerprint
    print("\n[2] Software-Fingerprinting")
    sw_tests = [
        "Microsoft Word for Windows",
        "LibreOffice/7.5.4.2$Windows",
        "Adobe Acrobat 11.0",
        "МойОфис Текст 3.0",
    ]
    for sw in sw_tests:
        sw_lower = sw.lower()
        hint = None
        for key, label in _SOFTWARE_HINTS.items():
            if key in sw_lower:
                hint = label
                break
        print(f"  '{sw[:40]}' → {hint or 'Unbekannt'}")

    # Test 3: Testdokument erstellen und analysieren
    print("\n[3] DOCX-Analyse (synthetisch)")
    # Minimales DOCX erstellen
    import io

    def _make_minimal_docx(author="Test Author", company="Test Organization",
                           last_modified_by="Second Editor", timezone_offset="+03:00"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            core_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:dcterms="http://purl.org/dc/terms/">
  <dc:creator>{author}</dc:creator>
  <cp:lastModifiedBy>{last_modified_by}</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">2023-06-15T14:30:22{timezone_offset}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">2023-06-16T09:15:00{timezone_offset}</dcterms:modified>
  <cp:revision>42</cp:revision>
</cp:coreProperties>'''
            app_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">
  <Application>Microsoft Word</Application>
  <Company>{company}</Company>
  <Pages>5</Pages>
  <Words>1234</Words>
</Properties>'''
            z.writestr("docProps/core.xml", core_xml)
            z.writestr("docProps/app.xml", app_xml)
        buf.seek(0)
        return buf.read()

    # Simuliere ein Dokument mit UTC+3 Timezone (Risiko-Indikator)
    # Testdaten sind bewusst neutral gehalten — die Erkennungslogik funktioniert mit echten Docs
    docx_bytes = _make_minimal_docx(
        author="OSINT Test Author",
        company="Test Ministry of Defense",
        last_modified_by="Second Test Editor",
        timezone_offset="+03:00",  # UTC+3 = Moskau-Risikozone — wird erkannt
    )
    with open("/tmp/test_nexus_doc.docx", "wb") as f:
        f.write(docx_bytes)

    analysis = analyze_document("/tmp/test_nexus_doc.docx")
    fmt   = analysis["format"]
    ap    = analysis["author_profile"]
    tz    = analysis["timezone"]
    risks = analysis["risk_indicators"]
    print("  Format:         " + fmt)
    print("  Autor:          " + str(ap.get("primary_author","?")))
    print("  Letzter Editor: " + str(ap.get("last_editor","?")))
    print("  Organisation:   " + str(ap.get("company","?")))
    print("  Timezone:       " + str(tz))
    print("  Risiken:        " + str(risks))

