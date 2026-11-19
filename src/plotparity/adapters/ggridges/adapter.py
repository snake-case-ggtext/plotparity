"""ggridges adapter: one component per ridge (geom_density_ridges, geom_ridgeline, stat_binline).

Both engines report the first panel's polygons and lines in paint order, in data units (observe.R, observe_py.py).
A shape belongs to the ridge whose baseline it sits on: discrete level i sits at y = i, a fill's lowest point is its
baseline, an outline belongs to the level just below its lowest point. Shapes the package draws that sit on no
level become their own component and are reported as unexpected. Ridges pair by level (never by drawn properties).

Positions are compared in data units and expressed in points with R's panel scale, so tolerances read as "visible or
not" and the ggplot2-vs-plotnine panel size difference cancels out. The built density curve (when the stat makes
one) is compared too: it names the cause when a ridge's shape differs.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from ...compare import color_delta_e, exceeds

NAME = "ggridges"
VERSION = "0.1.0"
HERE = Path(__file__).resolve().parent
OBSERVE_R = HERE / "observe.R"
PROFILE = HERE / "profile.toml"
EXPECT_KEYS: set[str] = set()


# ───────────────────────────── components ─────────────────────────────

def _top_curve(vd: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Upper boundary of a fill: highest y per distinct x."""
    order = np.argsort(vd[:, 0], kind="mergesort")
    xs, ys = vd[order, 0], vd[order, 1]
    ux, inv = np.unique(np.round(xs, 9), return_inverse=True)
    top = np.full(len(ux), -np.inf)
    np.maximum.at(top, inv, ys)
    return ux, top


def _eval_top(pieces: list[dict], xq: np.ndarray) -> np.ndarray:
    out = np.full(len(xq), np.nan)
    for p in pieces:
        m = (xq >= p["x_min"]) & (xq <= p["x_max"])
        if m.any():
            out[m] = np.interp(xq[m], p["top_x"], p["top_y"])
    return out


def components(engine: str, obs: dict[str, Any], expect: dict[str, Any],
               canvas_pt: tuple[float, float] = (640.0, 480.0)) -> tuple[list[dict], set[int]]:
    st = obs.get("ggridges") or {}
    events = st.get("events") or []
    labels = st.get("y_labels") or []
    xr, yr = st.get("panel_x_range") or [0, 1], st.get("panel_y_range") or [0, 1]
    # points per data unit, from this side's own panel (its bbox in normalised canvas units, times the canvas)
    norms = [np.asarray(p["norm"], float) for e in events for p in e["pieces"]]
    datas = [np.asarray(p["data"], float) for e in events for p in e["pieces"]]
    pt_x = pt_y = None
    if norms:
        n, d = np.vstack(norms), np.vstack(datas)
        if np.ptp(d[:, 0]) > 0 and np.ptp(d[:, 1]) > 0:
            pt_x = np.ptp(n[:, 0]) * canvas_pt[0] / np.ptp(d[:, 0])
            pt_y = np.ptp(n[:, 1]) * canvas_pt[1] / np.ptp(d[:, 1])
    n_levels = len(labels) or int(math.floor(yr[1]))
    ridges: dict[int, dict] = {}
    stray = []
    for ev in sorted(events, key=lambda e: e["paint_index"]):
        for pc in ev["pieces"]:
            vd = np.asarray(pc["data"], float)
            nv = np.asarray(pc["norm"], float)
            if ev["filled"]:
                base = float(vd[:, 1].min())
                pos = int(round(base))
                ok = 1 <= pos <= n_levels and abs(base - pos) < 0.05
            else:
                pos = int(math.floor(float(vd[:, 1].min()) + 1e-6))
                ok = 1 <= pos <= n_levels
            rec = {k: ev[k] for k in ("paint_index", "facecolor_hex", "face_alpha", "edgecolor_hex", "edge_alpha",
                                      "linewidth_pt")}
            rec.update(x_min=float(vd[:, 0].min()), x_max=float(vd[:, 0].max()),
                       y_min=float(vd[:, 1].min()), y_max=float(vd[:, 1].max()),
                       bbox_norm_tl=[float(nv[:, 0].min()), float(nv[:, 1].min()),
                                     float(nv[:, 0].max()), float(nv[:, 1].max())])
            if not ok:
                stray.append({"pos": None, "label": "unattributed", "fills": [], "strokes": [], "stray": rec,
                              "bbox_norm_tl": rec["bbox_norm_tl"]})
                continue
            r = ridges.setdefault(pos, {"pos": pos, "label": labels[pos - 1] if pos <= len(labels) else str(pos),
                                        "fills": [], "strokes": []})
            if ev["filled"]:
                rec["top_x"], rec["top_y"] = _top_curve(vd)
                r["fills"].append(rec)
            else:
                r["strokes"].append(rec)
    build = {b["pos"]: b for b in st.get("build") or []}
    comps = []
    for pos in sorted(ridges):
        r = ridges[pos]
        r["fills"].sort(key=lambda p: p["x_min"])
        r["strokes"].sort(key=lambda p: p["x_min"])
        f = r["fills"]
        r["baseline"] = min(p["y_min"] for p in f) if f else float("nan")
        r["peak"] = (max(p["y_max"] for p in f) - r["baseline"]) if f else float("nan")
        allp = f + r["strokes"]
        r["paint_first"] = min(p["paint_index"] for p in allp)
        r["bbox_norm_tl"] = [min(p["bbox_norm_tl"][0] for p in allp), min(p["bbox_norm_tl"][1] for p in allp),
                             max(p["bbox_norm_tl"][2] for p in allp), max(p["bbox_norm_tl"][3] for p in allp)]
        r["pt_x"], r["pt_y"], r["x_span"] = pt_x, pt_y, xr[1] - xr[0]
        b = build.get(pos)
        r["density"] = (np.asarray(b["x"], float), np.asarray(b["density"], float)) \
            if b and isinstance(b.get("density"), list) and len(b["density"]) == len(b["x"]) > 1 else None
        comps.append(r)
    return comps + stray, set()


