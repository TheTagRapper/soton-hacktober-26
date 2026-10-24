"""Adapter: #nowplaying-RS -> events table + audio features (spec section 4.2).

Zenodo record 3248543, CC BY 4.0 -- the cleaner licence of the two. The file
that matters is `context_content_features.csv`: per event it carries ids, a
`created_at`, geographic `coordinates` / `place` / `geo` as geoJSON strings,
and the per-track audio features that are the control condition for section
6.4. Those features are a frozen 2018 snapshot of a feature set that is no
longer publicly obtainable; they are treated as irreplaceable and never
re-derived from a live API.

Only a minority of events carry precise `coordinates`. This adapter keeps
exactly those. Events with only a `place` bounding box, or only a country, are
DROPPED -- never scattered, never jittered, never distributed within the
country. That is the single most important data rule in the project.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import duckdb
import pyarrow as pa

from pipeline import bulk

RAW = Path("data/raw/npr.zip")
WORK = Path("data/work")
CSV = WORK / "context_content_features.csv"
MEMBER_CANDIDATES = ("context_content_features.csv",)
FEATURES_TABLE = "npr_features"


def available() -> bool:
    return RAW.exists() and RAW.stat().st_size > 0


def extract() -> Path | None:
    if CSV.exists() and CSV.stat().st_size > 0:
        return CSV
    if not available():
        print(f"[npr] {RAW} absent; skipping secondary dataset")
        return None
    WORK.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(RAW) as zf:
        names = [i.filename for i in zf.infolist() if not i.is_dir()]
        member = next(
            (n for n in names if Path(n).name in MEMBER_CANDIDATES),
            None,
        )
        if member is None:
            print(f"[npr] no context_content_features.csv in archive; found {names}")
            return None
        print(f"[npr] extracting {member}")
        with zf.open(member) as src, CSV.open("wb") as dst:
            while chunk := src.read(1 << 22):
                dst.write(chunk)
    print(f"[npr] extracted {CSV.stat().st_size:,} bytes")
    return CSV


def dump_schema(out: Path = WORK / "npr_schema.txt") -> None:
    """As with MMTD: inspect, never hardcode the schema from the spec."""
    if not available():
        return
    lines = []
    with zipfile.ZipFile(RAW) as zf:
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            if info.is_dir():
                continue
            lines.append(f"## {info.filename} ({info.file_size:,} bytes)")
            with zf.open(info) as fh:
                head = fh.read(1 << 16).decode("utf-8", "replace").splitlines()
            for i, row in enumerate(head[:4]):
                lines.append(f"  {'header' if i == 0 else f'row{i}'}: {row[:600]}")
            lines.append("")
    out.write_text("\n".join(lines))
    print(out.read_text()[:4000])


def point_from_coordinates(raw: str | None) -> tuple[float, float] | None:
    """Parse the geoJSON `coordinates` field. Points only; no bounding boxes."""
    if not raw or raw in ("", "NULL", "None", "nan"):
        return None
    try:
        obj = json.loads(raw.replace("'", '"'))
    except (json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("type") != "Point":
        return None  # a Polygon here is a place bounding box: drop, never centre
    coords = obj.get("coordinates")
    if not (isinstance(coords, (list, tuple)) and len(coords) == 2):
        return None
    lon, lat = float(coords[0]), float(coords[1])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    return lat, lon


FEATURE_COLS = (
    "instrumentalness", "liveness", "speechiness", "danceability", "valence",
    "loudness", "tempo", "acousticness", "energy", "mode", "key",
)


def build(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Create events_npr and npr_features. Returns a drop report."""
    path = extract()
    if path is None:
        return {}
    con.execute(
        f"""
        CREATE OR REPLACE TABLE npr_raw AS
        SELECT * FROM read_csv('{path.as_posix()}', header = true,
                               all_varchar = true, ignore_errors = true,
                               sample_size = -1)
        """
    )
    cols = {r[0] for r in con.execute("DESCRIBE npr_raw").fetchall()}
    print(f"[npr] {len(cols)} columns: {sorted(cols)}")
    need = {"coordinates", "created_at", "user_id", "artist_id", "track_id"}
    missing = need - cols
    if missing:
        print(f"[npr] archive lacks expected columns {missing}; skipping")
        return {}

    rows = con.execute(
        "SELECT id, user_id, created_at, coordinates FROM npr_raw"
    ).fetchall() if "id" in cols else con.execute(
        "SELECT row_number() OVER () AS id, user_id, created_at, coordinates FROM npr_raw"
    ).fetchall()
    kept = []
    dropped_no_point = 0
    for rid, user_id, created_at, coordinates in rows:
        pt = point_from_coordinates(coordinates)
        if pt is None:
            dropped_no_point += 1
            continue
        kept.append((str(rid), str(user_id), created_at, pt[0], pt[1]))
    print(
        f"[npr] {len(kept):,} events with a precise point; "
        f"{dropped_no_point:,} dropped for having no point (never scattered)"
    )
    bulk.create_table(
        con,
        "npr_points",
        {
            "original_id": pa.string(),
            "user_id_raw": pa.string(),
            "created_at": pa.string(),
            "lat": pa.float64(),
            "lon": pa.float64(),
        },
        kept,
    )

    id_col = "id" if "id" in cols else "NULL"
    artist_col = "artist_name" if "artist_name" in cols else "artist_id"
    title_col = "track_name" if "track_name" in cols else "track_id"
    con.execute(
        f"""
        CREATE OR REPLACE TABLE events_npr AS
        SELECT
          'npr:' || p.original_id                        AS event_id,
          'npr'                                          AS source,
          try_cast(r.created_at AS TIMESTAMP)            AS ts_utc,
          p.lat, p.lon,
          'point'                                        AS geo_precision,
          NULL::INTEGER                                  AS tz_offset_min,
          r.{artist_col}                                 AS artist_raw,
          r.{title_col}                                  AS title_raw,
          p.user_id_raw
        FROM npr_points p
        JOIN npr_raw r ON r.{id_col} = p.original_id
        WHERE try_cast(r.created_at AS TIMESTAMP) IS NOT NULL
        """
    )
    feats = [c for c in FEATURE_COLS if c in cols]
    con.execute(
        f"""
        CREATE OR REPLACE TABLE npr_track_features AS
        SELECT r.{artist_col} AS artist_raw, r.{title_col} AS title_raw,
               {', '.join(f'avg(try_cast(r.{c} AS DOUBLE)) AS {c}' for c in feats)}
        FROM npr_raw r GROUP BY 1, 2
        """
    )
    n = con.execute("SELECT count(*) FROM events_npr").fetchone()[0]
    print(f"[npr] {n:,} events normalisable; {len(feats)} audio feature columns")
    return {
        "npr_rows": len(rows),
        "npr_dropped_no_point": dropped_no_point,
        "npr_events": n,
    }
