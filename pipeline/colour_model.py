"""The colour model: constrained local inference -> judgements -> Oklch.

Spec section 6 is LOCKED in what the model is asked for, the decoding options,
the system prompt, the user message and the cache format. One deliberate
deviation, at the operator's instruction: the runtime is **llama.cpp**
(`llama-server`) rather than Ollama. The substance of 6.2 is preserved --
local, no network, JSON-*schema*-constrained decoding (not prose parsing, not
few-shot), temperature 0.2 / top_p 0.9 / seed 20260101 / num_ctx 2048, batch
size 1 with 4-8 concurrent requests -- because llama-server's
/v1/chat/completions takes `response_format: {type: "json_schema"}` and
converts the schema to a GBNF grammar, which is the same mechanism Ollama's
`format` uses. Set COLOUR_BACKEND=ollama to use the spec's runtime verbatim.

The model does NOT output RGB (section 6.1). It returns five perceptual
judgements plus era/confidence/note, and oklab.py turns those into colour
deterministically, so the palette can be retuned without re-running inference.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb
import httpx

from pipeline.oklab import judgement_to_oklch

CACHE = Path("data/cache/track_colour.jsonl")
TRACKS = Path("data/work/tracks.parquet")

SEED = 20260101

# --- section 6.2, verbatim ---------------------------------------------------

SYSTEM_PROMPT = (
    "You are a music critic with synaesthesia. Given an artist and a song "
    "title, you judge the sound of the recording. You answer only with the "
    "requested JSON object. If you do not recognise the track, infer from the "
    "artist and from the conventions of what you can tell of the genre, and "
    "lower `confidence` accordingly. Never refuse. Never explain."
)

# --- section 6.1, LOCKED -----------------------------------------------------

JUDGEMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "valence": {"type": "number", "minimum": -1, "maximum": 1},
        "arousal": {"type": "number", "minimum": 0, "maximum": 1},
        "warmth": {"type": "number", "minimum": 0, "maximum": 1},
        "density": {"type": "number", "minimum": 0, "maximum": 1},
        "grain": {"type": "number", "minimum": 0, "maximum": 1},
        "era": {"type": "integer", "minimum": 1900, "maximum": 2025},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "note": {"type": "string", "maxLength": 80},
    },
    "required": [
        "valence", "arousal", "warmth", "density", "grain",
        "era", "confidence", "note",
    ],
    "additionalProperties": False,
}

AXES = ("valence", "arousal", "warmth", "density", "grain")
RANGES = {
    "valence": (-1.0, 1.0),
    "arousal": (0.0, 1.0),
    "warmth": (0.0, 1.0),
    "density": (0.0, 1.0),
    "grain": (0.0, 1.0),
    "confidence": (0.0, 1.0),
}

OPTIONS = {"temperature": 0.2, "top_p": 0.9, "seed": SEED, "num_ctx": 2048}


@dataclass(frozen=True)
class Track:
    track_key: str
    artist_norm: str
    title_norm: str
    n_events: int


def model_tag() -> str:
    backend = os.environ.get("COLOUR_BACKEND", "llamacpp")
    if backend == "ollama":
        return os.environ.get("OLLAMA_MODEL", "gemma3:4b")
    return os.environ.get("COLOUR_MODEL_TAG", "gemma-4-E4B-it-qat-q4_0")


def validate(j: dict) -> dict | None:
    """Reject anything the schema let through that the maths cannot use."""
    out: dict = {}
    for field, (lo, hi) in RANGES.items():
        v = j.get(field)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return None
        f = float(v)
        if f != f or f < lo - 1e-6 or f > hi + 1e-6:
            return None
        out[field] = min(hi, max(lo, f))
    era = j.get("era")
    if not isinstance(era, int) or isinstance(era, bool) or not 1900 <= era <= 2025:
        return None
    out["era"] = era
    note = j.get("note", "")
    out["note"] = (note if isinstance(note, str) else "")[:80]
    return out


# --- cache (section 6.3, LOCKED) ---------------------------------------------


def read_cache() -> dict[str, dict]:
    """track_key -> newest record. Append-only file; later records win."""
    if not CACHE.exists():
        return {}
    best: dict[str, dict] = {}
    with CACHE.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # a record truncated by a kill; the next run redoes it
            if rec.get("header"):
                continue
            key = rec.get("track_key")
            if not key:
                continue
            prev = best.get(key)
            # Prefer the newest timestamp, so a rebuild with a different model
            # string keeps both lines on disk but exports the later judgement.
            if prev is None or rec.get("ts", "") >= prev.get("ts", ""):
                best[key] = rec
    return best


def append(rec: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE.open("a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def write_header(tag: str, backend: str, extra: dict | None = None) -> None:
    append(
        {
            "header": True,
            "model": tag,
            "backend": backend,
            "options": OPTIONS,
            "system_prompt": SYSTEM_PROMPT,
            "schema_version": 1,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **(extra or {}),
        }
    )


# --- track selection (sections 5 and 6.5) ------------------------------------


def select_tracks(coverage: float, max_tracks: int) -> tuple[list[Track], int]:
    con = duckdb.connect()
    rows = con.execute(
        f"SELECT track_key, artist_norm, title_norm, n_events "
        f"FROM '{TRACKS.as_posix()}' ORDER BY n_events DESC"
    ).fetchall()
    total = sum(r[3] for r in rows)
    target = coverage * total
    picked: list[Track] = []
    acc = 0
    for key, artist, title, n in rows:
        if acc >= target or len(picked) >= max_tracks:
            break
        picked.append(Track(key, artist, title, n))
        acc += n
    print(
        f"[colour] {len(picked):,} of {len(rows):,} tracks selected, "
        f"covering {acc:,}/{total:,} events ({acc / max(1, total):.1%}); "
        f"budget: coverage<={coverage:.0%}, max_tracks={max_tracks:,}"
    )
    return picked, total


# --- backends ----------------------------------------------------------------


async def _call_llamacpp(client: httpx.AsyncClient, host: str, tag: str, t: Track) -> dict:
    r = await client.post(
        f"{host}/v1/chat/completions",
        json={
            "model": tag,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"{t.artist_norm} — {t.title_norm}"},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "judgement", "strict": True,
                                "schema": JUDGEMENT_SCHEMA},
            },
            "temperature": OPTIONS["temperature"],
            "top_p": OPTIONS["top_p"],
            "seed": OPTIONS["seed"],
            "max_tokens": 220,
        },
        timeout=180.0,
    )
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])


async def _call_ollama(client: httpx.AsyncClient, host: str, tag: str, t: Track) -> dict:
    r = await client.post(
        f"{host}/api/chat",
        json={
            "model": tag,
            "stream": False,
            "format": JUDGEMENT_SCHEMA,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"{t.artist_norm} — {t.title_norm}"},
            ],
            "options": OPTIONS,
        },
        timeout=180.0,
    )
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])


# --- runner ------------------------------------------------------------------


class Rate:
    def __init__(self, total: int) -> None:
        self.total = total
        self.done = 0
        self.failed = 0
        self.t0 = time.time()

    def tick(self, ok: bool) -> None:
        self.done += 1
        if not ok:
            self.failed += 1
        el = time.time() - self.t0
        rate = self.done / el if el else 0.0
        remaining = (self.total - self.done) / rate if rate else 0.0
        sys.stderr.write(
            f"\r[colour] {self.done:,}/{self.total:,} "
            f"({self.done / max(1, self.total):.1%}) "
            f"{rate * 60:.1f}/min  failed {self.failed}  "
            f"elapsed {el / 60:.1f}m  ETA {remaining / 60:.1f}m   "
        )
        sys.stderr.flush()


async def run(tracks: list[Track], concurrency: int) -> None:
    backend = os.environ.get("COLOUR_BACKEND", "llamacpp")
    tag = model_tag()
    host = (
        os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        if backend == "ollama"
        else os.environ.get("LLAMA_HOST", "http://127.0.0.1:8080")
    )
    call = _call_ollama if backend == "ollama" else _call_llamacpp
    print(f"[colour] backend={backend} host={host} model={tag} concurrency={concurrency}")
    write_header(tag, backend, {"host": host, "n_requested": len(tracks)})

    rate = Rate(len(tracks))
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient() as client:
        async def one(t: Track) -> None:
            async with sem:
                judgement = None
                for attempt in range(3):
                    try:
                        raw = await call(client, host, tag, t)
                        judgement = validate(raw)
                        if judgement is not None:
                            break
                    except (httpx.HTTPError, json.JSONDecodeError, KeyError):
                        pass
                    await asyncio.sleep(1.5 * (attempt + 1))
                if judgement is None:
                    async with lock:
                        rate.tick(False)
                    return
                L, C, h = judgement_to_oklch(*(judgement[a] for a in AXES))
                rec = {
                    "track_key": t.track_key,
                    "artist_norm": t.artist_norm,
                    "title_norm": t.title_norm,
                    "model": tag,
                    "judgement": judgement,
                    "L": L,
                    "C": C,
                    "h": h,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                # Flush per record, never rewrite in place: this run will be
                # killed and resumed.
                async with lock:
                    append(rec)
                    rate.tick(True)

        await asyncio.gather(*(one(t) for t in tracks))
    sys.stderr.write("\n")
    print(f"[colour] done: {rate.done - rate.failed:,} written, {rate.failed:,} failed")


def main() -> int:
    coverage = float(os.environ.get("COLOUR_EVENT_COVERAGE", "0.95"))
    max_tracks = int(os.environ.get("COLOUR_MAX_TRACKS", "2000"))
    concurrency = int(os.environ.get("COLOUR_CONCURRENCY", "6"))
    tracks, _ = select_tracks(coverage, max_tracks)
    cached = read_cache()
    tag = model_tag()
    todo = [t for t in tracks if t.track_key not in cached or cached[t.track_key].get("model") != tag]
    print(f"[colour] {len(cached):,} cached, {len(todo):,} to call")
    if not todo:
        return 0
    random.Random(SEED).shuffle(todo)  # spread popular/obscure over the run
    asyncio.run(run(todo, concurrency))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
