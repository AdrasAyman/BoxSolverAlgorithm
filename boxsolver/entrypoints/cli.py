"""Command line interface.

    python -m boxsolver.entrypoints.cli --boxes data/boxes.json --items data/items.json
    python -m boxsolver.entrypoints.cli -b data/boxes.json -i data/items.json -o result.json --pretty

WHAT THIS FILE IS FOR
---------------------
This is one of two "doorways" into BoxSolverAlgorithm. It lets a human run the packer
from a terminal. The other doorway is entrypoints/api.py, which lets another
program call us over HTTP.

The important thing to understand: NEITHER doorway contains any packing logic.
Both do the same three things -

    1. gather the input (from files here, from an HTTP body in api.py)
    2. call solve()  <- the engine, in core/packer.py
    3. present the answer

That is deliberate. If the packing logic lived in here, the terminal and the
HTTP service could drift apart and give different answers for the same order.
Keeping both thin means there is exactly one implementation to trust.

"""

# `from __future__ import annotations` makes Python treat every type hint as
# plain text rather than evaluating it at import time. It makes startup slightly
# faster and lets us write newer-style hints on older Python versions.
from __future__ import annotations

import argparse  # builds the --flags and generates --help for free
import json      # reads the input files, writes the output file
import sys       # gives us sys.stderr, so errors don't pollute normal output
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# These three imports are the entire dependency on the rest of the project.
# The `..` means "go up one folder", because this file lives in
# boxsolver/entrypoints/ and these modules live in boxsolver/.
from ..models import PackRequest, SchemaError   # input validation
from ..core.packer import solve                 # THE ENGINE
from ..output import Solution                   # the result object


# --------------------------------------------------------------------------- #
# PART 1 - reading the input files
# --------------------------------------------------------------------------- #
def _load(path: Path) -> Any:
    """Read one JSON file, or exit with a message a human can act on.

    The leading underscore in the name is a Python convention meaning "internal
    to this file" - nothing outside cli.py should call it.

    Notice this function is six lines and five of them are error handling. That
    ratio is on purpose. A typo in a filename should produce
        error: file not found: data/boxs.json
    not a fifteen-line traceback. SystemExit stops the program immediately with
    just that message.
    """
    try:
        # read_text() opens the file, reads it, and closes it.
        # json.loads() turns that text into Python lists and dicts.
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # The user mistyped a path, or is running from the wrong directory.
        raise SystemExit(f"error: file not found: {path}")
    except json.JSONDecodeError as exc:
        # The file exists but is not valid JSON - usually a trailing comma or a
        # missing bracket. We include `exc` because it names the line and column.
        raise SystemExit(f"error: {path} is not valid JSON ({exc})")


