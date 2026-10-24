"""Arrow shards + manifest.json (spec section 9, LOCKED).

One Arrow IPC file per (axis, res, frame). Columns in order:
  cell Uint64, n Uint32, n_users_ub Uint32, geo Uint8, L/a/b Float32.

Only derived aggregates that survived suppression ship. Raw events are never
committed or deployed.

Hard limits honoured: each file under 25 MB, total files under 20,000 --
Cloudflare Pages free-tier deployment limits, not suggestions. The data
directory carries a version segment so the immutable Cache-Control header is
safe across rebuilds.
"""

from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.ipc as ipc

from pipeline import rollup
from pipeline.cowen import EMOTIONS, LABELS, PALETTE_SRGB, SHARPEN as COWEN_SHARPEN
from pipeline.spatial import BASE_RES, DOW_LABELS, GEO_ENUM, RESOLUTIONS, cell_radius_m

DATA_VERSION = os.environ.get("DATA_VERSION", "v1")
OUT = Path("site/public/data") / DATA_VERSION

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_FILES = 20_000
USEFUL_RES_MIN_CELLS = 500

SCHEMA = pa.schema(
    [
        pa.field("cell", pa.uint64()),
        pa.field("n", pa.uint32()),
        pa.field("n_users_ub", pa.uint32()),
        pa.field("geo", pa.uint8()),
        pa.field("L", pa.float32()),
        pa.field("a", pa.float32()),
        pa.field("b", pa.float32()),
    ]
)

SOURCES = [
    {
        "name": "MMTD (Million Musical Tweets Dataset)",
        "citation": "Hauger & Schedl, ISMIR 2013, Johannes Kepler University Linz",
        "url": "https://www.cp.jku.at/datasets/MMTD/mmtd.zip",
        "licence": "Research use, as published by JKU Linz; raw data not redistributed",
    }
]


