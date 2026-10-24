# Build: "Chromatic Listening" — a world map whose colour is the music being played on it

You are implementing a complete, working proof of concept from this specification.
Read the whole document before writing any code. Where this document gives an
exact value, URL, table or formula, use it verbatim rather than substituting your
own choice. Where it marks a decision as **LOCKED**, do not revisit it.

---

## 0. What the thing is

A single static web page showing a world map. Over the map sits a hexagonal grid.
Each cell is filled with one colour. That colour is the count-weighted perceptual
average of the colours assigned — by a local language model, offline, in advance —
to every song that was actually being listened to inside that cell during the
currently selected time window.

A time scrubber moves the window. As it moves, the planet changes colour: regions
drift warm and bright in the evening, cold and sparse at 04:00, a whole continent
shifts hue when a genre peaks there.

Zooming in subdivides the grid and reveals detail. Zooming out merges cells and
averages their colours. Everyone who opens the URL sees the same data and the same
colours; camera position, time window, filters and view mode are per-visitor and
live only in the URL hash.

This is an art piece with a defensible data pipeline, not a dashboard. Prioritise
in this order: correctness of the aggregation, honesty of what the data can
support, visual quality, then features.

---

## 1. Hard constraints

- **Zero cost.** No paid API, no paid hosting, no credit card, no API key that
  requires a billing relationship.
- **No backend.** No server process, no database server at request time, no
  serverless function. The deployed artefact is static files only.
- **No live data.** All listening data is historical, from public research
  datasets. The time axis is dataset time, not wall-clock time.
- **The model runs offline, once.** The language model is never called from the
  browser and never called per request. It is called during the build, once per
  unique track, and its output is committed as a lookup table.
- **Everything reproducible.** `make all` from a clean checkout must rebuild every
  derived artefact from the raw downloads.

---

## 2. Architecture

Two halves with one interface between them.

```
OFFLINE (Python, run on the dev machine, output committed to git)

  raw datasets --> normalise --> unique-track table
       |                              |
       |                              v
       |                    Gemma (Ollama, local)
       |                              |
       |                              v
       |                   track_colour: key -> Oklch
       |                              |
       v                              v
  events(lat, lon, ts, track_key) --join--> coloured events
                                              |
                                              v
                                    H3 index at res 8
                                              |
                                              v
                       pyramid rollup res 8 -> 1, per time frame
                       (count-weighted mean in Oklab)
                                              |
                                              v
                       site/public/data/**.arrow + manifest.json
                                              |
ONLINE (static site, TypeScript)              v
  MapLibre GL JS  +  OpenFreeMap basemap
  deck.gl H3HexagonLayer, resolution chosen from map zoom
  fetch only the (axis, res, frame) shards the viewport needs
  Oklab -> sRGB in the client; opacity from cell count
```

The interface between halves is the file layout in section 9 plus `manifest.json`.
Build the offline half first and verify its outputs against section 14 before
writing any front-end code.

---

## 3. Repository layout

```
chromatic-listening/
  Makefile                  # the only entry point a human needs
  pyproject.toml            # uv-managed; Python 3.12
  .env.example              # OLLAMA_HOST, OLLAMA_MODEL
  README.md
  DATA_LICENCES.md          # filled in per section 4.5 — mandatory
  pipeline/
    __init__.py
    fetch.py                # download + checksum raw archives
    ingest_mmtd.py          # adapter: MMTD          -> events table
    ingest_npr.py           # adapter: nowplaying-RS -> events table
    normalise.py            # union adapters, clean, track_key
    colour_model.py         # Ollama calls, cache, retries, validation
    colour_baseline.py      # non-LLM colour from audio features (control)
    oklab.py                # colour space maths, shared with TS via tests
    spatial.py              # H3 indexing
    rollup.py               # DuckDB pyramid build
    export.py               # arrow shards + manifest.json
    audit.py                # coverage + sanity report
  data/
    raw/                    # gitignored; downloads land here
    work/                   # gitignored; intermediate parquet
    cache/
      track_colour.jsonl    # COMMITTED. the model's output. append-only.
  site/
    index.html
    src/
      main.ts
      map.ts                # MapLibre + OpenFreeMap setup
      grid.ts               # deck.gl layer, zoom->res, shard loading
      colour.ts             # Oklab -> sRGB, gamut clamp (port of oklab.py)
      time.ts               # scrubber, playback, frame model
      state.ts              # URL-hash-backed view state
      ui.ts                 # legend, panels, counts, about
      shards.ts             # fetch + Arrow decode + LRU cache
    public/
      data/                 # COMMITTED build output (see section 9)
    package.json            # vite + typescript
    vite.config.ts
  tests/
    test_oklab.py
    test_rollup.py
    fixtures/
```

