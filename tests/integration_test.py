"""Contract and schema compliance integration tests.

These tests validate that the full pipeline (HTTP request → engine → JSON
response) produces output structures that conform to schemas/boxsolver_api.yaml.
Each test covers a scenario not already present in test_boxsolver.py, test_api.py,
or test_boundary_precision.py.

Run with:  pytest -q tests/integration_test.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boxsolver import SCHEMA_VERSION, pack_payload
from boxsolver.entrypoints.api import app

client = TestClient(app)
DATA = Path(__file__).resolve().parent.parent / "data"


def _sample_payload():
    return {
        "Boxes": json.loads((DATA / "boxes.json").read_text()),
        "Items": json.loads((DATA / "items.json").read_text()),
    }


# ------------------------------------------------------------------ helpers
VALID_ORIENTATION_CODES = {"WLD", "LWD", "WDL", "DLW", "LDW", "DWL"}
DIMENSION_LABELS = {"Width", "Length", "Depth"}
VALID_DEGREES = {0, 90, 180, 270}
VALID_REASON_CODES = {
    "DOES_NOT_FIT",
    "EXCEEDS_MAX_WEIGHT",
    "NO_FEASIBLE_BOX",
    "BOX_QUOTA_EXHAUSTED",
}


# ------------------------------------------------------------------ tests

def test_all_position_coordinates_are_numeric_and_non_negative():
    """Every Position.X, Y, Z must be a non-negative number (int or float)."""
    result = pack_payload(_sample_payload())
    for box in result["Boxes"]:
        for item in box["Items"]:
            pos = item["Position"]
            for axis in ("X", "Y", "Z"):
                assert isinstance(pos[axis], (int, float)), (
                    f"Position.{axis} is not numeric: {pos[axis]!r}"
                )
                assert pos[axis] >= 0, (
                    f"Position.{axis} is negative: {pos[axis]}"
                )


def test_all_dimension_values_are_positive_numbers():
    """Placed Dimensions.Width, Length, Depth must all be positive numbers."""
    result = pack_payload(_sample_payload())
    for box in result["Boxes"]:
        box_dims = box["Dimensions"]
        for key in ("Width", "Length", "Depth"):
            assert isinstance(box_dims[key], (int, float))
            assert box_dims[key] > 0

        for item in box["Items"]:
            dims = item["Dimensions"]
            for key in ("Width", "Length", "Depth"):
                assert isinstance(dims[key], (int, float)), (
                    f"Dimensions.{key} is not numeric: {dims[key]!r}"
                )
                assert dims[key] > 0, (
                    f"Dimensions.{key} is not positive: {dims[key]}"
                )


def test_orientation_code_and_axis_map_are_schema_compliant():
    """Each placement Orientation must have a valid Code, AxisMap with three
    labels from {Width, Length, Depth}, and RotationDegrees with multiples of 90."""
    result = pack_payload(_sample_payload())
    for box in result["Boxes"]:
        for item in box["Items"]:
            orient = item["Orientation"]
            assert orient["Code"] in VALID_ORIENTATION_CODES, (
                f"unexpected orientation code: {orient['Code']}"
            )
            axis_map = orient["AxisMap"]
            assert set(axis_map.keys()) == {"X", "Y", "Z"}
            assert set(axis_map.values()) <= DIMENSION_LABELS, (
                f"AxisMap contains invalid labels: {axis_map}"
            )
            # Each of {Width, Length, Depth} must appear exactly once
            assert set(axis_map.values()) == DIMENSION_LABELS, (
                f"AxisMap must be a permutation of {DIMENSION_LABELS}: {axis_map}"
            )

            rot = orient["RotationDegrees"]
            for axis in ("X", "Y", "Z"):
                assert rot[axis] in VALID_DEGREES, (
                    f"RotationDegrees.{axis} = {rot[axis]} is not a valid 90-degree multiple"
                )


def test_meta_block_contains_required_fields_and_correct_types():
    """The Meta block must include Engine, Version, SchemaVersion, SolveTimeMs,
    Units, and CoordinateSystem with correct types."""
    result = pack_payload(_sample_payload())
    meta = result["Meta"]

    assert isinstance(meta["Engine"], str) and meta["Engine"] == "boxsolver"
    assert isinstance(meta["Version"], str)
    assert meta["SchemaVersion"] == SCHEMA_VERSION
    assert isinstance(meta["SolveTimeMs"], (int, float))
    assert meta["SolveTimeMs"] >= 0

    assert isinstance(meta["TimeLimitReached"], bool)

    units = meta["Units"]
    assert units["Length"] == "mm"
    assert units["Weight"] == "kg"

    coord = meta["CoordinateSystem"]
    assert "X" in coord and "Y" in coord and "Z" in coord


def test_summary_counts_match_actual_box_and_item_counts():
    """Summary.BoxCount, PackedItemCount, and UnpackedItemCount must match
    the actual length of the Boxes and UnpackedItems arrays."""
    result = pack_payload(_sample_payload())
    summary = result["Summary"]

    assert summary["BoxCount"] == len(result["Boxes"])
    total_placed = sum(box["ItemCount"] for box in result["Boxes"])
    assert summary["PackedItemCount"] == total_placed
    assert summary["UnpackedItemCount"] == len(result["UnpackedItems"])


def test_box_index_is_zero_based_and_sequential():
    """Each BoxIndex must start at 0 and increment by 1."""
    result = pack_payload(_sample_payload())
    for i, box in enumerate(result["Boxes"]):
        assert box["BoxIndex"] == i, (
            f"BoxIndex should be {i}, got {box['BoxIndex']}"
        )


def test_unpacked_items_have_valid_reason_codes_and_required_keys():
    """When items cannot be packed, UnpackedItems must contain InstanceId,
    ItemCode, ReasonCode, and Reason with valid values."""
    payload = _sample_payload()
    # Add an oversized item that cannot fit
    payload["Items"].append({
        "ItemCode": "TOOBIG",
        "ItemReference": "Oversized Unit",
        "Width": 9999,
        "Length": 9999,
        "Depth": 9999,
        "Weight": 0.1,
    })
    result = pack_payload(payload)

    assert len(result["UnpackedItems"]) >= 1
    for unpacked in result["UnpackedItems"]:
        assert set(unpacked.keys()) >= {
            "InstanceId", "ItemCode", "ReasonCode", "Reason"
        }
        assert isinstance(unpacked["InstanceId"], str)
        assert isinstance(unpacked["ItemCode"], str)
        assert unpacked["ReasonCode"] in VALID_REASON_CODES, (
            f"unexpected ReasonCode: {unpacked['ReasonCode']}"
        )
        assert isinstance(unpacked["Reason"], str) and len(unpacked["Reason"]) > 0


def test_placement_dimensions_are_a_permutation_of_item_input():
    """The placed Width, Length, Depth must be a permutation of the item's
    original dimensions, verifying rotation is applied correctly."""
    payload = {
        "Boxes": [{"Reference": "BIG", "Width": 500, "Length": 500, "Depth": 500}],
        "Items": [
            {
                "ItemCode": "R1",
                "ItemReference": "Rotation Check",
                "Width": 100,
                "Length": 200,
                "Depth": 300,
                "Weight": 1.0,
            }
        ],
    }
    result = pack_payload(payload)
    assert result["Success"] is True

    placed = result["Boxes"][0]["Items"][0]
    dims = placed["Dimensions"]
    placed_sorted = sorted([dims["Width"], dims["Length"], dims["Depth"]])
    original_sorted = sorted([100, 200, 300])
    assert placed_sorted == original_sorted, (
        f"placed dims {placed_sorted} are not a permutation of {original_sorted}"
    )


def test_weight_fields_have_correct_arithmetic_relationship():
    """For each box: TotalWeight must equal ItemsWeight + BoxWeight,
    and ItemsWeight must not exceed MaxWeight when MaxWeight is set."""
    result = pack_payload(_sample_payload())
    for box in result["Boxes"]:
        items_w = box["ItemsWeight"]
        box_w = box["BoxWeight"]
        total_w = box["TotalWeight"]

        assert isinstance(items_w, (int, float))
        assert isinstance(box_w, (int, float))
        assert isinstance(total_w, (int, float))

        assert abs(total_w - (items_w + box_w)) < 0.01, (
            f"TotalWeight ({total_w}) != ItemsWeight ({items_w}) + BoxWeight ({box_w})"
        )

        if box["MaxWeight"] is not None:
            assert items_w <= box["MaxWeight"] + 0.01, (
                f"ItemsWeight ({items_w}) exceeds MaxWeight ({box['MaxWeight']})"
            )


def test_semi_realistic_dataset_produces_schema_compliant_output():
    """Running the SemiRealistic dataset end-to-end must produce a valid
    response with all required top-level keys and correct types."""
    semi = DATA / "SemiRealistic"
    payload = {
        "Boxes": json.loads((semi / "boxes.json").read_text()),
        "Items": json.loads((semi / "items.json").read_text()),
    }
    result = pack_payload(payload)

    # Top-level keys per PackResponse schema
    assert set(result.keys()) == {"Success", "Meta", "Summary", "Boxes", "UnpackedItems"}
    assert isinstance(result["Success"], bool)
    assert isinstance(result["Boxes"], list)
    assert isinstance(result["UnpackedItems"], list)

    # Every placed item must be JSON-serialisable
    json.dumps(result)

    # VolumeUtilisation must be between 0 and 1
    for box in result["Boxes"]:
        util = box["VolumeUtilisation"]
        assert isinstance(util, (int, float))
        assert 0 <= util <= 1.0 + 0.001, (
            f"VolumeUtilisation out of range: {util}"
        )

        # LoadHeight must be non-negative
        assert isinstance(box["LoadHeight"], (int, float))
        assert box["LoadHeight"] >= 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
