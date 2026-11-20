"""`plotparity validate <project>`: library hygiene + this project's splits against its known_good port.

Library-level (shared): parsing, adapter expect keys, references fresh, reference component counts.
Project-level: every split case has a python block for this port (else it's `needs_translation`), and with a
known_good port: dev/holdout cases must pass; other selected cases may fail only with port findings.
"""

from __future__ import annotations

import datetime as dt
import shutil
import tempfile
from pathlib import Path

from . import adapters, references
from .compare import load_known
from .engines.python import engine as py_engine
from .runner import candidate_python, run


def validate(project, out: Path | None = None, k: str | None = None) -> int:
    from .project import UsageError, match_k

    problems: list[str] = []
    notes: list[str] = []
    lib = project.library
    adapter = adapters.load(lib.adapter_name)
    cases = lib.cases()                                       # parsing errors raise (exit 3)
    if k:
        cases = {cid: c for cid, c in cases.items() if match_k(k, cid)}
        if not cases:
            raise UsageError(f"no cases matched -k {k!r}")
    env = py_engine.environment(candidate_python(project))
    for cid, c in cases.items():
        bad = set(c.expect) - {"components", "measurable"} - set(adapter.EXPECT_KEYS)
        if bad:
            problems.append(f"{c.relpath}: unknown expect keys {sorted(bad)}")
        ref = references.load(lib, adapter, c, env["font_hashes"])
        if "error" in ref:
            problems.append(f"{c.relpath}: {ref['error']['kind']}: {ref['error']['message']}")
            continue
        comps, _ = adapter.components("r", ref["observations"], c.expect, project.canvas_pt)
        if c.measurable and not comps:
            problems.append(f"{c.relpath}: measurable case but the reference has 0 components")
        if c.measurable and c.n_components is not None and len(comps) != c.n_components:
            problems.append(f"{c.relpath}: reference has {len(comps)} components, case expects {c.n_components}")
    in_splits = [c for c in cases.values() if project.split_of(c)]
    untranslated = [c.relpath for c in in_splits if c.python_for(project.package) is None]
    if untranslated:
        notes.append(f"{len(untranslated)} split case(s) have no python block for {project.package} "
                     f"(they report needs_translation): {untranslated}")
    kg = project.known_good_root()
    if kg and not problems:
        out = out or project.runs_dir / f"validate_{dt.datetime.now():%Y%m%d_%H%M%S}"
        with tempfile.TemporaryDirectory() as td:
            port = Path(td) / "kg"
            shutil.copytree(kg, port, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".git"))
            sel = ",".join(project.splits) or "all"
            rep, _, _ = run(project, sel, out, source=str(port), ids=list(cases) if k else None)
        for c in rep["cases"]:
            fails = sorted({f["check"] for f in c["findings"] if f["status"] in ("fail", "unmeasured")})
            if c["split"] in ("dev", "holdout") and c["status"] != "pass":
                problems.append(f"{c['relpath']}: known_good port does not pass this {c['split']} case ({c['status']}: {fails})")
            infra = [f for f in c["findings"] if f["status"] == "infrastructure_error"]
            if infra:
                problems.append(f"{c['relpath']}: infrastructure error, not a port finding: "
                                f"{infra[0]['check']}: {infra[0]['message'][:140]}")
        for k in rep["known_differences_unused"]:
            problems.append(f"known difference never matched (stale?): {k['case']} / {k['check']}")
    elif not kg:
        notes.append("no [candidate] known_good: dev/holdout cases are UNVALIDATED against a working port")
    for k in load_known(project.known_differences_path()):
        if k["case"] not in cases:
            problems.append(f"known difference names unknown case {k['case']!r}")
    for n in notes:
        print("NOTE", n)
    for p in problems:
        print("PROBLEM", p)
    print(f"{len(cases)} cases, {len(in_splits)} in splits. VALIDATE",
          "PASSED" if not problems else f"FAILED ({len(problems)} problems)")
    return 0 if not problems else 1
