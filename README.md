# plotparity

**Does my Python port draw the same plot as the R original?**

plotparity answers that for ggplot2 extensions ported to [plotnine](https://plotnine.org). It renders the same plot in R and in Python and records every text, shape and image each one draws. Then it compares those drawings piece by piece and tells you exactly what differs.

For example, it caught a Python port of ggtext that didn't recognise R's colour name `red3`: the port drew the word in black and raised no error. Comparing screenshots wouldn't have caught it, because an R plot and a Python plot never match pixel for pixel anyway.

It's built for **coding agents porting an extension**: fast feedback (a few seconds), findings that say what to fix, and checks that are hard to game. It works just as well for a human.

---

## Try it in two minutes

You need [uv](https://docs.astral.sh/uv/) and git. The worked examples live in their own repo, so clone that and run plotparity on it straight from GitHub:

```bash
git clone https://github.com/snake-case-ggtext/plotparity-examples
cd plotparity-examples
uvx --from git+https://github.com/snake-case-ggtext/plotparity plotparity run projects/ggtext
```

That compares the [plotnine-ggtext](https://github.com/snake-case-ggtext/plotnine-ggtext) port with R's ggtext on six cases, and should print `PARITY: PASS`. You don't need R: the R renders are stored in the examples repo. You don't need the port either: plotparity fetches it from GitHub at a pinned commit and builds its environment for you. The first run takes about 20 seconds while that happens, and later runs take a few.

For the interesting run, point it at a second ggtext port that has real gaps:

```bash
uvx --from git+https://github.com/snake-case-ggtext/plotparity plotparity run projects/ggtext-shanellis --select all
```

Among its findings is that `red3` example:

```
## markup/coverage_r_colour_names: FAIL (2 findings)
  1. [style] box[1:'R colours red3 grey40 st']/run[2:'red3'] :: text_color  expected #cd0000 → observed #000000
       Span 'red3' rendered with color #000000 instead of #cd0000 (ΔE=42.17).
```

Each finding names the component (the first box, the word `red3`), the check that failed (`text_color`), and R's value vs. the port's.

**Output**

Each run writes `report.md` (the readable one shown above, with links to the R and Python PNGs) and `report.json` (everything) to `projects/<name>/runs/`.

**Exit codes**

| Code | Meaning |
| --- | --- |
| `0` | everything passes |
| `1` | any failure |
| `2` | something couldn't be measured |

Use the exit code to gate CI or an agent loop.

**To use plotparity regularly,** install it once as a tool, and then it's just `plotparity`:

```bash
uv tool install git+https://github.com/snake-case-ggtext/plotparity
plotparity run projects/ggtext
```

## How it fits together

```
plotparity-examples/
  cases/ggtext/              the CASES: plots defined once, R + Python side by side
    geom_richtext/  geom_textbox/  markup/  elements/
    _references/             stored R renders: the "expected" answers
  projects/ggtext/           a PROJECT: one port, compared against those cases
    parity.toml              which port (git URL + commit), its dependencies, which cases are dev/holdout
    known_differences.toml   differences a human has accepted as "not a bug"
```

- **One set of cases serves every port.** [plotnine-ggtext](https://github.com/snake-case-ggtext/plotparity-examples/blob/main/projects/ggtext/parity.toml) and [Shannon Ellis's ggtext](https://github.com/snake-case-ggtext/plotparity-examples/blob/main/projects/ggtext-shanellis/parity.toml) share the same cases and the same R renders.
- **Each project picks its own splits.** `dev` cases give the agent full feedback. `holdout` cases show pass/fail counts only. `coverage` cases are R features the port doesn't have yet, which makes them the to-do list.

## What a case looks like

A case is one Markdown file. Here's [a real one](https://github.com/snake-case-ggtext/plotparity-examples/blob/main/cases/ggtext/markup/coverage_r_colour_names.md), trimmed:

````markdown
---
id: coverage_r_colour_names
expect: {components: 1, frame: true, measurable: true}
data:
  label: R colours <span style='color:red3'>red3</span> <span style='color:grey40'>grey40</span>
  x: 0.05
  y: 0.5
---

# coverage_r_colour_names

R colour names (`red3`, `grey40`) in span CSS.

```r
ggplot(df, aes(x, y, label = label)) + geom_richtext(hjust = 0) + xlim(0, 1) + ylim(0, 1)
```

```python plotnine_ggtext
ggplot(df, aes("x", "y", label="label")) + geom_richtext(hjust=0) + xlim(0, 1) + ylim(0, 1)
```

```python ggtext
ggplot(df, aes("x", "y", label="label")) + geom_richtext(hjust=0) + xlim(0, 1) + ylim(0, 1)
```
````

Reading it top to bottom:

- **`data`** is a single table that becomes `df` in both languages. Data lives here, never inside the code blocks, so R and Python can't accidentally plot different things.
- **`expect`** tells the checker what to look for. Here that's one labelled box, drawn with a frame.
- **One sentence** says why the case exists.
- **The `r` block is the spec.** Its render is the expected answer.
- **Each `python` block is tagged with the port it's written for.** Ports have different APIs, so each one gets its own translation of the same plot. A port with no block for a case reports `needs_translation`, rather than silently skipping it.

For richer examples, see [a rotated-box case](https://github.com/snake-case-ggtext/plotparity-examples/blob/main/cases/ggtext/geom_textbox/textbox_rotated_alignments.md), whose second port uses different units and margin conventions, and [a theme-element case](https://github.com/snake-case-ggtext/plotparity-examples/blob/main/cases/ggtext/elements/coverage_element_markdown_title.md).

To add your own, follow **[Writing cases](docs/writing-cases.md)**. It covers how to scaffold a case, render its R reference, validate it, and generate a matched set of variants.

## Common tasks

Run these from a directory with cases and projects, such as `plotparity-examples`.

| I want to… | Run |
| --- | --- |
| check a port | `plotparity run projects/ggtext` |
| check my local checkout of the port | `… run projects/ggtext --source ~/code/plotnine-ggtext` |
| check a pinned commit | `… --source git+https://github.com/org/repo@<commit-or-tag>` |
| run particular cases | `… --select holdout`, `--select 'markup/*'`, `--select richtext_example_markup` |
| see what a port is missing | `… run projects/ggtext --select coverage` |
| add a case | [Writing cases](docs/writing-cases.md) |
| evaluate a new port of ggtext | copy `projects/ggtext/`, edit `parity.toml` ([example](https://github.com/snake-case-ggtext/plotparity-examples/blob/main/projects/ggtext-shanellis/parity.toml)), add `python <package>` blocks |
| let an agent port a feature | [the agent skill](skills/plotparity/SKILL.md) (`plotparity loop …`) |
| check the checker still catches bugs | `plotparity selftest projects/ggtext` (re-runs known-bad mutations) |

## Learn more

- [Writing cases](docs/writing-cases.md): the case format, rules, and authoring checklist
- [DESIGN.md](DESIGN.md): how it works, and the rules that stop it passing broken ports
- [AGENTS.md](AGENTS.md): current status, open issues and working notes
- [plotparity-examples](https://github.com/snake-case-ggtext/plotparity-examples): the ggtext case library and projects

**Working on plotparity itself:** `uv sync && uv run pytest -q`. The tests use a small built-in fixture. Set `PLOTPARITY_TEST_PORT=~/code/plotnine-ggtext` to test against a local checkout instead of the pinned GitHub commit.
