FROM python:3.11-slim

WORKDIR /app

# No system packages needed — boto3 talks to AWS APIs over HTTPS.
# ffmpeg is listed in README as an optional tool for pre-processing audio
# *before* passing it to this CLI, not as a runtime dependency of the CLI itself.

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p dist/translations dist/audio

ENTRYPOINT ["python", "-m", "src.cli"]
