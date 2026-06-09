"""
NEXUS - Alert & Benachrichtigungs-Modul
Windows Toast-Notifications + Sound für kritische OSINT-Ereignisse.

Keine Installation nötig — nutzt Windows-interne APIs.
Optional: pip install win10toast  (für bessere Notifications)
"""

from __future__ import annotations
import threading
import time
from typing import Optional

# ── Prioritätsstufen ──────────────────────────────────────────────────────────
PRIORITY_CRITICAL = "KRITISCH"   # Mehrere unabhängige Quellen, bestätigt
PRIORITY_HIGH     = "HOCH"       # Korrelation bestätigt
PRIORITY_MEDIUM   = "MITTEL"     # Einzelquelle, OSINT-relevant
PRIORITY_LOW      = "INFO"       # Hintergrundinformation

# Cooldown: gleiche Meldung nicht mehrfach senden (Sekunden)
_COOLDOWN_SEC = 300
_sent_hashes: dict[str, float] = {}
_lock = threading.Lock()


def _hash_msg(title: str) -> str:
    """Einfacher Hash um Duplikate zu vermeiden."""
    return str(hash(title[:50]))


def _is_cooldown(title: str) -> bool:
    h = _hash_msg(title)
    with _lock:
        last = _sent_hashes.get(h, 0)
        if time.time() - last < _COOLDOWN_SEC:
            return True
        _sent_hashes[h] = time.time()
    return False


# ── Sound-Alerts ─────────────────────────────────────────────────────────────

def _play_sound(priority: str) -> None:
    """Spielt Systemton je nach Priorität."""
    try:
        import winsound
        if priority == PRIORITY_CRITICAL:
            # 3x kurze Töne für kritisch
            for _ in range(3):
                winsound.Beep(880, 200)
                time.sleep(0.1)
        elif priority == PRIORITY_HIGH:
            winsound.Beep(660, 400)
        elif priority == PRIORITY_MEDIUM:
            winsound.Beep(440, 300)
        else:
            winsound.MessageBeep(0)
    except Exception:
        pass  # Kein Sound-Gerät oder kein Windows


# ── Windows Toast Notification ─────────────────────────────────────────────────

def _toast_win10toast(title: str, message: str, duration: int = 8) -> bool:
    """Versucht win10toast zu nutzen."""
    try:
        from win10toast import ToastNotifier  # type: ignore
        notifier = ToastNotifier()
        notifier.show_toast(
            title,
            message,
            icon_path=None,
            duration=duration,
            threaded=True,
        )
        return True
    except Exception:
        return False


def _toast_powershell(title: str, message: str) -> bool:
    """Nutzt PowerShell für Windows Toast — kein pip nötig."""
    try:
        import subprocess
        script = (
            f'Add-Type -AssemblyName System.Windows.Forms;'
            f'$n = New-Object System.Windows.Forms.NotifyIcon;'
            f'$n.Icon = [System.Drawing.SystemIcons]::Information;'
            f'$n.Visible = $true;'
            f'$n.ShowBalloonTip(8000, "{title}", "{message}", [System.Windows.Forms.ToolTipIcon]::Warning);'
            f'Start-Sleep -s 10;'
            f'$n.Dispose()'
        )
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-Command", script],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        return True
    except Exception:
        return False


def _toast_terminal(title: str, message: str, priority: str) -> None:
    """Fallback: Farbige Terminal-Ausgabe."""
    colors = {
        PRIORITY_CRITICAL: "\033[91m",  # Hellrot
        PRIORITY_HIGH:     "\033[93m",  # Gelb
        PRIORITY_MEDIUM:   "\033[96m",  # Cyan
        PRIORITY_LOW:      "\033[92m",  # Grün
    }
    reset = "\033[0m"
    c = colors.get(priority, "\033[97m")
    border = "═" * 60
    print(f"\n{c}{border}")
    print(f"  ⚠ NEXUS ALERT [{priority}]")
    print(f"  {title}")
    print(f"  {message[:100]}")
    print(f"{border}{reset}\n")


