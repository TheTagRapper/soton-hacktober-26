// Scrubber, playback, frame model (spec section 10.4).

import type { Axis, Manifest, Store } from './state';

export function frameLabel(axis: Axis, frame: string, manifest: Manifest): string {
  if (axis === 'solar') return `${frame}:00`;
  if (axis === 'dow') {
    const labels = manifest.dow_labels ?? ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    return labels[Number(frame)] ?? frame;
  }
  const [y, m] = frame.split('-');
  const months = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];
  return `${months[Number(m) - 1] ?? m} ${y}`;
}

/** A sun glyph whose position tracks the solar hour, for the solar axis. */
export function sunGlyph(frame: string): string {
  const hour = Number(frame);
  const day = hour >= 6 && hour <= 18;
  return day ? '☀' : '☾';
}

const BASE_INTERVAL_MS = 900;

export class Playback {
  private timer: number | undefined;

  constructor(
    private store: Store,
    private reducedMotion: boolean,
  ) {}

  get playing(): boolean {
    return this.timer !== undefined;
  }

  toggle(): void {
    if (this.playing) this.pause();
    else this.play();
  }

  play(): void {
    // Under prefers-reduced-motion, autoplay is disabled; the scrubber still
    // works (spec section 14.9).
    if (this.reducedMotion || this.playing) return;
    this.store.patch({ playing: true });
    const tick = () => {
      this.store.stepFrame(1);
      this.timer = window.setTimeout(tick, BASE_INTERVAL_MS / this.store.state.speed);
    };
    this.timer = window.setTimeout(tick, BASE_INTERVAL_MS / this.store.state.speed);
  }

  pause(): void {
    if (this.timer !== undefined) window.clearTimeout(this.timer);
    this.timer = undefined;
    this.store.patch({ playing: false });
  }

  /** Frames worth prefetching: the current one plus the next two while playing. */
  lookahead(): string[] {
    const frames = this.store.frames();
    if (!frames.length) return [];
    const i = this.store.frameIndex();
    const out = [frames[i]];
    if (this.playing) {
      out.push(frames[(i + 1) % frames.length], frames[(i + 2) % frames.length]);
    }
    return out;
  }
}
