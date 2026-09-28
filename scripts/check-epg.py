#!/usr/bin/env python3
"""Check the browser's text matching protocol and a current РЕН ТВ HD programme."""
import json
import re
import sys
import time
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


def check(base):
    base = base.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Expected an HTTPS origin")
    body = ("{}\n\t\n\n\t\n1-0-0-0~" + quote("РЕН ТВ HD") + "\n").encode()
    request = Request(base + "/m3u/match-channels", data=body,
                      headers={"Content-Type": "text/plain", "Origin": base,
                               "User-Agent": "OTT-play-EPG-check/1.0"})
    with urlopen(request, timeout=30) as response:
        text = response.read().decode()
        if "text/html" in response.headers.get("Content-Type", ""):
            raise ValueError("Matching returned SPA HTML instead of EPG data")
    parts = text.split("\n\t\n")
    if len(parts) != 3 or parts[2].strip() != "local~/":
        raise ValueError("Unexpected matching protocol or EPG base URL")
    match = re.fullmatch(r"1~local~([a-f0-9]{16})", parts[1].strip())
    if not match:
        raise ValueError("РЕН ТВ HD has no channel match; EPG may not be warm yet")
    request = Request(base + "/epg/" + match[1] + ".json",
                      headers={"Origin": base, "User-Agent": "OTT-play-EPG-check/1.0"})
    with urlopen(request, timeout=30) as response:
        if "application/json" not in response.headers.get("Content-Type", ""):
            raise ValueError("Programme endpoint did not return JSON")
        programmes = json.load(response)["epg_data"]
    now = time.time()
    current = [p for p in programmes if p["time"] <= now < p["time_to"]]
    if not current:
        raise ValueError("РЕН ТВ HD has no programme covering the current time")
    print(f"{base}: РЕН ТВ HD — {len(programmes)} programmes; now: {current[0]['name']}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 scripts/check-epg.py https://player.ottplay.here.now")
    check(sys.argv[1])