---

## 4. Data

### 4.1 Primary dataset — MMTD (build on this first)

**Million Musical Tweets Dataset**, Hauger & Schedl, ISMIR 2013. Johannes Kepler
University, Linz. Geolocated listening events harvested from music-related
hashtags (`#nowplaying`, `#itunes`) between **2011-11-09 and 2013-04-30**.
Approximately 1,100,000 events, 215,000 users, 134,000 unique songs, 25,000
artists. Each event carries latitude, longitude, timestamp, timezone, city,
county, state, country, plus artist and track names.

- Archive: `https://www.cp.jku.at/datasets/MMTD/mmtd.zip` (approx. 128 MB)
- **The directory index page returns HTTP 403 to non-browser clients; the `.zip`
  itself returns 200/206 normally.** Do not treat the 403 as "dataset gone". Fetch
  with an ordinary browser User-Agent string. Verified working at time of writing:

  ```
  curl -L -A "Mozilla/5.0 (X11; Linux x86_64)" \
    -o data/raw/mmtd.zip https://www.cp.jku.at/datasets/MMTD/mmtd.zip
  ```

- The archive contains several tab-separated files with a header row. **Do not
  hardcode the schema from this document.** Unzip, enumerate the files, print the
  header row and first three data rows of each to `data/work/mmtd_schema.txt`, and
  write the adapter against what is actually there. Expect separate tables for
  listening events, tracks, artists and places, joined by integer ids.
- Record the real schema you found in `DATA_LICENCES.md`.

**Why this one first:** 128 MB, simple TSVs, real coordinates, an 18-month span.
The whole pipeline can be exercised in minutes.

### 4.2 Secondary dataset — #nowplaying-RS (swap in once the pipeline works)

**#nowplaying-RS**, Poddar, Zangerle & Yang, SMC 2018. Zenodo record 3248543.
**CC BY 4.0** — the cleaner licence of the two. 11.6M listening events, 139K
users, 346K tracks, Twitter-sourced.

- `https://zenodo.org/records/3248543/files/nowplayingrs.zip?download=1` (1.3 GB)
- A plain `HEAD` returns 403; a `GET`, including a ranged one, returns 200/206.
  Use `curl -L` with a browser User-Agent.
- Files inside: `user_track_hashtag_timestamp.csv`, `context_content_features.csv`,
  `sentiment_values.csv`.
- `context_content_features.csv` is the one that matters. Per event it carries
  `id, user_id, track_id, artist_id, created_at`, geographic `coordinates` /
  `place` / `geo` as **geoJSON strings**, `tweet_language`, `user_lang`,
  `time_zone`, tweet `entities`, and **per-track audio features**:
  `instrumentalness, liveness, speechiness, danceability, valence, loudness,
  tempo, acousticness, energy, mode, key`.
- Those audio features are the control condition for section 6.4. They are a
  frozen 2018 snapshot of a feature set that is no longer publicly obtainable —
  treat them as irreplaceable and do not try to re-derive them from a live API.
- **Only a minority of events have precise `coordinates`.** Many have only
  `place`, a bounding box, or nothing. Handle this explicitly per section 4.4.

### 4.3 Optional enrichment — AcousticBrainz

Frozen June 2022 dump, approximately 7.5M recordings keyed by MusicBrainz
recording MBID, with high-level model outputs (mood happy/sad/aggressive/relaxed/
party, danceability, genre, timbre) and low-level descriptors (BPM, key, MFCCs).
The submission API is permanently offline; **the dumps remain downloadable** from
`https://acousticbrainz.org/download`. CC0.

Use only if you want a second independent feature source for the control
condition. It requires MBID resolution, which MMTD partially provides and
nowplaying-RS does not. **Stretch goal; do not block on it.**

### 4.4 Normalised events schema — LOCKED

Every adapter must emit exactly this, as Parquet, to `data/work/events.parquet`:

| column | type | notes |
|---|---|---|
| `event_id` | `VARCHAR` | `"{source}:{original_id}"` |
| `source` | `VARCHAR` | `'mmtd'` or `'npr'` |
| `ts_utc` | `TIMESTAMP` | UTC, parsed, never a string |
| `lat` | `DOUBLE` | WGS84, -90..90 |
| `lon` | `DOUBLE` | WGS84, -180..180 |
| `geo_precision` | `VARCHAR` | `'point'` / `'place_centroid'` / `'city'` |
| `tz_offset_min` | `INTEGER` | nullable; from the dataset where present |
| `artist_raw` | `VARCHAR` | |
| `title_raw` | `VARCHAR` | |
| `track_key` | `VARCHAR` | per section 5 |
| `user_hash` | `VARCHAR` | salted SHA-256 of the source user id, 16 hex chars |

