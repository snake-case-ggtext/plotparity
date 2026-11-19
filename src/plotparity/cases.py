"""Case library files: `cases/<library>/<feature-folder>/<id>.md` = YAML front matter + a sentence + code blocks.

Blocks: one ```r block (the spec) and zero or more ```python blocks, each TAGGED with the port package it targets
(```python plotnine_ggtext); an untagged ```python block is the default for ports without their own block.
Splits are not in case files: projects assign them (globs over library-relative paths).

Python is the only parser. Data is resolved here once (strict booleans, null, recycling, `{assets}`) and
handed to every engine as fully expanded columns, so both engines see exactly the same table.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_FM_KEYS = {"id", "expect", "source", "data", "canvas", "geom", "upstream", "deep_probe"}
_FENCE_RE = re.compile(r"^```(\w+)([^\n]*)\n(.*?)^```", re.S | re.M)


class CaseError(ValueError):
    """A case file is malformed (usage/config error, exit code 3)."""


class _StrictBoolLoader(yaml.SafeLoader):
    """Only true/false are booleans. YAML 1.1 also treats y/n/yes/no/on/off as booleans (R's yaml even
    turns a column named `y` into TRUE); here they stay strings, in keys and values."""


_StrictBoolLoader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:bool"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_StrictBoolLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


def load_yaml(text: str) -> Any:
    return yaml.load(text, Loader=_StrictBoolLoader)


@dataclass(frozen=True)
class Case:
    id: str
    relpath: str                              # library-relative path without .md, e.g. "geom_richtext/foo"
    path: Path
    front_matter: dict[str, Any]
    r_code: str
    python: dict[str, str]                    # port package tag ("" = default) -> code block
    why: str
    expect: dict[str, Any] = field(default_factory=dict)

    @property
    def measurable(self) -> bool:
        return bool(self.expect.get("measurable", True))

    @property
    def n_components(self) -> int | None:
        return self.expect.get("components")

    def python_for(self, package: str) -> str | None:
        """The port-specific block, else the default block, else None (→ `needs_translation`)."""
        return self.python.get(package, self.python.get(""))

    def table(self, assets_dir: Path) -> dict[str, list] | None:
        """Shared data table: fully expanded columns (recycled), `{assets}` substituted."""
        return expand_data(self.front_matter.get("data"), assets_dir, where=str(self.path))

    def reference_fingerprint(self) -> str:
        """Hash of everything in the case that affects the R reference render (not the python blocks)."""
        blob = json.dumps({"fm": self.front_matter, "code": self.r_code}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()


def _check_value(v: Any, where: str) -> None:
    if v is not None and not isinstance(v, (str, int, float, bool)):
        raise CaseError(f"{where}: data values must be str/int/float/bool/null, got {type(v).__name__}: {v!r}")


def expand_data(data: dict[str, Any] | None, assets_dir: Path, where: str = "case") -> dict[str, list] | None:
    if not data:
        return None
    if not isinstance(data, dict):
        raise CaseError(f"{where}: `data` must be a mapping of column -> value or list")
    cols: dict[str, list] = {}
    for k, v in data.items():
        if not isinstance(k, str):
            raise CaseError(f"{where}: column name {k!r} is not a string")
        vals = v if isinstance(v, list) else [v]
        for x in vals:
            _check_value(x, f"{where}: column {k!r}")
        cols[k] = [x.replace("{assets}", str(assets_dir)) if isinstance(x, str) else x for x in vals]
    n = max(len(v) for v in cols.values())
    out = {}
    for k, v in cols.items():
        if n % len(v) != 0:
            raise CaseError(f"{where}: column {k!r} has {len(v)} values, which does not divide the table length {n}")
        out[k] = v * (n // len(v))
    return out


def parse_case(path: Path, root: Path | None = None) -> Case:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise CaseError(f"{path}: must start with YAML front matter (---)")
    try:
        _, fm_text, body = text.split("---\n", 2)
    except ValueError:
        raise CaseError(f"{path}: unterminated front matter") from None
    fm = load_yaml(fm_text) or {}
    if "split" in fm:
        raise CaseError(f"{path}: `split` is not a case property; projects assign splits in parity.toml [splits]")
    unknown = set(fm) - _FM_KEYS
    if unknown:
        raise CaseError(f"{path}: unknown front-matter keys {sorted(unknown)} (allowed: {sorted(_FM_KEYS)})")
    if fm.get("id") != path.stem:
        raise CaseError(f"{path}: id {fm.get('id')!r} must equal the file name {path.stem!r}")
    rubric_cut = body.split("\n## VLM rubric", 1)[0]       # legacy research section, ignored
    r_code, python = None, {}
    for lang, info, block in _FENCE_RE.findall(rubric_cut):
        lang = {"py": "python", "R": "r"}.get(lang, lang)
        tag = info.strip()
        if lang == "r":
            if r_code is not None:
                raise CaseError(f"{path}: more than one ```r block")
            r_code = block.rstrip()
        elif lang == "python":
            if tag in python:
                raise CaseError(f"{path}: more than one ```python {tag} block")
            python[tag] = block.rstrip()
    if r_code is None:
        raise CaseError(f"{path}: needs a ```r block (the reference)")
    why = "\n".join(l for l in _FENCE_RE.sub("", rubric_cut).splitlines() if l.strip() and not l.startswith("#")).strip()
    expect = fm.get("expect") or {}
    if not isinstance(expect, dict):
        raise CaseError(f"{path}: expect must be a mapping")
    rel = path.relative_to(root).with_suffix("").as_posix() if root else path.stem
    case = Case(id=fm["id"], relpath=rel, path=path, front_matter=fm, r_code=r_code, python=python, why=why,
                expect=expect)
    case.table(Path("."))  # validate data eagerly
    return case


def load_library(root: Path) -> dict[str, Case]:
    """All *.md under the library root (recursively), skipping folders that start with `_` (references, assets)."""
    cases: dict[str, Case] = {}
    for p in sorted(Path(root).rglob("*.md")):
        if any(part.startswith(("_", ".")) for part in p.relative_to(root).parts[:-1]) or p.name.startswith(("_", "README")):
            continue
        c = parse_case(p, root)
        if c.id in cases:
            raise CaseError(f"duplicate case id {c.id!r}: {cases[c.id].path} and {p}")
        cases[c.id] = c
    return cases


def _hit(case: Case, pattern: str) -> bool:
    return fnmatch.fnmatchcase(case.relpath, pattern) or fnmatch.fnmatchcase(case.id, pattern)


def split_of(case: Case, splits: dict[str, list[str]]) -> str | None:
    names = [name for name, globs in splits.items() if any(_hit(case, g) for g in globs)]
    if len(names) > 1:
        raise CaseError(f"case {case.relpath} is in several splits {names}; splits must not overlap")
    return names[0] if names else None


def select(cases: dict[str, Case], selector: str, splits: dict[str, list[str]] | None = None) -> list[str]:
    """Comma-separated union of split names (project-defined), `all`, and ids / globs over ids or library-relative
    paths (e.g. `geom_textbox/*`). A selector matching nothing is an error."""
    splits = splits or {}
    chosen: list[str] = []
    for tok in [t.strip() for t in selector.split(",") if t.strip()]:
        if tok in splits:
            hits = [c for c, v in cases.items() if any(_hit(v, g) for g in splits[tok])]
        elif tok == "all":
            hits = list(cases)
        else:
            hits = [c for c, v in cases.items() if _hit(v, tok)]
        if not hits:
            raise CaseError(f"selector {tok!r} matches no case")
        chosen += [h for h in hits if h not in chosen]
    if not chosen:
        raise CaseError("empty selection")
    return chosen
