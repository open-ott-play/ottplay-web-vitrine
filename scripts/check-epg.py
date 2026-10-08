#!/usr/bin/env python3
"""Check the bounded EPG v1 API and current/archived РЕН ТВ HD programmes."""
import json
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


SOURCE_ID = "epg-one"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_ERROR_RESPONSE_BYTES = 4096
EPG_ERRORS = {
    "EPG_REQUEST": 400, "EPG_REQUEST_LIMIT": 413, "EPG_NOT_READY": 503,
    "EPG_CHANNEL": 404, "EPG_GENERATION": 409, "EPG_BUSY": 429,
    "EPG_CHANNEL_LIMIT": 422, "EPG_INTERNAL": 500, "EPG_TIMEOUT": 504,
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


# Keep every probe at the explicitly selected origin, including POST requests.
# Retain a module-level binding so offline tests can replace only the transport.
urlopen = build_opener(NoRedirect()).open


class EpgHttpError(Exception):
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        super().__init__("EPG smoke check HTTP failure: " + json.dumps(
            diagnostic, sort_keys=True, separators=(",", ":")))


def safe_response_headers(headers):
    """Project known, bounded header values; never echo arbitrary header text."""
    result = {}
    if headers is None:
        return result
    for name in ("Content-Type", "Server", "CF-Ray", "CF-Mitigated", "Retry-After"):
        values = headers.get_all(name, []) if hasattr(headers, "get_all") else [headers.get(name)]
        if len(values) != 1:
            continue
        value = values[0]
        if (not isinstance(value, str) or len(value) > 256 or
                any(ord(c) < 32 or ord(c) > 126 for c in value)):
            continue
        value = value.strip()
        if name == "Content-Type":
            value = value.partition(";")[0].lower().strip()
            valid = value in ("application/json", "application/problem+json", "text/html", "text/plain")
        elif name == "Server":
            valid = re.fullmatch(r"(?:cloudflare|nginx|caddy|apache|envoy|awselb)(?:/[0-9][0-9.]{0,19})?",
                                 value, re.IGNORECASE)
        elif name == "CF-Ray":
            valid = re.fullmatch(r"[0-9a-fA-F]{16}-[A-Z]{3}", value)
        elif name == "CF-Mitigated":
            valid = value == "challenge"
        else:
            valid = re.fullmatch(r"[0-9]{1,6}", value) or re.fullmatch(
                r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), [0-9]{2} "
                r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
                r"[0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT", value)
        if valid:
            result[name] = value
    return result


def epg_error_code(error, headers):
    if headers.get("Content-Type") != "application/json":
        return None

    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate error field")
            value[key] = item
        return value

    def reject_constant(_):
        raise ValueError("Invalid error constant")

    try:
        body = error.read(MAX_ERROR_RESPONSE_BYTES + 1)
        if len(body) > MAX_ERROR_RESPONSE_BYTES:
            return None
        value = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object,
                           parse_constant=reject_constant)
        if (not isinstance(value, dict) or type(value.get("version")) is not int or
                value["version"] != 1 or value.get("source") != SOURCE_ID or
                not isinstance(value.get("error"), dict)):
            return None
        code = value["error"].get("code")
        return code if isinstance(code, str) and EPG_ERRORS.get(code) == error.code else None
    except (OSError, ValueError, TypeError, RecursionError):
        return None


def http_diagnostic(request, stage, error):
    # Use our original request, not error.url (which can contain a redirect query).
    url = urlsplit(request.full_url)
    headers = safe_response_headers(error.headers)
    code = epg_error_code(error, headers)
    reason = "access_denied" if error.code == 403 else "http_error"
    if code == "EPG_NOT_READY":
        reason = "epg_not_ready"
    elif code == "EPG_GENERATION":
        reason = "epg_generation_changed"
    diagnostic = {"stage": stage, "method": request.get_method(), "path": url.path,
                  "http_status": error.code, "reason": reason, "headers": headers}
    if url.hostname and re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", url.hostname):
        diagnostic["host"] = url.hostname
    if code:
        diagnostic["epg_code"] = code
    return diagnostic


