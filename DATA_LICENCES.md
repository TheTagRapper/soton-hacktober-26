# Data licences and provenance

One section per dataset: citation, source, licence, date retrieved, what was
derived from it, and whether raw data is redistributed.

**Raw events are never committed and never deployed.** The only things that
ship are derived aggregates that survived suppression (n >= 5 plays by >= 3
distinct listeners per cell-frame) plus the per-track colour table, which
contains no user data at all.

---

## 1. MMTD — Million Musical Tweets Dataset (primary)

- **Citation:** David Hauger and Markus Schedl, *The Million Musical Tweets
  Dataset — What We Can Learn From Microblogs*, ISMIR 2013. Department of
  Computational Perception, Johannes Kepler University Linz.
- **Source:** `https://www.cp.jku.at/datasets/MMTD/mmtd.zip` (134,305,875 bytes)
- **Retrieved:** 2026-10-24
- **Licence:** Published for research use by JKU Linz. The directory index page
  returns HTTP 403 to non-browser clients while the archive itself serves
  normally; that 403 is not a withdrawal of the dataset.
- **Redistributed here:** No. Neither the archive nor any per-event row is
  committed. `data/raw/` and `data/work/` are gitignored.
- **Derived:** normalised listening events (coordinates, UTC timestamp, UTC
  offset, artist, title), a unique-track table, and from those the H3 cell
  aggregates under `site/public/data/`.

### Actual schema found

The specification this project was built from expected "separate tables for
listening events, tracks, artists and places, joined by integer ids". **That is
not what the archive contains.** It holds exactly one file:

```
mmtd.txt   936,706,436 bytes   291 columns   tab-separated   one header row
```

Every record is fully denormalised: the event, the track, the artist MBID, the
reverse-geocoded place (down to street and postal code), country-level
statistics, and roughly 250 one-hot `lang_*` flags, all on one line. The
adapter in `pipeline/ingest_mmtd.py` is written against this, and the dump it
was written from is reproduced by `make schema` into
`data/work/mmtd_schema.txt`.

Columns used:

| column | used as |
|---|---|
| `tweet_id` | `event_id` (prefixed `mmtd:`) |
| `tweet_userId` | salted-SHA-256 `user_hash`, then discarded |
| `tweet_unixtime` | `ts_utc` |
| `tweet_latitude`, `tweet_longitude` | `lat`, `lon` |
| `timezone` | `tz_offset_min` (the field is in **hours**, e.g. `-5.00`) |
| `artist_name`, `track_title` | `artist_raw`, `title_raw`, then `track_key` |

The remaining ~280 columns, including all `lang_*` flags, the postal address
fields and the country statistics, are read and discarded.

### Counts as ingested (2026-10-24)

| metric | value |
|---|---|
| raw rows | 1,086,808 |
| dropped: missing timestamp / null coords / out-of-range / (0,0) / missing text | 0 |
| events normalised | 1,086,546 |
| unique tracks (`track_key`) | 125,845 |
| unique users | 214,136 |
| time span | 2011-11-09 to 2013-04-30 |

These agree with the paper's stated ~1.1M events, ~215K users and ~134K songs;
the track count is lower than 134K because `track_key` deliberately folds
remaster/version suffixes and featured artists together (see section 5 of the
specification).

**Geographic precision:** every MMTD event is a geotagged tweet reverse-geocoded
to street level, so every surviving row is labelled `point`. Nothing in this
dataset is a bounding-box centre, so nothing is labelled `place_centroid`.

---

## 2. #nowplaying-RS (secondary — control condition)

- **Citation:** Asmita Poddar, Eva Zangerle and Yi-Hsuan Yang,
  *#nowplaying-RS: A New Benchmark Dataset for Building Context-Aware Music
  Recommender Systems*, SMC 2018.
- **Source:** Zenodo record 3248543,
  `https://zenodo.org/records/3248543/files/nowplayingrs.zip?download=1` (~1.3 GB)
