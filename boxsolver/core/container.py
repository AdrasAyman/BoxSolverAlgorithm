"""Single-carton placement engine for BoxSolverAlgorithm."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .geometry import IDENTITY, Orientation, candidate_orientations
from ..models import BoxType, Item, ItemInstance, PackingConfig

EPS = 1e-6
Point = Tuple[float, float, float]


def _round(value: float, digits: int = 2) -> float:
    rounded = round(float(value) + 0.0, digits)
    return 0.0 if rounded == 0 else rounded


@dataclass
class Placement:
    """One item, positioned and oriented inside a carton."""

    instance: ItemInstance
    x: float
    y: float
    z: float
    dx: float
    dy: float
    dz: float
    orientation: Orientation
    sequence: int = 0

    @property
    def volume(self) -> float:
        return self.dx * self.dy * self.dz

    def to_dict(self) -> Dict[str, Any]:
        item = self.instance.item
        return {
            "Sequence": self.sequence,
            "InstanceId": self.instance.instance_id,
            "ItemCode": item.code,
            "ItemReference": item.reference,
            "BoxGroup": item.box_group,
            "Position": {
                "X": _round(self.x),
                "Y": _round(self.y),
                "Z": _round(self.z),
            },
            "Dimensions": {
                "Width": _round(self.dx),
                "Length": _round(self.dy),
                "Depth": _round(self.dz),
            },
            "Orientation": self.orientation.to_dict(),
            "Weight": _round(item.weight, 3),
        }


@dataclass
class UnpackedItem:
    instance: ItemInstance
    reason_code: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "InstanceId": self.instance.instance_id,
            "ItemCode": self.instance.item.code,
            "ItemReference": self.instance.item.reference,
            "ReasonCode": self.reason_code,
            "Reason": self.reason,
        }


class Container:
    """A single carton being filled."""

    def __init__(self, box_type: BoxType, config: PackingConfig, group: Optional[str] = None):
        self.box_type = box_type
        self.config = config
        self.group = group
        self.placements: List[Placement] = []
        self.items_weight = 0.0
        self._corners: List[Point] = [(0.0, 0.0, 0.0)]

    @property
    def used_volume(self) -> float:
        return sum(p.volume for p in self.placements)

    @property
    def utilisation(self) -> float:
        volume = self.box_type.volume
        return 0.0 if volume <= 0 else self.used_volume / volume

    @property
    def total_weight(self) -> float:
        return self.items_weight + self.box_type.box_weight

    @property
    def load_height(self) -> float:
        return max((p.z + p.dz for p in self.placements), default=0.0)

    def _weight_ok(self, extra: float) -> bool:
        if self.box_type.max_weight is None:
            return True
        return self.items_weight + extra <= self.box_type.max_weight + EPS

    def _inside(self, x: float, y: float, z: float, dx: float, dy: float, dz: float) -> bool:
        w, l, d = self.box_type.inner_dimensions
        return x + dx <= w + EPS and y + dy <= l + EPS and z + dz <= d + EPS

    def _collides(self, x: float, y: float, z: float, dx: float, dy: float, dz: float) -> bool:
        for p in self.placements:
            if x + dx <= p.x + EPS or p.x + p.dx <= x + EPS:
                continue
            if y + dy <= p.y + EPS or p.y + p.dy <= y + EPS:
                continue
            if z + dz <= p.z + EPS or p.z + p.dz <= z + EPS:
                continue
            return True
        return False

    def _support_ratio(self, x: float, y: float, z: float, dx: float, dy: float) -> float:
        """Fraction of the footprint resting on the floor or other items."""
        if z <= EPS:
            return 1.0
        footprint = dx * dy
        if footprint <= 0:
            return 1.0
        covered = 0.0
        for p in self.placements:
            if abs(p.z + p.dz - z) > EPS:
                continue
            overlap_x = min(x + dx, p.x + p.dx) - max(x, p.x)
            overlap_y = min(y + dy, p.y + p.dy) - max(y, p.y)
            if overlap_x > EPS and overlap_y > EPS:
                covered += overlap_x * overlap_y
        return covered / footprint

    def _is_valid(self, x, y, z, dx, dy, dz) -> bool:
        if not self._inside(x, y, z, dx, dy, dz):
            return False
        if self._collides(x, y, z, dx, dy, dz):
            return False
        if self.config.require_support and self._support_ratio(x, y, z, dx, dy) < self.config.min_support_ratio - EPS:
            return False
        return True

    def _orientations(self, item: Item):
        codes = item.rotation_codes if self.config.allow_rotation else (IDENTITY.code,)
        return candidate_orientations(item.width, item.length, item.depth, codes)

    def find_placement(self, instance: ItemInstance) -> Optional[Placement]:
        item = instance.item
        if not self._weight_ok(item.weight):
            return None
        best: Optional[Placement] = None
        best_score: Optional[Tuple[float, ...]] = None
        for orientation, (dx, dy, dz) in self._orientations(item):
            for (x, y, z) in self._corners:
                if not self._is_valid(x, y, z, dx, dy, dz):
                    continue
                score = (z, y, x, dz, orientation.rotation_degrees)
                if best_score is None or score < best_score:
                    best_score = score
                    best = Placement(instance, x, y, z, dx, dy, dz, orientation)
        return best

    def commit(self, placement: Placement) -> None:
        placement.sequence = len(self.placements)
        self.placements.append(placement)
        self.items_weight += placement.instance.item.weight
        x, y, z, dx, dy, dz = placement.x, placement.y, placement.z, placement.dx, placement.dy, placement.dz
        corners = [c for c in self._corners if c != (x, y, z)]
        for candidate in ((x + dx, y, z), (x, y + dy, z), (x, y, z + dz)):
            if candidate in corners:
                continue
            cx, cy, cz = candidate
            w, l, d = self.box_type.inner_dimensions
            if cx >= w - EPS or cy >= l - EPS or cz >= d - EPS:
                continue
            corners.append(candidate)
        self._corners = sorted(corners, key=lambda c: (c[2], c[1], c[0]))

    def try_add(self, instance: ItemInstance) -> bool:
        placement = self.find_placement(instance)
        if placement is None:
            return False
        self.commit(placement)
        return True

    def to_dict(self, box_index: int) -> Dict[str, Any]:
        bt = self.box_type
        return {
            "BoxIndex": box_index,
            "Reference": bt.reference,
            "BoxGroup": self.group,
            "Dimensions": {"Width": _round(bt.width), "Length": _round(bt.length), "Depth": _round(bt.depth)},
            "ItemCount": len(self.placements),
            "ItemsWeight": _round(self.items_weight, 3),
            "BoxWeight": _round(bt.box_weight, 3),
            "TotalWeight": _round(self.total_weight, 3),
            "MaxWeight": None if bt.max_weight is None else _round(bt.max_weight, 3),
            "VolumeUtilisation": _round(self.utilisation, 4),
            "LoadHeight": _round(self.load_height),
            "Items": [p.to_dict() for p in self.placements],
        }