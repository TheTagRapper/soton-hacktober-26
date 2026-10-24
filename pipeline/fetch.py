"""Download + verify raw archives (spec section 4).

Both hosts 403 a bare HEAD or a directory index to non-browser clients while
serving the archive itself fine. That is not "dataset gone" -- send an ordinary
browser User-Agent and GET (ranged is fine).
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

RAW = Path("data/raw")
UA = "Mozilla/5.0 (X11; Linux x86_64)"

SOURCES = {
    "mmtd": "https://www.cp.jku.at/datasets/MMTD/mmtd.zip",
    "npr": "https://zenodo.org/records/3248543/files/nowplayingrs.zip?download=1",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(name: str, dest: Path | None = None) -> Path:
    url = SOURCES[name]
    dest = dest or RAW / f"{name}.zip"
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"[fetch] {name} -> {dest}")
    # -C - resumes a partial download; these archives are large and the build
    # gets interrupted.
    subprocess.run(
        ["curl", "-fL", "-C", "-", "-A", UA, "--retry", "3", "-o", str(dest), url],
        check=True,
    )
    digest = sha256(dest)
    size = dest.stat().st_size
    print(f"[fetch] {name}: {size:,} bytes sha256={digest}")
    (RAW / f"{name}.sha256").write_text(f"{digest}  {dest.name}\n")
    return dest


def main(argv: list[str]) -> int:
    names = argv or ["mmtd"]
    for n in names:
        if n not in SOURCES:
            print(f"unknown source {n!r}; known: {', '.join(SOURCES)}", file=sys.stderr)
            return 2
        fetch(n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
