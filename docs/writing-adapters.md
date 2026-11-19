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

## observe_py.py

`observe(fig, plot) -> dict` runs in the package's interpreter right after the recorded draw; its keys are merged into the Python observations. Only plotnine, matplotlib, pandas and numpy are available there. The core probe already records every draw call; use this hook for what the draw calls don't carry (ggridges: plotnine's built layer data, and the panel's shapes in data units).

## The two adapters

| | ggtext | ggridges |
| --- | --- | --- |
| component | a rich-text box or theme-element label | a ridge |
| R side | gridtext grobs; the gtable for theme elements | panel polygons/lines in paint order; `ggplot_build` density |
| Python side | the probe's draw calls, roles tagged on themed artists | `observe_py`: panel artists in draw order; built layer data |
| matching | position + shared words; theme elements by role and order | by level |
| whole-plot checks | | paint order |
