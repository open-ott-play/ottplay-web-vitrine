"""Stage and verify the reviewed here.now deployment profile."""
import hashlib
from html.parser import HTMLParser
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
CONTROL_DISCOVERY_URL = "https://www.2560801.xyz/ott-control/api/discovery"
CONTROL_DISCOVERY_SCRIPT = "window.__OTT_CONTROL_DISCOVERY_URL__ = " + json.dumps(CONTROL_DISCOVERY_URL) + ";\n"
CONTROL_DISCOVERY_CAPABILITY = re.compile(
    rb"(?<![A-Za-z0-9_$.])window\.__OTT_CONTROL_DISCOVERY_VERSION__\s*=\s*1(?=\s*(?:[;,}]|$))"
)
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
HOSTED_SCRIPT = "window.__OTTPLAY_HOSTED__ = " + json.dumps(HOSTED_CONFIG, indent=2) + ";\n" + CONTROL_DISCOVERY_SCRIPT
BOOTSTRAP_TAG = '<script src="/local/hosted.js"></script>'
GRAPH_PATH = Path("hosted-runtime")
WORKER_IMPORTS = (
    "../js/runtime-polyfills.js", "../js/ottplay-core.js", "pako-inflate.js", "sax.js",
)
WORKER_HEADER = ("importScripts(" + ", ".join(json.dumps(path) for path in WORKER_IMPORTS) + ");\n").encode()
GRAPH_ASSETS = (
    Path("hosted/epg-worker.js"),
    Path("hosted/pako-inflate.js"),
    Path("hosted/sax.js"),
    Path("js/runtime-polyfills.js"),
    Path("js/ottplay-core.js"),
)
REQUIRED_RUNTIME_ASSETS = (
    *GRAPH_ASSETS,
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


def runtime_graph(directory):
    """Hash the exact release bytes plus their fixed paths, without rewriting JS."""
    contents = {}
    digest = hashlib.sha256(b"ottplay-hosted-runtime-v1\0")
    for relative in GRAPH_ASSETS:
        data = _regular_file(directory, relative, "Hosted runtime asset " + relative.as_posix()).read_bytes()
        if not data:
            raise SystemExit("Hosted runtime asset must not be empty: " + relative.as_posix())
        contents[relative] = data
        digest.update(relative.as_posix().encode() + b"\0" + str(len(data)).encode() + b"\0" + data)
    worker = contents[GRAPH_ASSETS[0]]
    # The release builder emits this literal first statement. A changed loader
    # needs review, not a best-effort guess about its relative dependencies.
    if not worker.startswith(WORKER_HEADER) or b"importScripts" in worker[len(WORKER_HEADER):]:
        raise SystemExit("Hosted worker import graph differs from the reviewed literal imports")
    return digest.hexdigest(), contents


def staged_hosted_script(graph):
    config = json.loads(json.dumps(HOSTED_CONFIG))
    config["epg"]["workerUrl"] = "/" + (GRAPH_PATH / graph / GRAPH_ASSETS[0]).as_posix()
    return "window.__OTTPLAY_HOSTED__ = " + json.dumps(config, indent=2) + ";\n" + CONTROL_DISCOVERY_SCRIPT


def bootstrap_tag(script):
    digest = hashlib.sha256(script.encode()).hexdigest()
    return '<script src="/local/hosted.js?v=' + digest + '"></script>'


class _BootstrapParser(HTMLParser):
    # Bootstrap validation assumes scripting is enabled. Tags inside HTML
    # raw-text/RCDATA containers (including noscript) cannot execute scripts.
    CDATA_CONTENT_ELEMENTS = HTMLParser.CDATA_CONTENT_ELEMENTS + (
        "title", "textarea", "xmp", "iframe", "noembed", "noframes", "noscript",
    )

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts = []
        self.parents = []

    def handle_starttag(self, tag, attrs):
        # HTML plaintext consumes the rest of the document, even an apparent
        # </plaintext>. Reject it instead of treating it as closable raw text.
        if tag == "plaintext":
            raise SystemExit("Hosted bootstrap document must not contain plaintext")
        if tag == "script":
            self.scripts.append((self.get_starttag_text(), tuple(self.parents)))
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.parents.append(tag)

    def handle_endtag(self, tag):
        if tag in self.parents:
            position = len(self.parents) - 1 - self.parents[::-1].index(tag)
            # A template is an inert scope; </head> inside it cannot close the
            # active document's head and make a subsequent inert script active.
            if "template" not in self.parents[position + 1:]:
                self.parents = self.parents[:position]

    def handle_startendtag(self, tag, attrs):
        # HTML ignores the slash on non-void elements: <template/> stays open.
        # HTMLParser's default instead synthesizes an end tag, hiding inert
        # containers from the ancestry check. Restore normal raw-text handling
        # too, since HTMLParser skips that setup for its start-end callback.
        self.handle_starttag(tag, attrs)
        if tag in self.CDATA_CONTENT_ELEMENTS:
            self.set_cdata_mode(tag)


def _verify_bootstrap_document(document, tag):
    parser = _BootstrapParser()
    parser.feed(document)
    if (document.count("local/hosted.js") != 1 or not parser.scripts or
            parser.scripts[0][0] != tag[:-9] or document.count(tag) != 1 or
            parser.scripts[0][1] not in {("html", "head"), ("head",)}):
        raise SystemExit("Hosted bootstrap must load the hashed local/hosted.js synchronously before all player scripts")


def verify_hosted_bootstrap(directory, script):
    path = _regular_file(directory, Path("index.html"), "Hosted bootstrap")
    _verify_bootstrap_document(path.read_text(), bootstrap_tag(script))


def verify_runtime_graph(directory, graph, contents):
    root = Path(directory) / GRAPH_PATH
    if root.is_symlink() or not root.is_dir():
        raise SystemExit("Hosted runtime graph is missing or unsafe")
    expected = set()
    for relative, data in contents.items():
        target = Path(graph) / relative
        expected.add(target)
        expected.update(parent for parent in target.parents if parent != Path("."))
        path = _regular_file(directory, GRAPH_PATH / target, "Hosted runtime graph asset")
        if path.read_bytes() != data:
            raise SystemExit("Hosted runtime graph asset differs from the release bytes")
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise SystemExit("Hosted runtime graph must not contain symlinks")
        actual.add(path.relative_to(root))
    if actual != expected:
        raise SystemExit("Hosted runtime graph contains unexpected files or directories")


def verify_swop(directory, *, template=False):
    if _read_json(directory, CONFIG_PATH, "SWOP public configuration") != EXPECTED_CONFIG:
        raise SystemExit("SWOP public configuration must be empty; the hosted profile selects Site Data")
    if _read_json(directory, PROXY_PATH, "SWOP proxy manifest") != EXPECTED_PROXY:
        raise SystemExit("SWOP proxy manifest must contain only the approved fixed VPortal route without credentials")
    if _read_json(directory, DATA_PATH, "SWOP Site Data manifest") != EXPECTED_DATA:
        raise SystemExit("SWOP Site Data manifest must match the reviewed encrypted pairing schema")
    hosted = _regular_file(directory, HOSTED_PATH, "Hosted public configuration")
    if template:
        script = HOSTED_SCRIPT
        graph_path = Path(directory) / GRAPH_PATH
        index = Path(directory) / "index.html"
        if graph_path.exists() or graph_path.is_symlink() or index.exists() or index.is_symlink():
            raise SystemExit("Hosted template must not contain a staged runtime graph or bootstrap")
    else:
        graph, contents = runtime_graph(directory)
        verify_runtime_graph(directory, graph, contents)
        script = staged_hosted_script(graph)
    if hosted.read_bytes() != script.encode():
        raise SystemExit("Hosted public configuration must match the reviewed client EPG, SWOP, VPortal and control discovery profile")
    if {path.name for path in (Path(directory) / ".herenow").iterdir()} != {"proxy.json", "data.json"}:
        raise SystemExit("SWOP proxy manifest directory contains unexpected publication controls")
    if not template:
        verify_hosted_bootstrap(directory, script)


def verify_swop_runtime(directory):
    """Reject older bundles before enabling this installation's hosted protocol."""
    runtime = _regular_file(directory, Path("dist/player.js"), "SWOP relay requires a compatible dist/player.js release bundle")
    script = runtime.read_bytes()
    if b"hosted-profile-v1" not in script or b"ottplay.swop.v2" not in script:
        raise SystemExit("SWOP relay requires a player release with hosted-profile-v1 and encrypted Site Data pairing support")
    # Compatibility tripwire for a trusted, immutable release artifact. Actual
    # execution and discovery behavior are checked by browser acceptance.
    if not CONTROL_DISCOVERY_CAPABILITY.search(script):
        raise SystemExit("Hosted control discovery requires a version 1 release capability marker")
    for relative in REQUIRED_RUNTIME_ASSETS:
        path = _regular_file(directory, relative, "Hosted runtime asset " + relative.as_posix())
        if not path.stat().st_size:
            raise SystemExit("Hosted runtime asset must not be empty: " + relative.as_posix())
    runtime_graph(directory)


def stage_swop(directory, source):
    """Stage repository-owned config and load it before the upstream bootstrap."""
    directory, source = Path(directory), Path(source)
    verify_swop(source, template=True)
    for relative in (CONFIG_PATH, HOSTED_PATH, Path(".herenow"), GRAPH_PATH):
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
    graph, contents = runtime_graph(directory)
    script = staged_hosted_script(graph)
    document = document[:head.end()] + "\n        " + bootstrap_tag(script) + document[head.end():]
    _verify_bootstrap_document(document, bootstrap_tag(script))
    for relative, data in contents.items():
        target = directory / GRAPH_PATH / graph / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for relative in (CONFIG_PATH, PROXY_PATH, DATA_PATH):
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
    (directory / HOSTED_PATH).write_bytes(script.encode())
    index.write_text(document)
    verify_swop(directory)


if __name__ == "__main__":
    runtime = len(sys.argv) == 3 and sys.argv[2] == "--runtime"
    verify_swop(Path(sys.argv[1]), template=not runtime)
    if runtime:
        verify_swop_runtime(Path(sys.argv[1]))
    print("SWOP endpoint configuration verified")
