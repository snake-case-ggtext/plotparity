"""Library (shared cases + R references) and Project (one port evaluated against a library).

    cases/<lib>/library.toml        adapter, R reference engine/packages/lib, render size (what references depend on)
    cases/<lib>/<folder>/<id>.md    cases;  cases/<lib>/_references/<id>/  stored R renders;  cases/<lib>/_assets/
    projects/<name>/parity.toml     which library, which port, splits, profile overrides, known differences

Unknown keys are errors everywhere (a typo must not silently change judging).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cases import Case, CaseError, load_library, select, split_of

LIB_SCHEMA = {"library": {"adapter"}, "reference": {"engine", "packages", "lib"}, "render": {"width_px", "height_px", "dpi"}}
LIB_DEFAULTS = {"reference": {"engine": "r", "packages": ["ggplot2"], "lib": ""},
                "render": {"width_px": 640, "height_px": 480, "dpi": 72}}
PROJ_SCHEMA = {
    "project": {"default_select"},
    "cases": {"library"},
    "splits": None,                       # any split name -> list of globs
    "candidate": {"engine", "package", "source", "known_good", "python", "requirements", "api"},
    "render": {"timeout_s"},
    "profile": {"checks"},
}
PROJ_DEFAULTS = {"project": {"default_select": "dev"}, "splits": {},
                 "candidate": {"engine": "python", "known_good": "", "python": "", "requirements": [], "api": []},
                 "render": {"timeout_s": 60}, "profile": {"checks": {}}}


class ConfigError(ValueError):
    """Bad configuration (exit code 3)."""


class UsageError(ValueError):
    """The command was used wrongly, e.g. a missing option or an existing loop name (exit code 3)."""


def _load_toml(f: Path, schema: dict, defaults: dict) -> tuple[dict, str]:
    if not f.exists():
        raise ConfigError(f"{f} not found")
    text = f.read_text()
    raw = tomllib.loads(text)
    unknown = set(raw) - set(schema)
    if unknown:
        raise ConfigError(f"{f.name}: unknown tables {sorted(unknown)}")
    cfg = {}
    for table, keys in schema.items():
        given = raw.get(table, {})
        if keys is not None and set(given) - keys:
            raise ConfigError(f"{f.name} [{table}]: unknown keys {sorted(set(given) - keys)} (allowed: {sorted(keys)})")
        cfg[table] = {**defaults.get(table, {}), **given}
    return cfg, text


def _resolve(base: Path, rel: str) -> Path:
    p = Path(rel).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


@dataclass
class Library:
    root: Path
    cfg: dict[str, Any]

    @classmethod
    def load(cls, root: Path) -> "Library":
        root = Path(root).resolve()
        cfg, _ = _load_toml(root / "library.toml", LIB_SCHEMA, LIB_DEFAULTS)
        if not cfg["library"].get("adapter"):
            raise ConfigError(f"{root}/library.toml: [library] adapter is required")
        if cfg["reference"]["engine"] != "r":
            raise ConfigError("v0.1 supports reference engine 'r' only")
        return cls(root, cfg)

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


@dataclass
class Project:
    root: Path
    cfg: dict[str, Any]
    config_text: str
    library: Library

    @classmethod
    def load(cls, root: str | Path) -> "Project":
        root = Path(root).resolve()
        cfg, text = _load_toml(root / "parity.toml", PROJ_SCHEMA, PROJ_DEFAULTS)
        for req in (("cases", "library"), ("candidate", "package"), ("candidate", "source")):
            if not cfg[req[0]].get(req[1]):
                raise ConfigError(f"parity.toml: [{req[0]}] {req[1]} is required")
        if cfg["candidate"]["engine"] != "python":
            raise ConfigError("v0.1 supports candidate engine 'python' only")
        for name, globs in cfg["splits"].items():
            if name == "all" or not isinstance(globs, list):
                raise ConfigError(f"parity.toml [splits] {name}: must be a list of globs (and not named 'all')")
        lib = Library.load(_resolve(root, cfg["cases"]["library"]))
        proj = cls(root, cfg, text, lib)
        cases = lib.cases()
        for c in cases.values():
            split_of(c, proj.splits)                      # overlapping splits are an error
        for globs in proj.splits.values():
            for g in globs:
                select(cases, g)                          # raises if a split glob matches nothing (typo)
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
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def canvas_pt(self) -> tuple[float, float]:
        """Canvas size in points (1/72 in) from the library's [render] config."""
        r = self.library.render
        return (r["width_px"] * 72.0 / r["dpi"], r["height_px"] * 72.0 / r["dpi"])

    @property
    def render_cfg(self) -> dict:
        return {**self.library.render, "timeout_s": self.cfg["render"]["timeout_s"]}

    def known_good_root(self) -> Path | None:
        """Repository root of the known-good port (a path or a pinned git URL, resolved like `source`)."""
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
        return select(self.cases(), selector or self.cfg["project"]["default_select"], self.splits)


__all__ = ["Library", "Project", "ConfigError", "UsageError", "CaseError"]
