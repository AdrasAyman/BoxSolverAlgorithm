"""Stable output models for BoxSolverAlgorithm solutions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .core.container import Container, UnpackedItem

__version__ = "0.1.0"

SCHEMA_VERSION = "1.0.0"
"""Version of the public response *contract*.

Independent of ``__version__``: the engine can change how it packs without
changing the shape of what it returns.

  * MINOR/PATCH (1.x) - fields may be **added** only.
  * MAJOR (2.0.0)     - anything renamed, removed, or re-meaned. Announce to
                        consumers before merging.
"""


def _round(value: float, digits: int = 2) -> float:
    rounded = round(float(value) + 0.0, digits)
    return 0.0 if rounded == 0 else rounded


@dataclass
class Solution:
    containers: List[Container] = field(default_factory=list)
    unpacked: List[UnpackedItem] = field(default_factory=list)
    elapsed_ms: float = 0.0
    truncated: bool = False

    @property
    def success(self) -> bool:
        return not self.unpacked

    def to_dict(self) -> Dict[str, Any]:
        packed_volume = sum(c.used_volume for c in self.containers)
        box_volume = sum(c.box_type.volume for c in self.containers)
        counts: Dict[str, int] = {}
        for c in self.containers:
            counts[c.box_type.reference] = counts.get(c.box_type.reference, 0) + 1
        return {
            "Success": self.success,
            "Meta": {
                "Engine": "boxsolver",
                "Version": __version__,
                "SchemaVersion": SCHEMA_VERSION,
                "SolveTimeMs": _round(self.elapsed_ms, 1),
                "TimeLimitReached": self.truncated,
                "Units": {"Length": "mm", "Weight": "kg"},
                "CoordinateSystem": {
                    "Origin": "internal front-bottom-left corner of the box",
                    "X": "Width", "Y": "Length", "Z": "Depth (vertical)",
                    "PositionMeaning": "minimum corner of the item's bounding box",
                },
            },
            "Summary": {
                "BoxCount": len(self.containers),
                "BoxCounts": counts,
                "PackedItemCount": sum(len(c.placements) for c in self.containers),
                "UnpackedItemCount": len(self.unpacked),
                "TotalWeight": _round(sum(c.total_weight for c in self.containers), 3),
                "OverallVolumeUtilisation": _round(0.0 if box_volume <= 0 else packed_volume / box_volume, 4),
            },
            "Boxes": [c.to_dict(i) for i, c in enumerate(self.containers)],
            "UnpackedItems": [u.to_dict() for u in self.unpacked],
        }