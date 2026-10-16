#!/usr/bin/env bash
set -euo pipefail
# GitHub macOS/Linux and Windows runners expose Python under different aliases.
for interpreter in python3 python; do
  if "$interpreter" -c 'import sys; assert sys.version_info >= (3, 8)' >/dev/null 2>&1; then
    exec "$interpreter" scripts/browser_candidate.py unpack browser-candidate dist "$1"
  fi
done
printf '%s\n' 'Python 3.8 or newer is required to verify the staged website.' >&2
exit 1
