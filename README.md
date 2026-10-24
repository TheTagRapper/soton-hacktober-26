# Chromatic Listening

A world map whose colour is the music being played on it.

Over a near-monochrome basemap sits a hexagonal grid. Each cell is filled with
one colour, and that colour is the **count-weighted perceptual average, in
Oklab, of the colours assigned — by a language model running offline, in
advance — to every song that was actually being listened to inside that cell**
during the selected window of time. A scrubber moves the window; the planet
changes colour. Zooming in subdivides the grid; zooming out merges cells and
averages their colours, which is a true statement rather than a visual trick,
for the reason given under *Why the zoom is honest* below.

Everyone who opens the URL sees the same data and the same colours. Camera,
time window, filters and view mode are per-visitor and live only in the URL
hash, so any view is shareable by copying the address bar.

It is an art piece with a defensible data pipeline, not a dashboard.

## Quick start

```bash
make setup        # venv + pip install -e '.[dev]'; npm ci in site/
make fetch        # MMTD, ~128 MB, into data/raw/ (gitignored)
make schema       # dump the real raw schemas for inspection
make normalise    # -> data/work/events.parquet, tracks.parquet
make serve-model  # start llama-server with the judgement schema
make colour       # resumable local inference -> data/cache/track_colour.jsonl
make rollup export audit   # H3 pyramid -> Arrow shards + manifest + report
make dev          # vite dev server on :5173
```

`make all` chains the lot. `make test` runs the two tests that matter.

## How it is built

```
OFFLINE (Python, run on the dev machine, output committed to git)

  MMTD / #nowplaying-RS --> normalise --> unique-track table
       |                                        |
       |                            Gemma 4 via llama.cpp, local
       |                                        |
       |                              track_colour: key -> Oklch
       v                                        v
  events(lat, lon, ts, track_key) --join--> coloured events
                                               |
                                      H3 index at res 8
                                               |
                         pyramid rollup res 8 -> 1, per time frame
                              (count-weighted mean in Oklab)
                                               |
                         site/public/data/<version>/**.arrow + manifest.json
                                               |
ONLINE (static site, TypeScript)               v
  MapLibre GL JS + OpenFreeMap positron basemap
  deck.gl H3HexagonLayer, resolution chosen from map zoom
  fetch only the (axis, res, frame) shards the viewport needs
  Oklab -> sRGB in the client; opacity from cell count
```

No backend, no database at request time, no serverless function. The deployed
artefact is static files. No model is ever called from the browser.

## How a song becomes a colour

The model is **not** asked for a colour. Language models choose bad RGB triples
and cluster on named web colours. It is asked for eight judgements about how the
recording *sounds* — `valence`, `arousal`, `warmth`, `density`, `grain`, the
decade it sounds like, its own `confidence`, and one note of 80 characters or
fewer — and deterministic code converts those to Oklch:

```
hue        h = (210 - 150 * valence) mod 360
chroma     C = 0.02 + 0.13 * (0.65*arousal + 0.35*density)
lightness  L = 0.38 + 0.34 * warmth - 0.10 * grain
```

This keeps the mapping auditable, keeps averaging meaningful, and means the
palette can be retuned without re-running hours of inference. `era` and
`confidence` are carried through and exposed as filters but do not affect
colour in v1.

The formula lives twice — `pipeline/oklab.py` and `site/src/colour.ts` — because
one half of the project is Python and the other is a browser.
`tests/test_oklab.py` runs the real TypeScript through Node and asserts the two
agree to **1e-9** over a 500-point shared fixture, so the shards and the
renderer cannot drift apart.

### Why the zoom is honest

All averaging happens on `(L, a, b)` in Oklab, never on sRGB and never on hue
angles. Averaging hex colours or hue degrees is the classic failure here: it
produces grey mud and hue-wraparound artefacts. Oklab is a Euclidean
perceptual space, so a count-weighted component-wise mean is the correct
operation — **and it is associative**.

That associativity is the entire basis of the zoom behaviour. The pyramid
builds res 7 from res 8, res 6 from res 7, and so on, never going back to the
events. `tests/test_rollup.py` asserts on a synthetic fixture that the res-5
colour computed through 8 → 7 → 6 → 5 equals the res-5 colour computed directly
from the events, to 1e-9, at res 7, 6 and 5. If that test fails, the zoom ladder
is lying and nothing else in the project matters.

## The colour model

**This build uses llama.cpp rather than Ollama**, at the operator's instruction.
The substance of the specification's requirements is preserved: inference is
local, offline, run once, and **JSON-schema-constrained** —
`llama-server`'s `/v1/chat/completions` takes
`response_format: {type: "json_schema"}` and compiles the schema to a GBNF
grammar, which is the same mechanism Ollama's `format` field uses. No prose is
parsed and no few-shot examples stand in for a schema. Decoding is pinned at
`temperature 0.2`, `top_p 0.9`, `seed 20260101`, `num_ctx 2048`, batch size 1,
with 4–8 concurrent requests.

Set `COLOUR_BACKEND=ollama` in `.env` to use Ollama instead; the request shape
for it is implemented and kept in step.

The exact model tag is recorded in a header record in the cache and in
`manifest.json` — the colour table is only reproducible if the model is
identified.

### The cache is the artefact

`data/cache/track_colour.jsonl` is **committed**. It is append-only, flushed
and fsynced after every record, never rewritten in place, and the runner reads
it first and only calls the model for missing keys — so a pass that takes hours
can be killed and resumed freely, and `make colour` prints a live rate and ETA.
It contains no user data, no coordinates and no listening timestamps: only
opinions about recordings. It is hours of local inference, and it is what makes
this piece reproducible by someone without a GPU.

