"""Adapter: MMTD -> normalised events table (spec sections 4.1, 4.4).

WHAT THE ARCHIVE ACTUALLY CONTAINS, which differs from the specification:
the spec expected "separate tables for listening events, tracks, artists and
places, joined by integer ids". It is in fact a single fully denormalised
tab-separated file, `mmtd.txt`, 936 MB uncompressed, with 291 columns and a
header row -- the event, the track, the reverse-geocoded place and ~250
one-hot `lang_*` country-language flags all on one line. The real data wins;
see data/work/mmtd_schema.txt for the dump this adapter was written against.

Columns we use:
  tweet_id, tweet_userId, tweet_datetime, tweet_unixtime,
  tweet_longitude, tweet_latitude, artist_name, track_title,
  timezone (offset in *hours*, e.g. "0.00", "-5.00"), countryName, city

geo_precision: MMTD events are geotagged tweets reverse-geocoded to street
level, so every surviving row is 'point'. Nothing here is a bounding-box
centroid, so nothing is labelled one.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import duckdb

RAW = Path("data/raw/mmtd.zip")
WORK = Path("data/work")
TSV = WORK / "mmtd.txt"
MEMBER = "mmtd.txt"


def extract() -> Path:
    """Unzip the single TSV once; it is gitignored working data."""
    WORK.mkdir(parents=True, exist_ok=True)
    if TSV.exists() and TSV.stat().st_size > 0:
        print(f"[mmtd] using existing {TSV} ({TSV.stat().st_size:,} bytes)")
        return TSV
    print(f"[mmtd] extracting {MEMBER} from {RAW}")
    with zipfile.ZipFile(RAW) as zf, zf.open(MEMBER) as src, TSV.open("wb") as dst:
        while chunk := src.read(1 << 22):
            dst.write(chunk)
    print(f"[mmtd] extracted {TSV.stat().st_size:,} bytes")
    return TSV


def load(con: duckdb.DuckDBPyConnection) -> None:
    """Create table `mmtd_raw` with the subset of columns we need."""
    extract()
    con.execute(
        f"""
        CREATE OR REPLACE TABLE mmtd_raw AS
        SELECT
          tweet_id                           AS original_id,
          tweet_userId                       AS user_id,
          try_cast(tweet_unixtime AS BIGINT) AS unixtime,
          tweet_datetime                     AS datetime_local,
          try_cast(tweet_latitude  AS DOUBLE) AS lat,
          try_cast(tweet_longitude AS DOUBLE) AS lon,
          try_cast(timezone AS DOUBLE)       AS tz_hours,
          artist_name                        AS artist_raw,
          track_title                        AS title_raw,
          countryName                        AS country_name,
          city                               AS city
        FROM read_csv(
          '{TSV.as_posix()}',
          delim = '\t',
          header = true,
          quote = '',
          escape = '',
          all_varchar = true,
          ignore_errors = true,
          sample_size = -1
        )
        """
    )
    n = con.execute("SELECT count(*) FROM mmtd_raw").fetchone()[0]
    print(f"[mmtd] {n:,} raw rows")


SQL_EVENTS = """
CREATE OR REPLACE TABLE events_mmtd AS
SELECT
  'mmtd:' || original_id                      AS event_id,
  'mmtd'                                      AS source,
  to_timestamp(unixtime)                      AS ts_utc,
  lat,
  lon,
  'point'                                     AS geo_precision,
  CAST(round(tz_hours * 60) AS INTEGER)       AS tz_offset_min,
  artist_raw,
  title_raw,
  user_id                                     AS user_id_raw
FROM mmtd_raw
WHERE unixtime IS NOT NULL
  AND lat IS NOT NULL AND lon IS NOT NULL
  AND lat BETWEEN -90 AND 90
  AND lon BETWEEN -180 AND 180
  AND NOT (lat = 0 AND lon = 0)
  AND artist_raw IS NOT NULL AND artist_raw <> '' AND artist_raw <> 'NULL'
  AND title_raw  IS NOT NULL AND title_raw  <> '' AND title_raw  <> 'NULL'
"""


def drop_report(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Per-rule drop counts, for reports/audit.md (spec section 14.4)."""
    q = lambda w: con.execute(f"SELECT count(*) FROM mmtd_raw WHERE {w}").fetchone()[0]
    return {
        "raw_rows": q("1=1"),
        "dropped_no_timestamp": q("unixtime IS NULL"),
        "dropped_null_coords": q("lat IS NULL OR lon IS NULL"),
        "dropped_coords_out_of_range": q(
            "lat IS NOT NULL AND lon IS NOT NULL AND"
            " (lat NOT BETWEEN -90 AND 90 OR lon NOT BETWEEN -180 AND 180)"
        ),
        "dropped_null_island": q("lat = 0 AND lon = 0"),
        "dropped_missing_artist_or_title": q(
            "artist_raw IS NULL OR artist_raw IN ('', 'NULL')"
            " OR title_raw IS NULL OR title_raw IN ('', 'NULL')"
        ),
    }


def build(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    load(con)
    report = drop_report(con)
    con.execute(SQL_EVENTS)
    report["events_kept"] = con.execute("SELECT count(*) FROM events_mmtd").fetchone()[0]
    for k, v in report.items():
        print(f"[mmtd] {k}: {v:,}")
    return report
