"""Original silhouette plates for the bestiary (issue #193).

Creature art online is almost entirely copyrighted, so none is used here.
Every shape below is drawn for this repo: eleven flat silhouettes, one per
category, on a 100x100 field.

A plate is not a portrait. It says three true things about the beast and
nothing it cannot know:

* **what kind of thing it is** — the category silhouette;
* **how much table it takes** — the silhouette's scale in its frame comes
  from ``size_hexes``, so a seven-hex figure fills the plate and a one-hex
  one sits small in it;
* **which entry this is** — the ground tint, the ink shade and the facing
  are a stable function of the slug, so a beast looks the same on every
  render and no two neighbours in a category look alike.

That last part is identity, not classification: the tint does not mean
anything about the creature, and the docstring says so rather than letting a
reader infer a meaning that is not there.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

# The category palettes, as (ground hue, saturation, lightness) anchors in
# HSL. Legendary borrows the site's gold; the rest are chosen to stay
# distinguishable from each other at card size.
_PALETTES: dict[str, tuple[int, int, int]] = {
    "insects_vermin": (35, 45, 62),
    "mammals": (25, 35, 58),
    "birds": (210, 30, 60),
    "reptiles_amphibians": (85, 30, 52),
    "sea_creatures": (185, 35, 55),
    "dragons": (355, 40, 55),
    "elementals": (275, 32, 60),
    "undead_spirits": (160, 14, 52),
    "plants": (120, 32, 50),
    "constructs": (30, 8, 55),
    "legendary": (45, 55, 58),
}

_FALLBACK_PALETTE = (0, 0, 55)


def _ellipse(cx: float, cy: float, rx: float, ry: float) -> str:
    """A closed ellipse as two half-arcs.

    The one-arc shorthand (``a rx ry 0 1 0 0.1 0``) is ambiguous about where
    the centre lands, which is what scattered the first draft's heads and
    bodies across their plates. Two explicit half-arcs are not.
    """
    return f"M{cx - rx} {cy}a{rx} {ry} 0 1 0 {2 * rx} 0a{rx} {ry} 0 1 0 {-2 * rx} 0z"


def _polygon(*points: tuple[float, float]) -> str:
    """A closed straight-sided shape."""
    (first_x, first_y), *rest = points
    return f"M{first_x} {first_y}" + "".join(f"L{x} {y}" for x, y in rest) + "z"


def _stroke(*segments: tuple[tuple[float, float], tuple[float, float]]) -> str:
    """Open lines — legs, antennae, branches — drawn by stroke alone."""
    return "".join(f"M{ax} {ay}L{bx} {by}" for (ax, ay), (bx, by) in segments)


# One silhouette per category, massed around (50, 58) on a 100x100 field.
# Each is a pair: a **body** of closed shapes that get filled, and
# **details** of open lines that only get stroked. They are separate because
# filling an open subpath closes it implicitly, which flattened the first
# draft's birds and dragons into slivers.
#
# The forms stay simple deliberately: they are read at 64-120px on a card,
# where detail turns to mud. Every one is drawn for this repo.
_SILHOUETTES: dict[str, tuple[tuple[str, ...], str]] = {
    # Ant seen from above: head, thorax, abdomen, six legs, two antennae.
    "insects_vermin": (
        (
            _ellipse(50, 26, 8, 7),
            _ellipse(50, 42, 10, 8),
            _ellipse(50, 64, 14, 12),
        ),
        _stroke(
            ((46, 20), (36, 10)),
            ((54, 20), (64, 10)),
            ((41, 40), (24, 32)),
            ((59, 40), (76, 32)),
            ((40, 46), (22, 48)),
            ((60, 46), (78, 48)),
            ((42, 52), (26, 62)),
            ((58, 52), (74, 62)),
        ),
    ),
    # Heavy quadruped facing right: barrel body, ears, snout, short tail.
    "mammals": (
        (
            _ellipse(46, 56, 24, 14),
            _ellipse(74, 50, 12, 10),
            _polygon((82, 50), (95, 53), (82, 58)),
            _polygon((70, 42), (74, 32), (80, 42)),
            _polygon((24, 52), (8, 44), (12, 56)),
        ),
        _stroke(
            ((34, 68), (32, 88)),
            ((44, 70), (43, 90)),
            ((58, 70), (59, 90)),
            ((66, 68), (68, 88)),
        ),
    ),
    # Standing bird facing right: round body, raised neck, beak, tail, legs.
    "birds": (
        (
            _ellipse(46, 60, 19, 17),
            _polygon((48, 50), (56, 28), (66, 30), (58, 52)),
            _ellipse(60, 24, 9, 8),
            _polygon((69, 22), (82, 26), (69, 30)),
            _polygon((29, 56), (9, 48), (13, 62)),
        ),
        _stroke(
            ((42, 76), (40, 90)),
            ((54, 76), (56, 90)),
            ((36, 90), (46, 90)),
            ((52, 90), (62, 90)),
        ),
    ),
    # Long low reptile facing right: slab body, broad snout, ridged back.
    "reptiles_amphibians": (
        (
            _ellipse(46, 58, 30, 11),
            _polygon((70, 52), (90, 50), (96, 58), (90, 66), (70, 64)),
            _polygon((18, 55), (2, 49), (4, 61)),
        ),
        _stroke(
            ((32, 49), (30, 40)),
            ((42, 48), (40, 39)),
            ((52, 47), (50, 38)),
            ((62, 48), (60, 39)),
            ((32, 66), (26, 80)),
            ((44, 68), (41, 82)),
            ((58, 68), (61, 82)),
            ((68, 66), (74, 80)),
        ),
    ),
    # Crab: broad carapace, two claws, eye stalks, walking legs.
    "sea_creatures": (
        (
            _ellipse(50, 58, 24, 17),
            _polygon((30, 48), (14, 38), (5, 44), (12, 52), (4, 56), (16, 62)),
            _polygon((70, 48), (86, 38), (95, 44), (88, 52), (96, 56), (84, 62)),
        ),
        _stroke(
            ((43, 44), (41, 36)),
            ((57, 44), (59, 36)),
            ((34, 72), (26, 85)),
            ((43, 75), (39, 88)),
            ((57, 75), (61, 88)),
            ((66, 72), (74, 85)),
        ),
    ),
    # Winged dragon facing right: barrel body, raised neck, one wing lifted
    # clear behind it, long tail.
    "dragons": (
        (
            _ellipse(46, 68, 20, 12),
            _polygon((56, 62), (70, 40), (82, 36), (86, 44), (74, 50), (64, 66)),
            _polygon((40, 60), (26, 24), (46, 34), (60, 22), (58, 52)),
            _polygon((26, 66), (4, 76), (9, 84), (31, 74)),
        ),
        _stroke(((38, 79), (34, 92)), ((54, 80), (57, 92)), ((80, 40), (90, 34))),
    ),
    # Elemental: a column of matter tapering out of a broad base, no limbs.
    "elementals": (
        (
            _polygon(
                (34, 86),
                (38, 50),
                (44, 24),
                (50, 10),
                (56, 24),
                (62, 50),
                (66, 86),
            ),
            _ellipse(50, 84, 20, 10),
        ),
        _stroke(((42, 66), (58, 58)), ((42, 50), (58, 42))),
    ),
    # Wraith: hood and shoulders over a robe fraying into three tails.
    "undead_spirits": (
        (
            _polygon(
                (50, 12),
                (64, 20),
                (68, 40),
                (72, 60),
                (76, 86),
                (62, 76),
                (56, 88),
                (50, 74),
                (44, 88),
                (38, 76),
                (24, 86),
                (28, 60),
                (32, 40),
                (36, 20),
            ),
        ),
        _stroke(((44, 32), (48, 36)), ((56, 32), (52, 36))),
    ),
    # A rooted growth: canopy, trunk, spread roots.
    "plants": (
        (
            _ellipse(50, 38, 26, 23),
            _polygon((45, 56), (55, 56), (56, 88), (44, 88)),
        ),
        _stroke(((50, 74), (34, 84)), ((50, 74), (66, 84)), ((32, 88), (68, 88))),
    ),
    # A built figure: blocky, symmetrical, jointed.
    "constructs": (
        (
            _polygon((38, 18), (62, 18), (62, 32), (38, 32)),
            _polygon((31, 36), (69, 36), (69, 68), (31, 68)),
            _polygon((18, 38), (29, 38), (29, 62), (18, 62)),
            _polygon((71, 38), (82, 38), (82, 62), (71, 62)),
            _polygon((37, 72), (47, 72), (47, 90), (37, 90)),
            _polygon((53, 72), (63, 72), (63, 90), (53, 90)),
        ),
        _stroke(((43, 24), (48, 24)), ((52, 24), (57, 24)), ((40, 48), (60, 48))),
    ),
    # A maned beast with a single raised wing — at card size a chimera reads
    # as a mane, a wing and a heavy body, not as a count of heads.
    "legendary": (
        (
            _ellipse(46, 68, 22, 13),
            _ellipse(66, 58, 13, 12),
            _polygon((74, 54), (90, 56), (88, 66), (74, 66)),
            _polygon((40, 60), (30, 28), (52, 40)),
            _polygon((26, 66), (6, 60), (9, 72)),
        ),
        _stroke(((36, 80), (33, 92)), ((48, 81), (48, 92)), ((60, 80), (63, 92))),
    ),
}

# The fallback: a plain specimen mark, so a category added to the model
# without art still renders something honest rather than an empty frame.
_FALLBACK_SILHOUETTE = (
    (_ellipse(50, 50, 22, 22),),
    _stroke(((50, 40), (50, 60)), ((40, 50), (60, 50))),
)

# How much of the plate the silhouette fills, by figure size in hexes. A
# one-hex creature and a megahex dragon should not be drawn the same size.
_SCALE_BY_SIZE: dict[int, float] = {1: 0.70, 2: 0.80, 3: 0.88, 4: 0.94, 7: 1.0}
_MAX_SCALE = 1.0


@dataclass(frozen=True)
class Plate:
    """Everything a template needs to draw one beast's plate."""

    shapes: tuple[str, ...]
    details: str
    ground: str
    ink: str
    scale: float
    flipped: bool
    label: str

    @property
    def transform(self) -> str:
        """Scale about the plate's centre, mirrored for some creatures."""
        parts = [f"translate(50 50) scale({self.scale:.3f})"]
        if self.flipped:
            parts.append("scale(-1 1)")
        parts.append("translate(-50 -50)")
        return " ".join(parts)


