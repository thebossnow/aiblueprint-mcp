---
name: kitchen-floor-plan-from-photos
description: Convert site photos and hand-drawn kitchen sketches into clean dimensioned PDF and SVG floor plans for remodel visualization and CAD/IFC import. Use when the user provides kitchen photos plus rough sketches or measurements and wants a professional existing-condition plan, blueprint, or CAD-ready drawing.
---

# Kitchen Floor Plan From Photos

Turn unstructured site photos + rough hand sketches into clean, dimensioned, CAD-ready floor plans (PDF + SVG).

## When to Use

- User uploads kitchen (or similar room) photos and one or more hand sketches with dimensions
- User asks for a clean blueprint, floor plan, existing-condition plan, or CAD/IFC-ready drawing for a remodel presentation
- User wants a repeatable process for future jobs

## Required Inputs (see also references/capture-checklist.md)

Minimum viable package:
- 3–4 overall and wall photos
- One clear sketch with overall dimensions, island size + orientation, and appliance locations
- Property address

Ideal package (use the checklist in `references/capture-checklist.md`):
- Full set of overall, wall, island, and opening photos
- Measured overall room sizes, island length/depth, key counter runs, window and door sizes
- Notes on keep/replace/move decisions

## Workflow (follow these steps in order)

1. **Inventory the inputs**
   - List every photo and sketch.
   - Extract all written dimensions, the address, and any notes (counter SF, linear feet, “REPLACE”, “BUILT IN”, etc.).

2. **Establish orientation and major elements**
   - Decide which wall is North for the drawing (convention: window/sink wall at top of page when possible).
   - Identify: sliding door or large opening, window + sink, range, dishwasher, fridge, island, any peninsula or return.
   - Confirm island long-axis orientation from photos and the user’s sketch notes. If the user later corrects it, rotate 90°.

3. **Synthesize room geometry**
   - Overall width and depth from the sketches (prefer the more careful “home” sketch when available).
   - Place base-cabinet runs so they sit **inside** the wall lines (never outside).
   - Place appliances on the correct runs (range usually belongs on the same wall as the sink when photos show that).
   - Island coordinates and orientation (NS = long edges vertical on the page).

4. **Generate the drawings**
   - Prefer the reusable script in `scripts/generate_floor_plan.py`.
   - Edit the `GEOMETRY` dictionary at the top of the script with the synthesized numbers and labels.
   - Run the script. It produces both a landscape PDF (title block, dimensions, legend, notes) and a clean SVG suitable for CAD import.
   - If the script needs non-trivial changes, adapt the ReportLab + SVG generation code directly.

5. **Verify and correct**
   - Render the PDF to a PNG preview (`pdftoppm`) and inspect.
   - Confirm: island orientation matches the latest user request, all cabinets and appliances are inside the room boundary, range/sink/DW locations match the photos, dimensions are readable.
   - If the user reports an orientation or placement error, fix the geometry and regenerate. Do not argue with clear photo evidence.

6. **Deliver**
   - Provide both the PDF and the SVG.
   - Note that the SVG is the preferred file for direct import into SketchUp, AutoCAD, Revit, Chief Architect, etc.
   - Offer to produce a side-by-side existing vs proposed version if the user is ready for the remodel design.

## Common Pitfalls (avoid these)

- Drawing cabinets or appliances outside the wall lines (especially easy in SVG if y-transforms are inverted).
- Placing the range on the wrong wall when photos clearly show it next to the sink.
- Leaving the island in the wrong orientation after the user has specified long edges vertical or horizontal.
- Using only one sketch when a second, cleaner sketch exists — always cross-check both.
- Forgetting the address, north arrow, scale note, or the “not a construction document” disclaimer.

## Script Usage

```bash
# Edit GEOMETRY in the script first, then:
python /home/workdir/.grok/skills/kitchen-floor-plan-from-photos/scripts/generate_floor_plan.py
```

The script writes to `/home/workdir/artifacts/` by default. Adjust paths or the GEOMETRY block as needed for each new job.

## Extending the Skill

- For proposed remodel layouts, duplicate the geometry block, change colors/labels, and generate a second sheet or a side-by-side view.
- For other rooms (bath, laundry, ADU), the same capture → synthesize → generate pattern applies; only the appliance set and typical dimensions change.
