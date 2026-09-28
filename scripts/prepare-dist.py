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

from demo_media import verify_demo
from msx import stage_msx
from swop import stage_swop, verify_swop_runtime

REPO = "open-ott-play/ottplay-foss"
tag = sys.argv[1] if len(sys.argv) == 3 else ""
manifest_sha256 = sys.argv[2] if len(sys.argv) == 3 else ""
if not re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", tag):
    raise SystemExit("An explicit stable vX.Y.Z tag is required")
if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
    raise SystemExit("An independently verified accepted RC manifest SHA-256 is required")


def api(path):
    return json.loads(subprocess.check_output(["gh", "api", f"repos/{REPO}/{path}"]))


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def verify_source_run(run, run_id, attempt, source_sha):
    require(run.get("id") == run_id and run.get("status") == "completed" and run.get("conclusion") == "success" and
            run.get("head_sha") == source_sha and run.get("run_attempt") == attempt and
            run.get("path") == ".github/workflows/release-pipeline.yml" and
            run.get("repository", {}).get("full_name") == REPO, "RC validation run is not successful/current")


release = api(f"releases/tags/{tag}")
require(release.get("tag_name") == tag and release.get("draft") is False and release.get("prerelease") is False,
        "Only published stable releases can be deployed")
root = Path(__file__).resolve().parents[1]
require(not (root / "dist").exists(), "dist already exists; use a fresh checkout for deployment")
with tempfile.TemporaryDirectory(prefix="vitrine-release-") as temporary:
    download = Path(temporary)
    subprocess.run(["gh", "release", "download", tag, "--repo", REPO, "--pattern", "release-manifest.json",
                    "--dir", str(download)], check=True)
    manifest_path = download / "release-manifest.json"
    require(manifest_path.stat().st_size <= 2_000_000, "Release manifest is too large")
    raw_manifest = manifest_path.read_bytes()
    require(hashlib.sha256(raw_manifest).hexdigest() == manifest_sha256,
            "Stable manifest differs from the independently verified accepted RC manifest")
    manifest = json.loads(raw_manifest)
    require(manifest.get("repository") == REPO and manifest.get("version") == tag[1:] and manifest.get("channel") == "rc",
            "Stable release must contain the unchanged verified RC manifest")
    ref = api(f"git/ref/tags/{tag}")
    require(ref.get("object", {}).get("type") == "commit" and ref["object"].get("sha") == manifest.get("source_sha"),
            "Stable tag and manifest source SHA do not match")
    run_id, attempt = manifest.get("run_id"), manifest.get("run_attempt")
    require(type(run_id) is int and run_id > 0 and type(attempt) is int and attempt > 0, "Invalid source run provenance")
    run = api(f"actions/runs/{run_id}")
    verify_source_run(run, run_id, attempt, manifest["source_sha"])
    pages = json.loads(subprocess.check_output(["gh", "api", "--paginate", "--slurp",
        f"repos/{REPO}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"]))
    gates = [job for page in pages for job in page["jobs"] if job.get("name") == "Release gate"]
    require(len(gates) == 1 and gates[0].get("status") == "completed" and gates[0].get("conclusion") == "success" and
            gates[0].get("head_sha") == manifest["source_sha"], "Release gate did not explicitly pass")
    assets = [item for item in manifest.get("assets", []) if item.get("name") == "ottplay-foss-dist.tar.gz"]
    require(len(assets) == 1, "Manifest does not identify the player distribution")
    subprocess.run(["gh", "release", "download", tag, "--repo", REPO, "--pattern", "ottplay-foss-dist.tar.gz",
                    "--dir", str(download)], check=True)
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
    verify_source_run(api(f"actions/runs/{run_id}"), run_id, attempt, manifest["source_sha"])
    verify_swop_runtime(selected)
    # here.now replaces the whole site; demo media is deliberately absent from
    # upstream application bundles and must be supplied by this repository.
    verify_demo(root / "static/demo", root / "demo-media.json")
    require(not (selected / "demo").exists(), "Player archive conflicts with the repository-owned demo directory")
    import shutil
    shutil.copytree(root / "static/demo", selected / "demo")
    stage_msx(selected, root / "static/msx")
    stage_swop(selected, root / "static")
    shutil.copytree(selected, root / "dist")
print(f"Prepared verified {tag}; publication requires the protected production job.")
