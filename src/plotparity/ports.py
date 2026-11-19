"""Resolve a candidate source: a local path, or `git+URL@<commit-or-tag>` (shallow clone, cached)."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

CACHE = Path(os.environ.get("PLOTPARITY_CACHE", Path.home() / ".cache" / "plotparity")) / "ports"
COMPLETE = "plotparity-complete"     # written inside .git (invisible to `git status`) once a clone is validated


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


def _package_parent(root: Path, package: str) -> Path | None:
    """Where `<package>/__init__.py` lives: root/src, root, or (only if root IS the package dir) root's parent."""
    candidates = [root / "src", root] + ([root.parent] if root.name == package else [])
    return next((p for p in candidates if (p / package / "__init__.py").exists()), None)


def _clone(url: str, rev: str, package: str, dest: Path) -> None:
    """Fetch `rev` into a fresh temp dir, check the package is there, then publish it at `dest` by rename.
    A failed or interrupted fetch never leaves anything at `dest` that a later run would trust."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".tmp-{dest.name}-{uuid.uuid4().hex[:8]}"
    try:
        tmp.mkdir()
        try:
            _git(["init", "-q"], tmp)
            _git(["remote", "add", "origin", url], tmp)
            _git(["fetch", "-q", "--depth", "1", "origin", rev], tmp)
            _git(["checkout", "-q", "FETCH_HEAD"], tmp)
        except subprocess.CalledProcessError as exc:
            raise PortError(f"could not fetch {url}@{rev}: {(exc.stderr or '').strip()[-400:]}") from None
        if _package_parent(tmp, package) is None:
            raise PortError(f"package {package!r} not found in {url}@{rev} (looked in src/{package}, {package}/)")
        (tmp / ".git" / COMPLETE).write_text(_git(["rev-parse", "HEAD"], tmp) + "\n")
        if dest.exists() and not (dest / ".git" / COMPLETE).exists():
            shutil.rmtree(dest, ignore_errors=True)          # an incomplete entry from an older/failed run
        try:
            os.replace(tmp, dest)
        except OSError:
            if not (dest / ".git" / COMPLETE).exists():      # lost a race to a complete entry: fine; else re-raise
                raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def resolve(source: str, package: str, base: Path) -> Port:
    if source.startswith("git+"):
        url, sep, rev = source[4:].rpartition("@")
        if not sep or not url or not rev:
            raise PortError(f"git source must be git+URL@<commit-or-tag>, got {source!r}")
        dest = CACHE / hashlib.sha256(url.encode()).hexdigest()[:16] / rev
        if not (dest / ".git" / COMPLETE).exists():
            _clone(url, rev, package, dest)
        root = dest
    else:
        p = Path(source).expanduser()
        root = (p if p.is_absolute() else base / p).resolve()
        if not root.exists():
            raise PortError(f"candidate source {root} does not exist")
    parent = _package_parent(root, package)
    if parent is None:
        raise PortError(f"package {package!r} not found under {root} (looked in src/{package}, {package}/)")
    commit = dirty = None
    try:
        commit = _git(["rev-parse", "HEAD"], root)
        dirty = bool(_git(["status", "--porcelain"], root))
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return Port(source, root, parent, commit, dirty)
