"""Carton selection engine for BoxSolverAlgorithm.

This module contains First-Fit-Decreasing with best-fit container selection, over an extreme-point 
placement heuristic. It selects which
box type to use for each group of items and delegates the spatial placement
of individual items to Container (in container.py).

The pipeline is:  screen → group → pack_pool → downsize → return Solution.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .container import Container, UnpackedItem
from ..models import BoxType, Item, ItemInstance, PackingConfig, PackRequest, expand_instances
from ..output import Solution

EPS = 1e-6
__version__ = "0.1.0"


# --------------------------------------------------------------------------- #
# Packer: greedy best-fit carton selection
# --------------------------------------------------------------------------- #
class Packer:
    """Best-fit greedy carton selection over the corner-point placer."""

    def __init__(self, box_types: Sequence[BoxType], config: Optional[PackingConfig] = None):
        # Filter to active box types, sort by volume ascending for greedy selection.
        self.config = config or PackingConfig()
        self.all_box_types = list(box_types)
        self.box_types = sorted((b for b in box_types if b.active), key=lambda b: (b.volume, b.reference))
        self._quota: Dict[str, Optional[int]] = {b.reference: b.maximum_boxes for b in self.box_types}
        self._deadline: Optional[float] = None

    # ------------------------------------------------------------------- #
    # pack: main entry point that orchestrates the full solve pipeline
    # ------------------------------------------------------------------- #
    def pack(self, items: Sequence[Item]) -> Solution:
        """Run the full packing pipeline.

        Steps: expand quantities into instances, screen out impossible items,
        split by BoxGroup, pack each group, attempt ungrouped sharing, then
        downsize any oversized final cartons.
        """
        started = time.perf_counter()
        self._deadline = started + max(self.config.time_limit_seconds, 0.1)
        solution = Solution()
        instances = expand_instances(items)
        feasible, solution.unpacked = self._screen(instances)
        grouped: Dict[Optional[str], List[ItemInstance]] = {}
        for instance in feasible:
            grouped.setdefault(instance.item.box_group, []).append(instance)
        ungrouped = grouped.pop(None, [])
        for group in sorted(grouped, key=lambda g: str(g)):
            containers, failed = self._pack_pool(grouped[group], group)
            solution.containers.extend(containers)
            solution.unpacked.extend(failed)
        if ungrouped:
            remaining = self._sorted(ungrouped)
            if self.config.allow_ungrouped_to_share:
                still: List[ItemInstance] = []
                for instance in remaining:
                    if not any(c.try_add(instance) for c in solution.containers):
                        still.append(instance)
                remaining = still
            containers, failed = self._pack_pool(remaining, None)
            solution.containers.extend(containers)
            solution.unpacked.extend(failed)
        self._downsize(solution.containers)
        solution.truncated = self._out_of_time()
        solution.elapsed_ms = (time.perf_counter() - started) * 1000.0
        solution.unpacked.sort(key=lambda u: u.instance.instance_id)
        return solution

    # ------------------------------------------------------------------- #
    # time budget helpers
    # ------------------------------------------------------------------- #
    def _out_of_time(self) -> bool:
        """Return True if the solver has exceeded its time budget."""
        return self._deadline is not None and time.perf_counter() > self._deadline

    # ------------------------------------------------------------------- #
    # sorting: largest volume first for greedy placement order
    # ------------------------------------------------------------------- #
    def _sorted(self, instances: Iterable[ItemInstance]) -> List[ItemInstance]:
        """Sort instances by volume descending, then max dimension, weight,
        and instance ID for deterministic tie-breaking."""
        return sorted(instances, key=lambda i: (-i.item.volume, -max(i.item.width, i.item.length, i.item.depth), -i.item.weight, i.instance_id))

    # ------------------------------------------------------------------- #
    # screen: fast pre-filter that rejects items impossible to pack
    # ------------------------------------------------------------------- #
    def _screen(self, instances: Sequence[ItemInstance]) -> Tuple[List[ItemInstance], List[UnpackedItem]]:
        """Separate feasible items from impossible ones before the main loop.

        Each item is tested against every active box type. If no box can hold
        the item by both dimension and weight, it is classified with the
        appropriate reason code (DOES_NOT_FIT, EXCEEDS_MAX_WEIGHT, or
        NO_FEASIBLE_BOX).
        """
        ok: List[ItemInstance] = []
        bad: List[UnpackedItem] = []
        for instance in instances:
            item = instance.item
            feasible = fits_somewhere = weight_ok_somewhere = False
            for box_type in self.box_types:
                container = Container(box_type, self.config)
                dimension_ok = any(container._inside(0.0, 0.0, 0.0, dx, dy, dz) for _, (dx, dy, dz) in container._orientations(item))
                weight_ok = box_type.max_weight is None or item.weight <= box_type.max_weight + EPS
                fits_somewhere = fits_somewhere or dimension_ok
                weight_ok_somewhere = weight_ok_somewhere or weight_ok
                if dimension_ok and weight_ok:
                    feasible = True
                    break
            if feasible:
                ok.append(instance)
            elif not fits_somewhere:
                bad.append(UnpackedItem(instance, "DOES_NOT_FIT", "No active box type is large enough for this item in any orientation."))
            elif not weight_ok_somewhere:
                bad.append(UnpackedItem(instance, "EXCEEDS_MAX_WEIGHT", "Item weight exceeds the MaxWeight of every active box type."))
            else:
                bad.append(UnpackedItem(instance, "NO_FEASIBLE_BOX", "No single active box type satisfies both the dimensions and the weight limit for this item."))
        return ok, bad

    # ------------------------------------------------------------------- #
    # box type availability: quota tracking
    # ------------------------------------------------------------------- #
    def _available_box_types(self) -> List[BoxType]:
        """Return box types that still have remaining quota."""
        return [b for b in self.box_types if self._quota[b.reference] is None or self._quota[b.reference] > 0]  # type: ignore[operator]

    # ------------------------------------------------------------------- #
    # trial fill: test how many items a single box type can hold
    # ------------------------------------------------------------------- #
    def _trial_fill(self, box_type: BoxType, pool: Sequence[ItemInstance], group: Optional[str]) -> Tuple[Container, List[ItemInstance]]:
        """Create one container and greedily place as many items as possible.

        Returns the filled container and the list of items that were placed.
        """
        container = Container(box_type, self.config, group)
        packed: List[ItemInstance] = []
        for instance in pool:
            if container.try_add(instance):
                packed.append(instance)
        return container, packed

    # ------------------------------------------------------------------- #
    # scoring: rank trial-fill results to pick the best box type
    # ------------------------------------------------------------------- #
    def _score(self, container: Container, packed_count: int) -> Tuple[float, ...]:
        """Score a trial fill for comparison. Higher is better.

        min_boxes: prioritise packing more items, then higher utilisation.
        min_volume: prioritise utilisation, then item count.
        """
        if self.config.objective == "min_volume":
            return (container.utilisation, packed_count, -container.box_type.volume)
        return (packed_count, container.utilisation, -container.box_type.volume)

    # ------------------------------------------------------------------- #
    # pack pool: main loop that fills cartons until all items are placed
    # ------------------------------------------------------------------- #
    def _pack_pool(self, pool: Sequence[ItemInstance], group: Optional[str]) -> Tuple[List[Container], List[UnpackedItem]]:
        """Pack a group of items into cartons using greedy best-fit.

        For each iteration, try every available box type with trial_fill,
        pick the one with the best score, commit it, and remove the placed
        items from the remaining pool. Repeat until the pool is empty or
        no box type can accept any remaining item.
        """
        remaining = self._sorted(pool)
        containers: List[Container] = []
        failed: List[UnpackedItem] = []
        while remaining:
            available = self._available_box_types()
            if not available:
                failed.extend(UnpackedItem(i, "BOX_QUOTA_EXHAUSTED", "All box types have reached their MaximumBoxes limit.") for i in remaining)
                break
            best: Optional[Container] = None
            best_packed: List[ItemInstance] = []
            best_score: Optional[Tuple[float, ...]] = None
            for box_type in available:
                container, packed = self._trial_fill(box_type, remaining, group)
                if not packed:
                    continue
                score = self._score(container, len(packed))
                if best_score is None or score > best_score:
                    best_score, best, best_packed = score, container, packed
                if self._out_of_time() and best is not None:
                    break
            if best is None or not best_packed:
                failed.extend(UnpackedItem(i, "NO_FEASIBLE_BOX", "Item could not be placed in any remaining box type.") for i in remaining)
                break
            containers.append(best)
            self._consume(best.box_type.reference)
            packed_ids = {i.instance_id for i in best_packed}
            remaining = [i for i in remaining if i.instance_id not in packed_ids]
        return containers, failed

    # ------------------------------------------------------------------- #
    # quota management: consume and release box type allocations
    # ------------------------------------------------------------------- #
    def _consume(self, reference: str, amount: int = 1) -> None:
        """Decrement the remaining quota for a box type after using one."""
        quota = self._quota.get(reference)
        if quota is not None:
            self._quota[reference] = max(0, quota - amount)

    def _release(self, reference: str, amount: int = 1) -> None:
        """Increment the quota back when a box is freed during downsizing."""
        quota = self._quota.get(reference)
        if quota is not None:
            self._quota[reference] = quota + amount

    # ------------------------------------------------------------------- #
    # downsize: re-evaluate final cartons against smaller box types
    # ------------------------------------------------------------------- #
    def _downsize(self, containers: List[Container]) -> None:
        """Try to repack each container into a smaller box type.

        After the main loop, some cartons may be only partially filled.
        This pass checks whether all items in a container can fit into a
        smaller box type. If so, the container is replaced in-place and
        the quota is updated accordingly.
        """
        for index, container in enumerate(containers):
            if self._out_of_time():
                return
            instances = self._sorted([p.instance for p in container.placements])
            for box_type in self.box_types:
                if box_type.reference == container.box_type.reference or box_type.volume >= container.box_type.volume:
                    continue
                quota = self._quota.get(box_type.reference)
                if quota is not None and quota <= 0:
                    continue
                candidate, packed = self._trial_fill(box_type, instances, container.group)
                if len(packed) == len(instances):
                    self._release(container.box_type.reference)
                    self._consume(box_type.reference)
                    containers[index] = candidate
                    container = candidate
                    break


# --------------------------------------------------------------------------- #
# convenience entry points
# --------------------------------------------------------------------------- #
def solve(request: PackRequest) -> Solution:
    """Convenience entry point used by the API and CLI."""
    return Packer(request.boxes, request.options).pack(request.items)


def pack_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Raw dict in, raw dict out - the whole engine as one function."""
    return solve(PackRequest.from_dict(payload)).to_dict()