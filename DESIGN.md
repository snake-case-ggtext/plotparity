# plotparity: design

**What it does.** It renders the same plot two ways (a *reference* engine and a *candidate* engine) and records
the draw calls on both sides. It turns them into comparable **components** and reports, per case, whether they
agree. When they don't, it says what differs, where, and by how much. The audience is a coding agent iterating on a package, and the humans who maintain the benchmark.

- Main use: an R ggplot2 extension vs its Python/plotnine package.
- The engines are symmetric plug-ins, so other pairings are possible later. Only R→Python is supported and tested so far.
- The research behind the design found that structured draw-call comparison works; pixel or VLM gating does not.

**Principles**
1. **Fail closed.** Something that can't be measured is never a pass.
2. **Extension-specific logic lives only in adapters.**
3. **Judgement config lives in hashed files, never in CLI flags.**
4. **Build only what a current case or user need requires.** See §10 for what was cut, and why.

## 1. Layout

```
plotparity/                        # the tool
  src/plotparity/
    cli.py  project.py (Library = a cases folder, Project = package + cases)  cases.py  ports.py  runner.py
    compare.py  references.py  report.py  loop.py  selftest.py  validate.py  scaffold.py
    engines/python/  worker.py (runs in the package's interpreter), engine.py (managed envs), probe.py
    engines/r/       render.R (fonts, PNG, adapter observe hooks)
    adapters/ggtext/ adapter.py, observe.R, profile.toml
    view/            build.py + template.html: the HTML report
  tests/  skills/plotparity/SKILL.md  docs/

<cases folder>/                    # e.g. ./parity/ in a package repo, or plotparity-examples/cases/ggtext/
  cases.toml                       # adapter, R packages, render size: everything references depend on
  <folder>/<id>.md                 # case files
  _references/<id>/                # stored R renders + meta.json input hash
  _assets/                         # files cases reference via {assets}

parity.toml (optional)             # saved defaults, in the directory you run from
known_differences.toml, mutations/ # next to parity.toml
.plotparity/runs/<timestamp>/      # run output (report.md / .json / .html, renders); `latest` points at the newest
```

Caches live in `$PLOTPARITY_CACHE` (default `~/.cache/plotparity`): `ports/` holds shallow clones of git sources, and `envs/` holds uv-built environments.

## 2. Configuration

Nothing is required. `plotparity run [PATH…] [--from SOURCE]`:

- **Cases.** Taken from PATHs (case folders or `.md` files inside one cases folder), else `cases` in parity.toml, else `./parity/` or `./cases/`.
- **The package under test.** Comes from `--from` (a directory or `git+URL@rev`), else `from` in parity.toml, else the current directory. Its import name is inferred from `src/<pkg>/` or `<pkg>/`, or given with `--package`.
- **Environment.**
  - The package is tested in a cached uv environment where it is installed with its dependencies, with plotnine, matplotlib, pandas, pyyaml and numpy pinned to the tool's versions.
  - The package's source stays first on `PYTHONPATH`, so the code under test is the checkout.
  - The pins mean the package draws with the same matplotlib (and the same DejaVu TTFs) the references were checked against, and the font-hash check catches drift.

`cases.toml`:

```toml
adapter = "ggtext"
[reference]
packages = ["ggplot2", "ggtext"]   # loaded before each case's R block
lib = ""                           # optional R library path (else R's own / R_LIBS_USER); not part of the hash
[render]
width_px = 640
height_px = 480
dpi = 72
```

`parity.toml` (all optional):

```toml
cases = "../../cases/ggtext"
from = "git+https://github.com/snake-case-ggtext/plotnine-ggtext@<commit>"   # or a path
package = "plotnine_ggtext"      # if it can't be inferred
known_good = "git+…@<commit>"    # a version known to match R: validate checks and selftest use it
default_split = "dev"            # what a bare `plotparity run` selects (default: every case)
requirements = []                # extra packages for the environment
python = ""                      # an explicit interpreter instead of a managed env
install = true                   # install the package itself into the env (brings its dependencies)
api = ["geom_richtext", "element_markdown"]   # names R exports: a case using one the package lacks → missing_api
out = ".plotparity/runs"
html = true
timeout_s = 60
[splits]                         # globs over folder/id; must not overlap
dev = ["geom_richtext/*"]
holdout = ["markup/*"]
[profile.checks.centroid]        # overrides of the adapter's profile
tol = 0.04
```

