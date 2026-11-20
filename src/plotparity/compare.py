"""Core comparison: profile merge, core checks, fail-closed rules, known differences, statuses.

A finding: {case, check, component, status, severity, tier, expected, observed, delta, tol, message, fingerprint}
  status   : fail | unmeasured | known_difference | infrastructure_error
  severity : gating | advisory (from the profile)
Case status order: infrastructure_error > fail (any gating fail) > unmeasured > pass.

Each case result also carries `outcomes` ({component id: {check: pass|fail|unmeasured|off|known_difference}}, plus
a "case" entry for case-level checks) so `--previous` can tell a fixed defect from an accepted or unmeasured one,
and `drawing` (how the candidate's draw events were accounted for; see `account_for_drawing`).
"""

from __future__ import annotations

import difflib
import fnmatch
import math
import tomllib
from pathlib import Path
from typing import Any, Callable

import numpy as np
from skimage.color import deltaE_ciede2000, rgb2lab

from .engines.python.probe import hex_to_rgb01, is_port_module

CASE_LEVEL = ("component_count", "missing_component", "unexpected_component", "unexpected_drawing", "extractor_blind",
              "text_content")
TIER_ORDER = {"inventory": 0, "layout": 1, "style": 2}
PORT_ERRORS = {"render_error", "missing_api", "render_timeout"}
INFRA_ERRORS = {"environment_error", "case_error", "reference_error", "stale_reference", "font_drift", "check_error"}
CASE = "case"           # outcomes key for case-level checks
CASE_KEYED = {"component_count", "unexpected_component", "unexpected_drawing", "extractor_blind"}


class ProfileError(ValueError):
    pass


class CheckError(ValueError):
    """A non-finite value reached a check (bad observation or computation): an infrastructure error."""


# ───────────────────────────── profile ─────────────────────────────

PROFILE_KEYS = {"severity", "tier", "tol", "rel_tol", "alpha_tol", "min", "calibration"}
NUMERIC_KEYS = PROFILE_KEYS - {"severity", "tier", "calibration"}


