"""plotparity CLI. Exit codes: 0 pass, 1 fail, 2 unmeasured/infrastructure, 3 usage/config error."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from .cases import CaseError
from .compare import ProfileError
from .ports import PortError
from .project import ConfigError, Project, UsageError


def _out(project: Project, given: str | None, kind: str) -> Path:
    if given:
        return Path(given).resolve()
    return project.runs_dir / f"{kind}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"


def cmd_run(a) -> int:
    from .runner import run
    project = Project.load(a.project)
    out = _out(project, a.out, "run")
    _, md, code = run(project, a.select, out, source=a.source, previous=a.previous, isolate=a.isolate)
    print(md)
    print(f"(report: {out}/report.md)")
    return code


def _lib_and_python(path: str):
    """A library dir (library.toml) or a project dir (parity.toml → its library + its candidate interpreter)."""
    from .engines.python import engine as py_engine
    from .project import Library
    from .runner import candidate_python
    p = Path(path).resolve()
    if (p / "library.toml").exists():
        return Library.load(p), py_engine.interpreter("", p)
    project = Project.load(p)
    return project.library, candidate_python(project)


def cmd_references(a) -> int:
    from . import adapters, references
    from .cases import select
    lib, python = _lib_and_python(a.path)
    adapter = adapters.load(lib.adapter_name)
    cases = lib.cases()
    ids = select(cases, a.select or "all")
    todo = [cases[i] for i in ids
            if a.force or references.load(lib, adapter, cases[i], {}).get("error")]
    if not todo:
        print("all selected references are fresh (use --force to re-render)")
        return 0
    res = references.render(lib, adapter, todo, python)
    print(res["stdout"])
    return 0


def cmd_validate(a) -> int:
    from .validate import validate
    return validate(Project.load(a.project), Path(a.out) if a.out else None)


def cmd_selftest(a) -> int:
    from .selftest import selftest
    return selftest(Project.load(a.project), isolate_check=not a.skip_isolate_check)


def cmd_loop(a) -> int:
    from . import loop
    return loop.dispatch(Project.load(a.project), a)


def cmd_new_case(a) -> int:
    from .scaffold import new_case
    from .project import Library
    print(new_case(Library.load(Path(a.library)), a.relpath, a.package))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="plotparity", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="render the candidate and compare against stored references")
    p.add_argument("project"); p.add_argument("--select"); p.add_argument("--source", help="override candidate source")
    p.add_argument("--out"); p.add_argument("--previous", help="previous report.json for new/fixed tagging")
    p.add_argument("--isolate", action="store_true", help="one process per case")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("references", help="render a library's R references (stale or missing ones by default)")
    p.add_argument("path", help="library dir (library.toml) or project dir")
    p.add_argument("--select"); p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_references)
    p = sub.add_parser("validate", help="case hygiene + reference freshness (+ known_good port checks)")
    p.add_argument("project"); p.add_argument("--out"); p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("selftest", help="mutation suite against the known_good port")
    p.add_argument("project"); p.add_argument("--skip-isolate-check", action="store_true"); p.set_defaults(fn=cmd_selftest)
    p = sub.add_parser("loop", help="iteration recorder for an agent porting loop")
    p.add_argument("action", choices=["init", "iterate", "holdout", "finish"]); p.add_argument("project")
    p.add_argument("--name", required=True); p.add_argument("--note", default=""); p.add_argument("--goal", default="")
    p.add_argument("--summary", default=""); p.add_argument("--source", help="candidate source (init only)")
    p.set_defaults(fn=cmd_loop)
    p = sub.add_parser("new-case", help="scaffold a case file in a library")
    p.add_argument("library"); p.add_argument("relpath", help="<feature-folder>/<id>, e.g. geom_richtext/my_case")
    p.add_argument("--package", default="", help="tag the python block for this port package ('' = default block)")
    p.set_defaults(fn=cmd_new_case)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (ConfigError, CaseError, ProfileError, PortError, UsageError) as exc:
        print(f"plotparity: error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
