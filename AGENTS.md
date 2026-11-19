# AGENTS.md

Notes for anyone (agent or human) changing plotparity itself. To *use* plotparity on a package, see `README.md`; agents doing that should follow `skills/plotparity/SKILL.md`.

| Doc | Covers |
| --- | --- |
| `README.md` | install, run, reading results |
| `DESIGN.md` | how it works: layout, configuration, engines, ownership and visibility rules, statuses, references, reports |
| `docs/writing-cases.md` | case format and authoring checklist |
| `docs/writing-adapters.md` | supporting another extension |
| `skills/plotparity/SKILL.md` | instructions for an agent working on a package |

Worked example and the ggtext benchmark: [plotparity-examples](https://github.com/snake-case-ggtext/plotparity-examples).

## State

- **Commands:** `run`, `view`, `init`, `new-case`, `references`, `validate`, `selftest` and `loop` all work. A bare `plotparity run` inside a package repo with `./parity/` needs no config.
- **One adapter: ggtext** (`geom_richtext`, `geom_textbox`).
- **Tests:** `uv run pytest -q`, about 60 tests in roughly 30 s. They use `tests/fixtures/`, which is 5 ggtext cases with their R references, copied from plotparity-examples. The package under test comes from `PLOTPARITY_TEST_PORT` (default: plotnine-ggtext at a pinned commit; set it to a local checkout to run offline).
- **plotparity-examples, `projects/ggtext`** (plotnine-ggtext):
  - dev and holdout all match R;
  - the coverage split is the backlog (theme elements, `<img>`, R colour names, `~sub~`/`^sup^`);
  - `validate` passes;
  - `selftest`: all 16 mutations are caught, each with a visible pixel change; the negative control passes; batch and `--isolate` agree.
- **plotparity-examples, `projects/ggtext-shanellis`** (a second ggtext package): 2 of 16 match, 10 differ, 4 are unmeasured.

## Rules for changing plotparity

- **Fail closed.** Anything that can't be measured is `unmeasured` or an error, never a pass. Every candidate draw event is either inside a component, or drawn by a plotnine/matplotlib artist (another layer), or `unexpected_drawing`.
- **After changing a check, the profile, an adapter or the probe:**
  - run pytest;
  - run `plotparity selftest` in `plotparity-examples/projects/ggtext`;
  - keep dev and holdout on that project all-matching.
- **Tolerances:**
  - calibrate on clean pairs only, and write the calibration into `profile.toml`;
  - never loosen a tolerance to make a case pass.
- **If you change `observe.R` or the reference inputs:**
  - re-render the references in plotparity-examples (`plotparity references cases/ggtext --force`, with R and `R_LIBS_USER`);
  - then refresh the five copies in `tests/fixtures/cases/ggtext/_references/`;
  - PNGs should stay byte-identical unless rendering really changed.
- **The adapter's `VERSION` is part of every reference hash.** Bump it only when stored references must be re-rendered.
- **`known_differences.toml` is human-owned.** Agents propose entries; a human adds or approves them.
- **Running from a local checkout:** `uvx --from ./plotparity` picks up source edits because `pyproject.toml` sets `[tool.uv] cache-keys` to cover `src/**`.

## Adapter contract (summary; details in docs/writing-adapters.md)

- **`components(engine, observations, expect, canvas_pt)`** returns `(components, assigned)`, where `assigned` is the set of Python paint indices put into components.
- **`describe_component(comp)`** is what the core prints for a missing or unexpected component.
- **Checks** return `False` when they don't apply to a pair.
- **Core keys:** `text_runs`, `rect`, `angle_deg`, `centroid_norm_tl`, `bbox_norm_tl`. The core runs a check only when its keys are present.

## Open issues

- **Theme elements (titles, axis labels, strips, legends) aren't extracted yet.** Those cases report `unmeasured`.
  - A spike showed it's feasible on both engines, with this design:
    - **R side:** roles come from the forced gtable names.
    - **Python side:** events are tagged by wrapping the artists plotnine applies each themeable to (`theme.targets`, guides), with a `gid="ggrole:<role>"` hook for packages.
    - **Positions:** compared relative to the panel edge, not the canvas.
    - **New checks:** `chrome_position`, `chrome_padding`, `chrome_line_count`.
  - Its tolerances come from 23 pairs on one package; recalibrate them before freezing.
  - The spike's code is in the maintainers' research folder (not published).
- **`<img>` content isn't compared.** Python records `draw_image`, but the R probe doesn't extract raster grobs.
- **Facets and multiple panels:** segmentation reads the first panel only.
- **Ownership limits:**
  - Plain matplotlib artists a package adds straight to the axes count as "other layers": their drawing is compared if it forms components, but extra drawing by them isn't flagged.
  - Drawing that bypasses the renderer (agg filters, writing to the canvas buffer) isn't recorded.
- **`ink_present` can be fooled by other ink under a run.** In `richtext_example_markup`, a `geom_point` dot sits under the word "text". `text_alpha` and `text_visible` are the primary visibility checks.
- **`_cluster_lines` in the ggtext adapter still has a 640×480 fallback.** It's unused today, because both engines always provide local coordinates.
- **Managed environments are keyed by their requirement list,** which includes the package path. Editing a local package's *dependencies* needs a fresh environment: delete `~/.cache/plotparity/envs/<key>`. Code changes are picked up anyway, because the source comes first on `PYTHONPATH`.
- **A ggridges adapter** (polygons, KDE statistics, paint order) has a working pilot in the research folder, and `docs/writing-adapters.md` lists what it needs.
