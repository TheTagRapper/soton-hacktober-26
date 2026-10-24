"""Union the adapters, clean, derive track_key (spec sections 4.4 and 5).

Outputs (both gitignored working data):
  data/work/events.parquet  -- the LOCKED normalised events schema
  data/work/tracks.parquet  -- one row per track_key, sorted by n_events desc
"""

from __future__ import annotations

import hashlib
import re
import sys
import unicodedata
from pathlib import Path

import duckdb

from pipeline import ingest_mmtd

WORK = Path("data/work")
EVENTS = WORK / "events.parquet"
TRACKS = WORK / "tracks.parquet"

# Not a security measure. A de-identification measure, so that one user's
# influence can be capped (spec section 7.5) without storing source user ids.
USER_SALT = "chromatic-listening/v1/8f2c1a"

# --- section 5, LOCKED -------------------------------------------------------

_VERSION_SUFFIX = re.compile(
    r"[\(\[]\s*(?:remaster|remastered|deluxe|mono|stereo|\d{4} ?version|radio edit|"
    r"single version|album version|explicit|bonus track?)[^\)\]]*[\)\]]\s*$"
)
_FEAT_SPLIT = re.compile(r" feat\. | feat | ft\. | ft | featuring | with | x | vs ")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]")
_WS = re.compile(r"\s+")


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _finish(s: str) -> str:
    s = s.replace("&", "and")
    s = _NON_ALNUM.sub(" ", s)
    return _WS.sub(" ", s).strip()


def norm_title(title: str) -> str:
    s = _fold(title)
    # Step 2 may expose another suffix underneath; strip until stable.
    while True:
        stripped = _VERSION_SUFFIX.sub("", s).strip()
        if stripped == s:
            break
        s = stripped
    if s.startswith("the "):
        s = s[4:]
    return _finish(s)


def norm_artist(artist: str) -> str:
    s = _fold(artist)
    s = _FEAT_SPLIT.split(s)[0]
    if s.startswith("the "):
        s = s[4:]
    return _finish(s)


def track_key(artist_norm: str, title_norm: str) -> str:
    return hashlib.sha1(f"{artist_norm} {title_norm}".encode()).hexdigest()[:16]


def user_hash(user_id: str) -> str:
    return hashlib.sha256(f"{USER_SALT}:{user_id}".encode()).hexdigest()[:16]


# --- pipeline ----------------------------------------------------------------


def _derive(con: duckdb.DuckDBPyConnection, table: str) -> None:
    """Add track_key / artist_norm / title_norm / user_hash in Python.

    The section 5 algorithm is LOCKED and involves NFKD folding and ordered
    regex stripping; reimplementing it in SQL would be a second, divergent
    implementation of a locked spec. Instead we normalise the *distinct*
    (artist_raw, title_raw) pairs -- around 10^5 of them, not 10^6 events --
    and join the result back.
    """
    pairs = con.execute(
        f"SELECT DISTINCT artist_raw, title_raw FROM {table}"
    ).fetchall()
    print(f"[normalise] folding {len(pairs):,} distinct artist/title pairs")
    rows = []
    for artist_raw, title_raw in pairs:
        a = norm_artist(artist_raw)
        t = norm_title(title_raw)
        if not a or not t:
            continue
        rows.append((artist_raw, title_raw, a, t, track_key(a, t)))
    con.execute(
        "CREATE OR REPLACE TABLE pair_norm("
        "artist_raw VARCHAR, title_raw VARCHAR,"
        "artist_norm VARCHAR, title_norm VARCHAR, track_key VARCHAR)"
    )
    con.executemany("INSERT INTO pair_norm VALUES (?,?,?,?,?)", rows)

    users = [r[0] for r in con.execute(f"SELECT DISTINCT user_id_raw FROM {table}").fetchall()]
    print(f"[normalise] hashing {len(users):,} distinct users")
    con.execute("CREATE OR REPLACE TABLE user_norm(user_id_raw VARCHAR, user_hash VARCHAR)")
    con.executemany(
        "INSERT INTO user_norm VALUES (?,?)", [(u, user_hash(u)) for u in users]
    )


def build(con: duckdb.DuckDBPyConnection | None = None) -> dict[str, int]:
    con = con or duckdb.connect()
    report = ingest_mmtd.build(con)
    con.execute("CREATE OR REPLACE VIEW events_all AS SELECT * FROM events_mmtd")
    _derive(con, "events_all")

    con.execute(
        """
        CREATE OR REPLACE TABLE events AS
        SELECT
          e.event_id, e.source, e.ts_utc, e.lat, e.lon, e.geo_precision,
          e.tz_offset_min, e.artist_raw, e.title_raw,
          p.track_key, p.artist_norm, p.title_norm, u.user_hash
        FROM events_all e
        JOIN pair_norm p USING (artist_raw, title_raw)
        JOIN user_norm u USING (user_id_raw)
        """
    )
    report["events_dropped_unnormalisable_text"] = (
        report["events_kept"] - con.execute("SELECT count(*) FROM events").fetchone()[0]
    )
    report["events_normalised"] = con.execute("SELECT count(*) FROM events").fetchone()[0]

    WORK.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"""
        COPY (
          SELECT event_id, source, ts_utc, lat, lon, geo_precision, tz_offset_min,
                 artist_raw, title_raw, track_key, user_hash
          FROM events
        ) TO '{EVENTS.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    con.execute(
        f"""
        COPY (
          SELECT track_key, any_value(artist_norm) AS artist_norm,
                 any_value(title_norm) AS title_norm, count(*) AS n_events
          FROM events GROUP BY track_key ORDER BY n_events DESC
        ) TO '{TRACKS.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    report["unique_tracks"] = con.execute(
        "SELECT count(DISTINCT track_key) FROM events"
    ).fetchone()[0]
    report["unique_users"] = con.execute(
        "SELECT count(DISTINCT user_hash) FROM events"
    ).fetchone()[0]
    # strftime in SQL: handing a raw TIMESTAMP to Python makes DuckDB want pytz.
    span = con.execute(
        "SELECT strftime(min(ts_utc), '%Y-%m-%d'), strftime(max(ts_utc), '%Y-%m-%d')"
        " FROM events"
    ).fetchone()
    print(f"[normalise] time span {span[0]} .. {span[1]}")
    for k, v in report.items():
        print(f"[normalise] {k}: {v:,}")
    print(f"[normalise] wrote {EVENTS} and {TRACKS}")
    return report


if __name__ == "__main__":
    build()
    sys.exit(0)
