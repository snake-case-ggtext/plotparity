"""Cases (a folder of case files + stored R references) and Project (a package tested against cases).

    <cases>/cases.toml              adapter, R packages, render size: everything the stored references depend on
    <cases>/<folder>/<id>.md        case files;  <cases>/_references/<id>/ stored R renders;  <cases>/_assets/
    parity.toml (optional)          saved defaults: which cases, which package (`from`), splits, known differences

A Project can be built from a parity.toml or straight from the command line (`plotparity run CASES --from PKG`).
Unknown keys are errors everywhere: a typo must not silently change how things are judged.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cases import Case, CaseError, load_library, select, split_of

CASES_FILES = ("cases.toml", "library.toml")          # library.toml: deprecated name
CASES_SCHEMA = {"adapter", "reference", "render", "library"}
REF_KEYS = {"engine", "packages", "lib"}
RENDER_KEYS = {"width_px", "height_px", "dpi"}
CONFIG_KEYS = {"cases", "from", "package", "requirements", "python", "install", "api", "known_good", "out", "html",
               "default_split", "timeout_s", "splits", "profile", "clean_room"}


class ConfigError(ValueError):
    """Bad configuration (exit code 3)."""


class UsageError(ValueError):
    """The command was used wrongly, e.g. a missing option or an existing loop name (exit code 3)."""


def _resolve(base: Path, rel: str) -> Path:
    p = Path(rel).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


def find_cases_root(start: Path) -> Path | None:
    """Nearest directory at or above `start` that holds a cases.toml (or library.toml)."""
    p = start.resolve()
    if p.is_file():
        p = p.parent
    for d in (p, *p.parents):
        if any((d / f).exists() for f in CASES_FILES):
            return d
    return None


@dataclass
class Library:
    """A folder of case files with its stored R references (`cases.toml` at its root)."""
    root: Path
    cfg: dict[str, Any]

    @classmethod
    def load(cls, root: Path) -> "Library":
        root = Path(root).resolve()
        f = next((root / n for n in CASES_FILES if (root / n).exists()), None)
        if f is None:
            raise ConfigError(f"{root} has no cases.toml (it marks the root of a cases folder; see docs/writing-cases.md)")
        raw = tomllib.loads(f.read_text())
        unknown = set(raw) - CASES_SCHEMA
        if unknown:
            raise ConfigError(f"{f.name}: unknown keys {sorted(unknown)} (allowed: adapter, [reference], [render])")
        adapter = raw.get("adapter") or raw.get("library", {}).get("adapter")
        if not adapter:
            raise ConfigError(f"{f}: `adapter = \"...\"` is required (e.g. \"ggtext\")")
        ref = {"engine": "r", "packages": ["ggplot2"], "lib": "", **raw.get("reference", {})}
        render = {"width_px": 640, "height_px": 480, "dpi": 72, **raw.get("render", {})}
        for name, given, keys in (("reference", ref, REF_KEYS), ("render", render, RENDER_KEYS)):
            if set(given) - keys:
                raise ConfigError(f"{f.name} [{name}]: unknown keys {sorted(set(given) - keys)} (allowed: {sorted(keys)})")
        if ref["engine"] != "r":
            raise ConfigError("only the R reference engine is supported")
        return cls(root, {"library": {"adapter": adapter}, "reference": ref, "render": render})

    @property
    def adapter_name(self) -> str:
        return self.cfg["library"]["adapter"]

    @property
    def references_dir(self) -> Path:
        return self.root / "_references"

    @property
    def assets_dir(self) -> Path:
        return self.root / "_assets"

    @property
    def render(self) -> dict:
        return self.cfg["render"]

    def r_lib(self) -> Path | None:
        return _resolve(self.root, self.cfg["reference"]["lib"]) if self.cfg["reference"]["lib"] else None

    def cases(self) -> dict[str, Case]:
        return load_library(self.root)


def infer_package(root: Path) -> str:
    """The import name of the Python package in a source tree: src/<pkg>/ or <pkg>/ (exactly one)."""
    found = []
    for base in (root / "src", root):
        if base.is_dir():
            found += [d.name for d in base.iterdir()
                      if (d / "__init__.py").exists() and not d.name.startswith((".", "_")) and d.name not in ("tests", "test", "docs")]
        if found:
            break
    if len(found) == 1:
        return found[0]
    if not found:
        raise UsageError(f"no Python package found in {root} (looked for src/<pkg>/ and <pkg>/); "
                         "pass --from PATH or --from git+URL@rev, or --package NAME")
    raise UsageError(f"several packages in {root}: {sorted(found)}; pass --package NAME")


@dataclass
class Project:
    """A package under test + the cases it's tested against. `root` is where parity.toml lives (or the cwd)."""
    root: Path
    cfg: dict[str, Any]
    config_text: str
    library: Library
    config_path: Path | None = None
    case_filter: list[str] | None = field(default=None)   # ids picked by CLI paths/-k; None = whole library

    @classmethod
    def load(cls, config: str | Path) -> "Project":
        """Load from a parity.toml (or a directory containing one)."""
        p = Path(config).resolve()
        if p.is_dir():
            p = p / "parity.toml"
        if not p.exists():
            raise ConfigError(f"{p} not found")
        return cls.build(config_path=p)

    @classmethod
    def build(cls, config_path: Path | None = None, paths: list[str] | None = None, from_: str | None = None,
              package: str | None = None, cwd: Path | None = None) -> "Project":
        cwd = (cwd or Path.cwd()).resolve()
        raw, text = {}, ""
        if config_path:
            text = config_path.read_text()
            raw = tomllib.loads(text)
            bad = set(raw) - CONFIG_KEYS
            if bad:
                raise ConfigError(f"{config_path.name}: unknown keys {sorted(bad)} (allowed: {sorted(CONFIG_KEYS)})")
        root = config_path.parent if config_path else cwd

        # cases: CLI paths > config `cases` > ./parity > ./cases
        targets = [Path(p).resolve() for p in (paths or [])]
        if targets:
            roots = {find_cases_root(t) for t in targets}
            if None in roots:
                raise UsageError(f"no cases.toml at or above {[str(t) for t in targets]}; a cases folder needs one")
            if len(roots) != 1:
                raise UsageError("all case paths must be in the same cases folder")
            lib_root = roots.pop()
        elif raw.get("cases"):
            lib_root = _resolve(root, raw["cases"])
        else:
            lib_root = next((d for d in (root / "parity", root / "cases") if find_cases_root(d) == d), None)
            if lib_root is None:
                raise UsageError("no cases given: pass case folders/files, set `cases` in parity.toml, "
                                 "or put them in ./parity/ (with a cases.toml)")
        lib = Library.load(lib_root)

        source = from_ or raw.get("from") or str(cwd)
        if package is None:
            package = raw.get("package")
        if not package:
            if source.startswith("git+"):
                from .ports import resolve_root
                package = infer_package(resolve_root(source))
            else:
                package = infer_package(_resolve(root if raw.get("from") and not from_ else cwd, source))
        splits = raw.get("splits", {})
        for name, globs in splits.items():
            if name == "all" or not isinstance(globs, list):
                raise ConfigError(f"parity.toml [splits] {name}: must be a list of globs (and not named 'all')")
        if "clean_room" in raw and not isinstance(raw["clean_room"], bool):
            raise ConfigError("parity.toml `clean_room` must be true or false")
        cfg = {
            "project": {"default_select": raw.get("default_split", "all" if not splits else "dev"),
                        "default_split": raw.get("default_split")},
            "splits": splits,
            "candidate": {"engine": "python", "package": package, "source": source,
                          "known_good": raw.get("known_good", ""), "python": raw.get("python", ""),
                          "requirements": raw.get("requirements", []), "install": raw.get("install", True),
                          "api": raw.get("api", [])},
            "render": {"timeout_s": raw.get("timeout_s", 60)},
            "profile": raw.get("profile", {"checks": {}}),
            "out": raw.get("out", ".plotparity/runs"), "html": raw.get("html", True),
            "clean_room": raw.get("clean_room", False),
        }
        proj = cls(root, cfg, text, lib, config_path)
        cases = lib.cases()
        for c in cases.values():
            split_of(c, proj.splits)                      # overlapping splits are an error
        for globs in proj.splits.values():
            for g in globs:
                select(cases, g)                          # a split glob that matches nothing is a typo
        if targets:
            ids = []
            for t in targets:
                hit = [cid for cid, c in cases.items() if c.path == t or t in c.path.parents]
                if not hit:
                    raise UsageError(f"{t} contains no case files")
                ids += [h for h in hit if h not in ids]
            proj.case_filter = ids
        return proj

    def path(self, rel: str) -> Path:
        return _resolve(self.root, rel)

    @property
    def splits(self) -> dict[str, list[str]]:
        return self.cfg["splits"]

    @property
    def package(self) -> str:
        return self.cfg["candidate"]["package"]

    @property
    def adapter_name(self) -> str:
        return self.library.adapter_name

    @property
    def clean_room(self) -> bool:
        return bool(self.cfg.get("clean_room", False))

    @property
    def runs_dir(self) -> Path:
        return _resolve(self.root, self.cfg.get("out", ".plotparity/runs"))

    @property
    def canvas_pt(self) -> tuple[float, float]:
        """Canvas size in points (1/72 in) from the cases folder's [render] config."""
        r = self.library.render
        return (r["width_px"] * 72.0 / r["dpi"], r["height_px"] * 72.0 / r["dpi"])

    @property
    def render_cfg(self) -> dict:
        return {**self.library.render, "timeout_s": self.cfg["render"]["timeout_s"]}

    def known_good_root(self) -> Path | None:
        """Repository root of the known-good package (a path or a pinned git URL, resolved like `from`)."""
        kg = self.cfg["candidate"]["known_good"]
        if not kg:
            return None
        from .ports import resolve
        return resolve(kg, self.package, self.root).root

    def known_differences_path(self) -> Path:
        return self.root / "known_differences.toml"

    def mutations_dir(self) -> Path:
        return self.root / "mutations"

    def cases(self) -> dict[str, Case]:
        return self.library.cases()

    def split_of(self, case: Case) -> str | None:
        return split_of(case, self.splits)

    def select(self, selector: str | None) -> list[str]:
        ids = select(self.cases(), selector or self.cfg["project"]["default_select"], self.splits)
        if self.case_filter is not None:
            ids = [i for i in ids if i in self.case_filter]
        return ids

    def pick(self, k: str | None = None, split: str | None = None) -> list[str]:
        """Case ids for a CLI run: path filter (if any), then --split (or parity.toml's default_split), then -k."""
        split = split or (self.cfg["project"].get("default_split") if self.case_filter is None else None)
        if split:
            if not self.splits:
                raise UsageError("--split needs [splits] in a parity.toml")
            if split not in self.splits:
                raise UsageError(f"unknown split {split!r}; known: {sorted(self.splits)}")
            ids = select(self.cases(), split, self.splits)
        else:
            ids = list(self.cases()) if self.case_filter is None else []
        if self.case_filter is not None:
            ids = [i for i in (ids or self.case_filter) if i in self.case_filter]
        if k:
            ids = [i for i in ids if match_k(k, i)]
        if not ids:
            raise UsageError("no cases selected")
        return ids


def match_k(expr: str, name: str) -> bool:
    """pytest-like -k: substrings joined by `or`, each optionally prefixed by `not`; `and` binds tighter."""
    for alt in re.split(r"\s+or\s+", expr.strip()):
        ok = True
        for term in re.split(r"\s+and\s+", alt):
            neg = term.startswith("not ")
            t = term[4:].strip() if neg else term.strip()
            ok &= (t not in name) if neg else (t in name)
        if ok:
            return True
    return False


__all__ = ["Library", "Project", "ConfigError", "UsageError", "CaseError", "infer_package", "find_cases_root"]
