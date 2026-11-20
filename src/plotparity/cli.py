"""plotparity: test a plotnine extension against the R original.

Exit codes: 0 every case matches R · 1 a case differs · 2 something couldn't be measured or run · 3 usage/config error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import webbrowser
from pathlib import Path

from .cases import CaseError
from .compare import ProfileError
from .ports import PortError
from .project import ConfigError, Project, UsageError


# ───────────────────────── project resolution (shared by all commands) ─────────────────────────

def _config_path(a) -> Path | None:
    if getattr(a, "config", None):
        p = Path(a.config).resolve()
        p = p / "parity.toml" if p.is_dir() else p
        if not p.exists():
            raise ConfigError(f"{p} not found")
        return p
    p = Path.cwd() / "parity.toml"
    return p if p.exists() else None


def _project(a) -> Project:
    return Project.build(config_path=_config_path(a), paths=getattr(a, "paths", None) or None,
                         from_=getattr(a, "from_", None), package=getattr(a, "package", None))


def _latest(runs_dir: Path) -> Path | None:
    runs = sorted((p for p in runs_dir.glob("*") if (p / "report.json").exists()), key=lambda p: p.name)
    return runs[-1] if runs else None


def _comparable(runs_dir: Path, report: dict) -> Path | None:
    """Latest earlier run of the same package (name + source) on the same cases."""
    for d in sorted(runs_dir.glob("*"), key=lambda p: p.name, reverse=True):
        f = d / "report.json"
        if not f.exists() or d.name == "latest":
            continue
        try:
            r = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        if (r.get("package") == report["package"] and r.get("candidate", {}).get("source") == report["candidate"]["source"]
                and sorted(r.get("case_ids") or []) == sorted(report["case_ids"])):
            return f
    return None


def _point_latest(runs_dir: Path, run_dir: Path) -> None:
    link = runs_dir / "latest"
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(run_dir.name)
    except OSError:
        pass


def _build_html(run_dirs: list[Path], out: Path, public: bool = False) -> Path:
    from .view.build import build
    return build([d / "report.json" for d in run_dirs], out, public=public)


# ───────────────────────── commands ─────────────────────────

def cmd_run(a) -> int:
    from . import adapters, references
    from . import report as rpt
    from .runner import candidate_python, run
    from .ports import resolve

    project = _project(a)
    ids = project.pick(k=a.k, split=a.split)
    lib, adapter = project.library, adapters.load(project.adapter_name)

    # R references: render missing/stale ones (unless told not to)
    cases = project.cases()
    stale = [cases[i] for i in ids if references.load(lib, adapter, cases[i], {}).get("error")]
    if stale:
        if a.no_render_r or shutil.which("Rscript") is None:
            why = "--no-render-r" if a.no_render_r else "Rscript is not on PATH"
            print(f"plotparity: {len(stale)} R reference(s) missing or stale ({why}): "
                  f"{', '.join(c.id for c in stale[:8])}{' …' if len(stale) > 8 else ''}", file=sys.stderr)
        else:
            print(f"rendering {len(stale)} R reference(s)…", file=sys.stderr)
            port = resolve(a.from_ or project.cfg["candidate"]["source"], project.package, project.root)
            references.render(lib, adapter, stale, candidate_python(project, port))

    runs_dir = project.runs_dir
    out = Path(a.out).resolve() if a.out else runs_dir / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    previous = None
    if a.compare:
        previous = Path(a.compare)
        previous = previous / "report.json" if previous.is_dir() else previous
    report, md, code = run(project, None, out, isolate=a.isolate, ids=ids)
    if previous is None and not a.no_compare and runs_dir.exists():
        previous = _comparable(runs_dir, report)
    if previous is not None and previous.exists():
        rpt.tag_against_previous(report, json.loads(previous.read_text()))
        md = rpt.write(report, out, True)
    if out.parent == runs_dir:
        _point_latest(runs_dir, out)
    html = None
    if not a.no_html and project.cfg.get("html", True):
        html = _build_html([out], out / "report.html")
    print(md.splitlines()[0] if a.quiet else md)
    if not a.quiet:
        print(f"report: {out / 'report.md'}" + (f"\n        {html}" if html else ""))
    if a.open and html:
        webbrowser.open(html.as_uri())
    return code


def cmd_view(a) -> int:
    targets = [Path(t).resolve() for t in a.runs]
    if not targets:
        cfg = _config_path(a)
        base = cfg.parent if cfg else Path.cwd()
        runs_dir = (base / ".plotparity" / "runs")
        latest = _latest(runs_dir)
        if latest is None:
            raise UsageError(f"no runs in {runs_dir}; run `plotparity run` first or pass a run directory")
        targets = [latest]
    run_dirs = [t.parent if t.suffix == ".json" else t for t in targets]
    for d in run_dirs:
        if not (d / "report.json").exists():
            raise UsageError(f"{d} has no report.json")
    out = Path(a.out).resolve() if a.out else (run_dirs[0] / ("report.html" if len(run_dirs) == 1 else "compare.html"))
    html = _build_html(run_dirs, out, public=a.public)
    print(html)
    if not a.no_open:
        webbrowser.open(html.as_uri())
    return 0


def cmd_references(a) -> int:
    from . import adapters, references
    from .engines.python import engine as py_engine
    from .project import Library, find_cases_root, match_k

    paths = [Path(p).resolve() for p in (a.paths or ["."])]
    roots = {find_cases_root(p) for p in paths}
    if None in roots or len(roots) != 1:
        raise UsageError("pass paths inside one cases folder (a folder with cases.toml)")
    lib = Library.load(roots.pop())
    adapter = adapters.load(lib.adapter_name)
    cases = lib.cases()
    picked = [c for c in cases.values() if any(c.path == p or p in c.path.parents for p in paths)]
    if a.k:
        picked = [c for c in picked if match_k(a.k, c.id)]
    if not picked:
        raise UsageError("no cases selected")
    todo = [c for c in picked if a.force or references.load(lib, adapter, c, {}).get("error")]
    if not todo:
        print("all selected references are fresh (use --force to re-render)")
        return 0
    res = references.render(lib, adapter, todo, py_engine.interpreter("", lib.root))
    print(res["stdout"])
    return 0


def cmd_validate(a) -> int:
    from .validate import validate
    return validate(_project(a), Path(a.out) if a.out else None, k=a.k)


def _compact_component(comp: dict, raw: bool = False) -> dict:
    if raw:
        return comp
    out = {}
    for k, v in comp.items():
        if k in ("text_runs", "lines", "panel_points"):
            out[k] = f"<{len(v)} items>"
        elif k == "density" and v is not None:
            out[k] = f"<{len(v[0])} pts>"
        elif k in ("fills", "strokes") and isinstance(v, list):
            out[k] = [{sk: sv for sk, sv in item.items() if sk not in ("top_x", "top_y")} for item in v]
        elif isinstance(v, list) and len(v) > 10:
            out[k] = f"<{len(v)} items>"
        else:
            out[k] = v
    return out


def cmd_inspect(a) -> int:
    from . import adapters
    from .compare import account_for_drawing
    from .project import Library, find_cases_root, match_k

    cfg = _config_path(a)
    lib = None
    package = a.package
    runs_dir = None
    if a.paths:
        roots = {find_cases_root(Path(p).resolve()) for p in a.paths}
        if None in roots or len(roots) != 1:
            raise UsageError("pass paths inside one cases folder (a folder with cases.toml)")
        lib = Library.load(roots.pop())
    elif cfg is not None:
        proj = _project(a)
        lib = proj.library
        package = package or proj.package
        runs_dir = proj.runs_dir
    else:
        root = find_cases_root(Path.cwd())
        if root is None:
            for d in (Path.cwd() / "parity", Path.cwd() / "cases"):
                if find_cases_root(d) == d:
                    root = d
                    break
        if root is None:
            raise UsageError("no cases folder found; pass a cases path or --config parity.toml")
        lib = Library.load(root)
    runs_dir = runs_dir or (Path.cwd() / ".plotparity" / "runs")
    cases = lib.cases()
    if a.case in cases:
        case = cases[a.case]
    else:
        hits = [c for cid, c in cases.items() if match_k(a.case, cid)]
        if not hits:
            raise UsageError(f"no case matching {a.case!r}")
        if len(hits) > 1:
            raise UsageError(f"{a.case!r} matched {len(hits)} cases: {[c.id for c in hits[:8]]}")
        case = hits[0]

    adapter = adapters.load(lib.adapter_name)
    r = lib.render
    canvas_pt = (r["width_px"] * 72.0 / r["dpi"], r["height_px"] * 72.0 / r["dpi"])
    ref_obs_p = lib.references_dir / case.id / "observations.json"
    ref_obs = json.loads(ref_obs_p.read_text()) if ref_obs_p.exists() else None

    run_dir = Path(a.run).resolve() if a.run else _latest(runs_dir)
    if run_dir and run_dir.suffix == ".json":
        run_dir = run_dir.parent
    cand_obs_p = (run_dir / "render" / case.id / "observations.json") if run_dir else None
    cand_obs = json.loads(cand_obs_p.read_text()) if (cand_obs_p and cand_obs_p.exists()) else None
    if not package and run_dir and (run_dir / "report.json").exists():
        package = json.loads((run_dir / "report.json").read_text()).get("package")

    print(f"CASE: {case.id} ({case.relpath})  adapter={adapter.NAME}  canvas_pt={canvas_pt}")
    if ref_obs is None:
        print(f"R reference: missing ({ref_obs_p})")
        rc = []
    else:
        rc, _ = adapter.components("r", ref_obs, case.expect, canvas_pt)
        print(f"\nR reference ({len(rc)} components)  [{ref_obs_p}]")
        for i, c in enumerate(rc):
            print(f"  {adapter.component_id(i, c)}: {json.dumps(_compact_component(c, a.raw), default=str)}")
    if cand_obs is None:
        print(f"\nPython candidate: none (checked {cand_obs_p or runs_dir}; pass --run DIR)")
        return 0
    cc_all, assigned = adapter.components("python", cand_obs, case.expect, canvas_pt)
    cc = [c for c in cc_all if adapter.in_scope(c, rc)] if hasattr(adapter, "in_scope") else cc_all
    print(f"\nPython candidate ({len(cc)} in-scope / {len(cc_all)} total)  [{cand_obs_p}]")
    if package and "owners" in cand_obs:
        unexplained, drawing = account_for_drawing(cand_obs, package, assigned)
        print(f"  drawing: port={drawing['port_events']} (assigned={drawing['port_assigned']}), "
              f"other={drawing['other_layer_events']}, unowned={drawing['unowned_events']}, "
              f"unexplained={len(unexplained)}, port_ink_px={cand_obs.get('port_ink_px')}")
    for j, c in enumerate(cc):
        print(f"  [{j + 1}] {adapter.component_id(j, c)}: {json.dumps(_compact_component(c, a.raw), default=str)}")
    if rc or cc:
        pairs, missing, unexpected = adapter.match(rc, cc)
        print(f"\nMatching: {len(pairs)} paired, {len(missing)} missing, {len(unexpected)} unexpected")
        for i, j in pairs:
            print(f"  pair: R {adapter.component_id(i, rc[i])}  <->  Py [{j + 1}] {adapter.component_id(j, cc[j])}")
        for i in missing:
            print(f"  missing: R {adapter.component_id(i, rc[i])}")
        for j in unexpected:
            print(f"  unexpected: Py unmatched_candidate[{j + 1}] {adapter.describe_component(cc[j])}")
    return 0


def cmd_selftest(a) -> int:
    from .selftest import selftest
    return selftest(_project(a), isolate_check=not a.skip_isolate_check)


def cmd_loop(a) -> int:
    from . import loop
    a.source = a.from_
    return loop.dispatch(_project(a), a)


def cmd_new_case(a) -> int:
    from .project import Library, find_cases_root
    from .scaffold import new_case
    target = Path(a.path).resolve()
    if target.suffix != ".md":
        target = target.with_suffix(".md")
    root = find_cases_root(target.parent if target.parent.exists() else target.parent.parent)
    if root is None:
        raise UsageError(f"no cases.toml at or above {target.parent}; create one at the root of your cases folder "
                         "(e.g. `adapter = \"ggtext\"`) or run `plotparity init`")
    rel = target.relative_to(root).with_suffix("").as_posix()
    print(new_case(Library.load(root), rel, a.package or ""))
    return 0


def cmd_calibrate(a) -> int:
    """Re-evaluate a run's stored observations with zero tolerances to report clean max deltas per check."""
    from . import adapters
    from .compare import evaluate_case, load_profile
    from .project import Library

    cfg = _config_path(a)
    base = cfg.parent if cfg else Path.cwd()
    run_target = Path(a.run).resolve() if a.run else _latest(base / ".plotparity" / "runs")
    if run_target is None:
        raise UsageError("no run found; pass a run directory or report.json (or run `plotparity run` first)")
    rf = run_target if run_target.suffix == ".json" else run_target / "report.json"
    if not rf.exists():
        raise UsageError(f"{rf} not found")
    run_dir = rf.parent
    rep = json.loads(rf.read_text())
    lib = Library.load(Path(rep["cases_root"])) if rep.get("cases_root") else _project(a).library
    adapter = adapters.load(lib.adapter_name)
    cases = lib.cases()
    r = lib.render
    canvas_pt = (r["width_px"] * 72.0 / r["dpi"], r["height_px"] * 72.0 / r["dpi"])
    pkg = rep.get("package") or "port"

    base_profile = rep.get("profile") or load_profile(adapter, {})
    zero_profile = {}
    for name, entry in base_profile.items():
        z = dict(entry)
        for nk in ("tol", "rel_tol", "alpha_tol"):
            if nk in z:
                z[nk] = 0.0
        zero_profile[name] = z

    stats: dict[str, dict] = {}
    n_cases = 0
    for c_res in rep.get("cases", []):
        cid = c_res["case"]
        case = cases.get(cid)
        if case is None or not case.measurable:
            continue
        ref_p = lib.references_dir / cid / "observations.json"
        cand_p = run_dir / "render" / cid / "observations.json"
        if not (ref_p.exists() and cand_p.exists()):
            continue
        n_cases += 1
        ref = {"observations": json.loads(ref_p.read_text()), "png": c_res.get("reference_png")}
        cand = {"observations": json.loads(cand_p.read_text()), "png": c_res.get("candidate_png")}
        res = evaluate_case(case, adapter, zero_profile, [], ref, cand, package=pkg, canvas_pt=canvas_pt)
        for f in res["findings"]:
            d = f.get("delta")
            if not isinstance(d, (int, float)) or isinstance(d, bool):
                continue
            ad = abs(float(d))
            sub = f["component"].split("/")[-1] if "/" in f["component"] else ""
            key = f"{f['check']}:{sub}" if sub and not sub.startswith(("run[", "script[", "line[")) else f["check"]
            st = stats.setdefault(key, {"check": f["check"], "n": 0, "max": 0.0, "worst": ""})
            st["n"] += 1
            if ad >= st["max"]:
                st["max"] = ad
                st["worst"] = cid

    print(f"CALIBRATION SUMMARY ({n_cases} cases, adapter={adapter.NAME}, run={run_dir.name})")
    if not stats:
        print("  no numeric deltas observed (every numeric check was exact 0)")
        return 0
    print(f"  {'check':<30} {'n':>5} {'max delta':>10} {'current tol':>14}  worst case")
    for key in sorted(stats):
        st = stats[key]
        e = base_profile.get(st["check"], {})
        tols = ", ".join(f"{k}={e[k]}" for k in ("tol", "rel_tol", "alpha_tol") if k in e) or "—"
        print(f"  {key:<30} {st['n']:>5} {st['max']:>10.3f} {tols:>14}  {st['worst']}")
    return 0


