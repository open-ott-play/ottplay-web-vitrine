#!/usr/bin/env python3
"""Verify the complete, repository-owned demo before a full site replacement."""
import hashlib
import json
from pathlib import Path
import re
import sys


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def verify_demo(directory, manifest_path):
    """Check pinned media bytes and every local HLS segment reference."""
    require(directory.is_dir() and not directory.is_symlink(),
            f"Demo directory missing or unsafe: {directory}")
    manifest = json.loads(manifest_path.read_text())
    files = manifest["files"]
    require({"pattern.mp4", "pattern.m3u8"} <= files.keys() and
            all(re.fullmatch(r"pattern(?:\.mp4|\.m3u8|[0-9]+\.ts)", name) for name in files),
            "Demo manifest must contain MP4, HLS and local pattern segments")
    require({item.name for item in directory.iterdir()} == files.keys(),
            "Demo file inventory differs from demo-media.json")
    for name, expected in files.items():
        path = directory / name
        require(path.is_file() and not path.is_symlink(), f"Demo file missing or unsafe: {name}")
        require(path.stat().st_size == expected["size"] and
                hashlib.sha256(path.read_bytes()).hexdigest() == expected["sha256"],
                f"Demo checksum mismatch: {name}")
    playlist = (directory / "pattern.m3u8").read_text().splitlines()
    require(playlist and playlist[0] == "#EXTM3U" and playlist[-1] == "#EXT-X-ENDLIST" and
            "#EXT-X-PLAYLIST-TYPE:VOD" in playlist,
            "Demo HLS playlist must be complete VOD")
    segments = [line for line in playlist if line and not line.startswith("#")]
    require(segments and set(segments) == {name for name in files if name.endswith(".ts")},
            "Demo HLS segment references differ from demo-media.json")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: demo_media.py SITE_ROOT")
    verify_demo(Path(sys.argv[1]) / "demo", Path(__file__).resolve().parents[1] / "demo-media.json")
    print("Verified complete MP4/HLS demo media.")
