# Writing cases

A **case** is one plot, defined once, with an R version (the spec) and one Python version per port. Cases live in a **library**, e.g. [`cases/ggtext/`](https://github.com/snake-case-ggtext/plotparity-examples/blob/main/cases/ggtext/) in plotparity-examples, and every project that evaluates a port of that extension shares them.

## Quick start

```bash
plotparity new-case cases/ggtext markup/my_case --package plotnine_ggtext   # 1. scaffold cases/ggtext/markup/my_case.md
#                                                                      2. edit it (see below)
plotparity references cases/ggtext --select my_case                         # 3. render the R reference (needs R)
plotparity validate projects/ggtext                                         # 4. check the case is sound
plotparity run projects/ggtext --select my_case                             # 5. see how the port does
```

Run these from the root of the repo that holds your cases and projects (e.g. plotparity-examples), with `plotparity` installed (`uv tool install git+https://github.com/snake-case-ggtext/plotparity`). Rendering references needs R with ggplot2 and the extension installed; set `R_LIBS_USER` if they live in a separate library. Then add the case to one of the project's splits in `parity.toml`.

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
| `expect.components` | How many labelled boxes the R render should contain. `validate` checks this against the R reference. |
| `expect.frame` | Whether boxes have a visible frame. Frame checks (padding, corners) only run when it's true. |
| `expect.measurable` | `false` for features the checker can't extract yet (e.g. theme elements). Such a case reports `unmeasured`, never a pass. |
| `data` | The one shared data table. |
| ```` ```r ```` | The spec. Its last expression must be the plot. |
| ```` ```python <package> ```` | One block per port, tagged with the port's import name. An untagged ```` ```python ```` block is the fallback. |

**What's in scope in each language**

- **R:** ggplot2, the extension, `grid::unit`, `PT_PER_MM`.
- **Python:** all of `plotnine`, `margin`, `pd`, `np`, and every public name of the port.
- **Both:** the string `{assets}` in data becomes the library's `_assets/` path.

**Rules the loader enforces**

- **Booleans are only `true` and `false`.** `y`, `n`, `yes`, `no`, `on` and `off` stay strings, in both languages. (R once read a column named `y` as `TRUE`.)
- **`null`** becomes `NA` in R and `None` in Python.
- **Values** must be strings, numbers, booleans or null.
- **Column lengths** must divide evenly.
- **No randomness:** both engines must plot identical data.
- **Don't set fonts or themes.** The renderers pin DejaVu Sans on both sides, and R sorts strings in the C locale so factor order matches Python.
- **No `split:` field.** Splits belong to each project's `parity.toml`.

## Checklist

1. **One variant per label.** "px, cm, mm, red3" in one label fails as a blob. One label each makes the finding name the variant. Prefer several small cases over one big one.
2. **All data in `data`.** Never build data differently in the two code blocks; that's how `lightblue1` vs `lightblue` once slipped into an upstream reference.
3. **Translate the meaning, not the syntax.** Each port has its own API and conventions. The [second ggtext port](https://github.com/snake-case-ggtext/plotparity-examples/blob/main/projects/ggtext-shanellis/parity.toml) uses points where R uses mm, treats `\n` as a line break unless you pass `newline="space"`, and measures box sizes *without* margins. Write each port's block so it asks that port for R's behaviour. [This case](https://github.com/snake-case-ggtext/plotparity-examples/blob/main/cases/ggtext/geom_textbox/textbox_rotated_alignments.md) shows all three conventions.
4. **Put the case in a split, per project:**
   - `dev`: the agent sees the findings.
   - `holdout`: the agent sees pass/fail counts only.
   - `coverage`: R features this port doesn't have yet.

   A dev or holdout case must pass on the project's `known_good` port, and `validate` enforces this. If the case fails there, the port really lacks the feature (move it to coverage), or the case has a bug.
5. **Validate after every change.** `validate` catches:
   - a component-count mismatch;
   - a stale R reference;
   - a case that crashes for reasons that aren't the port's fault (`case_error`);
   - dev or holdout cases that don't pass on `known_good`.

   It also lists cases with no block for the project's port.
6. **Never loosen a tolerance to make a case pass.** If R and Python legitimately differ, for reasons outside the port, a human adds an entry to the project's `known_differences.toml` with the reason. Tolerances are calibrated on clean pairs only. After touching any check, run `plotparity selftest`.

## Generating a matched set

To cover a grid of variants (every CSS unit × property, every orientation × alignment), generate one case file per variant from a small script. Then run `references` and `validate` as above. If you'll regenerate the set, keep the script next to the cases.