# ── Haupt-Alert-Funktion ──────────────────────────────────────────────────────

def send_alert(
    title: str,
    message: str,
    priority: str = PRIORITY_HIGH,
    sound: bool = True,
    no_duplicate: bool = True,
) -> None:
    """
    Sendet einen NEXUS-Alert über alle verfügbaren Kanäle.
    Thread-safe, mit Cooldown gegen Spam.
    """
    if no_duplicate and _is_cooldown(title):
        return

    def _send():
        # 1. Sound
        if sound:
            _play_sound(priority)

        # 2. Toast Notification (beste verfügbare Methode)
        toast_sent = _toast_win10toast(title, message)
        if not toast_sent:
            toast_sent = _toast_powershell(title, message)

        # 3. Terminal-Ausgabe immer (als Log)
        _toast_terminal(title, message, priority)

    # Im Hintergrund senden damit NEXUS nicht blockiert
    t = threading.Thread(target=_send, daemon=True)
    t.start()


# ── Vordefinierte Alert-Templates ─────────────────────────────────────────────

def alert_correlation(topic: str, confidence: str, n_sources: int, location: str = "") -> None:
    """Alert für korrelierte Ereignisse."""
    priority = PRIORITY_CRITICAL if confidence == "HOCH" else PRIORITY_HIGH
    loc_str = f" bei {location}" if location else ""
    send_alert(
        title=f"⚡ NEXUS KORRELATION [{confidence}]{loc_str}",
        message=f"Thema: {topic} · {n_sources} unabhängige Quellen bestätigt",
        priority=priority,
        sound=True,
    )


def alert_watchlist_hit(keyword: str, headline: str, source: str) -> None:
    """Alert wenn Watchlist-Keyword auftaucht."""
    send_alert(
        title=f"👁 WATCHLIST: {keyword}",
        message=f"{headline[:80]} ({source})",
        priority=PRIORITY_HIGH,
        sound=True,
    )


def alert_suspicious_flight(callsign: str, reason: str, region: str = "") -> None:
    """Alert bei auffälligem Flugzeug."""
    send_alert(
        title=f"✈ AUFFÄLLIGES FLUGZEUG: {callsign}",
        message=f"{reason[:80]}" + (f" · Region: {region}" if region else ""),
        priority=PRIORITY_MEDIUM,
        sound=True,
    )


def alert_firms_fire(frp: float, location: str = "") -> None:
    """Alert bei sehr intensivem Satelliten-Brandereignis."""
    if frp < 500:
        return  # Nur sehr intensive Brände alertieren
    send_alert(
        title=f"🔥 SATELLITE BRAND-ALARM: FRP {frp:.0f}MW",
        message=f"NASA FIRMS VIIRS bestätigt{' · ' + location if location else ''}",
        priority=PRIORITY_CRITICAL,
        sound=True,
    )


def alert_earthquake(magnitude: float, place: str) -> None:
    """Alert bei starkem Erdbeben."""
    if magnitude < 5.0:
        return
    priority = PRIORITY_CRITICAL if magnitude >= 6.5 else PRIORITY_HIGH
    send_alert(
        title=f"🌍 ERDBEBEN M{magnitude}: {place[:40]}",
        message=f"USGS bestätigt · M{magnitude} · {place}",
        priority=priority,
        sound=True,
    )


# ── Direktaufruf zum Testen ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("NEXUS Alert-System Test")
    print("─" * 40)

    print("Test 1: INFO-Alert (kein Sound)...")
    send_alert("Test INFO", "Dies ist ein Test-Alert", PRIORITY_LOW, sound=False)
    time.sleep(1)

    print("Test 2: HOCH-Alert mit Sound...")
    alert_correlation("Luftangriff Ukraine", "HOCH", 3, "Charkiw")
    time.sleep(2)

    print("Test 3: KRITISCH-Alert...")
    alert_firms_fire(750, "Charkiw Oblast")
    time.sleep(2)

    print("\n✅ Alert-Test abgeschlossen.")
    print("Falls keine Benachrichtigung erschien:")
    print("  pip install win10toast")
