#!/bin/bash
# Refresh the Edmonton Growth Monitor data from data.edmonton.ca.
# First run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pypdf pypdfium2 pillow
set -e
cd "$(dirname "$0")"
rm -f cache/api/*.json            # force fresh pulls (otherwise cached for 20 hours)
.venv/bin/python -W ignore pipeline/build_data.py
echo "Done. Re-publish index.html + data/ to update the shared dashboard."
