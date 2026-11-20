# Writing an adapter

An adapter teaches plotparity about one ggplot2 extension: what counts as a **component** (for ggtext, a rich-text box), how to find components in each engine's output, how to pair them up, and which checks apply. Everything else (rendering, references, reports, the CLI) is shared.

The ggtext adapter in `src/plotparity/adapters/ggtext/` is the reference implementation.

## Files

`src/plotparity/adapters/<name>/`:

| File | Contents |
| --- | --- |
| `adapter.py` | Python side: extraction, matching, extension-specific checks |
| `observe.R` | R side: what to record from the forced grob tree |
| `observe_py.py` | optional Python side: extra observations from the drawn figure and the plotnine plot (e.g. built layer data) |
| `profile.toml` | every check this adapter runs, with severity, tier, tolerance and how the tolerance was calibrated |

A cases folder selects it with `adapter = "<name>"` in its `cases.toml`; plotparity imports `plotparity.adapters.<name>.adapter`.

## adapter.py

| Name | Contract |
| --- | --- |
| `NAME`, `VERSION` | `VERSION` is part of every reference's input hash: bump it only when stored references must be re-rendered |
| `OBSERVE_R`, `PROFILE` | paths to the two files above |
| `EXPECT_KEYS` | extra keys a case may put in `expect` (ggtext: `frame`) |
| `components(engine, observations, expect, canvas_pt)` | returns `(components, assigned)`. `engine` is `"r"` or `"python"`. `assigned` is the set of Python draw-event paint indices the adapter put into components; every event drawn by the package's own artists that isn't assigned fails the case (`unexpected_drawing`) |
| `component_id(i, comp)` | stable id from the reference side, used in findings and fingerprints |
| `describe_component(comp)` | what to print for a missing or unexpected component |
| `match(ref, cand)` | returns `(pairs, missing, unexpected)`. Must not use the properties under test, and must refuse pairs above a maximum cost |
| `in_scope(comp, reference)` | optional: drop candidate components the reference has no counterpart for (ggtext: plain titles are plotnine's own) |
| `CASE_CHECKS` | optional whole-plot checks: `check(ref_comps, cand_comps, pairs, profile_entry, add, expect)` (e.g. ggridges' paint order) |
| `CHECKS` | extension-specific checks: `check(ref, cand, profile_entry, add, expect)`; return `False` when it doesn't apply to the pair |

Components use the core keys wherever they apply, so the core checks (colour, size, rotation, placement, visibility…) work unchanged:

- `text_runs`: list of `{text, x_norm, y_norm, color_hex, alpha, fontsize_pt, fontweight, fontstyle, …}`;
- `rect`: frame geometry and colours;
- `angle_deg`, `centroid_norm_tl`, `bbox_norm_tl`.

The core runs a check only when its keys are present.

Coordinates are normalised to the canvas with a top-left origin; physical sizes are in points (1/72 in).

## observe.R

- `adapter_setup(family)`: set geom defaults the theme's `base_family` doesn't reach (so both engines use the same font).
- `adapter_json_digits` (optional): decimals kept when the observations are saved (default 4; `NA` = full precision).
- `adapter_observe(p, w_in, h_in, dpi)`: return a named list of observation streams for one plot. Walk the forced grob tree (`grid.force()`); push each grob's viewport before `deviceLoc()`; report sizes in `"bigpts"` (1/72 in, matplotlib's point); resolve colours with `col2rgb` (R colour names don't exist in matplotlib).

## profile.toml

```toml
[checks.text_color]
severity = "gating"     # gating | advisory | off
tier = "style"          # inventory | layout | style: orders the feedback
tol = 6.0
calibration = "CIEDE2000; max on clean pairs 0.0"
```

Calibrate tolerances on clean pairs only (R vs a package known to be correct) and write down how. Never loosen a tolerance to make a case pass.

## Testing an adapter

1. Write cases for the extension and render their references.
2. Run them against a known-good package; every case should pass (`plotparity validate`).
3. Write mutations: patches that break one thing each in the known-good package (`mutations/*.patch` + `suite.toml`). Each must visibly change the render and fail the check you expect (`plotparity selftest`).

## observe_py.py & shared `panel_geometry`

`observe(fig, plot) -> dict` runs in the package's interpreter right after the recorded draw; its keys are merged into the Python observations. Only plotnine, matplotlib, pandas and numpy are available there. The core probe already records every draw call (including decoded `draw_path_collection` offsets/styles up to 500 items for draw-time custom artists); use this hook for what draw calls don't carry (built layer data, panel ranges/labels, or static panel shapes in data units).

For geom extensions that work on panel points, polygons, lines, segments, or rasters across single or facetted panels, opt in to the shared multi-panel extractors instead of writing your own grob/artist walkers:

- **R (`engines/r/panel_geometry.R`, auto-sourced by `render.R`):** call `extract_panel_geometry(p, w_in, h_in, dpi, kinds = c("points", "shapes", "rasters"))`. Handles single/facetted panel viewport ordering, `coord_flip()`, R `pch` `0.75 * fontsize` char sizing, vectorized `segments` `gpar`, `lwd * 72/96`, and degenerate `roundrectGrob(r = 0)` vertices.
- **Python (`plotparity.engines.python.panel_geometry`):** call `extract_panel_geometry(fig, plot, kinds=("points", "shapes", "rasters"))`. Handles multi-panel `fig.axes`, `scale_discrete` (including `scale_x_ordinal`), `coord_flip()`, `PathCollection` diameter conversion, and returns `extracted_classes` for `account_for_drawing()`.

## Adapters

| | ggtext | ggridges | ggbeeswarm | ggrepel |
| --- | --- | --- | --- | --- |
| component | rich-text box or theme-element label | a ridge | a `(panel, group)` point swarm | a repelled text/label annotation |
| R side | gridtext grobs; gtable chrome | panel polygons/lines; `ggplot_build` density | `extract_panel_geometry(..., "points")` | forced `textrepeltree`/`labelrepeltree` + panel points |
| Python side | probe draw calls + theme roles | `observe_py`: panel artists + built data | `extract_panel_geometry(..., ("points",))` | probe draw calls (labels/boxes/segments) + `extract_panel_geometry` points |
| matching | position + words; chrome by role & order | by level | by `(panel, level)` | by `(panel, exact text)` |
| whole-plot checks | | `paint_order` | | `label_no_overlap`, `label_no_point_overlap`, `segment_count` |
