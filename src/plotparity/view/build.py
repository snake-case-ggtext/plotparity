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


def _geometry(project, case, ref_obs_path: Path, cand_obs_path: Path) -> dict[str, Any]:
    """{component_id: {"ref": bbox, "cand": bbox, "runs": [{"text", "ref": [x,y], "cand": [x,y]}]}} (norm top-left)."""
    from .. import adapters

    try:
        adapter = adapters.load(project.adapter_name)
        ref_obs = json.loads(ref_obs_path.read_text())
        cand_obs = json.loads(cand_obs_path.read_text()) if cand_obs_path.exists() else None
        canvas = getattr(project, "canvas_pt", (640.0, 480.0))
        rc, _ = adapter.components("r", ref_obs, case.expect, canvas)
        cc, _ = adapter.components("python", cand_obs, case.expect, canvas) if cand_obs else ([], [])
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
        out[f"box[unmatched_cand_{j + 1}]"] = {"ref": None, "cand": cc[j].get("bbox_norm_tl"),
                                               "ref_runs": [], "cand_runs": runs(cc[j]), "angle": cc[j].get("angle_deg", 0)}
    return out


def _label(rep: dict, project) -> str:
    cand = rep.get("candidate", {})
    commit = (cand.get("commit") or "")[:7]
    pkg = project.package if project else "?"
    return f"{Path(rep.get('project', '?')).name} · {pkg}" + (f"@{commit}" if commit else "")


def _settings(rep: dict, project) -> dict:
    """Every check's settings as used by this run, which values the project overrides, and known differences."""
    overrides, known, files = {}, [], {}
    if project is not None:
        from .. import adapters
        from ..compare import load_known
        overrides = project.cfg.get("profile", {}).get("checks", {}) or {}
        files["defaults"] = str(adapters.load(project.adapter_name).PROFILE)
        files["project"] = str(project.config_path) if project.config_path else None
        kp = project.known_differences_path()
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


def build(report_files: list[Path], out: Path) -> Path:
    from ..project import Project

    runs, images, sizes, case_meta = [], {}, {}, {}
    for rf in report_files:
        rf = _report_path(rf).resolve()
        rep = json.loads(rf.read_text())
        run_dir = rf.parent
        try:
            project = Project.load(rep["project"])
            lib_cases = project.library.cases()
            ref_dir = project.library.references_dir
        except Exception:
            project, lib_cases, ref_dir = None, {}, None
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
            if case is not None and ref_dir is not None:
                geom = _geometry(project, case, ref_dir / cid / "observations.json",
                                 run_dir / "render" / cid / "observations.json")
            if case is not None and cid not in case_meta:
                case_meta[cid] = {"why": case.why, "relpath": case.relpath, "r": case.r_code,
                                  "python": case.python, "source": case.front_matter.get("source", "")}
            cases.append({**{k: c.get(k) for k in ("case", "relpath", "split", "status", "findings", "known")},
                          "render_hash": _pixel_hash(c.get("candidate_png")),
                          "ref_img": ref_key if images.get(ref_key) else "",
                          "cand_img": cand_key if images.get(cand_key) else "", "geom": geom})
        runs.append({"id": run_id, "label": _label(rep, project), "created": rep.get("created"),
                     "status": rep.get("status"), "selection": rep.get("selection"),
                     "package": project.package if project else "", "candidate": rep.get("candidate", {}),
                     "known_unused": rep.get("known_differences_unused", []), "cases": cases,
                     "settings": _settings(rep, project),
                     "report_path": str(rf)})
    data = {"runs": runs, "cases": case_meta, "sizes": sizes}
    html = TEMPLATE.read_text()
    html = html.replace("/*__DATA__*/null", json.dumps(data, default=str).replace("</", "<\\/"))
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
