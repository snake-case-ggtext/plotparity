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

- **Commands:** `run`, `view` (`--public` for a publishable copy), `init`, `new-case`, `references`, `validate`, `selftest` and `loop` all work. A bare `plotparity run` inside a package repo with `./parity/` needs no config.
- **Two adapters:**
  - **ggtext:** `geom_richtext`, `geom_textbox`, and the theme elements `element_markdown` / `element_textbox`;
  - **ggridges:** `geom_density_ridges` (incl. binline, quantile lines), `geom_ridgeline`. No known-good package, so it isn't in `selftest`; `tests/test_ggridges.py` covers its checks on synthetic observations.
- **Tests:** `uv run pytest -q`. They use `tests/fixtures/`: a few ggtext cases with their R references, copied from plotparity-examples. The package under test comes from `PLOTPARITY_TEST_PORT` (default: plotnine-ggtext at a pinned commit; set it to a local checkout to run offline).
- **plotparity-examples, `projects/ggtext`** (plotnine-ggtext):
  - dev and holdout all match R;
  - the coverage split is its backlog (R features it lacks);
  - `validate` passes;
  - `selftest`: every mutation is caught, each with a visible pixel change; the negative control passes; batch and `--isolate` agree.
- **plotparity-examples, `projects/ggtext-shanellis`** (a second ggtext package) and **`projects/ridgenine`** (ggridges): packages with real gaps.

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
  - then refresh the copies in `tests/fixtures/cases/ggtext/_references/`;
  - PNGs should stay byte-identical unless rendering really changed.
- **The adapter's `VERSION` is part of every reference hash.** Bump it only when stored references must be re-rendered.
- **`known_differences.toml` is human-owned.** Agents propose entries; a human adds or approves them.
- **Running from a local checkout:** `uvx --from ./plotparity` picks up source edits because `pyproject.toml` sets `[tool.uv] cache-keys` to cover `src/**`.

## Adapter contract (summary; details in docs/writing-adapters.md)

- **`components(engine, observations, expect, canvas_pt)`** returns `(components, assigned)`, where `assigned` is the set of Python paint indices put into components.
- **`in_scope(comp, reference)`** (optional) drops candidate components the reference has no counterpart for.
- **`CASE_CHECKS`** (optional): whole-plot checks, `fn(ref_comps, cand_comps, pairs, P, add, expect)`.
- **`observe_py.py`** (optional): `observe(fig, plot) -> dict`, merged into the Python observations; runs in the package's interpreter, so plotnine/pandas/numpy only.
- **`describe_component(comp)`** is what the core prints for a missing or unexpected component.
- **Checks** return `False` when they don't apply to a pair.
- **Core keys:** `text_runs`, `rect`, `angle_deg`, `centroid_norm_tl`, `bbox_norm_tl`. The core runs a check only when its keys are present.

## Open issues

- **Theme elements ("chrome")** are deliberately checked coarsely.
  - **How:** R roles come from the forced gtable's cell names (`extract_chrome` in observe.R). Python draw events are tagged with a role by wrapping the artists plotnine applies each theme element to (`role_artists` in the probe; plotnine internals pinned by `tests/test_roles.py`).
  - **Scope and pairing:** only roles that are rich text in R are compared (`in_scope`), paired by order along the canvas.
  - **Checks:** the usual text, style and frame checks; horizontal padding and line count only when both boxes are the same width; `chrome_position` is coarse (a fraction of the panel, see the profile).
  - **Not done:** fine placement. There is no known-good package for theme elements, so they aren't mutation-tested; the tolerances are the geom ones.
- **`<img>` content isn't compared.** Python records `draw_image`, but the R probe doesn't extract raster grobs.
- **Facets and multiple panels:** segmentation reads the first panel only.
- **Ownership limits:**
  - Plain matplotlib artists a package adds straight to the axes count as "other layers": their drawing is compared if it forms components, but extra drawing by them isn't flagged.
  - Drawing that bypasses the renderer (agg filters, writing to the canvas buffer) isn't recorded.
- **`ink_present` can be fooled by other ink under a run.** In `richtext_example_markup`, a `geom_point` dot sits under the word "text". `text_alpha` and `text_visible` are the primary visibility checks.
- **`_cluster_lines` in the ggtext adapter still has a 640×480 fallback.** It's unused today, because both engines always provide local coordinates.
- **Managed environments are keyed by their requirement list,** which includes the package path. Editing a local package's *dependencies* needs a fresh environment: delete `~/.cache/plotparity/envs/<key>`. Code changes are picked up anyway, because the source comes first on `PYTHONPATH`.
- **ggridges:** gradient fills (`geom_density_ridges_gradient`), jittered points and facets aren't covered; only the first layer's built data is read.
