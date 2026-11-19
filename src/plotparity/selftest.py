"""Evaluate the evaluator: inject known defects into a copy of the known_good port and assert they're caught."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

from .runner import run

IGNORE = shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "*.pyc")


def _copy_port(project, dest: Path) -> Path:
    kg = project.known_good_root()
    if kg is None:
        raise FileExistsError("selftest needs [candidate] known_good in parity.toml")
    shutil.copytree(kg, dest, ignore=IGNORE)
    return dest


def selftest(project, isolate_check: bool = True) -> int:
    suite_file = project.mutations_dir() / "suite.toml"
    suite = tomllib.loads(suite_file.read_text())["mutation"] if suite_file.exists() else []
    out_root = project.runs_dir / "selftest"
    shutil.rmtree(out_root, ignore_errors=True)
    rows, ok = [], True
    with tempfile.TemporaryDirectory() as td:
        clean = _copy_port(project, Path(td) / "clean")
        rep, _, _ = run(project, "dev,holdout", out_root / "clean", source=str(clean))
        for c in rep["cases"]:
            good = c["status"] == "pass"
            ok &= good
            rows.append((f"clean:{c['case']}", "pass", c["status"], _fails(c), good))
        if isolate_check:
            rep_iso, _, _ = run(project, "dev,holdout", out_root / "clean_isolated", source=str(clean), isolate=True)
            same = _signature(rep) == _signature(rep_iso)
            ok &= same
            rows.append(("batch == --isolate", "identical", "identical" if same else "DIFFERENT", "-", same))
        for m in suite:
            port = _copy_port(project, Path(td) / m["id"])
            patch = project.mutations_dir() / m["patch"]
            chk = subprocess.run(["git", "apply", "--check", str(patch)], cwd=port, capture_output=True, text=True)
            if chk.returncode != 0:
                ok = False
                rows.append((m["id"], "patch applies", "PATCH FAILED", chk.stderr.strip()[:80], False))
                continue
            subprocess.run(["git", "apply", str(patch)], cwd=port, check=True)
            rep, _, _ = run(project, ",".join(m["cases"]), out_root / m["id"], source=str(port))
            for c in rep["cases"]:
                fails = {f["check"] for f in c["findings"] if f["status"] == "fail" and f["severity"] == "gating"}
                if m.get("must_pass"):
                    good, exp = c["status"] == "pass", "pass"
                else:
                    good, exp = c["status"] == "fail" and bool(fails & set(m["must_fail"])), "fail:" + "|".join(m["must_fail"])
                ok &= good
                rows.append((f"{m['id']}:{c['case']}", exp, c["status"], ",".join(sorted(fails)) or "-", good))
    w = max(len(r[0]) for r in rows)
    lines = [f"{'OK ' if g else 'BAD'} {n:<{w}}  expected {e:<42} got {s:<10} [{p}]" for n, e, s, p, g in rows]
    lines.append("SELFTEST " + ("PASSED" if ok else "FAILED"))
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "selftest.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if ok else 1


def _fails(case: dict) -> str:
    return ",".join(sorted({f["check"] for f in case["findings"] if f["status"] == "fail"})) or "-"


def _signature(rep: dict) -> str:
    return json.dumps([(c["case"], c["status"], sorted((f["check"], f["component"], f["status"]) for f in c["findings"]))
                       for c in rep["cases"]])