def load_profile(adapter, project_overrides: dict[str, dict]) -> dict[str, dict]:
    base = tomllib.loads(Path(adapter.PROFILE).read_text())["checks"]
    known = set(CORE_CHECKS) | set(CASE_LEVEL) | set(adapter.CHECKS) | set(getattr(adapter, "CASE_CHECKS", {}))
    for name, entry in list(base.items()) + list(project_overrides.items()):
        if name not in known:
            raise ProfileError(f"profile names unknown check {name!r} (known: {sorted(known)})")
        bad = set(entry) - PROFILE_KEYS
        if bad:
            raise ProfileError(f"profile check {name!r}: unknown keys {sorted(bad)}")
    missing = known - set(base)
    if missing:
        raise ProfileError(f"adapter profile does not configure checks {sorted(missing)} (set severity='off' to disable)")
    merged = {k: dict(v) for k, v in base.items()}
    for name, entry in project_overrides.items():
        merged[name].update(entry)
    for name, entry in merged.items():
        if entry.get("severity") not in ("gating", "advisory", "off"):
            raise ProfileError(f"check {name!r}: severity must be gating|advisory|off")
        if entry.get("tier") not in TIER_ORDER:
            raise ProfileError(f"check {name!r}: tier must be one of {list(TIER_ORDER)}")
        for key in NUMERIC_KEYS & set(entry):     # a NaN tolerance would silently disable a check (`d > nan` is False)
            v = entry[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                raise ProfileError(f"check {name!r}: {key} must be a finite number >= 0, got {v!r}")
    return merged


# ───────────────────────────── numeric guards ─────────────────────────────

def exceeds(delta: float, tol: float, what: str) -> bool:
    """`delta > tol`, but a non-finite delta is an infrastructure error, never a silent pass."""
    if not math.isfinite(delta):
        raise CheckError(f"{what}: non-finite difference {delta!r}")
    return delta > tol


def assert_finite(obj: Any, where: str) -> None:
    """Every number in a component (recursively) must be finite; raises CheckError naming the field."""
    if isinstance(obj, float) and not math.isfinite(obj):
        raise CheckError(f"non-finite value {obj!r} at {where}")
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert_finite(v, f"{where}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            assert_finite(v, f"{where}[{i}]")


# ───────────────────────────── core pair checks ─────────────────────────────
# Signature: fn(ref, cand, P, add, ctx). P = merged profile. ctx: {"expect": ..., "png": RGB array | None}.
# Text checks run only on components that have `text_runs`; shape checks only on components that have `rect`.
# A check returns False when it does not apply to this pair (recorded as `not_applicable`, never as `pass`).

def color_delta_e(a: str, b: str) -> float:
    if a == b:
        return 0.0
    if a == "none" or b == "none":
        return 100.0
    ra, rb = hex_to_rgb01(a), hex_to_rgb01(b)
    if ra is None or rb is None:
        return 100.0
    la, lb = rgb2lab(np.array([[ra]], dtype=np.float64)), rgb2lab(np.array([[rb]], dtype=np.float64))
    return round(float(deltaE_ciede2000(la, lb)[0, 0]), 2)


def _aligned_runs(r: dict, c: dict) -> list[tuple[dict, dict]] | None:
    """Align text runs by their text (never by index). None = cannot align at all."""
    rw, cw = [t["text"] for t in r["text_runs"]], [t["text"] for t in c["text_runs"]]
    if not rw or not cw:
        return None
    sm = difflib.SequenceMatcher(a=rw, b=cw, autojunk=False)
    pairs = [(r["text_runs"][i + k], c["text_runs"][j + k]) for i, j, n in sm.get_matching_blocks() for k in range(n)]
    return pairs or None


TEXT_STYLE = ("text_color", "text_alpha", "font_weight", "font_style", "font_size", "text_visible", "ink_present")


def text_content(r, c, P, add, ctx):
    rw, cw = [t["text"] for t in r["text_runs"]], [t["text"] for t in c["text_runs"]]
    if rw != cw:
        add("text_content", rw, cw, "Word sequence differs from the reference.")


def style_checks(r, c, P, add, ctx):
    pairs = _aligned_runs(r, c)
    if pairs is None:
        if r["text_runs"]:
            for name in TEXT_STYLE:
                add(name, "aligned text runs", None, "Text runs could not be aligned by content; style unmeasured.",
                    status="unmeasured")
        return
    for k, (rt, ct) in enumerate(pairs):
        sub = f"run[{k}:{rt['text']!r}]"
        de = color_delta_e(rt["color_hex"], ct["color_hex"])
        if exceeds(de, P["text_color"]["tol"], "text_color"):
            add("text_color", rt["color_hex"], ct["color_hex"],
                f"Span {rt['text']!r} rendered with color {ct['color_hex']} instead of {rt['color_hex']} (ΔE={de}).",
                delta=de, sub=sub)
        da = round(abs(rt["alpha"] - ct["alpha"]), 4)
        if exceeds(da, P["text_alpha"]["tol"], "text_alpha"):
            add("text_alpha", rt["alpha"], ct["alpha"],
                f"Span {rt['text']!r} rendered with opacity {ct['alpha']} instead of {rt['alpha']}.", delta=da, sub=sub)
        if rt["fontweight"] != ct["fontweight"]:
            add("font_weight", rt["fontweight"], ct["fontweight"],
                f"Span {rt['text']!r} rendered with weight {ct['fontweight']!r} instead of {rt['fontweight']!r}.", sub=sub)
        if rt["fontstyle"] != ct["fontstyle"]:
            add("font_style", rt["fontstyle"], ct["fontstyle"],
                f"Span {rt['text']!r} rendered with style {ct['fontstyle']!r} instead of {rt['fontstyle']!r}.", sub=sub)
        d = round(abs(rt["fontsize_pt"] - ct["fontsize_pt"]), 3)
        if exceeds(d, P["font_size"]["tol"], "font_size"):
            add("font_size", rt["fontsize_pt"], ct["fontsize_pt"],
                f"Span {rt['text']!r} font size {ct['fontsize_pt']}pt instead of {rt['fontsize_pt']}pt.", delta=d, sub=sub)


def text_visible(r, c, P, add, ctx):
    """A candidate run drawn mostly outside its clip rectangle (or the canvas) while the reference run is visible."""
    pairs = _aligned_runs(r, c) or []
    for k, (rt, ct) in enumerate(pairs):
        vf = ct.get("visible_fraction")
        if rt["alpha"] <= 0 or vf is None:
            continue
        if exceeds(P["text_visible"]["min"] - vf, 0.0, "text_visible"):
            add("text_visible", f">= {P['text_visible']['min']} of the run inside its clip area", round(vf, 3),
                f"Span {rt['text']!r} is clipped away: only {vf:.0%} of it lies inside its clip rectangle.",
                delta=round(vf, 3), sub=f"run[{k}:{rt['text']!r}]")


def ink_count(img: np.ndarray, quad_norm_tl: list[list[float]], contrast: float) -> int:
    """Pixels (centres) inside the polygon that differ from its most common colour by >= `contrast`
    (max over RGB, 0-255 levels). The polygon is a text run's rotated rectangle in normalized top-left coords
    (baseline-left, baseline-right, top-right, top-left)."""
    from matplotlib.path import Path as MplPath
    h, w = img.shape[:2]
    q = np.asarray(quad_norm_tl, dtype=float) * [w, h]
    # Grow the rectangle by 1 px on every side: antialiasing spreads glyph ink ~1 px, and a tiny glyph ('.')
    # can have an extent below one pixel, with no pixel centre inside it.
    u, v = q[1] - q[0], q[3] - q[0]
    u, v = u / max(np.hypot(*u), 1e-9), v / max(np.hypot(*v), 1e-9)
    q = q + np.array([-u - v, u - v, u + v, -u + v])
    x0, y0 = max(0, int(math.floor(q[:, 0].min()))), max(0, int(math.floor(q[:, 1].min())))
    x1, y1 = min(w, int(math.ceil(q[:, 0].max()))), min(h, int(math.ceil(q[:, 1].max())))
    if x1 <= x0 or y1 <= y0:
        return 0
    yy, xx = np.mgrid[y0:y1, x0:x1]
    inside = MplPath(q).contains_points(np.c_[xx.ravel() + 0.5, yy.ravel() + 0.5])
    region = img[y0:y1, x0:x1, :3].reshape(-1, 3).astype(int)[inside]
    if not len(region):
        return 0
    colours, counts = np.unique(region, axis=0, return_counts=True)
    bg = colours[counts.argmax()]
    return int((np.abs(region - bg).max(axis=1) >= contrast).sum())


def ink_present(r, c, P, add, ctx):
    """The candidate PNG must show ink where each visible reference run is drawn (backstop for unrecorded effects)."""
    pairs = _aligned_runs(r, c) or []
    img = ctx.get("png")
    seen: set[tuple] = set()
    for k, (rt, ct) in enumerate(pairs):
        quad = ct.get("ink_quad_norm_tl")
        if rt["alpha"] <= 0 or quad is None or str(quad) in seen:
            continue
        seen.add(str(quad))
        sub = f"run[{k}:{rt['text']!r}]"
        if img is None:
            add("ink_present", "candidate PNG", None, "No candidate PNG to look for ink in.", status="unmeasured", sub=sub)
            continue
        n = ink_count(img, quad, P["ink_present"]["tol"])
        if exceeds(P["ink_present"]["min"] - n, 0.0, "ink_present"):
            add("ink_present", f">= {P['ink_present']['min']} ink px", n,
                f"Span {rt['text']!r}: the candidate PNG shows only {n} ink pixel(s) where it is drawn (invisible text?).",
                delta=n, sub=sub)


def rotation(r, c, P, add, ctx):
    d = round(abs((r["angle_deg"] - c["angle_deg"] + 180.0) % 360.0 - 180.0), 3)
    if exceeds(d, P["rotation"]["tol"], "rotation"):
        add("rotation", r["angle_deg"], c["angle_deg"],
            f"Rotation {c['angle_deg']}° instead of {r['angle_deg']}° (diff {d}°).", delta=d)


def centroid(r, c, P, add, ctx):
    d = round(math.hypot(r["centroid_norm_tl"][0] - c["centroid_norm_tl"][0],
                         r["centroid_norm_tl"][1] - c["centroid_norm_tl"][1]), 4)
    if exceeds(d, P["centroid"]["tol"], "centroid"):
        add("centroid", r["centroid_norm_tl"], c["centroid_norm_tl"],
            f"Placement shifted by {d:.4f} normalized canvas units.", delta=d)


def shape_checks(r, c, P, add, ctx):
    rr, cr = r.get("rect"), c.get("rect")
    if not rr:
        return False
    if cr is None:   # applicability is decided by the reference: R drew a frame, the candidate drew none
        add("shape_size", "frame", None, "Reference box has a frame; the candidate drew no frame shape.")
        return
    for name, key, akey in (("fill_color", "facecolor_hex", "face_alpha"), ("edge_color", "edgecolor_hex", "edge_alpha")):
        what = name.replace("_", " ")
        de = color_delta_e(rr[key], cr[key])
        if exceeds(de, P[name]["tol"], name):
            add(name, rr[key], cr[key], f"Box {what} {cr[key]} instead of {rr[key]} (ΔE={de}).", delta=de)
        da = round(abs(rr[akey] - cr[akey]), 4)
        if exceeds(da, P[name]["alpha_tol"], name):
            add(name, {"alpha": rr[akey]}, {"alpha": cr[akey]},
                f"Box {what} opacity {cr[akey]} instead of {rr[akey]}.", delta=da, sub="alpha")
    for dim in ("width", "height"):
        rv, cv = rr.get(f"unrotated_{dim}_pt"), cr.get(f"unrotated_{dim}_pt")
        if rv is None:
            continue
        if cv is None:
            add("shape_size", round(float(rv), 2), None, f"Candidate box {dim} not measurable.", sub=dim)
            continue
        rv, cv = float(rv), float(cv)
        tol = max(P["shape_size"]["tol"], P["shape_size"]["rel_tol"] * rv)
        if exceeds(abs(cv - rv), tol, "shape_size"):
            add("shape_size", round(rv, 2), round(cv, 2), f"Box {dim} {cv:.1f}pt instead of {rv:.1f}pt.",
                delta=round(cv - rv, 2), sub=dim)


# check name -> (function, the component key it needs). One function may serve several check names.
CORE_CHECKS: dict[str, tuple[Callable, str]] = {
    "text_color": (style_checks, "text_runs"), "text_alpha": (style_checks, "text_runs"),
    "font_weight": (style_checks, "text_runs"), "font_style": (style_checks, "text_runs"),
    "font_size": (style_checks, "text_runs"), "text_visible": (text_visible, "text_runs"),
    "ink_present": (ink_present, "text_runs"), "rotation": (rotation, "angle_deg"),
    "centroid": (centroid, "centroid_norm_tl"), "fill_color": (shape_checks, "rect"),
    "edge_color": (shape_checks, "rect"), "shape_size": (shape_checks, "rect"),
}


# ───────────────────────────── drawing ownership ─────────────────────────────

def account_for_drawing(obs: dict, package: str, assigned: set[int]) -> tuple[list[dict], dict]:
    """Every candidate draw event is accounted for (DESIGN.md §5, "Ownership"):
      - drawn by a top-level artist whose class is in the port package: must be assigned to a component;
      - drawn by any other top-level artist (plotnine/matplotlib: other layers, panel, axes, legends): permitted;
      - drawn outside every top-level artist: unexplained.
    Returns (unexplained events, summary)."""
    if "owners" not in obs:
        raise CheckError("candidate observations carry no draw-event owners (re-render with this plotparity version)")
    owners = obs["owners"]
    port = {i for i, o in enumerate(owners) if is_port_module(o["module"], package)}
    unexplained, other = [], {}
    n_port = n_port_assigned = n_unowned = 0
    for e in obs["events"]:
        k = e.get("owner")
        if k is None:
            n_unowned += 1
            unexplained.append(e)
        elif k in port:
            n_port += 1
            if e["paint_index"] in assigned:
                n_port_assigned += 1
            else:
                unexplained.append(e)
        else:
            cls = owners[k]["class"]
            other[cls] = other.get(cls, 0) + 1
    summary = {"port_events": n_port, "port_assigned": n_port_assigned, "unowned_events": n_unowned,
               "other_layer_events": sum(other.values()), "other_layer_owners": other}
    return unexplained, summary


def _describe_unexplained(events: list[dict], owners: list[dict]) -> str:
    kinds: dict[str, int] = {}
    for e in events:
        who = owners[e["owner"]]["class"] if e.get("owner") is not None else "no top-level artist"
        key = f"{e['kind']} by {who}"
        kinds[key] = kinds.get(key, 0) + 1
    return ", ".join(f"{n}× {k}" for k, n in sorted(kinds.items()))


# ───────────────────────────── known differences ─────────────────────────────

def load_known(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = tomllib.loads(path.read_text()).get("difference", [])
    for e in entries:
        bad = set(e) - {"case", "check", "component", "reason"}
        if bad or not {"case", "check", "reason"} <= set(e):
            raise ProfileError(f"known_differences.toml entry {e}: needs case, check, reason (+ optional component)")
    return entries


def _known_match(f: dict, known: list[dict]) -> int | None:
    """Index of the first known-difference entry matching the finding (exact entry, so unused ones are exact too)."""
    for i, k in enumerate(known):
        if k["case"] == f["case"] and k["check"] == f["check"] and fnmatch.fnmatchcase(f["component"], k.get("component", "*")):
            return i
    return None


def unused_known(known: list[dict], results: list[dict], case_ids: list[str]) -> list[dict]:
    """Entries for selected cases that matched no finding. Tracked per entry (case, check AND component glob), so
    a mistyped glob is reported even when a sibling entry for the same case and check is in use."""
    used = {f["known_entry"] for r in results for f in r["findings"] if f["status"] == "known_difference"}
    return [k for i, k in enumerate(known) if i not in used and k["case"] in case_ids]


# ───────────────────────────── evaluate one case ─────────────────────────────

def _load_png(path: str | None) -> np.ndarray | None:
    if not path or not Path(path).exists():
        return None
    from PIL import Image
    return np.asarray(Image.open(path).convert("RGB"))


def evaluate_case(case, adapter, profile: dict, known: list[dict], ref: dict, cand: dict, *, package: str,
                  split: str | None = None, canvas_pt: tuple[float, float] = (640.0, 480.0)) -> dict:
    """ref: {"observations": dict} or {"error": dict}; cand: the same, plus an optional "png" path.
    `package` is the port's import name: it decides which candidate draw events the port is responsible for."""
    if not package:
        raise ValueError("evaluate_case needs the port package name")
    findings: list[dict] = []
    outcomes: dict[str, dict[str, str]] = {}
    drawing: dict | None = None
    expect = case.expect

    def add(check, component, expected, observed, message, delta=None, status="fail"):
        P = profile.get(check, {"severity": "gating", "tier": "inventory"})
        if P["severity"] == "off":
            return
        findings.append({"case": case.id, "check": check, "component": component, "status": status,
                         "severity": "gating" if status == "infrastructure_error" else P["severity"],
                         "tier": P["tier"], "expected": expected, "observed": observed, "delta": delta,
                         "tol": P.get("tol"), "message": message})

    def ran(component, checks, applies=True):
        """Record that these checks ran on this component (outcome `pass` unless a finding says otherwise)."""
        for name in checks:
            off = profile.get(name, {}).get("severity") == "off"
            outcomes.setdefault(component, {})[name] = "off" if off else ("pass" if applies else "not_applicable")

    def finish():
        rank = {"pass": 0, "off": 0, "not_applicable": 0, "unmeasured": 1, "known_difference": 2, "fail": 3,
                "infrastructure_error": 3}
        for f in findings:
            f["fingerprint"] = f"{f['case']}|{f['check']}|{f['component']}"
            k = _known_match(f, known) if f["status"] == "fail" else None
            if k is not None:
                f["status"], f["known_reason"], f["known_entry"] = "known_difference", known[k]["reason"], k
            top = CASE if f["check"] in CASE_KEYED else f["component"].split("/")[0]
            if top in outcomes and f["check"] in outcomes[top]:
                cur = outcomes[top][f["check"]]
                if rank[f["status"]] >= rank[cur]:
                    outcomes[top][f["check"]] = f["status"]
        findings.sort(key=lambda f: (TIER_ORDER.get(f["tier"], 9), f["component"], f["check"]))
        gating = [f for f in findings if f["severity"] == "gating"]
        if any(f["status"] == "infrastructure_error" for f in findings):
            status = "infrastructure_error"
        elif any(f["status"] == "fail" for f in gating):
            status = "fail"
        elif any(f["status"] == "unmeasured" for f in gating):
            status = "unmeasured"
        else:
            status = "pass"
        res = {"case": case.id, "relpath": getattr(case, "relpath", case.id), "split": split, "status": status,
               "findings": findings, "outcomes": outcomes}
        if drawing is not None:
            res["drawing"] = drawing
        return res

    if "error" in ref:
        e = ref["error"]
        add(e.get("kind", "reference_error"), "reference", "reference render", None, e.get("message", ""),
            status="infrastructure_error")
        return finish()
    if "error" in cand and cand["error"].get("kind") == "needs_translation":
        add("needs_translation", "case", "a python block for this port", None, cand["error"]["message"],
            status="unmeasured")
        findings[-1]["tier"] = "inventory"
        return finish()
    if "error" in cand:
        e = cand["error"]
        kind = e.get("kind", "render_error")
        st = "fail" if kind in PORT_ERRORS else "infrastructure_error"
        add(kind, "render", "plot renders", f"{e.get('type')}: {e.get('message')}",
            f"[{e.get('stage', '?')}] {e.get('type')}: {e.get('message')}", status=st)
        findings[-1]["traceback"] = e.get("traceback", "")[-2500:]
        return finish()
    if not case.measurable:
        add("unmeasured_feature", "harness", "structured observation", None,
            f"Renders without error, but the adapter cannot extract this feature yet ({case.why}). Compare PNGs by eye.",
            status="unmeasured")
        findings[-1]["tier"] = "inventory"
        return finish()

    try:
        obs = cand["observations"]
        rc, _ = adapter.components("r", ref["observations"], expect, canvas_pt)
        cc, assigned = adapter.components("python", obs, expect, canvas_pt)
        if hasattr(adapter, "in_scope"):         # e.g. ggtext: theme elements only for roles rich in the reference
            cc = [c for c in cc if adapter.in_scope(c, rc)]
        for side, comps in (("reference", rc), ("candidate", cc)):
            for i, comp in enumerate(comps):
                assert_finite(comp, f"{side} component {i + 1}")
        unexplained, drawing = account_for_drawing(obs, package, assigned)
        ran(CASE, ("unexpected_drawing",))
        if unexplained:
            add("unexpected_drawing", "panel", 0, len(unexplained),
                f"{len(unexplained)} candidate draw call(s) not assigned to any component: "
                f"{_describe_unexplained(unexplained, obs['owners'])}. Every draw call made by the port's artists must "
                "belong to a component the adapter extracts.")
        if not rc:
            add("component_count", "panel", ">=1 reference component", 0,
                "Reference has no components; nothing to compare (fix the case or the reference).", status="unmeasured")
            return finish()
        ink = obs.get("port_ink_px")
        if not cc and ink != 0:
            # Nothing extracted, but the port's artists changed panel pixels (or we can't tell): the extractor is
            # blind to how this port draws. Never a pass; not blamed on the port either.
            ran(CASE, ("extractor_blind",))
            add("extractor_blind", "panel", f"{len(rc)} component(s)", {"port_ink_px": ink},
                f"No components extracted from the candidate, yet its artists drew {ink} panel pixel(s): the adapter "
                "cannot see how this port draws. Compare PNGs by eye; extend the adapter.", status="unmeasured")
            return finish()
        ran(CASE, ("component_count",))
        if len(cc) != len(rc):
            add("component_count", "panel", len(rc), len(cc),
                f"Component count mismatch: expected {len(rc)}, observed {len(cc)}.", delta=len(cc) - len(rc))
        pairs, missing, unexpected = adapter.match(rc, cc)
        ran(CASE, ("unexpected_component",))
        for i in missing:
            comp = adapter.component_id(i, rc[i])
            ran(comp, ("missing_component",))
            add("missing_component", comp, adapter.describe_component(rc[i]), None,
                f"Expected component {comp} is missing from the candidate output.")
        for j in unexpected:
            add("unexpected_component", f"unmatched_candidate[{j + 1}]", None, adapter.describe_component(cc[j]),
                f"Unexpected extra component in the candidate: {adapter.describe_component(cc[j])}.")
        png = _load_png(cand.get("png"))
        ctx = {"expect": expect, "png": png}
        for i, j in pairs:
            r, c = rc[i], cc[j]
            comp = adapter.component_id(i, r)

            def add_pair(check, expected, observed, message, delta=None, status="fail", sub=None):
                add(check, f"{comp}/{sub}" if sub else comp, expected, observed, message, delta, status)

            ran(comp, ("missing_component",))
            if "text_runs" in r:
                ran(comp, ("text_content",))
                text_content(r, c, profile, add_pair, ctx)
            served: dict[Callable, list[str]] = {}
            for name, (fn, needs) in CORE_CHECKS.items():
                if needs in r:
                    served.setdefault(fn, []).append(name)
            for fn, names in served.items():
                if all(profile[n]["severity"] == "off" for n in names):
                    ran(comp, names)
                else:
                    ran(comp, names, fn(r, c, profile, add_pair, ctx) is not False)
            for name, fn in adapter.CHECKS.items():
                if profile[name]["severity"] == "off":
                    ran(comp, (name,))
                else:
                    ran(comp, (name,), fn(r, c, profile[name], add_pair, expect) is not False)

        def add_case(check, expected, observed, message, delta=None, status="fail", sub=None):
            add(check, f"{CASE}/{sub}" if sub else CASE, expected, observed, message, delta, status)

        # whole-plot checks (e.g. paint order): fn(ref_components, cand_components, pairs, P, add, expect)
        for name, fn in getattr(adapter, "CASE_CHECKS", {}).items():
            if profile[name]["severity"] == "off":
                ran(CASE, (name,))
            else:
                ran(CASE, (name,), fn(rc, cc, pairs, profile[name], add_case, expect) is not False)
    except Exception as exc:  # noqa: BLE001  — a crashing check must never become a pass
        add("check_error", "comparator", None, f"{type(exc).__name__}: {exc}",
            f"Comparator raised {type(exc).__name__}: {exc}", status="infrastructure_error")
    return finish()
