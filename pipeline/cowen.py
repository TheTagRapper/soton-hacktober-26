"""The emotion palette, from Cowen & Keltner's map of music-evoked emotion.

Source: Alan Cowen's interactive map at
https://www.ocf.berkeley.edu/~acowen/music.html, accompanying

    Cowen, A. S., Fang, X., Sauter, D., & Keltner, D. (2020).
    What music makes us feel: At least 13 dimensions organize subjective
    experiences associated with music across different cultures.
    PNAS 117(4), 1924-1934.

The thirteen categories and the exact sRGB values that map uses for them are
reproduced below, read off that page rather than invented here. On Cowen's map
each clip is tinted by a blend of these colours weighted by how strongly
listeners reported each feeling; this project does the same thing to a song,
and then averages songs together geographically.

Those blends are computed in **Oklab**, not sRGB: a weighted mean of saturated
sRGB primaries goes muddy and shifts hue, and the whole pyramid in section 7
depends on the averaging operation being a Euclidean perceptual mean. The
palette is converted to Oklab once, here, at import.
"""

from __future__ import annotations

from pipeline.oklab import Lab, hex_to_oklab

# label -> (sRGB hex, letter on Cowen's map)
PALETTE_SRGB: dict[str, tuple[str, str]] = {
    "amusing":     ("#0000ff", "A"),
    "annoying":    ("#ff0000", "B"),
    "anxious":     ("#ffd300", "C"),  # anxious, tense
    "beautiful":   ("#ffc184", "D"),
    "calm":        ("#ff1ab9", "E"),  # calm, relaxing, serene
    "dreamy":      ("#00ff00", "F"),
    "energizing":  ("#d312ff", "G"),  # energizing, pump-up
    "erotic":      ("#8484ff", "H"),  # erotic, desirous
    "indignant":   ("#f69edc", "I"),  # indignant, defiant
    "joyful":      ("#00ffc1", "J"),  # joyful, cheerful
    "sad":         ("#008495", "K"),  # sad, depressing
    "scary":       ("#cccc33", "L"),  # scary, fearful
    "triumphant":  ("#9e4f46", "M"),  # triumphant, heroic
}

EMOTIONS: tuple[str, ...] = tuple(PALETTE_SRGB)

LABELS: dict[str, str] = {
    "amusing": "amusing",
    "annoying": "annoying",
    "anxious": "anxious, tense",
    "beautiful": "beautiful",
    "calm": "calm, relaxing, serene",
    "dreamy": "dreamy",
    "energizing": "energizing, pump-up",
    "erotic": "erotic, desirous",
    "indignant": "indignant, defiant",
    "joyful": "joyful, cheerful",
    "sad": "sad, depressing",
    "scary": "scary, fearful",
    "triumphant": "triumphant, heroic",
}

PALETTE_OKLAB: dict[str, Lab] = {
    name: Lab(*hex_to_oklab(hexv)) for name, (hexv, _letter) in PALETTE_SRGB.items()
}

# A track whose emotion weights are all ~0 has no defensible colour. Rather
# than painting it grey and pretending, it is dropped (see colour_table.py).
MIN_WEIGHT_SUM = 0.15

# Several of these thirteen colours are near-complementary (joyful is a
# cyan-green, calm a magenta), so a flat weighted mean of a track that scores
# moderately on many emotions lands on grey -- and then geographic averaging
# greys it further, which would make the map a field of mud regardless of what
# anyone was listening to. Weights are therefore raised to this exponent before
# blending, which lets the emotions a listener would actually name dominate the
# ones that merely registered. It is a deliberate, documented choice, applied
# identically in site/src/colour.ts; set it to 1.0 for the flat mean.
SHARPEN = 2.5


def sharpen(weights: dict[str, float]) -> dict[str, float]:
    return {k: (max(0.0, v) ** SHARPEN) for k, v in weights.items()}


def blend(weights: dict[str, float]) -> tuple[float, float, float] | None:
    """Intensity-weighted mean of the palette, in Oklab.

    Returns None when the weights carry no signal, so the caller can exclude
    the track rather than guess at it.
    """
    if sum(max(0.0, w) for w in weights.values()) < MIN_WEIGHT_SUM:
        return None
    sharp = sharpen(weights)
    total = 0.0
    L = a = b = 0.0
    for name, w in sharp.items():
        pal = PALETTE_OKLAB.get(name)
        if pal is None or w <= 0:
            continue
        total += w
        L += pal.L * w
        a += pal.a * w
        b += pal.b * w
    if total <= 0:
        return None
    return L / total, a / total, b / total


def dominant(weights: dict[str, float]) -> str | None:
    best, best_w = None, 0.0
    for name in EMOTIONS:
        w = weights.get(name, 0.0)
        if w > best_w:
            best, best_w = name, w
    return best
