---
name: plotparity
description: Port (or repair) a ggplot2 extension's Python/plotnine port until it renders like R, using the plotparity harness as the judge. Use when asked to port a ggplot2 extension feature to plotnine, fix a plotnine port's parity with R, or run/interpret a plotparity project.
---

# Porting with plotparity

plotparity renders each benchmark case in R (stored references) and in your port, records the draw calls on both
sides, and tells you what differs. **The spec is R's behaviour**: read the R package's source for semantics
(`Rscript -e 'print(pkg:::fn)'`, `ls(asNamespace("pkg"), all.names=TRUE)`); use plotparity's findings to see
where you're still wrong.

## Loop

```bash
# Run from the repo holding the cases and projects (e.g. plotparity-examples).
PP="uvx --from git+https://github.com/snake-case-ggtext/plotparity plotparity"   # or `plotparity` if installed as a uv tool
$PP loop init    <project> --name <loop> --goal "<what you're porting>" --source <your port checkout>
# edit the port …
$PP loop iterate <project> --name <loop> --note "<what you changed and which finding it addresses>"
# … repeat; when dev passes:
$PP loop holdout <project> --name <loop>          # counts only
$PP loop finish  <project> --name <loop> --summary "<outcome; anything you think the harness missed>"
```

- `iterate` prints findings ordered **inventory → layout → style**. Fix the first tier first: later findings are
  often consequences (a missing box makes every check on it fail).
- Findings carry `(new)` / `(persisting)` tags; "since previous run: N fixed" counts real fixes.
- Each finding names the case, component (`box[2:'Some text …']/run[1:'blue']`), check, expected (R) vs observed
  (yours). The report also gives both PNG paths; look at them.
- `missing_api` = the case uses a function your port doesn't export yet. `render_error` = your code raised.
- `needs_translation` (unmeasured) = the shared case has no ```` ```python <your package> ```` block. Translating
  cases is case authoring (it edits the shared library, which the loop hashes): do it outside a loop, then
  `plotparity validate`, then start a new loop.
- `infrastructure_error` (`case_error`, `stale_reference`, `environment_error`, …) is NOT yours to fix in the port:
  report it.

## Rules (the loop records everything; breaking these fails iterations)

- Edit only the port. Never edit the harness, the project's `parity.toml`, the case library (`cases/<ext>/`, incl.
  `_references/`), `known_differences.toml`, or the installed plotnine/matplotlib: the loop hashes all of these and FAILS any
  iteration after they change (`harness_changed`).
- If you think a finding is wrong or an acceptable cross-engine difference, say so in your `--note` and in
  `finish --summary`. A human decides whether it goes into `known_differences.toml`.
- Don't special-case benchmark inputs (labels, coordinates, colours). Holdout cases exist to catch that.
- Don't read holdout reports (`runs/loops/<loop>/holdout_*`), other ports' sources, or mutation patches.
- One coherent change per iteration, with an honest note.

## Interpreting status

`pass` all gating checks agree · `fail` at least one gating check differs · `unmeasured` the harness can't
observe the feature yet (not a pass) · `infrastructure_error` the case/reference/environment is broken.
Exit code: 0 pass, 1 fail, 2 unmeasured/infra, 3 usage error.
