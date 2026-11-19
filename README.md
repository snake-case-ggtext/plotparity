# plotparity

Test a [plotnine](https://plotnine.org) extension against the ggplot2 original.

You write each test plot once, in R and in Python. plotparity renders both, records every text, shape and image each one draws, and compares them piece by piece:

```
## markup/r_colour_names: FAIL (2 findings)
  1. [style] box[1:'R colours red3 grey40 st']/run[2:'red3'] :: text_color  expected #cd0000 → observed #000000
       Span 'red3' rendered with color #000000 instead of #cd0000 (ΔE=42.17).
```

That's a real finding: a Python port of ggtext didn't know R's colour name `red3`, drew the word in black, and raised no error. A screenshot comparison can't catch this, because R and Python plots never match pixel for pixel.

## Install

```bash
uv tool install git+https://github.com/snake-case-ggtext/plotparity
```

You need [uv](https://docs.astral.sh/uv/). R (with ggplot2 and the extension) is only needed to render the R side of new or changed test plots; those renders are stored next to the cases.

## Use it

From your package's repo, with test plots in `./parity/`:

```bash
plotparity run --open
```

Or point it at cases and a package anywhere:

```bash
plotparity run path/to/cases --from ~/code/plotnine-foo
plotparity run path/to/cases --from git+https://github.com/someone/plotnine-foo@v0.3
```

`run` finds the package, installs it with its dependencies into a cached environment, renders any missing R references, compares, and writes a report to `.plotparity/runs/` (`report.md`, `report.json`, `report.html`). `--open` opens the HTML report, which shows R and Python side by side (also blink, swipe and difference) with each finding outlined on both images.

Cases plotparity can't measure yet (such as `<img>`) are compared by eye. **OK** marks a finding or a whole case as reviewed. Marks are saved in your browser and tied to that exact render, so they carry over to later runs until the render changes.

| Option | |
| --- | --- |
| `PATH …` | case folders or `.md` files (default: `./parity/`) |
| `--from SOURCE` | the package under test: a directory or `git+URL@rev` (default: `.`) |
| `-k EXPR` | only cases whose name matches, e.g. `-k "textbox and not rotated"` |
| `--split NAME` | only a named group of cases, from `parity.toml` |
| `--open` / `--no-html` | open the HTML report / don't build it |
| `-q` | print only the status line |
| `--compare RUN` / `--no-compare` | mark findings new/fixed against a run (default: the last run of the same package and cases) |
| `--no-render-r` | fail instead of rendering missing R references (for CI without R) |

`plotparity view` reopens the latest report; `plotparity view RUN RUN` shows several runs side by side. `plotparity init` saves your defaults to a `parity.toml`.

## Reading the results

| Status | Meaning | What to do |
| --- | --- | --- |
| matches | every check agrees with R | nothing |
| differs | at least one check disagrees | fix the first findings first: content, then layout, then style |
| not measured | the case can't be compared yet (e.g. `<img>`) | look at the images; it is not a pass |
| needs translation | the case has no Python block for your package | add one to the case file |
| error | the case, reference or environment is broken, not your package | fix the case or the setup |

Exit codes: `0` all match, `1` something differs, `2` something wasn't measured or errored, `3` usage error.

If a difference is real but not your package's fault (for example, ggplot2 and plotnine lay out panels slightly differently), add it to `known_differences.toml` with the reason. It's then reported but no longer fails the run.

To change what counts as close enough for one project, override a check in `parity.toml`; the report's **Settings** panel lists every check with its tolerance and failure count:

```toml
[profile.checks.padding]
tol = 3.0                 # or severity = "advisory": reported, never fails a case
```

## Writing test plots

A case is one Markdown file: a shared data table, the R code, and the Python code.

````markdown
---
id: r_colour_names
expect: {components: 1, frame: true}
data:
  label: R colours <span style='color:red3'>red3</span> <span style='color:grey40'>grey40</span>
  x: 0.05
  y: 0.5
---

R colour names in span CSS.

```r
ggplot(df, aes(x, y, label = label)) + geom_richtext(hjust = 0) + xlim(0, 1) + ylim(0, 1)
```

```python
ggplot(df, aes("x", "y", label="label")) + geom_richtext(hjust=0) + xlim(0, 1) + ylim(0, 1)
```
````

`plotparity new-case parity/markup/my_case.md` creates one. See [docs/writing-cases.md](docs/writing-cases.md).

## Using it with a coding agent

Point the agent at [skills/plotparity/SKILL.md](skills/plotparity/SKILL.md). It covers the run–fix loop, how to read findings, and the rules (edit only the package; never the cases, references, tolerances or `known_differences.toml`). Agents usually want `--no-html`.

The agent may *propose* changes to the bar (a tolerance, an advisory check, a known difference) with the evidence and a ready-to-paste snippet; you decide. Those rules are enforced only under `plotparity loop`, which hashes the cases, settings and known differences and fails an iteration if they change. Plain `plotparity run` doesn't check.

## What a pass means

A pass means the checks passed for the cases that could be measured. For each thing R draws (for ggtext: each rich-text box), plotparity checks:

- **what is drawn:** the words and their order, no leaked markup (`**`, `<span>`, `~sub~`), the number of boxes, and nothing extra drawn by your package;
- **how text looks:** colour, opacity, weight, style, size, superscript/subscript offsets, and that it's visible (not clipped, actually inked);
- **layout:** rotation, placement, box size, padding, line count, overflow;
- **frames:** fill and outline colour and opacity, rounded corners;
- **theme elements** (`element_markdown`, `element_textbox` on titles, axis labels, strips, legend text): the same text, style, frame and padding checks, and a coarse position check (off by a large share of the panel along or away from its edge: wrong alignment, wrong side).

Not checked yet:

- fine placement of theme elements (a few points of margin), since ggplot2 and plotnine lay them out differently by design;
- the content of `<img>` images;
- in-panel boxes on facets (only the first panel is read);
- plain matplotlib artists a package adds straight to the axes, and drawing that bypasses matplotlib's renderer;
- glyph shapes and kerning, outline width and dashes, stacking order against other layers.

## Other extensions

Two adapters ship today: **ggtext** (rich-text labels and theme elements) and **ggridges** (ridgeline plots: each ridge's shape, density, fill, outline, pieces and which ridge is drawn on top). Supporting another extension means writing an adapter (what counts as a component, how to find components on both sides, how to pair them, which checks apply): see [docs/writing-adapters.md](docs/writing-adapters.md).

## Example

[plotparity-examples](https://github.com/snake-case-ggtext/plotparity-examples) has cases for ggtext and ggridges, and three packages tested against them: one that matches R and two with real gaps:

```bash
git clone https://github.com/snake-case-ggtext/plotparity-examples
cd plotparity-examples/projects/ggtext-shanellis && plotparity run --open
```

## More

- [docs/writing-cases.md](docs/writing-cases.md): case format and checklist
- [docs/writing-adapters.md](docs/writing-adapters.md): supporting another extension
- [DESIGN.md](DESIGN.md): how it works
- [AGENTS.md](AGENTS.md): working on plotparity itself

Developing plotparity: `uv sync && uv run pytest -q`.
