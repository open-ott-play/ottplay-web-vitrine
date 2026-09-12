#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/validate-source.py
python3 -m unittest discover -s tests -v
