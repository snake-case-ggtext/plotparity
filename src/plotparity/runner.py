"""One evaluation run: resolve candidate, render it, load references, compare, report."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from . import adapters, ports, references
from . import report as rpt
from .compare import evaluate_case, load_known, load_profile, unused_known
from .engines.python import engine as py_engine

TOOL_SRC = Path(__file__).resolve().parent


def candidate_python(project) -> str:
    c = project.cfg["candidate"]
    return py_engine.interpreter(c["python"], project.root, c["requirements"])


def harness_hash(project, env: dict | None = None) -> dict:
    """Hash of everything that decides HOW a candidate is judged. A loop fails an iteration if it changes."""
    def h(paths):
        m = hashlib.sha256()
        for p in sorted(paths):
            m.update(str(p.relative_to(p.anchor)).encode())
            m.update(p.read_bytes())
        return m.hexdigest()[:16]

    parts = {
        "tool": h([p for p in TOOL_SRC.rglob("*") if p.is_file() and p.suffix in (".py", ".R", ".toml")]),
        "parity.toml": hashlib.sha256(project.config_text.encode()).hexdigest()[:16],
        "known_differences": h([project.known_differences_path()]) if project.known_differences_path().exists() else None,
        "library": h([p for p in project.library.root.rglob("*") if p.is_file() and "_references" not in p.parts]),
        "references": h([p for p in project.library.references_dir.rglob("*") if p.is_file()])
        if project.library.references_dir.exists() else None,
        "plotnine": (env or {}).get("plotnine"), "matplotlib": (env or {}).get("matplotlib"),
    }
    parts["all"] = hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:16]
    return parts


def run(project, selector: str | None, out: Path, source: str | None = None, previous: Path | None = None,
        isolate: bool = False, show_findings: bool = True) -> tuple[dict, str, int]:
    adapter = adapters.load(project.adapter_name)
    lib = project.library
    profile = load_profile(adapter, project.cfg["profile"].get("checks", {}))
    known = load_known(project.known_differences_path())
    all_cases = project.cases()
    ids = project.select(selector)
    cases = [all_cases[i] for i in ids]
    cand_cfg = project.cfg["candidate"]
    port = ports.resolve(source or cand_cfg["source"], cand_cfg["package"], project.root)
    python = candidate_python(project)
    render_dir = out / "render"
    translated = [c for c in cases if c.python_for(project.package) is not None]
    info = py_engine.render(translated, port, cand_cfg["package"], cand_cfg["api"], project.render_cfg,
                            lib.assets_dir, render_dir, python, isolate=isolate)
    env = info["environment"]
    results = []
    for c in cases:
        ref = references.load(lib, adapter, c, env.get("font_hashes", {}))
        cdir = render_dir / c.id
        if c.python_for(project.package) is None:
            cand = {"error": {"kind": "needs_translation",
                              "message": f"no ```python {project.package} block (and no default ```python block) in "
                                         f"{c.relpath}.md: translate the R block for this port"}}
        elif (cdir / "error.json").exists():
            cand = {"error": json.loads((cdir / "error.json").read_text())}
        else:
            cand = {"observations": json.loads((cdir / "observations.json").read_text()),
                    "png": str(cdir / "candidate.png")}
        res = evaluate_case(c, adapter, profile, known, ref, cand, package=project.package, split=project.split_of(c),
                            canvas_pt=project.canvas_pt)
        res["reference_png"] = ref.get("png")
        res["candidate_png"] = str(cdir / "candidate.png") if (cdir / "candidate.png").exists() else None
        if "observations" in cand:
            for w in cand["observations"].get("warnings", []):
                res.setdefault("warnings", []).append(w)
        results.append(res)
    code = rpt.exit_code(results)
    report = {
        "status": {0: "pass", 1: "fail", 2: "unmeasured"}[code],
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "project": str(project.root), "selection": selector or project.cfg["project"]["default_select"],
        "candidate": {"source": port.source, "root": str(port.root), "commit": port.commit, "dirty": port.dirty,
                      "python": python, "environment": env},
        "profile": profile, "harness": harness_hash(project, env),
        "known_differences_unused": unused_known(known, results, ids),
        "render_log": info["log"], "cases": results,
    }
    if previous and Path(previous).exists():
        rpt.tag_against_previous(report, json.loads(Path(previous).read_text()))
    md = rpt.write(report, out, show_findings)
    return report, md, code
