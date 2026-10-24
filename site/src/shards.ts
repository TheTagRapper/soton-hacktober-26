// Shard fetch + Arrow decode + LRU cache (spec sections 8, 9, 10.3).
//
// No DuckDB-WASM in v1: after suppression the whole output is small, and
// pre-sharded files fetched directly are simpler, CDN-cacheable across all
// visitors (which matters precisely because everyone sees the same data), and
// avoid the duckdb-wasm bug class where range requests silently become
// whole-file downloads.

import { tableFromIPC } from 'apache-arrow';
import type { Axis, Geo, Manifest } from './state';

export interface Row {
  cell: bigint;
  n: number;
  nUsers: number;
  geo: number;
  L: number;
  a: number;
  b: number;
}

export interface Frame {
  key: string;
  res: number;
  rows: Row[];
  /** 95th percentile of n, the alpha reference for this frame. */
  nRef: number;
}

export const GEO_CODE: Record<Geo, number> = {
  point: 0,
  place_centroid: 1,
  city: 2,
};

const CAPACITY = 24;

export class ShardStore {
  private lru = new Map<string, Frame>();
  private inflight = new Map<string, Promise<Frame | null>>();
  private missing = new Set<string>();

  constructor(
    private base: string,
    private manifest: Manifest,
  ) {}

  key(axis: Axis, res: number, frame: string, baseline: boolean): string {
    return `${baseline ? 'baseline/' : ''}${axis}/r${res}/${frame}`;
  }

  get(axis: Axis, res: number, frame: string, baseline = false): Frame | undefined {
    const k = this.key(axis, res, frame, baseline);
    const hit = this.lru.get(k);
    if (hit) {
      // Touch: re-insert to move to the end of the eviction order.
      this.lru.delete(k);
      this.lru.set(k, hit);
    }
    return hit;
  }

  isKnownMissing(axis: Axis, res: number, frame: string, baseline = false): boolean {
    return this.missing.has(this.key(axis, res, frame, baseline));
  }

  isLoading(axis: Axis, res: number, frame: string, baseline = false): boolean {
    return this.inflight.has(this.key(axis, res, frame, baseline));
  }

  async load(axis: Axis, res: number, frame: string, baseline = false): Promise<Frame | null> {
    const k = this.key(axis, res, frame, baseline);
    const cached = this.get(axis, res, frame, baseline);
    if (cached) return cached;
    if (this.missing.has(k)) return null;
    const pending = this.inflight.get(k);
    if (pending) return pending;

    const p = (async () => {
      const url = `${this.base}/${k}.arrow`;
      try {
        // Shards are immutable per data version, so force-cache is correct and
        // makes repeat visits and playback free.
        const res0 = await fetch(url, { cache: 'force-cache' });
        if (!res0.ok) {
          this.missing.add(k);
          return null;
        }
        const buf = await res0.arrayBuffer();
        const frameObj = decode(k, buf, res);
        this.put(k, frameObj);
        return frameObj;
      } catch {
        this.missing.add(k);
        return null;
      } finally {
        this.inflight.delete(k);
      }
    })();
    this.inflight.set(k, p);
    return p;
  }

  private put(k: string, f: Frame): void {
    this.lru.set(k, f);
    while (this.lru.size > CAPACITY) {
      const oldest = this.lru.keys().next().value as string | undefined;
      if (oldest === undefined) break;
      this.lru.delete(oldest);
    }
  }

  /** Preload every frame of one axis at one resolution. Cheap at r1/r2. */
  async preload(axis: Axis, res: number): Promise<void> {
    const frames = this.manifest.axes[axis]?.frames ?? [];
    await Promise.all(frames.map((f) => this.load(axis, res, f)));
  }
}

function decode(key: string, buf: ArrayBuffer, res: number): Frame {
  const table = tableFromIPC(new Uint8Array(buf));
  const cell = table.getChild('cell')!;
  const n = table.getChild('n')!;
  const nu = table.getChild('n_users_ub')!;
  const geo = table.getChild('geo')!;
  const L = table.getChild('L')!;
  const a = table.getChild('a')!;
  const b = table.getChild('b')!;
  const rows: Row[] = new Array(table.numRows);
  for (let i = 0; i < table.numRows; i++) {
    rows[i] = {
      cell: cell.get(i) as bigint,
      n: Number(n.get(i)),
      nUsers: Number(nu.get(i)),
      geo: Number(geo.get(i)),
      L: Number(L.get(i)),
      a: Number(a.get(i)),
      b: Number(b.get(i)),
    };
  }
  return { key, res, rows, nRef: percentile(rows.map((r) => r.n), 95) };
}

export function percentile(values: number[], p: number): number {
  if (!values.length) return 1;
  const s = [...values].sort((x, y) => x - y);
  const i = Math.min(s.length - 1, Math.max(0, Math.round((p / 100) * (s.length - 1))));
  return s[i] || 1;
}
