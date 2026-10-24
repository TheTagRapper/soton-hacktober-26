// URL-hash-backed view state (spec section 10.5).
//
// The data is identical for every visitor because it is static files on a CDN.
// Everything in here is per-visitor and lives only in the hash, so any view is
// shareable by copying the address bar.
//
// Deliberately structured so that an external frame-index source could drive
// `frame` (see setFrame / subscribe): the spec's out-of-scope "synchronised
// time between visitors" would be one Durable Object calling setFrame, with
// nothing else in the app changing. It is not built here.

export type Axis = 'solar' | 'chrono' | 'dow';
export type Source = 'model' | 'features' | 'difference';
export type Geo = 'point' | 'place_centroid' | 'city';
export type Basemap = 'dark' | 'light';

export interface ViewState {
  axis: Axis;
  frame: string;
  src: Source;
  geo: Geo[];
  z: number;
  lat: number;
  lon: number;
  playing: boolean;
  speed: number;
  base: Basemap;
}

export const DEFAULTS: ViewState = {
  axis: 'solar', // the view that justifies the whole project
  frame: '21',
  src: 'model',
  geo: ['point'], // point only by default: a place_centroid piles a city onto one hex
  z: 1.4,
  lat: 20,
  lon: 0,
  playing: false,
  speed: 1,
  base: 'dark', // the colour field is the subject; it reads against black
};

const AXES: Axis[] = ['solar', 'chrono', 'dow'];
const SOURCES: Source[] = ['model', 'features', 'difference'];
const GEOS: Geo[] = ['point', 'place_centroid', 'city'];
const SPEEDS = [1, 2, 4];
const BASEMAPS: Basemap[] = ['dark', 'light'];

type Listener = (s: ViewState, changed: Set<keyof ViewState>) => void;

export interface Manifest {
  built_at: string;
  data_version: string;
  model: string;
  backend?: string;
  axes: Record<string, { frames: string[]; label: string }>;
  resolutions: number[];
  base_res: number;
  max_useful_res: number;
  cell_radius_m?: Record<string, number>;
  cells_per_res: Record<string, number>;
  cells_per_res_per_axis?: Record<string, Record<string, number>>;
  has_baseline?: boolean;
  n_events_total: number;
  n_events_coloured?: number;
  n_events_suppressed: number;
  n_tracks_total?: number;
  n_tracks_coloured: number;
  n_tracks_artist_fallback?: number;
  n_tracks_uncoloured: number;
  suppression?: { min_n: number; min_n_users_ub: number; user_cap_per_cell_frame: number };
  colour_model?: {
    palette: string;
    blend: string;
    emotions: Record<string, string>;
    labels: Record<string, string>;
  };
  grid?: { kind: string; drawn_as: string };
  dow_labels?: string[];
  sources?: Array<{ name: string; citation?: string; url: string; licence: string }>;
  n_notes?: number;
}

export class Store {
  state: ViewState;
  private listeners: Listener[] = [];
  private writeTimer: number | undefined;

  constructor(private manifest: Manifest) {
    this.state = this.parse();
  }

  /** Parse the hash, validating every field against the manifest (section 10.5). */
  private parse(): ViewState {
    const out: ViewState = { ...DEFAULTS };
    const frames = this.manifest.axes[DEFAULTS.axis]?.frames ?? [];
    out.frame = frames.includes(DEFAULTS.frame) ? DEFAULTS.frame : (frames[0] ?? '00');

    const raw = location.hash.replace(/^#/, '');
    if (!raw) return out;
    const p = new URLSearchParams(raw);

    const axis = p.get('axis');
    if (axis && (AXES as string[]).includes(axis) && this.manifest.axes[axis]) {
      out.axis = axis as Axis;
      out.frame = this.manifest.axes[axis].frames[0] ?? out.frame;
    }
    const frame = p.get('frame');
    if (frame && this.manifest.axes[out.axis]?.frames.includes(frame)) out.frame = frame;

    const src = p.get('src');
    if (src && (SOURCES as string[]).includes(src)) {
      const wantsBaseline = src === 'features' || src === 'difference';
      out.src = wantsBaseline && !this.manifest.has_baseline ? 'model' : (src as Source);
    }
    const geo = p.get('geo');
    if (geo) {
      const picked = geo.split(',').filter((g): g is Geo => (GEOS as string[]).includes(g));
      if (picked.length) out.geo = picked;
    }
    const num = (k: string, lo: number, hi: number, dflt: number) => {
      const v = Number(p.get(k));
      return Number.isFinite(v) && v >= lo && v <= hi ? v : dflt;
    };
    out.z = num('z', 0, 16, DEFAULTS.z);
    out.lat = num('lat', -85, 85, DEFAULTS.lat);
    out.lon = num('lon', -180, 180, DEFAULTS.lon);
    out.playing = p.get('playing') === '1';
    const speed = Number(p.get('speed'));
    out.speed = SPEEDS.includes(speed) ? speed : DEFAULTS.speed;
    const base = p.get('base');
    if (base && (BASEMAPS as string[]).includes(base)) out.base = base as Basemap;
    return out;
  }

  /** Throttled replaceState, so playback does not fill the history stack. */
  private write(): void {
    if (this.writeTimer !== undefined) return;
    this.writeTimer = window.setTimeout(() => {
      this.writeTimer = undefined;
      const s = this.state;
      const p = new URLSearchParams({
        axis: s.axis,
        frame: s.frame,
        src: s.src,
        geo: s.geo.join(','),
        z: s.z.toFixed(2),
        lat: s.lat.toFixed(4),
        lon: s.lon.toFixed(4),
        playing: s.playing ? '1' : '0',
      });
      if (s.speed !== 1) p.set('speed', String(s.speed));
      if (s.base !== DEFAULTS.base) p.set('base', s.base);
      history.replaceState(null, '', `#${p.toString()}`);
    }, 250);
  }

  subscribe(fn: Listener): () => void {
    this.listeners.push(fn);
    return () => {
      this.listeners = this.listeners.filter((f) => f !== fn);
    };
  }

  patch(partial: Partial<ViewState>): void {
    const changed = new Set<keyof ViewState>();
    for (const [k, v] of Object.entries(partial) as Array<[keyof ViewState, never]>) {
      if (k === 'geo') {
        if ((this.state.geo as string[]).join(',') !== (v as unknown as string[]).join(',')) {
          this.state.geo = v as unknown as Geo[];
          changed.add(k);
        }
        continue;
      }
      if (this.state[k] !== v) {
        (this.state as unknown as Record<string, unknown>)[k] = v;
        changed.add(k);
      }
    }
    if (!changed.size) return;
    this.write();
    for (const fn of this.listeners) fn(this.state, changed);
  }

  frames(): string[] {
    return this.manifest.axes[this.state.axis]?.frames ?? [];
  }

  frameIndex(): number {
    return Math.max(0, this.frames().indexOf(this.state.frame));
  }

  /** The single entry point a synchronised-time source would call. */
  setFrame(frame: string): void {
    if (this.frames().includes(frame)) this.patch({ frame });
  }

  stepFrame(delta: number): void {
    const f = this.frames();
    if (!f.length) return;
    const i = (this.frameIndex() + delta + f.length) % f.length;
    this.patch({ frame: f[i] });
  }
}
