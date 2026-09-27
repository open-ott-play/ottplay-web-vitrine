"""Stage the same-origin installation relays without publishing credentials."""
import json
from pathlib import Path
import re
import shutil
import sys


CONFIG_PATH = Path("local/swop.json")
PROXY_PATH = Path(".herenow/proxy.json")
EXPECTED_CONFIG = {"swopBaseUrl": "/swop"}
EXPECTED_PROXY = {
    "proxies": {
        "/vportal/api": {
            "upstream": "https://swop.2560801.xyz/vportal/api",
            "method": "POST",
            "headers": {"Authorization": "Bearer ${OTTPLAY_SWOP_INSTALLATION_TOKEN}"},
            "rateLimit": "1200/hour/ip",
        },
        "/swop/session": {
            "upstream": "https://swop.2560801.xyz/session",
            "method": "POST",
            "headers": {"Authorization": "Bearer ${OTTPLAY_SWOP_INSTALLATION_TOKEN}"},
            "rateLimit": "60/hour/ip",
        },
        "/swop/val": {
            "upstream": "https://swop.2560801.xyz/val",
            "method": "POST",
            "headers": {"Authorization": "Bearer ${OTTPLAY_SWOP_INSTALLATION_TOKEN}"},
            "rateLimit": "7200/hour/ip",
        },
    },
}


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate JSON key")
        result[name] = value
    return result


def _read_json(directory, relative, label):
    root = Path(directory)
    path = root / relative
    if root.is_symlink() or any((root / parent).is_symlink() for parent in relative.parents):
        raise SystemExit(f"{label} must not use symlink directories")
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"{label} is missing or is not a regular file")
    if path.stat().st_size > 8192:
        raise SystemExit(f"{label} is too large")
    try:
        return json.loads(path.read_text(), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError):
        raise SystemExit(f"{label} must contain valid JSON without duplicate keys") from None


def verify_swop(directory):
    config = _read_json(directory, CONFIG_PATH, "SWOP public configuration")
    if config != EXPECTED_CONFIG:
        raise SystemExit("SWOP public configuration must contain only the same-origin /swop service URL")
    proxy = _read_json(directory, PROXY_PATH, "SWOP proxy manifest")
    if proxy != EXPECTED_PROXY:
        raise SystemExit("SWOP proxy manifest must contain only the approved routes and server-side secret reference")
    # Files in this namespace are publication controls, not player assets.
    if {path.name for path in (Path(directory) / ".herenow").iterdir()} != {"proxy.json"}:
        raise SystemExit("SWOP proxy manifest directory contains unexpected publication controls")


def verify_swop_runtime(directory):
    """Reject older release bundles before enabling the session-capability relay.

    This is a compatibility tripwire for the reviewed player protocol, not a
    substitute for the release's runtime tests or checksum/provenance checks.
    """
    root = Path(directory)
    runtime = root / "dist/player.js"
    if root.is_symlink() or runtime.parent.is_symlink() or runtime.is_symlink() or not runtime.is_file():
        raise SystemExit("SWOP relay requires a compatible dist/player.js release bundle")
    script = runtime.read_bytes()
    if not re.search(rb"\bsessionToken\b", script) or b"Allowlist this Device ID" in script:
        raise SystemExit("SWOP relay requires a player release with sessionToken support; older Device ID allowlist builds cannot be published")


def stage_swop(directory, source):
    """Copy both repository-owned files from a static/ root into a verified bundle."""
    directory, source = Path(directory), Path(source)
    verify_swop(source)
    for relative in (CONFIG_PATH, Path(".herenow")):
        target = directory / relative
        if target.exists() or target.is_symlink():
            raise SystemExit("Player archive conflicts with the repository-owned SWOP configuration")
    local = directory / "local"
    if directory.is_symlink() or local.is_symlink() or (local.exists() and not local.is_dir()):
        raise SystemExit("Player archive has an unsafe SWOP configuration directory")
    for relative in (CONFIG_PATH, PROXY_PATH):
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
    verify_swop(directory)


if __name__ == "__main__":
    verify_swop(Path(sys.argv[1]))
    if len(sys.argv) == 3 and sys.argv[2] == "--runtime":
        verify_swop_runtime(Path(sys.argv[1]))
    print("SWOP endpoint configuration verified")
