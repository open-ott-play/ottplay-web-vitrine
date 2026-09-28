"""Stage and verify the here.now profile without a separately operated backend."""
import json
from pathlib import Path
import re
import shutil
import sys


CONFIG_PATH = Path("local/swop.json")
HOSTED_PATH = Path("local/hosted.js")
PROXY_PATH = Path(".herenow/proxy.json")
DATA_PATH = Path(".herenow/data.json")
EXPECTED_CONFIG = {}
PROVIDER_URL = "http://cd3c21307c36.vportalu.net/api/v1/"
HOSTED_CONFIG = {
    "version": 1,
    "epg": {
        "source": "https://cdn.epg.one/epg2.xml.gz",
        "workerUrl": "/hosted/epg-worker.js",
        "refreshMs": 7200000,
    },
    "swop": {
        "transport": "herenow",
        "collection": "swop_pairs",
        "entryUrl": "/swop-input/",
    },
    "vportal": {"routes": [{"upstream": PROVIDER_URL, "path": "/vportal/provider-1"}]},
}
EXPECTED_PROXY = {
    "proxies": {
        "/vportal/provider-1": {
            "upstream": PROVIDER_URL,
            "method": "POST",
            "headers": {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "OTT-play-FOSS/1.0",
            },
            "rateLimit": "1200/hour/ip",
        },
    },
}
EXPECTED_DATA = {
    "collections": {
        "swop_pairs": {
            "fields": {
                "v": {"type": "integer", "required": True, "minimum": 1, "maximum": 1},
                "offer": {"type": "string", "maxLength": 6500, "default": ""},
                "reply": {"type": "string", "maxLength": 6500, "default": ""},
                "ack": {"type": "string", "maxLength": 1000, "default": ""},
            },
            "access": {"read": "public", "insert": "public", "update": "public", "delete": "public"},
            "publicMutation": "open",
            "rateLimit": "1800/hour/ip",
        },
    },
}
HOSTED_SCRIPT = "window.__OTTPLAY_HOSTED__ = " + json.dumps(HOSTED_CONFIG, indent=2) + ";\n"
BOOTSTRAP_TAG = '<script src="/local/hosted.js"></script>'
REQUIRED_RUNTIME_ASSETS = (
    Path("hosted/epg-worker.js"),
    Path("swop-input/index.html"),
    Path("swop-input/app.js"),
)


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate JSON key")
        result[name] = value
    return result


def _regular_file(directory, relative, label):
    root = Path(directory)
    path = root / relative
    if root.is_symlink() or any((root / parent).is_symlink() for parent in relative.parents):
        raise SystemExit(f"{label} must not use symlink directories")
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"{label} is missing or is not a regular file")
    return path


def _read_json(directory, relative, label):
    path = _regular_file(directory, relative, label)
    if path.stat().st_size > 8192:
        raise SystemExit(f"{label} is too large")
    try:
        return json.loads(path.read_text(), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError):
        raise SystemExit(f"{label} must contain valid JSON without duplicate keys") from None


def verify_hosted_bootstrap(directory):
    path = _regular_file(directory, Path("index.html"), "Hosted bootstrap")
    document = path.read_text()
    first_script = re.search(r"<script\b[^>]*>", document, re.IGNORECASE)
    if document.count(BOOTSTRAP_TAG) != 1 or not first_script or first_script.group() != BOOTSTRAP_TAG[:-9]:
        raise SystemExit("Hosted bootstrap must load local/hosted.js synchronously before all player scripts")


def verify_swop(directory):
    if _read_json(directory, CONFIG_PATH, "SWOP public configuration") != EXPECTED_CONFIG:
        raise SystemExit("SWOP public configuration must be empty; the hosted profile selects Site Data")
    if _read_json(directory, PROXY_PATH, "SWOP proxy manifest") != EXPECTED_PROXY:
        raise SystemExit("SWOP proxy manifest must contain only the approved fixed VPortal route without credentials")
    if _read_json(directory, DATA_PATH, "SWOP Site Data manifest") != EXPECTED_DATA:
        raise SystemExit("SWOP Site Data manifest must match the reviewed encrypted pairing schema")
    hosted = _regular_file(directory, HOSTED_PATH, "Hosted public configuration")
    if hosted.read_text() != HOSTED_SCRIPT:
        raise SystemExit("Hosted public configuration must match the reviewed client EPG, SWOP and VPortal profile")
    if {path.name for path in (Path(directory) / ".herenow").iterdir()} != {"proxy.json", "data.json"}:
        raise SystemExit("SWOP proxy manifest directory contains unexpected publication controls")
    if (Path(directory) / "index.html").exists():
        verify_hosted_bootstrap(directory)


def verify_swop_runtime(directory):
    """Reject older bundles before enabling this installation's hosted protocol."""
    runtime = _regular_file(directory, Path("dist/player.js"), "SWOP relay requires a compatible dist/player.js release bundle")
    script = runtime.read_bytes()
    if b"hosted-profile-v1" not in script or b"ottplay.swop.v2" not in script:
        raise SystemExit("SWOP relay requires a player release with hosted-profile-v1 and encrypted Site Data pairing support")
    for relative in REQUIRED_RUNTIME_ASSETS:
        path = _regular_file(directory, relative, "Hosted runtime asset " + relative.as_posix())
        if not path.stat().st_size:
            raise SystemExit("Hosted runtime asset must not be empty: " + relative.as_posix())


def stage_swop(directory, source):
    """Stage repository-owned config and load it before the upstream bootstrap."""
    directory, source = Path(directory), Path(source)
    verify_swop(source)
    for relative in (CONFIG_PATH, HOSTED_PATH, Path(".herenow")):
        target = directory / relative
        if target.exists() or target.is_symlink():
            raise SystemExit("Player archive conflicts with the repository-owned SWOP configuration")
    local = directory / "local"
    if directory.is_symlink() or local.is_symlink() or (local.exists() and not local.is_dir()):
        raise SystemExit("Player archive has an unsafe SWOP configuration directory")
    index = _regular_file(directory, Path("index.html"), "Hosted bootstrap")
    document = index.read_text()
    head = re.search(r"<head(?:\s[^>]*)?>", document, re.IGNORECASE)
    if not head or "local/hosted.js" in document or "__OTTPLAY_HOSTED__" in document:
        raise SystemExit("Hosted bootstrap requires an unmodified HTML head")
    document = document[:head.end()] + "\n        " + BOOTSTRAP_TAG + document[head.end():]
    for relative in (CONFIG_PATH, HOSTED_PATH, PROXY_PATH, DATA_PATH):
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
    index.write_text(document)
    verify_swop(directory)


if __name__ == "__main__":
    verify_swop(Path(sys.argv[1]))
    if len(sys.argv) == 3 and sys.argv[2] == "--runtime":
        verify_swop_runtime(Path(sys.argv[1]))
    print("SWOP endpoint configuration verified")