Rules:

- Drop any event failing lat/lon range validation, or sitting at exactly `(0,0)`.
- `geo_precision` is **not cosmetic**. It is carried through to the aggregates and
  exposed in the UI. A `place_centroid` event is a bounding-box centre and will
  pile a whole city onto one hex; the viewer must be able to see and exclude
  those. Default the front-end filter to `point` only.
- **Never invent coordinates.** If a source gives only a country, that event is
  dropped, not scattered. This is the single most important data rule in the
  project. Do not add jitter, do not sample from a population raster, do not
  "distribute within the country". If you believe the dataset is too sparse
  without it, stop and report that rather than synthesising geography.
- `user_hash` exists so you can cap one user's influence (section 7.5). The salt
  is a constant in the repo; this is not a security measure, it is a
  de-identification measure.

### 4.5 `DATA_LICENCES.md` — mandatory deliverable

One section per dataset: full citation, source URL, licence, date retrieved, what
was derived from it, and whether raw data is redistributed. **Raw events are never
committed or deployed.** Only derived aggregates with n >= 5 per cell-frame ship.
Credit both papers in the site's About panel with links.

---

## 5. Track identity

`track_key` is the join key between events and model colours. LOCKED algorithm:

1. Lowercase both fields; NFKD-normalise; strip combining marks.
2. From the title, remove trailing parenthesised or bracketed segments matching
   `(remaster|remastered|deluxe|mono|stereo|\d{4} ?version|radio edit|single version|album version|explicit|bonus track?)`.
3. Strip a leading `the `.
4. Collapse `&` to `and`, remove all characters outside `[a-z0-9 ]`, collapse runs
   of whitespace, trim.
5. For multi-artist strings, keep only the segment before the first of
   ` feat. `, ` feat `, ` ft. `, ` ft `, ` featuring `, ` with `, ` x `, ` vs `.
6. `track_key = sha1(f"{artist_norm} {title_norm}").hexdigest()[:16]`

Also store `artist_norm` and `title_norm` on the unique-track table, so the model
prompt uses clean text and so a human can audit the table.

Write `data/work/tracks.parquet`: one row per `track_key` with `artist_norm`,
`title_norm`, `n_events`, and — when the source has them — the mean audio
features. Sort descending by `n_events`.

**Expected scale:** MMTD yields on the order of 10^5 unique tracks, with a
brutally long tail. Colour the tracks covering the top 95% of events first; the
tail is handled by section 6.5.

---

## 6. The colour model

### 6.1 What the model is asked to do — LOCKED

The model does **not** output RGB. Language models choose bad RGB triples and
cluster on named web colours. It outputs a small set of perceptual judgements
about the music, and deterministic code in `oklab.py` converts those to colour.
This keeps the mapping auditable, keeps averaging meaningful, and means the
palette can be retuned without re-running the model.

Per track, the model returns strictly this JSON:

```json
{
  "valence": 0.0,
  "arousal": 0.0,
  "warmth": 0.0,
  "density": 0.0,
  "grain": 0.0,
  "era": 1975,
  "confidence": 0.0,
  "note": ""
}
```

- `valence` -1..1 — bleak to elated
- `arousal` 0..1 — still to frantic
- `warmth` 0..1 — cold/metallic/digital to warm/wooden/analogue
- `density` 0..1 — sparse to wall of sound
- `grain` 0..1 — clean/polished to distorted/lo-fi/noisy
- `era` integer year 1900..2025: the decade the record *sounds* like, not its
  release year
- `confidence` 0..1 — the model's own certainty that it recognises this track
- `note` 80 characters or fewer, free text. This is for the artwork, not the
  maths. Keep it; a random sample is displayed in the UI.

### 6.2 Invocation — LOCKED

- Runtime: **Ollama**, local, `http://localhost:11434`.
- Model: read from `OLLAMA_MODEL`, defaulting to the local Gemma tag the developer
  has pulled. Target the 4B class for throughput; a 12B at 4-bit also works but
  roughly quarters the rate. Record the exact tag and quantisation in the output
  file's header record — the colour table is only reproducible if the model is
  identified.
- Call `/api/chat` with `"format"` set to a **JSON schema** matching section 6.1
  so the runtime constrains decoding. Do not parse free prose. Do not use few-shot
  examples in place of a schema.
