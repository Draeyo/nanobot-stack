"""Tests for VoiceProcessor — skeleton, guards, env vars."""
import os
import sys
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_processor(monkeypatch, *, voice_enabled="true", stt_backend="faster_whisper",
                    stt_model="whisper-base", stt_language="fr", piper_url="http://piper:5002",
                    whisper_url="http://whisper:9000", stt_device="cpu",
                    stt_compute_type="int8", tts_model="fr_siwis", tts_speed="1.0",
                    max_audio_s="60", max_tts_chars="2000"):
    monkeypatch.setenv("VOICE_ENABLED", voice_enabled)
    monkeypatch.setenv("STT_BACKEND", stt_backend)
    monkeypatch.setenv("STT_MODEL", stt_model)
    monkeypatch.setenv("STT_LANGUAGE", stt_language)
    monkeypatch.setenv("PIPER_URL", piper_url)
    monkeypatch.setenv("WHISPER_URL", whisper_url)
    monkeypatch.setenv("STT_DEVICE", stt_device)
    monkeypatch.setenv("STT_COMPUTE_TYPE", stt_compute_type)
    monkeypatch.setenv("TTS_MODEL", tts_model)
    monkeypatch.setenv("TTS_SPEED", tts_speed)
    monkeypatch.setenv("VOICE_MAX_AUDIO_DURATION_S", max_audio_s)
    monkeypatch.setenv("VOICE_MAX_TTS_CHARS", max_tts_chars)

    # Reload module so env vars are picked up
    if "voice_processor" in sys.modules:
        del sys.modules["voice_processor"]
    # Remove stale pydub mock from previous tests so real pydub is used
    # unless the caller explicitly sets it
    sys.modules.pop("pydub", None)

    # Mock faster_whisper to avoid importing the real library
    import types
    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = lambda *a, **kw: object()
    sys.modules["faster_whisper"] = fw

    import voice_processor
    return voice_processor.VoiceProcessor()


# ---------------------------------------------------------------------------
# Task 2 tests: skeleton / VOICE_ENABLED guard
# ---------------------------------------------------------------------------

class TestVoiceProcessorInit:
    def test_voice_enabled_true(self, monkeypatch):
        vp = _make_processor(monkeypatch, voice_enabled="true")
        assert vp.voice_enabled is True

    def test_voice_enabled_false(self, monkeypatch):
        vp = _make_processor(monkeypatch, voice_enabled="false")
        assert vp.voice_enabled is False

    def test_env_vars_loaded(self, monkeypatch):
        vp = _make_processor(monkeypatch, stt_model="whisper-small", piper_url="http://mypiper:5002")
        assert vp.stt_model == "whisper-small"
        assert vp.piper_url == "http://mypiper:5002"

    def test_max_audio_duration_int(self, monkeypatch):
        vp = _make_processor(monkeypatch, max_audio_s="90")
        assert vp.max_audio_duration_s == 90

    def test_max_tts_chars_int(self, monkeypatch):
        vp = _make_processor(monkeypatch, max_tts_chars="1500")
        assert vp.max_tts_chars == 1500

    def test_raise_if_disabled_raises(self, monkeypatch):
        from fastapi import HTTPException
        vp = _make_processor(monkeypatch, voice_enabled="false")
        with pytest.raises(HTTPException) as exc_info:
            vp.raise_if_disabled()
        assert exc_info.value.status_code == 503

    def test_raise_if_disabled_ok(self, monkeypatch):
        vp = _make_processor(monkeypatch, voice_enabled="true")
        vp.raise_if_disabled()  # must not raise

    def test_whisper_model_loaded_when_enabled(self, monkeypatch):
        import types
        fw = types.ModuleType("faster_whisper")
        calls = []
        fw.WhisperModel = lambda *a, **kw: calls.append((a, kw)) or object()
        sys.modules["faster_whisper"] = fw
        if "voice_processor" in sys.modules:
            del sys.modules["voice_processor"]
        monkeypatch.setenv("VOICE_ENABLED", "true")
        monkeypatch.setenv("STT_BACKEND", "faster_whisper")
        import voice_processor
        voice_processor.VoiceProcessor()
        assert len(calls) == 1

    def test_whisper_model_not_loaded_when_disabled(self, monkeypatch):
        import types
        fw = types.ModuleType("faster_whisper")
        calls = []
        fw.WhisperModel = lambda *a, **kw: calls.append((a, kw)) or object()
        sys.modules["faster_whisper"] = fw
        if "voice_processor" in sys.modules:
            del sys.modules["voice_processor"]
        monkeypatch.setenv("VOICE_ENABLED", "false")
        monkeypatch.setenv("STT_BACKEND", "faster_whisper")
        import voice_processor
        voice_processor.VoiceProcessor()
        assert len(calls) == 0


