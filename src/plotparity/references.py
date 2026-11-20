"""Stored reference renders, owned by the case LIBRARY: <library>/_references/<case>/{reference.png,
observations.json, meta.json}. They depend only on the R side (case R block + data, `_assets/` contents, library
render/reference config, R scripts, adapter R probe), so every project/port evaluated against the library shares them.

meta.json records an INPUT HASH over everything that affects the reference render. `run` refuses a reference
whose inputs changed (stale) or whose fonts differ from the candidate's (font drift): infrastructure errors.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from .cases import Case
from .engines.python import engine as py_engine

RENDER_R = Path(__file__).resolve().parent / "engines" / "r" / "render.R"
PANEL_GEOM_R = Path(__file__).resolve().parent / "engines" / "r" / "panel_geometry.R"
R_TIMEOUT_S = 120          # per case: a hung R graphics device must not stall CI


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assets_hash(lib) -> str | None:
    """Content hash of the whole `_assets/` folder (names + bytes; dotfiles such as .DS_Store skipped).
    Conservative: any asset edit makes every reference stale, since a case's {assets} paths may reach any file."""
    d = lib.assets_dir
    if not d.is_dir():
        return None
    m = hashlib.sha256()
    for p in sorted(d.rglob("*")):
        rel = p.relative_to(d)
        if p.is_file() and not any(part.startswith(".") for part in rel.parts):
            m.update(rel.as_posix().encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest())
    return m.hexdigest()


def input_hash(case: Case, lib, adapter) -> str:
    df = case.data_file_path(lib.assets_dir)
    parts = {
        "case": case.reference_fingerprint(),
        "assets": assets_hash(lib),
        "render": lib.render,
        # not `lib`: where the R library lives is irrelevant (package versions are recorded in meta.json)
        "reference": {k: lib.cfg["reference"][k] for k in ("engine", "packages")},
        "render.R": _sha(RENDER_R),
        "panel_geometry.R": _sha(PANEL_GEOM_R),
        "observe.R": _sha(Path(adapter.OBSERVE_R)),
        "adapter": [adapter.NAME, adapter.VERSION],
    }
    if df is not None:
        parts["data_file_sha"] = _sha(df)
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def render(lib, adapter, cases: list[Case], python: str, out_dir: Path | None = None) -> dict:
    """Render reference PNG + observations with R. Writes meta.json with the input hash and font hashes."""
    out_dir = out_dir or lib.references_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    env_py = py_engine.environment(python)
    for c in cases:
        shutil.rmtree(out_dir / c.id, ignore_errors=True)
    job = {"out_dir": str(out_dir), "packages": lib.cfg["reference"]["packages"], "ttf_dir": env_py["ttf_dir"],
           "observe_r": str(adapter.OBSERVE_R), **lib.render,
           "cases": [{"id": c.id, "code": c.r_code, "data": c.table(lib.assets_dir) or {}} for c in cases]}
    job_file = out_dir / "_job.json"
    job_file.write_text(json.dumps(job))
    env = dict(os.environ, LC_COLLATE="C")
    if lib.r_lib():
        env["R_LIBS_USER"] = str(lib.r_lib())
    timeout = R_TIMEOUT_S * max(1, len(cases))
    try:
        p = subprocess.run(["Rscript", str(RENDER_R), str(job_file)], env=env, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Rscript timed out after {timeout}s ({len(cases)} case(s))") from exc
    finally:
        job_file.unlink(missing_ok=True)
    if p.returncode != 0:
        raise RuntimeError(f"Rscript failed ({p.returncode}):\n{p.stderr[-3000:]}")
    r_env = json.loads((out_dir / "r_env.json").read_text())
    for c in cases:
        meta = {"case": c.id, "input_hash": input_hash(c, lib, adapter), "font_hashes": env_py["font_hashes"],
                "r_env": r_env, "adapter": [adapter.NAME, adapter.VERSION]}
        (out_dir / c.id / "meta.json").write_text(json.dumps(meta, indent=2))
    return {"stdout": p.stdout, "r_env": r_env}


def load(lib, adapter, case: Case, candidate_fonts: dict) -> dict:
    """{"observations": ..., "png": path} or {"error": {...}} (missing / stale / font drift / R error)."""
    d = lib.references_dir / case.id
    if (d / "error.json").exists():
        e = json.loads((d / "error.json").read_text())
        return {"error": {"kind": "reference_error", "message": f"R failed on this case: {e.get('message')}"}}
    if not (d / "meta.json").exists() or not (d / "observations.json").exists():
        return {"error": {"kind": "reference_error", "message": f"no reference for {case.id}; run `plotparity references`"}}
    meta = json.loads((d / "meta.json").read_text())
    if meta["input_hash"] != input_hash(case, lib, adapter):
        return {"error": {"kind": "stale_reference",
                          "message": f"reference for {case.id} is stale (case R block/data/assets/render config/R scripts/"
                                     "adapter changed since it was rendered); run `plotparity references -k "
                                     + case.id + "`"}}
    if candidate_fonts and meta["font_hashes"] != candidate_fonts:
        return {"error": {"kind": "font_drift",
                          "message": "candidate interpreter's DejaVu TTFs differ from those used for the reference "
                                     f"(reference {meta['font_hashes']}, candidate {candidate_fonts}); re-render references"}}
    return {"observations": json.loads((d / "observations.json").read_text()), "png": str(d / "reference.png")}
