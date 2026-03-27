"""Tests for voice_api.py REST endpoints."""
import io
import sys
import struct
import wave
import types
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_wav_bytes(duration_s=1.0, rate=16000):
    num = int(rate * duration_s)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack(f"<{num}h", *([0] * num)))
    return buf.getvalue()


class FakeProcessor:
    voice_enabled = True

    def raise_if_disabled(self):
        if not self.voice_enabled:
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail={"error": "voice_disabled"})

    async def transcribe(self, audio_bytes, filename):
        import types as t
        return t.SimpleNamespace(
            text="Bonjour le monde",
            duration_ms=500,
            audio_duration_s=2.0,
            model="whisper-base",
        )

    async def synthesize(self, text, voice=None):
        return b"mp3_audio_bytes"

    async def voice_chat(self, audio_bytes, filename, session_id=""):
        import types as t
        return t.SimpleNamespace(
            transcription="Bonjour",
            response_text="Il fait beau.",
            audio_bytes=b"mp3_response",
            latency_ms=800,
        )

    stt_backend = "faster_whisper"
    stt_model = "whisper-base"
    stt_language = "fr"
    tts_model = "fr_siwis"
    piper_url = "http://piper:5002"
    whisper_url = "http://whisper:9000"


def _build_app(processor=None):
    if "voice_api" in sys.modules:
        del sys.modules["voice_api"]
    import voice_api
    app = FastAPI()
    proc = processor or FakeProcessor()
    voice_api.init_voice_api(processor=proc, verify_token_dep=lambda: None)
    app.include_router(voice_api.router)
    return app


class TestTranscribeEndpoint:
    def test_transcribe_returns_json(self):
        app = _build_app()
        client = TestClient(app)
        wav = _make_wav_bytes()
        resp = client.post(
            "/api/voice/transcribe",
            files={"file": ("test.wav", wav, "audio/wav")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["text"] == "Bonjour le monde"
        assert data["duration_ms"] == 500
        assert data["audio_duration_s"] == 2.0
        assert data["model"] == "whisper-base"

    def test_transcribe_503_when_disabled(self):
        proc = FakeProcessor()
        proc.voice_enabled = False
        app = _build_app(proc)
        client = TestClient(app, raise_server_exceptions=False)
        wav = _make_wav_bytes()
        resp = client.post(
            "/api/voice/transcribe",
            files={"file": ("test.wav", wav, "audio/wav")},
        )
        assert resp.status_code == 503


class TestSynthesizeEndpoint:
    def test_synthesize_returns_audio_mpeg(self):
        app = _build_app()
        client = TestClient(app)
        resp = client.post(
            "/api/voice/synthesize",
            json={"text": "Bonjour", "voice": "fr_siwis"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mpeg"
        assert resp.content == b"mp3_audio_bytes"

    def test_synthesize_has_metadata_headers(self):
        app = _build_app()
        client = TestClient(app)
        resp = client.post(
            "/api/voice/synthesize",
            json={"text": "Bonjour", "voice": "fr_siwis"},
        )
        assert "x-tts-chars" in resp.headers

    def test_synthesize_empty_text_422(self):
        app = _build_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/voice/synthesize",
            json={"text": "", "voice": "fr_siwis"},
        )
        assert resp.status_code == 422


class TestChatEndpoint:
    def test_chat_returns_audio_mpeg(self):
        app = _build_app()
        client = TestClient(app)
        wav = _make_wav_bytes()
        resp = client.post(
            "/api/voice/chat",
            files={"file": ("test.wav", wav, "audio/wav")},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mpeg"
        assert resp.content == b"mp3_response"

    def test_chat_has_metadata_headers(self):
        app = _build_app()
        client = TestClient(app)
        wav = _make_wav_bytes()
        resp = client.post(
            "/api/voice/chat",
            files={"file": ("test.wav", wav, "audio/wav")},
        )
        assert "x-transcription" in resp.headers
        assert "x-response-text" in resp.headers
        assert "x-latency-ms" in resp.headers

    def test_chat_transcription_header_truncated_at_500(self):
        class LongTranscriptProcessor(FakeProcessor):
            async def voice_chat(self, audio_bytes, filename, session_id=""):
                import types as t
                return t.SimpleNamespace(
                    transcription="A" * 600,
                    response_text="B" * 600,
                    audio_bytes=b"audio",
                    latency_ms=100,
                )
        app = _build_app(LongTranscriptProcessor())
        client = TestClient(app)
        wav = _make_wav_bytes()
        resp = client.post(
            "/api/voice/chat",
            files={"file": ("test.wav", wav, "audio/wav")},
        )
        assert len(resp.headers["x-transcription"]) <= 500
        assert len(resp.headers["x-response-text"]) <= 500


class TestStatusEndpoint:
    def test_status_returns_json(self):
        app = _build_app()
        client = TestClient(app)
        resp = client.get("/api/voice/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data
        assert "stt_backend" in data
        assert "piper_ok" in data
