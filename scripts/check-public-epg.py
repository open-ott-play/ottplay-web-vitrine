#!/usr/bin/env python3
"""Check the fixed staged EPG origin before cutover and public routes after it."""
import json
from pathlib import Path
import runpy
import sys
import time

from swop import EPG_API_URL, verify_swop


EPG = runpy.run_path(str(Path(__file__).with_name("check-epg.py")))
PUBLIC_ORIGIN = "https://player.ottplay.here.now"
# The dedicated service marks generations stale after two hours. Only the
# future timestamp check permits five minutes of clock skew.
MAX_GENERATION_AGE_SECONDS = 2 * 60 * 60
MAX_FUTURE_SKEW_SECONDS = 5 * 60


def require_fresh(result):
    age = time.time() - result["fetchedAt"] / 1000
    if result["stale"] or not -MAX_FUTURE_SKEW_SECONDS <= age < MAX_GENERATION_AGE_SECONDS:
        raise ValueError("Public EPG generation is stale, expired or future-dated")


def check_public(stage):
    # This also verifies the exact proxy methods, upstreams and rate limits.
    # In particular, PUBLIC_ORIGIN must never become its own EPG upstream.
    verify_swop(Path(stage))
    result = EPG["check"](PUBLIC_ORIGIN)
    require_fresh(result)
    return {**result, "checkedOrigin": PUBLIC_ORIGIN}


def check_upstream(stage):
    # A cutover must qualify its new destination even if the old route is broken.
    # The origin comes from reviewed source, never from a CLI argument or fallback.
    verify_swop(Path(stage))
    origin = EPG_API_URL.removesuffix("/epg/v1")
    result = EPG["check"](origin)
    require_fresh(result)
    current = EPG["check_current"](origin, expected_generation=result["generation"])
    require_fresh(current)
    return {**result, "checkedOrigin": origin, "currentEndpointVerified": True}


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3) or (len(sys.argv) == 3 and sys.argv[2] != "--upstream"):
        raise SystemExit("Usage: python3 scripts/check-public-epg.py PREPARED_STOCK_DIRECTORY [--upstream]")
    try:
        check = check_upstream if len(sys.argv) == 3 else check_public
        print(json.dumps(check(sys.argv[1]), ensure_ascii=False))
    except (EPG["EpgHttpError"], ValueError, KeyError, TypeError) as error:
        raise SystemExit(str(error)) from None
