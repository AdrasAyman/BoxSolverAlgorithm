"""BoxSolverAlgorithm regression tests.

Run with:  pytest -q       (or: python tests/test_boxsolver.py)
"""

from __future__ import annotations

import time
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from boxsolver import (
    SCHEMA_VERSION,
    BoxType,
    Item,
    Packer,
    PackingConfig,
    PackRequest,
    SchemaError,
    pack_payload,
    solve,
)
from boxsolver.geometry import ORIENTATIONS, candidate_orientations, rotation_matrix

DATA = ROOT / "data"


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #
def test_there_are_exactly_six_orientations():
    assert len(ORIENTATIONS) == 6
    assert len({o.code for o in ORIENTATIONS}) == 6
    assert ORIENTATIONS[0].code == "WLD"
    assert ORIENTATIONS[0].rotation_degrees == (0, 0, 0)


def test_euler_angles_actually_produce_the_stated_axis_map():
    """The three orientation representations must agree.

    Applying the reported RotationDegrees to the item's own axes must land the
    dimension named in AxisMap on that world axis - otherwise a renderer
    would draw a correctly-sized box with the artwork rotated the wrong way.
    """
    labels = ("Width", "Length", "Depth")
    for orientation in ORIENTATIONS:
        rx, ry, rz = orientation.rotation_degrees
        matrix = rotation_matrix(rx, ry, rz)
        for world_axis, axis_name in enumerate("XYZ"):
            source = orientation.permutation[world_axis]
            assert abs(matrix[world_axis][source]) == 1
            assert orientation.axis_map[axis_name] == labels[source]


def test_orientation_extents_are_a_permutation_of_the_item():
    for orientation in ORIENTATIONS:
        assert sorted(orientation.apply(10, 20, 30)) == [10, 20, 30]


def test_a_cube_has_only_one_distinct_orientation():
    assert len(candidate_orientations(100, 100, 100)) == 1
    assert len(candidate_orientations(100, 100, 50)) == 3
    assert len(candidate_orientations(10, 20, 30)) == 6


def test_keep_upright_restricts_to_two_orientations():
    item = Item("A", "A", 10, 20, 30, 1.0, keep_upright=True)
    codes = {c for c in (item.rotation_codes or ())}
    assert codes == {"WLD", "LWD"}


# --------------------------------------------------------------------------- #
# schema parsing
# --------------------------------------------------------------------------- #
def test_sample_data_files_parse():
    payload = {
        "Boxes": json.loads((DATA / "boxes.json").read_text()),
        "Items": json.loads((DATA / "items.json").read_text()),
    }
    request = PackRequest.from_dict(payload)
    assert [b.reference for b in request.boxes] == ["SML", "MED", "LRG"]
    assert len(request.items) == 3
    assert request.items[1].box_group is None  # BoxGroup omitted => flexible


def test_missing_required_field_is_reported_with_context():
    with pytest.raises(SchemaError) as exc:
        PackRequest.from_dict(
            {
                "Boxes": [{"Reference": "SML", "Width": 10, "Length": 10, "Depth": 10}],
                "Items": [{"ItemCode": "X", "ItemReference": "X", "Width": 1, "Length": 1}],
            }
        )
    assert "Depth" in str(exc.value)
    assert "X" in str(exc.value)


def test_negative_and_zero_dimensions_are_rejected():
    for bad in (0, -5):
        with pytest.raises(SchemaError):
            BoxType.from_dict({"Reference": "B", "Width": bad, "Length": 10, "Depth": 10})


def test_duplicate_box_reference_is_rejected():
    with pytest.raises(SchemaError):
        PackRequest.from_dict(
            {
                "Boxes": [
                    {"Reference": "SML", "Width": 10, "Length": 10, "Depth": 10},
                    {"Reference": "SML", "Width": 20, "Length": 20, "Depth": 20},
                ],
                "Items": [
                    {
                        "ItemCode": "A",
                        "ItemReference": "A",
                        "Width": 1,
                        "Length": 1,
                        "Depth": 1,
                        "Weight": 0.1,
                    }
                ],
            }
        )


def test_field_names_are_case_insensitive():
    box = BoxType.from_dict(
        {"reference": "SML", "width": 10, "length": 10, "depth": 10, "boxWeight": 0.2}
    )
    assert box.reference == "SML" and box.box_weight == 0.2


# --------------------------------------------------------------------------- #
# packing invariants
# --------------------------------------------------------------------------- #
def _boxes():
    return [
        BoxType("SML", 150, 150, 150, max_weight=8.5, box_weight=0.5, maximum_boxes=100),
        BoxType("MED", 400, 400, 400, max_weight=15.2, box_weight=0.75),
        BoxType("LRG", 1200, 1200, 1200, active=False),
    ]


