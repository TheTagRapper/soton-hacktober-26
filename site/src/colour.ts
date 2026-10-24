// Port of pipeline/oklab.py. Spec sections 6.6 and 6.7 are LOCKED.
// tests/test_oklab.py asserts this file and the Python agree to 1e-9 over a
// 500-point shared fixture; if you change a number here, change it there.

export interface Lab {
  L: number;
  a: number;
  b: number;
}

export function judgementToOklch(
  valence: number,
  arousal: number,
  warmth: number,
  density: number,
  grain: number,
): [number, number, number] {
  const h = (((210.0 - 150.0 * valence) % 360.0) + 360.0) % 360.0;
  const C = 0.02 + 0.13 * (0.65 * arousal + 0.35 * density);
  const L = 0.38 + 0.34 * warmth - 0.1 * grain;
  return [L, C, h];
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

export function judgementToOklab(
  valence: number,
  arousal: number,
  warmth: number,
  density: number,
  grain: number,
): Lab {
  const [L, C, h] = judgementToOklch(valence, arousal, warmth, density, grain);
  return oklchToOklab(L, C, h);
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
