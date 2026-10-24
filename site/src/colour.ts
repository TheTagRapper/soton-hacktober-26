// Port of pipeline/oklab.py. Spec sections 6.6 and 6.7 are LOCKED.
// tests/test_oklab.py asserts this file and the Python agree to 1e-9 over a
// 500-point shared fixture; if you change a number here, change it there.

export interface Lab {
  L: number;
  a: number;
  b: number;
}

export function oklchToOklab(L: number, C: number, hDeg: number): Lab {
  const rad = (hDeg * Math.PI) / 180.0;
  return { L, a: C * Math.cos(rad), b: C * Math.sin(rad) };
}

export function oklabToOklch(L: number, a: number, b: number): [number, number, number] {
  const C = Math.hypot(a, b);
  const h = (((Math.atan2(b, a) * 180.0) / Math.PI) % 360.0 + 360.0) % 360.0;
  return [L, C, h];
}

// --- sRGB -> Oklab, for the fixed palette --------------------------------

export function srgbToLinear(c: number): number {
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

export function srgbToOklab(r: number, g: number, b: number): Lab {
  const lr = srgbToLinear(r);
  const lg = srgbToLinear(g);
  const lb = srgbToLinear(b);
  const l = 0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb;
  const m = 0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb;
  const s = 0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb;
  const cbrt = (v: number) => Math.sign(v) * Math.pow(Math.abs(v), 1 / 3);
  const l_ = cbrt(l);
  const m_ = cbrt(m);
  const s_ = cbrt(s);
  return {
    L: 0.2104542553 * l_ + 0.793617785 * m_ - 0.0040720468 * s_,
    a: 1.9779984951 * l_ - 2.428592205 * m_ + 0.4505937099 * s_,
    b: 0.0259040371 * l_ + 0.7827717662 * m_ - 0.808675766 * s_,
  };
}

export function hexToOklab(hex: string): Lab {
  const h = hex.replace('#', '');
  return srgbToOklab(
    parseInt(h.slice(0, 2), 16) / 255,
    parseInt(h.slice(2, 4), 16) / 255,
    parseInt(h.slice(4, 6), 16) / 255,
  );
}

// --- the Cowen emotion palette -------------------------------------------
//
// Thirteen dimensions of music-evoked emotion and the colours they are drawn
// in, from Cowen, Fang, Sauter & Keltner, PNAS 2020, as published at
// https://www.ocf.berkeley.edu/~acowen/music.html. Mirrors pipeline/cowen.py;
// tests/test_oklab.py asserts the two blend identically.

export const EMOTIONS = [
  'amusing', 'annoying', 'anxious', 'beautiful', 'calm', 'dreamy',
  'energizing', 'erotic', 'indignant', 'joyful', 'sad', 'scary', 'triumphant',
] as const;

export type Emotion = (typeof EMOTIONS)[number];

export const PALETTE_SRGB: Record<Emotion, string> = {
  amusing: '#0000ff',
  annoying: '#ff0000',
  anxious: '#ffd300',
  beautiful: '#ffc184',
  calm: '#ff1ab9',
  dreamy: '#00ff00',
  energizing: '#d312ff',
  erotic: '#8484ff',
  indignant: '#f69edc',
  joyful: '#00ffc1',
  sad: '#008495',
  scary: '#cccc33',
  triumphant: '#9e4f46',
};

export const EMOTION_LABELS: Record<Emotion, string> = {
  amusing: 'amusing',
  annoying: 'annoying',
  anxious: 'anxious, tense',
  beautiful: 'beautiful',
  calm: 'calm, relaxing, serene',
  dreamy: 'dreamy',
  energizing: 'energizing, pump-up',
  erotic: 'erotic, desirous',
  indignant: 'indignant, defiant',
  joyful: 'joyful, cheerful',
  sad: 'sad, depressing',
  scary: 'scary, fearful',
  triumphant: 'triumphant, heroic',
};

export const PALETTE_OKLAB: Record<Emotion, Lab> = Object.fromEntries(
  EMOTIONS.map((e) => [e, hexToOklab(PALETTE_SRGB[e])]),
) as Record<Emotion, Lab>;

/** See pipeline/cowen.py: several palette entries are near-complementary, so a
 *  flat mean of a track scoring moderately on many emotions lands on grey and
 *  geographic averaging then greys it further. Weights are sharpened first. */
export const SHARPEN = 2.5;

export const MIN_WEIGHT_SUM = 0.15;

export function blendEmotions(weights: Partial<Record<Emotion, number>>): Lab | null {
  let raw = 0;
  for (const e of EMOTIONS) raw += Math.max(0, weights[e] ?? 0);
  if (raw < MIN_WEIGHT_SUM) return null;
  let total = 0;
  let L = 0;
  let a = 0;
  let b = 0;
  for (const e of EMOTIONS) {
    const w = Math.pow(Math.max(0, weights[e] ?? 0), SHARPEN);
    if (w <= 0) continue;
    const pal = PALETTE_OKLAB[e];
    total += w;
    L += pal.L * w;
    a += pal.a * w;
    b += pal.b * w;
  }
  if (total <= 0) return null;
  return { L: L / total, a: a / total, b: b / total };
}

export function oklabToLinearSrgb(L: number, a: number, b: number): [number, number, number] {
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ * l_ * l_;
  const m = m_ * m_ * m_;
  const s = s_ * s_ * s_;
  return [
    +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
}

export function linearToSrgb(c: number): number {
  return c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1.0 / 2.4) - 0.055;
}

export function inGamut(L: number, a: number, b: number): boolean {
  const lin = oklabToLinearSrgb(L, a, b);
  return lin.every((v) => v >= -1e-9 && v <= 1.0 + 1e-9);
}

/** Reduce chroma until in gamut, holding L and h. Never clip channels: clipping shifts hue. */
export function clampChroma(L: number, a: number, b: number): Lab {
  if (inGamut(L, a, b)) return { L, a, b };
  let lo = 0.0;
  let hi = 1.0;
  for (let i = 0; i < 12; i++) {
    const mid = (lo + hi) / 2.0;
    if (inGamut(L, a * mid, b * mid)) lo = mid;
    else hi = mid;
  }
  return { L, a: a * lo, b: b * lo };
}

export function oklabToSrgb255(L: number, a: number, b: number): [number, number, number] {
  const c = clampChroma(L, a, b);
  const lin = oklabToLinearSrgb(c.L, c.a, c.b);
  const out = lin.map((v) => {
    const s = linearToSrgb(Math.min(1.0, Math.max(0.0, v)));
    return Math.round(Math.min(1.0, Math.max(0.0, s)) * 255.0);
  });
  return [out[0], out[1], out[2]];
}

export function oklabToRgba255(
  L: number,
  a: number,
  b: number,
  alpha: number,
): [number, number, number, number] {
  const [r, g, bb] = oklabToSrgb255(L, a, b);
  return [r, g, bb, Math.round(Math.min(1, Math.max(0, alpha)) * 255)];
}

export function oklabToHex(L: number, a: number, b: number): string {
  const [r, g, b2] = oklabToSrgb255(L, a, b);
  const h = (v: number) => v.toString(16).padStart(2, '0');
  return `#${h(r)}${h(g)}${h(b2)}`;
}

/** Count-weighted component-wise mean in Oklab. The only correct average here. */
export function weightedMean(items: Array<{ lab: Lab; w: number }>): Lab {
  let tw = 0;
  let L = 0;
  let a = 0;
  let b = 0;
  for (const { lab, w } of items) {
    tw += w;
    L += lab.L * w;
    a += lab.a * w;
    b += lab.b * w;
  }
  if (tw <= 0) return { L: 0, a: 0, b: 0 };
  return { L: L / tw, a: a / tw, b: b / tw };
}

export function deltaE(p: Lab, q: Lab): number {
  return Math.sqrt((p.L - q.L) ** 2 + (p.a - q.a) ** 2 + (p.b - q.b) ** 2);
}
