#!/usr/bin/env bash
# Experimental Unix source setup: Python dependencies only. Supply reviewed FFmpeg/ffprobe first; optional --with-whisper is outside the Windows locks.
cd "$(dirname "$0")" && python3 launcher/bootstrap.py install "$@"
