# Getting started (5 minutes)

## 1. Clone and open the folder

```bash
git clone https://github.com/AdrasAyman/BoxSolverAlgorithm.git
cd BoxSolverAlgorithm
```

If you use VS Code, install the **Python** extension — that's the only one needed.

## 2. Create a virtual environment

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows (PowerShell):

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Then in VS Code: `Ctrl/Cmd+Shift+P` → **Python: Select Interpreter** → pick the
one inside `.venv`.

> The **core engine needs no dependencies at all**. `requirements.txt` is only
> for the HTTP API and the tests. If you just want to run the packer,
> `python -m boxsolver.entrypoints.cli ...` works on a bare Python 3.10+.

## 3. Check it works

```bash
.venv/bin/pytest -q                                        # full test suite
.venv/bin/pytest -vv tests/test_boundary_precision.py      # boundary + precision
.venv/bin/python -m boxsolver.entrypoints.cli \
   -b data/boxes.json -i data/items.json                   # sample dataset
.venv/bin/python -m boxsolver.entrypoints.cli \
   -b data/SemiRealistic/boxes.json -i data/SemiRealistic/items.json
.venv/bin/python tests/benchmark.py                        # runtime evidence
```

Expected from the CLI:

```
Packed 3 item(s) into 2 box(es) in 1.3 ms (7.6% volume used)

  Box 0: MED (400.0x400.0x400.0 mm, group GROUP-A)
    weight 3.8 kg items + 0.75 kg box = 4.55 kg (limit 15.2 kg)
      [0] ITM-001#1    at (    0.0,     0.0,     0.0) size 100.0x200.0x50.0 orient WLD
      [1] ITM-002#1    at (  100.0,     0.0,     0.0) size 300.0x150.0x75.0 orient WLD
```

## 4. Start the API

```bash
uvicorn boxsolver.entrypoints.api:app --reload --port 8000
```

Then open <http://localhost:8000/docs> for interactive Swagger. Hit
`POST /v1/pack` with the contents of `data/boxes.json` and `data/items.json`
as `Boxes` and `Items`.

---

## Where to start reading

| If you want to... | Open |
|---|---|
| Understand the algorithm and the decisions | `README.md` |
| Change how items are placed | `boxsolver/core/container.py` → `Container.find_placement` |
| Add a new constraint | `boxsolver/core/container.py` → `Container._is_valid` |
| Change which carton gets chosen | `boxsolver/core/packer.py` → `Packer._score` |
| Change how payloads are validated | `boxsolver/models.py` |
| See the JSON contract | `schemas/boxsolver_api.yaml` |

## Good first contributions

1. **Benchmark against real order data.** The bundled datasets are synthetic,
   and utilisation numbers are most meaningful against real weight-to-volume
   ratios.
2. **Add load-bearing constraints** — don't stack heavy items on fragile ones.
   Everything needed is in `Container._is_valid`, and `Item.Fragile` is already
   parsed.
3. **Centre-of-gravity balancing** for cartons that get handled manually.
4. **Non-cuboid items** — cylinders and irregular shapes, via a bounding-volume
   approximation in `geometry.py`.

A GitHub Actions workflow at `.github/workflows/ci.yml` runs the tests, checks
the datasets and enforces the runtime budget on every push.
