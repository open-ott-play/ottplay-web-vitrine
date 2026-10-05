"""Offline owner graph fixtures shared with the real-publisher request tests."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import retain_runtime as RETAIN
import swop as SWOP

VERSION = "reviewed-owner-version"
SLUG = "liminal-sketch-vv8r"


def make_stage(target):
    shutil.copytree(ROOT / "static", target)
    for name in ("local", ".herenow"):
        shutil.rmtree(target / name)
    (target / "index.html").write_text('<!doctype html><html><head></head></html>')
    (target / "dist").mkdir()
    (target / "dist/player.js").write_text(
        'window.__OTTPLAY_HOSTED_PROTOCOL__="hosted-profile-v1"; var protocol="ottplay.swop.v2"; '
        'window.__OTT_CONTROL_DISCOVERY_VERSION__=1; window.__OTT_HOSTED_EPG_SERVER_VERSION__=1;')
    for relative in SWOP.REQUIRED_RUNTIME_ASSETS:
        asset = target / relative
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(SWOP.WORKER_HEADER + b"self.onmessage = function () {};\n"
                          if relative == Path("hosted/epg-worker.js") else b"/* fixture */\n")
    SWOP.stage_swop(target, ROOT / "static")


def make_owner(stage, orders=("canonical", "sorted-path"), version=VERSION):
    stock = RETAIN.snapshot(stage)
    files = [{"path": name, **identity, "contentType": RETAIN.protected_mime(name)}
             for name, identity in stock["files"].items() if RETAIN.protected(name)]
    bodies, graphs = {}, []
    for index, order in enumerate(orders):
        contents = {path: (stage / path).read_bytes() for path in SWOP.GRAPH_ASSETS}
        contents[Path("js/ottplay-core.js")] += f"/* older graph {index} */\n".encode()
        digest = hashlib.sha256(b"ottplay-hosted-runtime-v1\0")
        for path in sorted(contents) if order == "sorted-path" else SWOP.GRAPH_ASSETS:
            raw = contents[path]
            digest.update(path.as_posix().encode() + b"\0" + str(len(raw)).encode() + b"\0" + raw)
        graph = digest.hexdigest()
        graphs.append(graph)
        for relative, raw in contents.items():
            name = f"hosted-runtime/{graph}/{relative.as_posix()}"
            files.append({"path": name, "size": len(raw), "hash": RETAIN.sha256(raw), "contentType": RETAIN.JS_MIME})
            bodies["/files/" + name] = raw
    return {"metadata": {"slug": SLUG, "currentVersionId": version, "pendingVersionId": None},
            "listing": {"currentVersionId": version, "files": files}, "bodies": bodies, "graphs": graphs}


class Reader:
    def __init__(self, fixture):
        self.fixture = copy.deepcopy(fixture)
        self.calls = []
        self.metadata_changes = {}

    def __call__(self, suffix, limit):
        self.calls.append((suffix, limit))
        if suffix == "":
            count = sum(name == "" for name, _ in self.calls)
            return json.dumps(self.metadata_changes.get(count, self.fixture["metadata"])).encode()
        if suffix == "/files":
            return json.dumps(self.fixture["listing"]).encode()
        return self.fixture["bodies"][suffix]
