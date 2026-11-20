"""Build a self-contained HTML viewer from one or more report.json files.

    python -m plotparity.view.build <report.json | run-dir> [...] [--out report.html]

The viewer shows how a parity test went: per case, the R reference vs the port's render (side by side, blink,
swipe, difference, synced zoom) with every finding outlined on both images. Several reports → a cases × runs
overview (e.g. two ports, or a loop's iterations).

Findings carry only a component id; geometry is recovered here by re-running the adapter on the stored
observations (same matching as the comparison), so the viewer can outline components and runs.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import re
from pathlib import Path
from typing import Any

TEMPLATE = Path(__file__).with_name("template.html")


def _pixel_hash(path: str | Path | None) -> str | None:
    """Short hash of a render's pixels: the viewer's OK marks are tied to it, so they reset when the render changes."""
    if not path or not Path(path).exists():
        return None
    import hashlib

    from PIL import Image
    im = Image.open(path).convert("RGBA")
    return hashlib.sha256(repr(im.size).encode() + im.tobytes()).hexdigest()[:16]


def _img(path: str | Path | None) -> tuple[str, list[int] | None]:
    if not path or not Path(path).exists():
        return "", None
    from PIL import Image

    im = Image.open(path)
    if im.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="WEBP", lossless=True, method=4)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode(), list(im.size)


def _report_path(p: Path) -> Path:
    return p if p.suffix == ".json" else p / "report.json"


def _canvas_pt(library) -> tuple[float, float]:
    r = library.render
    return (r["width_px"] * 72.0 / r["dpi"], r["height_px"] * 72.0 / r["dpi"])


def _geometry(library, case, ref_obs_path: Path, cand_obs_path: Path) -> dict[str, Any]:
    """{component_id: {"ref": bbox, "cand": bbox, "runs": [{"text", "ref": [x,y], "cand": [x,y]}]}} (norm top-left)."""
    from .. import adapters

    try:
        adapter = adapters.load(library.adapter_name)
        ref_obs = json.loads(ref_obs_path.read_text())
        cand_obs = json.loads(cand_obs_path.read_text()) if cand_obs_path.exists() else None
        canvas = _canvas_pt(library)
        rc, _ = adapter.components("r", ref_obs, case.expect, canvas)
        cc, _ = adapter.components("python", cand_obs, case.expect, canvas) if cand_obs else ([], [])
        if hasattr(adapter, "in_scope"):
            cc = [c for c in cc if adapter.in_scope(c, rc)]
        pairs, missing, unexpected = adapter.match(rc, cc) if cc else ([], list(range(len(rc))), [])
    except Exception as exc:  # the viewer must never fail because geometry couldn't be recovered
        return {"_error": f"{type(exc).__name__}: {exc}"}

    def runs(comp):
        return [{"text": r["text"], "xy": [r["x_norm"], r["y_norm"]], "size": r.get("fontsize_pt", 11.0)}
                for r in comp.get("text_runs", [])]

    out: dict[str, Any] = {}
    for i, j in pairs:
        cid = adapter.component_id(i, rc[i])
        out[cid] = {"ref": rc[i].get("bbox_norm_tl"), "cand": cc[j].get("bbox_norm_tl"),
                    "ref_runs": runs(rc[i]), "cand_runs": runs(cc[j]), "angle": rc[i].get("angle_deg", 0)}
    for i in missing:
        out[adapter.component_id(i, rc[i])] = {"ref": rc[i].get("bbox_norm_tl"), "cand": None,
                                               "ref_runs": runs(rc[i]), "cand_runs": [], "angle": rc[i].get("angle_deg", 0)}
    for j in unexpected:
        out[f"unmatched_candidate[{j + 1}]"] = {"ref": None, "cand": cc[j].get("bbox_norm_tl"),
                                                "ref_runs": [], "cand_runs": runs(cc[j]), "angle": cc[j].get("angle_deg", 0)}
    return out


def _loop_notes(run_dir: Path) -> dict[str, str]:
    """If `run_dir` lives inside a `plotparity loop` folder, map run dir names (iter_000, holdout_01) to notes."""
    log = run_dir.parent / "loop.jsonl"
    if not log.exists():
        return {}
    notes: dict[str, str] = {}
    for line in log.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "iter" in row:
            notes[f"iter_{int(row['iter']):03d}"] = str(row.get("note") or "")
        elif "holdout" in row:
            notes[f"holdout_{int(row['holdout']):02d}"] = "unseen holdout evaluation"
    return notes


def _label(rep: dict, project, run_dir: Path | None = None) -> str:
    if run_dir is not None:
        m = re.match(r"^iter_(\d+)$", run_dir.name)
        if m:
            return f"Round {int(m.group(1))}"
        m = re.match(r"^holdout_(\d+)$", run_dir.name)
        if m:
            return f"Holdout {int(m.group(1))}"
    cand = rep.get("candidate", {})
    commit = (cand.get("commit") or "")[:7]
    pkg = rep.get("package") or (project.package if project else "?")
    return f"{Path(rep.get('project', '?')).name} · {pkg}" + (f"@{commit}" if commit else "")


def _settings(rep: dict, project, library=None) -> dict:
    """Every check's settings as used by this run, which values the project overrides, and known differences."""
    from .. import adapters
    from ..compare import load_known

    overrides, known, files = {}, [], {}
    if project is not None:
        overrides = project.cfg.get("profile", {}).get("checks", {}) or {}
        files["project"] = str(project.config_path) if project.config_path else None
    adapter_name = project.adapter_name if project is not None else (library.adapter_name if library is not None else None)
    if adapter_name:
        files["defaults"] = str(adapters.load(adapter_name).PROFILE)
    kp = project.known_differences_path() if project is not None else (
        Path(rep["project"]) / "known_differences.toml" if rep.get("project") else None
    )
    if kp is not None and (project is not None or kp.exists()):
        files["known"] = str(kp)
        try:
            known = load_known(kp)
        except Exception as exc:                      # noqa: BLE001 — show it, don't break the viewer
            known = [{"error": str(exc)}]
    fails: dict[str, int] = {}
    for c in rep.get("cases", []):
        for f in c.get("findings", []):
            if f.get("status") == "fail":
                fails[f["check"]] = fails.get(f["check"], 0) + 1
    checks = [{"name": name, **{k: v for k, v in e.items()}, "overridden": sorted(overrides.get(name, {})),
               "fails": fails.get(name, 0)} for name, e in (rep.get("profile") or {}).items()]
    return {"checks": checks, "known": known, "files": files}


