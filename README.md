# AWS Speech-to-Speech Translation Pipeline

A production-grade CLI tool that orchestrates Amazon Transcribe, Translate, and Polly to convert English speech into translated speech in multiple target languages.

## Architecture

```
Local Audio File
       │
       ▼
┌─────────────┐     S3 Staging      ┌───────────────────┐
│  CLI Entry  │──────────────────►  │   Amazon S3       │
│   (cli.py)  │                     │  (temporary file) │
└─────┬───────┘                     └────────┬──────────┘
      │                                      │
      │                              ┌───────▼──────────┐
      │                              │ Amazon Transcribe │
      │          Transcript JSON     │  (async job)     │
      │◄─────────────────────────────│                  │
      │                              └──────────────────┘
      │
      ├─── Save dist/transcript.txt
      ├─── Save dist/low_confidence.json
      │
      │  For each target language:
      │
      ├──────────────────────────────►┌──────────────────┐
      │          Translated text      │ Amazon Translate  │
      │◄──────────────────────────────│                  │
      │                               └──────────────────┘
      │  Save dist/translations/{lang}.txt
      │
      ├──────────────────────────────►┌──────────────────┐
      │          MP3 audio stream     │  Amazon Polly    │
      │◄──────────────────────────────│                  │
      │                               └──────────────────┘
      │  Save dist/audio/{lang}.mp3
      │
      └─── Delete temporary S3 object
```

## Repository Structure

```
aws-speech-translation-pipeline/
├── .env.example          # Required environment variables (no real secrets)
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── README.md
├── src/
│   ├── __init__.py
│   ├── cli.py            # CLI argument parsing and pipeline orchestration
│   ├── aws_clients.py    # Boto3 client initialisation
│   ├── s3_manager.py     # S3 upload/delete logic
│   ├── transcribe.py     # Async Transcribe job + confidence parsing
│   ├── translate.py      # Amazon Translate integration
│   └── polly.py          # Amazon Polly TTS synthesis
├── tests/
│   ├── __init__.py
│   ├── test_cli.py
│   ├── test_transcribe.py
│   ├── test_pipeline.py
│   └── test_polly.py
├── testdata/
│   └── README.md
└── dist/                 # Auto-generated outputs (git-ignored contents)
    ├── transcript.txt
    ├── low_confidence.json
    ├── translations/
    └── audio/
```

## Prerequisites

- **Docker** ≥ 20.10 and **docker-compose** ≥ 1.29 (recommended)
- Or **Python** ≥ 3.9 with pip
- An **AWS account** with the IAM permissions listed below

## AWS IAM Permissions

