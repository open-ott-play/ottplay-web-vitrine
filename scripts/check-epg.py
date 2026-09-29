#!/usr/bin/env python3
"""Check the bounded EPG v1 API and current/archived РЕН ТВ HD programmes."""
import json
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


SOURCE_ID = "epg-one"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def read_json(request):
    with urlopen(request, timeout=30) as response:
        if "application/json" not in response.headers.get("Content-Type", ""):
            raise ValueError("EPG endpoint did not return JSON")
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("EPG response exceeds the smoke-check bound")
    value = json.loads(body)
    if (not isinstance(value, dict) or value.get("version") != 1 or
            value.get("source") != SOURCE_ID or not isinstance(value.get("generation"), str) or
            not value["generation"] or type(value.get("fetchedAt")) is not int or
            value["fetchedAt"] <= 0 or type(value.get("stale")) is not bool):
        raise ValueError("EPG endpoint returned incompatible generation metadata")
    return value


def check(base):
    base = base.rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or
            parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Expected an HTTPS origin without credentials")
    headers = {"Accept": "application/json", "Origin": base, "User-Agent": "OTT-play-EPG-check/2.0"}
    body = json.dumps({"version": 1, "source": SOURCE_ID, "channels": [
        {"id": "ren-hd", "tvgId": "hlsproxy-382", "tvgName": "", "name": "РЕН ТВ HD"}
    ]}, ensure_ascii=False).encode()
    matched = read_json(Request(base + "/epg/v1/match", data=body,
                                headers={**headers, "Content-Type": "application/json"}))
    channel = matched.get("mappings", {}).get("ren-hd")
    if (not isinstance(channel, dict) or not isinstance(channel.get("channelId"), str) or
            not channel["channelId"] or type(channel.get("shift")) is not int):
        raise ValueError("РЕН ТВ HD has no channel match; EPG may not be warm yet")
    query = urlencode({"channelId": channel["channelId"], "shift": channel["shift"],
                       "hours": 0, "generation": matched["generation"]})
    guide = read_json(Request(base + "/epg/v1/programmes?" + query, headers=headers))
    if guide["generation"] != matched["generation"]:
        raise ValueError("EPG generation changed during the smoke check; rematch before retrying")
    programmes = guide.get("rows")
    if not isinstance(programmes, list) or not programmes:
        raise ValueError("РЕН ТВ HD programme response is empty")
    for row in programmes:
        if (not isinstance(row, dict) or type(row.get("time")) is not int or
                type(row.get("time_to")) is not int or row["time"] >= row["time_to"] or
                not isinstance(row.get("name"), str) or not isinstance(row.get("descr"), str)):
            raise ValueError("EPG programme row is invalid")
    now = time.time()
    current = [row for row in programmes if row["time"] <= now < row["time_to"]]
    archived = [row for row in programmes if row["time_to"] <= now]
    if not current or not current[0]["name"] or not current[0]["descr"]:
        raise ValueError("РЕН ТВ HD has no current programme with a title and description")
    if not archived:
        raise ValueError("РЕН ТВ HD has no archived programmes in the requested window")
    return {"source": SOURCE_ID, "generation": guide["generation"], "fetchedAt": guide["fetchedAt"],
            "stale": guide["stale"], "programmes": len(programmes), "archived": len(archived),
            "current": current[0]["name"]}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 scripts/check-epg.py https://player.ottplay.here.now")
    try:
        print(json.dumps(check(sys.argv[1]), ensure_ascii=False))
    except HTTPError as error:
        raise SystemExit(f"EPG smoke check failed with HTTP {error.code}; check server readiness and generation") from None
    except (ValueError, KeyError, TypeError) as error:
        raise SystemExit(str(error)) from None
