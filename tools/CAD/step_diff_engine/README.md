# STEP Diff Engine

A Python CAD geometry diff utility that compares two STEP commits and tracks surface evolution over time.

## Features

- Parse `.stp` / `.step` files with `pythonOCC`
- Extract per-face geometry:
  - surface type
  - area
  - center of mass
  - normal vector
  - radius / axis when applicable
- Generate stable fingerprints for each face
- Compare two commits and report:
  - added surfaces
  - removed surfaces
  - modified surfaces
  - volume delta
  - bounding box deltas
- Persist diff records in JSON database
- Query fingerprint evolution history via:
  - `get_history(fingerprint)`

## Project Layout

```text
step_diff_engine/
  __init__.py
  __main__.py
  cli.py
  database.py
  diff_engine.py
  geometry_fingerprint.py
  step_parser.py
  utils.py
  tests/
```

## Installation

```powershell
cd "S:\MKworld\creo vcs\creo_vcs_v4\tools\CAD\step_diff_engine"
python -m pip install -r requirements.txt
```

## CLI Usage

### Compare two STEP commits

```powershell
python -m tools.CAD.step_diff_engine compare `
  --step-a "C:\data\part_v1.step" `
  --step-b "C:\data\part_v2.step" `
  --commit-a "abc123" `
  --commit-b "def456" `
  --metadata "{\"author\":\"aziz\",\"ticket\":\"ECO-42\"}" `
  --output "C:\data\diff.json"
```

### Query fingerprint history

```powershell
python -m tools.CAD.step_diff_engine history <fingerprint_sha256>
```

## GUI Visualizer

Load two STEP files and visualize highlighted differences directly on the 3D models.

```powershell
cd "S:\MKworld\creo vcs\creo_vcs_v4"
& "C:\Users\mkazi\miniconda3\condabin\conda.bat" run -n pyoccenv python -m tools.CAD.step_diff_engine.step_diff_gui
```

Legend in the 3D view:

- Red: removed faces (model A)
- Green: added faces (model B)
- Orange: modified faces
- Gray (transparent): unchanged faces

## Python API

```python
from tools.CAD.step_diff_engine.step_parser import parse_step_file
from tools.CAD.step_diff_engine.diff_engine import compare_models
from tools.CAD.step_diff_engine.database import JsonDiffDatabase, get_history

m1 = parse_step_file("part_v1.step", commit_id="abc123")
m2 = parse_step_file("part_v2.step", commit_id="def456")
diff = compare_models(m1, m2)

db = JsonDiffDatabase()
db.append_comparison(model_a=m1, model_b=m2, diff=diff, metadata={"ticket": "ECO-42"})

history = get_history("<fingerprint>")
```

## Testing

The tests are intentionally pure-Python and do not require `pythonOCC` runtime for core diff/fingerprint logic validation.

```powershell
cd "S:\MKworld\creo vcs\creo_vcs_v4"
python -m pytest tools/CAD/step_diff_engine/tests -q
```
