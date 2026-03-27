"""voice_processor.py — STT/TTS pipeline for nanobot-stack Sub-project G."""
from __future__ import annotations

import logging
import os
import pathlib
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger("rag-bridge.voice")

# ---------------------------------------------------------------------------
# Voice alias map: short name -> full Piper model name
# ---------------------------------------------------------------------------
VOICE_ALIAS_MAP: dict[str, str] = {
    "fr_siwis":       "fr_FR-siwis-medium",
    "fr_siwis_low":   "fr_FR-siwis-low",
    "fr_upmc_pierre": "fr_FR-upmc_pierre-medium",
}

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TranscriptionResult:
    text: str
    duration_ms: int
    audio_duration_s: float
    model: str


@dataclass
class ValidationResult:
    ok: bool
    error_reason: str = ""
    duration_s: float = 0.0


@dataclass
class VoiceChatResult:
    transcription: str
    response_text: str
    audio_bytes: bytes
    latency_ms: int


# ---------------------------------------------------------------------------
# VoiceProcessor
# ---------------------------------------------------------------------------

class VoiceProcessor:
    """Central STT/TTS processor.  All audio processing is in-memory only."""

    def __init__(self) -> None:
        self.voice_enabled: bool = os.getenv("VOICE_ENABLED", "false").lower() == "true"
        self.stt_backend: str = os.getenv("STT_BACKEND", "faster_whisper")
        self.stt_model: str = os.getenv("STT_MODEL", "whisper-base")
        self.stt_language: Optional[str] = os.getenv("STT_LANGUAGE", "fr") or None
        self.stt_device: str = os.getenv("STT_DEVICE", "cpu")
        self.stt_compute_type: str = os.getenv("STT_COMPUTE_TYPE", "int8")
        self.whisper_url: str = os.getenv("WHISPER_URL", "http://whisper:9000")
        self.piper_url: str = os.getenv("PIPER_URL", "http://piper:5002")
        self.tts_model: str = os.getenv("TTS_MODEL", "fr_siwis")
        self.tts_speed: float = float(os.getenv("TTS_SPEED", "1.0"))
        self.max_audio_duration_s: int = int(os.getenv("VOICE_MAX_AUDIO_DURATION_S", "60"))
        self.max_tts_chars: int = int(os.getenv("VOICE_MAX_TTS_CHARS", "2000"))

        self._whisper_model = None
        self._deps: dict = {}
        if self.voice_enabled and self.stt_backend == "faster_whisper":
            self._load_whisper_model()

    def _load_whisper_model(self) -> None:
        from faster_whisper import WhisperModel  # pylint: disable=import-error
        logger.info("Loading WhisperModel: %s on %s/%s",
                    self.stt_model, self.stt_device, self.stt_compute_type)
        self._whisper_model = WhisperModel(
            self.stt_model,
            device=self.stt_device,
            compute_type=self.stt_compute_type,
        )

    def raise_if_disabled(self) -> None:
        """Raise HTTP 503 if VOICE_ENABLED is false."""
        if not self.voice_enabled:
            raise HTTPException(
                status_code=503,
                detail={"error": "voice_disabled",
                        "message": "Set VOICE_ENABLED=true and restart Docker services (piper)."},
            )

    # Supported extensions -> pydub format strings
    _SUPPORTED_FORMATS: dict[str, str] = {
        "wav":  "wav",
        "mp3":  "mp3",
        "ogg":  "ogg",
        "webm": "webm",
    }

    def validate_audio(self, audio_bytes: bytes, filename: str) -> ValidationResult:
        """Validate audio format and duration. Raises HTTPException on failure."""
        import io
        from pydub import AudioSegment  # pylint: disable=import-error

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        fmt = self._SUPPORTED_FORMATS.get(ext)
        if fmt is None:
            raise HTTPException(
                status_code=415,
                detail={"error": "unsupported_format",
                        "supported": list(self._SUPPORTED_FORMATS),
                        "received": ext},
            )

        try:
            segment = AudioSegment.from_file(io.BytesIO(audio_bytes), format=fmt)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": "corrupted_audio", "detail": str(exc)},
            ) from exc

        duration_s = segment.duration_seconds
        if duration_s > self.max_audio_duration_s:
            raise HTTPException(
                status_code=413,
                detail={"error": "audio_too_long",
                        "max_s": self.max_audio_duration_s,
                        "received_s": round(duration_s, 2)},
            )

        return ValidationResult(ok=True, duration_s=duration_s)

    async def transcribe(self, audio_bytes: bytes, filename: str) -> TranscriptionResult:
        """Transcribe audio bytes to text using configured STT backend."""
        import io
        import time
        from pydub import AudioSegment  # pylint: disable=import-error

        self.raise_if_disabled()
        validation = self.validate_audio(audio_bytes, filename)

        # Convert to 16 kHz mono WAV for Whisper
        ext = filename.rsplit(".", 1)[-1].lower()
        segment = AudioSegment.from_file(io.BytesIO(audio_bytes), format=ext)
        segment = segment.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        wav_buf = io.BytesIO()
        segment.export(wav_buf, format="wav")
        wav_bytes = wav_buf.getvalue()

        t0 = time.monotonic()

        if self.stt_backend == "whisper_server":
            text = await self._transcribe_server(wav_bytes)
        else:
            text = self._transcribe_local(wav_bytes)

        duration_ms = int((time.monotonic() - t0) * 1000)
        return TranscriptionResult(
            text=text.strip(),
            duration_ms=duration_ms,
            audio_duration_s=validation.duration_s,
            model=self.stt_model,
        )

    def _transcribe_local(self, wav_bytes: bytes) -> str:
        """Transcribe using the locally loaded faster-whisper model."""
        import io
        if self._whisper_model is None:
            raise HTTPException(status_code=503, detail={"error": "whisper_model_not_loaded"})
        segments, _info = self._whisper_model.transcribe(
            io.BytesIO(wav_bytes),
            language=self.stt_language,
            beam_size=5,
        )
        return " ".join(seg.text for seg in segments)

    async def _transcribe_server(self, wav_bytes: bytes) -> str:
        """Transcribe via whisper-server HTTP sidecar."""
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.whisper_url + "/inference",
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data={"language": self.stt_language or "fr"},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["text"]

    async def synthesize(self, text: str, voice: Optional[str] = None) -> bytes:
        """Synthesize text to MP3 bytes via Piper TTS Docker sidecar."""
        import httpx

        self.raise_if_disabled()

        effective_voice = voice or self.tts_model
        piper_voice = VOICE_ALIAS_MAP.get(effective_voice, effective_voice)

        # Truncate on word boundary
        if len(text) > self.max_tts_chars:
            truncated = text[: self.max_tts_chars]
            last_space = truncated.rfind(" ")
            text = truncated[:last_space] if last_space > 0 else truncated

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self.piper_url + "/api/tts",
                    json={"text": text, "voice": piper_voice, "speed": self.tts_speed},
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.content
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail={"error": "piper_unavailable",
                        "detail": str(exc)},
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail={"error": "piper_connection_error",
                        "detail": str(exc)},
            ) from exc

    def set_dependencies(self, deps: dict) -> None:
        """Inject runtime dependencies (handle_chat callable, state_dir)."""
        self._deps = deps

    async def voice_chat(
        self,
        audio_bytes: bytes,
        filename: str,
        session_id: str = "",
    ) -> VoiceChatResult:
        """Full voice round-trip: STT -> PII filter -> chat -> TTS -> DB write."""
        import sqlite3
        import time
        import uuid
        from datetime import datetime, timezone
        from pii_filter import redact_pii

        self.raise_if_disabled()

        t0 = time.monotonic()

        # 1. Transcribe
        transcription = await self.transcribe(audio_bytes, filename)

        # 2. PII filter on transcription text
        clean_text, _pii_types = redact_pii(transcription.text)

        # 3. Chat pipeline
        handle_chat = self._deps.get("handle_chat")
        if handle_chat is None:
            raise HTTPException(status_code=500, detail={"error": "handle_chat_not_wired"})
        response_text = await handle_chat(
            message=clean_text,
            session_id=session_id,
            source="voice",
        )

        # 4. Synthesize response
        audio_out = await self.synthesize(response_text)

        latency_ms = int((time.monotonic() - t0) * 1000)

        # 5. Write session metrics to SQLite
        state_dir = self._deps.get("state_dir", "/opt/nanobot-stack/rag-bridge/state")
        db_path = str(pathlib.Path(state_dir) / "scheduler.db")
        try:
            db = sqlite3.connect(db_path)
            db.execute(
                """INSERT INTO voice_sessions
                   (id, started_at, audio_duration_s, transcription_chars, tts_chars,
                    model_stt, model_tts, latency_ms, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    datetime.now(timezone.utc).isoformat(),
                    transcription.audio_duration_s,
                    len(transcription.text),
                    len(response_text),
                    self.stt_model,
                    self.tts_model,
                    latency_ms,
                    "ok",
                ),
            )
            db.commit()
            db.close()
        except Exception as exc:
            logger.warning("Failed to write voice_sessions: %s", exc)

        return VoiceChatResult(
            transcription=transcription.text,
            response_text=response_text,
            audio_bytes=audio_out,
            latency_ms=latency_ms,
        )