- `"options": { "temperature": 0.2, "top_p": 0.9, "seed": 20260101, "num_ctx": 2048 }`.
  Low temperature and a fixed seed, because this table must be stable across runs;
  a track must not change colour because you rebuilt.
- Batch size 1 per request, but run **4 to 8 concurrent requests**
  (`asyncio` + `httpx`); Ollama parallelises adequately and this is the entire
  runtime budget of the build.
- System prompt, verbatim:

  > You are a music critic with synaesthesia. Given an artist and a song title,
  > you judge the sound of the recording. You answer only with the requested JSON
  > object. If you do not recognise the track, infer from the artist and from the
  > conventions of what you can tell of the genre, and lower `confidence`
  > accordingly. Never refuse. Never explain.

- User message: `f"{artist_norm} — {title_norm}"`. Nothing else. No audio
  features, no genre tags, no play counts. **The model must not be fed the
  control-condition features** — section 6.4 depends on the two being
  independent.

### 6.3 Cache — LOCKED

`data/cache/track_colour.jsonl`, one JSON object per line, append-only, committed
to git:

```json
{"track_key":"...","artist_norm":"...","title_norm":"...","model":"gemma...","judgement":{},"L":0.0,"C":0.0,"h":0.0,"ts":"2026-..."}
```

The runner reads the cache first and only calls the model for missing keys. It
must be safely interruptible: flush after every completed record, never rewrite
the file in place. A full MMTD pass is hours of wall-clock time; it will be killed
and resumed. If a record exists with a different `model` string, keep both and let
export prefer the newest. **This file is the single most valuable artefact in the
repository** — it is hours of local inference, and it is what makes the piece
reproducible by someone without a GPU.

### 6.4 Control condition — build this, it is cheap

`colour_baseline.py` produces a second colour table for nowplaying-RS tracks using
only the dataset's own audio features, through the same axis mapping: map
`valence` to `2*valence-1`, `arousal` to `energy`, `warmth` to `acousticness`,
`density` to `loudness` rescaled from dB, and `grain` to whatever combination you
can defend — document the choice in the README, since there is no clean
equivalent in that feature set.

Export both tables. The UI gets a toggle: **model** / **features** / **difference**.
The difference view (Oklab delta-E between the two colour fields) is the most
interesting single image this project can produce: it shows where a language
model's impression of music diverges from its measured acoustics. Do not skip it.

### 6.5 The long tail

Tracks with `n_events` below the cutoff, or with `confidence < 0.25`, get no model
call. They are assigned the **artist's** mean colour if that artist has 3 or more
coloured tracks; otherwise they are excluded from aggregation and counted in an
`n_uncoloured` column that the UI surfaces. **Excluded is better than guessed.**

### 6.6 Judgement to Oklch — LOCKED

Deterministic, in `oklab.py`, with an identical port in `site/src/colour.ts` and a
shared test vector file asserting the two agree to 1e-9:

```
hue        h = (210 - 150 * valence) mod 360     # bleak cyan-blue -> elated yellow-red
chroma     C = 0.02 + 0.13 * (0.65*arousal + 0.35*density)
lightness  L = 0.38 + 0.34 * warmth - 0.10 * grain
```

Then `a = C*cos(h*pi/180)`, `b = C*sin(h*pi/180)`; store `(L, a, b)`. `era` and
`confidence` do not affect colour in v1 — carry them through, expose them as
filters, and leave the hooks for a later pass.

### 6.7 Colour space — LOCKED, read this twice

**All averaging happens on `(L, a, b)` in Oklab, never on sRGB, never on hue
angles.** Averaging hex colours or hue degrees is the classic failure here, and it
produces grey mud and hue-wraparound artefacts. Oklab is a Euclidean perceptual
space: a count-weighted component-wise mean is the correct operation, it is
associative, and that associativity is exactly what makes the zoom pyramid in
section 8 valid.

Convert Oklab to sRGB only at the last moment, in the client, for display:

```
l_ = L + 0.3963377774a + 0.2158037573b
m_ = L - 0.1055613458a - 0.0638541728b
s_ = L - 0.0894841775a - 1.2914855480b
l = l_^3 ; m = m_^3 ; s = s_^3
r_lin = +4.0767416621l - 3.3077115913m + 0.2309699292s
g_lin = -1.2684380046l + 2.6097574011m - 0.3413193965s
b_lin = -0.0041960863l - 0.7034186147m + 1.7076147010s
srgb(c) = c <= 0.0031308 ? 12.92c : 1.055 * c^(1/2.4) - 0.055
```

