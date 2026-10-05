#!/usr/bin/env python3
"""Create a publication copy retaining owner-verified immutable runtime URLs.

Only owner GET requests are made. The stock release stage remains unchanged;
no caller-supplied receipt can authorize extra files or bypass its strict checks.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from urllib import error, parse, request

from demo_media import verify_demo
from msx import verify_msx
from swop import GRAPH_ASSETS, GRAPH_PATH, runtime_graph, verify_swop, verify_swop_runtime

ROOT = Path(__file__).resolve().parents[1]
MAX_FILES = 2000
MAX_GRAPHS = 16
MAX_FILE_BYTES = 2_000_000
MAX_GRAPH_BYTES = 8_000_000
MAX_TOTAL_BYTES = 32_000_000
MAX_INVENTORY_BYTES = 4_000_000
JS_MIME = "text/javascript; charset=utf-8"
TOKEN = re.compile(r"[A-Za-z0-9_-]{1,128}")
SHA256 = re.compile(r"[0-9a-f]{64}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        require(name not in result, "Owner JSON contains duplicate keys")
        result[name] = value
    return result


def decode_json(raw):
    try:
        return json.loads(raw, object_pairs_hook=unique_object)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Owner response is not unique valid JSON") from exc


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OwnerReader:
    """Never forward owner authorization to a redirect or public asset host."""
    def __init__(self, workspace, slug, key):
        require(TOKEN.fullmatch(workspace) and TOKEN.fullmatch(slug), "Invalid owner workspace or slug")
        require(isinstance(key, str) and key and not any(ord(char) < 33 or ord(char) > 126 for char in key),
                "Missing or invalid here.now credentials")
        self.base = "https://here.now/api/v1/publish/" + slug
        self.headers = {"Authorization": "Bearer " + key, "X-HereNow-Account": workspace,
                        "X-HereNow-Client": "ottplay-web-vitrine/retain-runtime"}
        self.opener = request.build_opener(NoRedirect)

    def __call__(self, suffix, limit=MAX_INVENTORY_BYTES):
        require(suffix in ("", "/files") or suffix.startswith("/files/hosted-runtime/"), "Unexpected owner endpoint")
        try:
            req = request.Request(self.base + suffix, headers=self.headers, method="GET")
            with self.opener.open(req, timeout=30) as response:
                require(response.status == 200 and response.geturl() == req.full_url, "Owner request redirected or failed")
                raw = response.read(limit + 1)
        except (error.URLError, OSError, ValueError):
            # Response bodies/headers and request exception details may include
            # credentials. Keep them out of diagnostics and retained receipts.
            raise ValueError("Owner request failed or was redirected") from None
        require(len(raw) <= limit, "Owner response exceeds its size bound")
        return raw


def checked_metadata(reader, expected_version, slug):
    metadata = decode_json(reader("", MAX_INVENTORY_BYTES))
    require(isinstance(metadata, dict) and metadata.get("slug") == slug
            and metadata.get("currentVersionId") == expected_version
            and "pendingVersionId" in metadata and metadata["pendingVersionId"] is None,
            "Owner version changed or has a pending publication")
    return metadata


def owner_inventory(reader, expected_version):
    listing = decode_json(reader("/files", MAX_INVENTORY_BYTES))
    require(isinstance(listing, dict) and listing.get("currentVersionId") == expected_version
            and listing.get("pendingVersionId") is None and isinstance(listing.get("files"), list)
            and 0 < len(listing["files"]) <= MAX_FILES, "Owner inventory version, pending state or size is invalid")
    result = {}
    for item in listing["files"]:
        require(isinstance(item, dict), "Invalid owner file entry")
        name = item.get("path")
        require(isinstance(name, str) and name and not re.search(r"[\\\x00-\x1f\x7f]", name), "Unsafe owner file path")
        path = PurePosixPath(name)
        require(not path.is_absolute() and ".." not in path.parts and path.as_posix() == name and name != "."
                and name not in result, "Unsafe or duplicate owner file path")
        require(type(item.get("size")) is int and 0 <= item["size"] <= 128_000_000
                and isinstance(item.get("hash"), str) and SHA256.fullmatch(item["hash"])
                and isinstance(item.get("contentType"), str) and 0 < len(item["contentType"]) <= 200
                and not re.search(r"[\x00-\x1f\x7f]", item["contentType"]), "Invalid owner file identity")
        result[name] = {key: item[key] for key in ("size", "hash", "contentType")}
    return result


def snapshot(directory):
    require(directory.is_dir() and not directory.is_symlink(), "Stage must be a regular directory")
    files, directories = {}, []
    for path in sorted(directory.rglob("*")):
        name = path.relative_to(directory).as_posix()
        require(not path.is_symlink(), "Stage contains a symlink")
        if path.is_dir():
            directories.append(name)
        else:
            require(path.is_file(), "Stage contains a special file")
            require(path.stat().st_size <= 128_000_000 and len(files) < MAX_FILES, "Stage exceeds its file bounds")
            raw = path.read_bytes()
            files[name] = {"size": len(raw), "hash": sha256(raw)}
    return {"files": files, "directories": directories}


def protected(name):
    return name.startswith((".herenow/", "demo/", "msx/")) or name == "local/swop.json"


def protected_mime(name):
    if name.endswith(".json"):
        return "application/json; charset=utf-8"
    if name.endswith(".m3u8"):
        return "application/vnd.apple.mpegurl"
    if name.endswith(".ts"):
        return "video/mp2t"
    if name.endswith(".mp4"):
        return "video/mp4"
    raise ValueError("Unexpected protected file type")


def check_protected(stock, owner):
    expected = {name: {**item, "contentType": protected_mime(name)}
                for name, item in stock["files"].items() if protected(name)}
    require(expected and expected == {name: item for name, item in owner.items() if protected(name)},
            "Owner demo, MSX or publication controls differ from the reviewed stock stage")
    return expected


def graph_inventory(owner):
    groups = {}
    for name, item in owner.items():
        require(name != "hosted-runtime", "Owner graph root must not be a file")
        if not name.startswith("hosted-runtime/"):
            continue
        parts = name.split("/", 2)
        require(len(parts) == 3 and SHA256.fullmatch(parts[1]), "Invalid retained graph directory")
        groups.setdefault(parts[1], {})[parts[2]] = item
    require(len(groups) <= MAX_GRAPHS, "Retained graph count exceeds its bound")
    expected = {path.as_posix() for path in GRAPH_ASSETS}
    for files in groups.values():
        require(set(files) == expected, "Retained graph must contain exactly seven reviewed paths")
        require(all(0 < item["size"] <= MAX_FILE_BYTES and item["contentType"] == JS_MIME for item in files.values()),
                "Retained graph size or MIME differs from the publisher contract")
        require(sum(item["size"] for item in files.values()) <= MAX_GRAPH_BYTES, "Retained graph exceeds its byte bound")
    require(sum(item["size"] for files in groups.values() for item in files.values()) <= MAX_TOTAL_BYTES,
            "Retained graphs exceed the total byte bound")
    return groups


def sorted_graph_digest(directory):
    digest = hashlib.sha256(b"ottplay-hosted-runtime-v1\0")
    for relative in sorted(GRAPH_ASSETS):
        raw = (directory / relative).read_bytes()
        digest.update(relative.as_posix().encode() + b"\0" + str(len(raw)).encode() + b"\0" + raw)
    return digest.hexdigest()


def create_copy(stage, output, receipt, workspace, slug, expected_version, reader=None):
    """Validate stock and owner bytes before adding anything to a private copy."""
    stage, output, receipt = Path(stage), Path(output), Path(receipt)
    require(TOKEN.fullmatch(workspace) and TOKEN.fullmatch(slug) and TOKEN.fullmatch(expected_version),
            "Invalid reviewed owner identity")
    require(not stage.is_symlink(), "Stage must not be a symlink")
    stage = stage.resolve(strict=True)
    output, receipt = output.absolute(), receipt.absolute()
    require(not output.exists() and not output.is_symlink() and not receipt.exists() and not receipt.is_symlink(),
            "Publication copy and receipt must be new paths")
    resolved_output, resolved_receipt = output.resolve(), receipt.resolve()
    require(not resolved_output.is_relative_to(stage) and not stage.is_relative_to(resolved_output)
            and not resolved_receipt.is_relative_to(stage) and not resolved_receipt.is_relative_to(resolved_output)
            and resolved_receipt != resolved_output, "Keep publication copy and receipt outside the stock stage")
    stock = snapshot(stage)
    verify_demo(stage / "demo", ROOT / "demo-media.json")
    verify_msx(stage)
    verify_swop(stage)
    verify_swop_runtime(stage)
    if reader is None:
        key = os.environ.get("HERENOW_API_KEY")
        if not key:
            try:
                key = (Path.home() / ".herenow/credentials").read_text().strip()
            except OSError:
                raise ValueError("Configure here.now credentials before preparing a publication copy") from None
        reader = OwnerReader(workspace, slug, key)
    checked_metadata(reader, expected_version, slug)
    owner = owner_inventory(reader, expected_version)
    preserved = check_protected(stock, owner)
    groups = graph_inventory(owner)
    output.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with tempfile.TemporaryDirectory(prefix="ottplay-retained-", dir=output.parent) as temporary:
            cache = Path(temporary)
            graph_records = []
            additions = {}
            for graph, files in sorted(groups.items()):
                for relative, identity in sorted(files.items()):
                    name = (GRAPH_PATH / graph / relative).as_posix()
                    raw = reader("/files/" + parse.quote(name, safe="/"), identity["size"])
                    require(len(raw) == identity["size"] and sha256(raw) == identity["hash"], "Owner graph byte digest mismatch")
                    target = cache / graph / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(raw)
                    if name in stock["files"]:
                        require(stock["files"][name] == {key: identity[key] for key in ("size", "hash")},
                                "Owner graph collides with the new release bytes")
                    else:
                        additions[name] = {key: identity[key] for key in ("size", "hash")}
                canonical_graph, _ = runtime_graph(cache / graph)
                algorithm = "canonical" if graph == canonical_graph else "sorted-path"
                require(graph == canonical_graph or graph == sorted_graph_digest(cache / graph), "Retained graph name does not match its bytes")
                graph_records.append({"graph": graph, "algorithm": algorithm, "files": files})
            checked_metadata(reader, expected_version, slug)
            require(owner_inventory(reader, expected_version) == owner, "Owner inventory changed during retention")
            require(snapshot(stage) == stock, "Stock stage changed during retention")
            output.mkdir(mode=0o700)
            created = True
            shutil.copytree(stage, output, dirs_exist_ok=True)
            output.chmod(0o700)
            for name in additions:
                relative = Path(name).relative_to(GRAPH_PATH)
                target = output / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write((cache / relative).read_bytes())
            final = snapshot(output)
            expected_dirs = set(stock["directories"])
            for name in additions:
                expected_dirs.update(parent.as_posix() for parent in PurePosixPath(name).parents if str(parent) != ".")
            require(final["files"] == stock["files"] | additions and set(final["directories"]) == expected_dirs,
                    "Publication copy differs from stock plus attested graph additions")
            require(snapshot(stage) == stock, "Stock stage changed while copying")
            checked_metadata(reader, expected_version, slug)
            evidence = {"schema": 1, "workspace": workspace, "slug": slug, "expectedVersionId": expected_version,
                        "source": str(stage), "publicationCopy": str(output), "stockSha256": sha256(canonical(stock)),
                        "ownerInventorySha256": sha256(canonical(owner)), "publicationSha256": sha256(canonical(final)),
                        "stockUnchanged": True, "exactUnionVerified": True, "protectedFiles": preserved,
                        "graphs": graph_records, "addedFiles": additions, "files": final["files"]}
            receipt.parent.mkdir(parents=True, exist_ok=True)
            with os.fdopen(os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
                json.dump(evidence, stream, indent=2, sort_keys=True)
                stream.write("\n")
            return evidence
    except BaseException:
        if created:
            shutil.rmtree(output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--workspace", default="ottplay")
    parser.add_argument("--slug", default="liminal-sketch-vv8r")
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args()
    try:
        evidence = create_copy(args.stage, args.output, args.receipt, args.workspace, args.slug, args.expected_version)
    except (ValueError, OSError, SystemExit) as exc:
        parser.exit(1, f"Publication copy refused: {exc}\n")
    print(json.dumps({"publicationCopy": evidence["publicationCopy"], "receipt": str(args.receipt.absolute()),
                      "addedFiles": len(evidence["addedFiles"]), "publicationSha256": evidence["publicationSha256"]}))


if __name__ == "__main__":
    main()
