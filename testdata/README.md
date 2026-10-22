# Test Data

This directory stores fixture audio files used by tests and the CI pipeline.

## Adding your own test audio

For live AWS integration testing, drop an English WAV or MP3 file here and run:

```bash
python src/cli.py --input testdata/your_file.wav --targets es,fr
```

A suitable royalty-free English sample can be downloaded from:
https://commons.wikimedia.org/wiki/File:En-us-hello-3.ogg

Then convert it to WAV:
```bash
ffmpeg -i En-us-hello-3.ogg -ar 16000 testdata/sample.wav
```

> **Never commit audio files larger than a few kilobytes to version control.**