Unknown keys are errors. CLI flags only change *what* runs (cases, package, output), never how it is judged.

## 3. Case files (`<cases folder>/<folder>/<id>.md`)

```markdown
---
id: my_case               # = file stem, unique in the cases folder
expect: {components: 4, measurable: true, frame: true}   # frame etc. are adapter keys
source: upstream test / docs example / gap list
data: {x: 0.5, y: [0.2, 0.8], label: ["**a**", "b"]}    # → `df` in every engine
---
# my_case
One sentence: why this case exists.
```
Then a ```` ```r ```` block (the spec), and zero or more ```` ```python <package> ```` blocks, e.g. ```` ```python plotnine_ggtext ````. An untagged ```` ```python ```` block is the default for packages that have no block of their own.

- If a case has **no block for the package and no default block**, the result is `unmeasured` (`needs_translation`). This fails closed, and the cases double as a translation to-do list.
- **No `split` in case files.** Projects assign splits.
- Booleans are only `true`/`false`; `y`, `n`, `yes`, `no`, `on`, `off` stay strings.
- `null` becomes R `NA` and Python `None`.
- Allowed types: str, int, float, bool and null.
- Scalars recycle; lengths must divide.
- `{assets}` expands to `<cases folder>/_assets`.
- No randomness. Snippets don't set fonts or themes. R runs with `LC_COLLATE=C`.
- **Splits live in parity.toml.**
  - dev: the agent sees findings.
  - holdout: counts only, trust-based.
  - coverage: R features this package lacks, i.e. the backlog. A case in no split can still be selected by id or glob.
- **Selection:** a comma-separated union of split names, `all`, and ids or globs over ids or folder-relative paths (`geom_textbox/*`). On the command line, PATHs, `-k` and `--split` do this. A selector that matches nothing is an error.

## 4. Engines and error attribution

**Python worker**
- Runs in the candidate interpreter with `PYTHONPATH = <package parent>:<plotparity/src>`.
- Checks that `<package>.__file__` is under the resolved source. If not, the case is an `environment_error`.
- Before each case: resets rcParams, pins fonts, and resets the plotnine theme.
- Draws once to let plotnine finalize layout, then draws **once more through the recording proxy on the Agg renderer and saves `candidate.png` from that same buffer**, so the image and the observations come from one draw.
- Records every `draw_*` route (`draw_path`, `draw_text`, `draw_image` decoded; `draw_path_collection`, `draw_markers`, `draw_quad_mesh`, `draw_gouraud_triangles`, `draw_tex` recorded by name), with paint order, group stack, clip, panel bbox, effective alpha, and the **owner** of each event (§5, "Ownership").
- Tags each event drawn by a themed artist with its ggplot2 **role** (`plot.title`, `axis.text.x.bottom`, `strip.text.x.top`, `legend.text`, …). The themed artists are the ones plotnine applies each theme element to: `theme.targets`, each guide's own theme, the axes' tick labels. These are plotnine 0.15 internals, pinned by `tests/test_roles.py`.
- Calls the adapter's optional `observe_py.observe(fig, plot)` and merges what it returns.
- Then draws once more with the package's top-level artists hidden and records `port_ink_px`: how many panel pixels the package's artists changed. This diagnostic pass never feeds the PNG or the observations.

