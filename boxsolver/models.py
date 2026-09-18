"""Domain models for BoxSolverAlgorithm.

Parsing follows ``schemas/packer_schemas.yaml`` (the base BoxType and Item
definitions) with a small number of clearly-marked optional extensions.
Anything the schema marks as required is enforced here and surfaced as a
``SchemaError`` with a JSON-pointer-ish path, so a caller gets a useful 400
rather than a stack trace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .geometry import ORIENTATIONS_BY_CODE, UPRIGHT_CODES

_MISSING = object()


class SchemaError(ValueError):
    """An incoming payload does not satisfy the agreed schema."""


# --------------------------------------------------------------------------- #
# parsing helpers
# --------------------------------------------------------------------------- #
def _get(data: Dict[str, Any], key: str) -> Any:
    """Case-insensitive lookup, so `boxWeight` and `BoxWeight` both work."""
    if not isinstance(data, dict):
        raise SchemaError(f"expected an object, got {type(data).__name__}")
    if key in data:
        return data[key]
    for existing, value in data.items():
        if isinstance(existing, str) and existing.lower() == key.lower():
            return value
    return _MISSING


def _require(data: Dict[str, Any], key: str, where: str) -> Any:
    value = _get(data, key)
    if value is _MISSING or value is None:
        raise SchemaError(f"{where}: required field '{key}' is missing")
    return value


def _number(value: Any, key: str, where: str, *, positive: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaError(f"{where}: '{key}' must be a number, got {value!r}")
    number = float(value)
    if positive and number <= 0:
        raise SchemaError(f"{where}: '{key}' must be greater than zero, got {number}")
    if not positive and number < 0:
        raise SchemaError(f"{where}: '{key}' must not be negative, got {number}")
    return number


def _optional_number(
    data: Dict[str, Any], key: str, where: str, *, positive: bool = True
) -> Optional[float]:
    value = _get(data, key)
    if value is _MISSING or value is None:
        return None
    return _number(value, key, where, positive=positive)


def _optional_bool(data: Dict[str, Any], key: str, where: str, default: bool) -> bool:
    value = _get(data, key)
    if value is _MISSING or value is None:
        return default
    if not isinstance(value, bool):
        raise SchemaError(f"{where}: '{key}' must be a boolean, got {value!r}")
    return value


def _optional_int(data: Dict[str, Any], key: str, where: str) -> Optional[int]:
    value = _get(data, key)
    if value is _MISSING or value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(f"{where}: '{key}' must be an integer, got {value!r}")
    if value < 0:
        raise SchemaError(f"{where}: '{key}' must not be negative, got {value}")
    return value


def _string(value: Any, key: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{where}: '{key}' must be a non-empty string, got {value!r}")
    return value.strip()


# --------------------------------------------------------------------------- #
# box types
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BoxType:
    """A container reference from the BoxType schema.

    ``max_weight`` is the *rated capacity available to items*: MaxWeight
    excludes BoxWeight, so the check is
    ``sum(item weights) <= MaxWeight`` and the despatch weight reported back is
    ``BoxWeight + sum(item weights)``.
    """

    reference: str
    width: float
    length: float
    depth: float
    max_weight: Optional[float] = None
    box_weight: float = 0.0
    active: bool = True
    maximum_boxes: Optional[int] = None

    @property
    def volume(self) -> float:
        return self.width * self.length * self.depth

    @property
    def inner_dimensions(self) -> Tuple[float, float, float]:
        return (self.width, self.length, self.depth)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], index: int = 0) -> "BoxType":
        """Parse a single BoxType from a dict, validating required fields."""
        where = f"Boxes[{index}]"
        reference = _string(_require(data, "Reference", where), "Reference", where)
        where = f"Boxes[{index}] ({reference})"
        return cls(
            reference=reference,
            width=_number(_require(data, "Width", where), "Width", where),
            length=_number(_require(data, "Length", where), "Length", where),
            depth=_number(_require(data, "Depth", where), "Depth", where),
            max_weight=_optional_number(data, "MaxWeight", where),
            box_weight=_optional_number(data, "BoxWeight", where, positive=False) or 0.0,
            active=_optional_bool(data, "Active", where, default=True),
            maximum_boxes=_optional_int(data, "MaximumBoxes", where),
        )


def parse_box_types(payload: Sequence[Dict[str, Any]]) -> List[BoxType]:
    """Parse and validate the Boxes array, rejecting duplicates and empty lists."""
    if not isinstance(payload, (list, tuple)):
        raise SchemaError("Boxes: expected an array of BoxType objects")
    boxes = [BoxType.from_dict(entry, i) for i, entry in enumerate(payload)]
    seen: Dict[str, int] = {}
    for i, box in enumerate(boxes):
        if box.reference in seen:
            raise SchemaError(
                f"Boxes[{i}]: duplicate Reference '{box.reference}' "
                f"(already used at index {seen[box.reference]})"
            )
        seen[box.reference] = i
    if not boxes:
        raise SchemaError("Boxes: at least one box type is required")
    return boxes


# --------------------------------------------------------------------------- #
# items
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Item:
    """An item from the Item schema.

    Optional fields beyond the published schema (all default to schema
    behaviour when absent, so existing payloads keep working):

    * ``Quantity``          - integer, defaults to 1. The published schema has no
                              way to express "five of these", which every real
                              order needs.
    * ``KeepUpright``       - boolean, defaults to false. Restricts the item to
                              the two orientations that keep its Depth vertical.
       * ``AllowedRotations``  - explicit list of orientation codes, for finer
                              control than KeepUpright.
    * ``Fragile``           - boolean, defaults to false. Nothing may be
                              stacked on top of a fragile item (part of the
                              load-bearing constraint; enforced in
                              ``Container._is_valid``).
    """

    code: str
    reference: str
    width: float
    length: float
    depth: float
    weight: float
    box_group: Optional[str] = None
    quantity: int = 1
    keep_upright: bool = False
    allowed_rotations: Optional[Tuple[str, ...]] = None
    fragile: bool = False

    @property
    def volume(self) -> float:
        return self.width * self.length * self.depth

    @property
    def rotation_codes(self) -> Optional[Tuple[str, ...]]:
        if self.allowed_rotations is not None:
            return self.allowed_rotations
        if self.keep_upright:
            return UPRIGHT_CODES
        return None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], index: int = 0) -> "Item":
        """Parse a single Item from a dict, validating all required and optional fields.

        Handles BoxGroup normalisation, AllowedRotations validation against
        known orientation codes, and Quantity lower-bound check.
        """
        where = f"Items[{index}]"
        code = _string(_require(data, "ItemCode", where), "ItemCode", where)
        where = f"Items[{index}] ({code})"
        reference = _string(
            _require(data, "ItemReference", where), "ItemReference", where
        )
        group = _get(data, "BoxGroup")
        group_value = (
            None
            if group is _MISSING or group is None or str(group).strip() == ""
            else str(group).strip()
        )

        rotations_raw = _get(data, "AllowedRotations")
        rotations: Optional[Tuple[str, ...]] = None
        if rotations_raw is not _MISSING and rotations_raw is not None:
            if not isinstance(rotations_raw, (list, tuple)) or not rotations_raw:
                raise SchemaError(f"{where}: 'AllowedRotations' must be a non-empty array")
            unknown = [c for c in rotations_raw if c not in ORIENTATIONS_BY_CODE]
            if unknown:
                raise SchemaError(
                    f"{where}: unknown orientation code(s) {unknown!r}; "
                    f"valid codes are {sorted(ORIENTATIONS_BY_CODE)}"
                )
            rotations = tuple(rotations_raw)

        quantity = _optional_int(data, "Quantity", where)
        if quantity == 0:
            raise SchemaError(f"{where}: 'Quantity' must be at least 1")

        return cls(
            code=code,
            reference=reference,
            width=_number(_require(data, "Width", where), "Width", where),
            length=_number(_require(data, "Length", where), "Length", where),
            depth=_number(_require(data, "Depth", where), "Depth", where),
            weight=_number(_require(data, "Weight", where), "Weight", where, positive=False),
            box_group=group_value,
            quantity=1 if quantity is None else quantity,
            keep_upright=_optional_bool(data, "KeepUpright", where, default=False),
            allowed_rotations=rotations,
            fragile=_optional_bool(data, "Fragile", where, default=False),
        )


@dataclass(frozen=True)
class ItemInstance:
    """One physical unit of an Item.

    ``instance_id`` is stable for a given request (``ITM-001#1``) so a caller can
    key logs and a renderer can key DOM nodes even when an order contains
    several of the same ItemCode.
    """

    item: Item
    ordinal: int

    @property
    def instance_id(self) -> str:
        return f"{self.item.code}#{self.ordinal}"

    @property
    def volume(self) -> float:
        return self.item.volume


def parse_items(payload: Sequence[Dict[str, Any]]) -> List[Item]:
    """Parse and validate the Items array, rejecting non-arrays and empty lists."""
    if not isinstance(payload, (list, tuple)):
        raise SchemaError("Items: expected an array of Item objects")
    items = [Item.from_dict(entry, i) for i, entry in enumerate(payload)]
    if not items:
        raise SchemaError("Items: at least one item is required")
    return items


def expand_instances(items: Sequence[Item]) -> List[ItemInstance]:
    """Expand each Item's Quantity into individual ItemInstance objects.

    Each instance gets a unique ordinal (1-based) forming its instance_id
    as 'ItemCode#ordinal'.
    """
    instances: List[ItemInstance] = []
    for item in items:
        for ordinal in range(1, item.quantity + 1):
            instances.append(ItemInstance(item=item, ordinal=ordinal))
    return instances


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PackingConfig:
    """Knobs for a single solve. Defaults are the MVP behaviour."""

    objective: str = "min_boxes"
    """``min_boxes``   - fill each carton as full as possible (fewest cartons).
       ``min_volume``  - prefer the tightest carton for what actually fits."""

    allow_rotation: bool = True
    """Global override. False forces every item into its natural orientation."""

    require_support: bool = True
    """Items must rest on the floor or on other items (no floating boxes)."""

    min_support_ratio: float = 0.50
    """Fraction of an item's base that must be supported when require_support.
       Measured trade-off at 200 items: 0.0 -> 5.25 cartons / 79.5% full,
       0.5 -> 5.75 / 74.2%, 1.0 -> 6.50 / 68.1%. 0.5 keeps loads stable
       without paying the full stability tax; override per deployment."""

    allow_ungrouped_to_share: bool = True
    """Items with no BoxGroup may be added to a carton opened for a group.
       Set False for the stricter reading of the schema."""

    time_limit_seconds: float = 20.0
    """Soft budget. The solver returns its best complete answer if exceeded."""

    @staticmethod
    def from_dict(data: Optional[Dict[str, Any]]) -> "PackingConfig":
        """Parse the optional Options block, applying defaults for missing fields.

        Validates Objective enum, MinSupportRatio range (0 to 1), and
        TimeLimitSeconds positivity.
        """
        if not data:
            return PackingConfig()
        where = "Options"
        defaults = PackingConfig()
        objective = _get(data, "Objective")
        objective_value = defaults.objective if objective is _MISSING or objective is None else str(objective)
        if objective_value not in ("min_boxes", "min_volume"):
            raise SchemaError(
                f"{where}: 'Objective' must be 'min_boxes' or 'min_volume', got {objective_value!r}"
            )
        ratio = _optional_number(data, "MinSupportRatio", where, positive=False)
        if ratio is not None and ratio > 1:
            raise SchemaError(f"{where}: 'MinSupportRatio' must be between 0 and 1")
        limit = _optional_number(data, "TimeLimitSeconds", where)
        return PackingConfig(
            objective=objective_value,
            allow_rotation=_optional_bool(data, "AllowRotation", where, defaults.allow_rotation),
            require_support=_optional_bool(data, "RequireSupport", where, defaults.require_support),
            min_support_ratio=defaults.min_support_ratio if ratio is None else ratio,
            allow_ungrouped_to_share=_optional_bool(
                data, "AllowUngroupedToShare", where, defaults.allow_ungrouped_to_share
            ),
            time_limit_seconds=defaults.time_limit_seconds if limit is None else limit,
        )


@dataclass
class PackRequest:
    """The full request envelope: Boxes + Items + Options."""

    boxes: List[BoxType] = field(default_factory=list)
    items: List[Item] = field(default_factory=list)
    options: PackingConfig = field(default_factory=PackingConfig)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PackRequest":
        """Parse the full request envelope: Boxes + Items + optional Options."""
        if not isinstance(data, dict):
            raise SchemaError("request body must be a JSON object")
        boxes = _get(data, "Boxes")
        items = _get(data, "Items")
        if boxes is _MISSING:
            raise SchemaError("request: required field 'Boxes' is missing")
        if items is _MISSING:
            raise SchemaError("request: required field 'Items' is missing")
        options = _get(data, "Options")
        return cls(
            boxes=parse_box_types(boxes),
            items=parse_items(items),
            options=PackingConfig.from_dict(None if options is _MISSING else options),
        )