def _overlaps(a, b) -> bool:
    return (
        a.x < b.x + b.dx and b.x < a.x + a.dx
        and a.y < b.y + b.dy and b.y < a.y + a.dy
        and a.z < b.z + b.dz and b.z < a.z + a.dz
    )


def _assert_physically_valid(solution):
    for container in solution.containers:
        w, l, d = container.box_type.inner_dimensions
        for i, p in enumerate(container.placements):
            assert p.x >= 0 and p.y >= 0 and p.z >= 0
            assert p.x + p.dx <= w + 1e-6
            assert p.y + p.dy <= l + 1e-6
            assert p.z + p.dz <= d + 1e-6
            for q in container.placements[i + 1:]:
                assert not _overlaps(p, q), f"{p.instance.instance_id} overlaps {q.instance.instance_id}"
        if container.box_type.max_weight is not None:
            assert container.items_weight <= container.box_type.max_weight + 1e-6


def test_sample_order_packs_completely_and_validly():
    payload = {
        "Boxes": json.loads((DATA / "boxes.json").read_text()),
        "Items": json.loads((DATA / "items.json").read_text()),
    }
    solution = solve(PackRequest.from_dict(payload))
    assert solution.success
    assert sum(len(c.placements) for c in solution.containers) == 3
    _assert_physically_valid(solution)


def test_inactive_boxes_are_never_used():
    items = [Item("A", "A", 100, 100, 100, 1.0)]
    solution = Packer(_boxes()).pack(items)
    assert all(c.box_type.reference != "LRG" for c in solution.containers)


def test_box_groups_are_never_mixed():
    items = [
        Item("A", "A", 50, 50, 50, 0.5, box_group="GROUP-A"),
        Item("B", "B", 50, 50, 50, 0.5, box_group="GROUP-B"),
    ]
    solution = Packer(_boxes()).pack(items)
    assert len(solution.containers) == 2
    for container in solution.containers:
        groups = {p.instance.item.box_group for p in container.placements}
        assert len(groups) == 1


def test_ungrouped_items_may_share_a_group_carton_when_allowed():
    items = [
        Item("A", "A", 50, 50, 50, 0.5, box_group="GROUP-A"),
        Item("B", "B", 50, 50, 50, 0.5),
    ]
    shared = Packer(_boxes()).pack(items)
    assert len(shared.containers) == 1

    strict = Packer(_boxes(), PackingConfig(allow_ungrouped_to_share=False)).pack(items)
    assert len(strict.containers) == 2


def test_weight_limit_opens_a_second_carton():
    # Eight 2 kg items fit dimensionally in one SML but exceed its 8.5 kg rating.
    items = [Item(f"I{i}", f"I{i}", 70, 70, 70, 2.0) for i in range(8)]
    boxes = [BoxType("SML", 150, 150, 150, max_weight=8.5, box_weight=0.5)]
    solution = Packer(boxes).pack(items)
    assert len(solution.containers) >= 2
    for container in solution.containers:
        assert container.items_weight <= 8.5 + 1e-6


def test_max_weight_excludes_box_weight():
    """MaxWeight is the rated capacity of the carton and excludes BoxWeight."""
    boxes = [BoxType("SML", 150, 150, 150, max_weight=8.0, box_weight=0.5)]
    items = [Item("A", "A", 100, 100, 100, 8.0)]
    solution = Packer(boxes).pack(items)
    assert solution.success, "8.0 kg of items must fit an 8.0 kg rated box"
    container = solution.containers[0]
    assert container.items_weight == pytest.approx(8.0)
    assert container.total_weight == pytest.approx(8.5)  # despatch weight adds the carton


def test_maximum_boxes_quota_is_respected():
    boxes = [BoxType("SML", 150, 150, 150, maximum_boxes=1)]
    items = [Item(f"I{i}", f"I{i}", 140, 140, 140, 0.1) for i in range(3)]
    solution = Packer(boxes).pack(items)
    assert len(solution.containers) == 1
    assert len(solution.unpacked) == 2
    assert all(u.reason_code == "BOX_QUOTA_EXHAUSTED" for u in solution.unpacked)


def test_oversized_item_is_reported_not_silently_dropped():
    items = [Item("HUGE", "Huge", 5000, 5000, 5000, 1.0)]
    solution = Packer(_boxes()).pack(items)
    assert not solution.success
    assert solution.unpacked[0].reason_code == "DOES_NOT_FIT"


