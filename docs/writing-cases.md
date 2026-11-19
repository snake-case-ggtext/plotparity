# Writing cases

A **case** is one plot, defined once, with an R version (the spec) and a Python version per package. Cases live in a **cases folder**: any folder with a `cases.toml` at its root (by default `./parity/` in your package's repo). Several packages can share one cases folder, like [`cases/ggtext/`](https://github.com/snake-case-ggtext/plotparity-examples/blob/main/cases/ggtext/) in plotparity-examples.

```toml
# parity/cases.toml
adapter = "ggtext"

[reference]
packages = ["ggplot2", "ggtext"]   # loaded before each case's R block

[render]
width_px = 640
height_px = 480
dpi = 72
```

`plotparity init` writes one for you.

## Quick start

```bash
plotparity new-case parity/markup/my_case.md     # 1. create the file
#                                                  2. edit it (see below)
plotparity run parity/markup/my_case.md --open   # 3. renders the R reference if needed, then compares
plotparity validate                              # 4. check the case is sound
```

Rendering R references needs R with ggplot2 and the extension installed; set `R_LIBS_USER` if they live in a separate library. If you use splits, add the case to one in `parity.toml`.

## The file

````markdown
---
id: my_case                                  # must match the file name
expect: {components: 2, frame: true, measurable: true}
source: where the idea came from (upstream test, docs example, a bug report…)
data:                                        # becomes `df` in BOTH languages
  label: ["**bold** text", "<span style='color:blue'>blue</span>"]
  x: 0.5                                     # scalars repeat to the longest column
  y: [0.3, 0.7]
---

# my_case

One sentence: what this case checks and why.

```r
ggplot(df, aes(x, y, label = label)) + geom_richtext()
```

```python plotnine_ggtext
ggplot(df, aes("x", "y", label="label")) + geom_richtext()
```
````

| Part | Meaning |
| --- | --- |
| `expect.components` | How many rich-text labels the R render should contain: in-panel boxes plus rich theme elements (titles, axis labels, strips, legend text). `validate` checks this against the R reference. |
| `expect.frame` | Whether boxes have a visible frame. Frame checks (padding, corners) only run when it's true. |
| `expect.measurable` | `false` for features the checker can't extract yet (e.g. `<img>` content). Such a case reports `unmeasured`, never a pass. |
| `data` | The one shared data table. |
| ```` ```r ```` | The spec. Its last expression must be the plot. |
| ```` ```python ```` | The Python version. Tag it with an import name (```` ```python plotnine_ggtext ````) when several packages with different APIs share the cases; an untagged block is used by any package without its own. |

**What's in scope in each language**

- **R:** ggplot2, the extension, `grid::unit`, `PT_PER_MM`.
- **Python:** all of `plotnine`, `margin`, `pd`, `np`, and every public name of the package.
- **Both:** the string `{assets}` in data becomes the cases folder's `_assets/` path. Every file in `_assets/` is part of the reference hash, so editing any asset makes all references stale: re-run `references`.

**Rules the loader enforces**

- **Booleans are only `true` and `false`.** `y`, `n`, `yes`, `no`, `on` and `off` stay strings, in both languages. (R once read a column named `y` as `TRUE`.)
- **`null`** becomes `NA` in R and `None` in Python.
- **Values** must be strings, numbers, booleans or null.
- **Column lengths** must divide evenly.
- **No randomness:** both engines must plot identical data.
- **Don't set fonts or themes.** The renderers pin DejaVu Sans on both sides, and R sorts strings in the C locale so factor order matches Python.
- **No `split:` field.** Splits belong in `parity.toml`.

## Checklist

1. **One variant per label.** "px, cm, mm, red3" in one label fails as a blob. One label each makes the finding name the variant. Prefer several small cases over one big one.
2. **All data in `data`.** Never build data differently in the two code blocks; that's how `lightblue1` vs `lightblue` once slipped into an upstream reference.
3. **Translate the meaning, not the syntax.** Each package has its own API and conventions. The [second ggtext package](https://github.com/snake-case-ggtext/plotparity-examples/blob/main/projects/ggtext-shanellis/parity.toml) uses points where R uses mm, treats `\n` as a line break unless you pass `newline="space"`, and measures box sizes *without* margins. Write each package's block so it asks that package for R's behaviour. [This case](https://github.com/snake-case-ggtext/plotparity-examples/blob/main/cases/ggtext/geom_textbox/textbox_rotated_alignments.md) shows all three conventions.
4. **If you use splits** (in `parity.toml`), put the case in one:
   - `dev`: the agent sees the findings.
   - `holdout`: the agent sees pass/fail counts only.
   - `coverage`: R features the package doesn't have yet.

   With a `known_good` version set, a dev or holdout case must pass on it, and `validate` enforces this. If the case fails there, the package really lacks the feature (move it to coverage), or the case has a bug.
5. **Validate after every change.** `validate` catches:
   - a component-count mismatch;
   - a stale R reference;
   - a case that crashes for reasons that aren't the package's fault (`case_error`);
   - dev or holdout cases that don't pass on `known_good`.

   It also lists cases with no Python block for the package.
6. **Never loosen a tolerance to make a case pass.** If R and Python legitimately differ, for reasons outside the package, a human adds an entry to `known_differences.toml` (next to `parity.toml`) with the reason. Tolerances are calibrated on clean pairs only. After touching any check, run `plotparity selftest`.

## Generating a matched set

To cover a grid of variants (every CSS unit × property, every orientation × alignment), generate one case file per variant from a small script. Then run `references` and `validate` as above. If you'll regenerate the set, keep the script next to the cases.
