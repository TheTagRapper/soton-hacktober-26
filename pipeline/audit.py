"""Coverage and sanity report -> reports/audit.md (spec section 14.4).

Read this before declaring success. In particular: if the five judgement axes
are not reasonably spread -- if the model has given nearly everything the same
answer -- stop and report that rather than shipping a monochrome map. This
module decides that for you and says so in the report.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import duckdb

from pipeline.colour_model import AXES
from pipeline.spatial import RESOLUTIONS

REPORT = Path("reports/audit.md")
SPREAD_MIN_STDEV = 0.08  # below this, an axis is effectively a constant


def histogram(values: list[float], lo: float, hi: float, bins: int = 20, width: int = 46) -> str:
    if not values:
        return "  (no data)"
    counts = [0] * bins
    for v in values:
        idx = int((v - lo) / (hi - lo) * bins)
        counts[min(bins - 1, max(0, idx))] += 1
    peak = max(counts) or 1
    out = []
    for i, c in enumerate(counts):
        edge = lo + (hi - lo) * i / bins
        bar = "#" * int(round(c / peak * width))
        out.append(f"  {edge:+.2f} | {bar:<{width}} {c:,}")
    return "\n".join(out)


def percentiles(values: list[float], ps=(5, 50, 95)) -> dict[int, float]:
    if not values:
        return {p: 0.0 for p in ps}
    s = sorted(values)
    return {
        p: s[min(len(s) - 1, max(0, int(round(p / 100 * (len(s) - 1)))))] for p in ps
    }


def run(con: duckdb.DuckDBPyConnection, stats: dict, manifest: dict) -> str:
    q = lambda sql: con.execute(sql).fetchall()
    lines: list[str] = [
        "# Audit — Chromatic Listening",
        "",
        f"Built {manifest.get('built_at')} · data version `{manifest.get('data_version')}`",
        f"· colour model `{manifest.get('model')}` via `{manifest.get('backend')}`",
        "",
        "## Ingestion",
        "",
        "| metric | value |",
        "|---|---|",
    ]
    for k in (
        "raw_rows",
        "dropped_no_timestamp",
        "dropped_null_coords",
        "dropped_coords_out_of_range",
        "dropped_null_island",
        "dropped_missing_artist_or_title",
        "events_kept",
        "events_dropped_unnormalisable_text",
        "events_normalised",
        "unique_tracks",
        "unique_users",
    ):
        if k in stats:
            lines.append(f"| {k.replace('_', ' ')} | {stats[k]:,} |")

    lines += [
        "",
        "## Track colouring",
        "",
        "| metric | value |",
        "|---|---|",
    ]
    for k in (
        "n_tracks_total",
        "n_model_records",
        "n_tracks_coloured_model",
        "n_tracks_artist_fallback",
        "n_tracks_low_confidence",
        "n_tracks_coloured",
        "n_tracks_uncoloured",
    ):
        if k in stats:
            lines.append(f"| {k.replace('_', ' ')} | {stats[k]:,} |")

    ev_total = stats.get("events_normalised", 0)
    ev_col = stats.get("n_events_coloured", 0)
    lines += [
        "",
        f"Events whose track has a colour: **{ev_col:,} of {ev_total:,}**"
        f" ({ev_col / max(1, ev_total):.1%}). The remainder are excluded from"
        " aggregation, not guessed at.",
        "",
        "## Suppression (n >= 5 AND n_users_ub >= 3, user cap 20 per cell-frame)",
        "",
        "| metric | value |",
        "|---|---|",
        f"| cell-frames before suppression (res 8, solar) | {stats.get('pre_suppression_r8', 0):,} |",
        f"| cell-frames after suppression (res 8, solar) | {stats.get('post_suppression_r8', 0):,} |",
        f"| events removed by the per-user cap (solar) | {stats.get('solar_capped_events', 0):,} |",
        f"| events behind suppressed cell-frames | {stats.get('n_events_suppressed', 0):,} |",
        "",
        "## Cells per resolution per axis (after suppression)",
        "",
        "| axis | " + " | ".join(f"r{r}" for r in sorted(RESOLUTIONS)) + " |",
        "|---" * (len(RESOLUTIONS) + 1) + "|",
    ]
    for axis, cells in manifest.get("cells_per_res_per_axis", {}).items():
        lines.append(
            f"| {axis} | " + " | ".join(f"{cells.get(str(r), 0):,}" for r in sorted(RESOLUTIONS)) + " |"
        )
    lines += [
        "",
        f"`max_useful_res` = **{manifest.get('max_useful_res')}** "
        "(deepest resolution with >= 500 surviving cells per frame). The front end "
        "clamps the zoom ladder to this rather than showing an empty map when zoomed in.",
        "",
        "## Distribution of n (plays per cell-frame)",
        "",
    ]
    for res in (8, 6, 4):
        table = f"agg_sup_{res}"
        try:
            ns = [r[0] for r in q(f"SELECT n FROM {table}")]
        except Exception:
            continue
        p = percentiles(ns)
        lines.append(
            f"- res {res}: n p5 = {p[5]:,} · p50 = {p[50]:,} · p95 = {p[95]:,} "
            f"· max = {max(ns):,}" if ns else f"- res {res}: no cells"
        )

    lines += ["", "## Judgement axes", ""]
    flat = False
    for axis in AXES:
        vals = [r[0] for r in q(f"SELECT {axis} FROM model_judgement")]
        if not vals:
            lines += [f"### {axis}", "", "  (no model records)", ""]
            continue
        lo, hi = (-1.0, 1.0) if axis == "valence" else (0.0, 1.0)
        sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        if sd < SPREAD_MIN_STDEV:
            flat = True
        lines += [
            f"### {axis} — n={len(vals):,} mean={statistics.fmean(vals):+.3f} "
            f"sd={sd:.3f} min={min(vals):+.2f} max={max(vals):+.2f}",
            "",
            "```",
            histogram(vals, lo, hi),
            "```",
            "",
        ]
    conf = [r[0] for r in q("SELECT confidence FROM model_judgement")]
    if conf:
        lines += [
            f"### confidence — mean={statistics.fmean(conf):.3f}",
            "",
            "```",
            histogram(conf, 0.0, 1.0),
            "```",
            "",
        ]

    lines += ["## Verdict", ""]
    if flat:
        lines += [
            "**STOP — at least one judgement axis is effectively constant "
            f"(population sd < {SPREAD_MIN_STDEV}).** The model has given nearly "
            "everything the same answer on that axis, which will ship a monochrome "
            "map. Retune the system prompt or the model before deploying; do not "
            "compensate by stretching the colour formula.",
            "",
        ]
    else:
        lines += [
            "All five judgement axes are spread (population sd >= "
            f"{SPREAD_MIN_STDEV} on each). The colour field will vary.",
            "",
        ]
    lines += [
        "## Honesty",
        "",
        "This is a portrait of a dataset, not of humanity. MMTD and "
        "#nowplaying-RS are harvested from public Twitter posts in 2011-2013 and "
        "2014-2018 respectively, so the listening they record is biased towards "
        "young, urban, anglophone, smartphone-owning people who tweeted what they "
        "were playing. Empty regions are mostly absence of that behaviour, not "
        "absence of music.",
        "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    REPORT.write_text(text)
    print(f"[audit] wrote {REPORT} ({len(text):,} bytes); flat_axis={flat}")
    (REPORT.parent / "audit_stats.json").write_text(json.dumps(stats, indent=1, default=str))
    return text


def flat_axes(con: duckdb.DuckDBPyConnection) -> list[str]:
    out = []
    for axis in AXES:
        vals = [r[0] for r in con.execute(f"SELECT {axis} FROM model_judgement").fetchall()]
        if len(vals) > 1 and statistics.pstdev(vals) < SPREAD_MIN_STDEV:
            out.append(axis)
    return out
