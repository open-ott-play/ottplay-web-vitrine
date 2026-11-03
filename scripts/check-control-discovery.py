#!/usr/bin/env python3
"""Check public discovery against the reviewed, fingerprinted stage catalog."""
import argparse
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener

from swop import (CONTROL_API_BASE, CONTROL_DISCOVERY_PATH, CONTROL_DISCOVERY_SHA256,
                  CONTROL_PUBLIC_DISCOVERY_URL, MAX_DISCOVERY_BYTES,
                  validate_control_discovery, verify_control_discovery)


USER_AGENT = "OTT-play-control-discovery/1.0"
HTTP_TIMEOUT_SECONDS = 10


class ProbeError(ValueError):
    """Only closed diagnostic labels and an optional numeric status escape IO."""

    def __init__(self, reason, http_status=None):
        self.diagnostic = {"status": "failed", "reason": reason}
        if type(http_status) is int and 100 <= http_status <= 599:
            self.diagnostic["http_status"] = http_status
        super().__init__(reason)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def https_handler():
    context = ssl.create_default_context()
    context.minimum_version = max(context.minimum_version, ssl.TLSVersion.TLSv1_2)
    level = os.environ.get("OTTPLAY_TLS_SECURITY_LEVEL")
    if level is not None:
        if level not in ("2", "3", "4", "5"):
            raise ProbeError("invalid_tls_configuration")
        if int(level) > context.security_level:
            ciphers = ":".join(cipher["name"] for cipher in context.get_ciphers()
                               if cipher["protocol"] != "TLSv1.3")
            context.set_ciphers(ciphers + ":@SECLEVEL=" + level)
    return HTTPSHandler(context=context)


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ProbeError("invalid_json")
        value[key] = item
    return value


def reject_constant(_value):
    raise ProbeError("invalid_json")


def stage_catalog(stage):
    try:
        verify_control_discovery(stage)
        with (stage / CONTROL_DISCOVERY_PATH).open("rb") as source:
            raw = source.read(MAX_DISCOVERY_BYTES + 1)
        return validate_control_discovery(raw, CONTROL_DISCOVERY_SHA256)
    except (OSError, ValueError, SystemExit, RecursionError):
        raise ProbeError("invalid_stage_catalog") from None


def check(stage, *, upstream=False, open_url=None):
    expected = stage_catalog(Path(stage))
    url = CONTROL_API_BASE + "/api/discovery" if upstream else CONTROL_PUBLIC_DISCOVERY_URL
    request = Request(url, method="GET", headers={
        "Accept": "application/json", "User-Agent": USER_AGENT,
    })
    try:
        if open_url is None:
            open_url = build_opener(NoRedirect(), https_handler()).open
        with open_url(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                reason = "redirect_rejected" if 300 <= response.status < 400 else "http_status"
                raise ProbeError(reason, response.status)
            if response.geturl() != url:
                raise ProbeError("redirect_rejected")
            content_types = response.headers.get_all("Content-Type", [])
            if (len(content_types) != 1 or len(content_types[0]) > 256
                    or content_types[0].partition(";")[0].strip().lower() != "application/json"):
                raise ProbeError("unexpected_content_type")
            raw = response.read(MAX_DISCOVERY_BYTES + 1)
    except HTTPError as error:
        code = error.code
        error.close()
        raise ProbeError("redirect_rejected" if 300 <= code < 400 else "http_status", code) from None
    except (URLError, OSError, HTTPException):
        raise ProbeError("transport_failed") from None
    if not raw or len(raw) > MAX_DISCOVERY_BYTES:
        raise ProbeError("response_size")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                           parse_constant=reject_constant)
        # The existing Controller discovery route always supplies pairing_url.
        if not isinstance(value, dict) or set(value) != {"version", "servers", "pairing_url"}:
            raise ProbeError("invalid_schema")
        catalog = {key: value[key] for key in ("version", "servers")}
        canonical = (json.dumps(catalog, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()
        validate_control_discovery(canonical, hashlib.sha256(canonical).hexdigest())
    except ProbeError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise ProbeError("invalid_schema") from None
    if catalog != expected:
        raise ProbeError("catalog_mismatch")
    if value["pairing_url"] != CONTROL_API_BASE + "/api/pairings":
        raise ProbeError("pairing_url_mismatch")
    return {"status": "ok", "endpoint": "upstream" if upstream else "public",
            "catalog_sha256": CONTROL_DISCOVERY_SHA256, "response_bytes": len(raw),
            "server_count": len(catalog["servers"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("stage", type=Path, help="reviewed stock stage containing its pinned catalog")
    parser.add_argument("--upstream", action="store_true", help="check the fixed canonical controller directly")
    args = parser.parse_args(argv)
    try:
        result = check(args.stage, upstream=args.upstream)
    except ProbeError as error:
        print(json.dumps(error.diagnostic, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