def read_json(request, stage):
    try:
        with urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise ValueError("EPG endpoint did not return HTTP 200")
            if "application/json" not in response.headers.get("Content-Type", ""):
                raise ValueError("EPG endpoint did not return JSON")
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("EPG response exceeds the smoke-check bound")
    except HTTPError as error:
        try:
            diagnostic = http_diagnostic(request, stage, error)
        finally:
            error.close()
        raise EpgHttpError(diagnostic) from None
    value = json.loads(body)
    if (not isinstance(value, dict) or value.get("version") != 1 or
            value.get("source") != SOURCE_ID or not isinstance(value.get("generation"), str) or
            not value["generation"] or type(value.get("fetchedAt")) is not int or
            value["fetchedAt"] <= 0 or type(value.get("stale")) is not bool):
        raise ValueError("EPG endpoint returned incompatible generation metadata")
    if value["stale"]:
        raise ValueError("EPG endpoint returned a stale generation")
    return value


def check(base):
    base = base.rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or
            parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Expected an HTTPS origin without credentials")
    headers = {"Accept": "application/json", "Origin": base, "User-Agent": "OTT-play-EPG-check/2.0",
               "Cache-Control": "no-cache"}
    body = json.dumps({"version": 1, "source": SOURCE_ID, "channels": [
        {"id": "ren-hd", "tvgId": "hlsproxy-382", "tvgName": "", "name": "РЕН ТВ HD"}
    ]}, ensure_ascii=False).encode()
    matched = read_json(Request(base + "/epg/v1/match", data=body,
                                headers={**headers, "Content-Type": "application/json"}), "match")
    channel = matched.get("mappings", {}).get("ren-hd")
    if (not isinstance(channel, dict) or not isinstance(channel.get("channelId"), str) or
            not channel["channelId"] or type(channel.get("shift")) is not int):
        raise ValueError("РЕН ТВ HD has no channel match; EPG may not be warm yet")
    query = urlencode({"channelId": channel["channelId"], "shift": channel["shift"],
                       "hours": 0, "generation": matched["generation"]})
    guide = read_json(Request(base + "/epg/v1/programmes?" + query, headers=headers), "programmes")
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


def check_current(base, expected_generation=None):
    """Check the upstream-only current route with one public synthetic channel."""
    base = base.rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme != "https" or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or
            parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Expected an HTTPS origin without credentials")
    headers = {"Accept": "application/json", "Content-Type": "application/json", "Origin": base,
               "User-Agent": "OTT-play-EPG-check/2.0", "Cache-Control": "no-cache"}
    body = json.dumps({"version": 1, "source": SOURCE_ID, "search": "", "channels": [
        {"id": "ren-hd", "tvgId": "hlsproxy-382", "tvgName": "", "name": "РЕН ТВ HD", "shift": 0}
    ]}, ensure_ascii=False).encode()
    result = read_json(Request(base + "/epg/v1/current", data=body, headers=headers), "current")
    if (type(result["version"]) is not int or
            type(result.get("asOf")) is not int or abs(result["asOf"] - time.time()) > 60 or
            type(result.get("checked")) is not int or result["checked"] != 1 or
            type(result.get("total")) is not int or result["total"] != 1 or
            not isinstance(result.get("programs"), list) or len(result["programs"]) != 1):
        raise ValueError("EPG current response has invalid time or channel coverage")
    if expected_generation is not None and result["generation"] != expected_generation:
        raise ValueError("EPG generation changed during the current smoke check")
    programme = result["programs"][0]
    if (not isinstance(programme, dict) or programme.get("id") != "ren-hd" or
            not isinstance(programme.get("title"), str) or not programme["title"].strip() or
            len(programme["title"].encode("utf-16-le")) > 32768 or
            any(type(programme.get(key)) is not int or
                not -9007199254740991 <= programme[key] <= 9007199254740991
                for key in ("start", "end")) or
            not programme["start"] <= result["asOf"] < programme["end"]):
        raise ValueError("РЕН ТВ HD current programme is missing or invalid")
    return {"source": SOURCE_ID, "generation": result["generation"], "fetchedAt": result["fetchedAt"],
            "stale": result["stale"], "asOf": result["asOf"], "checked": result["checked"],
            "total": result["total"], "programmes": 1, "current": programme["title"]}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 scripts/check-epg.py https://player.ottplay.here.now")
    try:
        print(json.dumps(check(sys.argv[1]), ensure_ascii=False))
    except EpgHttpError as error:
        raise SystemExit(str(error)) from None
    except (ValueError, KeyError, TypeError) as error:
        raise SystemExit(str(error)) from None
