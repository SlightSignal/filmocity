#!/bin/bash
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Python 3 is required: https://www.python.org/downloads/macos/"; read -p "Press Enter to close"; exit 1; }
python3 launcher/bootstrap.py install "$@" || { read -p "Install failed — press Enter to close"; exit 1; }
read -p "Done. Double-click Filmocity.command to launch. Press Enter to close."