If any linear component falls outside [0,1], reduce chroma: binary-search a
multiplier on `C` in [0,1] for 12 iterations until in gamut, holding `L` and `h`.
Do not clip channels — clipping shifts hue.

---

## 7. Spatial aggregation

### 7.1 The grid — LOCKED

**H3** (`h3-py` offline, `h3-js` in the browser). Hexagons, 16 resolutions, one
deterministic parent per cell, roughly equal-area cells.

The rationale, so you do not second-guess it: a Web-Mercator square grid would
make a cell in Svalbard represent a tiny fraction of the ground area of one on the
equator, so "colour per area of human listening" would be silently false. H3 cells
are near-equal-area. The cost is that children do not tile their parent exactly;
this is irrelevant here, because aggregation assigns each cell to exactly one
parent via `cell_to_parent`, which is total and deterministic.

Index every event at **res 8**. Build rollups for **res 8, 7, 6, 5, 4, 3, 2, 1**.

### 7.2 Zoom to resolution — LOCKED

```ts
const ZOOM_TO_RES: number[] = [
//z: 0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16+
     1, 1, 1, 2, 2, 3, 4, 5, 5, 6, 6, 7, 7, 8, 8, 8, 8,
];
const resForZoom = (z: number) =>
  ZOOM_TO_RES[Math.max(0, Math.min(16, Math.floor(z)))];
```

Clamp additionally to `manifest.max_useful_res` (section 9). Resolution changes
must be **hysteretic**: only switch when `|z - z_at_last_switch| > 0.6`, otherwise
a user hovering on a zoom boundary gets a strobing grid.

### 7.3 Time frames — LOCKED, two independent axes

**Axis `chrono`** — calendar time. Bucket `ts_utc` to month (`YYYY-MM`). MMTD
gives 18 frames. Add a `week` variant only if month frames prove too coarse.

**Axis `solar`** — 24 frames, hour of **local solar time**:

```
solar_hour = floor( (hour_utc + minute_utc/60 + lon/15) mod 24 )
```

Use the dataset's `tz_offset_min` when present and non-null, falling back to the
longitude term; record which was used, per event. The `solar` axis is the view
that justifies the whole project: scrubbing it plays the terminator across the
planet and you watch the character of what people listen to rotate with the sun.
**Make `solar` the default axis on first load**, at a zoom showing the whole
world.

Optionally add axis `dow` — 7 frames, local day of week — which is nearly free
once the rollup is parameterised.

### 7.4 The rollup — DuckDB SQL

Do this in DuckDB (CLI or the `duckdb` Python module), not in pandas. Build res 8
from events, then **each coarser level from the level above**, never from the
events — this exercises the associativity property and is far faster:

```sql
-- level 8, from events
CREATE TABLE agg_8 AS
SELECT
  e.frame,
  h3_latlng_to_cell(e.lat, e.lon, 8) AS cell,
  e.geo_precision,
  count(*)                    AS n,
  count(DISTINCT e.user_hash) AS n_users,
  sum(c.L)/count(*)           AS L,
  sum(c.a)/count(*)           AS a,
  sum(c.b)/count(*)           AS b
FROM events_framed e
JOIN track_colour c USING (track_key)
GROUP BY 1,2,3;

-- level r, from level r+1  (run for r = 7 down to 1)
CREATE TABLE agg_7 AS
SELECT
  frame,
  h3_cell_to_parent(cell, 7) AS cell,
  geo_precision,
  sum(n)          AS n,
  sum(n_users)    AS n_users,   -- upper bound; see note
  sum(L*n)/sum(n) AS L,
  sum(a*n)/sum(n) AS a,
  sum(b*n)/sum(n) AS b
FROM agg_8
GROUP BY 1,2,3;
```

Notes to honour:

- `n_users` summed upward over-counts a user present in several children. Either
  accept it as a documented upper bound and name the column `n_users_ub`, or carry
  an HLL sketch. **Pick the documented upper bound for v1** and say so in the UI
  tooltip.
- Install the DuckDB `h3` community extension
  (`INSTALL h3 FROM community; LOAD h3;`). If it is unavailable, index in `h3-py`
  during normalisation and write the res-8 cell id as a column; the parent step
  then needs `h3-py` too, in which case do the whole pyramid in Python with
  `polars` group-bys. Either is acceptable. Do not hand-roll H3 maths.
- Keep `geo_precision` in the `GROUP BY` all the way up, so the client-side filter
  still works at every resolution. The client re-aggregates across precision
  classes when the filter includes more than one, using the same weighted-mean
  formula.

### 7.5 Suppression — LOCKED, non-negotiable

A cell-frame row is **dropped from export** unless `n >= 5 AND n_users_ub >= 3`.