A dev-preview generator (`pipeline/colour_stub.py`) exists so the front end can
be built before a long inference pass finishes. It refuses to write into the
real cache, tags every record `stub:dev-preview`, and its output is gitignored.

### The long tail

Tracks below the event-count cutoff, or with `confidence < 0.25`, get no model
call. They take the **artist's** mean colour if that artist has 3 or more
coloured tracks; otherwise they are **excluded from aggregation** and counted in
a figure the UI surfaces. Excluded is better than guessed.

### Control condition, and the one judgement call in it

`pipeline/colour_baseline.py` builds a second colour table from
#nowplaying-RS's own audio features through the same axis mapping, so the UI can
offer **model / features / difference**. The difference view fills each cell
with a single-hue ramp on Oklab ΔE between the two fields: it shows where a
language model's impression of music diverges from its measured acoustics. The
model is never shown those features — the comparison depends on the two being
independent.

The mapping is `valence <- 2*valence - 1`, `arousal <- energy`,
`warmth <- acousticness`, `density <- clamp((loudness + 60)/60)`. There is **no
clean equivalent of `grain`** in that feature set, which is the honest weak
point of the control condition; this build uses
`clamp(0.5*speechiness + 0.5*(1 - instrumentalness)*(1 - mode))` — speech-like
and vocal-led, non-major-mode recordings read as grainier. It is defensible, not
canonical, and it is stated here rather than dressed up.

When the #nowplaying-RS archive is absent at build time, `manifest.json` carries
`has_baseline: false`, the features and difference controls are **disabled**
rather than silently showing the model field twice, and the About panel says so.

## What is deliberately left out

- **Nothing is shown unless `n >= 5` plays by `>= 3` distinct listeners** in that
  cell and frame, and no single listener contributes more than 20 plays to any
  one cell-frame (random subsample, fixed seed). Privacy, statistics and
  aesthetics at once. These thresholds are never quietly lowered to make the map
  look fuller; `reports/audit.md` reports what each rule removed.
- **Coordinates are never synthesised.** An event that arrives with only a
  country is dropped — not jittered, not sampled from a population raster, not
  "distributed within the country". This is the single most important data rule
  in the project.
- `n_users` summed up the pyramid over-counts a listener present in several
  child cells. It is named `n_users_ub`, documented as an upper bound, and the
  hover tooltip says so.
- **No DuckDB-WASM in the browser.** After suppression the whole output is
  small; pre-sharded Arrow files fetched directly are simpler, CDN-cacheable
  across all visitors, and avoid the bug class where duckdb-wasm silently
  downloads whole files instead of issuing range requests.
- **No synchronised time between visitors.** "The same map for everyone,
  different views locally" is satisfied by shared immutable data plus local
  camera state. `state.ts` is structured so an external frame-index source could
  drive it (`Store.setFrame`), but that needs a backend and is out of scope.

## Runtime dependencies, and why each one is there

MapLibre GL JS (basemap and camera), deck.gl core/geo-layers/mapbox
(`H3HexagonLayer`, interleaved so hexes sit under the basemap's labels), h3-js
(cell-to-polygon and parent/child re-aggregation), apache-arrow (shard
decoding — a few hundred KB of JS rather than several MB of WASM). No UI
framework: the interface is a handful of plain-DOM controls and React would earn
nothing here.

## Deployment

Cloudflare Pages, free tier. Build command `npm run build` in `site/`, output
directory `site/dist`. Data ships inside the deployment as committed files under
`site/public/data/<version>/`, and `site/public/_headers` marks that tree
`immutable` — which is safe precisely because the version segment changes when
the data does.

Pages' limits are respected by the exporter, which fails the build rather than
exceeding them: **25 MB per file**, **20,000 files per deployment**. The current
plan is `(24 + 18 + 7) axes-frames x 8 resolutions x 2 colour sources ≈ 784`
files.

If the data tree ever outgrows that, move it to Cloudflare R2 (10 GB free, zero
egress) and change `DATA_BASE_ROOT` in `site/src/main.ts` — or set
`VITE_DATA_BASE`. It is one constant, in one place, deliberately.

## Known deviations from the specification

The specification this was built from is explicit that where it is wrong about
the real data, the real data wins. Three places where it was:

1. **MMTD is one denormalised 936 MB TSV with 291 columns**, not the separate
   id-joined event/track/artist/place tables the spec predicted. See
   `DATA_LICENCES.md` for the schema actually found and used.
2. **deck.gl 9.4's `H3HexagonLayer` has no `stitching` prop** — the
   low-precision path it belonged to was removed upstream. `highPrecision:
   'auto'` covers the case it existed for.
3. **The runtime is llama.cpp, not Ollama**, by operator instruction; see *The
   colour model* above for how each locked requirement is still met.

Additionally, `uv` and the DuckDB `h3` community extension were unavailable
here. The project uses a stdlib venv with pip against the same
`pyproject.toml`, and takes the specification's own documented fallback for H3:
index with `h3-py` and join a parent-mapping table, with every group-by still
in DuckDB SQL.

## Honestly

Twitter-sourced listening data from 2011–2013 is biased towards young, urban,
anglophone, smartphone-owning people who posted what they were playing. This map
is a portrait of a dataset, not a portrait of what the world listens to. Empty
regions are mostly the absence of that behaviour, not the absence of music. The
piece is more interesting for admitting it.

## Credits

- **MMTD** — Hauger & Schedl, ISMIR 2013, Johannes Kepler University Linz.
- **#nowplaying-RS** — Poddar, Zangerle & Yang, SMC 2018, CC BY 4.0.
- Basemap © OpenFreeMap and OpenStreetMap contributors (ODbL).

Full provenance, licences and counts: `DATA_LICENCES.md`.
