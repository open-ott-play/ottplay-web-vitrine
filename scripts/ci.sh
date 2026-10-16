#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/validate-source.py
python3 -m ruff check --select E9,F63,F7,F82 scripts tests
python3 scripts/demo_media.py static
python3 scripts/msx.py static
python3 scripts/swop.py static
node --test deploy/epg-worker-vpc/relay.test.mjs
npm test --prefix deploy/epg-worker-vpc/runtime-tests
python3 -m unittest discover -s tests -v
