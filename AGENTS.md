# AGENTS.md: plotparity working notes

Read this first if you're an agent or a human picking up work here. **Keep it current:** update Status, Open issues and the Log whenever you change behaviour, add cases or projects, or run a loop.

- Design: `DESIGN.md`.
- Usage: `README.md`.
- Writing cases: `docs/writing-cases.md`.
- Agent loop: `skills/plotparity/SKILL.md`.

**Related**

| Repo | Contents |
| --- | --- |
| [plotparity-examples](https://github.com/snake-case-ggtext/plotparity-examples) | the ggtext case library, plus projects for two ports |
| `plotparity-research/` (local folder in the maintainers' workspace, not published) | frozen research record: REPORT.md, the explorer, the ggridges pilot, the theme-element spike |

## Status (2026-11-19)

| Piece | State |
| --- | --- |
| `run` / `references` / `validate` / `selftest` / `loop` / `new-case` | working |
| Distribution | `uvx --from git+https://github.com/snake-case-ggtext/plotparity plotparity …`. Ports can be git URLs pinned to a commit; port dependencies come from `[candidate] requirements` (a cached uv env) |
| Tests (`uv run pytest -q`) | 16 passed. Uses `tests/fixtures/` (5 ggtext cases + R refs) and the port from `PLOTPARITY_TEST_PORT` (default: pinned plotnine-ggtext commit) |
| examples: `projects/ggtext` (plotnine-ggtext) | dev 6 / holdout 4 all pass; coverage 6 = backlog; `validate` and `selftest` pass (9/9 mutations caught, negative control passes, batch == `--isolate`) |
| examples: `projects/ggtext-shanellis` (Shannon Ellis's `ggtext`) | 2/16 pass, 10 fail, 4 unmeasured. Real gaps: sup/sub scale 0.7× vs R 0.8×; top padding +2.4–3.6 pt; outline colour follows `text_color` not `color`; R colour names (`red3`, `grey40`) silently black; `~sub~`/`^sup^` unsupported |
| Migration from the research prototype | done: golden test 158/160 identical, 2 explained (the old allowlist was over-broad), 0 unexplained |

## Commands

```bash
# from plotparity-examples (or any repo with cases/ + projects/):
PP="uvx --from git+https://github.com/snake-case-ggtext/plotparity plotparity"   # local checkout: --from ../plotparity
$PP run projects/ggtext [--select dev|holdout|coverage|all|<id>|<glob>] [--source <path|git+URL@rev>]
$PP references cases/ggtext [--select …] [--force]     # needs R (+ ggplot2, ggtext); set R_LIBS_USER if needed
$PP validate projects/ggtext
$PP selftest projects/ggtext
$PP loop init|iterate|holdout|finish projects/ggtext --name N …
$PP new-case cases/ggtext <folder>/<id> [--package plotnine_ggtext]
# in this repo:
uv run pytest -q
```

## Rules

- **Fail closed.** Unmeasurable is never a pass. These are all fail or unmeasured, never skipped:
  - unassigned candidate drawing
  - an empty candidate
  - a property the reference has but the candidate lacks
  - unalignable text runs
  - a missing translation
- **After changing a check, the profile or an adapter:** run pytest here, and `selftest` on every example project that has a `known_good`. Calibrate tolerances on clean pairs only, and record the calibration in `profile.toml`.
- **After adding or editing cases:** run `references`, then `validate`. Dev and holdout cases must pass on the project's `known_good` port.
- **`known_differences.toml` is human-owned.** Agents propose entries; a human adds or approves them.
- **Local `uvx --from <path>` users get fresh builds.** `pyproject.toml` sets `[tool.uv] cache-keys` to watch `src/**`; uv's default watches only `pyproject.toml`.

## Adding an adapter (new extension)

Create `src/plotparity/adapters/<name>/` with:

- `adapter.py`:
  - `NAME`, `VERSION`, `OBSERVE_R`, `PROFILE`, `EXPECT_KEYS`
  - `components(engine, observations, expect)`, which returns `(components, unassigned)`
  - `component_id`
  - `match`, which must enforce a max cost
  - `CHECKS`
- `observe.R`:
  - `adapter_setup(family)`
  - `adapter_observe(p, w_in, h_in, dpi)`, which returns a named list of streams
- `profile.toml`, which configures every core and adapter check

Components reuse the core keys (`text_runs`, `rect`, `angle_deg`, `centroid_norm_tl`, `words`, `full_text`) wherever they apply.

The template for a geometry-and-stat adapter is the ggridges pilot (research record: `experiment/ggridges_pilot/README.md`). It needs:

- a `build` observation stream: a worker hook that captures plotnine's built layer data;
- comparison in data coordinates;
- a case-level `paint_order` check.

## Open issues

- **Theme elements (titles, axes, strips, legends) aren't extracted yet.** Those cases are `measurable: false`. A spike (research record: `spikes/chrome_extraction/`) showed it's feasible on both engines and has a concrete design:
  - **Roles, R side:** come from the forced gtable names.
  - **Roles, Python side:** tag events by wrapping the artists plotnine applies each themeable to (`theme.targets`, guides), with a `gid="ggrole:<role>"` hook for ports.
  - **Position:** compare relative to the panel edge, not on the canvas.
  - **New checks:** `chrome_position`, `chrome_padding` and `chrome_line_count`.

  Its tolerances come from 23 pairs on one port; recalibrate them before freezing.
- **`observe.R` reports sizes in grid `"pt"` (1/72.27 in), but matplotlib uses 1/72 in.** R widths, heights and insets are 0.375% too large (found by the spike). It's harmless at current tolerances. Fix it by converting with `"bigpts"`, then re-render the references.
- **`<img>` isn't checked.** Python records `draw_image`; the R probe doesn't yet extract raster grobs.
- **`CANVAS_PT` (640×480) is hard-coded in the ggtext adapter** (padding insets, word-split positions). A per-case canvas override would distort them. Fix by passing the canvas into `components()`.
- **Facets and multiple panels aren't supported:** segmentation uses the first panel.

## Log

- **2026-11-19:** Built per DESIGN.md and migrated from the research prototype (golden test 158/160 identical).
  - The case library is shared across ports.
  - Python blocks are tagged by port package.
  - Splits live in projects.
  - Segmentation is geometry-first.
- **2026-11-19:** Stress test with a second ggtext port. The harness fix it forced: multi-word `draw_text` runs are now split into words, because gridtext draws per word while other ports draw per styled run.
- **2026-11-19:** Split into two repos, plotparity (tool) and plotparity-examples. The research record became a local, unpublished folder.
  - Added `[candidate] requirements` (a uv-built, cached env with plotnine and matplotlib pinned to the tool's versions).
  - `known_good` now resolves like `source`, so git URLs work.
  - `PLOTPARITY_CACHE` controls both caches.
  - The tests got their own fixture.
  - The `[ggtext]` extra was removed: ports declare their own dependencies.
