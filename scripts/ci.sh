#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/validate-source.py
python3 scripts/demo_media.py static
python3 scripts/msx.py static
python3 scripts/swop.py static
node --test deploy/epg-worker-vpc/relay.test.mjs
python3 -m unittest discover -s tests -v
