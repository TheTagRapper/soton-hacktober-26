// Pure rendering logic: zoom -> resolution, cell composition, alpha, viewport
// filtering. Kept free of deck.gl and MapLibre imports so it can be tested in
// Node against the real shard files (tests/test_site.mjs) -- the composition
// maths is where a wrong map would come from, and it should not need a GPU to
// check.

import { deltaE, type Lab } from './colour';
import { cellCentre, resForZoom } from './cells';
import { GEO_CODE, type Frame } from './shards';
import type { Geo } from './state';

export { resForZoom, ZOOM_TO_RES } from './cells';

/** Hysteresis: only switch resolution once the zoom has moved more than 0.6,
 *  otherwise a viewer hovering on a boundary gets a strobing grid. */
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
  cell: bigint;
  lat: number;
  lon: number;
  res: number;
  L: number;
  a: number;
  b: number;
  n: number;
  nUsers: number;
  geo: number;
  dE?: number;
}

/** Alpha carries confidence: sparse cells faint, well-evidenced cells solid.
 *  This is how the map tells the truth about its own coverage without needing
 *  a separate chart. */
export function alphaFor(n: number, nRef: number): number {
  return 0.25 + 0.75 * Math.min(1, Math.log1p(n) / Math.log1p(Math.max(2, nRef)));
}

/** Re-aggregate across the selected precision classes with the same
 *  count-weighted Oklab mean the pyramid used (spec section 7.4). */
export function composeFrame(frame: Frame, geo: Geo[]): HexRow[] {
  const allow = new Set(geo.map((g) => GEO_CODE[g]));
  const acc = new Map<
    bigint,
    { n: number; nu: number; L: number; a: number; b: number; geo: number }
  >();
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
    const [lat, lon] = cellCentre(cell);
    out.push({
      cell,
      lat,
      lon,
      res: frame.res,
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
  const byCell = new Map(features.map((r) => [r.cell, r]));
  const out: HexRow[] = [];
  for (const m of model) {
    const f = byCell.get(m.cell);
    if (!f) continue;
    const p: Lab = { L: m.L, a: m.a, b: m.b };
    const q: Lab = { L: f.L, a: f.a, b: f.b };
    out.push({ ...m, dE: deltaE(p, q) });
  }
  return out;
}

export interface Bounds {
  south: number;
  north: number;
  west: number;
  east: number;
}

/** Filter after decode, with a 25% margin. Whole frames at a resolution are
 *  small and the CDN caches them across all visitors, so we never fetch
 *  per-viewport slices. */
export function filterToBounds(rows: HexRow[], b: Bounds): HexRow[] {
  const latPad = (b.north - b.south) * 0.25;
  const lonPad = (b.east - b.west) * 0.25;
  const s = b.south - latPad;
  const n = b.north + latPad;
  const w = b.west - lonPad;
  const e = b.east + lonPad;
  const wraps = e - w >= 360;
  return rows.filter((r) => {
    if (r.lat < s || r.lat > n) return false;
    if (wraps) return true;
    const shifted = r.lon < w ? r.lon + 360 : r.lon > e ? r.lon - 360 : r.lon;
    return shifted >= w && shifted <= e;
  });
}

export function rowStats(rows: HexRow[]): { cells: number; plays: number } {
  let plays = 0;
  for (const r of rows) plays += r.n;
  return { cells: rows.length, plays };
}

const DIFF_MAX = 0.35; // Oklab delta-E at the top of the difference ramp

/** Single-hue sequential ramp for the difference view, stated in the legend
 *  because in that mode the fill is no longer the music's colour. */
export function diffRampLab(dE: number): Lab {
  const t = Math.min(1, Math.max(0, dE / DIFF_MAX));
  const L = 0.95 - 0.62 * t;
  const C = 0.02 + 0.13 * t;
  const h = (295 * Math.PI) / 180;
  return { L, a: C * Math.cos(h), b: C * Math.sin(h) };
}
