"""ggbeeswarm adapter: one component per (panel, category group) point swarm.

Supports both vertical (discrete x, continuous y) and horizontal (`coord_flip()` / discrete y, continuous x)
swarms, as well as multi-panel facets. Positions are compared in data units scaled by R's panel points-per-unit.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import wasserstein_distance

from ...compare import color_delta_e, exceeds

NAME = "ggbeeswarm"
VERSION = "0.1.0"
HERE = Path(__file__).resolve().parent
OBSERVE_R = HERE / "observe.R"
PROFILE = HERE / "profile.toml"
EXPECT_KEYS: set[str] = set()


def _overlap_fraction(pts: list[dict], kx: float, ky: float) -> float:
    n = len(pts)
    if n < 2:
        return 0.0
    xs = np.array([p["x"] * kx for p in pts], float)
    ys = np.array([p["y"] * ky for p in pts], float)
    diam = float(np.median([p["size_pt"] for p in pts]))
    thresh = 0.75 * diam
    if thresh <= 0:
        return 0.0
    dx = xs[:, None] - xs[None, :]
    dy = ys[:, None] - ys[None, :]
    d = np.hypot(dx, dy)
    np.fill_diagonal(d, np.inf)
    return float((d.min(axis=1) < thresh).mean())


def components(engine: str, obs: dict[str, Any], expect: dict[str, Any],
               canvas_pt: tuple[float, float] = (640.0, 480.0)) -> tuple[list[dict], set[int]]:
    st = obs.get("ggbeeswarm") or {}
    panels = st.get("panels") or []
    comps = []
    for panel in panels:
        pidx = int(panel.get("panel_index", 1))
        xr = panel.get("x_range") or [0.0, 1.0]
        yr = panel.get("y_range") or [0.0, 1.0]
        bb = panel.get("bbox_norm_tl") or [0.0, 0.0, 1.0, 1.0]
        kx = (bb[2] - bb[0]) * canvas_pt[0] / max(xr[1] - xr[0], 1e-9)
        ky = (bb[3] - bb[1]) * canvas_pt[1] / max(yr[1] - yr[0], 1e-9)
        pts = panel.get("points") or []
        if not pts:
            continue
        x_disc = bool(panel.get("x_is_discrete", True))
        y_disc = bool(panel.get("y_is_discrete", False))
        # Determine categorical (group) axis vs continuous (value) axis
        if y_disc and not x_disc:
            cat_axis, val_axis = "y", "x"
            labels = panel.get("y_labels") or []
            k_cat, k_val = ky, kx
        elif x_disc:
            cat_axis, val_axis = "x", "y"
            labels = panel.get("x_labels") or []
            k_cat, k_val = kx, ky
        else:
            cat_axis, val_axis = "x", "y"
            labels = []
            k_cat, k_val = kx, ky

        n_levels = len(labels)
        grouped: dict[int, list[dict]] = {}
        for pt in pts:
            if n_levels > 0:
                pos = max(1, min(n_levels, int(round(pt[cat_axis]))))
            else:
                pos = 1
            grouped.setdefault(pos, []).append(pt)

        for pos in sorted(grouped):
            gpts = grouped[pos]
            label = labels[pos - 1] if 1 <= pos <= len(labels) else str(pos)
            cat_vals = [float(p[cat_axis]) for p in gpts]
            val_vals = sorted(float(p[val_axis]) for p in gpts)
            cat_offsets = sorted(c - pos for c in cat_vals)
            xns = [float(p["x_norm"]) for p in gpts]
            yns = [float(p["y_norm"]) for p in gpts]
            pad = 0.01
            comps.append({
                "panel": pidx,
                "pos": pos,
                "label": label,
                "multi_panel": len(panels) > 1,
                "cat_axis": cat_axis,
                "n": len(gpts),
                "values": val_vals,
                "cat_offsets": cat_offsets,
                "cat_mean": round(float(np.mean(cat_vals)), 6),
                "cat_half_width": round(float(max(abs(o) for o in cat_offsets)), 6),
                "cat_iqr": round(float(np.percentile(cat_offsets, 75) - np.percentile(cat_offsets, 25)), 6),
                "k_cat": round(k_cat, 6),
                "k_val": round(k_val, 6),
                "overlap_frac": round(_overlap_fraction(gpts, kx, ky), 4),
                "colors": sorted({p["color_hex"] for p in gpts}),
                "median_alpha": round(float(np.median([p["alpha"] for p in gpts])), 4),
                "median_size_pt": round(float(np.median([p["size_pt"] for p in gpts])), 3),
                "bbox_norm_tl": [round(max(0.0, min(xns) - pad), 5), round(max(0.0, min(yns) - pad), 5),
                                 round(min(1.0, max(xns) + pad), 5), round(min(1.0, max(yns) + pad), 5)],
            })
    extracted = set(st.get("extracted_classes") or [])
    owners = obs.get("owners") or []
    assigned = {e["paint_index"] for e in obs.get("events") or []
                if e.get("owner") is not None and owners[e["owner"]]["class"] in extracted} if extracted else set()
    return comps, assigned


def component_id(i: int, comp: dict) -> str:
    if comp.get("multi_panel"):
        return f"swarm[p{comp['panel']}:{comp['label']}]"
    return f"swarm[{comp['label']}]"


def describe_component(comp: dict) -> dict:
    return {"panel": comp["panel"], "group": comp["label"], "n": comp["n"]}


def match(ref: list[dict], cand: list[dict]) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    ci = {(c["panel"], c["pos"]): j for j, c in enumerate(cand)}
    pairs, missing = [], []
    for i, r in enumerate(ref):
        key = (r["panel"], r["pos"])
        if key in ci:
            pairs.append((i, ci.pop(key)))
        else:
            missing.append(i)
    used = {j for _, j in pairs}
    return pairs, missing, [j for j in range(len(cand)) if j not in used]


# ───────────────────────────── checks ─────────────────────────────

def point_count(r, c, P, add, expect):
    if r["n"] != c["n"]:
        add("point_count", r["n"], c["n"],
            f"Swarm has {c['n']} point(s), expected {r['n']}.", delta=c["n"] - r["n"])


def value_axis(r, c, P, add, expect):
    if r["n"] != c["n"] or r["n"] == 0:
        return False
    kv = r["k_val"]
    diffs = np.abs(np.array(r["values"]) - np.array(c["values"])) * kv
    d = float(np.max(diffs))
    if exceeds(d, P["tol"], "value_axis"):
        add("value_axis", round(r["values"][int(np.argmax(diffs))], 4),
            round(c["values"][int(np.argmax(diffs))], 4),
            f"Continuous-axis values shifted by up to {d:.2f}pt.", delta=round(d, 2))


def swarm_center(r, c, P, add, expect):
    d = (c["cat_mean"] - r["cat_mean"]) * r["k_cat"]
    if exceeds(abs(d), P["tol"], "swarm_center"):
        add("swarm_center", round(r["cat_mean"], 4), round(c["cat_mean"], 4),
            f"Swarm mean offset along {r['cat_axis']} is {abs(d):.1f}pt off.", delta=round(d, 2))


def swarm_spread(r, c, P, add, expect):
    kc = r["k_cat"]
    # Max half-width (in pt, using R's scale)
    rh, ch = r["cat_half_width"] * kc, c["cat_half_width"] * kc
    tol = max(P["tol"], P.get("rel_tol", 0.0) * rh)
    if exceeds(abs(ch - rh), tol, "swarm_spread"):
        add("swarm_spread", round(rh, 1), round(ch, 1),
            f"Swarm half-width is {ch:.1f}pt, expected {rh:.1f}pt.", delta=round(ch - rh, 2), sub="half_width")
    ri, ci = r["cat_iqr"] * kc, c["cat_iqr"] * kc
    tol_i = max(P["tol"], P.get("rel_tol", 0.0) * ri)
    if exceeds(abs(ci - ri), tol_i, "swarm_spread"):
        add("swarm_spread", round(ri, 1), round(ci, 1),
            f"Swarm IQR width is {ci:.1f}pt, expected {ri:.1f}pt.", delta=round(ci - ri, 2), sub="iqr")


def swarm_distribution(r, c, P, add, expect):
    if not r["cat_offsets"] or not c["cat_offsets"]:
        return False
    kc = r["k_cat"]
    w1 = float(wasserstein_distance(r["cat_offsets"], c["cat_offsets"])) * kc
    tol = max(P["tol"], P.get("rel_tol", 0.0) * (r["cat_half_width"] * kc))
    if exceeds(w1, tol, "swarm_distribution"):
        add("swarm_distribution", f"<= {tol:.1f}pt", round(w1, 2),
            f"1D Wasserstein distance of swarm offsets is {w1:.2f}pt (limit {tol:.1f}pt).", delta=round(w1, 2))


def point_overlap(r, c, P, add, expect):
    # Only gate overlap when R itself separates the points (< 10% overlap)
    if r["overlap_frac"] > 0.10:
        return False
    d = c["overlap_frac"] - r["overlap_frac"]
    if exceeds(d, P["tol"], "point_overlap"):
        add("point_overlap", round(r["overlap_frac"], 3), round(c["overlap_frac"], 3),
            f"{c['overlap_frac']:.0%} of points overlap closely vs {r['overlap_frac']:.0%} in R.",
            delta=round(d, 3))


def point_style(r, c, P, add, expect):
    # Compare colour set (paired by sorted order if same length, else set diff)
    rc, cc = r["colors"], c["colors"]
    if len(rc) != len(cc):
        add("point_style", rc, cc, f"Palette size differs: expected {rc}, observed {cc}.", sub="colour")
    else:
        worst = max((color_delta_e(a, b) for a, b in zip(rc, cc)), default=0.0)
        if exceeds(worst, P["tol"], "point_style"):
            add("point_style", rc, cc, f"Point colour differs (max ΔE {worst}).", delta=worst, sub="colour")
    da = abs(c["median_alpha"] - r["median_alpha"])
    if exceeds(da, P["alpha_tol"], "point_style"):
        add("point_style", r["median_alpha"], c["median_alpha"],
            f"Point opacity {c['median_alpha']}, expected {r['median_alpha']}.",
            delta=round(c["median_alpha"] - r["median_alpha"], 4), sub="alpha")
    ds = abs(c["median_size_pt"] - r["median_size_pt"])
    size_tol = max(1.0, P.get("rel_tol", 0.15) * r["median_size_pt"])
    if exceeds(ds, size_tol, "point_style"):
        add("point_style", r["median_size_pt"], c["median_size_pt"],
            f"Point diameter {c['median_size_pt']:.2f}pt, expected {r['median_size_pt']:.2f}pt.",
            delta=round(c["median_size_pt"] - r["median_size_pt"], 2), sub="size")


CHECKS = {f.__name__: f for f in (point_count, value_axis, swarm_center, swarm_spread,
                                   swarm_distribution, point_overlap, point_style)}
