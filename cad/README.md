# cadmeasure: excavation CAD to measurements, without coordinates

Step 1 of the plan. It runs on the excavation team's own computer, reads the AutoCAD drawings and writes a table of lengths: wall thicknesses, room widths, stair and balustrade widths, heights and element sizes. Each one is tagged with its drawing, layer and AutoCAD handle, so any number can be found again in the original drawing.

**What leaves the computer:** `measurements.csv`, `summary.json` and `freeze.json`. They hold lengths, angles, counts and names only. Positions are used in memory to line edges up and are never written. `--alias-key` also replaces drawing file names with D01, D02 … and keeps the key in a separate file that stays with the team.

## Setup

Needs Python 3.9 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -r cad/requirements.txt
.venv/bin/python cad/cadmeasure.py selftest      # proves the reader on made-up drawings (about 10 s)
```

DWG files need the free [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter). With it installed, the tool reads `.dwg` directly. Without it, use the converter's window to batch-convert the folder to DXF (2010 or later) and point the tool at the DXFs.

## Steps

```bash
# 1. List every layer with a suggested meaning (Spanish and English layer names are recognised)
.venv/bin/python cad/cadmeasure.py layers drawings/*.dwg -o layer_map.csv

# 2. Open layer_map.csv and fill in element, condition, phase and include for each layer
#    include: yes / no. Rerunning step 1 keeps what you filled in.
#    The colour column shows each layer's colour as drawn (and any entities drawn in another colour),
#    so a team's colour conventions are in front of whoever confirms the map.

# 3. Measure
.venv/bin/python cad/cadmeasure.py extract drawings/*.dwg --layers layer_map.csv -o out/ --alias-key private/drawing_key.csv

# 4. Before any statistics: freeze (hash) the table for the pre-registration
.venv/bin/python cad/cadmeasure.py freeze out/measurements.csv --layers layer_map.csv
```

`freeze` refuses while any included layer is unconfirmed: the archaeologist decides what every layer is, not the script.

**Layers that must stay out of the test.** Each would plant a false unit or a modern line. The suggestions already exclude the obvious ones, in Spanish and English:
- hypothetical, idealised or reconstructed lines, abbreviations such as "Reconstr." included (a reconstruction can be well founded and still not be the builders' own line);
- modern features;
- excavation units, test pits and excavation limits (2 × 2 m squares are a modern unit);
- looting pits;
- survey grids;
- contours;
- dimensions and text.

Restored or consolidated walls, and anything named with cement or concrete, are tagged `restored` so the analysis can drop them. Layers marked original or excavated are tagged `excavated`. Layers the rules can't place (a plain "Stone" layer, say) come out as `check`: the archaeologist decides.

## Whole-site drawings

A drawing of a whole zone holds many buildings on the same layers. Left whole, the tool would measure the space between two separate buildings as if it were a design dimension. So `extract` splits each drawing into **structures** first:
- drawn pieces within about 2 m of each other in plan belong together (`--join 2`, the default);
- anything wholly inside another structure's footprint joins it, so the tiers of a stepped platform stay one building;
- lines are only paired, merged and counted within one structure, and each record says which (`S001`, `S002` …, numbered by size, not by position).

`summary.json` lists how many structures each drawing has and the size of the largest ones (sizes only, never positions). Check them: if the largest structure is the size of the whole site, an included layer is joining everything (a boundary, a road, a terrain mesh). Exclude that layer or raise/lower `--join`. `--join 0` keeps each drawing whole. Cutting each structure into its own file in AutoCAD (`WBLOCK`) also works.

## What it reads

| AutoCAD entity | Handling |
|---|---|
| LINE, LWPOLYLINE, 2D/3D POLYLINE | straight segments; arcs (bulges) left out; AutoCAD thickness becomes walls with height |
| 3DFACE, MESH, polyface and polygon meshes, SOLID | face edges; edges between coplanar faces (triangulation, tiling) dropped |
| INSERT / MINSERT (blocks) | exploded in place, nested blocks too; each MINSERT copy is its own element |
| 3DSOLID, BODY, REGION, SURFACE | **cannot be read** (ACIS). Counted per layer in `summary.json`. In AutoCAD, convert them to meshes with `MESHSMOOTH` (smoothness 0), save, rerun |
| ARC, CIRCLE, SPLINE, TEXT, DIMENSION, HATCH | skipped and counted |

External references are not followed: `layers` and `extract` print a warning naming any they find, and `summary.json` counts them. Bind them first (`XREF` → Bind) or measure the referenced drawings too. Only model space is read.

For each drawing, the tool:
1. finds the dominant grid orientation, reported as degrees east of north;
2. converts units from `$INSUNITS`, or from `--units` if that's missing;
3. merges collinear touching edges, so a wall drawn in pieces is measured whole.

Each measurement is one of these kinds:
- `edge`: the length of a merged run of edges along u, v or z (vertical), or along a slope (`su`, `sv`, with inclination).
- `gap`: the distance between neighbouring parallel lines in plan, such as wall thickness, room width or tread depth.
- `extent`: the overall size of one drawn element.

Parallel copies of the same length (the four long edges of a wall, the edges of every step of one stair) count once, with `copies`.

## What the self-test shows

The test uses made-up drawings in metres and centimetres, rotated onto the city grid and placed at real UTM coordinates. They use every entity type above, plus the traps.
- **Lengths and grid:** every designed length comes back within 2 cm (in practice, within a few mm). The grid angle and units are recovered.
- **Units 83 cm and 80.5 cm**, which are 3% apart: each is recovered, and told apart, in 20 of 20 drawing sets.
- **No coordinates in the output:** the tests check this, and that excluded layers never reach the table.

**Statistics warning:** the standard quantogram noise bar (Kendall's ±15% jitter) assumes every length is independent. Lengths read off drawings are not: a few design choices (one wall thickness, one room depth, a mirrored plan) drive many measured lengths.
- **Counting every record:** on made-up drawings with **no unit**, it finds a "unit" every time.
- **Distinct gaps and heights only** (the `TEST_SET` in `cadmeasure.py`): it still crosses the 1% bar in about one set in eight (11 of 100 for seeds 1–100).
- **Independent random lengths:** the same bar is right (1 in 100).

So the unit test needs a null that keeps the drawings' structure before any real result is called significant. `tests/test_cadmeasure.py::test_noise_bar_is_right_for_lengths_from_drawings` is the target. It is marked as an expected failure until a method passes it.

## Tests

```bash
.venv/bin/python -m pytest cad/tests -q
```
