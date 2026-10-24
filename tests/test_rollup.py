"""The correctness proof of the entire zoom behaviour (spec section 14.3).

The map claims that a coarse cell's colour is the average of the fine cells
inside it. The pyramid builds level r from level r+1, never from the events,
so that claim is only true if the count-weighted Oklab mean is associative
under H3's cell_to_parent. This test asserts it on a synthetic fixture:
res-5 computed through 8 -> 7 -> 6 -> 5 must equal res-5 computed directly
from the events, to 1e-9.

If this test fails, the zoom ladder is lying and nothing else in the project
matters.
"""

from __future__ import annotations

import math
import random

import duckdb
import pytest

from pipeline import rollup, spatial
from pipeline.cowen import EMOTIONS, blend

TOL = 1e-9
SEED = 4242


def _fixture(con: duckdb.DuckDBPyConnection, n_events: int = 4000) -> None:
    """Synthetic events over a few dense metros plus scattered noise."""
    rng = random.Random(SEED)
    metros = [(51.5074, -0.1278), (40.7128, -74.0060), (-23.55, -46.63), (35.68, 139.69)]
    tracks = []
    for i in range(60):
        weights = {name: rng.random() for name in EMOTIONS}
        L, a, b = blend(weights)
        tracks.append((f"t{i:03d}", L, a, b))
    con.execute(
        "CREATE OR REPLACE TABLE track_colour("
        "track_key VARCHAR, artist_norm VARCHAR, title_norm VARCHAR,"
        "L DOUBLE, a DOUBLE, b DOUBLE, confidence DOUBLE, era INTEGER,"
        "note VARCHAR, provenance VARCHAR)"
    )
    con.executemany(
        "INSERT INTO track_colour VALUES (?,?,?,?,?,?,1.0,2000,'','model')",
        [(k, "a", "t", L, a, b) for k, L, a, b in tracks],
    )

    rows = []
    for i in range(n_events):
        lat0, lon0 = metros[i % len(metros)]
        lat = lat0 + rng.gauss(0, 0.25)
        lon = lon0 + rng.gauss(0, 0.25)
        key = tracks[rng.randrange(len(tracks))][0]
        cell = spatial.cell_at(lat, lon, spatial.BASE_RES)
        # Several frames and several users per cell, so suppression and the
        # per-user cap are exercised rather than bypassed.
        rows.append(
            (f"e{i}", key, f"u{rng.randrange(400)}", "point", cell, f"{i % 6:02d}",
             f"{i % 6:02d}", f"{i % 7}", "tz")
        )
    con.execute(
        "CREATE OR REPLACE TABLE events_idx("
        "event_id VARCHAR, track_key VARCHAR, user_hash VARCHAR,"
        "geo_precision VARCHAR, cell UBIGINT, f_solar VARCHAR,"
        "f_chrono VARCHAR, f_dow VARCHAR, solar_src VARCHAR)"
    )
    con.executemany("INSERT INTO events_idx VALUES (?,?,?,?,?,?,?,?,?)", rows)


def _direct_res(con: duckdb.DuckDBPyConnection, res: int) -> dict:
    """res-N aggregate computed straight from the capped events."""
    rows = con.execute(
        "SELECT e.frame, e.cell, e.geo_precision, e.n, e.L, e.a, e.b FROM ("
        "  SELECT c.frame, c.cell, c.geo_precision, 1 AS n, t.L, t.a, t.b"
        "  FROM events_capped c JOIN track_colour t USING (track_key)"
        ") e"
    ).fetchall()
    acc: dict[tuple, list[float]] = {}
    for frame, cell, geo, n, L, a, b in rows:
        parent = spatial.parent_int(cell, res)
        k = (frame, parent, geo)
        slot = acc.setdefault(k, [0.0, 0.0, 0.0, 0.0])
        slot[0] += n
        slot[1] += L * n
        slot[2] += a * n
        slot[3] += b * n
    return {
        k: (v[0], v[1] / v[0], v[2] / v[0], v[3] / v[0]) for k, v in acc.items()
    }


