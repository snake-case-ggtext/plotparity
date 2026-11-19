---
name: plotparity
description: Make a Python/plotnine package render like its ggplot2 original, using plotparity as the judge. Use when asked to port a ggplot2 extension feature to plotnine, fix a plotnine extension's parity with R, or run/interpret plotparity results.
---

# Working on a plotnine package with plotparity

plotparity renders each test case in R (stored references) and in the package, records the draw calls on both sides, and reports what differs. **The spec is R's behaviour.** Read the R package's source for semantics (`Rscript -e 'print(pkg:::fn)'`, `ls(asNamespace("pkg"), all.names=TRUE)`), and use the findings to see where the Python side still differs.

## Run

```bash
PP="uvx --from git+https://github.com/snake-case-ggtext/plotparity plotparity"   # or `plotparity` if installed
$PP run --no-html                      # from the package repo, cases in ./parity/
$PP run path/to/cases --from path/to/package --no-html
$PP run --no-html -k textbox           # a subset
```

Findings are printed in tier order: **inventory → layout → style**. Fix the first tier first; later findings are often consequences (a missing box makes every check on it fail). Each finding names the case, the component (`box[2:'Some text …']/run[1:'blue']`), the check, and R's value vs yours. The report lists both PNG paths; look at them.

| Finding / status | Meaning |
| --- | --- |
| `missing_api` | the case uses a function the package doesn't export yet |
| `render_error` | the package raised (traceback included) |
| `unexpected_drawing` | the package drew something that isn't part of any component (an extra shape, a stray line, a box outside the panel) |
| `text_alpha`, `text_visible`, `ink_present` | text that is drawn but not (fully) visible |
| `needs_translation` | the case has no ```` ```python <package> ```` block; writing one is case authoring, not package work |
| `unmeasured` | plotparity can't observe this feature yet; not a pass |
| `infrastructure_error` (`case_error`, `stale_reference`, `environment_error`, …) | the case, reference or setup is broken; report it, don't work around it in the package |

Exit codes: 0 all match, 1 something differs, 2 unmeasured or error, 3 usage error.

## Loop (recorded iterations)

When asked to run a recorded porting loop (needs a `parity.toml` with `dev` and `holdout` splits):

```bash
$PP loop init    --name <loop> --goal "<what you're porting>" [--from <package checkout>]
# edit the package …
$PP loop iterate --name <loop> --note "<what you changed and which finding it addresses>"
# … repeat; when dev passes:
$PP loop holdout --name <loop>          # counts only
$PP loop finish  --name <loop> --summary "<outcome; anything you think plotparity missed>"
```

Findings carry `(new)` / `(persisting)` tags. "N fixed" counts only checks that ran and passed on the same component; `accepted` / `disabled` / `gone` mean a finding vanished for another reason, not that you fixed it.

## Rules

- **Edit only the package.**
  - Never edit plotparity, `parity.toml`, the cases (including `_references/`), `known_differences.toml`, or the installed plotnine/matplotlib.
  - A loop hashes all of these and fails any iteration after they change (`harness_changed`).
- **If a finding looks wrong or like an acceptable R-vs-Python difference,** say so in your note or summary. A human decides whether it goes into `known_differences.toml`.
- **Don't special-case test inputs** (labels, coordinates, colours). Holdout cases exist to catch that.
- **Don't read** holdout reports (`.plotparity/runs/loops/<loop>/holdout_*`), mutation patches, or other implementations of the same package.
- **One coherent change per iteration,** with an honest note.