**R script**
- Registers the same DejaVu TTFs (taken from the candidate interpreter's matplotlib).
- Evaluates the R block and saves the PNG with ragg.
- Calls the adapter's `observe.R`. Observations are saved with 4 decimals unless the adapter sets `adapter_json_digits`.

| Outcome | Classification |
| --- | --- |
| Timeout | `render_timeout` (package fail) |
| `ModuleNotFoundError` for a module other than the package, or the import check fails | `environment_error` |
| `NameError` for a name listed in `candidate.api` | `missing_api` (package fail) |
| Any exception while **drawing** (`fig` render), since drawing runs package code | `render_error` (package fail) |
| Exception while **building** the plot, with a traceback frame in the package | `render_error` (package fail) |
| Exception while **building** the plot, without a frame in the package | `case_error` (infrastructure) |

## 5. Adapters and components

The adapter owns extraction, matching and its own checks.

**Ownership: every candidate draw event is accounted for (core-enforced).** During the recording draw the probe wraps the `draw` of every *top-level artist* (each child of an Axes, each non-Axes child of the Figure) and tags every event with the outermost one that was drawing. Then:

| Event drawn by | Rule |
| --- | --- |
| an artist whose class comes from the package (`type(a).__module__` is the package or below it) | must be assigned to a component by the adapter; anything left over, including paths the segmentation rejects, is `unexpected_drawing` (gating fail) |
| any other artist (plotnine/matplotlib: other layers such as `geom_point`, panel background, grid, axes, legends, titles) | permitted: not the package's responsibility. Counted in the report (`drawing.other_layer_events`) |
| no top-level artist | `unexpected_drawing` |

Known limit: a package that adds plain matplotlib artists (e.g. `ax.text`) straight to the axes is treated as "other layers". Its drawing is still segmented into components and compared, but drawing outside components isn't flagged. Effects that bypass the renderer proxy (an `agg_filter`, writing to the canvas buffer) are invisible to the probe; the `ink_present` and empty-candidate rules below are the backstops.

**ggtext segmentation (Python side) works by geometry, not group names.** Group names are ambiguous: matplotlib's `Text` and `Patch` open `text` and `patch` groups, and packages differ (plotnine-ggtext wraps every box in a single `open_group("richtext")`; ggtext_ShanEllis wraps each label in `open_group("text")`). The steps:
1. Partition draw events by the group *instance* directly below `axes`. The probe records instance ids.
2. A partition counts as rich text if it has text anchored inside the panel.
3. Keep that partition's text and images, plus closed paths strictly inside the panel. This excludes the panel background.
4. A frame path starts a new box, and the text that follows belongs to it.
5. The adapter returns the paint indices of the events it assigned; the core applies the ownership rule to the rest.

**ggtext theme elements.** Role-tagged events never enter the segmentation above; they are assigned.
- **Components:** one per themed artist that drew text. R gets the same from the forced gtable: each cell name maps to a role, and a gTree of `gridtext.*` grobs is one rich label.
- **Scope:** only roles that are rich text in R are compared (`in_scope`). Plain titles are plotnine's own; a package that leaves a rich role plain is still caught, because the role is the same.
- **Pairing:** within a role, by order along the canvas.
- **Position:** measured relative to the panel edge the role hangs off. `chrome_position` is coarse (a fraction of the panel; see the profile); fine placement differs between ggplot2 and plotnine by design.
- **Width-dependent checks:** horizontal padding and line count apply only when both boxes have the same width, since an `element_textbox` takes its width from the layout.

```python
# adapter module
NAME, VERSION, OBSERVE_R, PROFILE, EXPECT_KEYS
def components(engine, observations, expect, canvas_pt) -> (components, assigned_paint_indices)  # {} for "r"
def component_id(i, comp) -> str               # stable id from the reference side
def describe_component(comp) -> dict           # what the core prints for a missing/unexpected component
def match(ref_components, cand_components) -> (pairs, missing, unexpected)   # adapter-local; must
    # not use the properties under test; must enforce a max cost (no forced matches)
CHECKS = {name: fn}     # fn(ref, cand, P, add, expect); returns False when it doesn't apply to the pair
def in_scope(comp, reference) -> bool          # optional: drop candidate components with no reference counterpart
CASE_CHECKS = {name: fn}   # optional whole-plot checks: fn(ref_comps, cand_comps, pairs, P, add, expect)
# optional observe_py.py: observe(fig, plot) -> dict, merged into the Python observations (runs in the package env)
```

A component carries core keys where they apply (the core runs a check only if its key is present):
- `text_runs` (each: `text`, `color_hex`, effective `alpha`, `fontsize_pt`, `fontweight`, `fontstyle`; candidate runs also `visible_fraction`, `ink_quad_norm_tl`)
- `rect` (`facecolor_hex`/`face_alpha`, `edgecolor_hex`/`edge_alpha`, `unrotated_width_pt`/`unrotated_height_pt`)
- `angle_deg`, `centroid_norm_tl`
- adapter extras

**Fail-closed rules (core-enforced)**
- **Unaccounted candidate drawing** (the ownership table above) makes the case **fail** with `unexpected_drawing`. This catches extra, duplicate or stray output drawn outside components.
- **Applicability from the reference.**
  - Rule: a property present on the reference side and missing on the candidate side is a **fail**.
  - Example: the reference box has a frame, the candidate draws none.
- **Text runs align by text content.**
  - Rule: runs are aligned by their text, never by index. If they can't be aligned, `text_content` fails and that pair's style and visibility checks are `unmeasured`.
- **Visibility is checked, not assumed.** A drawn run can still be invisible:
  - `text_alpha` compares effective opacity: the colour's own alpha (R's `#RRGGBBAA`) times the graphics-context alpha (R's gpar `alpha`, inherited down the grob tree; matplotlib's forced artist alpha). `fill_color`/`edge_color` compare frame opacity the same way (`alpha_tol`). A transparent colour is never turned into opaque black.
  - `text_visible` fails a candidate run that lies mostly outside its clip rectangle (or the canvas) while the reference run is visible.
  - `ink_present` (pixel backstop) requires ink in `candidate.png` inside each visible run's rotated rectangle. It can be fooled by other ink under the text (e.g. a `geom_point` dot at the label's anchor); the structured checks above are primary.
