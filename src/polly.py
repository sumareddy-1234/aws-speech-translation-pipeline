"""
Amazon Polly text-to-speech synthesis.
"""
import logging
from pathlib import Path
from typing import Dict

from botocore.exceptions import BotoCoreError, ClientError

from .aws_clients import get_polly_client

logger = logging.getLogger(__name__)

# Maximum characters Polly's SynthesizeSpeech API accepts (billed characters).
_POLLY_MAX_CHARS = 3_000

# Language code → Polly VoiceId mapping.  Add more voices here as needed.
VOICE_MAP: Dict[str, str] = {
    "es": "Lupe",
    "fr": "Celine",
    "de": "Marlene",
    "pt": "Ines",
    "it": "Carla",
    "ja": "Mizuki",
    "ko": "Seoyeon",
    "zh": "Zhiyu",
    "ar": "Zeina",
    "hi": "Aditi",
    "ru": "Tatyana",
    "nl": "Lotte",
    "pl": "Ewa",
    "tr": "Filiz",
    "sv": "Astrid",
    "da": "Naja",
    "nb": "Liv",
    "ro": "Carmen",
    "is": "Dora",
    "cy": "Gwyneth",
}

# Voices that require Engine="neural".  All others default to Engine="standard".
# Source: https://docs.aws.amazon.com/polly/latest/dg/voicelist.html
_NEURAL_VOICES = {
    "Lupe",      # es-US
    "Seoyeon",   # ko-KR
    "Zhiyu",     # cmn-CN
    "Takumi",    # ja-JP
    "Kazuha",    # ja-JP
    "Tomoko",    # ja-JP
    "Aria",      # en-NZ
    "Ayanda",    # en-ZA
    "Hiujin",    # zh-HK
}

# Valid MP3 magic bytes accepted from Polly:
#   ID3  — ID3v2 tag (most common Polly output)
#   0xFF 0xFB — MPEG-1 Layer 3, 128 kbps CBR, no CRC
#   0xFF 0xFA — MPEG-1 Layer 3, CBR, CRC protected  ← added
#   0xFF 0xF3 — MPEG-2 Layer 3, no CRC
#   0xFF 0xF2 — MPEG-2 Layer 3, CRC protected
#   0xFF 0xE3 — MPEG-2.5 Layer 3 frame sync variant
_MP3_MAGIC = (
    b"ID3",
    b"\xff\xfb",
    b"\xff\xfa",   # CRC-protected MPEG-1 Layer 3 — Polly emits this occasionally
    b"\xff\xf3",
    b"\xff\xf2",
    b"\xff\xe3",
)


class PollyManager:
    """Synthesises speech via Amazon Polly and validates the output."""

    def __init__(self):
        self._client = get_polly_client()

    def synthesize(self, text: str, language_code: str, output_path: str) -> None:
        """
        Synthesise *text* into an MP3 file at *output_path*.

        Args:
            text: Translated text to synthesise.
            language_code: Target language ISO 639-1 code (e.g. ``'es'``).
            output_path: Destination path for the MP3 file.

        Raises:
            ValueError: For unsupported languages, oversized text, or corrupt audio.
            RuntimeError: On AWS API errors.
        """
        voice_id = self._resolve_voice(language_code)
        engine = "neural" if voice_id in _NEURAL_VOICES else "standard"

        if len(text) > _POLLY_MAX_CHARS:
            raise ValueError(
                f"Text for '{language_code}' is {len(text)} chars, which exceeds Polly's "
                f"{_POLLY_MAX_CHARS}-char limit. Chunk the text before synthesising."
            )

        logger.info(
            "Synthesising speech for '%s' using VoiceId='%s' engine='%s' (%d chars).",
            language_code,
            voice_id,
            engine,
            len(text),
        )
        try:
            response = self._client.synthesize_speech(
                Text=text,
                OutputFormat="mp3",
                VoiceId=voice_id,
                Engine=engine,
            )
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError(
                f"Polly synthesis error for language '{language_code}' (voice '{voice_id}'): {exc}"
            ) from exc

        audio_bytes = self._read_stream(response)
        self._validate_mp3(audio_bytes, language_code)
        self._write_audio(audio_bytes, output_path)
        logger.info("Audio saved to '%s' (%d bytes).", output_path, len(audio_bytes))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_voice(language_code: str) -> str:
        """Return the VoiceId for *language_code* or raise ValueError."""
        voice = VOICE_MAP.get(language_code.lower())
        if not voice:
            supported = ", ".join(sorted(VOICE_MAP))
            raise ValueError(
                f"No Polly voice configured for language code '{language_code}'. "
                f"Supported codes: {supported}"
            )
        return voice

    @staticmethod
    def _read_stream(response: dict) -> bytes:
        """Safely read the StreamingBody from a Polly response."""
        stream = response.get("AudioStream")
        if stream is None:
            raise RuntimeError("Polly response contained no AudioStream.")
        try:
            data = stream.read()
        finally:
            # Close streaming body to release the underlying HTTP connection.
            try:
                stream.close()
            except Exception:
                pass
        return data

    @staticmethod
    def _validate_mp3(data: bytes, language_code: str) -> None:
        """Raise ValueError if *data* does not look like an MP3 file."""
        if not data:
            raise ValueError(
                f"Polly returned 0 bytes for language '{language_code}'. "
                "The voice or region may be unsupported."
            )
        if not any(data.startswith(magic) for magic in _MP3_MAGIC):
            raise ValueError(
                f"Audio data for '{language_code}' does not start with a recognised "
                "MP3 magic signature (ID3 or MPEG frame sync)."
            )

    @staticmethod
    def _write_audio(data: bytes, output_path: str) -> None:
        """Write binary audio data to disk, creating parent directories."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
