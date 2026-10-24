"""Control condition: colour from the dataset's own audio features (spec 6.4).

The same axis mapping as the model, fed measured features instead of a
language model's impression. The model is never shown these features --
section 6.4 depends on the two tables being independent.

Mapping, with the one judgement call documented in the README:
  valence <- 2*valence - 1       (feature valence is 0..1, our axis is -1..1)
  arousal <- energy
  warmth  <- acousticness        (acoustic/wooden vs electronic/metallic)
  density <- loudness rescaled from dB: clamp((loudness + 60) / 60, 0, 1)
  grain   <- there is no clean equivalent in this feature set. We use
             clamp(0.5*speechiness + 0.5*(1 - instrumentalness) * (1 - mode), 0, 1)
             ... see README; it is defensible, not canonical, and it is the
             weakest link in the control condition. Stated plainly rather than
             dressed up.
"""

from __future__ import annotations

import duckdb

from pipeline.oklab import judgement_to_oklab


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return min(hi, max(lo, v))


def features_to_judgement(f: dict) -> dict:
    valence = clamp(float(f.get("valence") or 0.5)) * 2.0 - 1.0
    arousal = clamp(float(f.get("energy") or 0.5))
    warmth = clamp(float(f.get("acousticness") or 0.5))
    density = clamp(((float(f.get("loudness") or -30.0)) + 60.0) / 60.0)
    speech = clamp(float(f.get("speechiness") or 0.0))
    instr = clamp(float(f.get("instrumentalness") or 0.0))
    mode = clamp(float(f.get("mode") or 1.0))
    grain = clamp(0.5 * speech + 0.5 * (1.0 - instr) * (1.0 - mode))
    return {
        "valence": valence,
        "arousal": arousal,
        "warmth": warmth,
        "density": density,
        "grain": grain,
    }


def build(con: duckdb.DuckDBPyConnection, features_table: str = "npr_features") -> dict:
    """Create track_colour_baseline from a table of per-track audio features."""
    exists = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
        [features_table],
    ).fetchone()[0]
    if not exists:
        print(f"[baseline] no {features_table}; skipping control condition")
        return {"n_tracks_baseline": 0}
    rows = con.execute(
        f"SELECT track_key, artist_norm, title_norm, valence, energy, acousticness,"
        f" loudness, speechiness, instrumentalness, mode FROM {features_table}"
    ).fetchall()
    cols = (
        "valence", "energy", "acousticness", "loudness", "speechiness",
        "instrumentalness", "mode",
    )
    out = []
    for r in rows:
        f = dict(zip(cols, r[3:]))
        j = features_to_judgement(f)
        L, a, b = judgement_to_oklab(
            j["valence"], j["arousal"], j["warmth"], j["density"], j["grain"]
        )
        out.append((r[0], r[1], r[2], L, a, b, 1.0, 0, "", "features"))
    con.execute(
        "CREATE OR REPLACE TABLE track_colour_baseline("
        "track_key VARCHAR, artist_norm VARCHAR, title_norm VARCHAR,"
        "L DOUBLE, a DOUBLE, b DOUBLE, confidence DOUBLE, era INTEGER,"
        "note VARCHAR, provenance VARCHAR)"
    )
    if out:
        con.executemany(
            "INSERT INTO track_colour_baseline VALUES (" + ",".join("?" * 10) + ")", out
        )
    print(f"[baseline] {len(out):,} tracks coloured from audio features")
    return {"n_tracks_baseline": len(out)}