- **Empty candidate.** If the reference has components and the candidate has none:
  - the package's artists changed no panel pixels (`port_ink_px == 0`): **fail** (`component_count`, `missing_component`);
  - they did, or it couldn't be measured: `unmeasured` (`extractor_blind`). The adapter can't see how this package draws; extend it. Never a pass.
- **Non-finite numbers.** Profile numbers must be finite and >= 0 (else a configuration error, exit 3). Every number in both sides' components must be finite, and every computed difference is checked before it is compared with a tolerance (`d > nan` is false, which would silently pass). Either violation is an infrastructure error (`check_error`), as is a check that raises.

**Core checks** (`compare.py`)
- inventory: `component_count`, `missing_component`, `unexpected_component`, `unexpected_drawing`, `extractor_blind`, `text_content`, `text_visible`, `ink_present`
- text style: `text_color`, `text_alpha`, `font_weight`, `font_style`, `font_size`
- shape and placement: `fill_color`, `edge_color` (colour and opacity), `rotation`, `centroid`, `shape_size`

**ggtext adapter checks:** `raw_markup_leak`, `sup_sub_offset`, `line_count`, `line_breaks` (advisory), `text_overflow`, `padding`, `rounded_corners`, `chrome_position`.

**ggridges adapter checks:** `ridge_pieces`, `ridge_position` (baseline, height, x extent and top curve, in points), `density_curve`, `ridge_fill`, `ridge_outline`, and the whole-plot `paint_order`. Ridges pair by level; shapes on no level are reported as unexpected components.

**Profile** (shipped by the adapter, overridden in parity.toml)

```toml
[checks.text_color]
severity = "gating"    # gating | advisory | off
tier = "style"         # inventory | layout | style: orders the feedback
tol = 6.0              # numeric keys: tol, rel_tol, alpha_tol, min (finite, >= 0)
calibration = "CIEDE2000; clean max 0"
```

An unknown check name or key is an error.

## 6. Status and exit codes

**Case status**, checked in this order:
1. `infrastructure_error`
2. `fail`: any gating fail
3. `unmeasured`: any gating `unmeasured`, or `measurable: false`
4. `pass`

**Exit codes:** `1` if any case fails, else `2` if any case is unmeasured or infrastructure, else `0`. `3` means a usage or configuration error.

**What a pass means:** the selected gating checks passed for the selected measurable cases. It says nothing about what the adapter doesn't extract (README, "What a pass means").

**Per-check outcomes.** Each case result records, per component and per check, `pass`, `fail`, `unmeasured`, `known_difference`, `off` or `not_applicable` (plus a `case` entry for case-level checks), and a `drawing` summary (package events, how many are in components, other-layer events, events with no owner).

**`known_differences.toml`** (human-owned)
- Each entry has `case`, `check`, an optional `component` glob, and a `reason`.
- Matching findings are reported as `known_difference`, which doesn't gate.
- Unused entries are reported per exact entry (a mistyped `component` glob shows up even if a sibling entry for the same case and check is used).
- The file is in the harness hash.

## 7. References

`plotparity references [PATH…]` (and `run`, for missing or stale ones) stores, per case in `<cases folder>/_references/<id>/`: `reference.png`, `observations.json` (raw R observations; components are computed at run time) and `meta.json`.
References belong to the cases folder because they depend only on the R side. Every package tested against the cases shares them.

`meta.json` holds an **input hash** over:
- the case front matter (minus `split`) and the R block
- the contents of the cases folder's whole `_assets/` folder (conservative: any asset edit makes every reference stale; dotfiles are ignored)
- the `[render]` and `[reference]` config
- `render.R`, `observe.R` and the adapter version
- R and package versions
- font file hashes

