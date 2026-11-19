"""Core comparison: profile merge, core checks, fail-closed rules, known differences, statuses.

A finding: {case, check, component, status, severity, tier, expected, observed, delta, tol, message, fingerprint}
  status   : fail | unmeasured | known_difference | infrastructure_error
  severity : gating | advisory (from the profile)
Case status order: infrastructure_error > fail (any gating fail) > unmeasured > pass.
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

from .engines.python.probe import hex_to_rgb01

CASE_LEVEL = ("component_count", "missing_component", "unexpected_component", "unexpected_drawing", "text_content")
TIER_ORDER = {"inventory": 0, "layout": 1, "style": 2}
PORT_ERRORS = {"render_error", "missing_api", "render_timeout"}
INFRA_ERRORS = {"environment_error", "case_error", "reference_error", "stale_reference", "font_drift", "check_error"}


class ProfileError(ValueError):
    pass


# ───────────────────────────── profile ─────────────────────────────

PROFILE_KEYS = {"severity", "tier", "tol", "rel_tol", "calibration"}


def load_profile(adapter, project_overrides: dict[str, dict]) -> dict[str, dict]:
    base = tomllib.loads(Path(adapter.PROFILE).read_text())["checks"]
    known = set(CORE_CHECKS) | set(CASE_LEVEL) | set(adapter.CHECKS)
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
    return merged


# ───────────────────────────── core pair checks ─────────────────────────────

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


def style_checks(r, c, P, add, expect):
    pairs = _aligned_runs(r, c)
    if pairs is None:
        if r["text_runs"]:
            for name in ("text_color", "font_weight", "font_style", "font_size"):
                add(name, "aligned text runs", None, "Text runs could not be aligned by content; style unmeasured.",
                    status="unmeasured")
        return
    for k, (rt, ct) in enumerate(pairs):
        sub = f"run[{k}:{rt['text']!r}]"
        de = color_delta_e(rt["color_hex"], ct["color_hex"])
        if de > P["text_color"]["tol"]:
            add("text_color", rt["color_hex"], ct["color_hex"],
                f"Span {rt['text']!r} rendered with color {ct['color_hex']} instead of {rt['color_hex']} (ΔE={de}).",
                delta=de, sub=sub)
        if rt["fontweight"] != ct["fontweight"]:
            add("font_weight", rt["fontweight"], ct["fontweight"],
                f"Span {rt['text']!r} rendered with weight {ct['fontweight']!r} instead of {rt['fontweight']!r}.", sub=sub)
        if rt["fontstyle"] != ct["fontstyle"]:
            add("font_style", rt["fontstyle"], ct["fontstyle"],
                f"Span {rt['text']!r} rendered with style {ct['fontstyle']!r} instead of {rt['fontstyle']!r}.", sub=sub)
        d = round(abs(rt["fontsize_pt"] - ct["fontsize_pt"]), 3)
        if d > P["font_size"]["tol"]:
            add("font_size", rt["fontsize_pt"], ct["fontsize_pt"],
                f"Span {rt['text']!r} font size {ct['fontsize_pt']}pt instead of {rt['fontsize_pt']}pt.", delta=d, sub=sub)


def rotation(r, c, P, add, expect):
    d = round(abs((r["angle_deg"] - c["angle_deg"] + 180.0) % 360.0 - 180.0), 3)
    if d > P["rotation"]["tol"]:
        add("rotation", r["angle_deg"], c["angle_deg"],
            f"Rotation {c['angle_deg']}° instead of {r['angle_deg']}° (diff {d}°).", delta=d)


def centroid(r, c, P, add, expect):
    d = round(math.hypot(r["centroid_norm_tl"][0] - c["centroid_norm_tl"][0],
                         r["centroid_norm_tl"][1] - c["centroid_norm_tl"][1]), 4)
    if d > P["centroid"]["tol"]:
        add("centroid", r["centroid_norm_tl"], c["centroid_norm_tl"],
            f"Placement shifted by {d:.4f} normalized canvas units.", delta=d)


def shape_checks(r, c, P, add, expect):
    rr, cr = r.get("rect"), c.get("rect")
    if not (expect.get("frame", True) and rr):
        return
    if cr is None:   # applicability is decided by the reference: R drew a frame, the candidate drew none
        add("shape_size", "frame", None, "Reference box has a frame; the candidate drew no frame shape.")
        return
    for name, key in (("fill_color", "facecolor_hex"), ("edge_color", "edgecolor_hex")):
        de = color_delta_e(rr[key], cr[key])
        if de > P[name]["tol"]:
            add(name, rr[key], cr[key], f"Box {name.replace('_', ' ')} {cr[key]} instead of {rr[key]} (ΔE={de}).", delta=de)
    for dim in ("width", "height"):
        rv, cv = rr.get(f"unrotated_{dim}_pt"), cr.get(f"unrotated_{dim}_pt")
        if rv is None:
            continue
        if cv is None:
            add("shape_size", round(float(rv), 2), None, f"Candidate box {dim} not measurable.", sub=dim)
            continue
        rv, cv = float(rv), float(cv)
        tol = max(P["shape_size"]["tol"], P["shape_size"]["rel_tol"] * rv)
        if abs(cv - rv) > tol:
            add("shape_size", round(rv, 2), round(cv, 2), f"Box {dim} {cv:.1f}pt instead of {rv:.1f}pt.",
                delta=round(cv - rv, 2), sub=dim)


CORE_CHECKS: dict[str, Callable] = {}
for _name, _fn in (("text_color", style_checks), ("font_weight", style_checks), ("font_style", style_checks),
                   ("font_size", style_checks), ("rotation", rotation), ("centroid", centroid),
                   ("fill_color", shape_checks), ("edge_color", shape_checks), ("shape_size", shape_checks)):
    CORE_CHECKS[_name] = _fn


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


def _known_match(f: dict, known: list[dict]) -> dict | None:
    for k in known:
        if k["case"] == f["case"] and k["check"] == f["check"] and fnmatch.fnmatchcase(f["component"], k.get("component", "*")):
            return k
    return None


# ───────────────────────────── evaluate one case ─────────────────────────────

def evaluate_case(case, adapter, profile: dict, known: list[dict], ref: dict, cand: dict, split: str | None = None) -> dict:
    """ref/cand: {"observations": dict} or {"error": dict}. Returns case result with findings + status."""
    findings: list[dict] = []
    expect = case.expect

    def add(check, component, expected, observed, message, delta=None, status="fail"):
        P = profile.get(check, {"severity": "gating", "tier": "inventory"})
        if P["severity"] == "off":
            return
        findings.append({"case": case.id, "check": check, "component": component, "status": status,
                         "severity": "gating" if status == "infrastructure_error" else P["severity"],
                         "tier": P["tier"], "expected": expected, "observed": observed, "delta": delta,
                         "tol": P.get("tol"), "message": message})

    def finish():
        for f in findings:
            f["fingerprint"] = f"{f['case']}|{f['check']}|{f['component']}"
            k = _known_match(f, known) if f["status"] == "fail" else None
            if k:
                f["status"], f["known_reason"] = "known_difference", k["reason"]
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
        return {"case": case.id, "relpath": getattr(case, "relpath", case.id), "split": split, "status": status,
                "findings": findings}

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
        rc, _ = adapter.components("r", ref["observations"], expect)
        cc, unassigned = adapter.components("python", cand["observations"], expect)
        if unassigned:
            routes = sorted({e["kind"] for e in unassigned})
            add("unexpected_drawing", "panel.richtext", 0, len(unassigned),
                f"{len(unassigned)} candidate draw call(s) via {routes} that the adapter cannot assign to a component.")
        if not rc:
            add("component_count", "panel", ">=1 reference component", 0,
                "Reference has no components; nothing to compare (fix the case or the reference).", status="unmeasured")
            return finish()
        if len(cc) != len(rc):
            add("component_count", "panel", len(rc), len(cc),
                f"Component count mismatch: expected {len(rc)}, observed {len(cc)}.", delta=len(cc) - len(rc))
        pairs, missing, unexpected = adapter.match(rc, cc)
        for i in missing:
            add("missing_component", f"box[ref_{i + 1}]", {"text": rc[i]["full_text"]}, None,
                f"Expected box {i + 1} ({rc[i]['full_text']!r}) is missing from the candidate output.")
        for j in unexpected:
            add("unexpected_component", f"box[unmatched_cand_{j + 1}]", None,
                {"text": cc[j]["full_text"], "centroid_norm_tl": cc[j]["centroid_norm_tl"]},
                f"Unexpected extra box at {cc[j]['centroid_norm_tl']} with text {cc[j]['full_text']!r}.")
        for i, j in pairs:
            r, c = rc[i], cc[j]
            comp = adapter.component_id(i, r)

            def add_pair(check, expected, observed, message, delta=None, status="fail", sub=None):
                add(check, f"{comp}/{sub}" if sub else comp, expected, observed, message, delta, status)

            if r["words"] != c["words"]:
                add_pair("text_content", r["words"], c["words"], "Word sequence differs from the reference.")
            for fn in dict.fromkeys(CORE_CHECKS.values()):
                fn(r, c, profile, add_pair, expect)
            for name, fn in adapter.CHECKS.items():
                fn(r, c, profile[name], add_pair, expect)
        for f in findings:
            for v in (f["delta"], f["observed"]):
                if isinstance(v, float) and math.isnan(v):
                    f["status"], f["check"] = "infrastructure_error", "check_error"
    except Exception as exc:  # noqa: BLE001  — a crashing check must never become a pass
        add("check_error", "comparator", None, f"{type(exc).__name__}: {exc}",
            f"Comparator raised {type(exc).__name__}: {exc}", status="infrastructure_error")
    return finish()