Create an IAM user or role with the following inline policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::YOUR_STAGING_BUCKET/*"
    },
    {
      "Effect": "Allow",
      "Action": ["transcribe:StartTranscriptionJob", "transcribe:GetTranscriptionJob"],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "translate:TranslateText",
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "polly:SynthesizeSpeech",
      "Resource": "*"
    }
  ]
}
```

## Environment Setup

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Fill in your real credentials in `.env` (never commit this file):
   ```
   AWS_ACCESS_KEY_ID=AKIA...
   AWS_SECRET_ACCESS_KEY=...
   AWS_DEFAULT_REGION=us-east-1
   S3_STAGING_BUCKET=my-pipeline-staging
   ```

3. Create your S3 staging bucket:
   ```bash
   aws s3 mb s3://my-pipeline-staging --region us-east-1
   ```

## Running with Docker (Recommended)

```bash
# Build the image (docker compose V2; use docker-compose on older installs)
docker compose build

# Run --help (no AWS credentials needed — works without a .env file)
docker compose run --rm app --help

# Run the full pipeline (requires .env with real credentials)
docker compose run --rm app --input testdata/sample.wav --targets es,fr

# Run with a custom output directory
docker compose run --rm app --input testdata/sample.wav --targets es,fr,de --out-dir results/
```

## Running Locally (without Docker)

```bash
# Install dependencies
pip install -r requirements.txt

# Run --help
python -m src.cli --help

# Run the pipeline
python -m src.cli --input testdata/sample.wav --targets es,fr
```

## Running Tests

### Unit tests (no AWS credentials required)

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

All 51 unit tests run with mocked AWS clients. No real credentials needed.

### Live AWS integration test

Requires real credentials in `.env` and a provisioned S3 bucket:

```bash
python -m src.cli --input testdata/sample.wav --targets es,fr
# Then verify:
ls dist/translations/   # es.txt  fr.txt
ls dist/audio/          # es.mp3  fr.mp3
cat dist/transcript.txt
cat dist/low_confidence.json
```

## Expected Output

After a successful run with `--targets es,fr`:

```
dist/
├── transcript.txt          # "Hello world, this is a test."
├── low_confidence.json     # [] or [{"word":"...","confidence":0.xx,...}]
├── translations/
│   ├── es.txt              # "Hola mundo, esto es una prueba."
│   └── fr.txt              # "Bonjour monde, c'est un test."
└── audio/
    ├── es.mp3              # Playable MP3 (Polly Lupe voice)
    └── fr.mp3              # Playable MP3 (Polly Celine voice)
```

### Example `low_confidence.json`

```json
[
  {
    "word": "ambiguous",
    "confidence": 0.65,
    "start_time": "1.20",
    "end_time": "1.85"
  }
]
```

When no low-confidence words are found, the file contains an empty array `[]`. The pipeline **always** creates this file and **never** aborts translation due to low-confidence words.

## Supported Languages and Polly Voices

| Code | Language   | Polly Voice |
|------|------------|-------------|
| `es` | Spanish    | Lupe        |
| `fr` | French     | Celine      |
| `de` | German     | Marlene     |
| `pt` | Portuguese | Ines        |
| `it` | Italian    | Carla       |
| `ja` | Japanese   | Mizuki      |
| `ko` | Korean     | Seoyeon     |
| `zh` | Chinese    | Zhiyu       |
| `ar` | Arabic     | Zeina       |
| `hi` | Hindi      | Aditi       |
| `ru` | Russian    | Tatyana     |
| `nl` | Dutch      | Lotte       |
| `pl` | Polish     | Ewa         |
| `tr` | Turkish    | Filiz       |
| `sv` | Swedish    | Astrid      |

> **Note**: Polly voice availability varies by AWS region. `us-east-1` supports the broadest set.

## Troubleshooting

### `AccessDenied` on S3 upload
Verify your IAM user has `s3:PutObject` on the specific bucket ARN, not a wildcard.

### `AccessDenied` when downloading transcript
The `TranscriptFileUri` returned by Transcribe is a pre-signed URL — the pipeline downloads it via `requests.get()`, not boto3, which is correct.

### Transcribe job timeout
The default timeout is 300 seconds (5 minutes). For longer audio files, you may need to modify `TranscribeManager(timeout=600)` in `cli.py`.

### `InvalidSampleRateException`
Transcribe requires audio sampled at ≥ 8 000 Hz. Re-encode with:
```bash
ffmpeg -i input.ogg -ar 16000 output.wav
```

### MP3 file is 0 bytes
This happens when the Polly `AudioStream` is read as text. The implementation always uses `stream.read()` and writes with binary mode `'wb'`.

### Polly `TextLengthExceededException`
Polly's `SynthesizeSpeech` accepts up to 3 000 billed characters. The pipeline raises `ValueError` and exits cleanly for oversized input before calling the API.

### Unsupported language code
If you pass a language code not in the voice map, the pipeline exits with a clear error listing all supported codes *before* making any AWS calls.

## AWS Cost Considerations

- **S3**: Staging files are deleted immediately after transcription completes (in a `finally` block — always runs on success *and* failure).
- **Transcribe**: Free tier covers 60 minutes/month for the first 12 months.
- **Translate**: Free tier covers 2 million characters/month for the first 12 months.
- **Polly**: Free tier covers 5 million characters/month for the first 12 months.

## Security

- Credentials are read exclusively from environment variables via `python-dotenv` + boto3.
- `.env` is git-ignored and must never be committed.
- S3 staging objects use a UUID-prefixed key (`staging/<uuid8>/<filename>`) and are deleted after use.
- No credentials are baked into the Docker image; they are injected at runtime via `.env`.

## Mocked vs Live Tests

| Test file | Requires AWS? | Mechanism |
|-----------|--------------|-----------|
| `tests/test_cli.py` | No | subprocess + `unittest.mock` |
| `tests/test_transcribe.py` | No | `MagicMock` + `responses` library |
| `tests/test_pipeline.py` | No | `MagicMock` |
| `tests/test_polly.py` | No | `MagicMock` |
| Live pipeline run | **Yes** | Real AWS credentials in `.env` |