def test_overweight_item_reports_the_weight_reason():
    items = [Item("HEAVY", "Heavy", 50, 50, 50, 500.0)]
    solution = Packer(_boxes()).pack(items)
    assert solution.unpacked[0].reason_code == "EXCEEDS_MAX_WEIGHT"

def test_no_single_box_satisfies_both_dimension_and_weight_reports_no_feasible_box():
    # fits dimensionally in ROOMY, fits on weight in STURDY, but no box does both
    boxes = [
        BoxType("ROOMY", 200, 200, 200, max_weight=1.0),
        BoxType("STURDY", 50, 50, 50, max_weight=100.0),
    ]
    items = [Item("A", "A", 100, 100, 100, 5.0)]
    solution = Packer(boxes).pack(items)
    assert solution.unpacked[0].reason_code == "NO_FEASIBLE_BOX"

def test_quantity_expands_into_separate_instances():
    items = [Item("A", "A", 70, 70, 70, 0.2, quantity=4)]
    solution = Packer(_boxes()).pack(items)
    ids = [p.instance.instance_id for c in solution.containers for p in c.placements]
    assert sorted(ids) == ["A#1", "A#2", "A#3", "A#4"]


def test_support_constraint_keeps_items_grounded():
    """With gravity on, nothing may hang in mid-air."""
    items = [Item(f"I{i}", f"I{i}", 60, 60, 60, 0.2, quantity=1) for i in range(6)]
    solution = Packer(_boxes(), PackingConfig(require_support=True)).pack(items)
    for container in solution.containers:
        for p in container.placements:
            if p.z <= 1e-6:
                continue
            ratio = container._support_ratio(p.x, p.y, p.z, p.dx, p.dy)
            assert ratio >= 0.50 - 1e-6


def test_downsize_pass_avoids_an_oversized_final_carton():
    """One tiny item must land in the SML, not in whatever carton was opened first."""
    boxes = [
        BoxType("SML", 150, 150, 150, box_weight=0.5),
        BoxType("MED", 400, 400, 400, box_weight=0.75),
    ]
    solution = Packer(boxes).pack([Item("A", "A", 100, 100, 100, 0.5)])
    assert solution.containers[0].box_type.reference == "SML"


def test_perfect_fill_uses_a_single_carton():
    """Eight 75mm cubes exactly fill one 150mm carton."""
    boxes = [BoxType("SML", 150, 150, 150, box_weight=0.5)]
    items = [Item("C", "Cube", 75, 75, 75, 0.1, quantity=8)]
    solution = Packer(boxes).pack(items)
    assert len(solution.containers) == 1
    assert solution.containers[0].utilisation == pytest.approx(1.0)
    _assert_physically_valid(solution)


def test_solver_is_deterministic():
    payload = {
        "Boxes": json.loads((DATA / "boxes.json").read_text()),
        "Items": json.loads((DATA / "items.json").read_text()),
    }
    first = pack_payload(payload)
    second = pack_payload(payload)
    first["Meta"]["SolveTimeMs"] = second["Meta"]["SolveTimeMs"] = 0
    assert first == second

def test_time_limit_reached_flag_is_set_when_solver_is_truncated(monkeypatch):
    real_perf_counter = time.perf_counter
    calls = {"n": 0}

    def fake_perf_counter():
        calls["n"] += 1
        return real_perf_counter() if calls["n"] == 1 else real_perf_counter() + 10

    monkeypatch.setattr(time, "perf_counter", fake_perf_counter)
    items = [Item("A", "A", 70, 70, 70, 0.2, quantity=4)]
    solution = Packer(_boxes(), PackingConfig(time_limit_seconds=0.1)).pack(items)
    assert solution.truncated is True

def test_no_rotation_option_keeps_natural_orientation():
    items = [Item("A", "A", 100, 200, 50, 1.0)]
    boxes = [BoxType("MED", 400, 400, 400)]
    solution = Packer(boxes, PackingConfig(allow_rotation=False)).pack(items)
    placement = solution.containers[0].placements[0]
    assert (placement.dx, placement.dy, placement.dz) == (100, 200, 50)


# --------------------------------------------------------------------------- #
# response contract
# --------------------------------------------------------------------------- #
def test_response_shape_matches_the_agreed_contract():
    payload = {
        "Boxes": json.loads((DATA / "boxes.json").read_text()),
        "Items": json.loads((DATA / "items.json").read_text()),
    }
    result = pack_payload(payload)
    assert set(result) == {"Success", "Meta", "Summary", "Boxes", "UnpackedItems"}
    box = result["Boxes"][0]
    assert set(box) >= {
        "BoxIndex", "Reference", "BoxGroup", "Dimensions", "ItemCount",
        "ItemsWeight", "BoxWeight", "TotalWeight", "VolumeUtilisation", "Items",
    }
    placed = box["Items"][0]
    assert set(placed["Position"]) == {"X", "Y", "Z"}
    assert set(placed["Dimensions"]) == {"Width", "Length", "Depth"}
    assert set(placed["Orientation"]) == {"Code", "AxisMap", "RotationDegrees"}
    assert placed["Sequence"] == 0
    json.dumps(result)  # must be JSON-serialisable end to end

