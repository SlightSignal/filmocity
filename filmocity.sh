#!/usr/bin/env bash
# Linux: install if needed, then launch and open the browser. Options: --port 8787 --data ~/filmocity_data --no-browser
cd "$(dirname "$0")" && python3 launcher/bootstrap.py "$@"
