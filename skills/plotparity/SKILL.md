---
name: plotparity
description: Make a Python/plotnine package render like its ggplot2 original, using plotparity as the judge. Use when asked to port a ggplot2 extension feature to plotnine, fix a plotnine extension's parity with R, or run/interpret plotparity results.
---

# Working on a plotnine package with plotparity

plotparity renders each test case in R (stored references) and in the package, records the draw calls on both sides, and reports what differs. **The spec is R's behaviour.** Inspect R's documented API (`?pkg::fn`, `args(pkg::fn)`), built layer data (`ggplot2::ggplot_build(p)`), and rendered outputs, and use the findings to see where the Python side still differs. *(Clean-room rule: only read an R package's source code when its license is compatible with your port; for MIT/BSD ports of GPL-3 R packages, rely on docs, built outputs, and black-box behaviour only.)*

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
- **Your job is to make the package match R, not to move the bar.** Never suggest a tolerance, a severity or a known difference. A pattern across many cases (the same check off by a similar amount) almost always means one bug in the package: find it.
- **If you believe a finding is the harness's fault** (a check measuring the wrong thing, a broken case or reference), report it in your note or summary with the evidence: case, check, values, why. The finding still counts as failing; it isn't done.
- **These rules are enforced only under `plotparity loop`** (the harness hash). With plain `plotparity run` nothing stops an edit to `parity.toml`; follow them anyway.