def test_meta_contract_values_match_the_agreed_schema():
    # Downstream consumers hard-code these, so they can't drift silently
    payload = {
        "Boxes": json.loads((DATA / "boxes.json").read_text()),
        "Items": json.loads((DATA / "items.json").read_text()),
    }
    result = pack_payload(payload)
    meta = result["Meta"]
    assert meta["Engine"] == "boxsolver"
    assert meta["SchemaVersion"] == SCHEMA_VERSION
    assert meta["Units"] == {"Length": "mm", "Weight": "kg"}
    assert meta["CoordinateSystem"] == {
        "Origin": "internal front-bottom-left corner of the box",
        "X": "Width", "Y": "Length", "Z": "Depth (vertical)",
        "PositionMeaning": "minimum corner of the item's bounding box",
    }


def test_overall_utilisation_is_zero_when_nothing_is_packed():
    items = [Item("HUGE", "Huge", 5000, 5000, 5000, 1.0)]
    solution = Packer(_boxes()).pack(items)
    result = solution.to_dict()
    assert result["Summary"]["BoxCount"] == 0
    assert result["Summary"]["OverallVolumeUtilisation"] == 0.0

@pytest.mark.xfail(reason="load-bearing enforcement not yet in Container._is_valid, tracked on Trello (Andrew's file)")
def test_nothing_may_be_placed_on_top_of_a_fragile_item():
    boxes = [BoxType("SML", 150, 150, 150, box_weight=0.5)]
    items = [
        Item("F", "Fragile Glassware", 150, 150, 50, 1.0, fragile=True),
        Item("S", "Stacked Item", 150, 150, 50, 1.0),
    ]
    solution = Packer(boxes).pack(items)
    container = solution.containers[0]
    fragile_placements = [p for p in container.placements if p.instance.item.fragile]
    for p in container.placements:
        if p.instance.item.fragile:
            continue
        for f in fragile_placements:
            overlap_x = min(p.x + p.dx, f.x + f.dx) - max(p.x, f.x)
            overlap_y = min(p.y + p.dy, f.y + f.dy) - max(p.y, f.y)
            resting_on_top = abs(p.z - (f.z + f.dz)) < 1e-6
            assert not (resting_on_top and overlap_x > 1e-6 and overlap_y > 1e-6), \
                f"{p.instance.instance_id} is resting on fragile item {f.instance.instance_id}"
def test_box_counts_aggregate_same_reference_across_multiple_boxes():
    boxes = [BoxType("SML", 150, 150, 150, box_weight=0.5)]
    items = [Item("C", "Cube", 75, 75, 75, 0.1, quantity=16)]
    solution = Packer(boxes).pack(items)
    result = solution.to_dict()
    assert len(solution.containers) == 2
    assert result["Summary"]["BoxCounts"] == {"SML": 2}

def test_placement_order_is_a_valid_loading_sequence():
    """Sequence n must never be buried under sequence < n (loaders pack bottom-up)."""
    items = [Item("C", "Cube", 70, 70, 70, 0.2, quantity=8)]
    solution = Packer(_boxes()).pack(items)
    for container in solution.containers:
        for i, later in enumerate(container.placements):
            for earlier in container.placements[:i]:
                assert earlier.z <= later.z + 1e-6


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# --------------------------------------------------------------------------- #
# HTTP transport
#
# The engine is consumed over HTTP. These tests cover the wire
# contract: status codes, error bodies, and the RequestId passthrough. They use
# FastAPI's TestClient, which exercises the real app without opening a port.
# --------------------------------------------------------------------------- #
import pytest as _pytest

_fastapi = _pytest.importorskip("fastapi", reason="HTTP transport tests need FastAPI")
from fastapi.testclient import TestClient  # noqa: E402

from boxsolver.entrypoints.api import app  # noqa: E402


@_pytest.fixture
def client():
    return TestClient(app)


def _sample_payload():
    return {
        "Boxes": json.loads((DATA / "boxes.json").read_text()),
        "Items": json.loads((DATA / "items.json").read_text()),
    }


