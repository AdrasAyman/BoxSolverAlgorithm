"""BoxSolverAlgorithm - a deterministic 3D bin-packing engine.

Quick start::

    from boxsolver import pack_payload
    result = pack_payload({"Boxes": [...], "Items": [...]})

The core package has **no third-party dependencies**, so a calling application
can vendor it directly (``pip install boxsolver``) or call it over HTTP via
``boxsolver.entrypoints.api``. FastAPI is only needed for the HTTP transport.
"""

from .core.geometry import ORIENTATIONS, ORIENTATIONS_BY_CODE, Orientation
from .models import (
    BoxType,
    Item,
    ItemInstance,
    PackingConfig,
    PackRequest,
    SchemaError,
    parse_box_types,
    parse_items,
)
from .core.container import Container, Placement, UnpackedItem
from .core.packer import pack_payload, Packer, solve
from .output import SCHEMA_VERSION, Solution, __version__

__all__ = [
    "SCHEMA_VERSION",
    "BoxType",
    "Container",
    "Item",
    "ItemInstance",
    "Orientation",
    "ORIENTATIONS",
    "ORIENTATIONS_BY_CODE",
    "Packer",
    "PackingConfig",
    "PackRequest",
    "Placement",
    "SchemaError",
    "Solution",
    "UnpackedItem",
    "__version__",
    "pack_payload",
    "parse_box_types",
    "parse_items",
    "solve",
]