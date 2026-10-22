"""
Tests for PollyManager: voice selection, audio writing, MP3 validation.
"""
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.polly import PollyManager, VOICE_MAP


# ---------------------------------------------------------------------------
# Voice resolution
# ---------------------------------------------------------------------------

class TestVoiceResolution:
    def test_spanish_maps_to_lupe(self):
        assert VOICE_MAP["es"] == "Lupe"

    def test_french_maps_to_celine(self):
        assert VOICE_MAP["fr"] == "Celine"

    def test_german_maps_to_marlene(self):
        assert VOICE_MAP["de"] == "Marlene"

    def test_unsupported_language_raises(self):
        with pytest.raises(ValueError, match="No Polly voice"):
            PollyManager._resolve_voice("xx")

    def test_all_required_voices_present(self):
        for code in ("es", "fr", "de"):
            assert code in VOICE_MAP, f"Missing required voice for '{code}'"


# ---------------------------------------------------------------------------
# MP3 validation
# ---------------------------------------------------------------------------

class TestMp3Validation:
    def test_valid_id3_header_passes(self):
        PollyManager._validate_mp3(b"ID3" + b"\x00" * 100, "es")

    def test_valid_mpeg_sync_0xfb_passes(self):
        PollyManager._validate_mp3(b"\xff\xfb" + b"\x00" * 100, "es")

    def test_valid_mpeg_sync_0xfa_passes(self):
        # 0xFF 0xFA = MPEG-1 Layer 3 with CRC — Polly emits this on some regions.
        PollyManager._validate_mp3(b"\xff\xfa" + b"\x00" * 100, "es")

    def test_empty_bytes_raises(self):
        with pytest.raises(ValueError, match="0 bytes"):
            PollyManager._validate_mp3(b"", "es")

    def test_invalid_magic_raises(self):
        with pytest.raises(ValueError, match="magic"):
            PollyManager._validate_mp3(b"RIFF" + b"\x00" * 100, "es")


class TestEngineSelection:
    """Verify that neural-only voices get Engine='neural' and others get 'standard'."""

    def _make_manager(self):
        mgr = PollyManager.__new__(PollyManager)
        mgr._client = MagicMock()
        return mgr

    def _mp3_stream(self):
        mock = MagicMock()
        mock.read.return_value = b"ID3" + b"\x00" * 100
        return mock

    def test_lupe_uses_neural_engine(self, tmp_path):
        """Lupe (es) is neural-only — must pass Engine='neural'."""
        mgr = self._make_manager()
        mgr._client.synthesize_speech.return_value = {"AudioStream": self._mp3_stream()}
        mgr.synthesize("Hola.", "es", str(tmp_path / "es.mp3"))
        call_kwargs = mgr._client.synthesize_speech.call_args[1]
        assert call_kwargs["Engine"] == "neural"
        assert call_kwargs["VoiceId"] == "Lupe"

    def test_celine_uses_standard_engine(self, tmp_path):
        """Celine (fr) is a standard voice — must pass Engine='standard'."""
        mgr = self._make_manager()
        mgr._client.synthesize_speech.return_value = {"AudioStream": self._mp3_stream()}
        mgr.synthesize("Bonjour.", "fr", str(tmp_path / "fr.mp3"))
        call_kwargs = mgr._client.synthesize_speech.call_args[1]
        assert call_kwargs["Engine"] == "standard"
        assert call_kwargs["VoiceId"] == "Celine"

    def test_marlene_uses_standard_engine(self, tmp_path):
        """Marlene (de) is a standard voice — must pass Engine='standard'."""
        mgr = self._make_manager()
        mgr._client.synthesize_speech.return_value = {"AudioStream": self._mp3_stream()}
        mgr.synthesize("Hallo.", "de", str(tmp_path / "de.mp3"))
        call_kwargs = mgr._client.synthesize_speech.call_args[1]
        assert call_kwargs["Engine"] == "standard"
        assert call_kwargs["VoiceId"] == "Marlene"


# ---------------------------------------------------------------------------
# Full synthesise mock
# ---------------------------------------------------------------------------

class TestSynthesize:
    def _make_manager(self):
        mgr = PollyManager.__new__(PollyManager)
        mgr._client = MagicMock()
        return mgr

    def test_synthesize_writes_mp3(self, tmp_path):
        mgr = self._make_manager()
        mp3_data = b"ID3" + b"\x00" * 500

        stream_mock = MagicMock()
        stream_mock.read.return_value = mp3_data

        mgr._client.synthesize_speech.return_value = {"AudioStream": stream_mock}

        output = str(tmp_path / "output.mp3")
        mgr.synthesize("Hola mundo.", "es", output)

        assert Path(output).exists()
        assert Path(output).read_bytes() == mp3_data

    def test_synthesize_calls_polly_with_correct_voice(self, tmp_path):
        mgr = self._make_manager()
        mp3_data = b"ID3" + b"\x00" * 200

        stream_mock = MagicMock()
        stream_mock.read.return_value = mp3_data
        mgr._client.synthesize_speech.return_value = {"AudioStream": stream_mock}

        mgr.synthesize("Bonjour.", "fr", str(tmp_path / "fr.mp3"))

        mgr._client.synthesize_speech.assert_called_once_with(
            Text="Bonjour.",
            OutputFormat="mp3",
            VoiceId="Celine",
            Engine="standard",   # Celine is a standard voice
        )

    def test_synthesize_unsupported_language_raises(self, tmp_path):
        mgr = self._make_manager()
        with pytest.raises(ValueError, match="No Polly voice"):
            mgr.synthesize("Hello.", "xx", str(tmp_path / "xx.mp3"))

    def test_synthesize_oversized_text_raises(self, tmp_path):
        mgr = self._make_manager()
        big_text = "a" * 3_001
        with pytest.raises(ValueError, match="limit"):
            mgr.synthesize(big_text, "es", str(tmp_path / "big.mp3"))

    def test_synthesize_creates_parent_dirs(self, tmp_path):
        mgr = self._make_manager()
        mp3_data = b"ID3" + b"\x00" * 200

        stream_mock = MagicMock()
        stream_mock.read.return_value = mp3_data
        mgr._client.synthesize_speech.return_value = {"AudioStream": stream_mock}

        nested_output = str(tmp_path / "deep" / "nested" / "dir" / "audio.mp3")
        mgr.synthesize("Hola.", "es", nested_output)
        assert Path(nested_output).exists()

    def test_write_audio_creates_file(self, tmp_path):
        """_write_audio is a static helper — test in isolation."""
        data = b"ID3" + b"\x00" * 50
        out = tmp_path / "sub" / "test.mp3"
        PollyManager._write_audio(data, str(out))
        assert out.read_bytes() == data

    def test_read_stream_closes_on_error(self):
        """_read_stream must close the stream even when read() raises."""
        stream_mock = MagicMock()
        stream_mock.read.side_effect = OSError("network error")
        with pytest.raises(OSError):
            PollyManager._read_stream({"AudioStream": stream_mock})
        stream_mock.close.assert_called_once()

    def test_read_stream_none_raises(self):
        """Missing AudioStream in response must raise RuntimeError."""
        with pytest.raises(RuntimeError, match="no AudioStream"):
            PollyManager._read_stream({})
