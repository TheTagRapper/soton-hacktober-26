"""The Python and TypeScript colour implementations must agree.

Two ports of the same palette and the same blend is the obvious place for this
project to drift: the pipeline would bake one set of colours into the shards
and the browser would render another, and nothing would look obviously broken.
This test is the guard. It runs the real site/src/colour.ts through Node and
compares to 1e-9 over a 500-profile shared fixture.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.gen_vectors import OUT as VECTORS
from tests.gen_vectors import build

TOL = 1e-9
ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "ts_oklab_harness.mjs"


@pytest.fixture(scope="module")
def vectors() -> list[dict]:
    VECTORS.parent.mkdir(parents=True, exist_ok=True)
    data = build()
    VECTORS.write_text(json.dumps(data, indent=1))
    return data


def test_fixture_is_well_formed(vectors: list[dict]) -> None:
    assert len(vectors) == 500
    hues = {round(v["oklch"]["h"], 6) for v in vectors}
    assert len(hues) > 100, "fixture should sweep the hue circle, not cluster"


def test_single_emotion_reproduces_its_palette_colour() -> None:
    """A song that is purely one emotion must come out exactly that colour."""
    from pipeline.cowen import EMOTIONS, PALETTE_SRGB, blend
    from pipeline.oklab import oklab_to_hex

    for name in EMOTIONS:
        lab = blend({k: (1.0 if k == name else 0.0) for k in EMOTIONS})
        assert lab is not None
        assert oklab_to_hex(*lab) == PALETTE_SRGB[name][0]


def test_python_and_typescript_agree(vectors: list[dict]) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    proc = subprocess.run(
        [node, "--experimental-strip-types", str(HARNESS)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if proc.returncode != 0:
        pytest.fail(f"TS harness failed:\n{proc.stderr[-4000:]}")
    ts = json.loads(proc.stdout)
    assert len(ts) == len(vectors)
    for i, (py, t) in enumerate(zip(vectors, ts)):
        for field in ("L", "C", "h"):
            assert abs(py["oklch"][field] - t["oklch"][field]) < TOL, (
                f"vector {i}: oklch.{field} {py['oklch'][field]} != {t['oklch'][field]}"
            )
        for field in ("L", "a", "b"):
            assert abs(py["oklab"][field] - t["oklab"][field]) < TOL, (
                f"vector {i}: oklab.{field} {py['oklab'][field]} != {t['oklab'][field]}"
            )
        assert py["hex"] == t["hex"], f"vector {i}: hex {py['hex']} != {t['hex']}"


def test_gamut_clamp_holds_hue() -> None:
    from pipeline.oklab import clamp_chroma, in_gamut, oklab_to_oklch

    # A deliberately out-of-gamut, very saturated light colour.
    L, a, b = 0.72, 0.30, -0.22
    assert not in_gamut(L, a, b)
    L2, a2, b2 = clamp_chroma(L, a, b)
    assert in_gamut(L2, a2, b2)
    assert L2 == L
    _, c_in, h_in = oklab_to_oklch(L, a, b)
    _, c_out, h_out = oklab_to_oklch(L2, a2, b2)
    assert c_out < c_in
    assert abs(h_out - h_in) < 1e-9, "clamping chroma must not shift hue"
