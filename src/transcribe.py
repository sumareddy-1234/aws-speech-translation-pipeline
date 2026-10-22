"""
Amazon Transcribe: async job submission, polling, and JSON parsing.
"""
import json
import logging
import time
from typing import Any, Dict, List, Optional

import requests
from botocore.exceptions import BotoCoreError, ClientError

from .aws_clients import get_transcribe_client

logger = logging.getLogger(__name__)

# Confidence threshold below which words are flagged.
CONFIDENCE_THRESHOLD = 0.80

# Mapping of file extensions to Transcribe MediaFormat values.
MEDIA_FORMAT_MAP = {
    ".mp3": "mp3",
    ".mp4": "mp4",
    ".wav": "wav",
    ".flac": "flac",
    ".ogg": "ogg",
    ".webm": "webm",
    ".amr": "amr",
}


class TranscribeManager:
    """Submits and monitors Amazon Transcribe jobs."""

    def __init__(self, poll_interval: int = 5, timeout: int = 300):
        """
        Args:
            poll_interval: Seconds between status polls.
            timeout: Maximum seconds to wait before raising TimeoutError.
        """
        self._client = get_transcribe_client()
        self.poll_interval = poll_interval
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_and_wait(self, job_name: str, s3_uri: str, media_format: Optional[str] = None) -> Dict[str, Any]:
        """
        Start an asynchronous English transcription job and poll until it
        reaches COMPLETED or FAILED.

        Args:
            job_name: Unique name for this Transcribe job.
            s3_uri: S3 URI of the staged audio file.
            media_format: Optional override for media format (e.g. 'wav').

        Returns:
            Parsed JSON response dict downloaded from TranscriptFileUri.

        Raises:
            ValueError: For invalid arguments.
            RuntimeError: If the job fails or the JSON is malformed.
            TimeoutError: If the job does not complete within *timeout* seconds.
        """
        fmt = media_format or self._infer_format(s3_uri)

        logger.info("Starting Transcribe job '%s' (format=%s)", job_name, fmt)
        try:
            self._client.start_transcription_job(
                TranscriptionJobName=job_name,
                Media={"MediaFileUri": s3_uri},
                MediaFormat=fmt,
                LanguageCode="en-US",
            )
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError(f"Could not start Transcribe job '{job_name}': {exc}") from exc

        transcript_uri = self._poll(job_name)
        return self._download_transcript(transcript_uri)

    # ------------------------------------------------------------------
    # Transcript helpers
    # ------------------------------------------------------------------

    @staticmethod
    def extract_transcript_text(transcript_json: Dict[str, Any]) -> str:
        """
        Extract the best-guess transcript string from the Transcribe JSON.

        Raises:
            RuntimeError: If the expected fields are absent.
        """
        try:
            text = transcript_json["results"]["transcripts"][0]["transcript"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"Unexpected Transcribe JSON structure — cannot find transcript text: {exc}"
            ) from exc

        if not text or not text.strip():
            logger.warning("Transcribe returned an empty transcript.")
        return text

    @staticmethod
    def extract_low_confidence_words(
        transcript_json: Dict[str, Any],
        threshold: float = CONFIDENCE_THRESHOLD,
    ) -> List[Dict[str, Any]]:
        """
        Return pronunciation items whose confidence is below *threshold*.

        Each result dict has the shape::

            {
                "word": str,
                "confidence": float,
                "start_time": str,
                "end_time": str,
            }

        Missing or malformed confidence values are skipped (not treated as 0).
        """
        low_conf: List[Dict[str, Any]] = []

        items = []
        try:
            items = transcript_json["results"]["items"]
        except (KeyError, TypeError):
            logger.warning("No 'results.items' found in Transcribe JSON; skipping confidence extraction.")
            return low_conf

        for item in items:
            if item.get("type") != "pronunciation":
                continue

            alternatives = item.get("alternatives", [])
            if not alternatives:
                continue

            raw_conf = alternatives[0].get("confidence")
            if raw_conf is None:
                # Missing confidence — skip rather than treating as 0.
                continue

            try:
                conf = float(raw_conf)
            except (ValueError, TypeError):
                logger.debug("Non-numeric confidence value '%s'; skipping.", raw_conf)
                continue

            if conf < threshold:
                low_conf.append(
                    {
                        "word": alternatives[0].get("content", ""),
                        "confidence": conf,
                        "start_time": item.get("start_time", ""),
                        "end_time": item.get("end_time", ""),
                    }
                )

        logger.info("Found %d low-confidence word(s) (threshold=%.2f).", len(low_conf), threshold)
        return low_conf

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _poll(self, job_name: str) -> str:
        """Poll until the job is COMPLETED or FAILED; return TranscriptFileUri."""
        start = time.time()
        while True:
            elapsed = time.time() - start
            if elapsed > self.timeout:
                raise TimeoutError(
                    f"Transcribe job '{job_name}' did not complete within {self.timeout}s."
                )

            try:
                resp = self._client.get_transcription_job(TranscriptionJobName=job_name)
            except (BotoCoreError, ClientError) as exc:
                raise RuntimeError(
                    f"Error polling Transcribe job '{job_name}': {exc}"
                ) from exc

            job = resp["TranscriptionJob"]
            status = job["TranscriptionJobStatus"]
            logger.debug("Job '%s' status: %s (%.0fs elapsed)", job_name, status, elapsed)

            if status == "COMPLETED":
                uri = job["Transcript"]["TranscriptFileUri"]
                logger.info("Job '%s' completed. Transcript URI: %s", job_name, uri)
                return uri

            if status == "FAILED":
                reason = job.get("FailureReason", "unknown reason")
                raise RuntimeError(
                    f"Transcribe job '{job_name}' failed: {reason}"
                )

            logger.info(
                "Job '%s' is %s — waiting %ds\u2026", job_name, status, self.poll_interval
            )
            time.sleep(self.poll_interval)

    @staticmethod
    def _download_transcript(uri: str) -> Dict[str, Any]:
        """Download transcript JSON from a pre-signed URL."""
        logger.info("Downloading transcript from %s", uri)
        try:
            response = requests.get(uri, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Failed to download transcript from '{uri}': {exc}"
            ) from exc

        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Transcript response from '{uri}' is not valid JSON: {exc}"
            ) from exc

    @staticmethod
    def _infer_format(s3_uri: str) -> str:
        """Infer MediaFormat from the S3 URI file extension."""
        ext = ""
        if "." in s3_uri.split("/")[-1]:
            ext = "." + s3_uri.split(".")[-1].lower()
        fmt = MEDIA_FORMAT_MAP.get(ext)
        if not fmt:
            raise ValueError(
                f"Cannot infer Transcribe media format from URI '{s3_uri}'. "
                f"Supported extensions: {list(MEDIA_FORMAT_MAP.keys())}"
            )
        return fmt