def cmd_init(a) -> int:
    cfg = Path.cwd() / "parity.toml"
    if cfg.exists():
        raise UsageError(f"{cfg} already exists")
    cases_dir = Path(a.cases)
    lines = ["# plotparity settings for this directory (all optional; command-line flags override them).",
             f'cases = "{a.cases}"']
    if a.from_:
        lines.append(f'from = "{a.from_}"')
    if a.package:
        lines.append(f'package = "{a.package}"')
    lines += ["# clean_room = true      # set for MIT/BSD ports of GPL R packages (reminds agents not to read R source)",
              "# requirements = []      # extra packages for the environment the package is tested in",
              "# known_good = \"\"        # a version known to match R (enables `validate` checks and `selftest`)",
              "", "# [splits]               # optional: name groups of cases (globs over folder/id)",
              '# dev = ["geom_*/*"]', '# holdout = ["markup/*"]', ""]
    cfg.write_text("\n".join(lines))
    made = [str(cfg)]
    if not (cases_dir / "cases.toml").exists():
        cases_dir.mkdir(parents=True, exist_ok=True)
        (cases_dir / "cases.toml").write_text(
            f'adapter = "{a.adapter}"\n\n[reference]\npackages = ["ggplot2"]   # R packages each case needs\n\n'
            "[render]\nwidth_px = 640\nheight_px = 480\ndpi = 72\n")
        made.append(str(cases_dir / "cases.toml"))
    gi = Path.cwd() / ".gitignore"
    text = gi.read_text() if gi.exists() else ""
    if ".plotparity/" not in text.split():
        gi.write_text(text + ("" if not text or text.endswith("\n") else "\n") + ".plotparity/\n")
        made.append(f"{gi} (+ .plotparity/)")
    print("wrote " + "\n      ".join(made))
    return 0