def component_id(i: int, comp: dict) -> str:
    return f"ridge[{comp['label']}]"


def describe_component(comp: dict) -> dict:
    if comp.get("stray"):
        s = comp["stray"]
        return {"shape": "filled" if s["facecolor_hex"] != "none" else "line",
                "x": [round(s["x_min"], 3), round(s["x_max"], 3)], "y": [round(s["y_min"], 3), round(s["y_max"], 3)]}
    return {"ridge": comp["label"], "fills": len(comp["fills"]), "outlines": len(comp["strokes"])}


def match(ref: list[dict], cand: list[dict]) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Ridges pair by level; unattributed shapes never pair."""
    ci = {c["pos"]: j for j, c in enumerate(cand) if c.get("pos") is not None}
    pairs, missing = [], []
    for i, r in enumerate(ref):
        if r.get("pos") is not None and r["pos"] in ci:
            pairs.append((i, ci.pop(r["pos"])))
        else:
            missing.append(i)
    used = {j for _, j in pairs}
    return pairs, missing, [j for j in range(len(cand)) if j not in used]


# ───────────────────────────── checks ─────────────────────────────
# fn(ref, cand, P, add, expect); add(check, expected, observed, message, delta=None, status="fail", sub=None).
# Return False when the check does not apply to the pair.

def _both_filled(r, c) -> bool:
    return bool(r["fills"]) and bool(c["fills"])


def ridge_pieces(r, c, P, add, expect):
    """How many filled pieces make up the ridge (rel_min_height trims tails and can split a ridge)."""
    if len(r["fills"]) != len(c["fills"]):
        add("ridge_pieces", len(r["fills"]), len(c["fills"]),
            f"Drawn as {len(c['fills'])} filled piece(s), expected {len(r['fills'])} "
            "(tail trimming or splitting differs, or the fill is missing).", delta=len(c["fills"]) - len(r["fills"]))
    if len(r["strokes"]) != len(c["strokes"]):
        add("ridge_pieces", len(r["strokes"]), len(c["strokes"]),
            f"Drawn with {len(c['strokes'])} line(s) (outline, quantile lines…), expected {len(r['strokes'])}.",
            delta=len(c["strokes"]) - len(r["strokes"]), sub="lines")


def ridge_position(r, c, P, add, expect):
    """Baseline, height, x extent and top curve. Compared in data units and expressed in points with R's panel scale
    (plotnine lays out a slightly different panel than ggplot2 by design; that must not read as a taller ridge)."""
    if not _both_filled(r, c) or not (r["pt_x"] and r["pt_y"]):
        return False
    tol, kx, ky = P["tol"], r["pt_x"], r["pt_y"]
    d = abs(r["baseline"] - c["baseline"]) * ky
    if exceeds(d, tol, "ridge_position"):
        add("ridge_position", round(r["baseline"], 4), round(c["baseline"], 4),
            f"Baseline is {d:.1f}pt off.", delta=round(d, 2), sub="baseline")
    d = (c["peak"] - r["peak"]) * ky
    if exceeds(abs(d), tol, "ridge_position"):
        add("ridge_position", round(r["peak"] * ky, 1), round(c["peak"] * ky, 1),
            f"Peak height {c['peak'] * ky:.1f}pt, expected {r['peak'] * ky:.1f}pt.", delta=round(d, 2), sub="height")
    ra = [min(p["x_min"] for p in r["fills"]), max(p["x_max"] for p in r["fills"])]
    ca = [min(p["x_min"] for p in c["fills"]), max(p["x_max"] for p in c["fills"])]
    d = max(abs(ra[0] - ca[0]), abs(ra[1] - ca[1])) * kx
    if exceeds(d, tol, "ridge_position"):
        add("ridge_position", [round(v, 4) for v in ra], [round(v, 4) for v in ca],
            f"Ridge spans x {ca[0]:.4g}–{ca[1]:.4g}, expected {ra[0]:.4g}–{ra[1]:.4g} ({d:.1f}pt off).",
            delta=round(d, 2), sub="x_extent")
    xs = np.concatenate([p["top_x"] for p in r["fills"]])
    ta, tb = _eval_top(r["fills"], xs), _eval_top(c["fills"], xs)
    m = np.isfinite(ta) & np.isfinite(tb)
    if m.any():
        dev = np.abs((ta[m] - r["baseline"]) - (tb[m] - c["baseline"])) * ky
        k = int(np.argmax(dev))
        d = float(dev[k])
        if exceeds(d, tol, "ridge_position"):
            add("ridge_position", None, None,
                f"The ridge's top curve is up to {d:.1f}pt off (at x={xs[m][k]:.4g}).", delta=round(d, 2), sub="curve")


def ridge_fill(r, c, P, add, expect):
    if not _both_filled(r, c):
        return False
    fa, fb = r["fills"][0], c["fills"][0]
    de = color_delta_e(fa["facecolor_hex"], fb["facecolor_hex"])
    if exceeds(de, P["tol"], "ridge_fill"):
        add("ridge_fill", fa["facecolor_hex"], fb["facecolor_hex"],
            f"Fill {fb['facecolor_hex']}, expected {fa['facecolor_hex']} (ΔE {de}).", delta=de, sub="colour")
    d = abs(fa["face_alpha"] - fb["face_alpha"])
    if exceeds(d, P["alpha_tol"], "ridge_fill"):
        add("ridge_fill", fa["face_alpha"], fb["face_alpha"], f"Fill opacity {fb['face_alpha']}, expected "
            f"{fa['face_alpha']}.", delta=round(fb["face_alpha"] - fa["face_alpha"], 4), sub="alpha")
    rs, cs = fa["edgecolor_hex"] != "none", fb["edgecolor_hex"] != "none"
    if rs != cs:
        add("ridge_fill", "stroked" if rs else "unstroked", "stroked" if cs else "unstroked",
            "The filled area is " + ("not " if not cs else "") + "stroked; R " + ("strokes it." if rs else
            "draws an unstroked area and a separate top line, so a stroke also outlines the baseline and sides."),
            sub="stroke")


def ridge_outline(r, c, P, add, expect):
    if not (r["strokes"] and c["strokes"]):
        return False
    sa, sb = r["strokes"][0], c["strokes"][0]
    de = color_delta_e(sa["edgecolor_hex"], sb["edgecolor_hex"])
    if exceeds(de, P["tol"], "ridge_outline"):
        add("ridge_outline", sa["edgecolor_hex"], sb["edgecolor_hex"],
            f"Outline {sb['edgecolor_hex']}, expected {sa['edgecolor_hex']} (ΔE {de}).", delta=de, sub="colour")
    d = abs(sa["edge_alpha"] - sb["edge_alpha"])
    if exceeds(d, P["alpha_tol"], "ridge_outline"):
        add("ridge_outline", sa["edge_alpha"], sb["edge_alpha"], f"Outline opacity {sb['edge_alpha']}, expected "
            f"{sa['edge_alpha']}.", delta=round(sb["edge_alpha"] - sa["edge_alpha"], 4), sub="alpha")
    d = abs(sa["linewidth_pt"] - sb["linewidth_pt"]) / max(sa["linewidth_pt"], 1e-9)
    if exceeds(d, P["rel_tol"], "ridge_outline"):
        add("ridge_outline", sa["linewidth_pt"], sb["linewidth_pt"], f"Outline {sb['linewidth_pt']:.2f}pt wide, "
            f"expected {sa['linewidth_pt']:.2f}pt.", delta=round(sb["linewidth_pt"] - sa["linewidth_pt"], 3),
            sub="width")


def density_curve(r, c, P, add, expect):
    """The stat's density estimate, where both sides compute one (names the cause of a shape difference)."""
    if r.get("density") is None or c.get("density") is None:
        return False
    (ax, ad), (bx, bd) = r["density"], c["density"]
    peak = float(np.max(ad))
    if peak <= 0:
        return False
    lo, hi = max(ax.min(), bx.min()), min(ax.max(), bx.max())
    m = (ax >= lo) & (ax <= hi)
    if m.sum() < 2:
        add("density_curve", [ax.min(), ax.max()], [bx.min(), bx.max()], "The density ranges don't overlap.")
        return
    dev = np.abs(np.interp(ax[m], bx, bd) - ad[m]) / peak
    k = int(np.argmax(dev))
    if exceeds(float(dev[k]), P["rel_tol"], "density_curve"):
        add("density_curve", round(float(ad[m][k]), 5), round(float(np.interp(ax[m][k], bx, bd)), 5),
            f"The stat's density differs by up to {dev[k]:.1%} of the peak (at x={ax[m][k]:.4g}).",
            delta=round(float(dev[k]), 4))


