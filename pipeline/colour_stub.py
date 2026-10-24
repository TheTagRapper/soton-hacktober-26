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
from pipeline.cowen import EMOTIONS, blend
from pipeline.oklab import oklab_to_oklch

TAG = "stub:dev-preview"


def fake(track_key: str) -> dict:
    h = hashlib.sha256(f"stub/{track_key}".encode()).digest()
    u = [b / 255.0 for b in h]
    # Two or three dominant emotions per track, like a real profile.
    j = {name: round(u[i] * 0.25, 3) for i, name in enumerate(EMOTIONS)}
    for k in range(2 + int(u[20] * 2)):
        j[EMOTIONS[int(u[14 + k] * len(EMOTIONS)) % len(EMOTIONS)]] = round(
            0.55 + u[17 + k] * 0.45, 3
        )
    j["era"] = 1960 + int(u[13] * 60)
    j["confidence"] = 0.9
    j["note"] = ""
    return j


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
        lab = blend({name: j[name] for name in EMOTIONS})
        if lab is None:
            continue
        L, C, h = oklab_to_oklch(*lab)
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