def write_shard(rows: list[tuple], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = list(zip(*rows)) if rows else [()] * 7
    table = pa.Table.from_arrays(
        [
            pa.array(cols[0], type=pa.uint64()),
            pa.array(cols[1], type=pa.uint32()),
            pa.array(cols[2], type=pa.uint32()),
            pa.array(cols[3], type=pa.uint8()),
            pa.array(cols[4], type=pa.float32()),
            pa.array(cols[5], type=pa.float32()),
            pa.array(cols[6], type=pa.float32()),
        ],
        schema=SCHEMA,
    )
    with path.open("wb") as fh, ipc.new_file(fh, SCHEMA) as writer:
        writer.write_table(table)
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise SystemExit(
            f"{path} is {size:,} bytes, over the 25 MB Cloudflare Pages per-file limit"
        )
    return size


def export_axis(
    con: duckdb.DuckDBPyConnection, axis: str, subdir: Path, prefix: str = "agg"
) -> tuple[list[str], dict[int, int], int]:
    """Write every (res, frame) shard for one axis. Returns frames, cells/res, files."""
    frames: set[str] = set()
    cells_per_res: dict[int, int] = {}
    n_files = 0
    for res in RESOLUTIONS:
        table = rollup.suppress(con, res, prefix)
        rows = con.execute(
            f"""
            SELECT frame, cell, sum(n) AS n, sum(n_users_ub) AS nu,
                   min(geo_rank) AS geo,
                   sum(L*n)/sum(n) AS L, sum(a*n)/sum(n) AS a, sum(b*n)/sum(n) AS b
            FROM (
              SELECT frame, cell, n, n_users_ub, L, a, b,
                     CASE geo_precision WHEN 'point' THEN 0
                          WHEN 'place_centroid' THEN 1 ELSE 2 END AS geo_rank
              FROM {table}
            )
            GROUP BY frame, cell, geo_rank
            ORDER BY frame, cell
            """
        ).fetchall()
        by_frame: dict[str, list[tuple]] = {}
        for frame, cell, n, nu, geo, L, a, b in rows:
            by_frame.setdefault(frame, []).append((cell, int(n), int(nu), int(geo), L, a, b))
        cells_per_res[res] = sum(len(v) for v in by_frame.values())
        for frame, frows in by_frame.items():
            frames.add(frame)
            write_shard(frows, subdir / f"r{res}" / f"{frame}.arrow")
            n_files += 1
    return sorted(frames), cells_per_res, n_files


def max_useful_res(cells_per_res: dict[int, int], n_frames: int) -> int:
    """Deepest resolution where surviving cells per frame number 500 or more.

    Computed, never assumed: with MMTD alone this may well be 6 or 7, and the
    front end has to clamp its zoom ladder to it rather than showing an empty
    map when the viewer zooms past what the data can support.
    """
    best = min(RESOLUTIONS)
    for res in sorted(cells_per_res):
        if cells_per_res[res] / max(1, n_frames) >= USEFUL_RES_MIN_CELLS:
            best = max(best, res)
    return best


def export_notes(con: duckdb.DuckDBPyConnection, n: int = 300) -> int:
    """A sample of the model's `note` strings -- for the artwork, not the maths."""
    rows = con.execute(
        "SELECT artist_norm, title_norm, note, track_key FROM track_colour "
        "WHERE note IS NOT NULL AND length(note) > 0"
    ).fetchall()
    random.Random(20260101).shuffle(rows)
    sample = [
        {"track_key": r[3], "artist": r[0], "title": r[1], "note": r[2]} for r in rows[:n]
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "notes.json").write_text(json.dumps(sample, ensure_ascii=False, indent=1))
    return len(sample)


def run(con: duckdb.DuckDBPyConnection, stats: dict) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    axes_meta: dict[str, dict] = {}
    cells_all: dict[str, dict[int, int]] = {}
    n_files = 0
    labels = {
        "solar": "local solar hour",
        "chrono": "month",
        "dow": "local day of week",
    }
    for axis in rollup.AXES:
        # Carries {axis}_capped_events and per-resolution row counts, which
        # reports/audit.md reports; dropping them silently made the audit
        # claim the per-user cap removed nothing.
        stats.update(rollup.build_pyramid(con, axis, "track_colour", "agg"))
        frames, cells, files = export_axis(con, axis, OUT / axis, "agg")
        n_files += files
        axes_meta[axis] = {"frames": frames, "label": labels[axis]}
        cells_all[axis] = cells
        print(f"[export] {axis}: {len(frames)} frames, {files} files, cells {cells}")

    # Control condition (section 6.4), same tree under baseline/.
    has_baseline = (
        con.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name = 'track_colour_baseline'"
        ).fetchone()[0]
        > 0
    )
    if has_baseline:
        for axis in rollup.AXES:
            stats.update(rollup.build_pyramid(con, axis, "track_colour_baseline", "bagg"))
            _, _, files = export_axis(con, axis, OUT / "baseline" / axis, "bagg")
            n_files += files

    n_notes = export_notes(con)
    if n_files > MAX_FILES:
        raise SystemExit(f"{n_files} files exceeds the 20,000-file Pages limit")

    solar_frames = len(axes_meta["solar"]["frames"]) or 1
    manifest = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data_version": DATA_VERSION,
        "model": stats.get("model", "unknown"),
        "backend": stats.get("backend", "llamacpp"),
        "sources": [dict(s, events_used=stats.get("n_events_total", 0)) for s in SOURCES],
        "axes": axes_meta,
        "resolutions": sorted(RESOLUTIONS),
        "base_res": BASE_RES,
        "max_useful_res": max_useful_res(cells_all["solar"], solar_frames),
        "cell_radius_m": {str(r): round(cell_radius_m(r), 2) for r in sorted(RESOLUTIONS)},
        "cells_per_res": {str(k): v for k, v in sorted(cells_all["solar"].items())},
        "cells_per_res_per_axis": {
            a: {str(k): v for k, v in sorted(c.items())} for a, c in cells_all.items()
        },
        "has_baseline": has_baseline,
        "n_files": n_files,
        "n_notes": n_notes,
        "n_events_total": stats.get("n_events_total", 0),
        "n_events_coloured": stats.get("n_events_coloured", 0),
        "n_events_suppressed": stats.get("n_events_suppressed", 0),
        "n_tracks_total": stats.get("n_tracks_total", 0),
        "n_tracks_coloured": stats.get("n_tracks_coloured", 0),
        "n_tracks_artist_fallback": stats.get("n_tracks_artist_fallback", 0),
        "n_tracks_uncoloured": stats.get("n_tracks_uncoloured", 0),
        "suppression": {
            "min_n": rollup.MIN_N,
            "min_n_users_ub": rollup.MIN_USERS,
            "user_cap_per_cell_frame": rollup.USER_CAP_PER_CELL_FRAME,
        },
        "colour_model": {
            "palette": "Cowen, Fang, Sauter & Keltner (PNAS 2020), 13 dimensions "
            "of music-evoked emotion; colours as published at "
            "https://www.ocf.berkeley.edu/~acowen/music.html",
            "blend": "intensity-weighted mean of the 13 palette colours in Oklab, "
            f"weights raised to {COWEN_SHARPEN} first",
            "emotions": {name: PALETTE_SRGB[name][0] for name in EMOTIONS},
            "labels": LABELS,
        },
        "grid": {
            "kind": "H3 hexagons, resolutions 8 to 1",
            "drawn_as": "one soft-edged circle per cell, of equal area to the "
            "hexagon, overlapping its neighbours",
        },
        "dow_labels": DOW_LABELS,
        "geo_enum": GEO_ENUM,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"[export] wrote {OUT}/manifest.json; {n_files} shard files")
    return manifest
