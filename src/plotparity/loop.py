"""Iteration recorder for an agent porting loop. The log is written by the tool, not the agent.

loop init     --name N [--source S] [--goal G]   record harness hash + baseline evaluation (dev)
loop iterate  --name N --note "what changed"     snapshot + diff the port package, evaluate dev with --previous
loop holdout  --name N                           evaluate holdout; prints pass/fail counts only
loop finish   --name N --summary "outcome"
An iteration or holdout run FAILS (harness_changed) if anything that decides judging changed since init.
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
import shutil
from pathlib import Path

from . import ports
from .engines.python import engine as py_engine
from .project import UsageError
from .runner import candidate_python, harness_hash, run

MAX_FILE = 2_000_000


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _snapshot(pkg_dir: Path, dest: Path) -> list[str]:
    skipped = []
    for p in pkg_dir.rglob("*"):
        if p.is_dir() or "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        if p.stat().st_size > MAX_FILE:
            skipped.append(str(p.relative_to(pkg_dir)))
            continue
        t = dest / p.relative_to(pkg_dir)
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, t)
    return skipped


def _diff(a: Path, b: Path) -> str:
    out = []
    rels = sorted({p.relative_to(a) for p in a.rglob("*") if p.is_file()} | {p.relative_to(b) for p in b.rglob("*") if p.is_file()})
    for rel in rels:
        fa, fb = a / rel, b / rel
        try:
            la = fa.read_text().splitlines(keepends=True) if fa.exists() else []
            lb = fb.read_text().splitlines(keepends=True) if fb.exists() else []
        except UnicodeDecodeError:
            continue
        out += difflib.unified_diff(la, lb, f"a/{rel}", f"b/{rel}")
    return "".join(out)


def _summary(rep: dict) -> dict:
    by = {}
    for c in rep["cases"]:
        for f in c["findings"]:
            if f["status"] == "fail":
                by[f["check"]] = by.get(f["check"], 0) + 1
    return {"status": rep["status"], "n_findings": sum(by.values()), "findings_by_check": by,
            "cases": {c["case"]: c["status"] for c in rep["cases"]}}


def dispatch(project, a) -> int:
    d = project.runs_dir / "loops" / a.name
    meta_f = d / "meta.json"
    if a.action == "init":
        if d.exists():
            raise UsageError(f"loop {a.name!r} already exists at {d}")
        source = a.source or project.cfg["candidate"]["source"]
        port = ports.resolve(source, project.cfg["candidate"]["package"], project.root)
        env = py_engine.environment(candidate_python(project))
        d.mkdir(parents=True)
        meta = {"name": a.name, "goal": a.goal, "source": str(port.root), "package_dir": str(port.package_parent / project.cfg["candidate"]["package"]),
                "created": _now(), "harness": harness_hash(project, env), "finished": None, "summary": None}
        meta_f.write_text(json.dumps(meta, indent=2))
        return _iterate(project, d, meta, "baseline (starting point)")
    if not meta_f.exists():
        raise UsageError(f"no loop {a.name!r} at {d}; start one with `loop init`")
    meta = json.loads(meta_f.read_text())
    if a.action == "iterate":
        if not a.note:
            raise UsageError("loop iterate needs --note describing the change")
        return _iterate(project, d, meta, a.note)
    if a.action == "holdout":
        k = len(list(d.glob("holdout_*"))) + 1
        rep, md, code = run(project, "holdout", d / f"holdout_{k:02d}", source=meta["source"], show_findings=False)
        changed = _harness_changed(meta, rep)
        line = {"holdout": k, "ts": _now(), "harness_changed": changed, **_summary(rep)}
        if changed:
            code, line["status"] = 1, "fail"
            md = _harness_message(changed) + md
        with open(d / "loop.jsonl", "a") as fh:
            fh.write(json.dumps(line) + "\n")
        print(md)
        print("(holdout findings are stored for humans; this is trust-based: please don't read them)")
        return code
    if a.action == "finish":
        meta.update(finished=_now(), summary=a.summary)
        meta_f.write_text(json.dumps(meta, indent=2))
        iters = sorted(p for p in d.glob("iter_*") if (p / "report.json").exists())
        holdouts = sorted(p for p in d.glob("holdout_*") if (p / "report.json").exists())
        runs = iters + holdouts
        print(f"loop {a.name} finished")
        if runs:
            no_html = getattr(a, "no_html", False) or not project.cfg.get("html", True)
            if not no_html:
                from .view.build import build
                out_html = build([r / "report.json" for r in runs], d / "report.html")
                print(f"report: {out_html}")
            picked = runs if len(runs) <= 4 else [iters[0], iters[-1], *holdouts[-1:]] if iters else runs[-2:]
            print("view:   plotparity view " + " ".join(str(p) for p in picked))
        return 0
    raise ValueError(a.action)


def _harness_changed(meta: dict, rep: dict) -> list[str]:
    return sorted(k for k, v in rep["harness"].items() if k != "all" and meta["harness"].get(k) != v)


def _harness_message(changed: list[str]) -> str:
    return (f"HARNESS CHANGED since loop init ({', '.join(changed)}): this run FAILS. The harness (tool, parity.toml, "
            "profile, cases, references, known differences, plotnine/matplotlib) must not be edited during a loop.\n\n")


def _iterate(project, d: Path, meta: dict, note: str) -> int:
    iters = sorted(p for p in d.glob("iter_*") if p.is_dir())
    n = len(iters)
    it = d / f"iter_{n:03d}"
    skipped = _snapshot(Path(meta["package_dir"]), it / "src")
    diff = _diff(iters[-1] / "src", it / "src") if iters else ""
    (it / "changes.diff").write_text(diff)
    prev = iters[-1] / "report.json" if iters else None
    rep, md, code = run(project, "dev", it, source=meta["source"], previous=prev)
    changed = _harness_changed(meta, rep)
    if changed:
        code = 1
        md = _harness_message(changed) + md
        (it / "report.md").write_text(md)
    adds = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    rems = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
    line = {"iter": n, "ts": _now(), "note": note, "harness_changed": changed, "skipped_files": skipped,
            "diff": {"added": adds, "removed": rems}, **_summary(rep)}
    if changed:
        line["status"] = "fail"
    with open(d / "loop.jsonl", "a") as fh:
        fh.write(json.dumps(line) + "\n")
    print(f"iteration {n} recorded in {it}\n")
    print(md)
    return code
