"""The grid, and the two time axes.

GRID -- H3 hexagons (`h3-py` offline, `h3-js` in the browser), at resolutions
8 down to 1.

The rationale, so it is not second-guessed: a Web-Mercator square grid would
make a cell in Svalbard represent a tiny fraction of the ground area of one on
the equator, so "colour per area of human listening" would be silently false.
H3 cells are near-equal-area, and each has exactly one deterministic parent via
cell_to_parent, which is total -- which is all the pyramid needs. Children do
not tile their parent exactly; that is irrelevant here, because aggregation
assigns each cell to exactly one parent.

We never hand-roll H3 maths; h3-py does it.

Cells are carried through the pipeline and the Arrow shards as the H3 index's
64-bit integer form. The front end draws each cell as a **circle** centred on
the hexagon, with the same area as the hexagon and a soft overlapping edge, so
the result reads as a continuous field rather than a honeycomb.
"""

from __future__ import annotations

import math

import h3

BASE_RES = 8
RESOLUTIONS = [8, 7, 6, 5, 4, 3, 2, 1]

GEO_ENUM = {"point": 0, "place_centroid": 1, "city": 2}


def cell_at(lat: float, lon: float, res: int = BASE_RES) -> int:
    """H3 index as a 64-bit integer (the export column type)."""
    return h3.str_to_int(h3.latlng_to_cell(lat, lon, res))


def parent_cell(cell: int, res: int) -> int:
    return h3.str_to_int(h3.cell_to_parent(h3.int_to_str(cell), res))


# Kept under both names: rollup.py talks about parents, tests about cells.
parent_int = parent_cell


def cell_to_hex(cell: int) -> str:
    return h3.int_to_str(cell)


def cell_centre(cell: int) -> tuple[float, float]:
    return h3.cell_to_latlng(h3.int_to_str(cell))


def cell_resolution(cell: int) -> int:
    return h3.get_resolution(h3.int_to_str(cell))


def cell_area_m2(res: int) -> float:
    """Average H3 cell area at a resolution, in square metres."""
    return h3.average_hexagon_area(res, unit="m^2")


def cell_radius_m(res: int) -> float:
    """Radius of a circle with the same area as an average cell at this
    resolution. This is what the front end draws, so the painted area matches
    the area the cell actually represents."""
    return math.sqrt(cell_area_m2(res) / math.pi)


# --- time axes ----------------------------------------------------------------


def solar_hour(hour_utc: int, minute_utc: int, lon: float, tz_offset_min: int | None):
    """Local solar hour frame, and which source produced it.

    Prefer the dataset's own timezone offset where present and non-null; fall
    back to the longitude term. Returns (hour 0..23, 'tz' | 'lon').
    """
    base = hour_utc + minute_utc / 60.0
    if tz_offset_min is not None:
        return int((base + tz_offset_min / 60.0) % 24), "tz"
    return int((base + lon / 15.0) % 24), "lon"


def frame_solar(hour: int) -> str:
    return f"{hour:02d}"


def frame_chrono(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def frame_dow(dow: int) -> str:
    """0 = Monday, matching the local-time day of week used in the rollup."""
    return str(dow)


DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Aliases, so the pyramid code can speak of levels or resolutions.
LEVELS = RESOLUTIONS
BASE_LEVEL = BASE_RES
