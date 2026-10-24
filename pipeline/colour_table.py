"""Build the per-track colour table from the model cache (spec section 6.5).

Long tail, LOCKED policy: a track with confidence < 0.25, or with no model
record at all, takes the *artist's* mean colour when that artist has 3 or more
coloured tracks; otherwise it is excluded from aggregation entirely and
counted in n_uncoloured, which the UI surfaces. Excluded is better than
guessed.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pyarrow as pa

from pipeline import bulk
from pipeline.colour_model import CACHE, read_cache
from pipeline.cowen import EMOTIONS, dominant
from pipeline.oklab import oklch_to_oklab

MIN_CONFIDENCE = 0.25
MIN_ARTIST_TRACKS = 3
TRACKS = Path("data/work/tracks.parquet")


def build(con: duckdb.DuckDBPyConnection) -> dict:
    cached = read_cache()
    model_tags = sorted({r.get("model", "?") for r in cached.values()})
    rows = []
    for key, rec in cached.items():
        j = rec.get("judgement") or {}
        conf = float(j.get("confidence", 0.0))
        L, a, b = oklch_to_oklab(rec["L"], rec["C"], rec["h"])
        weights = {name: float(j.get(name, 0.0) or 0.0) for name in EMOTIONS}
        rows.append(
            (
                key,
                rec.get("artist_norm", ""),
                rec.get("title_norm", ""),
                L, a, b,
                conf,
                int(j.get("era", 0) or 0),
                j.get("note", ""),
                *(weights[name] for name in EMOTIONS),
                dominant(weights) or "",
                rec.get("model", "?"),
            )
        )
    bulk.create_table(
        con,
        "model_judgement",
        {
            "track_key": pa.string(),
            "artist_norm": pa.string(),
            "title_norm": pa.string(),
            "L": pa.float64(),
            "a": pa.float64(),
            "b": pa.float64(),
            "confidence": pa.float64(),
            "era": pa.int32(),
            "note": pa.string(),
            **{name: pa.float64() for name in EMOTIONS},
            "dominant": pa.string(),
            "model": pa.string(),
        },
        rows,
    )

    con.execute(
        f"CREATE OR REPLACE VIEW all_tracks AS SELECT * FROM '{TRACKS.as_posix()}'"
    )
    # Confident model colours.
    con.execute(
        f"""
        CREATE OR REPLACE TABLE colour_direct AS
        SELECT track_key, artist_norm, title_norm, L, a, b, confidence, era, note,
               'model' AS provenance
        FROM model_judgement WHERE confidence >= {MIN_CONFIDENCE}
        """
    )
    # Artist means, from the confident colours only.
    con.execute(
        f"""
        CREATE OR REPLACE TABLE artist_mean AS
        SELECT artist_norm,
               count(*) AS n_tracks,
               avg(L) AS L, avg(a) AS a, avg(b) AS b
        FROM colour_direct
        GROUP BY artist_norm
        HAVING count(*) >= {MIN_ARTIST_TRACKS}
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE colour_fallback AS
        SELECT t.track_key, t.artist_norm, t.title_norm,
               m.L, m.a, m.b, 0.0 AS confidence, 0 AS era, '' AS note,
               'artist_fallback' AS provenance
        FROM all_tracks t
        JOIN artist_mean m USING (artist_norm)
        WHERE t.track_key NOT IN (SELECT track_key FROM colour_direct)
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE track_colour AS
        SELECT * FROM colour_direct UNION ALL SELECT * FROM colour_fallback
        """
    )
    q = lambda sql: con.execute(sql).fetchone()[0]
    stats = {
        "model": ", ".join(model_tags) or "none",
        "n_tracks_total": q("SELECT count(*) FROM all_tracks"),
        "n_model_records": len(cached),
        "n_tracks_coloured_model": q("SELECT count(*) FROM colour_direct"),
        "n_tracks_artist_fallback": q("SELECT count(*) FROM colour_fallback"),
        "n_tracks_low_confidence": q(
            f"SELECT count(*) FROM model_judgement WHERE confidence < {MIN_CONFIDENCE}"
        ),
    }
    stats["n_tracks_coloured"] = q("SELECT count(*) FROM track_colour")
    stats["n_tracks_uncoloured"] = stats["n_tracks_total"] - stats["n_tracks_coloured"]
    for k, v in stats.items():
        print(f"[colour_table] {k}: {v}")
    return stats