# --------------------------------------------------------------------------- #
# PART 2 - turning the result into readable text
# --------------------------------------------------------------------------- #
def render_text(solution: Solution) -> str:
    """Format a Solution as the human-readable summary you see in the terminal.

    ONE SUBTLE BUT IMPORTANT DETAIL, on the very first line of the body:
    we call solution.to_dict() and then format THE DICTIONARY, not the Solution
    object itself.

    Why it matters: to_dict() produces exactly the JSON an API caller receives.
    By formatting that dictionary, the terminal output is guaranteed to reflect
    what a caller actually gets. If a field were missing from the JSON, it would be
    missing here too, and you would spot it immediately. A formatter that read
    the internal objects could look perfectly correct while the JSON was broken -
    which is the kind of bug that only surfaces during integration week.
    """
    lines: List[str] = []          # we build up a list of lines...
    data = solution.to_dict()      # ...from the same dict callers receive
    summary = data["Summary"]

    # -- the headline ------------------------------------------------------- #
    # f-strings let us drop values straight into text. The `:.1f` means
    # "one decimal place", so 0.0763 * 100 prints as 7.6 rather than
    # 7.62999999999.
    lines.append(
        f"Packed {summary['PackedItemCount']} item(s) into {summary['BoxCount']} box(es) "
        f"in {data['Meta']['SolveTimeMs']} ms "
        f"({summary['OverallVolumeUtilisation'] * 100:.1f}% volume used)"
    )

    # -- one block per carton ----------------------------------------------- #
    for box in data["Boxes"]:
        # Only mention the BoxGroup if the carton actually has one. Ungrouped
        # cartons have BoxGroup = None, which is falsy, so we print nothing.
        group = f", group {box['BoxGroup']}" if box["BoxGroup"] else ""

        lines.append("")   # a blank line between cartons, purely for readability
        lines.append(
            f"  Box {box['BoxIndex']}: {box['Reference']} "
            f"({box['Dimensions']['Width']}x{box['Dimensions']['Length']}x"
            f"{box['Dimensions']['Depth']} mm{group})"
        )

        # The weight line spells out the arithmetic on purpose, because the
        # distinction matters and people get it wrong:
        #   ItemsWeight  = the contents           <- this is what MaxWeight caps
        #   BoxWeight    = the empty carton (tare)
        #   TotalWeight  = the two added together <- this is the despatch weight
        lines.append(
            f"    weight {box['ItemsWeight']} kg items + {box['BoxWeight']} kg box "
            f"= {box['TotalWeight']} kg"
            # Only show the limit if this box type has one. MaxWeight is
            # optional in the schema, and `is not None` matters here rather than
            # a plain truthiness check, because a limit of 0 would be falsy.
            + (f" (limit {box['MaxWeight']} kg)" if box["MaxWeight"] is not None else "")
        )
        lines.append(f"    utilisation {box['VolumeUtilisation'] * 100:.1f}%")

        # -- one line per item inside this carton --------------------------- #
        for placed in box["Items"]:
            pos = placed["Position"]     # the item's minimum corner, in mm
            dim = placed["Dimensions"]   # its size AS PLACED, already rotated
            lines.append(
                # [0] is the load sequence - the order a picker should pack them.
                f"      [{placed['Sequence']}] "
                # :<12 left-aligns the ID in a 12-character column so the
                # coordinates line up vertically and are easy to scan.
                f"{placed['InstanceId']:<12} "
                # :>7 right-aligns each number in 7 characters, same reason.
                f"at ({pos['X']:>7}, {pos['Y']:>7}, {pos['Z']:>7}) "
                f"size {dim['Width']}x{dim['Length']}x{dim['Depth']} "
                # WLD means unrotated. Anything else means the item was turned:
                # DLW, for example, means its Depth now lies along X.
                f"orient {placed['Orientation']['Code']}"
            )

    # -- anything we could not pack ----------------------------------------- #
    # This block only appears when something failed. An unpackable item is a
    # legitimate outcome, not a crash - one impossible item should not fail a
    # 200-line order.
    if data["UnpackedItems"]:
        lines.append("")
        lines.append("  Unpacked:")
        for unpacked in data["UnpackedItems"]:
            # ReasonCode is the machine-readable one (callers switch on it).
            # Reason is the human sentence, safe to show a warehouse operator.
            lines.append(
                f"    {unpacked['InstanceId']} - {unpacked['ReasonCode']}: {unpacked['Reason']}"
            )

    # Join the list into one string with newlines between each entry. Building a
    # list and joining once is faster and cleaner than repeatedly gluing strings.
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# PART 3 - defining the command line flags
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    """Describe every flag the command accepts.

    argparse is part of Python's standard library. We describe the flags once
    here and it handles parsing, type conversion, error messages for bad input,
    and generating `--help` automatically.
    """
    parser = argparse.ArgumentParser(
        prog="boxsolver",                                        # name shown in --help
        description="Pack items into the fewest / tightest cartons.",
    )

    # Each argument gets a short form (-b) and a long form (--boxes).
    #   required=True  -> the program refuses to start without it
    #   type=Path      -> argparse converts the text into a Path object for us
    #   help=...       -> the text that appears in --help
    parser.add_argument("-b", "--boxes", required=True, type=Path, help="BoxType[] JSON file")
    parser.add_argument("-i", "--items", required=True, type=Path, help="Item[] JSON file")

    # No required=True, so --output is optional. If omitted, args.output is None.
    parser.add_argument("-o", "--output", type=Path, help="write the JSON response here")

    # `choices` means argparse rejects anything else before our code runs.
    # This is the switch that answers client question CQ-1: if Thomax is billed
    # on dimensional weight rather than carton count, min_volume becomes the
    # right default and this is the only line that changes.
    parser.add_argument(
        "--objective",
        choices=("min_boxes", "min_volume"),
        default="min_boxes",
        help="optimisation objective (default: min_boxes)",
    )

    # action="store_true" makes an on/off switch. You write `--no-rotation` with
    # nothing after it, and args.no_rotation becomes True.
    # The flags are phrased NEGATIVELY (--no-rotation, --no-support) because both
    # features default to ON, so the flag is how you turn them off.
    parser.add_argument("--no-rotation", action="store_true", help="disable item rotation")
    parser.add_argument(
        "--no-support", action="store_true", help="allow items to float (disable gravity check)"
    )

    # type=float converts the text "5.0" into the number 5.0.
    parser.add_argument(
        "--time-limit", type=float, default=20.0, help="soft solve budget in seconds"
    )

    parser.add_argument("--json", action="store_true", help="print JSON instead of a summary")
    parser.add_argument("--pretty", action="store_true", help="indent the JSON output")
    return parser