# ---------------------------------------------------------------------------
# Task 3 tests: validate_audio
# ---------------------------------------------------------------------------
import io
import wave
import struct


def _make_wav_bytes(duration_s: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Generate a minimal valid WAV file in memory."""
    num_samples = int(sample_rate * duration_s)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{num_samples}h", *([0] * num_samples)))
    return buf.getvalue()


class TestValidateAudio:
    def test_valid_wav(self, monkeypatch):
        vp = _make_processor(monkeypatch, voice_enabled="true")
        audio = _make_wav_bytes(1.0)
        result = vp.validate_audio(audio, "test.wav")
        assert result.ok is True
        assert result.duration_s == pytest.approx(1.0, abs=0.1)

    def test_unsupported_mime_raises_415(self, monkeypatch):
        from fastapi import HTTPException
        vp = _make_processor(monkeypatch, voice_enabled="true")
        with pytest.raises(HTTPException) as exc_info:
            vp.validate_audio(b"fake", "video.mp4")
        assert exc_info.value.status_code == 415

    def test_audio_too_long_raises_413(self, monkeypatch):
        from fastapi import HTTPException
        vp = _make_processor(monkeypatch, voice_enabled="true", max_audio_s="5")
        audio = _make_wav_bytes(10.0)
        with pytest.raises(HTTPException) as exc_info:
            vp.validate_audio(audio, "long.wav")
        assert exc_info.value.status_code == 413
        assert exc_info.value.detail["error"] == "audio_too_long"
        assert exc_info.value.detail["max_s"] == 5

    def test_corrupted_file_raises_422(self, monkeypatch):
        from fastapi import HTTPException
        vp = _make_processor(monkeypatch, voice_enabled="true")
        with pytest.raises(HTTPException) as exc_info:
            vp.validate_audio(b"not audio at all", "broken.wav")
        assert exc_info.value.status_code == 422

    def test_mp3_extension_accepted(self, monkeypatch):
        """mp3 extension passes format check (pydub mock returns valid duration)."""
        import types
        vp = _make_processor(monkeypatch, voice_enabled="true")
        pydub_mod = types.ModuleType("pydub")

        class FakeSegment:
            duration_seconds = 2.0

            @classmethod
            def from_file(cls, *a, **kw):
                return cls()
        pydub_mod.AudioSegment = FakeSegment
        sys.modules["pydub"] = pydub_mod
        result = vp.validate_audio(b"fakemp3", "speech.mp3")
        assert result.ok is True

    def test_ogg_extension_accepted(self, monkeypatch):
        import types
        vp = _make_processor(monkeypatch, voice_enabled="true")
        pydub_mod = types.ModuleType("pydub")

        class FakeSegment:
            duration_seconds = 3.0

            @classmethod
            def from_file(cls, *a, **kw):
                return cls()
        pydub_mod.AudioSegment = FakeSegment
        sys.modules["pydub"] = pydub_mod
        result = vp.validate_audio(b"fakeogg", "speech.ogg")
        assert result.ok is True

    def test_webm_extension_accepted(self, monkeypatch):
        import types
        vp = _make_processor(monkeypatch, voice_enabled="true")
        pydub_mod = types.ModuleType("pydub")

        class FakeSegment:
            duration_seconds = 1.5

            @classmethod
            def from_file(cls, *a, **kw):
                return cls()
        pydub_mod.AudioSegment = FakeSegment
        sys.modules["pydub"] = pydub_mod
        result = vp.validate_audio(b"fakewebm", "speech.webm")
        assert result.ok is True


# ---------------------------------------------------------------------------
# Task 4 tests: transcribe
# ---------------------------------------------------------------------------
import asyncio


class TestTranscribe:
    def test_transcribe_faster_whisper_mock(self, monkeypatch):
        """Mock WhisperModel.transcribe -> verify text returned and duration_ms >= 0."""
        import types

        # Stub faster_whisper
        fw = types.ModuleType("faster_whisper")

        class FakeSegResult:
            text = " Bonjour le monde"

        class FakeModel:
            def transcribe(self, buf, language=None, beam_size=5):
                info = types.SimpleNamespace(language="fr")
                return iter([FakeSegResult()]), info
        fw.WhisperModel = lambda *a, **kw: FakeModel()
        sys.modules["faster_whisper"] = fw

        vp = _make_processor(monkeypatch, voice_enabled="true", stt_backend="faster_whisper")
        vp._whisper_model = FakeModel()

        # Stub pydub AFTER _make_processor (which clears sys.modules["pydub"])
        pydub_mod = types.ModuleType("pydub")

        class FakeSeg:
            duration_seconds = 2.0

            @classmethod
            def from_file(cls, *a, **kw):
                return cls()

            def set_frame_rate(self, r):
                return self

            def set_channels(self, c):
                return self

            def set_sample_width(self, w):
                return self

            def export(self, buf, format):
                buf.write(b"RIFF\x00\x00\x00\x00WAVEfmt ")
        pydub_mod.AudioSegment = FakeSeg
        sys.modules["pydub"] = pydub_mod

        result = asyncio.get_event_loop().run_until_complete(
            vp.transcribe(_make_wav_bytes(2.0), "test.wav")
        )
        assert result.text == "Bonjour le monde"
        assert result.duration_ms >= 0
        assert result.audio_duration_s == pytest.approx(2.0, abs=0.5)
        assert result.model == "whisper-base"

    def test_transcribe_whisper_server_mock(self, monkeypatch):
        """STT_BACKEND=whisper_server — mock httpx, check multipart payload sent."""
        import types

        captured = {}

        class FakeResp:
            status_code = 200

            def json(self):
                return {"text": "Quelle heure est-il ?"}

            def raise_for_status(self):
                pass

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, url, **kwargs):
                captured["url"] = url
                captured["kwargs"] = kwargs
                return FakeResp()
        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

        vp = _make_processor(monkeypatch, voice_enabled="true", stt_backend="whisper_server",
                             whisper_url="http://whisper:9000")

        # Stub pydub AFTER _make_processor
        pydub_mod = types.ModuleType("pydub")

        class FakeSeg:
            duration_seconds = 1.5

            @classmethod
            def from_file(cls, *a, **kw):
                return cls()

            def set_frame_rate(self, r):
                return self

            def set_channels(self, c):
                return self

            def set_sample_width(self, w):
                return self

            def export(self, buf, format):
                buf.write(b"wav_data")
        pydub_mod.AudioSegment = FakeSeg
        sys.modules["pydub"] = pydub_mod

        result = asyncio.get_event_loop().run_until_complete(
            vp.transcribe(_make_wav_bytes(1.5), "speech.wav")
        )
        assert result.text == "Quelle heure est-il ?"
        assert "http://whisper:9000/inference" in captured["url"]

    def test_transcribe_unsupported_mime(self, monkeypatch):
        from fastapi import HTTPException
        vp = _make_processor(monkeypatch, voice_enabled="true")
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                vp.transcribe(b"data", "video.mp4")
            )
        assert exc_info.value.status_code == 415

    def test_transcribe_audio_too_long(self, monkeypatch):
        from fastapi import HTTPException
        vp = _make_processor(monkeypatch, voice_enabled="true", max_audio_s="5")
        audio = _make_wav_bytes(10.0)
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                vp.transcribe(audio, "long.wav")
            )
        assert exc_info.value.status_code == 413
        assert exc_info.value.detail["error"] == "audio_too_long"

    def test_transcribe_corrupted_file(self, monkeypatch):
        from fastapi import HTTPException
        vp = _make_processor(monkeypatch, voice_enabled="true")
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                vp.transcribe(b"garbage bytes", "broken.wav")
            )
        assert exc_info.value.status_code == 422

    def test_audio_conversion_to_16khz_mono(self, monkeypatch):
        """Verify pydub conversion chain: set_frame_rate(16000), set_channels(1)."""
        import types
        calls = []

        fw = types.ModuleType("faster_whisper")

        class FakeModel:
            def transcribe(self, buf, language=None, beam_size=5):
                import types as t
                return iter([t.SimpleNamespace(text="ok")]), t.SimpleNamespace(language="fr")
        fw.WhisperModel = lambda *a, **kw: FakeModel()
        sys.modules["faster_whisper"] = fw

        vp = _make_processor(monkeypatch, voice_enabled="true", stt_backend="faster_whisper")
        vp._whisper_model = FakeModel()

        # Set pydub mock AFTER _make_processor (which clears it)
        pydub_mod = types.ModuleType("pydub")

        class FakeSeg:
            duration_seconds = 1.0

            @classmethod
            def from_file(cls, *a, **kw):
                return cls()

            def set_frame_rate(self, r):
                calls.append(("set_frame_rate", r))
                return self

            def set_channels(self, c):
                calls.append(("set_channels", c))
                return self

            def set_sample_width(self, w):
                calls.append(("set_sample_width", w))
                return self

            def export(self, buf, format):
                buf.write(b"wav_data")
        pydub_mod.AudioSegment = FakeSeg
        sys.modules["pydub"] = pydub_mod

        asyncio.get_event_loop().run_until_complete(
            vp.transcribe(_make_wav_bytes(1.0), "stereo.wav")
        )
        assert ("set_frame_rate", 16000) in calls
        assert ("set_channels", 1) in calls
        assert ("set_sample_width", 2) in calls


# ---------------------------------------------------------------------------
# Task 5 tests: synthesize
# ---------------------------------------------------------------------------

class TestSynthesize:
    def test_synthesize_fr_siwis_alias(self, monkeypatch):
        """Mock httpx — verify alias fr_siwis resolves to fr_FR-siwis-medium."""
        captured = {}

        class FakeResp:
            status_code = 200
            content = b"mp3_audio_data"

            def raise_for_status(self):
                pass

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, url, **kwargs):
                captured["url"] = url
                captured["json"] = kwargs.get("json", {})
                return FakeResp()
        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

        vp = _make_processor(monkeypatch, voice_enabled="true", tts_model="fr_siwis")
        result = asyncio.get_event_loop().run_until_complete(
            vp.synthesize("Bonjour", "fr_siwis")
        )
        assert result == b"mp3_audio_data"
        assert captured["json"]["voice"] == "fr_FR-siwis-medium"

    def test_synthesize_text_truncation(self, monkeypatch):
        """3000-char text with max_tts_chars=2000 -> truncated on word boundary."""
        import httpx
        words = ["mot"] * 1000          # 1000 x "mot " = 4000 chars
        long_text = " ".join(words)
        captured = {}

        class FakeResp:
            status_code = 200
            content = b"audio"

            def raise_for_status(self):
                pass

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, url, **kwargs):
                captured["sent_text"] = kwargs["json"]["text"]
                return FakeResp()
        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

        vp = _make_processor(monkeypatch, voice_enabled="true", max_tts_chars="2000")
        asyncio.get_event_loop().run_until_complete(
            vp.synthesize(long_text, "fr_siwis")
        )
        sent = captured["sent_text"]
        assert len(sent) <= 2000
        # Must end on a complete word (no mid-word cut)
        assert not sent[-1].isalpha() or sent.endswith("mot")

    def test_synthesize_piper_unavailable(self, monkeypatch):
        """Piper returns 500 -> HTTP 502 raised."""
        from fastapi import HTTPException
        import httpx

        class FakeResp:
            status_code = 500
            content = b""

            def raise_for_status(self):
                raise httpx.HTTPStatusError("error", request=None, response=self)

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, url, **kwargs):
                return FakeResp()
        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

        vp = _make_processor(monkeypatch, voice_enabled="true")
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                vp.synthesize("Bonjour", "fr_siwis")
            )
        assert exc_info.value.status_code == 502

    def test_synthesize_unknown_alias_passthrough(self, monkeypatch):
        """Unknown alias is passed through as-is to Piper."""
        import httpx
        captured = {}

        class FakeResp:
            status_code = 200
            content = b"audio"

            def raise_for_status(self):
                pass

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, url, **kwargs):
                captured["voice"] = kwargs["json"]["voice"]
                return FakeResp()
        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

        vp = _make_processor(monkeypatch, voice_enabled="true")
        asyncio.get_event_loop().run_until_complete(
            vp.synthesize("test", "fr_FR-custom-voice")
        )
        assert captured["voice"] == "fr_FR-custom-voice"

    def test_synthesize_speed_passed_to_piper(self, monkeypatch):
        import httpx
        captured = {}

        class FakeResp:
            status_code = 200
            content = b"audio"

            def raise_for_status(self):
                pass

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, url, **kwargs):
                captured["speed"] = kwargs["json"]["speed"]
                return FakeResp()
        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

        vp = _make_processor(monkeypatch, voice_enabled="true", tts_speed="1.25")
        asyncio.get_event_loop().run_until_complete(
            vp.synthesize("test", "fr_siwis")
        )
        assert captured["speed"] == 1.25


# ---------------------------------------------------------------------------
# Task 6 tests: voice_chat
# ---------------------------------------------------------------------------
import sqlite3
import pathlib


class TestVoiceChat:
    def _make_vp_with_deps(self, monkeypatch, tmp_path):
        """Build a VoiceProcessor wired with mock STT, TTS, chat, and DB."""
        import types

        monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))

        fw = types.ModuleType("faster_whisper")

        class FakeModel:
            def transcribe(self, buf, language=None, beam_size=5):
                import types as t
                return iter([t.SimpleNamespace(text=" Bonjour")]), t.SimpleNamespace()
        fw.WhisperModel = lambda *a, **kw: FakeModel()
        sys.modules["faster_whisper"] = fw

        import httpx

        class FakeResp:
            status_code = 200
            content = b"mp3_bytes"

            def raise_for_status(self):
                pass

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, url, **kwargs):
                return FakeResp()
        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

        vp = _make_processor(monkeypatch, voice_enabled="true", stt_backend="faster_whisper")
        vp._whisper_model = FakeModel()

        # Stub pydub AFTER _make_processor (which clears sys.modules["pydub"])
        pydub_mod = types.ModuleType("pydub")

        class FakeSeg:
            duration_seconds = 2.0

            @classmethod
            def from_file(cls, *a, **kw):
                return cls()

            def set_frame_rate(self, r):
                return self

            def set_channels(self, c):
                return self

            def set_sample_width(self, w):
                return self

            def export(self, buf, format):
                buf.write(b"wav")
        pydub_mod.AudioSegment = FakeSeg
        sys.modules["pydub"] = pydub_mod

        # Create the voice_sessions table in tmp_path
        db = sqlite3.connect(str(tmp_path / "scheduler.db"))
        db.execute("""
            CREATE TABLE IF NOT EXISTS voice_sessions (
                id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                audio_duration_s REAL NOT NULL DEFAULT 0.0,
                transcription_chars INTEGER NOT NULL DEFAULT 0,
                tts_chars INTEGER NOT NULL DEFAULT 0,
                model_stt TEXT NOT NULL, model_tts TEXT NOT NULL,
                latency_ms INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ok'
            )
        """)
        db.commit()
        db.close()

        async def fake_handle_chat(message, session_id, source):
            return "Il fait beau."

        vp.set_dependencies({"handle_chat": fake_handle_chat,
                             "state_dir": str(tmp_path)})
        return vp

    def test_voice_chat_round_trip(self, monkeypatch, tmp_path):
        vp = self._make_vp_with_deps(monkeypatch, tmp_path)
        result = asyncio.get_event_loop().run_until_complete(
            vp.voice_chat(_make_wav_bytes(2.0), "test.wav", session_id="sess-1")
        )
        assert result.transcription == "Bonjour"
        assert result.response_text == "Il fait beau."
        assert result.audio_bytes == b"mp3_bytes"
        assert result.latency_ms >= 0

    def test_voice_chat_session_stored(self, monkeypatch, tmp_path):
        vp = self._make_vp_with_deps(monkeypatch, tmp_path)
        asyncio.get_event_loop().run_until_complete(
            vp.voice_chat(_make_wav_bytes(2.0), "test.wav", session_id="sess-2")
        )
        db = sqlite3.connect(str(tmp_path / "scheduler.db"))
        rows = db.execute("SELECT * FROM voice_sessions").fetchall()
        db.close()
        assert len(rows) == 1
        row = rows[0]
        # id, started_at, audio_duration_s, transcription_chars, tts_chars, model_stt, model_tts, latency_ms, status
        assert row[8] == "ok"                  # status
        assert row[3] > 0                      # transcription_chars
        assert row[4] > 0                      # tts_chars

    def test_pii_filter_on_transcription(self, monkeypatch, tmp_path):
        """Transcription containing a phone number -> chat receives redacted text."""
        import types

        monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
        monkeypatch.setenv("PII_FILTER_ENABLED", "true")

        fw = types.ModuleType("faster_whisper")

        class FakeModel:
            def transcribe(self, buf, language=None, beam_size=5):
                import types as t
                return iter([t.SimpleNamespace(text=" Mon numero est 514-555-1234")]), t.SimpleNamespace()
        fw.WhisperModel = lambda *a, **kw: FakeModel()
        sys.modules["faster_whisper"] = fw

        import httpx

        class FakeResp:
            status_code = 200
            content = b"audio"

            def raise_for_status(self):
                pass

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, *a, **kw):
                return FakeResp()
        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

        vp = _make_processor(monkeypatch, voice_enabled="true")
        vp._whisper_model = FakeModel()

        # Stub pydub AFTER _make_processor
        pydub_mod = types.ModuleType("pydub")

        class FakeSeg:
            duration_seconds = 1.0

            @classmethod
            def from_file(cls, *a, **kw):
                return cls()

            def set_frame_rate(self, r):
                return self

            def set_channels(self, c):
                return self

            def set_sample_width(self, w):
                return self

            def export(self, buf, format):
                buf.write(b"wav")
        pydub_mod.AudioSegment = FakeSeg
        sys.modules["pydub"] = pydub_mod

        db = sqlite3.connect(str(tmp_path / "scheduler.db"))
        db.execute("""
            CREATE TABLE IF NOT EXISTS voice_sessions (
                id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                audio_duration_s REAL NOT NULL DEFAULT 0.0,
                transcription_chars INTEGER NOT NULL DEFAULT 0,
                tts_chars INTEGER NOT NULL DEFAULT 0,
                model_stt TEXT NOT NULL, model_tts TEXT NOT NULL,
                latency_ms INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ok'
            )
        """)
        db.commit()
        db.close()

        received_messages = []

        async def capture_chat(message, session_id, source):
            received_messages.append(message)
            return "Reponse."

        vp.set_dependencies({"handle_chat": capture_chat, "state_dir": str(tmp_path)})
        asyncio.get_event_loop().run_until_complete(
            vp.voice_chat(_make_wav_bytes(1.0), "test.wav", session_id="sess-pii")
        )
        assert len(received_messages) == 1
        assert "514-555-1234" not in received_messages[0]
        assert "[REDACTED]" in received_messages[0] or "REDACTED" in received_messages[0]

    def test_voice_disabled_returns_503(self, monkeypatch, tmp_path):
        from fastapi import HTTPException
        vp = _make_processor(monkeypatch, voice_enabled="false")
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                vp.voice_chat(b"audio", "test.wav", session_id="x")
            )
        assert exc_info.value.status_code == 503
