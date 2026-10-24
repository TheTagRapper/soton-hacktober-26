// Entry point: wire the store, the map, the grid and the shard loader.

import 'maplibre-gl/dist/maplibre-gl.css';
import './style.css';
import { Grid, ResolutionPicker, composeDifference, composeFrame, filterToViewport, rowStats, type HexRow } from './grid';
import { createMap } from './map';
import { ShardStore } from './shards';
import { Store, type Manifest } from './state';
import { Playback } from './time';
import { UI } from './ui';

// One place to change if the data tree ever moves to R2 or another origin
// (spec section 12).
const DATA_BASE_ROOT = import.meta.env.VITE_DATA_BASE ?? './data';
const DATA_VERSION = import.meta.env.VITE_DATA_VERSION ?? 'v1';
const BASE = `${DATA_BASE_ROOT}/${DATA_VERSION}`;

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function fatal(message: string, detail?: unknown): void {
  console.error(message, detail);
  const el = document.createElement('div');
  el.className = 'fatal';
  el.innerHTML = `<h1>Chromatic Listening</h1><p>${message}</p>`;
  document.body.appendChild(el);
}

async function main(): Promise<void> {
  let manifest: Manifest;
  try {
    const r = await fetch(`${BASE}/manifest.json`, { cache: 'no-cache' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    manifest = (await r.json()) as Manifest;
  } catch (err) {
    fatal(
      `Could not load the aggregate data from <code>${BASE}</code>. ` +
        'Run <code>make export</code> to build it.',
      err,
    );
    return;
  }

  const store = new Store(manifest);
  const shards = new ShardStore(BASE, manifest);
  const ui = new UI(store, manifest);
  const playback = new Playback(store, reducedMotion);
  const picker = new ResolutionPicker(store.state.z, manifest.max_useful_res);

  const map = createMap(document.getElementById('map')!, store);
  // Exposed for the browser verification script (tests/verify_browser.mjs)
  // and for poking at a deployed build from the console. Read-only in spirit;
  // nothing in the app reads these back.
  Object.assign(window as unknown as Record<string, unknown>, {
    __map: map,
    __store: store,
    __manifest: manifest,
  });
  const grid = new Grid(map, { reducedMotion }, (row, x, y) =>
    ui.tooltip(row, x, y, picker.current()),
  );

  ui.mount(
    () => void refresh(),
    () => {
      playback.toggle();
      ui.syncAll();
      void refresh();
    },
  );
  ui.syncAll();
  void ui.loadNotes(BASE);

  store.subscribe((_s, changed) => {
    if (changed.has('frame')) {
      ui.syncScrubber();
      void refresh();
    }
  });

  let generation = 0;

  async function refresh(): Promise<void> {
    const gen = ++generation;
    const s = store.state;
    const res = picker.pick(map.getZoom());
    const clamped = res >= manifest.max_useful_res && map.getZoom() > 11;

    const wantBaseline = s.src === 'features' || s.src === 'difference';
    const haveBaseline = Boolean(manifest.has_baseline);

    const baseline = wantBaseline && haveBaseline;
    const cached = shards.get(s.axis, res, s.frame, baseline);
    if (!cached && !shards.isKnownMissing(s.axis, res, s.frame, baseline)) {
      // Show the frame as loading rather than as empty: an empty map that is
      // in fact still loading is the defect most likely to ship here.
      ui.setReadout(res, 0, 0, 'loading', clamped);
    }

    const primary = await shards.load(s.axis, res, s.frame, baseline);
    if (gen !== generation) return;

    let rows: HexRow[] = [];
    let nRef = 1;
    let mode: 'colour' | 'difference' = 'colour';

    if (primary) {
      rows = composeFrame(primary, s.geo);
      nRef = primary.nRef;
      if (s.src === 'difference' && haveBaseline) {
        const modelFrame = await shards.load(s.axis, res, s.frame, false);
        if (gen !== generation) return;
        if (modelFrame) {
          rows = composeDifference(composeFrame(modelFrame, s.geo), rows);
          nRef = modelFrame.nRef;
          mode = 'difference';
        }
      }
    }

    const visible = filterToViewport(rows, map);
    const stats = rowStats(visible);
    grid.setData(visible, nRef, res, `${s.axis}/${res}/${s.frame}/${s.src}`, mode);
    ui.setReadout(
      res,
      stats.cells,
      stats.plays,
      visible.length ? 'ok' : 'empty',
      res >= manifest.max_useful_res && map.getZoom() > 11,
    );

    // Prefetch the frames playback is about to need.
    for (const f of playback.lookahead()) {
      if (f !== s.frame) void shards.load(s.axis, res, f, baseline);
    }
  }

  let idleTimer: number | undefined;
  const onCameraSettle = () => {
    if (idleTimer !== undefined) window.clearTimeout(idleTimer);
    idleTimer = window.setTimeout(() => void refresh(), 150);
  };

  map.on('load', async () => {
    map.addControl(grid.overlay);
    // The whole world at low resolution is tiny; preloading makes the opening
    // instant and the first paint already coloured.
    void shards.preload(store.state.axis, 1);
    await refresh();
    void shards.preload(store.state.axis, 2);
    if (store.state.playing && !reducedMotion) {
      playback.play();
      ui.syncAll();
    }
  });
  map.on('move', onCameraSettle);
  map.on('zoom', onCameraSettle);
  map.on('error', (e) => console.warn('maplibre', e?.error ?? e));
}

void main();
