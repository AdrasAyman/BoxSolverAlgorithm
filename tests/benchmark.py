"""Runtime + quality benchmark.

The design priority is optimal allocation first, subject to the solve finishing
in a reasonable time. This script is the evidence for that requirement.

    python tests/benchmark.py
"""

from __future__ import annotations

import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from boxsolver import BoxType, Item, Packer, PackingConfig

BOXES = [
    BoxType("XS", 150, 150, 150, max_weight=8.5, box_weight=0.5),
    BoxType("SML", 250, 200, 150, max_weight=10.0, box_weight=0.6),
    BoxType("MED", 400, 400, 400, max_weight=15.2, box_weight=0.75),
    BoxType("LRG", 600, 400, 400, max_weight=25.0, box_weight=1.2),
]


def make_order(count: int, seed: int, density: float = 0.25) -> list[Item]:
    """A pseudo-random order.

    Weight is derived from volume at a realistic packaged-goods density
    (~0.25 kg per litre) rather than drawn independently. Drawing weight at
    random produces items denser than water, which makes every carton
    weight-limited and hides all volume-packing behaviour.
    """
    rng = random.Random(seed)
    groups = [None, None, "GROUP-A", "GROUP-B"]
    items = []
    for i in range(count):
        w = rng.randint(40, 220)
        l = rng.randint(40, 220)
        d = rng.randint(30, 180)
        litres = (w * l * d) / 1_000_000
        items.append(
            Item(
                code=f"ITM-{i:04d}",
                reference=f"Item {i}",
                width=w,
                length=l,
                depth=d,
                weight=round(max(0.05, litres * density * rng.uniform(0.7, 1.3)), 2),
                box_group=rng.choice(groups),
            )
        )
    return items


def run(count: int, repeats: int = 3) -> None:
    timings: list[float] = []
    utilisations: list[float] = []
    boxes_used: list[int] = []
    unpacked = 0

    for seed in range(repeats):
        items = make_order(count, seed)
        started = time.perf_counter()
        solution = Packer(BOXES, PackingConfig(time_limit_seconds=60)).pack(items)
        timings.append(time.perf_counter() - started)

        used = sum(c.used_volume for c in solution.containers)
        capacity = sum(c.box_type.volume for c in solution.containers)
        utilisations.append(used / capacity if capacity else 0.0)
        boxes_used.append(len(solution.containers))
        unpacked += len(solution.unpacked)

    print(
        f"{count:>5} items | "
        f"median {statistics.median(timings):>7.3f}s | "
        f"max {max(timings):>7.3f}s | "
        f"{statistics.mean(boxes_used):>6.1f} boxes | "
        f"{statistics.mean(utilisations) * 100:>5.1f}% full | "
        f"{unpacked} unpacked"
    )


if __name__ == "__main__":
    print("BoxSolverAlgorithm benchmark - mixed sizes, mixed box groups, gravity on\n")
    for size in (10, 25, 50, 100, 200, 400):
        run(size)
