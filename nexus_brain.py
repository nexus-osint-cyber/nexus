"""
NEXUS - Brain
Verbindung zu Ollama (lokales LLM) inklusive System-Prompt und Gesprächsverlauf.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import List, Dict, Optional

import requests

import config

# Claude API Konfiguration
_CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
_CLAUDE_API_VER = "2023-06-01"


def _use_claude() -> bool:
    """Prüft ob Claude API verwendet werden soll."""
    provider = getattr(config, "LLM_PROVIDER", "auto").lower()
    if provider == "ollama":
        return False
    key = getattr(config, "CLAUDE_API_KEY", "").strip()
    if provider == "claude":
        return bool(key)
    # auto: Claude wenn Key gesetzt
    return bool(key and key.startswith("sk-ant-"))


def _claude_chat(system_prompt: str, messages: list, timeout: int = 60) -> str:
    """
    Direkte Claude API Anfrage.
    messages = Liste von {"role": "user"/"assistant", "content": "..."}
    Der system_prompt wird separat übergeben (Claude API standard).
    """
    api_key    = getattr(config, "CLAUDE_API_KEY", "").strip()
    model      = getattr(config, "CLAUDE_MODEL", "claude-haiku-4-5-20251001")
    max_tokens = getattr(config, "CLAUDE_MAX_TOKENS", 2000)

    if not api_key:
        return ""

    # Filtere system-messages aus messages-Liste (Claude hat separates system-Feld)
    user_msgs = [m for m in messages if m.get("role") in ("user", "assistant")]

    headers = {
        "x-api-key":         api_key,
        "anthropic-version": _CLAUDE_API_VER,
        "content-type":      "application/json",
    }
    payload = {
        "model":      model,
        "max_tokens": max_tokens,
        "system":     system_prompt,
        "messages":   user_msgs,
    }
    try:
        r = requests.post(_CLAUDE_API_URL, json=payload,
                          headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        blocks = data.get("content", [])
        return " ".join(b.get("text", "") for b in blocks
                        if b.get("type") == "text").strip()
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response else "?"
        if status == 401:
            return "[Claude] Ungültiger API-Key – bitte CLAUDE_API_KEY in config.py prüfen."
        elif status == 429:
            return "[Claude] Rate-Limit – bitte kurz warten und erneut versuchen."
        return f"[Claude] HTTP-Fehler {status}"
    except requests.exceptions.Timeout:
        return "[Claude] Timeout – Antwort dauerte zu lang."
    except Exception as exc:
        return f"[Claude] Fehler: {exc}"


class NexusBrain:
    """Kapselt die Kommunikation mit dem lokalen Ollama-Server."""

    def __init__(
        self,
        host: str = config.OLLAMA_HOST,
        model: str = config.OLLAMA_MODEL,
        system_prompt: str = config.SYSTEM_PROMPT,
        timeout: int = config.OLLAMA_TIMEOUT,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.system_prompt = system_prompt
        self.timeout = timeout
        self.history: List[Dict[str, str]] = []

    # -------------------------------------------------
    # Verfügbarkeitsprüfung
    # -------------------------------------------------
    def is_available(self) -> bool:
        """Prüft, ob der Ollama-Server erreichbar ist."""
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def model_available(self) -> bool:
        """Prüft, ob das gewünschte Modell installiert ist."""
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=5)
            if r.status_code != 200:
                return False
            data = r.json().get("models", [])
            names = [m.get("name", "") for m in data]
            # Ollama hängt oft ":latest" an
            return any(n == self.model or n.startswith(self.model + ":") for n in names)
        except requests.RequestException:
            return False

    # -------------------------------------------------
    # Nachricht erzeugen
    # -------------------------------------------------
    def _build_messages(self, user_text: str, search_context: Optional[str]) -> List[Dict[str, str]]:
        # Aktuelles Datum in System-Prompt - damit das Modell weiss was "heute" ist
        today = datetime.now().strftime("%A, %d. %B %Y")
        dated_prompt = self.system_prompt + f"\n\nAKTUELLES DATUM: {today}"
        messages: List[Dict[str, str]] = [{"role": "system", "content": dated_prompt}]
        messages.extend(self.history)

        if search_context:
            user_content = (
                "=== AKTUELLE INTERNET-RECHERCHE ===\n"
                "PFLICHT: Beantworte die Frage AUSSCHLIESSLICH auf Basis dieser Quellen.\n"
                "VERBOT: Nutze KEIN Trainingswissen fuer Fakten die hier enthalten sind.\n"
                "Falls die Quellen die Frage beantworten: Nenne zuerst die Antwort, dann die Quelle.\n"
                "Falls die Quellen veraltet oder irrelevant sind: Sage das explizit.\n"
                "=================================\n"
                f"{search_context}\n"
                "=================================\n\n"
                f"FRAGE: {user_text}"
            )
        else:
            user_content = user_text

        messages.append({"role": "user", "content": user_content})
        return messages

    def chat(self, user_text: str, search_context: Optional[str] = None) -> str:
        """Schickt eine Nutzeranfrage an Ollama und gibt die Antwort zurück."""
        messages = self._build_messages(user_text, search_context)

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": config.OLLAMA_KEEP_ALIVE,
            "options": config.OLLAMA_OPTIONS,
        }

        # ── Claude API (wenn konfiguriert) ────────────────────────────────────
        if _use_claude():
            today = datetime.now().strftime("%A, %d. %B %Y")
            sys_prompt = self.system_prompt + f"\n\nAKTUELLES DATUM: {today}"
            answer = _claude_chat(sys_prompt, payload["messages"], timeout=self.timeout)
            if answer and not answer.startswith("[Claude]"):
                self.history.append({"role": "user", "content": user_text})
                self.history.append({"role": "assistant", "content": answer})
                max_turns = 24
                if len(self.history) > max_turns:
                    self.history = self.history[-max_turns:]
                return answer
            # Fehler von Claude zurückgeben wenn kein Ollama-Fallback sinnvoll
            if answer.startswith("[Claude]"):
                return answer
            # Sonst: Ollama-Fallback

        try:
            r = requests.post(
                f"{self.host}/api/chat",
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            r.raise_for_status()
        except requests.Timeout:
            return "Die Antwort hat zu lange gedauert. Bitte erneut versuchen."
        except requests.ConnectionError:
            return ("Ollama-Server nicht erreichbar. "
                    "Bitte stellen Sie sicher, dass Ollama läuft.")
        except requests.RequestException as exc:
            return f"Kommunikationsfehler: {exc}"

        try:
            data = r.json()
            answer = data.get("message", {}).get("content", "").strip()
        except (ValueError, KeyError):
            return "Antwort des Modells konnte nicht interpretiert werden."

        if not answer:
            return "Keine Antwort erhalten. Bitte Anfrage wiederholen."

        # Verlauf pflegen, aber den Suchkontext nicht in die Historie schreiben,
        # damit das Kontextfenster sauber bleibt.
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": answer})

        # Historie begrenzen (z.B. die letzten 12 Wechsel)
        max_turns = 24
        if len(self.history) > max_turns:
            self.history = self.history[-max_turns:]

        return answer


    def chat_analysis(self, user_text: str, search_context: Optional[str] = None) -> str:
        """
        Objektives Lagebild im Intelligence-Analyst-Stil.
        Verwendet SYSTEM_PROMPT_ANALYSIS statt des normalen Prompts.
        Strenge Quellenbindung: nur was in search_context steht darf behauptet werden.
        """
        analysis_prompt = getattr(config, "SYSTEM_PROMPT_ANALYSIS", self.system_prompt)
        today = datetime.now().strftime("%A, %d. %B %Y")
        full_prompt = analysis_prompt + f"\n\nAKTUELLES DATUM: {today}"

        messages: List[Dict[str, str]] = [{"role": "system", "content": full_prompt}]

        if search_context:
            user_content = (
                f"RECHERCHE-ERGEBNISSE (deine EINZIGE Informationsquelle):\n"
                f"Alles was nicht hier steht, darfst du NICHT behaupten.\n\n"
                f"{search_context}\n\n"
                f"AUFTRAG: Erstelle ein objektives Lagebild zu dieser Anfrage:\n"
                f"{user_text}"
            )
        else:
            user_content = (
                f"PROBLEM: Es liegen keine aktuellen Suchergebnisse vor.\n"
                f"Teile dem Nutzer mit, dass du ohne aktuelle Quellen kein "
                f"belastbares Lagebild erstellen kannst und schlage vor, "
                f"die Anfrage mit Suchbegriff zu wiederholen.\n\n"
                f"Urspruengliche Anfrage: {user_text}"
            )

        messages.append({"role": "user", "content": user_content})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": config.OLLAMA_KEEP_ALIVE,
            "options": {
                **config.OLLAMA_OPTIONS,
                "temperature": 0.2,   # sehr niedrig: Fakten, kein Kreativitaets-Spielraum
                "num_ctx": max(config.OLLAMA_OPTIONS.get("num_ctx", 8192), 12000),
            },
        }

        # ── Claude API (wenn konfiguriert) ────────────────────────────────────
        if _use_claude():
            answer = _claude_chat(full_prompt, [{"role": "user", "content": user_content}],
                                  timeout=self.timeout)
            if answer and not answer.startswith("[Claude]"):
                return answer
            if answer.startswith("[Claude]"):
                return answer
            # Ollama-Fallback

        try:
            import requests as _req
            r = _req.post(
                f"{self.host}/api/chat",
                data=__import__("json").dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            r.raise_for_status()
            answer = r.json().get("message", {}).get("content", "").strip()
        except Exception as exc:
            return f"Analyse fehlgeschlagen: {exc}"

        if not answer:
            return "Keine Antwort erhalten."

        # Analyse-Antworten NICHT in die Gespraechshistorie aufnehmen
        # (sie haben einen anderen System-Prompt und wuerden das Gespraech kontaminieren)
        return answer

    def reset(self) -> None:
        """Gesprächsverlauf zurücksetzen."""
        self.history.clear()
