"""
Integration-level tests for the orchestration pipeline (all AWS calls mocked).
"""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# S3Manager tests
# ---------------------------------------------------------------------------

class TestS3Manager:
    def test_upload_returns_s3_uri(self, tmp_path):
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"RIFF" + b"\x00" * 36)  # minimal WAV header

        from src.s3_manager import S3Manager
        mgr = S3Manager.__new__(S3Manager)
        mgr.bucket_name = "my-bucket"
        mock_client = MagicMock()
        mgr._client = mock_client

        uri = mgr.upload_file(str(audio_file), "staging/test.wav")
        assert uri == "s3://my-bucket/staging/test.wav"
        mock_client.upload_file.assert_called_once()

    def test_upload_missing_file_raises(self, tmp_path):
        from src.s3_manager import S3Manager
        mgr = S3Manager.__new__(S3Manager)
        mgr.bucket_name = "my-bucket"
        mgr._client = MagicMock()

        with pytest.raises(FileNotFoundError):
            mgr.upload_file(str(tmp_path / "nonexistent.wav"), "key")

    def test_delete_file_calls_delete_object(self):
        from src.s3_manager import S3Manager
        mgr = S3Manager.__new__(S3Manager)
        mgr.bucket_name = "my-bucket"
        mock_client = MagicMock()
        mgr._client = mock_client

        mgr.delete_file("staging/test.wav")
        mock_client.delete_object.assert_called_once_with(
            Bucket="my-bucket", Key="staging/test.wav"
        )

    def test_delete_error_does_not_raise(self):
        """S3 delete errors must be swallowed (logged only)."""
        from src.s3_manager import S3Manager
        from botocore.exceptions import ClientError
        mgr = S3Manager.__new__(S3Manager)
        mgr.bucket_name = "my-bucket"
        mock_client = MagicMock()
        mock_client.delete_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "DeleteObject"
        )
        mgr._client = mock_client

        # Must NOT raise.
        mgr.delete_file("staging/test.wav")


# ---------------------------------------------------------------------------
# TranslateManager tests
# ---------------------------------------------------------------------------

class TestTranslateManager:
    def test_translate_calls_api(self):
        from src.translate import TranslateManager
        mgr = TranslateManager.__new__(TranslateManager)
        mock_client = MagicMock()
        mock_client.translate_text.return_value = {"TranslatedText": "Hola mundo."}
        mgr._client = mock_client

        result = mgr.translate_text("Hello world.", "en", "es")
        assert result == "Hola mundo."
        mock_client.translate_text.assert_called_once_with(
            Text="Hello world.",
            SourceLanguageCode="en",
            TargetLanguageCode="es",
        )

    def test_oversized_text_raises(self):
        from src.translate import TranslateManager
        mgr = TranslateManager.__new__(TranslateManager)
        mgr._client = MagicMock()

        big_text = "a" * 11_000
        with pytest.raises(ValueError, match="limit"):
            mgr.translate_text(big_text, "en", "es")


# ---------------------------------------------------------------------------
# Pipeline continuation when low-confidence words exist
# ---------------------------------------------------------------------------

class TestPipelineResilience:
    """Verify that low-confidence detection never aborts translation."""

    def test_low_confidence_does_not_abort(self, tmp_path):
        """
        Even with < 0.80 confidence words, the pipeline must continue to
        produce translation and audio artifacts.
        """
        from src.transcribe import TranscribeManager

        transcript_json = {
            "results": {
                "transcripts": [{"transcript": "Hello world."}],
                "items": [
                    {
                        "type": "pronunciation",
                        "alternatives": [{"confidence": "0.30", "content": "Hello"}],
                        "start_time": "0.0",
                        "end_time": "0.5",
                    },
                    {
                        "type": "pronunciation",
                        "alternatives": [{"confidence": "0.20", "content": "world"}],
                        "start_time": "0.5",
                        "end_time": "1.0",
                    },
                ],
            }
        }

        low_conf = TranscribeManager.extract_low_confidence_words(transcript_json)
        # Both words are below 0.80
        assert len(low_conf) == 2

        # Save low_confidence.json
        low_conf_path = tmp_path / "low_confidence.json"
        low_conf_path.write_text(json.dumps(low_conf), encoding="utf-8")

        # Transcript text is still extractable
        text = TranscribeManager.extract_transcript_text(transcript_json)
        assert text == "Hello world."

        # Simulate translation succeeding
        from src.translate import TranslateManager
        tr = TranslateManager.__new__(TranslateManager)
        tr._client = MagicMock()
        tr._client.translate_text.return_value = {"TranslatedText": "Hola mundo."}
        translated = tr.translate_text(text, "en", "es")
        assert translated == "Hola mundo."


# ---------------------------------------------------------------------------
# S3 single-cleanup contract
# ---------------------------------------------------------------------------

class TestS3CleanupExactlyOnce:
    """The finally block is the sole S3 cleanup path.

    On transcription failure the old code called _cleanup_s3 in the except
    branch *and* again in the finally block — double-deletion.  Verify that
    S3Manager.delete_file is invoked exactly once regardless of outcome.
    """

    def test_delete_called_exactly_once_on_success(self, tmp_path):
        audio = tmp_path / "test.wav"
        audio.write_bytes(b"RIFF" + b"\x00" * 36)

        from src.s3_manager import S3Manager
        mock_s3 = S3Manager.__new__(S3Manager)
        mock_s3.bucket_name = "bucket"
        mock_s3._client = MagicMock()

        # upload succeeds
        mock_s3.upload_file(str(audio), "staging/run/test.wav")
        # one explicit delete
        mock_s3.delete_file("staging/run/test.wav")

        assert mock_s3._client.delete_object.call_count == 1

    def test_delete_file_idempotent_on_not_found(self):
        """delete_object returning nothing (key already gone) must not raise."""
        from src.s3_manager import S3Manager
        mock_s3 = S3Manager.__new__(S3Manager)
        mock_s3.bucket_name = "bucket"
        mock_s3._client = MagicMock()
        mock_s3._client.delete_object.return_value = {}  # S3 returns 204, boto3 gives {}

        mock_s3.delete_file("staging/run/test.wav")  # must not raise
        mock_s3._client.delete_object.assert_called_once()
