#!/usr/bin/env python3
"""Prepare a checksum-verified stable player distribution; never publish it."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

REPO = "open-ott-play/ottplay-foss"
tag = sys.argv[1] if len(sys.argv) == 2 else ""
if not re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", tag):
    raise SystemExit("An explicit stable vX.Y.Z tag is required")


def api(path):
    return json.loads(subprocess.check_output(["gh", "api", f"repos/{REPO}/{path}"]))


def require(condition, message):
    if not condition:
        raise SystemExit(message)


release = api(f"releases/tags/{tag}")
require(release.get("tag_name") == tag and release.get("draft") is False and release.get("prerelease") is False,
        "Only published stable releases can be deployed")
root = Path(__file__).resolve().parents[1]
require(not (root / "dist").exists(), "dist already exists; use a fresh checkout for deployment")
with tempfile.TemporaryDirectory(prefix="vitrine-release-") as temporary:
    download = Path(temporary)
    for name in ("release-manifest.json", "ottplay-foss-dist.tar.gz"):
        subprocess.run(["gh", "release", "download", tag, "--repo", REPO, "--pattern", name,
                        "--dir", str(download)], check=True)
    manifest = json.loads((download / "release-manifest.json").read_text())
    require(manifest.get("repository") == REPO and manifest.get("version") == tag[1:] and manifest.get("channel") == "rc",
            "Stable release must contain the unchanged verified RC manifest")
    ref = api(f"git/ref/tags/{tag}")
    require(ref.get("object", {}).get("type") == "commit" and ref["object"].get("sha") == manifest.get("source_sha"),
            "Stable tag and manifest source SHA do not match")
    run_id, attempt = manifest.get("run_id"), manifest.get("run_attempt")
    require(type(run_id) is int and run_id > 0 and type(attempt) is int and attempt > 0, "Invalid source run provenance")
    run = api(f"actions/runs/{run_id}")
    require(run.get("status") == "completed" and run.get("conclusion") == "success" and
            run.get("head_sha") == manifest["source_sha"] and run.get("run_attempt") == attempt and
            run.get("path") == ".github/workflows/release-pipeline.yml" and
            run.get("repository", {}).get("full_name") == REPO, "RC validation run is not successful/current")
    pages = json.loads(subprocess.check_output(["gh", "api", "--paginate", "--slurp",
        f"repos/{REPO}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"]))
    gates = [job for page in pages for job in page["jobs"] if job.get("name") == "Release gate"]
    require(len(gates) == 1 and gates[0].get("status") == "completed" and gates[0].get("conclusion") == "success" and
            gates[0].get("head_sha") == manifest["source_sha"], "Release gate did not explicitly pass")
    assets = [item for item in manifest.get("assets", []) if item.get("name") == "ottplay-foss-dist.tar.gz"]
    require(len(assets) == 1, "Manifest does not identify the player distribution")
    package = download / "ottplay-foss-dist.tar.gz"
    require(package.stat().st_size == assets[0].get("size") and hashlib.sha256(package.read_bytes()).hexdigest() == assets[0].get("sha256"),
            "Player archive checksum mismatch")
    stage = download / "unpacked"
    stage.mkdir()
    with tarfile.open(package, "r:gz") as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            require(not path.is_absolute() and ".." not in path.parts and not member.issym() and not member.islnk() and
                    (member.isfile() or member.isdir()), "Unsafe archive member")
        archive.extractall(stage, filter="data")
    candidates = [stage, stage / "dist", *[item for item in stage.iterdir() if item.is_dir()]]
    selected = next((item for item in candidates if (item / "index.html").is_file()), None)
    require(selected is not None, "Verified archive does not contain index.html")
    require(api(f"git/ref/tags/{tag}") == ref, "Stable source tag changed during verification")
    import shutil
    shutil.copytree(selected, root / "dist")
print(f"Prepared verified {tag}; publication requires the protected production job.")
