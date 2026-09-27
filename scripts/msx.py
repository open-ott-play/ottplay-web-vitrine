"""Stage and verify the MSX bootstrap shipped with every site publication."""
from pathlib import Path
import shutil
import sys


SOURCE = Path(__file__).resolve().parents[1] / "static/msx"


def verify_msx(directory, source=SOURCE):
    target = Path(directory) / "msx"
    expected = {"start.json", "content.json"}
    if not target.is_dir() or {p.name for p in target.iterdir()} != expected:
        raise SystemExit("MSX bootstrap inventory mismatch; prepare the complete distribution")
    for name in expected:
        file = target / name
        if file.is_symlink() or not file.is_file() or file.read_bytes() != (source / name).read_bytes():
            raise SystemExit(f"MSX bootstrap differs from the site configuration: {name}")


def stage_msx(directory, source):
    target = Path(directory) / "msx"
    if target.exists():
        raise SystemExit("Player archive conflicts with the repository-owned MSX directory")
    shutil.copytree(source, target)
    verify_msx(directory, source)


if __name__ == "__main__":
    verify_msx(Path(sys.argv[1]))
    print("MSX bootstrap verified")
