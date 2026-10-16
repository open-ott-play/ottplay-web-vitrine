#!/usr/bin/env python3
"""Carry the exact staged site through browser qualification and publication."""

import argparse
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

MAX_BYTES = 256 * 1024 * 1024
MAX_FILES = 8192


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity():
    return {key: os.environ[key] for key in
            ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")}


def safe_name(value):
    """Keep archive names portable across POSIX and Windows runners."""
    name = PurePosixPath(value)
    return (bool(value) and not name.is_absolute() and name.as_posix() == value
            and all(part not in (".", "..") and not part.endswith((".", " "))
                    and not re.search(r'[<>:"\\|?*\x00-\x1f]', part)
                    and not re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
                    for part in name.parts))


def inventory(root):
    require(root.is_dir() and not root.is_symlink(), "Site must be a real directory")
    files = []
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "Site must not contain symbolic links")
        if path.is_dir():
            continue
        require(path.is_file(), "Site contains a non-regular file")
        relative = path.relative_to(root).as_posix()
        require(safe_name(relative), "Site contains a non-portable path")
        files.append({"path": relative,
                      "size": path.stat().st_size, "sha256": digest(path)})
    require(0 < len(files) <= MAX_FILES, "Invalid site file count")
    require(sum(item["size"] for item in files) <= MAX_BYTES, "Site exceeds size limit")
    require(len({item["path"].casefold() for item in files}) == len(files),
            "Site contains case-colliding paths")
    require(any(item["path"] == "index.html" for item in files), "Site lacks index.html")
    return files


def pack(source, output, tag, manifest_digest, channel):
    require(channel in ("stable", "beta"), "Unsupported deployment channel")
    require(re.fullmatch(r"[0-9a-f]{64}", manifest_digest), "Invalid accepted manifest digest")
    require(not output.exists(), "Candidate output already exists")
    files = inventory(source)
    output.mkdir(parents=True)
    archive = output / "site.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for item in files:
            tar.add(source / item["path"], arcname=item["path"], recursive=False)
    manifest = {"schema": 1, **identity(), "tag": tag, "releaseChannel": channel,
                "acceptedManifestSha256": manifest_digest,
                "archiveSha256": digest(archive), "files": files}
    path = output / "site-manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return digest(path)


def unpack(candidate, destination, expected_digest):
    require(re.fullmatch(r"[0-9a-f]{64}", expected_digest), "Invalid candidate manifest digest")
    require(not destination.exists(), "Destination already exists")
    require(candidate.is_dir() and not candidate.is_symlink(), "Invalid candidate directory")
    require({p.name for p in candidate.iterdir()} == {"site.tar.gz", "site-manifest.json"},
            "Unexpected candidate files")
    archive, path = candidate / "site.tar.gz", candidate / "site-manifest.json"
    require(all(p.is_file() and not p.is_symlink() for p in (archive, path)), "Invalid candidate files")
    require(path.stat().st_size <= 2_000_000 and digest(path) == expected_digest,
            "Candidate manifest differs from the prepared site")
    manifest = json.loads(path.read_text())
    require(manifest.get("schema") == 1 and all(manifest.get(k) == v for k, v in identity().items()),
            "Candidate belongs to another source, run or attempt")
    require(archive.stat().st_size <= MAX_BYTES and digest(archive) == manifest.get("archiveSha256"),
            "Candidate archive differs from the prepared site")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".browser-site-", dir=destination.parent))
    try:
        with tarfile.open(archive, "r:gz") as tar:
            names, total = set(), 0
            for member in tar:
                name = PurePosixPath(member.name)
                require(member.isfile() and safe_name(member.name)
                        and member.name.casefold() not in names, "Unsafe or duplicate archive member")
                names.add(member.name.casefold())
                total += member.size
                require(len(names) <= MAX_FILES and total <= MAX_BYTES, "Candidate exceeds size limit")
                target = temporary.joinpath(*name.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src, target.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
        require(inventory(temporary) == manifest.get("files"), "Staged site inventory differs")
        temporary.rename(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("pack")
    create.add_argument("source", type=Path)
    create.add_argument("output", type=Path)
    create.add_argument("tag")
    create.add_argument("manifest_digest")
    create.add_argument("channel")
    extract = sub.add_parser("unpack")
    extract.add_argument("candidate", type=Path)
    extract.add_argument("destination", type=Path)
    extract.add_argument("expected_digest")
    args = parser.parse_args()
    if args.command == "pack":
        value = pack(args.source, args.output, args.tag, args.manifest_digest, args.channel)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
                out.write(f"manifest-sha256={value}\n")
        print(value)
    else:
        unpack(args.candidate, args.destination, args.expected_digest)
        print("Prepared site identity and every file verified")


if __name__ == "__main__":
    main()
