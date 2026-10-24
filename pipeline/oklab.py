"""Colour-space maths. Ported verbatim to site/src/colour.ts.

Spec sections 6.6 and 6.7 are LOCKED. Every number in this file is from the
spec; do not retune without retuning the TypeScript port and regenerating
tests/fixtures/oklab_vectors.json.

All averaging happens on (L, a, b) in Oklab. Never on sRGB, never on hue
angles. Oklab is Euclidean and perceptual, so a count-weighted component-wise
mean is both correct and associative -- and that associativity is exactly what
makes the zoom pyramid in section 7.4 valid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# --- section 6.6, LOCKED -----------------------------------------------------


def judgement_to_oklch(
    valence: float, arousal: float, warmth: float, density: float, grain: float
) -> tuple[float, float, float]:
    """Five perceptual judgements -> (L, C, h in degrees)."""
    h = (210.0 - 150.0 * valence) % 360.0
    c = 0.02 + 0.13 * (0.65 * arousal + 0.35 * density)
    lightness = 0.38 + 0.34 * warmth - 0.10 * grain
    return lightness, c, h


def oklch_to_oklab(L: float, C: float, h_deg: float) -> tuple[float, float, float]:
    rad = h_deg * math.pi / 180.0
    return L, C * math.cos(rad), C * math.sin(rad)


def oklab_to_oklch(L: float, a: float, b: float) -> tuple[float, float, float]:
    c = math.hypot(a, b)
    h = math.degrees(math.atan2(b, a)) % 360.0
    return L, c, h


def judgement_to_oklab(
    valence: float, arousal: float, warmth: float, density: float, grain: float
) -> tuple[float, float, float]:
    return oklch_to_oklab(*judgement_to_oklch(valence, arousal, warmth, density, grain))


# --- section 6.7, LOCKED -----------------------------------------------------


def oklab_to_linear_srgb(L: float, a: float, b: float) -> tuple[float, float, float]:
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l = l_ * l_ * l_
    m = m_ * m_ * m_
    s = s_ * s_ * s_
    return (
        +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )


def linear_to_srgb(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055


def in_gamut(L: float, a: float, b: float) -> bool:
    return all(-1e-9 <= v <= 1.0 + 1e-9 for v in oklab_to_linear_srgb(L, a, b))


def clamp_chroma(L: float, a: float, b: float) -> tuple[float, float, float]:
    """Reduce chroma until in gamut, holding L and h.

    Binary-search a multiplier on C in [0,1] for 12 iterations. Do not clip
    channels -- clipping shifts hue.
    """
    if in_gamut(L, a, b):
        return L, a, b
    lo, hi = 0.0, 1.0
    for _ in range(12):
        mid = (lo + hi) / 2.0
        if in_gamut(L, a * mid, b * mid):
            lo = mid
        else:
            hi = mid
    return L, a * lo, b * lo


def oklab_to_srgb255(L: float, a: float, b: float) -> tuple[int, int, int]:
    L2, a2, b2 = clamp_chroma(L, a, b)
    out = []
    for lin in oklab_to_linear_srgb(L2, a2, b2):
        v = linear_to_srgb(min(1.0, max(0.0, lin)))
        out.append(int(round(min(1.0, max(0.0, v)) * 255.0)))
    return out[0], out[1], out[2]


def oklab_to_hex(L: float, a: float, b: float) -> str:
    r, g, bb = oklab_to_srgb255(L, a, b)
    return f"#{r:02x}{g:02x}{bb:02x}"


# --- aggregation -------------------------------------------------------------


@dataclass(frozen=True)
class Lab:
    L: float
    a: float
    b: float


def weighted_mean(items: list[tuple[Lab, float]]) -> Lab:
    """Count-weighted component-wise mean in Oklab. Associative by construction."""
    total = sum(w for _, w in items)
    if total <= 0:
        return Lab(0.0, 0.0, 0.0)
    return Lab(
        sum(c.L * w for c, w in items) / total,
        sum(c.a * w for c, w in items) / total,
        sum(c.b * w for c, w in items) / total,
    )


def delta_e(p: Lab, q: Lab) -> float:
    """Euclidean distance in Oklab -- the honest delta-E for this space."""
    return math.sqrt((p.L - q.L) ** 2 + (p.a - q.a) ** 2 + (p.b - q.b) ** 2)
