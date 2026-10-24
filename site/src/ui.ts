// Legend, panels, counts, about (spec section 10.4).

import {
  EMOTIONS,
  EMOTION_LABELS,
  PALETTE_SRGB,
  SHARPEN,
  oklabToHex,
  oklabToSrgb255,
} from './colour';
import type { HexRow } from './grid';
import { frameLabel, sunGlyph } from './time';
import type { Manifest, Store } from './state';

export interface Notes {
  track_key: string;
  artist: string;
  title: string;
  note: string;
}

const $ = <T extends HTMLElement>(sel: string): T => document.querySelector<T>(sel)!;

/** The thirteen emotions, each in its own colour. Not a ramp -- a key. */
export function paletteKey(): string {
  return EMOTIONS.map(
    (e) =>
      `<span class="key"><i style="background:${PALETTE_SRGB[e]}"></i>${EMOTION_LABELS[e]}</span>`,
  ).join('');
}

function diffRampRow(): string {
  const stops: string[] = [];
  for (let i = 0; i <= 12; i++) {
    const t = i / 12;
    const L = 0.95 - 0.62 * t;
    const C = 0.02 + 0.13 * t;
    const h = (295 * Math.PI) / 180;
    const [r, g, b] = oklabToSrgb255(L, C * Math.cos(h), C * Math.sin(h));
    stops.push(`rgb(${r},${g},${b})`);
  }
  return stops.map((c) => `<i style="background:${c}"></i>`).join('');
}

export class UI {
  private notes: Notes[] = [];

  constructor(
    private store: Store,
    private manifest: Manifest,
  ) {}

  async loadNotes(base: string): Promise<void> {
    try {
      const r = await fetch(`${base}/notes.json`, { cache: 'force-cache' });
      if (r.ok) this.notes = (await r.json()) as Notes[];
    } catch {
      this.notes = [];
    }
  }

