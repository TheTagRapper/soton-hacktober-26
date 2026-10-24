"""DEV PREVIEW ONLY — deterministic fake judgements, never the real artefact.

This exists so the front end can be built and verified before a multi-hour
local inference pass has finished. It writes to whatever COLOUR_CACHE points
at, which for a stub run must NOT be data/cache/track_colour.jsonl: records
here are tagged `model: "stub:dev-preview"` and must never be mistaken for or
committed alongside model output.

The judgements are a seeded hash of the track key, so the map is spatially
structured enough to debug the renderer against and obviously not a claim
about any record.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time

from pipeline.colour_model import CACHE, append, select_tracks, write_header
from pipeline.oklab import judgement_to_oklch

TAG = "stub:dev-preview"


def fake(track_key: str) -> dict:
    h = hashlib.sha256(f"stub/{track_key}".encode()).digest()
    u = [b / 255.0 for b in h[:6]]
    return {
        "valence": round(u[0] * 2 - 1, 3),
        "arousal": round(u[1], 3),
        "warmth": round(u[2], 3),
        "density": round(u[3], 3),
        "grain": round(u[4], 3),
        "era": 1960 + int(u[5] * 60),
        "confidence": 0.9,
        "note": "",
    }


def main() -> int:
    if CACHE.name == "track_colour.jsonl" and "data/cache" in CACHE.as_posix():
        print(
            "refusing to write stub judgements into the real cache; "
            "set COLOUR_CACHE to a path under data/work/",
            file=sys.stderr,
        )
        return 2
    coverage = float(os.environ.get("COLOUR_EVENT_COVERAGE", "0.95"))
    max_tracks = int(os.environ.get("COLOUR_MAX_TRACKS", "4000"))
    tracks, _ = select_tracks(coverage, max_tracks)
    write_header(TAG, "stub", {"warning": "dev preview, not model output"})
    for t in tracks:
        j = fake(t.track_key)
        L, C, h = judgement_to_oklch(j["valence"], j["arousal"], j["warmth"], j["density"], j["grain"])
        append(
            {
                "track_key": t.track_key,
                "artist_norm": t.artist_norm,
                "title_norm": t.title_norm,
                "model": TAG,
                "judgement": j,
                "L": L,
                "C": C,
                "h": h,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
    print(f"[stub] wrote {len(tracks):,} dev-preview judgements to {CACHE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
