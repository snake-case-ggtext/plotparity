# plotparity: design

**What it does.** It renders the same plot two ways (a *reference* engine and a *candidate* engine) and records
the draw calls on both sides. It turns them into comparable **components** and reports, per case, whether they
agree. When they don't, it says what differs, where, and by how much. The audience is a coding agent iterating on a port, and the humans who maintain the benchmark.

- Main use: an R ggplot2 extension vs its Python/plotnine port.
- The engines are symmetric plug-ins, so other pairings are possible later. Only R→Python is supported and tested so far.
- The research behind the design found that structured draw-call comparison works; pixel or VLM gating does not.

**Principles**
1. **Fail closed.** Something that can't be measured is never a pass.
2. **Extension-specific logic lives only in adapters.**
3. **Judgement config lives in hashed files, never in CLI flags.**
4. **Build only what a current case or user need requires.** See §10 for what was cut, and why.

## 1. Layout

Two repos. The tool knows nothing about any particular extension's cases.

```
plotparity/                        # the tool (this repo)
  src/plotparity/
    cli.py  project.py (Library, Project)  cases.py  ports.py  runner.py  compare.py  references.py
    report.py  loop.py  selftest.py  validate.py  scaffold.py
    engines/python/  worker.py (runs in the candidate interpreter), engine.py (incl. requirement envs), probe.py
    engines/r/       render.R (fonts, PNG, adapter observe hooks)
    adapters/ggtext/ adapter.py (segmentation, matching, ggtext checks), observe.R, profile.toml
  tests/  (+ tests/fixtures: a 5-case slice of the ggtext library)   skills/plotparity/SKILL.md   docs/

plotparity-examples/               # cases + projects (any repo laid out like this works)
  cases/<extension>/               # SHARED case library (the R side is the spec), e.g. cases/ggtext/
    library.toml                   # adapter, R reference engine/packages, render size
    <feature-folder>/<id>.md       # cases (geom_richtext/, geom_textbox/, markup/, elements/, …)
    _references/<id>/              # stored R renders (depend only on the R side) + meta.json input hash
    _assets/                       # files cases reference via {assets}
  projects/<port>/                 # one port evaluated against a library
    parity.toml                    # library, port package/source/known_good/requirements, [splits], profile overrides
    known_differences.toml         # human-owned allowlist
    mutations/                     # *.patch + suite.toml for selftest
    runs/                          # outputs (gitignored)
```

Caches live in `$PLOTPARITY_CACHE` (default `~/.cache/plotparity`): `ports/` holds shallow clones of git sources, and `envs/` holds uv-built candidate environments.

## 2. Configuration

`cases/<ext>/library.toml` contains everything the stored references depend on:

```toml
[library]
adapter = "ggtext"
[reference]                 # engine "r"
packages = ["ggplot2", "ggtext"]
lib = ""                    # optional R library path; default = R's own (or R_LIBS_USER). Not part of the reference hash
[render]
width_px = 640
height_px = 480
dpi = 72
```

`projects/<port>/parity.toml` covers one port:

```toml
[project]
default_select = "dev"
[cases]
library = "../../cases/ggtext"
[splits]                    # globs over library-relative paths (folder/id) or ids; must not overlap
dev = ["geom_richtext/*", "geom_textbox/textbox_rotated_alignments"]
holdout = ["markup/richtext_feature_matrix_scripts"]
coverage = ["elements/*"]
[candidate]                 # engine "python"
package = "plotnine_ggtext" # import name AND the tag of this port's ```python blocks
source = "git+https://github.com/snake-case-ggtext/plotnine-ggtext@<commit>"   # or a local path
known_good = "git+https://github.com/snake-case-ggtext/plotnine-ggtext@<commit>"  # optional: what dev/holdout must pass
requirements = ["markdown>=3.5"]   # the port's dependencies → a cached uv env
python = ""                 # alternative to requirements: an explicit interpreter path
api = ["geom_richtext", "geom_textbox", "element_markdown", "element_textbox", "element_textbox_simple"]
[render]
timeout_s = 60
[profile.checks.centroid]   # overrides of the adapter's profile
tol = 0.04
```

**`requirements` environments**
- They are built with uv and cached by the hash of their requirements.
- plotnine, matplotlib, pandas, pyyaml and numpy are always pinned to the versions plotparity runs with. The candidate therefore draws with the same matplotlib and the same DejaVu TTFs the references were checked against, and the font hash check catches any drift.

Unknown keys are errors. CLI flags may only change *what* runs (`--select`, `--source`, `--out`), never how it is judged.

## 3. Case files (`cases/<ext>/<folder>/<id>.md`)

```markdown
---
id: my_case               # = file stem, unique in the library
expect: {components: 4, measurable: true, frame: true}   # frame etc. are adapter keys
source: upstream test / docs example / gap list
data: {x: 0.5, y: [0.2, 0.8], label: ["**a**", "b"]}    # → `df` in every engine
---
# my_case
One sentence: why this case exists.
```
Then a ```` ```r ```` block (the spec), and zero or more ```` ```python <port-package> ```` blocks, e.g. ```` ```python plotnine_ggtext ````. An untagged ```` ```python ```` block is the default for ports that have no block of their own.

- If a case has **no block for the project's port and no default block**, the result is `unmeasured` (`needs_translation`). This fails closed, and the cases double as a translation to-do list.
- **No `split` in case files.** Projects assign splits.
- Booleans are only `true`/`false`; `y`, `n`, `yes`, `no`, `on`, `off` stay strings.
- `null` becomes R `NA` and Python `None`.
- Allowed types: str, int, float, bool and null.
- Scalars recycle; lengths must divide.
- `{assets}` expands to `<library>/_assets`.
- No randomness. Snippets don't set fonts or themes. R runs with `LC_COLLATE=C`.
- **Splits are per project.**
  - dev: the agent sees findings.
  - holdout: counts only, trust-based.
  - coverage: R features this port lacks, i.e. the backlog. A case in no split can still be selected by id or glob.
- **Selection:** a comma-separated union of split names, `all`, and ids or globs over ids or library-relative paths (`geom_textbox/*`). A selector that matches nothing is an error.

## 4. Engines and error attribution

**Python worker**
- Runs in the candidate interpreter with `PYTHONPATH = <port parent>:<plotparity/src>`.
- Checks that `<package>.__file__` is under the resolved source. If not, the case is an `environment_error`.
- Before each case: resets rcParams, pins fonts, and resets the plotnine theme.
- Records draw calls through every `draw_*` route, along with route, paint order, group stack, clip and panel bbox. Saves the PNG.

**R script**
- Registers the same DejaVu TTFs (taken from the candidate interpreter's matplotlib).
- Evaluates the R block and saves the PNG with ragg.
- Calls the adapter's `observe.R`.

| Outcome | Classification |
| --- | --- |
| Timeout | `render_timeout` (port fail) |
| `ModuleNotFoundError` for a module other than the port, or the import check fails | `environment_error` |
| `NameError` for a name listed in `candidate.api` | `missing_api` (port fail) |
| Any exception while **drawing** (`fig` render), since drawing runs port code | `render_error` (port fail) |
| Exception while **building** the plot, with a traceback frame in the port | `render_error` (port fail) |
| Exception while **building** the plot, without a frame in the port | `case_error` (infrastructure) |

## 5. Adapters and components

The adapter owns extraction, matching and its own checks.

**ggtext segmentation (Python side) works by geometry, not group names.** Group names are ambiguous: matplotlib's `Text` and `Patch` open `text` and `patch` groups, and ports differ (plotnine-ggtext wraps every box in a single `open_group("richtext")`; ggtext_ShanEllis wraps each label in `open_group("text")`). The steps:
1. Partition draw events by the group *instance* directly below `axes`. The probe records instance ids.
2. A partition counts as rich text if it has text anchored inside the panel.
3. Keep that partition's text and images, plus closed paths strictly inside the panel. This excludes the panel background.
4. A frame path starts a new box, and the text that follows belongs to it.
5. Undecoded routes in a rich-text partition are unassigned, so the case fails closed. It reuses the core checks in `compare.py`.

```python
class Adapter:
    name, version
    def components(engine, observations, canvas, expect) -> (components, unassigned_events)
    def match(ref_components, cand_components) -> (pairs, missing, unexpected)   # adapter-local; must
        # not use the properties under test; must enforce a max cost (no forced matches)
    checks = [...]          # adapter checks, registered by name
    profile = "profile.toml"
```

A component carries:
- id: from the reference side
- `text_runs`
- an optional `shape` (vertices, fill, edge, rounded)
- angle, bbox, clip, paint range
- adapter extras

**Fail-closed rules (core-enforced)**
- **Unassigned candidate events.**
  - Rule: a candidate draw event in the panel that the adapter didn't assign to a component (and didn't whitelist as chrome, e.g. `geom_point` markers) makes the case **fail** with `unexpected_drawing`.
  - Reason: this catches extra or duplicate output drawn outside components.
- **Applicability from the reference.**
  - Rule: a property present on the reference side and missing on the candidate side is a **fail**.
  - Example: the reference box has a frame, the candidate draws none.
- **Text runs align by text content.**
  - Rule: runs are aligned by their text, never by index. If they can't be aligned, `text_content` fails and that pair's style checks are `unmeasured`.
- **Empty candidate.**
  - Rule: if the reference has components and the candidate has none, the result depends on the candidate PNG:
    - no ink in the panel: **fail**
    - ink present: `unmeasured` (`extractor_blind`)
- **Check errors.**
  - Rule: a check that raises, or produces a NaN, is an infrastructure error.

**Core checks** (`compare.py`)
- inventory: `component_count`, `missing_component`, `unexpected_component`
- text: `text_content`, `text_color`, `font_weight`, `font_style`, `font_size`
- shape and placement: `fill_color`, `edge_color`, `rotation`, `centroid`, `shape_size`

**ggtext adapter checks:** `raw_markup_leak`, `sup_sub_offset`, `line_count`, `text_overflow`, `padding`, `rounded_corners`.

**Profile** (shipped by the adapter, overridden by the project)

```toml
[checks.text_color]
severity = "gating"    # gating | advisory | off
tier = "style"         # inventory | layout | style: orders the feedback
tol = 6.0
calibration = "ΔE2000; frozen in REPORT.md before mutation testing"
```

An unknown check name or key is an error.

## 6. Status and exit codes

**Case status**, checked in this order:
1. `infrastructure_error`
2. `fail`: any gating fail
3. `unmeasured`: any gating `unmeasured`, or `measurable: false`
4. `pass`

**Exit codes:** `1` if any case fails, else `2` if any case is unmeasured or infrastructure, else `0`. `3` means a usage error.

**`known_differences.toml`** (human-owned)
- Each entry has `case`, `check`, an optional `component` glob, and a `reason`.
- Matching findings are reported as `known_difference`, which doesn't gate.
- Unused entries are reported.
- The file is in the harness hash.

## 7. References

`plotparity references <library|project>` stores, per case in `<library>/_references/<id>/`: `reference.png`, `observations.json` (raw R observations; components are computed at run time) and `meta.json`.
References belong to the library because they depend only on the R side. Every port evaluated against the library shares them.

`meta.json` holds an **input hash** over:
- the case front matter (minus `split`) and the R block
- the `[render]` and `[reference]` config
- `render.R`, `observe.R` and the adapter version
- R and package versions
- font file hashes

`run` treats a reference as an infrastructure error if:
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

Each finding has a fingerprint, `case|check|ref_component`. With `--previous`, each finding is tagged `new`, `persisting` or `fixed`. A finding is `fixed` only if the pair is still matched and the check ran.

**`report.md`**, for agents:
- status
- per case: PNG paths, then findings by tier (capped)
- known differences, one line each

## 9. Loop and selftest

**Loop**

| Command | What it does |
| --- | --- |
| `loop init` | Stores the harness hash. |
| `loop iterate` | Snapshots the source and records the diff, then runs `run --previous`. Appends to `loop.jsonl`. Fails the iteration if the harness hash changed. |
| `loop holdout` | Prints counts only. |
| `loop finish` | Closes the loop. |

The harness hash covers:
- tool source
- project config
- the merged profile
- known differences
- cases
- the reference manifest
- the candidate interpreter's plotnine and matplotlib versions

**Selftest**
- `mutations/suite.toml` has entries `{id, patch, cases, must_fail = [checks]}`, or `must_pass = true` for negative controls.
- Selftest copies `known_good`, applies each patch (`git apply --check` first), runs, and asserts the expectation.
- It also asserts that the clean `known_good` passes dev and holdout.

**Validate** checks:
- parsing and adapter `expect` keys
- references are fresh
- each reference's component count matches `expect`
- known differences are unused or used
- with `known_good`: dev and holdout pass, and coverage cases fail only with port findings

## 10. Not built yet (add when a real case needs it)

| Item | Note |
| --- | --- |
| Same-engine regression mode, other engines | The engine abstraction allows them; untested |
| `datasets`, `data_file`, `levels` | No current case needs them |
| Severity `info`, per-split exit status, progress score | Simpler status model |
| `paint_order`, `ink_present` checks | Add with the ggridges adapter (paint order), or when a mutation shows the need |
| `explore` command, `new-project` scaffold | |
| Git branch resolution / port caching | Ports are a shallow clone of an explicit commit or tag into `~/.cache/plotparity` |
| Known-difference approval/expiry workflow | The file is human-owned and hashed |
| Crash re-run | One process per run; `--isolate` gives one per case |

## 11. Implementation notes

- **Python is the only case parser.** R receives fully expanded data as JSON and never parses YAML, so the strict-boolean rule is enforced once, in one place.
- **References store raw R observations, not components.** Components are computed at run time, so a Python-side adapter change doesn't force an R re-render. The `observe.R` hash and the adapter version are part of the input hash.
- **R colours are resolved with R's `col2rgb`** in `observe.R`, as `#RRGGBB` or `#RRGGBBAA`. R-only names such as `red3` and `grey40` were previously unparseable on the Python side.
- **The R observations keep jsonlite's default 4-decimal precision,** identical to the research references.
- **`MAX_MATCH_COST` (2.5) and `CANVAS_PT` (640×480 pt) are adapter constants.** `CANVAS_PT` assumes the 640×480 @ 72 dpi canvas; see the open issues in AGENTS.md.
- **Empty candidate output is a `fail`** (`component_count` and `missing_component`). It is never `unmeasured`.
