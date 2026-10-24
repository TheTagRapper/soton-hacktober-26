"""DuckDB pyramid build (spec section 7.4).

Level 8 is built from events. Every coarser level is built from the level
above, never from the events -- this is the associativity property of the
count-weighted Oklab mean being exercised, and it is what tests/test_rollup.py
proves. It is also much faster.

The DuckDB `h3` community extension is not available in this environment, so
per the spec's own fallback we index with h3-py and join a parent-mapping
table; the group-bys stay in DuckDB SQL exactly as specified.
"""

from __future__ import annotations

import os
import random
from pathlib import Path

import duckdb
import pyarrow as pa

from pipeline import bulk, spatial
from pipeline.spatial import BASE_RES, GEO_ENUM, RESOLUTIONS

WORK = Path("data/work")
EVENTS = WORK / "events.parquet"
DB = WORK / "rollup.duckdb"

# Section 7.5, LOCKED
MIN_N = 5
MIN_USERS = 3
USER_CAP_PER_CELL_FRAME = 20
SUBSAMPLE_SEED = 20260101

AXES = ("solar", "chrono", "dow")


def connect(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(path or DB))
    con.execute("PRAGMA threads=8")
    return con


def load_inputs(con: duckdb.DuckDBPyConnection, colour_table: str) -> None:
    """Index events at res 8 and attach the per-track colours."""
    con.execute(
        f"CREATE OR REPLACE VIEW ev_raw AS SELECT * FROM '{EVENTS.as_posix()}'"
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE events_geo AS
        SELECT event_id, track_key, user_hash, geo_precision, lat, lon,
               ts_utc, tz_offset_min
        FROM ev_raw
        WHERE track_key IN (SELECT track_key FROM """
        + colour_table
        + ")"
    )
    rows = con.execute(
        "SELECT event_id, lat, lon, ts_utc, tz_offset_min FROM events_geo"
    ).fetchall()
    print(f"[rollup] indexing {len(rows):,} coloured events at res {BASE_RES}")
    indexed = []
    for event_id, lat, lon, ts, tz in rows:
        cell = spatial.cell_at(lat, lon, BASE_RES)
        hour, src = spatial.solar_hour(ts.hour, ts.minute, lon, tz)
        # Local wall clock, for the chrono and dow axes.
        off = tz if tz is not None else int(round(lon / 15.0 * 60))
        local = ts.timestamp() + off * 60
        import datetime as _dt

        ld = _dt.datetime.fromtimestamp(local, _dt.UTC)
        indexed.append(
            (
                event_id,
                cell,
                spatial.frame_solar(hour),
                src,
                spatial.frame_chrono(ld.year, ld.month),
                spatial.frame_dow(ld.weekday()),
            )
        )
    bulk.create_table(
        con,
        "ev_idx",
        {
            "event_id": pa.string(),
            "cell": pa.uint64(),
            "f_solar": pa.string(),
            "solar_src": pa.string(),
            "f_chrono": pa.string(),
            "f_dow": pa.string(),
        },
        indexed,
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE events_idx AS
        SELECT e.event_id, e.track_key, e.user_hash, e.geo_precision,
               i.cell, i.f_solar, i.solar_src, i.f_chrono, i.f_dow
        FROM events_geo e JOIN ev_idx i USING (event_id)
        """
    )
    n = con.execute("SELECT count(*) FROM events_idx").fetchone()[0]
    src = dict(
        con.execute("SELECT solar_src, count(*) FROM ev_idx GROUP BY 1").fetchall()
    )
    print(f"[rollup] {n:,} events indexed; solar hour from {src}")


def cap_users(con: duckdb.DuckDBPyConnection, axis: str) -> int:
    """Cap any single user_hash to 20 events per (cell, frame) at res 8.

    Random subsampling with a fixed seed, so the build is reproducible: one
    teenager must not colour a province (section 7.5).
    """
    frame_col = {"solar": "f_solar", "chrono": "f_chrono", "dow": "f_dow"}[axis]
    # DuckDB rejects a volatile expression in a window ORDER BY, and we want
    # this reproducible anyway: order by a seeded hash of the event id. That is
    # a fixed-seed random subsample that survives a rebuild, which random()
    # would not.
    con.execute(
        f"""
        CREATE OR REPLACE TABLE events_capped AS
        SELECT * EXCLUDE (rk) FROM (
          SELECT event_id, track_key, user_hash, geo_precision, cell,
                 {frame_col} AS frame,
                 row_number() OVER (
                   PARTITION BY cell, {frame_col}, user_hash
                   ORDER BY hash(event_id || '{SUBSAMPLE_SEED}')
                 ) AS rk
          FROM events_idx
        ) WHERE rk <= {USER_CAP_PER_CELL_FRAME}
        """
    )
    before = con.execute("SELECT count(*) FROM events_idx").fetchone()[0]
    after = con.execute("SELECT count(*) FROM events_capped").fetchone()[0]
    print(f"[rollup] {axis}: user cap removed {before - after:,} of {before:,} events")
    return before - after


def build_pyramid(
    con: duckdb.DuckDBPyConnection, axis: str, colour_table: str, prefix: str = "agg"
) -> dict[str, int]:
    """agg_8 from events; agg_r from agg_{r+1} for r = 7..1."""
    stats: dict[str, int] = {}
    stats[f"{axis}_capped_events"] = cap_users(con, axis)

    con.execute(
        f"""
        CREATE OR REPLACE TABLE {prefix}_{BASE_RES} AS
        SELECT
          e.frame,
          e.cell                      AS cell,
          e.geo_precision,
          count(*)                    AS n,
          count(DISTINCT e.user_hash) AS n_users_ub,
          sum(c.L)/count(*)           AS L,
          sum(c.a)/count(*)           AS a,
          sum(c.b)/count(*)           AS b
        FROM events_capped e
        JOIN {colour_table} c USING (track_key)
        GROUP BY 1,2,3
        """
    )
    for res in RESOLUTIONS[1:]:
        child = res + 1
        cells = [
            r[0] for r in con.execute(f"SELECT DISTINCT cell FROM {prefix}_{child}").fetchall()
        ]
        bulk.create_table(
            con,
            f"parent_{res}",
            {"cell": pa.uint64(), "parent": pa.uint64()},
            [(c, spatial.parent_int(c, res)) for c in cells],
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE {prefix}_{res} AS
            SELECT
              frame,
              p.parent        AS cell,
              geo_precision,
              sum(n)          AS n,
              sum(n_users_ub) AS n_users_ub,   -- documented upper bound
              sum(L*n)/sum(n) AS L,
              sum(a*n)/sum(n) AS a,
              sum(b*n)/sum(n) AS b
            FROM {prefix}_{child} JOIN parent_{res} p USING (cell)
            GROUP BY 1,2,3
            """
        )
    for res in RESOLUTIONS:
        stats[f"{axis}_rows_r{res}"] = con.execute(
            f"SELECT count(*) FROM {prefix}_{res}"
        ).fetchone()[0]
    return stats


def suppress(con: duckdb.DuckDBPyConnection, res: int, prefix: str = "agg") -> str:
    """Section 7.5: drop a cell-frame unless n >= 5 AND n_users_ub >= 3.

    Privacy (real people's listening histories with coordinates attached),
    statistics (one teenager must not colour a province) and aesthetics
    (noise-floor cells look like television static) at once. Never quietly
    lowered to make the map look fuller.
    """
    name = f"{prefix}_sup_{res}"
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {name} AS
        SELECT * FROM {prefix}_{res}
        WHERE n >= {MIN_N} AND n_users_ub >= {MIN_USERS}
        """
    )
    return name
