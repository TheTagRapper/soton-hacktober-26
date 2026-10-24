// deck.gl H3HexagonLayer, zoom -> resolution, shard composition (spec 7.2, 10.2).

import { H3HexagonLayer } from '@deck.gl/geo-layers';
import { MapboxOverlay } from '@deck.gl/mapbox';
import { cellToLatLng, cellToParent, getResolution } from 'h3-js';
import maplibregl from 'maplibre-gl';
import { deltaE, oklabToRgba255, type Lab } from './colour';
import { firstLabelLayerId } from './map';
import { GEO_CODE, type Frame, type Row } from './shards';
import type { Geo, Manifest, Store } from './state';

// Spec section 7.2, LOCKED.
export const ZOOM_TO_RES: number[] = [
  // z: 0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16+
  1, 1, 1, 2, 2, 3, 4, 5, 5, 6, 6, 7, 7, 8, 8, 8, 8,
];

export const resForZoom = (z: number): number =>
  ZOOM_TO_RES[Math.max(0, Math.min(16, Math.floor(z)))];

/** Hysteresis: only switch resolution once the zoom has moved more than 0.6,
 *  otherwise a user hovering on a boundary gets a strobing grid. */
export class ResolutionPicker {
  private res: number;
  private zAtSwitch: number;

  constructor(z: number, private maxRes: number) {
    this.res = Math.min(resForZoom(z), maxRes);
    this.zAtSwitch = z;
  }

  pick(z: number): number {
    const want = Math.min(resForZoom(z), this.maxRes);
    if (want !== this.res && Math.abs(z - this.zAtSwitch) > 0.6) {
      this.res = want;
      this.zAtSwitch = z;
    }
    return this.res;
  }

  current(): number {
    return this.res;
  }
}

export interface HexRow {
  cellHex: string;
  L: number;
  a: number;
  b: number;
  n: number;
  nUsers: number;
  geo: number;
  dE?: number;
}

/** Alpha carries confidence: sparse cells faint, well-evidenced cells solid.
 *  This is how the map tells the truth about its own coverage. */
export function alphaFor(n: number, nRef: number): number {
  return 0.25 + 0.75 * Math.min(1, Math.log1p(n) / Math.log1p(Math.max(2, nRef)));
}

/** Re-aggregate across the selected precision classes with the same
 *  weighted-mean formula the pyramid used (spec section 7.4). */
export function composeFrame(frame: Frame, geo: Geo[]): HexRow[] {
  const allow = new Set(geo.map((g) => GEO_CODE[g]));
  const acc = new Map<bigint, { n: number; nu: number; L: number; a: number; b: number; geo: number }>();
  for (const r of frame.rows) {
    if (!allow.has(r.geo)) continue;
    const prev = acc.get(r.cell);
    if (prev) {
      prev.L += r.L * r.n;
      prev.a += r.a * r.n;
      prev.b += r.b * r.n;
      prev.n += r.n;
      prev.nu += r.nUsers;
      prev.geo = Math.min(prev.geo, r.geo);
    } else {
      acc.set(r.cell, {
        n: r.n,
        nu: r.nUsers,
        L: r.L * r.n,
        a: r.a * r.n,
        b: r.b * r.n,
        geo: r.geo,
      });
    }
  }
  const out: HexRow[] = [];
  for (const [cell, v] of acc) {
    out.push({
      cellHex: cell.toString(16),
      L: v.L / v.n,
      a: v.a / v.n,
      b: v.b / v.n,
      n: v.n,
      nUsers: v.nu,
      geo: v.geo,
    });
  }
  return out;
}

/** The difference view: Oklab delta-E between the model field and the
 *  feature-derived control field (spec section 6.4). */
export function composeDifference(model: HexRow[], features: HexRow[]): HexRow[] {
  const byCell = new Map(features.map((r) => [r.cellHex, r]));
  const out: HexRow[] = [];
  for (const m of model) {
    const f = byCell.get(m.cellHex);
    if (!f) continue;
    const p: Lab = { L: m.L, a: m.a, b: m.b };
    const q: Lab = { L: f.L, a: f.a, b: f.b };
    out.push({ ...m, dE: deltaE(p, q) });
  }
  return out;
}

const DIFF_MAX = 0.35; // Oklab delta-E at the top of the ramp

