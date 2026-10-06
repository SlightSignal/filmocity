#!/bin/bash
cd "$(dirname "$0")"
python3 launcher/bootstrap.py "$@" || read -p "Filmocity could not start (run Install Filmocity.command first). Press Enter to close."
