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
and cluster on named web colours. It is asked how strongly a listener would feel
each of the **thirteen dimensions of music-evoked emotion** from

> Cowen, A. S., Fang, X., Sauter, D., & Keltner, D. (2020). *What music makes us
> feel: At least 13 dimensions organize subjective experiences associated with
> music across different cultures.* PNAS 117(4), 1924–1934.

and the song's colour is the blend of that paper's own published palette —
the colours used on [Cowen's interactive map of those
dimensions](https://www.ocf.berkeley.edu/~acowen/music.html) — weighted by those
ratings and mixed in Oklab:

| | | | |
|---|---|---|---|
| `#0000ff` amusing | `#ff0000` annoying | `#ffd300` anxious, tense | `#ffc184` beautiful |
| `#ff1ab9` calm, relaxing, serene | `#00ff00` dreamy | `#d312ff` energizing, pump-up | `#8484ff` erotic, desirous |
| `#f69edc` indignant, defiant | `#00ffc1` joyful, cheerful | `#008495` sad, depressing | `#cccc33` scary, fearful |
| `#9e4f46` triumphant, heroic | | | |

The axes are therefore a published, peer-reviewed taxonomy of what music
actually makes people feel, rather than five axes invented for this project, and
the colours are the ones that taxonomy is drawn in. `era`, `confidence` and the
model's one-line `note` are carried through: the first two are exposed as
filters, the third is shown in the hover label.

**One judgement call, stated plainly.** Several of those thirteen colours are
near-complementary — joyful is a cyan-green, calm a magenta — so a flat blend of
a song scoring moderately on many dimensions lands on grey, and averaging places
together would then grey the whole map. Ratings are raised to the power **2.5**
before blending, which lets the feelings a listener would actually name dominate
the ones that merely registered. Set `SHARPEN = 1.0` in `pipeline/cowen.py` and
`site/src/colour.ts` for the flat mean.

The palette and the blend live twice — `pipeline/cowen.py` and
`site/src/colour.ts` — because one half of the project is Python and the other is
a browser. `tests/test_oklab.py` runs the real TypeScript through Node and
asserts the two agree to **1e-9** over a 500-profile shared fixture, and that a
song rated purely one emotion comes out exactly that palette colour, so the
shards and the renderer cannot drift apart.

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

## The grid, and why it is drawn as circles

Aggregation is on **H3 hexagons**, resolutions 8 to 1. A Web-Mercator square
grid would make a cell in Svalbard represent a tiny fraction of the ground area
of one on the equator, so "colour per area of human listening" would be silently
false. H3 cells are near-equal-area — measured over a uniform sample from 85°S to
85°N the largest cell at a resolution is about **2.3×** the smallest, against
roughly 130× for Mercator squares over the same span. `tests/test_rollup.py`
pins that figure down rather than letting "near-equal-area" go unexamined.

Each cell is **drawn as a circle of the same area as its hexagon**, with a soft
radial falloff and a radius 1.45× the equal-area circle, so neighbouring cells
overlap and the picture reads as a continuous field of mood rather than a
honeycomb. The painted area still matches the area the cell represents;
`test_drawn_circle_matches_cell_area` asserts that at every resolution.

The circles are interleaved into the basemap **beneath** its boundary, road and
label layers, so country, state and city lines and place names stay legible on
top of the colour at every zoom.

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
2. **The runtime is llama.cpp, not Ollama**, by operator instruction; see *The
   colour model* above for how each locked requirement is still met.
3. **The colour axes are Cowen et al.'s thirteen emotion dimensions**, not the
   five perceptual axes the specification locked, by operator instruction — and
   the palette is that paper's own. Everything the specification locked *about*
   colour still holds: the model never emits RGB, the judgement-to-colour step is
   deterministic and auditable in one place, and all averaging is in Oklab.
4. **Cells are drawn as overlapping soft circles**, not as filled hexagons, by
   operator instruction. The aggregation grid is unchanged; only the mark is.
   deck.gl's `ScatterplotLayer` is subclassed to add the radial falloff, so
   `H3HexagonLayer` and its missing-in-9.4 `stitching` prop are no longer used.

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
