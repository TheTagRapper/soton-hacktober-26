"""H3 indexing and the two time axes (spec sections 7.1 and 7.3).

H3 rather than a Web-Mercator square grid because H3 cells are near-equal-area:
a Mercator square in Svalbard covers a tiny fraction of the ground area of one
on the equator, which would make "colour per area of human listening" silently
false. Each cell has exactly one deterministic parent via cell_to_parent, which
is total -- that is all the pyramid needs.

We never hand-roll H3 maths; h3-py does it.
"""

from __future__ import annotations

import h3

BASE_RES = 8
RESOLUTIONS = [8, 7, 6, 5, 4, 3, 2, 1]

GEO_ENUM = {"point": 0, "place_centroid": 1, "city": 2}


def cell_at(lat: float, lon: float, res: int = BASE_RES) -> int:
    """H3 index as a 64-bit integer (the export column type)."""
    return h3.str_to_int(h3.latlng_to_cell(lat, lon, res))


def parent_int(cell: int, res: int) -> int:
    return h3.str_to_int(h3.cell_to_parent(h3.int_to_str(cell), res))


def cell_to_hex(cell: int) -> str:
    return h3.int_to_str(cell)


def solar_hour(hour_utc: int, minute_utc: int, lon: float, tz_offset_min: int | None):
    """Local solar hour frame, and which source produced it (section 7.3).

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
    """0 = Monday, matching the local-time day-of-week used in the rollup."""
    return str(dow)


DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