@pytest.mark.parametrize("res", [7, 6, 5])
def test_pyramid_equals_direct_aggregation(res: int) -> None:
    con = duckdb.connect()
    _fixture(con)
    rollup.cap_users(con, "solar")
    rollup.build_pyramid(con, "solar", "track_colour", "agg")

    direct = _direct_res(con, res)
    pyramid = {
        (frame, cell, geo): (n, L, a, b)
        for frame, cell, geo, n, _nu, L, a, b in con.execute(
            f"SELECT frame, cell, geo_precision, n, n_users_ub, L, a, b FROM agg_{res}"
        ).fetchall()
    }

    assert pyramid, "pyramid produced no rows"
    assert set(pyramid) == set(direct), (
        f"res {res}: pyramid covers {len(pyramid)} cell-frames, direct {len(direct)}"
    )
    for key, (n_p, L_p, a_p, b_p) in pyramid.items():
        n_d, L_d, a_d, b_d = direct[key]
        assert n_p == n_d, f"res {res} {key}: n {n_p} != {n_d}"
        assert math.isclose(L_p, L_d, abs_tol=TOL), f"res {res} {key}: L"
        assert math.isclose(a_p, a_d, abs_tol=TOL), f"res {res} {key}: a"
        assert math.isclose(b_p, b_d, abs_tol=TOL), f"res {res} {key}: b"


def test_parent_is_total_and_deterministic() -> None:
    """cell_to_parent is what makes the pyramid well-defined.

    H3 children do not tile their parent exactly, which does not matter here:
    aggregation assigns every cell to exactly one parent, and this asserts
    that assignment is total, stable and resolution-correct all the way up.
    """
    rng = random.Random(SEED)
    for _ in range(200):
        lat = rng.uniform(-80, 80)
        lon = rng.uniform(-180, 180)
        base = spatial.cell_at(lat, lon, spatial.BASE_RES)
        chain = base
        for res in [7, 6, 5, 4, 3, 2, 1]:
            chain = spatial.parent_cell(chain, res)
            assert chain == spatial.parent_cell(base, res), "parent chain must be total"
            assert spatial.cell_resolution(chain) == res


def test_every_cell_has_exactly_one_parent() -> None:
    rng = random.Random(SEED)
    seen: dict[int, int] = {}
    for _ in range(3000):
        lat = rng.uniform(-85, 85)
        lon = rng.uniform(-180, 180)
        child = spatial.cell_at(lat, lon, 7)
        parent = spatial.parent_cell(child, 4)
        assert seen.setdefault(child, parent) == parent, "a cell must have one parent"


def test_cells_are_near_equal_area() -> None:
    """Near-equal-area is the whole reason the grid is H3 and not Mercator
    squares: colour per area of human listening has to mean the same thing in
    Svalbard as on the equator.

    "Near" is doing real work and this test pins down how much. Measured over
    a uniform sample from 85S to 85N, the largest H3 cell at a resolution is
    about 2.3x the smallest. A Web-Mercator square grid over the same span
    would vary by roughly cos(85 deg)^-2, about 130x. The claim this grid
    supports is "comparable areas", not "identical areas", and the drawn
    circles use the resolution's average area accordingly.
    """
    import h3

    for res in (2, 5, 8):
        avg = spatial.cell_area_m2(res)
        rng = random.Random(SEED + res)
        areas = []
        for _ in range(300):
            lat = rng.uniform(-85, 85)
            lon = rng.uniform(-180, 180)
            areas.append(
                h3.cell_area(h3.int_to_str(spatial.cell_at(lat, lon, res)), unit="m^2")
            )
        spread = max(areas) / min(areas)
        assert spread < 2.5, f"res {res}: cell areas vary by {spread:.2f}x"
        assert 0.9 < (sum(areas) / len(areas)) / avg < 1.1


def test_drawn_circle_matches_cell_area() -> None:
    """The map draws circles, not hexagons; they must cover the area the cell
    actually represents, or the picture overstates its own coverage."""
    import math

    for res in spatial.RESOLUTIONS:
        r = spatial.cell_radius_m(res)
        assert math.isclose(math.pi * r * r, spatial.cell_area_m2(res), rel_tol=1e-12)


def test_suppression_thresholds_are_the_locked_ones() -> None:
    assert (rollup.MIN_N, rollup.MIN_USERS) == (5, 3)
    assert rollup.USER_CAP_PER_CELL_FRAME == 20
