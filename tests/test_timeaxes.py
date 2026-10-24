"""The time axes must not depend on the build machine's timezone.

DuckDB returns TIMESTAMP values in the session timezone. Left unpinned, a
developer in Europe/London builds a solar axis an hour out from a developer in
UTC for every summer event -- and the solar axis is the view the whole piece
exists for. This test fails if that pinning is ever removed.
"""

from __future__ import annotations

import datetime
import os

import duckdb
import pytest

from pipeline import rollup, spatial


def test_connection_is_pinned_to_utc(tmp_path):
    con = rollup.connect(tmp_path / "tz.duckdb")
    assert con.execute("SELECT current_setting('TimeZone')").fetchone()[0] == "UTC"


def test_timestamps_come_back_as_utc_whatever_the_machine(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "Pacific/Auckland")  # UTC+12/+13, as far off as it gets
    con = rollup.connect(tmp_path / "tz2.duckdb")
    ts = con.execute("SELECT TIMESTAMP '2012-07-31 09:44:10'").fetchone()[0]
    as_utc = ts.astimezone(datetime.UTC) if ts.tzinfo else ts.replace(tzinfo=datetime.UTC)
    assert (as_utc.hour, as_utc.minute) == (9, 44)


@pytest.mark.parametrize(
    "hour_utc,minute,lon,tz_min,expected,source",
    [
        # The dataset's own offset wins where it is present.
        (12, 0, -0.12, 0, 12, "tz"),
        (12, 0, -74.0, -300, 7, "tz"),
        (23, 30, 139.7, 540, 8, "tz"),       # Tokyo, next day local
        # Longitude fallback where it is not.
        (12, 0, -74.0, None, 7, "lon"),
        (0, 0, 180.0, None, 12, "lon"),
        (0, 0, -180.0, None, 12, "lon"),     # wraps to the same hour
    ],
)
def test_solar_hour(hour_utc, minute, lon, tz_min, expected, source):
    hour, src = spatial.solar_hour(hour_utc, minute, lon, tz_min)
    assert (hour, src) == (expected, source)


def test_solar_hour_is_always_in_range():
    for h in range(24):
        for lon in (-180, -90, 0, 90, 180):
            for tz in (None, -720, 0, 840):
                hour, _ = spatial.solar_hour(h, 59, lon, tz)
                assert 0 <= hour <= 23
