"""
NEXUS - Voice
Spracheingabe (sounddevice + Whisper/Google/Vosk) und Sprachausgabe (edge-tts / pyttsx3).

=== Interrupt ===
voice.stop() setzt _stop_event und beendet laufende TTS sofort.

=== pyttsx3 / Windows / Python 3.14 ===
pyttsx3.init() im Hauptprozess loest sys.exit(0) aus (comtypes-Bug).
Alle pyttsx3-Operationen laufen ausschliesslich in Subprozessen.

=== STT-Backends (Prioritaet) ===
  whisper: faster-whisper lokal (beste Qualitaet, offline)
  vosk:    Vosk lokal (schnell, offline, pip install vosk)
  google:  SpeechRecognition + Google-API (online, Standard)

=== Wake-Word ===
  NexusWakeWord: Hintergrund-Thread, ruft callback() bei Erkennung auf.
  Standard-Schluesselwort: config.WAKE_WORD ("nexus")

=== Oeffentliche API ===
  voice_status()            -> Dict
  print_voice_status()      -> None
  NexusEars.listen()        -> str|None
  NexusVoice.speak(text)    -> None
  NexusVoice.speak_chunked() -> None
  NexusVoice.stop()         -> None
  NexusWakeWord.start/stop()
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, Dict, List, Optional

import config

logger = logging.getLogger(__name__)


# =====================================================
# TEXT-BEREINIGUNG FUER SPRACHAUSGABE
# =====================================================

def _clean_for_speech(text: str) -> str:
    """Entfernt Markdown-Formatierung vor der TTS-Ausgabe."""
    if not text:
        return text
    text = re.sub(r'```[^\n]*\n(.*?)```', r'Hier ist der Code: \1', text, flags=re.DOTALL)
    text = re.sub(r'```', '', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[-*]+\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'__(.+?)__', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\*([^\n*]+?)\*', r'\1', text)
    text = re.sub(r'_([^\n_]+?)_', r'\1', text)
    text = re.sub(r'`([^`\n]+?)`', r'\1', text)
    text = re.sub(r'[*_`]', '', text)
    text = re.sub(r'\n{2,}', '. ', text)
    text = re.sub(r'\n', '. ', text)
    text = re.sub(r'\.\s*\.+', '.', text)
    text = re.sub(r',\s*\.', '.', text)
    text = re.sub(r'\.\s*,', ',', text)
    text = re.sub(r'  +', ' ', text)
    return text.strip()


# =====================================================
# SPRACHEINGABE - Backend 1: sounddevice (empfohlen)
# =====================================================

class _EarsSounddevice:
    """Mikrofon via sounddevice + numpy. Unterstuetzt Google, Whisper und Vosk."""

    def __init__(self) -> None:
        import sounddevice as sd
        import numpy as np
        import speech_recognition as sr

        self._sd = sd
        self._np = np
        self._sr = sr
        self._whisper_model = None
        self._vosk_model = None

        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = config.STT_ENERGY_THRESHOLD

        self.sample_rate = 16000
        self.sample_width = 2
        self.channels = 1
        self.chunk_ms = 30
        self.chunk_samples = int(self.sample_rate * self.chunk_ms / 1000)

        self.energy_threshold = max(300, int(config.STT_ENERGY_THRESHOLD))
        self.silence_chunks_to_stop = int(
            (config.STT_PAUSE_THRESHOLD * 1000) / self.chunk_ms
        )
        self.timeout_chunks = int((config.STT_TIMEOUT * 1000) / self.chunk_ms)
        self.max_chunks = int((config.STT_PHRASE_TIME_LIMIT * 1000) / self.chunk_ms)
        self._device_ok = False

    def initialize_microphone(self) -> bool:
        try:
            devices = self._sd.query_devices()
            inputs = [d for d in devices if d.get("max_input_channels", 0) > 0]
            if not inputs:
                print("[NEXUS] Kein Eingabegeraet gefunden.", file=sys.stderr)
                return False
            self._calibrate_noise()
            self._device_ok = True
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[NEXUS] Mikrofonfehler: {exc}", file=sys.stderr)
            return False

    def _calibrate_noise(self) -> None:
        try:
            frames = int(self.sample_rate * 1.0 / self.chunk_samples)
            energies = []
            with self._sd.InputStream(
                samplerate=self.sample_rate, channels=self.channels, dtype="int16"
            ) as stream:
                for _ in range(frames):
                    data, _ = stream.read(self.chunk_samples)
                    s = data.flatten().astype(self._np.float32)
                    rms = float(self._np.sqrt(self._np.mean(s * s)))
                    energies.append(rms)
            if energies:
                avg = sum(energies) / len(energies)
                self.energy_threshold = max(300, int(avg * 3.5))
                if config.VERBOSE:
                    print(f"[NEXUS] Mikrofon kalibriert: Schwelle={self.energy_threshold}", flush=True)
        except Exception:  # noqa: BLE001
            pass

    def _rms(self, samples) -> float:
        if samples.size == 0:
            return 0.0
        x = samples.astype(self._np.float32)
        return float(self._np.sqrt(self._np.mean(x * x)))

    def _recognize_google(self, audio_data) -> Optional[str]:
        sr = self._sr
        try:
            text = self.recognizer.recognize_google(audio_data, language=config.STT_LANGUAGE)
            return text.strip() if text else None
        except sr.UnknownValueError:
            if config.VERBOSE:
                print("[NEXUS] Sprache nicht verstanden.", flush=True)
            return None
        except sr.RequestError as exc:
            print(f"[NEXUS] Google-STT nicht erreichbar: {exc}", file=sys.stderr)
            return None

    def _recognize_whisper(self, audio_bytes: bytes) -> Optional[str]:
        """Lokale Transkription via faster-whisper (besser als Google, offline)."""
        import wave
        if self._whisper_model is None:
            try:
                from faster_whisper import WhisperModel
                model_name = getattr(config, "WHISPER_MODEL", "small")
                if config.VERBOSE:
                    print(f"[NEXUS] Lade Whisper-Modell '{model_name}' ...", flush=True)
                self._whisper_model = WhisperModel(model_name, device="cpu", compute_type="int8")
                if config.VERBOSE:
                    print("[NEXUS] Whisper bereit.", flush=True)
            except ImportError:
                print("[NEXUS] faster-whisper fehlt -> Fallback Google.", file=sys.stderr)
                audio_data = self._sr.AudioData(audio_bytes, self.sample_rate, self.sample_width)
                return self._recognize_google(audio_data)

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        try:
            with wave.open(tmp.name, "wb") as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(self.sample_width)
                wf.setframerate(self.sample_rate)
                wf.writeframes(audio_bytes)
            segments, _ = self._whisper_model.transcribe(
                tmp.name, language="de", vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 400}, beam_size=5,
            )
            text = " ".join(s.text.strip() for s in segments)
            return text.strip() if text.strip() else None
        except Exception as exc:  # noqa: BLE001
            print(f"[NEXUS] Whisper-Fehler: {exc}", file=sys.stderr)
            return None
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def _recognize_vosk(self, audio_bytes: bytes) -> Optional[str]:
        """
        Lokale Offline-Transkription via Vosk.
        Installation: pip install vosk --break-system-packages
        Modell (DE, ~50MB): https://alphacephei.com/vosk/models -> vosk-model-small-de-0.15
        config.py: VOSK_MODEL_PATH = r"C:/vosk-model-small-de-0.15"
        """
        try:
            import vosk
        except ImportError:
            if config.VERBOSE:
                print("[NEXUS] vosk nicht installiert -> Fallback Google.", file=sys.stderr)
            audio_data = self._sr.AudioData(audio_bytes, self.sample_rate, self.sample_width)
            return self._recognize_google(audio_data)

        model_path = getattr(config, "VOSK_MODEL_PATH", "")
        if not model_path or not os.path.isdir(model_path):
            if config.VERBOSE:
                print("[NEXUS] VOSK_MODEL_PATH fehlt -> Fallback Google.", file=sys.stderr)
            audio_data = self._sr.AudioData(audio_bytes, self.sample_rate, self.sample_width)
            return self._recognize_google(audio_data)

        try:
            vosk.SetLogLevel(-1)
            if self._vosk_model is None:
                self._vosk_model = vosk.Model(model_path)
            rec = vosk.KaldiRecognizer(self._vosk_model, self.sample_rate)
            rec.SetWords(True)
            chunk_size = 4096
            for i in range(0, len(audio_bytes), chunk_size):
                rec.AcceptWaveform(audio_bytes[i : i + chunk_size])
            result = json.loads(rec.FinalResult())
            text = result.get("text", "").strip()
            return text if text else None
        except Exception as exc:  # noqa: BLE001
            print(f"[NEXUS] Vosk-Fehler: {exc}", file=sys.stderr)
            return None

    def listen(self) -> Optional[str]:
        if not self._device_ok and not self.initialize_microphone():
            return None

        sd = self._sd
        buffer = bytearray()
        speech_started = False
        silence_chunks = 0
        chunks_recorded = 0
        chunks_waiting = 0

        try:
            with sd.InputStream(
                samplerate=self.sample_rate, channels=self.channels, dtype="int16"
            ) as stream:
                if config.VERBOSE:
                    print("[NEXUS] Hoere zu ...", flush=True)
                while True:
                    data, _ = stream.read(self.chunk_samples)
                    samples = data.flatten()
                    rms = self._rms(samples)
                    if not speech_started:
                        chunks_waiting += 1
                        if rms > self.energy_threshold:
                            speech_started = True
                            buffer.extend(samples.tobytes())
                        elif chunks_waiting > self.timeout_chunks:
                            return None
                    else:
                        buffer.extend(samples.tobytes())
                        chunks_recorded += 1
                        if rms < self.energy_threshold * 0.5:
                            silence_chunks += 1
                            if silence_chunks >= self.silence_chunks_to_stop:
                                break
                        else:
                            silence_chunks = 0
                        if chunks_recorded >= self.max_chunks:
                            break
        except Exception as exc:  # noqa: BLE001
            print(f"[NEXUS] Audio-Fehler: {exc}", file=sys.stderr)
            return None

        if not buffer:
            return None

        audio_bytes = bytes(buffer)
        backend = getattr(config, "STT_BACKEND", "google").lower()

        if backend == "whisper":
            return self._recognize_whisper(audio_bytes)
        if backend == "vosk":
            return self._recognize_vosk(audio_bytes)

        # Standard: Google mit Vosk-Offline-Fallback
        result = None
        try:
            audio_data = self._sr.AudioData(audio_bytes, self.sample_rate, self.sample_width)
            result = self._recognize_google(audio_data)
        except Exception:  # noqa: BLE001
            pass

        if result is None:
            vosk_path = getattr(config, "VOSK_MODEL_PATH", "")
            if vosk_path and os.path.isdir(vosk_path):
                if config.VERBOSE:
                    print("[NEXUS] Google nicht erreichbar -> Vosk Offline-Fallback.", flush=True)
                result = self._recognize_vosk(audio_bytes)

        return result


# =====================================================
# SPRACHEINGABE - Backend 2: pyaudio (Fallback)
# =====================================================

class _EarsPyAudio:
    def __init__(self) -> None:
        import speech_recognition as sr
        self._sr = sr
        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = config.STT_ENERGY_THRESHOLD
        self.recognizer.pause_threshold = config.STT_PAUSE_THRESHOLD
        self.recognizer.dynamic_energy_threshold = True
        self.microphone: Optional[sr.Microphone] = None

    def initialize_microphone(self) -> bool:
        try:
            self.microphone = self._sr.Microphone()
            with self.microphone as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=1.0)
            return True
        except (OSError, AttributeError) as exc:
            print(f"[NEXUS] PyAudio nicht verfuegbar: {exc}", file=sys.stderr)
            return False

    def listen(self) -> Optional[str]:
        if self.microphone is None and not self.initialize_microphone():
            return None
        try:
            with self.microphone as source:
                if config.VERBOSE:
                    print("[NEXUS] Hoere zu ...", flush=True)
                audio = self.recognizer.listen(
                    source,
                    timeout=config.STT_TIMEOUT,
                    phrase_time_limit=config.STT_PHRASE_TIME_LIMIT,
                )
        except self._sr.WaitTimeoutError:
            return None
        except OSError as exc:
            print(f"[NEXUS] Audio-Fehler: {exc}", file=sys.stderr)
            return None
        try:
            text = self.recognizer.recognize_google(audio, language=config.STT_LANGUAGE)
            return text.strip() if text else None
        except self._sr.UnknownValueError:
            return None
        except self._sr.RequestError as exc:
            print(f"[NEXUS] Google-STT: {exc}", file=sys.stderr)
            return None


# =====================================================
# PUBLIC WRAPPER: NexusEars
# =====================================================

class NexusEars:
    def __init__(self) -> None:
        try:
            import speech_recognition  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("SpeechRecognition fehlt. Bitte install.bat ausfuehren.") from exc

        backend = None
        backend_name = ""
        try:
            import sounddevice  # noqa: F401
            import numpy  # noqa: F401
            backend = _EarsSounddevice()
            backend_name = "sounddevice"
        except ImportError:
            pass

        if backend is None:
            try:
                backend = _EarsPyAudio()
                backend_name = "pyaudio"
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError("sounddevice und numpy fehlen.") from exc

        self._impl = backend
        self.backend_name = backend_name
        if config.VERBOSE:
            print(f"[NEXUS] Mikrofon-Backend: {backend_name}", flush=True)

    def initialize_microphone(self) -> bool:
        return self._impl.initialize_microphone()

    def listen(self) -> Optional[str]:
        return self._impl.listen()


# =====================================================
# SPRACHAUSGABE: NexusVoice
# =====================================================

class NexusVoice:
    """
    Sprachausgabe via edge-tts (neural) oder pyttsx3 (Fallback).

    Interrupt: voice.stop() beendet laufende Ausgabe sofort.
    Audio-Prioritaet: pygame -> playsound -> Windows MCI -> mpg123/ffplay (Linux)
    pyttsx3: laeuft immer als Subprocess (Python 3.14 / comtypes-Bug).
    """

    def __init__(self) -> None:
        self.engine_name = config.TTS_ENGINE.lower().strip()
        self._lock = threading.Lock()
        self._pyttsx3_voice_id: Optional[str] = None
        self._stop_event = threading.Event()
        self._current_proc: Optional[subprocess.Popen] = None

        try:
            self._pyttsx3_init()
        except RuntimeError:
            if self.engine_name == "pyttsx3":
                raise

        if self.engine_name == "edge-tts":
            try:
                self._check_edge_tts()
            except RuntimeError as exc:
                print(f"[NEXUS] edge-tts nicht verfuegbar ({exc}), nutze pyttsx3.", flush=True)
                self.engine_name = "pyttsx3"
        elif self.engine_name != "pyttsx3":
            raise ValueError(f"Unbekannte TTS-Engine: {self.engine_name!r}")

        if config.VERBOSE:
            print(f"[NEXUS] TTS-Engine: {self.engine_name}", flush=True)

    def stop(self) -> None:
        """Unterbricht die laufende Sprachausgabe sofort."""
        self._stop_event.set()
        proc = self._current_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass

    # -- pyttsx3 (immer via Subprocess) --

    def _pyttsx3_init(self) -> None:
        try:
            import pyttsx3  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("pyttsx3 nicht installiert.") from exc

        hint = (config.PYTTSX3_VOICE_HINT or "").lower()
        if not hint:
            return

        script = "\n".join([
            "import pyttsx3",
            "try:",
            "    engine = pyttsx3.init()",
            f"    hint = {repr(hint)}",
            "    for v in engine.getProperty('voices'):",
            "        parts = [str(v.id or ''), str(v.name or '')]",
            "        if hasattr(v, 'languages'):",
            "            parts += (v.languages or [])",
            "        if hint in ' '.join(parts).lower():",
            "            print(v.id)",
            "            break",
            "except Exception:",
            "    pass",
        ])
        try:
            result = subprocess.run(
                [sys.executable, "-c", script], capture_output=True, text=True, timeout=15
            )
            voice_id = result.stdout.strip()
            if voice_id:
                self._pyttsx3_voice_id = voice_id
                if config.VERBOSE:
                    short = voice_id.split("\\")[-1] if "\\" in voice_id else voice_id
                    print(f"[NEXUS] pyttsx3-Stimme: {short}", flush=True)
        except Exception:  # noqa: BLE001
            pass

    def _speak_pyttsx3(self, text: str) -> None:
        import json as _json
        lines = [
            "import pyttsx3",
            "engine = pyttsx3.init()",
            f"engine.setProperty('rate', {config.PYTTSX3_RATE})",
            f"engine.setProperty('volume', {config.PYTTSX3_VOLUME})",
        ]
        if self._pyttsx3_voice_id:
            lines.append(f"engine.setProperty('voice', {_json.dumps(self._pyttsx3_voice_id)})")
        lines.append(f"engine.say({_json.dumps(text)})")
        lines.append("engine.runAndWait()")
        try:
            proc = subprocess.Popen([sys.executable, "-c", "\n".join(lines)])
            self._current_proc = proc
            while not self._stop_event.is_set():
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except FileNotFoundError:
            print("[NEXUS] Python-Executable nicht gefunden.", file=sys.stderr)
        finally:
            self._current_proc = None

    # -- edge-tts --

    def _check_edge_tts(self) -> None:
        try:
            import edge_tts  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("edge-tts fehlt.") from exc

    def _play_mp3_mci(self, path: str) -> None:
        """Windows MCI – eingebaut, kein extra Paket. Unterbrechbar via _stop_event."""
        import ctypes
        abs_path = os.path.abspath(path).replace("/", "\\")
        alias = "nexus_pb"
        winmm = ctypes.windll.winmm
        buf = ctypes.create_unicode_buffer(512)
        ret = winmm.mciSendStringW(f'open "{abs_path}" type mpegvideo alias {alias}', buf, 512, None)
        if ret != 0:
            raise RuntimeError(f"MCI open Code {ret}")
        try:
            winmm.mciSendStringW(f"set {alias} time format milliseconds", buf, 512, None)
            winmm.mciSendStringW(f"play {alias}", buf, 512, None)
            while not self._stop_event.is_set():
                winmm.mciSendStringW(f"status {alias} mode", buf, 512, None)
                mode = buf.value.strip().lower() if buf.value else ""
                if mode not in ("playing", ""):
                    break
                time.sleep(0.05)
        finally:
            winmm.mciSendStringW(f"stop {alias}", None, 0, None)
            winmm.mciSendStringW(f"close {alias}", None, 0, None)

    def _play_mp3(self, path: str) -> None:
        """Spielt MP3 synchron ab: pygame -> playsound -> MCI (Win) -> mpg123/ffplay (Linux)."""
        # pygame
        try:
            import pygame
            try:
                pygame.mixer.init()
                pygame.mixer.music.load(path)
                pygame.mixer.music.play()
                clock = pygame.time.Clock()
                while not self._stop_event.is_set() and pygame.mixer.music.get_busy():
                    clock.tick(20)
                pygame.mixer.music.stop()
                pygame.mixer.quit()
                return
            except Exception as exc:  # noqa: BLE001
                if config.VERBOSE:
                    print(f"[NEXUS] pygame-Fehler: {exc}", file=sys.stderr)
                try:
                    pygame.mixer.quit()
                except Exception:  # noqa: BLE001
                    pass
        except ImportError:
            pass

        # playsound
        try:
            from playsound import playsound
            playsound(path)
            return
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001
            if config.VERBOSE:
                print(f"[NEXUS] playsound-Fehler: {exc}", file=sys.stderr)

        # Windows MCI
        if sys.platform == "win32":
            try:
                self._play_mp3_mci(path)
                return
            except Exception as exc:  # noqa: BLE001
                if config.VERBOSE:
                    print(f"[NEXUS] MCI-Fehler: {exc}", file=sys.stderr)

        # Linux
        if sys.platform != "win32":
            abs_path = os.path.abspath(path)
            for cmd in [
                ["mpg123", "-q", abs_path],
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", abs_path],
                ["mplayer", "-really-quiet", abs_path],
            ]:
                try:
                    proc = subprocess.Popen(cmd)
                    self._current_proc = proc
                    while not self._stop_event.is_set():
                        if proc.poll() is not None:
                            break
                        time.sleep(0.05)
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait()
                    return
                except FileNotFoundError:
                    continue
                finally:
                    self._current_proc = None

        raise RuntimeError("Kein Audio-Player verfuegbar.")

    def _speak_edge_tts(self, text: str) -> None:
        import edge_tts

        async def _synth(path: str) -> None:
            comm = edge_tts.Communicate(
                text,
                voice=config.EDGE_TTS_VOICE,
                rate=config.EDGE_TTS_RATE,
                volume=config.EDGE_TTS_VOLUME,
            )
            await comm.save(path)

        tmp = tempfile.NamedTemporaryFile(prefix="nexus_tts_", suffix=".mp3", delete=False)
        tmp.close()
        try:
            asyncio.run(_synth(tmp.name))
            try:
                self._play_mp3(tmp.name)
            except RuntimeError as exc:
                if config.VERBOSE:
                    print(f"[NEXUS] MP3-Fehler: {exc} -> pyttsx3", flush=True)
                self._speak_pyttsx3(text)
        except Exception as exc:  # noqa: BLE001
            print(f"[NEXUS] edge-tts-Fehler: {exc}", file=sys.stderr)
            self._speak_pyttsx3(text)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    # -- Oeffentliche Schnittstelle --

    def speak(self, text: str) -> None:
        """Gibt text per TTS aus. Bereinigt Markdown. Unterbrechbar via stop()."""
        if not text:
            return
        clean = _clean_for_speech(text)
        if not clean:
            return
        if config.VERBOSE:
            print(f"[NEXUS] >> {clean}", flush=True)
        self._stop_event.clear()
        with self._lock:
            try:
                if self.engine_name == "edge-tts":
                    self._speak_edge_tts(clean)
                else:
                    self._speak_pyttsx3(clean)
            except Exception as exc:  # noqa: BLE001
                print(f"[NEXUS] TTS-Fehler: {exc}", file=sys.stderr)
                print(clean)

    def speak_chunked(self, text: str, max_chars: int = 250) -> None:
        """
        Gibt langen Text in Satz-Chunks aus – erste Reaktion kommt schneller.
        Bricht sofort ab wenn stop() aufgerufen wird.
        """
        clean = _clean_for_speech(text)
        if not clean:
            return
        parts = re.split(r'(?<=[.!?])\s+', clean)
        chunks: List[str] = []
        current = ""
        for part in parts:
            if len(current) + len(part) + 1 <= max_chars:
                current = (current + " " + part).strip()
            else:
                if current:
                    chunks.append(current)
                current = part
        if current:
            chunks.append(current)

        self._stop_event.clear()
        for chunk in chunks:
            if self._stop_event.is_set():
                break
            if config.VERBOSE:
                print(f"[NEXUS] >> {chunk}", flush=True)
            with self._lock:
                try:
                    if self.engine_name == "edge-tts":
                        self._speak_edge_tts(chunk)
                    else:
                        self._speak_pyttsx3(chunk)
                except Exception as exc:  # noqa: BLE001
                    print(f"[NEXUS] TTS-Fehler: {exc}", file=sys.stderr)
                    print(chunk)

    @property
    def is_speaking(self) -> bool:
        return self._lock.locked()


# =====================================================
# WAKE-WORD LISTENER
# =====================================================

class NexusWakeWord:
    """
    Hintergrund-Thread der dauerhaft auf das Wake-Word lauscht.
    Bei Erkennung wird callback() aufgerufen.

    Konfiguration in config.py:
      WAKE_WORD = "nexus"

    Verwendung:
      ww = NexusWakeWord(callback=lambda: print("Wake!"))
      ww.start()
      ww.stop()
    """

    def __init__(self, callback: Callable[[], None], ears: Optional[NexusEars] = None) -> None:
        self.callback = callback
        self._ears = ears
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self.wake_word = getattr(config, "WAKE_WORD", "nexus").lower().strip()
        self._detection_count = 0
        self._last_detection = 0.0
        self._cooldown = 3.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(
            target=self._listen_loop, daemon=True, name="nexus-wakeword"
        )
        self._thread.start()
        if config.VERBOSE:
            print(f"[NEXUS] Wake-Word-Listener gestartet: '{self.wake_word}'", flush=True)

    def stop(self) -> None:
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=5)
        if config.VERBOSE:
            print("[NEXUS] Wake-Word-Listener gestoppt.", flush=True)

    @property
    def is_running(self) -> bool:
        return self._running.is_set() and bool(self._thread and self._thread.is_alive())

    def _listen_loop(self) -> None:
        ears = self._ears
        if ears is None:
            try:
                ears = NexusEars()
                ears.initialize_microphone()
            except Exception as exc:  # noqa: BLE001
                print(f"[NEXUS] Wake-Word: Mikrofon-Init fehlgeschlagen: {exc}", file=sys.stderr)
                return

        while self._running.is_set():
            try:
                text = ears.listen()
                if not text:
                    continue
                if self.wake_word in text.lower():
                    now = time.monotonic()
                    if now - self._last_detection >= self._cooldown:
                        self._last_detection = now
                        self._detection_count += 1
                        if config.VERBOSE:
                            print(
                                f"[NEXUS] Wake-Word erkannt: '{text}' "
                                f"(#{self._detection_count})",
                                flush=True,
                            )
                        try:
                            self.callback()
                        except Exception as exc:  # noqa: BLE001
                            print(f"[NEXUS] Wake-Word-Callback-Fehler: {exc}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                if self._running.is_set():
                    logger.debug("Wake-Word-Loop-Fehler: %s", exc)
                time.sleep(0.5)

    def get_stats(self) -> Dict:
        return {
            "wake_word":      self.wake_word,
            "running":        self.is_running,
            "detections":     self._detection_count,
            "last_detection": self._last_detection,
            "cooldown_sec":   self._cooldown,
        }


# =====================================================
# STATUS-FUNKTION (konsistente NEXUS-API)
# =====================================================

def voice_status() -> Dict:
    """
    Prueft welche Voice-Backends verfuegbar sind.
    Konsistente API wie sar_status(), conflict_status() etc.
    """
    status: Dict = {
        "tts_engine":  getattr(config, "TTS_ENGINE", "edge-tts"),
        "stt_backend": getattr(config, "STT_BACKEND", "google"),
        "edge_tts":    False,
        "pyttsx3":     False,
        "sounddevice": False,
        "pyaudio":     False,
        "whisper":     False,
        "vosk":        False,
        "google_stt":  False,
        "wake_word":   getattr(config, "WAKE_WORD", "nexus"),
        "warnings":    [],
    }
    try:
        import edge_tts  # noqa: F401
        status["edge_tts"] = True
    except ImportError:
        status["warnings"].append("edge-tts fehlt (pip install edge-tts)")
    try:
        import pyttsx3  # noqa: F401
        status["pyttsx3"] = True
    except ImportError:
        status["warnings"].append("pyttsx3 fehlt (pip install pyttsx3)")
    try:
        import sounddevice  # noqa: F401
        import numpy  # noqa: F401
        status["sounddevice"] = True
    except ImportError:
        status["warnings"].append("sounddevice/numpy fehlen")
    try:
        import speech_recognition  # noqa: F401
        status["google_stt"] = True
    except ImportError:
        status["warnings"].append("SpeechRecognition fehlt")
    try:
        import pyaudio  # noqa: F401
        status["pyaudio"] = True
    except ImportError:
        pass
    try:
        import faster_whisper  # noqa: F401
        status["whisper"] = True
    except ImportError:
        pass
    try:
        import vosk  # noqa: F401
        vosk_path = getattr(config, "VOSK_MODEL_PATH", "")
        if vosk_path and os.path.isdir(vosk_path):
            status["vosk"] = vosk_path
        else:
            status["vosk"] = "installiert – VOSK_MODEL_PATH in config.py setzen"
    except ImportError:
        pass

    tts_avail = "edge-tts" if status["edge_tts"] else ("pyttsx3" if status["pyttsx3"] else "KEINS")
    stt_avail = []
    if status["whisper"]:
        stt_avail.append("Whisper(offline)")
    if isinstance(status["vosk"], str) and os.sep in status["vosk"]:
        stt_avail.append("Vosk(offline)")
    if status["google_stt"]:
        stt_avail.append("Google")
    if not stt_avail:
        stt_avail.append("KEINS")
    mic = "sounddevice" if status["sounddevice"] else ("pyaudio" if status["pyaudio"] else "KEIN")

    status["summary"] = (
        f"TTS: {tts_avail} | STT: {'|'.join(stt_avail)} | "
        f"Mikrofon: {mic} | Wake-Word: '{status['wake_word']}'"
    )
    return status


def print_voice_status() -> None:
    """Gibt voice_status() leserlich auf der Konsole aus."""
    s = voice_status()
    ok = "[OK]"
    no = "[--]"
    warn = "[!!]"
    print("\n  -- NEXUS Voice Status --")
    print(f"  TTS-Engine (config):  {s['tts_engine']}")
    print(f"  STT-Backend (config): {s['stt_backend']}")
    print()
    print(f"  {ok if s['edge_tts'] else no}  edge-tts (neural TTS, empfohlen)")
    print(f"  {ok if s['pyttsx3'] else no}  pyttsx3  (Offline-Fallback)")
    print(f"  {ok if s['sounddevice'] else no}  sounddevice + numpy (Mikrofon)")
    print(f"  {ok if s['pyaudio'] else no}  pyaudio  (Mikrofon-Fallback)")
    print(f"  {ok if s['google_stt'] else no}  SpeechRecognition + Google STT")
    print(f"  {ok if s['whisper'] else no}  faster-whisper (Offline, beste Qualitaet)")
    if s["vosk"] is False:
        print(f"  {no}  vosk (nicht installiert)")
    elif isinstance(s["vosk"], str) and os.sep not in s["vosk"]:
        print(f"  {warn}  vosk: {s['vosk']}")
    else:
        print(f"  {ok}  vosk (Modell: {s['vosk']})")
    print(f"\n  Wake-Word: '{s['wake_word']}'")
    if s["warnings"]:
        for w in s["warnings"]:
            print(f"  {warn} {w}")
    print(f"\n  {s['summary']}\n")


# =====================================================
# CLI-TEST
# =====================================================

if __name__ == "__main__":
    print("NEXUS Voice – Selbsttest")
    print("=" * 40)
    print_voice_status()
    s = voice_status()
    if s["edge_tts"] or s["pyttsx3"]:
        print("  TTS-Test: Spreche Testtext ...")
        try:
            v = NexusVoice()
            v.speak("NEXUS Stimme aktiv. Alle Systeme bereit.")
            print("  TTS: OK")
        except Exception as exc:
            print(f"  TTS: FEHLER – {exc}")
    else:
        print("  TTS: Kein Backend installiert.")
