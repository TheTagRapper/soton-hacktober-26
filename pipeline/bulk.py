"""Bulk-insert helper.

DuckDB's executemany appends row at a time; registering a PyArrow table and
doing CREATE TABLE AS SELECT is two orders of magnitude faster at the sizes
this pipeline deals in (10^5 to 10^6 rows). Same result, same types.
"""

from __future__ import annotations

import duckdb
import pyarrow as pa

_counter = 0


def create_table(
    con: duckdb.DuckDBPyConnection,
    name: str,
    columns: dict[str, pa.DataType],
    rows: list[tuple],
) -> None:
    global _counter
    _counter += 1
    view = f"_bulk_{_counter}"
    cols = list(columns)
    data = list(zip(*rows)) if rows else [()] * len(cols)
    table = pa.table(
        {c: pa.array(list(data[i]), type=columns[c]) for i, c in enumerate(cols)}
    )
    con.register(view, table)
    con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM {view}")
    con.unregister(view)
