#!/usr/bin/env bash
# Linux: install dependencies (venv + pip + ffmpeg static build). Add --with-whisper for auto-captions.
cd "$(dirname "$0")" && python3 launcher/bootstrap.py install "$@"
