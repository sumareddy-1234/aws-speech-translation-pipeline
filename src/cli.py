"""
CLI entry-point for the AWS Speech-to-Speech Translation Pipeline.

Usage:
    python src/cli.py --input speech.wav --targets es,fr --out-dir dist/
"""
import argparse
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# Load .env if present (never raises if the file is absent).
load_dotenv()

# Configure logging before importing internal modules.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s \u2014 %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("speech_pipeline")

from .s3_manager import S3Manager
from .transcribe import TranscribeManager
from .translate import TranslateManager
from .polly import PollyManager, VOICE_MAP


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="speech-pipeline",
        description=(
            "AWS Speech-to-Speech Translation Pipeline: transcribes English audio, "
            "translates to target languages, and synthesises speech using Amazon Polly."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python src/cli.py --input speech.wav --targets es,fr\n"
            "  python src/cli.py --input audio.mp3 --targets de --out-dir output/\n"
        ),
    )
    parser.add_argument(
        "--input",
        required=True,
        metavar="FILE",
        help="Path to the local source audio file (WAV, MP3, FLAC, OGG, etc.).",
    )
    parser.add_argument(
        "--targets",
        required=True,
        metavar="CODES",
        help="Comma-separated ISO 639-1 target language codes (e.g. es,fr,de).",
    )
    parser.add_argument(
        "--out-dir",
        default="dist/",
        metavar="DIR",
        help="Output directory for transcripts, translations, and audio (default: dist/).",
    )
    return parser


def parse_targets(raw: str) -> List[str]:
    """Parse, deduplicate, and validate target language codes."""
    codes = [c.strip().lower() for c in raw.split(",") if c.strip()]
    if not codes:
        raise argparse.ArgumentTypeError(
            "--targets must be a non-empty comma-separated list of language codes."
        )

    seen, unique = set(), []
    for code in codes:
        if code in seen:
            logger.warning("Duplicate language code '%s' ignored.", code)
        else:
            seen.add(code)
            unique.append(code)

    unsupported = [c for c in unique if c not in VOICE_MAP]
    if unsupported:
        raise ValueError(
            f"Unsupported Polly language code(s): {unsupported}. "
            f"Supported codes: {sorted(VOICE_MAP)}"
        )

    return unique


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------

def run_pipeline(input_path: str, target_codes: List[str], out_dir: str) -> None:
    """
    Execute the full speech-to-speech translation pipeline.

    Raises SystemExit on unrecoverable errors.
    """
    # --- Validate configuration ---
    bucket = os.environ.get("S3_STAGING_BUCKET", "").strip()
    if not bucket:
        logger.error(
            "S3_STAGING_BUCKET environment variable is not set. "
            "Copy .env.example to .env and fill in the required values."
        )
        sys.exit(1)

    audio_path = Path(input_path).resolve()
    if not audio_path.exists():
        logger.error("Input file '%s' does not exist.", audio_path)
        sys.exit(1)
    if not audio_path.is_file():
        logger.error("'%s' is not a regular file.", audio_path)
        sys.exit(1)

    # Prepare output directories.
    out = Path(out_dir)
    translations_dir = out / "translations"
    audio_dir = out / "audio"
    out.mkdir(parents=True, exist_ok=True)
    translations_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    # Unique identifiers for this run.
    run_id = uuid.uuid4().hex[:8]
    object_key = f"staging/{run_id}/{audio_path.name}"
    job_name = f"speech-pipeline-{run_id}"

    s3 = S3Manager(bucket)
    transcribe = TranscribeManager()
    translator = TranslateManager()
    polly = PollyManager()

    s3_uri = None

    # ------------------------------------------------------------------ #
    # Phase 1 — S3 staging                                                #
    # ------------------------------------------------------------------ #
    try:
        s3_uri = s3.upload_file(str(audio_path), object_key)
    except Exception as exc:
        logger.error("S3 upload failed: %s", exc)
        sys.exit(1)

    # ------------------------------------------------------------------ #
    # Phase 2 & 3 — Transcription + confidence parsing                    #
    # ------------------------------------------------------------------ #
    transcript_text = ""
    low_confidence = []

    try:
        transcript_json = transcribe.start_and_wait(job_name, s3_uri)

        # Save raw transcript text.
        transcript_text = TranscribeManager.extract_transcript_text(transcript_json)
        transcript_file = out / "transcript.txt"
        transcript_file.write_text(transcript_text, encoding="utf-8")
        logger.info("Transcript saved to '%s'.", transcript_file)

        # Extract low-confidence words (never aborts the pipeline).
        try:
            low_confidence = TranscribeManager.extract_low_confidence_words(transcript_json)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Low-confidence extraction failed (pipeline continues): %s", exc)
            low_confidence = []

        low_conf_file = out / "low_confidence.json"
        low_conf_file.write_text(
            json.dumps(low_confidence, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(
            "Low-confidence words (%d) saved to '%s'.", len(low_confidence), low_conf_file
        )

    except (RuntimeError, TimeoutError) as exc:
        logger.error("Transcription failed: %s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.error("Unexpected error during transcription: %s", exc, exc_info=True)
        sys.exit(1)
    finally:
        # S3 cleanup — always runs whether transcription succeeded or failed.
        if s3_uri:
            _cleanup_s3(s3, object_key)

    # ------------------------------------------------------------------ #
    # Phase 4 & 5 — Translation + speech synthesis                        #
    # ------------------------------------------------------------------ #
    pipeline_errors: List[str] = []

    for lang in target_codes:
        # --- Translate ---
        try:
            translated = translator.translate_text(transcript_text, "en", lang)
        except Exception as exc:  # noqa: BLE001
            msg = f"Translation to '{lang}' failed: {exc}"
            logger.error(msg)
            pipeline_errors.append(msg)
            continue  # Skip Polly for this language but continue with others.

        trans_file = translations_dir / f"{lang}.txt"
        trans_file.write_text(translated, encoding="utf-8")
        logger.info("Translation '%s' saved to '%s'.", lang, trans_file)

        # --- Polly ---
        try:
            audio_file = str(audio_dir / f"{lang}.mp3")
            polly.synthesize(translated, lang, audio_file)
        except Exception as exc:  # noqa: BLE001
            msg = f"Speech synthesis for '{lang}' failed: {exc}"
            logger.error(msg)
            pipeline_errors.append(msg)

    if pipeline_errors:
        logger.warning("Pipeline completed with %d error(s):", len(pipeline_errors))
        for err in pipeline_errors:
            logger.warning("  \u2022 %s", err)
        sys.exit(1)

    logger.info(
        "Pipeline complete. Outputs in '%s': transcript.txt, low_confidence.json, "
        "translations/, audio/.",
        out,
    )


def _cleanup_s3(s3: S3Manager, object_key: str) -> None:
    """Best-effort S3 cleanup — logs errors but never raises."""
    try:
        s3.delete_file(object_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("S3 cleanup warning: %s", exc)


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        target_codes = parse_targets(args.targets)
    except (ValueError, argparse.ArgumentTypeError) as exc:
        parser.error(str(exc))  # prints message and exits with code 2.
        return

    run_pipeline(args.input, target_codes, args.out_dir)


if __name__ == "__main__":
    main()