def _load_context(rep: dict):
    """Load (Project | None, Library | None) for a report, supporting both parity.toml and config-free runs."""
    from ..project import Library, Project

    project = library = None
    cfg = rep.get("config")
    if cfg and Path(cfg).exists():
        try:
            project = Project.load(cfg)
            library = project.library
        except Exception:
            pass
    elif rep.get("project") and (Path(rep["project"]) / "parity.toml").exists():
        try:
            project = Project.load(rep["project"])
            library = project.library
        except Exception:
            pass
    if library is None and rep.get("cases_root"):
        try:
            library = Library.load(Path(rep["cases_root"]))
        except Exception:
            pass
    return project, library


def build(report_files: list[Path], out: Path, public: bool = False) -> Path:
    """Self-contained HTML for one or more runs. `public`: write paths under the home directory as `~` (for
    publishing a report without the local user name)."""
    runs, images, sizes, case_meta = [], {}, {}, {}
    for rf in report_files:
        rf = _report_path(rf).resolve()
        rep = json.loads(rf.read_text())
        run_dir = rf.parent
        project, library = _load_context(rep)
        try:
            lib_cases = library.cases() if library is not None else {}
            ref_dir = library.references_dir if library is not None else None
        except Exception:
            lib_cases, ref_dir = {}, None
        run_id = f"r{len(runs)}"
        cases = []
        for c in rep.get("cases", []):
            cid = c["case"]
            case = lib_cases.get(cid)
            ref_key, cand_key = f"ref:{cid}", f"{run_id}:{cid}"
            if ref_key not in images:
                images[ref_key], sizes[ref_key] = _img(c.get("reference_png"))
            images[cand_key], sizes[cand_key] = _img(c.get("candidate_png"))
            geom = {}
            if library is not None and case is not None and ref_dir is not None:
                geom = _geometry(library, case, ref_dir / cid / "observations.json",
                                 run_dir / "render" / cid / "observations.json")
            if case is not None and cid not in case_meta:
                case_meta[cid] = {"why": case.why, "relpath": case.relpath, "r": case.r_code,
                                  "python": case.python, "source": case.front_matter.get("source", "")}
            cases.append({**{k: c.get(k) for k in ("case", "relpath", "split", "status", "findings", "known")},
                          "render_hash": _pixel_hash(c.get("candidate_png")),
                          "ref_img": ref_key if images.get(ref_key) else "",
                          "cand_img": cand_key if images.get(cand_key) else "", "geom": geom})
        notes = _loop_notes(run_dir)
        runs.append({"id": run_id, "label": _label(rep, project, run_dir), "run_name": run_dir.name,
                     "note": notes.get(run_dir.name, ""), "created": rep.get("created"),
                     "status": rep.get("status"), "selection": rep.get("selection"),
                     "package": rep.get("package") or (project.package if project else ""),
                     "candidate": rep.get("candidate", {}),
                     "known_unused": rep.get("known_differences_unused", []), "cases": cases,
                     "settings": _settings(rep, project, library),
                     "report_path": str(rf)})
    # Disambiguate identical labels (e.g. comparing two non-loop runs of the same commit)
    label_counts: dict[str, int] = {}
    for r in runs:
        label_counts[r["label"]] = label_counts.get(r["label"], 0) + 1
    for r in runs:
        if label_counts[r["label"]] > 1 and r.get("run_name"):
            r["label"] = f"{r['label']} ({r['run_name']})"
    data = {"runs": runs, "cases": case_meta, "sizes": sizes}
    html = TEMPLATE.read_text()
    blob = json.dumps(data, default=str)
    if public:
        home = str(Path.home())
        blob = blob.replace(json.dumps(home)[1:-1], "~")
    html = html.replace("/*__DATA__*/null", blob.replace("</", "<\\/"))
    html = html.replace("/*__IMAGES__*/null", json.dumps(images))
    out.write_text(html)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reports", nargs="+", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    out = a.out or (_report_path(a.reports[0]).parent / ("report.html" if len(a.reports) == 1 else "compare.html"))
    print(build(a.reports, out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
