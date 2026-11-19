"""Resolve a candidate source: a local path, or `git+URL@<commit-or-tag>` (shallow clone, cached)."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
import os
from pathlib import Path

CACHE = Path(os.environ.get("PLOTPARITY_CACHE", Path.home() / ".cache" / "plotparity")) / "ports"


@dataclass(frozen=True)
class Port:
    source: str            # as given
    root: Path             # repository / source root
    package_parent: Path   # directory to put on PYTHONPATH (contains <package>/)
    commit: str | None
    dirty: bool | None


class PortError(ValueError):
    pass


def _git(args: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def resolve(source: str, package: str, base: Path) -> Port:
    if source.startswith("git+"):
        url, sep, rev = source[4:].rpartition("@")
        if not sep or not url or not rev:
            raise PortError(f"git source must be git+URL@<commit-or-tag>, got {source!r}")
        dest = CACHE / hashlib.sha256(url.encode()).hexdigest()[:16] / rev
        if not (dest / ".git").exists():
            dest.mkdir(parents=True, exist_ok=True)
            _git(["init", "-q"], dest)
            _git(["remote", "add", "origin", url], dest)
            _git(["fetch", "-q", "--depth", "1", "origin", rev], dest)
            _git(["checkout", "-q", "FETCH_HEAD"], dest)
        root = dest
    else:
        p = Path(source).expanduser()
        root = (p if p.is_absolute() else base / p).resolve()
        if not root.exists():
            raise PortError(f"candidate source {root} does not exist")
    for parent in (root / "src", root, root.parent):
        if (parent / package / "__init__.py").exists():
            break
    else:
        raise PortError(f"package {package!r} not found under {root} (looked in src/{package}, {package}/)")
    commit = dirty = None
    try:
        commit = _git(["rev-parse", "HEAD"], root)
        dirty = bool(_git(["status", "--porcelain"], root))
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return Port(source, root, parent, commit, dirty)