Rendering has a timeout (120 s per case). `run` treats a reference as an infrastructure error if:
- its input hash is stale, or
- the font hashes differ from the candidate's.

## 8. Reports

**`report.json`** contains:
- `schema_version`
- the resolved candidate source and commit
- Python, plotnine and matplotlib versions
- the merged profile
- the harness hash
- per-case findings

Each finding has a fingerprint, `case|check|ref_component`. When compared with an earlier run (`run --compare RUN`; by default the last run of the same package and cases), current failures are tagged `new` or `persisting`. Each previous failure that no longer fails is classified from the per-check outcome on the same component:

| Outcome now | Reported as |
| --- | --- |
| the check ran on the still-matched component and passed | `fixed` |
| it matches a known difference | `accepted` (the defect is still there) |
| the check is `off` | `disabled` |
| anything else (component unmatched, case unmeasured or errored, check not applicable or unmeasured) | `gone` |

If the harness hash differs from the previous report's, the report says so and names the changed parts.

**`report.html`**, for people: side by side, blink, swipe and difference views with findings outlined; a Settings panel with every check's severity, tolerance, failure count and calibration, plus the known differences; and OK marks kept in the browser, tied to each render's pixels.

**`report.md`**, for agents:
- status
- per case: PNG paths, then findings by tier (capped)
- known differences, one line each

## 9. Loop and selftest

**Loop**

| Command | What it does |
| --- | --- |
| `loop init` | Stores the harness hash. |
| `loop iterate` | Snapshots the source and records the diff, then runs and compares with the previous iteration. Appends to `loop.jsonl`. Fails the iteration if the harness hash changed. |
| `loop holdout` | Prints counts only. Fails (exit 1) if the harness hash changed since `loop init`, like `iterate`. |
| `loop finish` | Closes the loop. |

The harness hash covers:
- tool source
- parity.toml
- the merged profile
- known differences
- cases
- the reference manifest
- the candidate interpreter's plotnine and matplotlib versions

**Selftest**
- `mutations/suite.toml` has entries `{id, patch, cases, must_fail = [checks]}`, or `must_pass = true` for negative controls.
- Selftest copies `known_good`, applies each patch (`git apply --check` first), runs, and asserts the expectation.
- A `must_fail` mutation must also be **visible**: its `candidate.png` must differ from the clean render of the same case (same engine). The changed-pixel count is printed (`Δpx`); a mutation nobody could see proves nothing about the checker.
- It also asserts that the clean `known_good` passes dev and holdout.

**Validate** checks:
- parsing and adapter `expect` keys
- references are fresh
- each reference's component count matches `expect`
- known differences are unused or used
- with `known_good`: dev and holdout pass, and coverage cases fail only with package findings

## 10. Not built yet (add when a real case needs it)

| Item | Note |
| --- | --- |
| Same-engine regression mode, other engines | The engine abstraction allows them; untested |
| `datasets`, `data_file`, `levels` | No current case needs them |
| Severity `info`, per-split exit status, progress score | Simpler status model |
| Git branch resolution | Ports are a shallow clone of an explicit commit or tag into `~/.cache/plotparity` |
| Known-difference approval/expiry workflow | The file is human-owned and hashed |
| Crash re-run | One process per run; `--isolate` gives one per case |

## 11. Implementation notes

- **Python is the only case parser.** R receives fully expanded data as JSON and never parses YAML, so the strict-boolean rule is enforced once, in one place.
- **References store raw R observations, not components.** Components are computed at run time, so a Python-side adapter change doesn't force an R re-render. The `observe.R` hash and the adapter version are part of the input hash.
- **R colours are resolved with R's `col2rgb`** in `observe.R`, as `#RRGGBB` or `#RRGGBBAA`. R-only names such as `red3` and `grey40` were previously unparseable on the Python side.
- **The R observations keep jsonlite's default 4-decimal precision,** identical to the research references.
- **`MAX_MATCH_COST` (2.5) is an adapter constant.** The canvas size in points comes from cases.toml's `[render]` config and is passed to `components()`.
- **Caches are published atomically.** A git package is fetched into a temp dir, checked for the package, marked complete inside `.git`, then renamed into place; an entry without the marker is rebuilt. A managed env is built under an exclusive lock and trusted only once `.complete` exists (written after the env imports plotnine/matplotlib). Envs aren't built elsewhere and renamed, because a venv embeds its own path.