  mount(onChange: () => void, onPlayToggle: () => void): void {
    const s = this.store.state;
    $('#chrome').hidden = false;

    // Axis / source / geo controls reflect the parsed hash, not the defaults.
    for (const el of document.querySelectorAll<HTMLInputElement>('input[name=axis]')) {
      el.checked = el.value === s.axis;
      el.disabled = !this.manifest.axes[el.value];
      el.addEventListener('change', () => {
        if (el.checked) {
          const frames = this.manifest.axes[el.value]?.frames ?? [];
          this.store.patch({ axis: el.value as never, frame: frames[0] ?? '00' });
          this.syncScrubber();
          onChange();
        }
      });
    }
    for (const el of document.querySelectorAll<HTMLInputElement>('input[name=src]')) {
      el.checked = el.value === s.src;
      // No control condition in the data? Say so rather than offering a dead
      // control that silently shows the model field.
      const needsBaseline = el.value !== 'model';
      el.disabled = needsBaseline && !this.manifest.has_baseline;
      if (el.disabled) {
        el.closest('label')?.setAttribute(
          'title',
          'No feature-derived control table in this build — see the About panel.',
        );
      }
      el.addEventListener('change', () => {
        if (el.checked) {
          this.store.patch({ src: el.value as never });
          this.renderLegend();
          onChange();
        }
      });
    }
    for (const el of document.querySelectorAll<HTMLInputElement>('input[name=base]')) {
      el.checked = el.value === s.base;
      el.addEventListener('change', () => {
        if (el.checked) this.store.patch({ base: el.value as never });
      });
    }
    for (const el of document.querySelectorAll<HTMLInputElement>('input[name=geo]')) {
      el.checked = s.geo.includes(el.value as never);
      el.addEventListener('change', () => {
        const picked = [...document.querySelectorAll<HTMLInputElement>('input[name=geo]')]
          .filter((x) => x.checked)
          .map((x) => x.value);
        if (!picked.length) {
          el.checked = true; // never leave the map with nothing selected
          return;
        }
        this.store.patch({ geo: picked as never });
        onChange();
      });
    }

    const scrubber = $<HTMLInputElement>('#scrubber');
    scrubber.addEventListener('input', () => {
      const frames = this.store.frames();
      const frame = frames[Number(scrubber.value)];
      if (frame) {
        this.store.setFrame(frame);
        this.syncFrameLabel();
        onChange();
      }
    });
    $('#play').addEventListener('click', onPlayToggle);
    for (const b of document.querySelectorAll<HTMLButtonElement>('.speeds button')) {
      b.addEventListener('click', () => {
        this.store.patch({ speed: Number(b.dataset.speed) as never });
        for (const o of document.querySelectorAll('.speeds button')) o.classList.remove('on');
        b.classList.add('on');
      });
    }

    $('#about-open').addEventListener('click', () => this.toggleAbout(true));
    $('#about-close').addEventListener('click', () => this.toggleAbout(false));
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') this.toggleAbout(false);
      if (e.key === ' ' && (e.target as HTMLElement).tagName !== 'INPUT') {
        e.preventDefault();
        onPlayToggle();
      }
      if (e.key === 'ArrowRight') {
        this.store.stepFrame(1);
        this.syncAll();
        onChange();
      }
      if (e.key === 'ArrowLeft') {
        this.store.stepFrame(-1);
        this.syncAll();
        onChange();
      }
    });

    this.syncScrubber();
    this.renderLegend();
    this.renderAbout();
  }

  toggleAbout(open: boolean): void {
    $('#about').hidden = !open;
    $('#about-open').setAttribute('aria-expanded', String(open));
  }

  syncScrubber(): void {
    const frames = this.store.frames();
    const scrubber = $<HTMLInputElement>('#scrubber');
    scrubber.max = String(Math.max(0, frames.length - 1));
    scrubber.value = String(this.store.frameIndex());
    this.syncFrameLabel();
  }

  syncFrameLabel(): void {
    const s = this.store.state;
    const glyph = s.axis === 'solar' ? `${sunGlyph(s.frame)} ` : '';
    $('#frame-label').textContent = `${glyph}${frameLabel(s.axis, s.frame, this.manifest)}`;
  }

  syncAll(): void {
    this.syncScrubber();
    $('#play').textContent = this.store.state.playing ? '❙❙' : '▶';
    $('#play').setAttribute('aria-label', this.store.state.playing ? 'Pause' : 'Play');
  }

  /** "res 6 / 4,218 cells / 1.1M plays" — never let the viewer guess.
   *
   *  Three states have to be distinguishable or the map lies by omission:
   *  still loading, loaded with cells, and loaded with nothing to show
   *  because nothing here cleared the suppression threshold. */
  setReadout(
    res: number,
    cells: number,
    plays: number,
    state: 'loading' | 'ok' | 'empty',
    clamped: boolean,
  ): void {
    const fmt = (n: number) =>
      n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n);
    const el = $('#readout');
    if (state === 'loading') {
      el.textContent = `res ${res} / loading…`;
      el.classList.add('loading');
      return;
    }
    el.classList.remove('loading');
    const bits = [`res ${res}`, `${cells.toLocaleString()} cells`, `${fmt(plays)} plays`];
    if (clamped) bits.push(`max useful res ${this.manifest.max_useful_res}`);
    if (state === 'empty' || cells === 0) {
      const sup = this.manifest.suppression;
      bits.push(
        `nothing here clears the threshold${
          sup ? ` (${sup.min_n} plays by ${sup.min_n_users_ub} listeners)` : ''
        }`,
      );
    }
    el.textContent = bits.join(' / ');
  }

  renderLegend(): void {
    const diff = this.store.state.src === 'difference';
    $('#legend').innerHTML = diff
      ? `<div class="ramp">${diffRampRow()}</div>
         <p class="legend-note">
           Difference view: fill is a single-hue sequential ramp on Oklab ΔE between the
           model's colour field and the feature-derived control field — pale where the two
           agree, deep where a language model's impression of the music diverges from its
           measured acoustics. <strong>In this mode the fill is not the music's colour.</strong>
         </p>`
      : `<div class="keys">${paletteKey()}</div>
         <p class="legend-note">
           Thirteen dimensions of music-evoked emotion and their colours, after
           <a href="https://www.ocf.berkeley.edu/~acowen/music.html" rel="noreferrer">Cowen
           et al.</a> Each cell is the blend of what was playing there, mixed in Oklab.
           Opacity carries how much evidence the cell has.
         </p>`;
  }

  tooltip(row: HexRow | null, x: number, y: number, res: number): void {
    const el = $('#tooltip');
    if (!row) {
      el.hidden = true;
      return;
    }
    const hex = oklabToHex(row.L, row.a, row.b);
    const C = Math.hypot(row.a, row.b);
    const h = ((Math.atan2(row.b, row.a) * 180) / Math.PI + 360) % 360;
    const note = this.noteFor(row.cell.toString(16));
    const geoName = ['point', 'place centroid', 'city'][row.geo] ?? 'unknown';
    el.innerHTML = `
      <div class="tt-head">
        <span class="chip" style="background:${hex}"></span>
        <span class="mono">${hex}</span>
        <span class="muted">res ${res}</span>
      </div>
      <dl>
        <dt>cell</dt><dd class="mono">r${row.res} · ${row.cell.toString(16)}</dd>
        <dt>plays</dt><dd>${row.n.toLocaleString()}</dd>
        <dt>listeners</dt>
        <dd>${row.nUsers.toLocaleString()} <span class="muted">(upper bound — summed over
          child cells, so a listener in several of them is counted more than once)</span></dd>
        <dt>Oklch</dt>
        <dd class="mono">L ${row.L.toFixed(3)} · C ${C.toFixed(3)} · h ${h.toFixed(0)}°</dd>
        ${row.dE !== undefined ? `<dt>ΔE</dt><dd class="mono">${row.dE.toFixed(3)}</dd>` : ''}
        <dt>precision</dt><dd>${geoName}</dd>
      </dl>
      ${
        note
          ? `<p class="note">“${note.note}”
               <span class="muted">— the model on ${note.artist} — ${note.title}.
               One of its notes, drawn from the sample, not this cell's top track.</span></p>`
          : ''
      }
    `;
    el.hidden = false;
    // Keep the label on screen at phone widths.
    const pad = 14;
    const w = el.offsetWidth;
    const hgt = el.offsetHeight;
    el.style.left = `${Math.min(window.innerWidth - w - pad, Math.max(pad, x + 16))}px`;
    el.style.top = `${Math.min(window.innerHeight - hgt - pad, Math.max(pad, y + 16))}px`;
  }

  /** A note string, chosen deterministically per cell so hovering is stable.
   *
   *  The specification wanted the note belonging to the cell's own top track.
   *  The shards deliberately carry no track identity -- only counts and a
   *  colour -- so that is not available client-side, and inventing an
   *  association would be a small lie told in a museum label. The tooltip says
   *  what this actually is instead. */
  private noteFor(cellHex: string): Notes | undefined {
    if (!this.notes.length) return undefined;
    let acc = 0;
    for (let i = 0; i < cellHex.length; i++) acc = (acc * 31 + cellHex.charCodeAt(i)) >>> 0;
    return this.notes[acc % this.notes.length];
  }

  renderAbout(): void {
    const m = this.manifest;
    const sup = m.suppression ?? { min_n: 5, min_n_users_ub: 3, user_cap_per_cell_frame: 20 };
    const sources = m.sources ?? [];
    $('#about-body').innerHTML = `
      <h2>What this is</h2>
      <p>
        A world map whose colour is the music being played on it. Each hexagon is filled
        with the count-weighted perceptual average, in Oklab, of the colours assigned to
        every song actually being listened to inside that cell during the selected window
        of time. Zoom in and the grid subdivides; zoom out and cells merge and their
        colours average. Everyone who opens this URL sees the same data and the same
        colours — the camera, the time window and the filters are yours alone and live
        only in the address bar.
      </p>

      <h2>How a song becomes a colour</h2>
      <p>
        A language model (<code>${m.model}</code>) ran offline, once, over the most-played
        tracks. It was never asked for a colour: language models pick bad RGB triples and
        cluster on named web colours. It was asked how strongly a listener would feel each
        of the <strong>thirteen dimensions of music-evoked emotion</strong> identified by
        <a href="https://www.ocf.berkeley.edu/~acowen/music.html" rel="noreferrer">Cowen,
        Fang, Sauter &amp; Keltner (PNAS 2020)</a> — and the song's colour is the blend of
        that published palette, weighted by those ratings and mixed in Oklab:
      </p>
      <div class="keys">${paletteKey()}</div>
      <p class="muted">
        Several of those thirteen colours are near-complementary, so a flat blend of a song
        that scores moderately on many of them lands on grey — and averaging places
        together would then grey the whole map. Ratings are raised to the power
        ${SHARPEN} before blending, which lets the feelings a listener would actually name
        dominate the ones that merely registered.
      </p>
      <p>
        All averaging happens on (L, a, b) in Oklab, never on sRGB and never on hue angles;
        averaging hex colours or hue degrees produces grey mud and wraparound artefacts.
        Because the Oklab mean is associative, a coarse cell really is the average of the
        fine cells inside it — that is what makes zooming out honest.
      </p>

      <h2>What is left out</h2>
      <p>
        A cell is shown only if it holds at least ${sup.min_n} plays by at least
        ${sup.min_n_users_ub} distinct listeners, and no single listener contributes more
        than ${sup.user_cap_per_cell_frame} plays to any one cell-and-frame. This is privacy
        (these are real people's listening histories with coordinates attached), statistics
        (one teenager must not colour a province) and aesthetics at once.
        ${(m.n_tracks_uncoloured ?? 0).toLocaleString()} tracks have no colour and are
        excluded from the average rather than guessed at. Events that came with only a
        country are dropped, never scattered across it.
      </p>

      <h2>The data</h2>
      <ul>
        ${sources
          .map(
            (s) =>
              `<li><a href="${s.url}" rel="noreferrer">${s.name}</a>${
                s.citation ? ` — ${s.citation}` : ''
              }. <span class="muted">${s.licence}</span></li>`,
          )
          .join('')}
        <li>
          <a href="https://zenodo.org/records/3248543" rel="noreferrer">#nowplaying-RS</a>
          — Poddar, Zangerle &amp; Yang, SMC 2018, CC BY 4.0. Source of the per-track audio
          features used as the non-model control condition.
          ${m.has_baseline ? '' : '<strong>Not present in this build, so the features and difference views are disabled.</strong>'}
        </li>
      </ul>
      <p class="muted">
        Built ${m.built_at} · data version ${m.data_version} ·
        ${(m.n_events_coloured ?? 0).toLocaleString()} of
        ${(m.n_events_total ?? 0).toLocaleString()} events aggregated ·
        ${(m.n_tracks_coloured ?? 0).toLocaleString()} tracks coloured ·
        basemap © OpenFreeMap, OpenStreetMap contributors.
      </p>

      <h2>Honestly</h2>
      <p class="honest">
        Twitter-sourced listening data from 2011–2013 is biased towards young, urban,
        anglophone, smartphone-owning people who posted what they were playing, and this
        map is therefore a portrait of a dataset rather than a portrait of what the world
        listens to. Empty regions are mostly the absence of that behaviour, not the absence
        of music.
      </p>
    `;
  }
}
