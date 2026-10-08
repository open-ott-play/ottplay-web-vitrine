#!/usr/bin/env python3
"""Check the existing public EPG client route without changing its upstream."""
import json
from pathlib import Path
import runpy
import sys
import time

from swop import verify_swop


EPG = runpy.run_path(str(Path(__file__).with_name("check-epg.py")))
PUBLIC_ORIGIN = "https://player.ottplay.here.now"
# The dedicated service marks generations stale after two hours. Only the
# future timestamp check permits five minutes of clock skew.
MAX_GENERATION_AGE_SECONDS = 2 * 60 * 60
MAX_FUTURE_SKEW_SECONDS = 5 * 60


def check_public(stage):
    # This also verifies the exact proxy methods, upstreams and rate limits.
    # In particular, PUBLIC_ORIGIN must never become its own EPG upstream.
    verify_swop(Path(stage))
    result = EPG["check"](PUBLIC_ORIGIN)
    age = time.time() - result["fetchedAt"] / 1000
    if result["stale"] or not -MAX_FUTURE_SKEW_SECONDS <= age < MAX_GENERATION_AGE_SECONDS:
        raise ValueError("Public EPG generation is stale, expired or future-dated")
    return {**result, "checkedOrigin": PUBLIC_ORIGIN}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 scripts/check-public-epg.py PREPARED_STOCK_DIRECTORY")
    try:
        print(json.dumps(check_public(sys.argv[1]), ensure_ascii=False))
    except (EPG["EpgHttpError"], ValueError, KeyError, TypeError) as error:
        raise SystemExit(str(error)) from None