# ───────────────────────── argument parsing ─────────────────────────

def _common(p, paths_help="case folders or .md files (default: `cases` in parity.toml, else ./parity/ or ./cases/)"):
    p.add_argument("paths", nargs="*", metavar="PATH", help=paths_help)
    p.add_argument("--from", dest="from_", metavar="SOURCE",
                   help="the Python package under test: a directory or git+URL@rev (default: `from` in parity.toml, else .)")
    p.add_argument("--package", help="import name of the package, if it can't be inferred")
    p.add_argument("--config", metavar="FILE", help="a parity.toml to use (default: ./parity.toml if present)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="plotparity", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    p = sub.add_parser("run", help="test the package against the cases' R output")
    _common(p)
    p.add_argument("-k", metavar="EXPR", help="only cases whose name matches (substring; `or`, `and`, `not`)")
    p.add_argument("--split", metavar="NAME", help="only cases in this split (from [splits] in parity.toml)")
    p.add_argument("--out", metavar="DIR", help="where to write the run (default: .plotparity/runs/<timestamp>/)")
    p.add_argument("--open", action="store_true", help="open report.html when done")
    p.add_argument("--no-html", action="store_true", help="don't build report.html")
    p.add_argument("--compare", metavar="RUN", help="tag findings new/fixed against this run (dir or report.json)")
    p.add_argument("--no-compare", action="store_true",
                   help="don't tag against the latest earlier run of the same package and cases (the default)")
    p.add_argument("-q", "--quiet", action="store_true", help="print only the status line")
    p.add_argument("--no-render-r", action="store_true", help="fail instead of rendering missing or stale R references")
    p.add_argument("--isolate", action="store_true", help="render each case in its own process (to pin down crashes)")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("view", help="open the HTML report for the latest run, a given run, or several runs side by side")
    p.add_argument("runs", nargs="*", metavar="RUN", help="run directories or report.json files (default: the latest run)")
    p.add_argument("--out", metavar="FILE", help="where to write the HTML (default: inside the first run)")
    p.add_argument("--no-open", action="store_true", help="write the HTML without opening it")
    p.add_argument("--public", action="store_true", help="write local paths under your home directory as ~ (for publishing)")
    p.add_argument("--config", metavar="FILE", help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_view)

    p = sub.add_parser("init", help="write a parity.toml (and a cases.toml if needed) in this directory")
    p.add_argument("--cases", default="parity", help="cases folder (default: parity)")
    p.add_argument("--from", dest="from_", metavar="SOURCE", help="the package under test (default: this directory)")
    p.add_argument("--package", help="import name of the package")
    p.add_argument("--adapter", default="ggtext", help="extension adapter for a new cases.toml (default: ggtext)")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("new-case", help="create a case file, e.g. parity/markup/my_case.md")
    p.add_argument("path", help="path of the new case file inside a cases folder")
    p.add_argument("--package", help="tag the python block for this package (default: an untagged block)")
    p.set_defaults(fn=cmd_new_case)

    p = sub.add_parser("references", help="render R references for cases (missing or stale ones by default)")
    p.add_argument("paths", nargs="*", metavar="PATH", help="case folders or .md files (default: .)")
    p.add_argument("-k", metavar="EXPR", help="only cases whose name matches (substring; `or`, `and`, `not`)")
    p.add_argument("--force", action="store_true", help="re-render even if fresh")
    p.set_defaults(fn=cmd_references)

    p = sub.add_parser("validate", help="check cases: parsing, fresh references, component counts, known_good results")
    _common(p)
    p.add_argument("-k", metavar="EXPR", help="only cases whose name matches (substring; `or`, `and`, `not`)")
    p.add_argument("--out", metavar="DIR", help="where to write the known_good run")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("inspect", help="dump extracted R vs Python components and pairing for one case")
    p.add_argument("case", metavar="CASE", help="case id or unique substring")
    p.add_argument("paths", nargs="*", metavar="PATH", help="cases folder (default: parity.toml or ./parity/)")
    p.add_argument("--run", metavar="DIR", help="run directory or report.json (default: latest run)")
    p.add_argument("--raw", action="store_true", help="show full component dicts without truncating arrays")
    p.add_argument("--package", help="import name of the package (for drawing ownership summary)")
    p.add_argument("--config", metavar="FILE", help="a parity.toml to use (default: ./parity.toml if present)")
    p.set_defaults(fn=cmd_inspect)

    p = sub.add_parser("calibrate", help="summarize max numeric deltas across a run's stored observations (for setting tolerances)")
    p.add_argument("run", nargs="?", metavar="RUN", help="run directory or report.json (default: latest run)")
    p.add_argument("--config", metavar="FILE", help="a parity.toml to use (default: ./parity.toml if present)")
    p.set_defaults(fn=cmd_calibrate)

    p = sub.add_parser("selftest", help="check the checker: re-run the mutation suite against known_good")
    _common(p)
    p.add_argument("--skip-isolate-check", action="store_true", help="skip the batch vs --isolate comparison")
    p.set_defaults(fn=cmd_selftest)

    p = sub.add_parser("loop", help="record an agent's iterations (init, iterate, holdout, finish)")
    p.add_argument("action", choices=["init", "iterate", "holdout", "finish"])
    _common(p)
    p.add_argument("--name", required=True, help="loop name")
    p.add_argument("--note", default="", help="iterate: what changed and which finding it addresses")
    p.add_argument("--goal", default="", help="init: what is being ported")
    p.add_argument("--summary", default="", help="finish: outcome")
    p.add_argument("--no-html", action="store_true", help="finish: don't build report.html")
    p.set_defaults(fn=cmd_loop)

    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (ConfigError, CaseError, ProfileError, PortError, UsageError) as exc:
        print(f"plotparity: error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
