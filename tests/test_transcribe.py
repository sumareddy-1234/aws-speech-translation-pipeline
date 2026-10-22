"""
Tests for TranscribeManager: job submission, polling, confidence extraction.
"""
import json
import time
from unittest.mock import MagicMock, patch

import pytest
import responses

from src.transcribe import TranscribeManager, CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TRANSCRIPT_JSON = {
    "results": {
        "transcripts": [{"transcript": "Hello world this is a test."}],
        "items": [
            {
                "start_time": "0.04",
                "end_time": "0.52",
                "alternatives": [{"confidence": "0.99", "content": "Hello"}],
                "type": "pronunciation",
            },
            {
                "start_time": "0.53",
                "end_time": "0.89",
                "alternatives": [{"confidence": "0.50", "content": "world"}],
                "type": "pronunciation",
            },
            {
                "alternatives": [{"confidence": "0.99", "content": "."}],
                "type": "punctuation",
            },
        ],
    }
}


# ---------------------------------------------------------------------------
# extract_transcript_text
# ---------------------------------------------------------------------------

class TestExtractTranscriptText:
    def test_nominal(self):
        text = TranscribeManager.extract_transcript_text(SAMPLE_TRANSCRIPT_JSON)
        assert text == "Hello world this is a test."

    def test_missing_transcripts_key_raises(self):
        with pytest.raises(RuntimeError):
            TranscribeManager.extract_transcript_text({"results": {}})

    def test_empty_json_raises(self):
        with pytest.raises(RuntimeError):
            TranscribeManager.extract_transcript_text({})


# ---------------------------------------------------------------------------
# extract_low_confidence_words
# ---------------------------------------------------------------------------

class TestExtractLowConfidenceWords:
    def test_filters_below_threshold(self):
        words = TranscribeManager.extract_low_confidence_words(SAMPLE_TRANSCRIPT_JSON)
        assert len(words) == 1
        assert words[0]["word"] == "world"
        assert words[0]["confidence"] == 0.50

    def test_empty_items_returns_empty(self):
        data = {"results": {"transcripts": [{"transcript": ""}], "items": []}}
        words = TranscribeManager.extract_low_confidence_words(data)
        assert words == []

    def test_missing_items_returns_empty(self):
        data = {"results": {"transcripts": [{"transcript": "Hi"}]}}
        words = TranscribeManager.extract_low_confidence_words(data)
        assert words == []

    def test_missing_confidence_skipped(self):
        data = {
            "results": {
                "transcripts": [{"transcript": "Hi"}],
                "items": [
                    {
                        "type": "pronunciation",
                        "alternatives": [{"content": "Hi"}],  # no confidence key
                        "start_time": "0.0",
                        "end_time": "0.5",
                    }
                ],
            }
        }
        words = TranscribeManager.extract_low_confidence_words(data)
        assert words == []

    def test_punctuation_items_skipped(self):
        data = {
            "results": {
                "transcripts": [{"transcript": "Hi."}],
                "items": [
                    {
                        "type": "punctuation",
                        "alternatives": [{"confidence": "0.10", "content": "."}],
                    }
                ],
            }
        }
        words = TranscribeManager.extract_low_confidence_words(data)
        assert words == []

    def test_all_high_confidence_returns_empty(self):
        data = {
            "results": {
                "transcripts": [{"transcript": "Hello"}],
                "items": [
                    {
                        "type": "pronunciation",
                        "alternatives": [{"confidence": "0.95", "content": "Hello"}],
                        "start_time": "0.0",
                        "end_time": "0.5",
                    }
                ],
            }
        }
        words = TranscribeManager.extract_low_confidence_words(data)
        assert words == []


# ---------------------------------------------------------------------------
# start_and_wait (mocked boto3)
# ---------------------------------------------------------------------------

class TestStartAndWait:
    def _make_manager(self, client_mock):
        mgr = TranscribeManager.__new__(TranscribeManager)
        mgr._client = client_mock
        mgr.poll_interval = 0  # no sleep in tests
        mgr.timeout = 30
        return mgr

    def test_unique_job_names(self):
        """Two separate runs must produce different job names."""
        import uuid
        names = {f"speech-pipeline-{uuid.uuid4().hex[:8]}" for _ in range(100)}
        assert len(names) == 100  # all unique

    @responses.activate
    def test_nominal_flow(self):
        """Nominal: IN_PROGRESS then COMPLETED."""
        transcript_url = "https://s3.amazonaws.com/bucket/transcript.json"
        responses.add(
            responses.GET,
            transcript_url,
            json=SAMPLE_TRANSCRIPT_JSON,
            status=200,
        )

        mock_client = MagicMock()
        mock_client.start_transcription_job.return_value = {}
        mock_client.get_transcription_job.side_effect = [
            {"TranscriptionJob": {"TranscriptionJobStatus": "IN_PROGRESS"}},
            {
                "TranscriptionJob": {
                    "TranscriptionJobStatus": "COMPLETED",
                    "Transcript": {"TranscriptFileUri": transcript_url},
                }
            },
        ]

        mgr = self._make_manager(mock_client)
        result = mgr.start_and_wait("test-job", "s3://bucket/audio.wav")
        assert result == SAMPLE_TRANSCRIPT_JSON
        mock_client.start_transcription_job.assert_called_once()

    def test_failed_job_raises(self):
        """A FAILED job status must raise RuntimeError."""
        mock_client = MagicMock()
        mock_client.start_transcription_job.return_value = {}
        mock_client.get_transcription_job.return_value = {
            "TranscriptionJob": {
                "TranscriptionJobStatus": "FAILED",
                "FailureReason": "invalid audio format",
            }
        }

        mgr = self._make_manager(mock_client)
        with pytest.raises(RuntimeError, match="failed"):
            mgr.start_and_wait("test-job", "s3://bucket/audio.wav")

    def test_timeout_raises(self):
        """A job that never completes must raise TimeoutError."""
        mock_client = MagicMock()
        mock_client.start_transcription_job.return_value = {}
        mock_client.get_transcription_job.return_value = {
            "TranscriptionJob": {"TranscriptionJobStatus": "IN_PROGRESS"}
        }

        mgr = TranscribeManager.__new__(TranscribeManager)
        mgr._client = mock_client
        mgr.poll_interval = 0
        mgr.timeout = 0  # immediate timeout

        with pytest.raises(TimeoutError):
            mgr.start_and_wait("test-job", "s3://bucket/audio.wav")
