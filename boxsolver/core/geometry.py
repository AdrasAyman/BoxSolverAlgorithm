"""Geometry helpers for BoxSolverAlgorithm.

Coordinate system (this is the contract downstream consumers rely on):

    Origin (0, 0, 0) is the INTERNAL front-bottom-left corner of the box.
    +X runs along the box Width   (left  -> right)
    +Y runs along the box Length  (front -> back)
    +Z runs along the box Depth   (floor -> lid) i.e. Depth is the vertical axis.

    All values are in millimetres. A placement's Position is the minimum corner
    of its axis-aligned bounding box, so the item occupies
    [X, X+Width] x [Y, Y+Length] x [Z, Z+Depth].

An orientation is one of the six axis-aligned permutations of an item's
(Width, Length, Depth). It is reported to consumers in three redundant forms so
that a renderer can pick whichever is convenient:

  * ``code``            - e.g. "LWD": the original dimension that now lies along
                          X, then Y, then Z. "WLD" is the identity.
  * ``axis_map``        - the same thing as explicit labels, e.g.
                          {"X": "Length", "Y": "Width", "Z": "Depth"}.
  * ``rotation_degrees`` - extrinsic rotations about the fixed world axes,
                          applied X first, then Y, then Z (R = Rz . Ry . Rx).

The Euler angles are *derived* from the permutation at import time by brute
force rather than hand-written, and asserted in the test-suite, so the three
representations cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from typing import Dict, List, Optional, Sequence, Tuple

AXIS_LABELS: Tuple[str, str, str] = ("Width", "Length", "Depth")
AXIS_CODES: Tuple[str, str, str] = ("W", "L", "D")
WORLD_AXES: Tuple[str, str, str] = ("X", "Y", "Z")

Matrix = Tuple[Tuple[int, int, int], ...]
_ANGLES = (0, 90, 180, 270)
_COS = {0: 1, 90: 0, 180: -1, 270: 0}
_SIN = {0: 0, 90: 1, 180: 0, 270: -1}


# --------------------------------------------------------------------------- #
# rotation matrix construction from 90-degree Euler angles
# --------------------------------------------------------------------------- #


def _rx(a: int) -> Matrix:
    return ((1, 0, 0), (0, _COS[a], -_SIN[a]), (0, _SIN[a], _COS[a]))


def _ry(a: int) -> Matrix:
    return ((_COS[a], 0, _SIN[a]), (0, 1, 0), (-_SIN[a], 0, _COS[a]))


def _rz(a: int) -> Matrix:
    return ((_COS[a], -_SIN[a], 0), (_SIN[a], _COS[a], 0), (0, 0, 1))


def _mul(a: Matrix, b: Matrix) -> Matrix:
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
        for i in range(3)
    )


def rotation_matrix(rx: int, ry: int, rz: int) -> Matrix:
    """Extrinsic rotation about the fixed axes, X then Y then Z."""
    return _mul(_rz(rz), _mul(_ry(ry), _rx(rx)))


# --------------------------------------------------------------------------- #
# Euler angle discovery: brute-force search for the rotation matching a permutation
# --------------------------------------------------------------------------- #
def _euler_for(perm: Sequence[int]) -> Tuple[int, int, int]:
    """Smallest (rx, ry, rz) whose rotation maps original axis perm[i] onto world axis i."""
    for rx in _ANGLES:
        for ry in _ANGLES:
            for rz in _ANGLES:
                m = rotation_matrix(rx, ry, rz)
                if all(abs(m[axis][perm[axis]]) == 1 for axis in range(3)):
                    return (rx, ry, rz)
    raise RuntimeError(f"no rotation found for permutation {perm!r}")  # unreachable



# --------------------------------------------------------------------------- #
# Orientation: one of six axis-aligned rotations of a cuboid
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Orientation:
    """One of the six axis-aligned orientations of a cuboid."""

    code: str
    permutation: Tuple[int, int, int]
    rotation_degrees: Tuple[int, int, int]

    @property
    def axis_map(self) -> Dict[str, str]:
        return {WORLD_AXES[i]: AXIS_LABELS[self.permutation[i]] for i in range(3)}

    @property
    def is_upright(self) -> bool:
        """True when the item's own Depth still points up (+Z)."""
        return self.permutation[2] == 2

    def apply(self, width: float, length: float, depth: float) -> Tuple[float, float, float]:
        """Return the (X, Y, Z) extents of the item once rotated."""
        source = (width, length, depth)
        return tuple(source[i] for i in self.permutation)  # type: ignore[return-value]

    def to_dict(self) -> Dict[str, object]:
        rx, ry, rz = self.rotation_degrees
        return {
            "Code": self.code,
            "AxisMap": self.axis_map,
            "RotationDegrees": {"X": rx, "Y": ry, "Z": rz},
        }


# --------------------------------------------------------------------------- #
# build the six orientations at import time
# --------------------------------------------------------------------------- #
def _build_orientations() -> Tuple[Orientation, ...]:
    """Generate all six axis-aligned orientations from the three-axis permutations.

    Each permutation produces a code (e.g. 'LWD'), a tuple of axis indices,
    and the matching Euler angles. Results are sorted so WLD (identity) comes
    first for deterministic tie-breaking.
    """
    built: List[Orientation] = []
    for perm in permutations(range(3)):
        code = "".join(AXIS_CODES[i] for i in perm)
        built.append(Orientation(code, perm, _euler_for(perm)))
    # Identity first, then the yaw-only rotation, then the rest: this makes the
    # solver's tie-breaking deterministic and keeps output diffs readable.
    built.sort(key=lambda o: (o.code != "WLD", o.rotation_degrees))
    return tuple(built)


# --------------------------------------------------------------------------- #
# module-level constants: available to the rest of the engine at import time
# --------------------------------------------------------------------------- #
ORIENTATIONS: Tuple[Orientation, ...] = _build_orientations()
ORIENTATIONS_BY_CODE: Dict[str, Orientation] = {o.code: o for o in ORIENTATIONS}
IDENTITY: Orientation = ORIENTATIONS_BY_CODE["WLD"]
UPRIGHT_CODES: Tuple[str, ...] = tuple(o.code for o in ORIENTATIONS if o.is_upright)



# --------------------------------------------------------------------------- #
# candidate selection: de-duplicated orientations for a specific item
# --------------------------------------------------------------------------- #
def candidate_orientations(
    width: float,
    length: float,
    depth: float,
    allowed_codes: Optional[Sequence[str]] = None,
) -> List[Tuple[Orientation, Tuple[float, float, float]]]:
    """Distinct orientations for an item, de-duplicated on resulting extents.

    A cube has six orientations but only one distinct footprint; trying all six
    would multiply the search cost for no benefit.
    """
    allowed = ORIENTATIONS if allowed_codes is None else [
        ORIENTATIONS_BY_CODE[c] for c in allowed_codes if c in ORIENTATIONS_BY_CODE
    ]
    seen: Dict[Tuple[float, float, float], bool] = {}
    out: List[Tuple[Orientation, Tuple[float, float, float]]] = []
    for orientation in allowed:
        extents = orientation.apply(width, length, depth)
        if extents in seen:
            continue
        seen[extents] = True
        out.append((orientation, extents))
    return out
