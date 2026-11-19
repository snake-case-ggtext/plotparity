"""Parent side of the Python engine: write a job, run worker.py in the candidate interpreter, collect results."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ...cases import Case
from ...ports import Port

SRC = Path(__file__).resolve().parents[3]          # plotparity/src


def interpreter(cfg_python: str, base: Path, requirements: list[str] | None = None) -> str:
    """The candidate's interpreter: an explicit path, a cached env built from `requirements`, or our own."""
    if cfg_python:
        p = Path(cfg_python).expanduser()
        return str(p if p.is_absolute() else (base / p))
    if requirements:
        return managed_env(requirements)
    return sys.executable


def managed_env(requirements: list[str]) -> str:
    """A uv-built venv for ports with their own dependencies, cached by the hash of its requirements.

    plotnine / matplotlib / pandas / pyyaml are pinned to the versions plotparity itself runs with, so the
    candidate draws with the same matplotlib (and the same DejaVu TTFs) the references were checked against.
    Readers trust an env only once `.complete` exists; it is written after the env is built and imports work.
    Builders hold an exclusive lock, so concurrent runs never delete or half-build each other's env.
    """
    import fcntl
    import hashlib
    from importlib.metadata import version

    pins = [f"{p}=={version(p)}" for p in ("plotnine", "matplotlib", "pandas", "pyyaml", "numpy")]
    reqs = sorted(set(pins) | set(requirements))
    py = f"{sys.version_info.major}.{sys.version_info.minor}"
    key = hashlib.sha256("\n".join([py, *reqs]).encode()).hexdigest()[:16]
    env_dir = Path(os.environ.get("PLOTPARITY_CACHE", Path.home() / ".cache" / "plotparity")) / "envs" / key
    exe = env_dir / "bin" / "python"
    if exe.exists() and (env_dir / ".complete").exists():
        return str(exe)
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("[candidate] requirements needs `uv` on PATH (https://docs.astral.sh/uv/)")
    env_dir.parent.mkdir(parents=True, exist_ok=True)
    with open(env_dir.parent / f"{key}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if exe.exists() and (env_dir / ".complete").exists():      # another run finished it while we waited
            return str(exe)
        shutil.rmtree(env_dir, ignore_errors=True)                   # incomplete leftovers are never reused
        subprocess.run([uv, "venv", "-q", "--python", py, str(env_dir)], check=True)
        subprocess.run([uv, "pip", "install", "-q", "--python", str(exe), *reqs], check=True)
        subprocess.run([str(exe), "-c", "import plotnine, matplotlib, pandas, numpy, yaml"], check=True)
        (env_dir / ".complete").write_text("\n".join(reqs) + "\n")
    return str(exe)


def environment(python: str, port: Port | None = None) -> dict:
    env = dict(os.environ, PYTHONPATH=str(SRC))
    code = "import json; from plotparity.engines.python.worker import environment; print(json.dumps(environment()))"
    out = subprocess.run([python, "-c", code], env=env, capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def render(cases: list[Case], port: Port, package: str, api: list[str], render_cfg: dict, assets_dir: Path,
           out_dir: Path, python: str, isolate: bool = False) -> dict:
    """Render cases that have a python block for `package` (or a default block); callers skip the rest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for c in cases:
        shutil.rmtree(out_dir / c.id, ignore_errors=True)   # never reuse a previous run's outputs
    if not cases:
        return {"log": [], "environment": environment(python)}
    groups = [[c] for c in cases] if isolate else [cases]
    env = dict(os.environ, PYTHONPATH=f"{port.package_parent}{os.pathsep}{SRC}", MPLBACKEND="Agg")
    log = []
    for group in groups:
        job = {"out_dir": str(out_dir), "package": package, "port_dir": str(port.package_parent / package),
               "api": api, "render": render_cfg,
               "cases": [{"id": c.id, "file": str(c.path), "code": c.python_for(package), "data": c.table(assets_dir)}
                         for c in group]}
        job_file = out_dir / f"job_{group[0].id}.json"
        job_file.write_text(json.dumps(job))
        timeout = render_cfg["timeout_s"] * len(group)
        try:
            p = subprocess.run([python, "-m", "plotparity.engines.python.worker", str(job_file)], env=env,
                               capture_output=True, text=True, timeout=timeout)
            log.append({"cases": [c.id for c in group], "returncode": p.returncode, "stderr": p.stderr[-3000:]})
        except subprocess.TimeoutExpired:
            log.append({"cases": [c.id for c in group], "returncode": None, "stderr": f"timeout after {timeout}s"})
        for c in group:
            cdir = out_dir / c.id
            if not (cdir / "observations.json").exists() and not (cdir / "error.json").exists():
                cdir.mkdir(parents=True, exist_ok=True)
                tail = log[-1]["stderr"]
                kind = "render_timeout" if log[-1]["returncode"] is None else "render_error"
                (cdir / "error.json").write_text(json.dumps({
                    "kind": kind, "stage": "draw", "type": "WorkerDied",
                    "message": f"worker produced no output for this case ({'timeout' if kind == 'render_timeout' else 'crash'})",
                    "traceback": tail}))
    worker_env = out_dir / "worker_env.json"
    return {"log": log, "environment": json.loads(worker_env.read_text()) if worker_env.exists() else {}}
