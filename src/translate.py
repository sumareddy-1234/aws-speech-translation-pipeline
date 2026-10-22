"""
Amazon Translate integration.
"""
import logging

from botocore.exceptions import BotoCoreError, ClientError

from .aws_clients import get_translate_client

logger = logging.getLogger(__name__)

# Amazon Translate maximum text size (UTF-8 bytes).
_MAX_BYTES = 10_000


class TranslateManager:
    """Wraps Amazon Translate for single-request text translation."""

    def __init__(self):
        self._client = get_translate_client()

    def translate_text(self, text: str, source_lang: str, target_lang: str) -> str:
        """
        Translate *text* from *source_lang* to *target_lang*.

        Args:
            text: Source text (English transcript).
            source_lang: BCP-47 source language code (e.g. ``'en'``).
            target_lang: BCP-47 target language code (e.g. ``'es'``).

        Returns:
            Translated text as a plain string.

        Raises:
            ValueError: If text exceeds Amazon Translate's character limit.
            RuntimeError: On AWS API errors.
        """
        encoded = text.encode("utf-8")
        if len(encoded) > _MAX_BYTES:
            raise ValueError(
                f"Input text is {len(encoded)} UTF-8 bytes, which exceeds Amazon Translate's "
                f"{_MAX_BYTES}-byte limit. Shorten the input or chunk the text before translating."
            )

        logger.info("Translating text from '%s' to '%s' (%d chars).", source_lang, target_lang, len(text))
        try:
            response = self._client.translate_text(
                Text=text,
                SourceLanguageCode=source_lang,
                TargetLanguageCode=target_lang,
            )
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError(
                f"Amazon Translate error ({source_lang}\u2192{target_lang}): {exc}"
            ) from exc

        translated = response.get("TranslatedText", "")
        logger.info(
            "Translation to '%s' complete (%d chars).", target_lang, len(translated)
        )
        return translated