CHECKS = {f.__name__: f for f in (ridge_pieces, ridge_position, ridge_fill, ridge_outline, density_curve)}


def paint_order(rc, cc, pairs, P, add, expect):
    """Which of two overlapping neighbouring ridges is drawn on top. ggridges draws the top ridge first, so a
    lower ridge covers the one above where they overlap. Only overlapping pairs are compared (elsewhere the
    order is invisible)."""
    by_pos = {rc[i]["pos"]: (rc[i], cc[j]) for i, j in pairs if rc[i].get("pos") is not None}
    checked = False
    for pos in sorted(by_pos):
        if pos + 1 not in by_pos:
            continue
        (rl, cl), (ru, cu) = by_pos[pos], by_pos[pos + 1]
        if not (rl["fills"] and ru["fills"] and cl["fills"] and cu["fills"]):
            continue
        xs = np.unique(np.concatenate([p["top_x"] for p in rl["fills"]]))
        overlap = np.nanmax(_eval_top(rl["fills"], xs), initial=-np.inf) > ru["baseline"] + 1e-3
        if not overlap:
            continue
        checked = True
        r_lower_on_top = rl["paint_first"] > ru["paint_first"]
        c_lower_on_top = cl["paint_first"] > cu["paint_first"]
        if r_lower_on_top != c_lower_on_top:
            on_top = lambda low: rl["label"] if low else ru["label"]       # noqa: E731
            add("paint_order", f"{on_top(r_lower_on_top)} on top", f"{on_top(c_lower_on_top)} on top",
                f"Where ridges {rl['label']} and {ru['label']} overlap, {on_top(c_lower_on_top)} is drawn on top; "
                f"R draws {on_top(r_lower_on_top)} on top.", sub=f"{rl['label']}/{ru['label']}")
    return checked or False


CASE_CHECKS = {"paint_order": paint_order}
