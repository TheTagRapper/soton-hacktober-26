"""Generate the shared colour test fixture.

500 emotion profiles: the thirteen Cowen dimensions, including every
single-emotion extreme (which must reproduce that palette colour exactly) and
flat profiles, so the Oklab blend, the gamut clamp and the sRGB conversion are
all exercised on both sides of the Python/TypeScript boundary.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from pipeline.cowen import EMOTIONS, blend
from pipeline.oklab import oklab_to_hex, oklab_to_oklch

OUT = Path(__file__).parent / "fixtures" / "oklab_vectors.json"


def build(n: int = 500, seed: int = 20260101) -> list[dict]:
    rng = random.Random(seed)
    cases: list[dict[str, float]] = []
    # Each emotion alone: the blend must reproduce the palette colour exactly.
    for name in EMOTIONS:
        cases.append({k: (1.0 if k == name else 0.0) for k in EMOTIONS})
    # Flat profiles, where near-complementary palette entries cancel.
    for v in (0.2, 0.5, 1.0):
        cases.append({k: v for k in EMOTIONS})
    # Sparse two- and three-emotion profiles, the common real shape.
    while len(cases) < n * 2:
        prof = {k: 0.0 for k in EMOTIONS}
        for _ in range(rng.randint(1, 4)):
            prof[EMOTIONS[rng.randrange(len(EMOTIONS))]] = rng.random()
        if rng.random() < 0.5:
            for k in EMOTIONS:
                prof[k] = max(prof[k], rng.random() * 0.3)
        cases.append(prof)
    out = []
    for prof in cases:
        if len(out) >= n:
            break
        lab = blend(prof)
        if lab is None:
            continue  # no emotional signal: excluded, not guessed
        L, C, h = oklab_to_oklch(*lab)
        out.append(
            {
                "in": prof,
                "oklch": {"L": L, "C": C, "h": h},
                "oklab": {"L": lab[0], "a": lab[1], "b": lab[2]},
                "hex": oklab_to_hex(*lab),
            }
        )
    return out


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=1))
    print(f"wrote {OUT}")
