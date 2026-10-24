"""Driver for the offline half: rollup -> export -> audit.

Kept separate from the individual modules so `make rollup`, `make export` and
`make audit` all share one DuckDB session; the pyramid tables are large enough
that rebuilding them per target would be wasteful.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pipeline import audit, colour_baseline, colour_table, export, ingest_npr, rollup, spatial

STATS = Path("data/work/stats.json")


def _normalise_stats() -> dict:
    p = Path("data/work/normalise_stats.json")
    return json.loads(p.read_text()) if p.exists() else {}


def main(argv: list[str]) -> int:
    con = rollup.connect()
    stats: dict = _normalise_stats()

    tstats = colour_table.build(con)
    stats.update(tstats)

    rollup.load_inputs(con, "track_colour")
    stats["n_events_total"] = con.execute(
        "SELECT count(*) FROM ev_raw"
    ).fetchone()[0]
    stats["n_events_coloured"] = con.execute(
        "SELECT count(*) FROM events_idx"
    ).fetchone()[0]
    stats.setdefault("events_normalised", stats["n_events_total"])
    stats.setdefault("unique_tracks", stats.get("n_tracks_total", 0))

    # Control condition, if the nowplaying-RS features are present.
    stats.update(colour_baseline.build(con, ingest_npr.FEATURES_TABLE))

    manifest = export.run(con, stats)

    base = spatial.BASE_RES
    stats["pre_suppression_base"] = con.execute(
        f"SELECT count(*) FROM agg_{base}"
    ).fetchone()[0]
    stats["post_suppression_base"] = con.execute(
        f"SELECT count(*) FROM agg_sup_{base}"
    ).fetchone()[0]
    stats["n_events_suppressed"] = con.execute(
        f"SELECT coalesce(sum(n), 0) FROM agg_{base} "
        f"WHERE NOT (n >= {rollup.MIN_N} AND n_users_ub >= {rollup.MIN_USERS})"
    ).fetchone()[0]
    manifest["n_events_suppressed"] = stats["n_events_suppressed"]
    manifest["n_events_coloured"] = stats["n_events_coloured"]
    (export.OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))

    audit.run(con, stats, manifest)
    STATS.write_text(json.dumps(stats, indent=1, default=str))

    flat = audit.flat_axes(con)
    if flat:
        print(
            f"[build] WARNING: judgement axes effectively constant: {', '.join(flat)}."
            " See reports/audit.md — do not ship a monochrome map.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