def test_health_endpoint_reports_the_engine_version(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["engine"] == "boxsolver"


def test_orientations_endpoint_returns_all_six(client):
    body = client.get("/v1/orientations").json()
    assert len(body["Orientations"]) == 6
    codes = {o["Code"] for o in body["Orientations"]}
    assert codes == {"WLD", "LWD", "WDL", "DLW", "LDW", "DWL"}


def test_pack_endpoint_returns_a_solution(client):
    response = client.post("/v1/pack", json=_sample_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["Success"] is True
    assert body["Meta"]["SchemaVersion"] == SCHEMA_VERSION
    assert body["Summary"]["PackedItemCount"] == 3


def test_http_response_matches_the_in_process_response(client):
    """The two call paths must never disagree.

    api.py is deliberately thin so that HTTP and a direct pack_payload() call
    produce identical results. If this ever fails, logic has leaked into the
    transport layer.
    """
    payload = _sample_payload()
    over_http = client.post("/v1/pack", json=payload).json()
    in_process = pack_payload(payload)
    over_http["Meta"]["SolveTimeMs"] = in_process["Meta"]["SolveTimeMs"] = 0
    assert over_http == in_process


def test_malformed_payload_returns_422_naming_the_field(client):
    payload = _sample_payload()
    payload["Items"] = [
        {"ItemCode": "X", "ItemReference": "X", "Width": 1, "Length": 1, "Depth": 1}
    ]  # Weight is missing
    response = client.post("/v1/pack", json=payload)
    assert response.status_code == 422
    body = response.json()
    assert body["Error"] == "SCHEMA_ERROR"
    assert "Weight" in body["Message"], "the 422 body must name the offending field"


def test_both_kinds_of_422_share_one_body_shape(client):
    """Callers must not have to branch on which validation layer rejected them.

    Pydantic catches shape problems and models.py catches semantic ones. Left
    alone they return different bodies (a list vs a dict). validation_error in
    api.py normalises them.
    """
    payload = _sample_payload()
    base_item = {"ItemCode": "X", "ItemReference": "X", "Width": 1, "Length": 1, "Depth": 1}

    shape_problem = client.post(
        "/v1/pack", json={**payload, "Items": [base_item]}  # Weight missing entirely
    )
    semantic_problem = client.post(
        "/v1/pack", json={**payload, "Items": [{**base_item, "Weight": -5}]}  # invalid value
    )

    for response in (shape_problem, semantic_problem):
        assert response.status_code == 422
        body = response.json()
        assert set(body) == {"Error", "Message"}, "both 422s must have the same keys"
        assert body["Error"] == "SCHEMA_ERROR"
        assert "Weight" in body["Message"]


def test_missing_required_top_level_field_is_rejected(client):
    assert client.post("/v1/pack", json={"Items": []}).status_code == 422


def test_unpackable_item_returns_200_not_an_error(client):
    """A partial solution is a legitimate result.

    One impossible item must not fail a whole order - callers switch on
    Success and ReasonCode, not on the HTTP status.
    """
    payload = _sample_payload()
    payload["Items"].append(
        {
            "ItemCode": "HUGE",
            "ItemReference": "Oversized crate",
            "Width": 9000,
            "Length": 9000,
            "Depth": 9000,
            "Weight": 1.0,
        }
    )
    response = client.post("/v1/pack", json=payload)
    assert response.status_code == 200, "an unpackable item is not an HTTP error"
    body = response.json()
    assert body["Success"] is False
    assert body["UnpackedItems"][0]["ReasonCode"] == "DOES_NOT_FIT"
    assert len(body["Boxes"]) > 0, "the packable items must still be returned"


def test_request_id_is_echoed_back_for_correlation(client):
    payload = {**_sample_payload(), "RequestId": "ORDER-8891"}
    body = client.post("/v1/pack", json=payload).json()
    assert body["Meta"]["RequestId"] == "ORDER-8891"


def test_request_id_is_absent_when_not_supplied(client):
    body = client.post("/v1/pack", json=_sample_payload()).json()
    assert "RequestId" not in body["Meta"]


def test_options_are_honoured_over_http(client):
    """Callers can steer the solver, not just call it."""
    payload = {**_sample_payload(), "Options": {"AllowRotation": False}}
    body = client.post("/v1/pack", json=payload).json()
    for box in body["Boxes"]:
        for placed in box["Items"]:
            assert placed["Orientation"]["Code"] == "WLD", "rotation should be disabled"


def test_invalid_option_value_is_rejected(client):
    payload = {**_sample_payload(), "Options": {"MinSupportRatio": 5.0}}
    assert client.post("/v1/pack", json=payload).status_code == 422