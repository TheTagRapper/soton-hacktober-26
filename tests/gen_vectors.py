"""Generate the shared Oklab test fixture (spec section 14.2).

500 points: random judgements plus the extremes of every axis, so the gamut
clamp and the hue wraparound are both exercised.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from pipeline.oklab import judgement_to_oklab, judgement_to_oklch, oklab_to_hex

OUT = Path(__file__).parent / "fixtures" / "oklab_vectors.json"


def build(n: int = 500, seed: int = 20260101) -> list[dict]:
    rng = random.Random(seed)
    cases: list[tuple[float, ...]] = []
    for v in (-1.0, 0.0, 1.0):
        for x in (0.0, 1.0):
            cases.append((v, x, x, x, x))
    cases.append((-1.0, 1.0, 0.0, 1.0, 1.0))
    cases.append((1.0, 1.0, 1.0, 1.0, 0.0))
    while len(cases) < n:
        cases.append(
            (
                rng.uniform(-1, 1),
                rng.random(),
                rng.random(),
                rng.random(),
                rng.random(),
            )
        )
    out = []
    for valence, arousal, warmth, density, grain in cases[:n]:
        L, C, h = judgement_to_oklch(valence, arousal, warmth, density, grain)
        lab = judgement_to_oklab(valence, arousal, warmth, density, grain)
        out.append(
            {
                "in": {
                    "valence": valence,
                    "arousal": arousal,
                    "warmth": warmth,
                    "density": density,
                    "grain": grain,
                },
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
