"""voice_api.py — REST endpoints for Sub-project G: Voice Interface."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel

logger = logging.getLogger("rag-bridge.voice_api")
router = APIRouter(prefix="/api/voice", tags=["voice"])

_processor: Any = None
_verify_token_dep = None


def init_voice_api(processor: Any, verify_token_dep) -> None:
    global _processor, _verify_token_dep  # pylint: disable=global-statement
    _processor = processor
    _verify_token_dep = verify_token_dep


def _auth():
    """Call the verify_token dependency — indirection avoids unnecessary-lambda."""
    return _verify_token_dep()


class SynthesizeRequest(BaseModel):
    text: str
    voice: Optional[str] = None


# ---------------------------------------------------------------------------
# POST /api/voice/transcribe
# ---------------------------------------------------------------------------

@router.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    _=Depends(_auth),
):
    """Upload audio file -> JSON transcription."""
    _processor.raise_if_disabled()
    audio_bytes = await file.read()
    filename = file.filename or "audio.wav"
    result = await _processor.transcribe(audio_bytes, filename)
    return {
        "text": result.text,
        "duration_ms": result.duration_ms,
        "audio_duration_s": result.audio_duration_s,
        "model": result.model,
    }


# ---------------------------------------------------------------------------
# POST /api/voice/synthesize
# ---------------------------------------------------------------------------

@router.post("/synthesize")
async def synthesize(
    body: SynthesizeRequest,
    _=Depends(_auth),
):
    """Synthesize text -> audio/mpeg response."""
    _processor.raise_if_disabled()
    if not body.text or not body.text.strip():
        raise HTTPException(status_code=422, detail={"error": "empty_text"})

    piper_voice = body.voice or _processor.tts_model
    audio_bytes = await _processor.synthesize(body.text, piper_voice)

    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={
            "X-TTS-Model": piper_voice,
            "X-TTS-Chars": str(len(body.text)),
        },
    )


# ---------------------------------------------------------------------------
# POST /api/voice/chat
# ---------------------------------------------------------------------------

@router.post("/chat")
async def voice_chat(
    file: UploadFile = File(...),
    session_id: str = Form(default=""),
    _=Depends(_auth),
):
    """Upload audio -> full pipeline -> audio/mpeg response with metadata headers."""
    _processor.raise_if_disabled()
    audio_bytes = await file.read()
    filename = file.filename or "audio.wav"
    result = await _processor.voice_chat(audio_bytes, filename, session_id=session_id)

    return Response(
        content=result.audio_bytes,
        media_type="audio/mpeg",
        headers={
            "X-Transcription": result.transcription[:500],
            "X-Response-Text": result.response_text[:500],
            "X-Latency-Ms": str(result.latency_ms),
        },
    )


# ---------------------------------------------------------------------------
# GET /api/voice/status
# ---------------------------------------------------------------------------

@router.get("/status", include_in_schema=True)
async def voice_status():
    """Return voice subsystem status and health of external services."""
    import httpx

    enabled = _processor.voice_enabled
    piper_ok = False
    whisper_ok = None

    if enabled:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get(_processor.piper_url + "/health")
                piper_ok = r.status_code == 200
        except Exception:
            piper_ok = False

        if _processor.stt_backend == "whisper_server":
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    r = await client.get(_processor.whisper_url + "/health")
                    whisper_ok = r.status_code == 200
            except Exception:
                whisper_ok = False

    return {
        "enabled": enabled,
        "stt_backend": _processor.stt_backend,
        "stt_model": _processor.stt_model,
        "stt_language": _processor.stt_language,
        "tts_model": _processor.tts_model,
        "piper_ok": piper_ok,
        "whisper_ok": whisper_ok,
    }
