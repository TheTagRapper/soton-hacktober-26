# Audit findings — 2026-10-24

Reported symptoms: cells grey at most zooms; almost all pink in the weekday view;
too few data points; several filters inert.

All four have been traced. **One root cause explains both colour symptoms.**

---

## 1. The colour model has never run. The map renders a hash function.

- Colour table in use: `data/work/stub_colour.jsonl`
- Its header: `"model": "stub:dev-preview"`, `"warning": "dev preview, not model output"`
- `site/public/data/dev/manifest.json` carries `"model": "stub:dev-preview"`
- `data/cache/` is empty — no `track_colour.jsonl` exists
- **`ollama` is not installed on this machine**

`pipeline/colour_stub.py` generates judgements as a seeded hash of the track key.
Across its 4,000 tracks the mean weight of all thirteen emotions is 0.231–0.250:

```
annoying 0.250   calm 0.249   joyful 0.249   beautiful 0.249   indignant 0.248
scary    0.244   triumphant 0.244   erotic 0.243   energizing 0.240
amusing  0.239   dreamy 0.239   sad 0.234   anxious 0.231
```

Uniform noise around 0.25. Consequences:

**The grey.** Thirteen near-complementary palette colours blended at near-equal
weight give grey *before any spatial averaging*. Every track is already grey, so
every cell was always going to be. This is not the aggregation problem it looks
like.

**The pink.** `cowen.SHARPEN = 2.5` raises weights to a power so the strongest
emotion dominates. With no real winner it amplifies hash noise — and four of the
five highest-mean emotions (`annoying` `#ff0000`, `calm` `#ff1ab9`, `beautiful`
`#ffc184`, `indignant` `#f69edc`) lie in the red–pink–magenta corner of the
palette. Ties break toward pink, and the effect survives best where fewest tracks
share a cell.

### Fix, in order

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma3:4b      # 8 GB VRAM -> 4B class; a 12B at 4-bit works but ~4x slower
make colour                # resumable, hours; writes data/cache/track_colour.jsonl
make rollup export audit
```

`colour_stub.py` is good work and should stay — building the renderer against a
labelled stub was correct. The defect is that nothing prevents shipping it:

- [ ] `export.py` fails if the colour table header `model` starts with `stub:`,
      unless `--allow-stub` is passed.
- [ ] With `--allow-stub`, the front end shows a persistent banner — "preview:
      placeholder colours, not model output" — driven from `manifest.model`.
      A stub must never be able to look like a result.

## 2. Cowen's palette is wrong for blending (secondary, fix after the model runs)

Adopting Cowen et al. (2020)'s thirteen dimensions over the spec's five invented
axes was a genuinely better choice. **Keep the dimensions.**

But `PALETTE_SRGB` holds *categorical identification colours from a scatter plot* —
chosen so thirteen clusters of dots can be told apart, not as an ordered colour
space. Several are near-complementary. Their weighted mean is grey by construction
for any track that feels like more than one thing, which is most music. `SHARPEN`
is a plaster over this and introduces the bias shown above.

Rule: **blend in an ordered space, label in a categorical one.**

- [ ] Assign each emotion a fixed `(valence, arousal)` coordinate.
- [ ] Per track, take the weight-weighted centroid in that 2-D space.
- [ ] Map centroid to colour: `h = atan2(arousal - 0.5, valence)`; chroma from
      distance from neutral, so genuinely ambivalent tracks are desaturated —
      which is true rather than muddy.
- [ ] Store the thirteen weights as an additive 13-bin histogram in the
      aggregates. Replaces a hue histogram and is better: `dominant` mode can name
      the emotion.
- [ ] Use Cowen's palette **only** in `dominant` mode, where nothing is blended.
      Credit it on screen there.
- [ ] Then set `SHARPEN = 1.0` and delete the constant.

## 3. Every UI control is inert

`site/index.html` declares `axis-switch`, `base-switch`, `geo-filter`,
`src-switch`. **None of these ids appears anywhere in `site/src/`.** The only
element the TypeScript reaches for is `getElementById('map')`. The control panel
is decoration.

The data behind them largely exists — `manifest.json` exports all three axes
(`solar`, `chrono`, `dow`) and resolutions 1–8 with `max_useful_res: 7`.

- [ ] `axis-switch` — **wire it.** The data is exported; this is the cheapest win.
- [ ] `src-switch` (`model`/`features`/`difference`) — `colour_baseline.py` exists;
      wire it, or hide the control until nowplaying-RS is ingested.
- [ ] `geo-filter` — **remove.** MMTD is effectively all `point`, so it can never
      do anything. Replace with a **source** filter once radio data lands; that is
      the distinction that will matter.
- [ ] `base-switch` — wire or delete. Do not leave it.

An inert control is worse than an absent one: it tells the viewer the map is
answering a question it is not answering.

## 4. Data volume — the pipeline is fine, the corpus is too small

MMTD ingested correctly: **1,086,546 events**, 295,366 suppressed (27%) by the
`n>=5 AND n_users>=3` floor. Cells per resolution peak at res 5 (18,580) and fall
to 8,957 by res 8, hence `max_useful_res: 7`. Nothing is broken here; 1.1M events
spread over the planet and eighteen months simply cannot fill a city-scale grid.

A harvester for a much larger, live, genuinely geolocated source is running now —
see `~/Desktop/chromatic-listening/pipeline/harvest_radio.py` and §2.2 of
`SPEC_REVISION_2.md`. Measured: **12,774 geolocated radio stations across 180
countries**, 63% of which expose their current track via ICY metadata, yielding
roughly **400,000 events/day**. It matches MMTD's entire corpus in about three
days and has no personal data in it, so the suppression floor can drop to `n>=3`
with no user condition — which also recovers depth at high zoom.

- [ ] Ingest adapter for `data/raw/radio/*.jsonl` -> normalised events schema
- [ ] Cleaner per §2.4 of the revision (real examples of the filth are listed there)
- [ ] Per-source suppression floors per §2.6
- [ ] Build res 9–10; render individual stations as points past `max_useful_res`
      (§4.6) — radio events have exact transmitter coordinates, which is the
      "more detailed location points" the brief asks for

---

## Order of work

1. Install Ollama, pull Gemma, `make colour`. **Nothing else until this is done** —
   every colour decision below it is tuning applied to noise.
2. Export guard + preview banner (§1), so this cannot recur.
3. Wire `axis-switch`; remove `geo-filter`. One hour, visible.
4. Palette fix (§2).
5. Radio ingest (§4), then res 9–10 and the point layer.
6. Sums-not-means schema and the four render modes from `SPEC_REVISION_2.md` §1.2–1.4.

Re-check the picture after step 1 alone. It may need far less of the rest than
it currently looks.
