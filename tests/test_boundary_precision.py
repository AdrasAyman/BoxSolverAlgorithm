"""Boundary and floating-point precision tests for BoxSolverAlgorithm."""

import pytest

from boxsolver import BoxType, Item, Packer


@pytest.mark.parametrize("width, expected_reason", [(150.00, None), (150.01, "DOES_NOT_FIT")])
def test_dimension_boundary_accepts_exact_fit_and_rejects_over_limit(width, expected_reason):
    """This test checks exact dimensional fit and a small dimensional overflow."""
    boxes = [BoxType("SML", 150, 150, 150)]
    solution = Packer(boxes).pack([Item("A", "A", width, 1, 1, 0.1)])

    if expected_reason is None:
        assert solution.success
        assert len(solution.containers[0].placements) == 1
    else:
        assert not solution.success
        assert solution.unpacked[0].reason_code == expected_reason


@pytest.mark.parametrize("weight, expected_reason", [(8.0, None), (8.01, "EXCEEDS_MAX_WEIGHT")])
def test_weight_boundary_accepts_exact_capacity_and_rejects_over_limit(weight, expected_reason):
    """This test checks the exact item weight capacity and a small excess."""
    boxes = [BoxType("SML", 150, 150, 150, max_weight=8.0)]
    solution = Packer(boxes).pack([Item("A", "A", 10, 10, 10, weight)])

    if expected_reason is None:
        assert solution.success
        assert solution.containers[0].items_weight == pytest.approx(8.0)
    else:
        assert not solution.success
        assert solution.unpacked[0].reason_code == expected_reason


def test_single_item_order_packs_into_one_box():
    """This test checks that one valid item creates one box with one placement."""
    boxes = [BoxType("SML", 150, 150, 150)]
    solution = Packer(boxes).pack([Item("A", "A", 20, 30, 40, 0.5)])

    assert solution.success
    assert len(solution.containers) == 1
    assert len(solution.containers[0].placements) == 1


def test_inactive_only_box_configuration_does_not_pack_items():
    """This test checks that inactive boxes cannot receive valid items."""
    boxes = [BoxType("SML", 150, 150, 150, active=False)]
    solution = Packer(boxes).pack([Item("A", "A", 20, 30, 40, 0.5)])

    assert not solution.success
    assert solution.containers == []
    assert solution.unpacked[0].reason_code == "DOES_NOT_FIT"