/** Single-hue sequential ramp for the difference view -- stated in the legend,
 *  because in this mode the fill is no longer the music's colour. */
function diffColour(dE: number, alpha: number): [number, number, number, number] {
  const t = Math.min(1, Math.max(0, dE / DIFF_MAX));
  const L = 0.95 - 0.62 * t;
  const C = 0.02 + 0.13 * t;
  const h = (295 * Math.PI) / 180;
  return oklabToRgba255(L, C * Math.cos(h), C * Math.sin(h), alpha);
}

export function filterToViewport(rows: HexRow[], map: maplibregl.Map): HexRow[] {
  const b = map.getBounds();
  const latPad = (b.getNorth() - b.getSouth()) * 0.25;
  const lonPad = (b.getEast() - b.getWest()) * 0.25;
  const s = b.getSouth() - latPad;
  const n = b.getNorth() + latPad;
  const w = b.getWest() - lonPad;
  const e = b.getEast() + lonPad;
  const wraps = e - w >= 360;
  return rows.filter((r) => {
    const [lat, lon] = cellToLatLng(r.cellHex);
    if (lat < s || lat > n) return false;
    if (wraps) return true;
    // Compare in the same wrapped frame as the viewport.
    const shifted = lon < w ? lon + 360 : lon > e ? lon - 360 : lon;
    return shifted >= w && shifted <= e;
  });
}

export interface GridOptions {
  reducedMotion: boolean;
}

export class Grid {
  readonly overlay: MapboxOverlay;
  private rows: HexRow[] = [];
  private nRef = 1;
  private mode: 'colour' | 'difference' = 'colour';
  private version = 0;
  private frameId = '';

  constructor(
    private map: maplibregl.Map,
    private opts: GridOptions,
    private onHover: (row: HexRow | null, x: number, y: number) => void,
  ) {
    this.overlay = new MapboxOverlay({
      interleaved: true, // hexes under the basemap's labels
      layers: [],
    });
  }

  setData(rows: HexRow[], nRef: number, frameId: string, mode: 'colour' | 'difference'): void {
    this.rows = rows;
    this.nRef = nRef;
    this.frameId = frameId;
    this.mode = mode;
    this.version += 1;
    this.render();
  }

  private render(): void {
    const nRef = this.nRef;
    const mode = this.mode;
    const layer = new H3HexagonLayer<HexRow>({
      id: 'listening',
      data: this.rows,
      getHexagon: (d) => d.cellHex,
      highPrecision: 'auto',
      extruded: false,
      // The spec's layer config also passes `stitching: true`. deck.gl 9.4's
      // H3HexagonLayer no longer has that prop -- the low-precision path it
      // belonged to was removed -- and `highPrecision: 'auto'` covers the case
      // it existed for. The real library wins.
      filled: true,
      getFillColor: (d) =>
        mode === 'difference'
          ? diffColour(d.dE ?? 0, alphaFor(d.n, nRef))
          : oklabToRgba255(d.L, d.a, d.b, alphaFor(d.n, nRef)),
      pickable: true,
      // Cross-fade frame to frame rather than flicker; off under
      // prefers-reduced-motion.
      transitions: this.opts.reducedMotion ? {} : { getFillColor: 220 },
      updateTriggers: { getFillColor: [this.version, this.frameId, mode] },
      onHover: ({ object, x, y }) => this.onHover((object as HexRow) ?? null, x, y),
      // `beforeId` is a @deck.gl/mapbox interleaved-overlay prop, so it is not
      // on the layer's own prop type; spreading it keeps the rest of the config
      // type-checked while still placing the hexes under the basemap's labels.
      ...({ beforeId: firstLabelLayerId(this.map) } as { beforeId?: string }),
    });
    this.overlay.setProps({ layers: [layer] });
  }
}

/** Coarse colour of a cell from its children, for the "is the average real?"
 *  check in the About panel and for the hover tooltip's parent line. */
export function parentOf(cellHex: string, res: number): string {
  return getResolution(cellHex) <= res ? cellHex : cellToParent(cellHex, res);
}

export function rowStats(rows: HexRow[]): { cells: number; plays: number } {
  let plays = 0;
  for (const r of rows) plays += r.n;
  return { cells: rows.length, plays };
}

export type { Row };
export type { Manifest, Store };