- **Licence:** **CC BY 4.0** — the cleaner licence of the two.
- **Retrieved:** download started 2026-10-24; see "Status" below.
- **Redistributed here:** No.
- **Derived:** the per-track audio features (`valence`, `energy`,
  `acousticness`, `loudness`, `speechiness`, `instrumentalness`, `mode`, and
  others) feeding `pipeline/colour_baseline.py`, which is the non-model control
  condition, plus any events carrying a precise `Point` coordinate.

A plain `HEAD` against the Zenodo file returns 403; a `GET`, including a ranged
one, returns 200/206.

**Only a minority of events carry precise `coordinates`.** The adapter keeps
exactly those. An event with only a `place` bounding box, or only a country, is
**dropped** — never centred, never jittered, never distributed across the
country. Synthesising geography would make the entire map a lie, which is why
it is the one rule in this project with no exceptions.

Those audio features are a frozen 2018 snapshot of a feature set that is no
longer publicly obtainable (the Spotify audio-features endpoints were
restricted for new applications in November 2024). They are treated as
irreplaceable and are never re-derived from a live API.

**Status in this build:** see `has_baseline` in
`site/public/data/<version>/manifest.json`. When it is `false`, the archive was
not present at build time, the `features` and `difference` views are disabled
in the UI, and the About panel says so rather than silently showing the model
field twice.

---

## 3. AcousticBrainz (optional enrichment — not used)

- Frozen June 2022 dump, ~7.5M recordings keyed by MusicBrainz recording MBID,
  CC0, still downloadable from `https://acousticbrainz.org/download` although
  the submission API is permanently offline.
- **Not used in this build.** It would require MBID resolution, which MMTD
  partially provides (`artist_mbid` only, not recording MBIDs) and
  #nowplaying-RS does not. It remains a stretch goal for a second independent
  feature source.

---

## 4. The emotion palette

- **Citation:** Cowen, A. S., Fang, X., Sauter, D., & Keltner, D. (2020). *What
  music makes us feel: At least 13 dimensions organize subjective experiences
  associated with music across different cultures.* PNAS 117(4), 1924–1934.
- **Source of the colours:** Alan Cowen's interactive map accompanying that
  paper, `https://www.ocf.berkeley.edu/~acowen/music.html`, retrieved
  2026-10-24. The thirteen category labels and the exact sRGB value each is
  drawn in were read off that page and are reproduced in `pipeline/cowen.py`.
- **Redistributed here:** only the thirteen colour values and category names —
  no audio, no clips, no ratings data from the study. The map's own audio
  samples and participant data are not used.
- **Derived:** every colour in this project. A track's colour is the
  intensity-weighted mean of those thirteen palette entries in Oklab, weighted
  by a local language model's ratings of the track on those thirteen
  dimensions, with the ratings sharpened (exponent 2.5) before blending. The
  study's own ratings are *not* used — the model's are. The taxonomy and the
  palette come from the paper; the per-track ratings do not, and nothing here
  should be read as a finding of that study.

---

## 5. Basemap

- **OpenFreeMap**, style `positron`, `https://tiles.openfreemap.org/styles/positron`.
  Free, no API key, no registration, no usage limit. Vector tiles built from
  **OpenStreetMap** data, © OpenStreetMap contributors, ODbL 1.0. Credited in
  the map's attribution control and in the About panel.
- `tile.openstreetmap.org` raster tiles are deliberately **not** used: their
  tile usage policy disallows this kind of application, and raster tiles cannot
  be styled down out of the way of a colour visualisation.

---

## 6. The colour table

`data/cache/track_colour.jsonl` is committed. It holds one record per track:
the normalised artist and title, the model tag that produced it, the eight
judgements, and the resulting Oklch triple. It contains **no user data, no
coordinates and no timestamps of listening** — only opinions about recordings.

It is committed because it is hours of local inference and it is what makes
this piece reproducible by someone without a GPU.
