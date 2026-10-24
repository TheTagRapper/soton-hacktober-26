"""Dump the real raw schemas for inspection (spec section 4.1).

The spec is explicit: do not hardcode the dataset schema from the document.
Enumerate what is actually in the archive, print the header and first rows,
and write the adapter against that.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

RAW = Path("data/raw")
WORK = Path("data/work")


def dump(archive: Path, out: Path, n_rows: int = 3) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [f"# schema dump of {archive} ({archive.stat().st_size:,} bytes)", ""]
    with zipfile.ZipFile(archive) as zf:
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            if info.is_dir():
                continue
            lines.append(f"## {info.filename}  ({info.file_size:,} bytes)")
            try:
                with zf.open(info) as fh:
                    head = fh.read(1 << 16).decode("utf-8", "replace").splitlines()
            except Exception as exc:  # pragma: no cover - diagnostic path
                lines.append(f"  <unreadable: {exc}>")
                lines.append("")
                continue
            for i, row in enumerate(head[: n_rows + 1]):
                tag = "header" if i == 0 else f"row{i}"
                lines.append(f"  {tag}: {row[:600]}")
            if head:
                lines.append(f"  n_fields(tab): {len(head[0].split(chr(9)))}")
            lines.append("")
    out.write_text("\n".join(lines))
    print(out.read_text())


if __name__ == "__main__":
    dump(RAW / "mmtd.zip", WORK / "mmtd_schema.txt")