# --------------------------------------------------------------------------- #
# PART 4 - main(): where it all happens
# --------------------------------------------------------------------------- #
def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run one packing job. Returns the process exit code.

    `argv=None` means "read the real command line". Passing a list instead lets
    tests call main(["-b", "boxes.json", "-i", "items.json"]) directly, without
    launching a subprocess. Small design choice, big testing benefit.
    """
    args = build_parser().parse_args(argv)

    # -- STEP 1: build the request ------------------------------------------ #
    # This dictionary is EXACTLY the payload a caller would send over HTTP.
    # Same shape, same field names. That is what makes this file a faithful way
    # to reproduce a reported bug.
    payload: Dict[str, Any] = {
        "Boxes": _load(args.boxes),
        "Items": _load(args.items),
        "Options": {
            "Objective": args.objective,
            # Note the inversion: the user-facing flag is negative
            # (--no-rotation) but the config field is positive (AllowRotation).
            # `not args.no_rotation` bridges the two.
            "AllowRotation": not args.no_rotation,
            "RequireSupport": not args.no_support,
            "TimeLimitSeconds": args.time_limit,
        },
    }

    # -- STEP 2: validate, then solve --------------------------------------- #
    try:
        # Two things happen on this line, inner call first:
        #   PackRequest.from_dict(payload)  -> models.py checks the payload and
        #                                      turns it into typed objects
        #   solve(...)                      -> core/packer.py does the packing
        # This is the ONLY line in the whole file that does real work.
        solution = solve(PackRequest.from_dict(payload))
    except SchemaError as exc:
        # SchemaError is raised by models.py when the input is malformed. The
        # message names the exact field, e.g.
        #   Items[2] (ITM-003): required field 'Weight' is missing
        #
        # We print to sys.stderr, not stdout, so that piping the normal output
        # somewhere (`... > result.json`) still shows errors on screen.
        print(f"error: invalid input - {exc}", file=sys.stderr)
        return 2   # exit code 2 = bad input (see the note on codes below)

    # -- STEP 3: write the JSON file, if asked ------------------------------ #
    document = solution.to_dict()
    if args.output:
        args.output.write_text(
            # json.dumps turns the dict back into text.
            # indent=2 pretty-prints it; indent=None puts it all on one line.
            json.dumps(document, indent=2 if args.pretty else None), encoding="utf-8"
        )
        # This confirmation goes to stderr too, so it never contaminates piped output.
        print(f"wrote {args.output}", file=sys.stderr)

    # -- STEP 4: print something ------------------------------------------- #
    # The logic reads oddly but is deliberate: print to the screen if the user
    # asked for JSON, OR if they gave no output file (otherwise a plain
    # `-o result.json` run would appear to do nothing at all).
    if args.json or not args.output:
        if args.json:
            print(json.dumps(document, indent=2 if args.pretty else None))
        else:
            print(render_text(solution))

    # -- STEP 5: return an exit code --------------------------------------- #
    # Easy to overlook, genuinely useful. Every command line program returns a
    # number that scripts and CI can check without parsing any text:
    #     0 = everything packed
    #     1 = ran fine, but some items could not be packed
    #     2 = the input was invalid (returned earlier, above)
    # This is why `python -m boxsolver.entrypoints.cli ... && echo "all packed"`
    # behaves correctly - the shell reads this number.
    return 0 if solution.success else 1


# This block runs only when the file is executed directly, e.g. via
# `python -m boxsolver.entrypoints.cli`. It does NOT run when another module
# imports this file, which is what lets the tests import main() safely.
#
# SystemExit(main()) passes our return value out to the operating system as the
# process exit code.
if __name__ == "__main__":
    raise SystemExit(main())