This is simultaneously a privacy measure (these are real people's listening
histories with coordinates attached), a statistical measure (one teenager must not
colour a province), and an aesthetic one (noise-floor cells make the map look like
television static). Additionally, cap any single `user_hash` to **20 events per
(cell, frame)** at the res-8 stage, before aggregation, by random subsampling with
a fixed seed.

Report in the audit how many events and cells each rule removed.

---

## 8. Why there is no DuckDB-WASM in the browser

You may be tempted. Don't, in v1.

After suppression the whole output is small — on the order of 10^5 to 10^6 rows
across all resolutions and frames, at roughly 40 bytes a row. Pre-sharded files
fetched directly are simpler, cacheable by the CDN, and avoid a known class of bug
where duckdb-wasm does not actually issue HTTP range requests and silently
downloads whole files (duckdb/duckdb-wasm#2228). Use `apache-arrow` JS to decode
Arrow IPC shards, which is a few hundred KB of JS rather than several MB of WASM.

Keep DuckDB-WASM on the stretch list for a "query any slice" power view, behind a
lazy import, once the main piece works.

---

## 9. Export format — LOCKED

```
site/public/data/
  manifest.json
  solar/r1/00.arrow  ...  23.arrow
  solar/r2/...
  ...
  solar/r8/...
  chrono/r1/2011-11.arrow ... 2013-04.arrow
  ...
  baseline/solar/r1/00.arrow ...        # control condition, same tree
  notes.json                            # sample of 300 model `note` strings
```

A shard is one Arrow IPC file per `(axis, res, frame)`. Columns, in order:
`cell` (`Uint64`), `n` (`Uint32`), `n_users_ub` (`Uint32`), `geo` (`Uint8` enum:
0 = point, 1 = place_centroid, 2 = city), `L`, `a`, `b` (`Float32`).

Hard limits to respect: **each file under 25 MB**, **total files under 20,000** —
these are Cloudflare Pages free-tier deployment limits, not suggestions. The
current plan yields roughly `(24 + 18 + 7) * 8 * 2 = 784` files, comfortably
inside both.

`manifest.json`:

```json
{
  "built_at": "2026-...",
  "model": "gemma...",
  "sources": [{"name": "MMTD", "url": "...", "licence": "...", "events_used": 0}],
  "axes": {
    "solar":  {"frames": ["00", "01"], "label": "local solar hour"},
    "chrono": {"frames": ["2011-11"],  "label": "month"}
  },
  "resolutions": [1, 2, 3, 4, 5, 6, 7, 8],
  "max_useful_res": 7,
  "cells_per_res": {"1": 0, "2": 0},
  "n_events_total": 0,
  "n_events_suppressed": 0,
  "n_tracks_coloured": 0,
  "n_tracks_uncoloured": 0,
  "colour_axes": {
    "hue": "valence",
    "chroma": "arousal+density",
    "lightness": "warmth-grain"
  }
}
```

`max_useful_res` is the deepest resolution where surviving cells number 500 or
more. Compute it; do not assume it. With MMTD alone it may well be 6 or 7, and the
UI must handle that gracefully rather than showing an empty map at zoom 14.

---

## 10. The web application

### 10.1 Stack — LOCKED

- **Vite + TypeScript**, no UI framework. Plain DOM; the UI is a handful of
  controls. Do not add React for this.
- **MapLibre GL JS** (BSD-3) for the basemap and camera.
- **OpenFreeMap** for tiles: free, no API key, no registration, no usage limit,
  OpenStreetMap data. Style URL: `https://tiles.openfreemap.org/styles/positron`.
  Use **positron** — a near-monochrome style. The map must recede; the colour
  field is the subject. Never put a colourful basemap under a colour
  visualisation.
- **deck.gl** `H3HexagonLayer` via `MapboxOverlay` in **interleaved** mode, so the
  hexes sit under the basemap's label layer and place names stay readable.
- **h3-js** for client-side cell-to-polygon work and for parent/child when
  re-aggregating.
- **apache-arrow** JS for shard decoding.
- No other runtime dependencies. If you reach for one, justify it in the README.

### 10.2 Layer configuration

```ts
new H3HexagonLayer({
  id: 'listening',
  data: rows,                       // {cellHex: string, L, a, b, n}
  getHexagon: d => d.cellHex,
  highPrecision: 'auto',
  extruded: false,
  stitching: true,
  getFillColor: d => oklabToRgba255(d.L, d.a, d.b, alphaFor(d.n)),
  pickable: true,
  transitions: { getFillColor: 220 },
  updateTriggers: { getFillColor: [palette.version, frameId] },
});
```

- **Opacity carries confidence.** Alpha is
  `0.25 + 0.75 * min(1, log1p(n) / log1p(nRef))`, where `nRef` is the 95th
  percentile of `n` in the loaded frame. Sparse cells are faint, well-evidenced
  cells are solid. This is how the map tells the truth about its own coverage
  without needing a separate chart.
- Frame-to-frame colour transitions cross-fade rather than flicker.
- Hover tooltip: cell id, `n`, `n_users_ub`, the resolved sRGB hex, the Oklch
  triple, and a `note` string from `notes.json` where one is available for that
  cell's top track. Hovering should feel like reading a label in a museum.

### 10.3 Shard loading

- On camera idle (debounce 150 ms) compute `res = resForZoom(zoom)`, then the set
  of frames needed: the current frame plus, when playing, the next two.
- `shards.ts` keeps an LRU of decoded frames, capacity 24, keyed
  `${axis}/r${res}/${frame}`. Fetch with `cache: 'force-cache'`.
- Filter to the viewport **after** decode, using `h3-js` `cellToLatLng`, with a
  25% bbox margin. Do not fetch per-viewport slices — whole frames at a given
  resolution are small, and the CDN caches them across all visitors, which matters
  precisely because everyone sees the same data.
- Preload `solar/r1` and `solar/r2` for all 24 frames on first paint; the whole
  world at low resolution is tiny, and this makes the opening animation instant.
- Show a frame as loading rather than as empty. An empty map that is in fact still
  loading is the defect most likely to ship here.

### 10.4 Controls

- **Time scrubber.** A range input over the active axis' frames, with play/pause,
  speed 1x/2x/4x, and loop. On the `solar` axis, draw a small sun glyph and label
  frames `03:00` and so on; on `chrono`, label `Nov 2011`.
- **Axis switch.** `solar` / `chrono` / `dow`.
- **Colour source switch.** `model` / `features` / `difference` (section 6.4). In
  `difference` mode the fill is a sequential ramp on Oklab delta-E, not the colour
  itself — use a single-hue ramp and say so in the legend.
- **Geo precision filter.** Checkboxes; `point` only by default.
- **Resolution readout.** Always visible: "res 6 / 4,218 cells / 1.1M plays".
  Never let the viewer guess what they are looking at.
- **About panel.** What this is; both dataset citations with links; the colour
  mapping from section 6.6 stated plainly; the model tag; the suppression
  thresholds; and one sentence admitting that Twitter-sourced 2011-2013 listening
  data is biased towards young, urban, anglophone, smartphone-owning people and is
  not a picture of what the world listens to. **This paragraph is required.** The
  piece is more interesting when it is honest about being a portrait of a dataset
  rather than a portrait of humanity.

### 10.5 Shared data, local view

The map is identical for every visitor because the data is static files on a CDN.
View state is per-visitor and lives in the URL hash, so any view is shareable:

```
#axis=solar&frame=21&src=model&geo=point&z=4.2&lat=51.5&lon=-0.12&playing=0
```

Serialise on change (throttled 250 ms, `replaceState`), parse on load, validate
every field against the manifest, and fall back to defaults on anything
unexpected.

**Explicit interpretation of the brief:** "the same map for everyone, different
views locally" is satisfied by shared immutable data plus local camera state. It
does **not** mean live cursor or time synchronisation between visitors. If that is
wanted later it is one Cloudflare Durable Object broadcasting a frame index over a
WebSocket, which stays inside the free tier — but it needs a backend and is
therefore out of scope for v1. Do not build it. Do leave `state.ts` structured so
that an external frame-index source could drive it.

---

## 11. Makefile targets

```
make setup      # uv sync; npm ci in site/; check the ollama model is pulled
make fetch      # download + verify raw archives
make schema     # dump real raw schemas to data/work/ for inspection
make normalise  # -> data/work/events.parquet, tracks.parquet
make colour     # resumable Ollama pass -> data/cache/track_colour.jsonl
make baseline   # feature-derived control table
make rollup     # DuckDB pyramid
make export     # Arrow shards + manifest.json into site/public/data
make audit      # coverage/sanity report -> reports/audit.md
make dev        # vite dev server
make build      # vite build
make all        # fetch -> ... -> export -> audit -> build
```

`make colour` must print a live rate and ETA, and must be safe to interrupt.

---

## 12. Hosting — LOCKED

**Cloudflare Pages**, free tier. Static assets get unlimited bandwidth and
requests, 500 builds per month, **25 MB per file**, **20,000 files per
deployment**. Connect the GitHub repo; build command `npm run build` in `site/`;
output directory `site/dist`. Data ships inside the deployment as committed files
under `site/public/data`.

Add a `_headers` file:

```
/data/*
  Cache-Control: public, max-age=31536000, immutable
  Access-Control-Allow-Origin: *
```

and include a build version in the data directory name (`data/v3/...`) so that
immutable caching is safe across rebuilds.

Fallback if the data tree ever exceeds Pages' limits: move `data/` to **Cloudflare
R2** (10 GB free, zero egress) behind a custom domain, and change one base-URL
constant. Second fallback: GitHub Pages (100 MB per file, 1 GB repo, roughly
100 GB/month soft bandwidth). Keep the base URL in one place so this stays a
one-line change.

---

## 13. Things not to do

- Do not call any model from the browser.
- Do not add a server, a database, or a serverless function.
- Do not use the Spotify Audio Features endpoints — access was restricted for new
  applications in November 2024. The nowplaying-RS features are the substitute.
- Do not use `tile.openstreetmap.org` raster tiles; their tile usage policy
  disallows this kind of application, and raster tiles cannot be styled down.
- Do not average sRGB or hue angles anywhere. Oklab only (section 6.7).
- Do not synthesise coordinates from country-level data (section 4.4).
- Do not let the model output RGB (section 6.1).
- Do not hardcode the raw dataset schemas from this document — inspect them.
- Do not quietly lower the suppression thresholds to make the map look fuller.

---

## 14. Definition of done

Offline:

1. `make all` succeeds from a clean checkout with only the raw downloads cached.
2. `tests/test_oklab.py` asserts that the Python and TypeScript Oklab
   implementations agree to 1e-9 over a 500-point shared fixture.
3. `tests/test_rollup.py` asserts, on a synthetic fixture, that the res-5 colour
   computed from res 8 -> 7 -> 6 -> 5 equals the res-5 colour computed directly
   from events, to 1e-9. **This test is the correctness proof of the entire zoom
   behaviour.** It must exist and it must pass.
4. `reports/audit.md` reports: events ingested per source; events dropped per
   validation rule; events dropped by suppression; unique tracks; tracks coloured
   by the model, by artist fallback, and not at all; cells per resolution per axis;
   the 5th/50th/95th percentiles of `n`; and the distribution of each of the five
   judgement axes as plain-text histograms. Read it before declaring success.
   **If the five axes are not reasonably spread — if the model has given nearly
   everything the same answer — stop and report that rather than shipping a
   monochrome map.**

Online, verified in a real browser at both desktop and phone viewport sizes, with
the console open and zero errors:

5. The whole-world view on the `solar` axis, with play pressed, shows a visibly
   rotating pattern of colour. This is the headline result; if it is not visible,
   the piece has failed regardless of code quality.
6. Zooming from z2 to z12 over a dense city subdivides the grid through the
   resolution ladder without flicker, without a blank frame, and with the coarse
   colour visibly being the average of the fine colours.
7. Reloading a shared URL hash reproduces the exact view.
8. With JavaScript disabled, the page shows a legible explanation of what it is
   plus the dataset credits — not a blank screen.
9. Under `prefers-reduced-motion: reduce`, autoplay and cross-fades are disabled;
   the scrubber still works.
10. First paint under 2.5 s on a throttled connection, with the low-resolution
    solar frames already drawn.

---

## 15. Build order

Do not parallelise this. Each step gates the next.

1. Scaffold, `make setup`, `make fetch`, `make schema`. **Print the real schemas
   and read them** before writing adapters.
2. `ingest_mmtd` then `normalise`. Confirm event counts against the paper's stated
   ~1.1M, and check the lat/lon histograms are sane.
3. `oklab.py` + `colour.ts` + the agreement test. Colour maths before colour.
4. `colour_model.py` on the top 2,000 tracks only. Inspect 50 judgements by hand
   against your own knowledge of those records. **Tune the system prompt here, not
   later** — a full pass takes hours and you do not want to repeat it.
5. Full `make colour`. Run it overnight.
6. `rollup` plus `test_rollup.py`.
7. `export`, `manifest.json`, `audit`.
8. Map with basemap only. Then one static frame at one resolution. Then the
   resolution ladder. Then the scrubber. Then the filters, legend and About panel.
9. The nowplaying-RS adapter, `baseline`, and the `difference` view.
10. Deploy. Then re-run section 14 against the deployed URL, not localhost.

Report at each step: what you ran, what the numbers were, and anything that
contradicted this specification. Where this document is wrong about the real data,
the real data wins — say so explicitly rather than bending the data to fit the
spec.