def _fingerprint(slug: str) -> int:
    """A stable integer for a slug.

    ``hash()`` is salted per process, so it would repaint the bestiary on
    every restart; blake2b does not.
    """
    return int.from_bytes(hashlib.blake2b(slug.encode(), digest_size=8).digest())


def scale_for_size(size_hexes: int) -> float:
    """How much of the frame a figure of this size fills."""
    if size_hexes in _SCALE_BY_SIZE:
        return _SCALE_BY_SIZE[size_hexes]
    if size_hexes < 1:
        return _SCALE_BY_SIZE[1]
    # An unlisted size sits with the nearest listed one below it.
    return max(
        (value for size, value in _SCALE_BY_SIZE.items() if size <= size_hexes),
        default=_MAX_SCALE,
    )


def plate_for(beast) -> Plate:
    """The plate for one beast — silhouette, palette, scale and facing."""
    slug = beast.slug or beast.name
    fingerprint = _fingerprint(slug)
    hue, saturation, lightness = _PALETTES.get(beast.category, _FALLBACK_PALETTE)
    shapes, details = _SILHOUETTES.get(beast.category, _FALLBACK_SILHOUETTE)

    # Identity, not classification: a nudge within the category's own band,
    # wide enough to tell neighbours apart and narrow enough that the
    # category still reads at a glance.
    hue_shift = (fingerprint % 21) - 10
    lightness_shift = ((fingerprint >> 8) % 13) - 6
    plate_hue = (hue + hue_shift) % 360
    ground_lightness = min(92, max(38, lightness + lightness_shift + 30))
    ink_lightness = min(45, max(16, lightness - 30))

    return Plate(
        shapes=shapes,
        details=details,
        ground=f"hsl({plate_hue} {saturation}% {ground_lightness}%)",
        ink=f"hsl({plate_hue} {min(70, saturation + 15)}% {ink_lightness}%)",
        scale=scale_for_size(beast.size_hexes or 1),
        flipped=bool((fingerprint >> 16) & 1),
        label=f"{beast.name}, a {beast.get_category_display().lower()} silhouette",
    )
