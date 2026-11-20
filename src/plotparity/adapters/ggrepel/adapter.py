"""ggrepel adapter: repelled text/label annotations with leader segments and collision invariants.

Unlike exact-coordinate geoms, force-directed label repulsion differs slightly across solvers.
So this adapter combines:
  - exact parity on label inventory, text content, label box presence/rounding/style, text style,
    and leader-segment presence;
  - geometric invariants (whole-plot & per-label): no label-label overlap, no label-point overlap,
    labels stay inside their panel, and leader segments connect their label box to an anchor point.

Python labels/boxes/segments are extracted directly from probe.py's draw calls (`obs["events"]`),
proving that draw-time layout artists need zero capture-renderer hacks.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ...compare import color_delta_e, exceeds
from ...engines.python.probe import point_in_bbox

NAME = "ggrepel"
VERSION = "0.1.0"
HERE = Path(__file__).resolve().parent
OBSERVE_R = HERE / "observe.R"
PROFILE = HERE / "profile.toml"
EXPECT_KEYS: set[str] = set()


def _bbox_center(bb: list[float]) -> tuple[float, float]:
    return 0.5 * (bb[0] + bb[2]), 0.5 * (bb[1] + bb[3])


def _point_to_bbox_pt(px: float, py: float, bb: list[float], canvas_pt: tuple[float, float]) -> float:
    dx = max(bb[0] - px, 0.0, px - bb[2]) * canvas_pt[0]
    dy = max(bb[1] - py, 0.0, py - bb[3]) * canvas_pt[1]
    return math.hypot(dx, dy)


def _boxes_overlap_pt(a: list[float], b: list[float], canvas_pt: tuple[float, float], pad_pt: float = 0.0) -> bool:
    W, H = canvas_pt
    ax0, ay0, ax1, ay1 = a[0] * W - pad_pt, a[1] * H - pad_pt, a[2] * W + pad_pt, a[3] * H + pad_pt
    bx0, by0, bx1, by1 = b[0] * W, b[1] * H, b[2] * W, b[3] * H
    return (ax0 < bx1) and (ax1 > bx0) and (ay0 < by1) and (ay1 > by0)


def _panel_for(cx: float, cy: float, panels: list[dict]) -> dict:
    for p in panels:
        if point_in_bbox(cx, cy, p["bbox_norm_tl"], pad=0.02):
            return p
    if panels:
        return min(panels, key=lambda p: math.hypot(cx - 0.5 * (p["bbox_norm_tl"][0] + p["bbox_norm_tl"][2]),
                                                    cy - 0.5 * (p["bbox_norm_tl"][1] + p["bbox_norm_tl"][3])))
    return {"panel_index": 1, "bbox_norm_tl": [0.0, 0.0, 1.0, 1.0], "points": []}


def _extract_python(obs: dict[str, Any], panels: list[dict], canvas_pt: tuple[float, float]) -> tuple[list[dict], set[int]]:
    """Extract repelled text labels, optional closed boxes, and open 2-point leader segments from probe events."""
    events = obs.get("events") or []
    owners = obs.get("owners") or []
    assigned: set[int] = set()

    # Identify draw calls made inside panel axes (excluding theme roles) that belong to non-matplotlib/non-plotnine
    # artists OR text/path events inside repel groups.
    def is_panel_repel_event(ev: dict) -> bool:
        if "role" in ev:
            return False
        k = ev.get("owner")
        if k is None or k >= len(owners):
            return False
        mod = owners[k].get("module", "")
        if not mod.startswith(("matplotlib.", "plotnine.")):
            return True
        return False

    repel_evs = [e for e in events if is_panel_repel_event(e)]
    # Fallback: if a port uses stock matplotlib Text/FancyBboxPatch inside axes, pick in-panel texts/paths
    if not repel_evs and panels:
        for e in events:
            if "role" in e or "axes" not in e.get("groups", []):
                continue
            if e["kind"] == "text" and any(point_in_bbox(e["x_norm"], e["y_norm"], p["bbox_norm_tl"], pad=0.05) for p in panels):
                repel_evs.append(e)

    texts = []
    closed_paths = []
    open_segs = []
    for ev in repel_evs:
        kind = ev["kind"]
        if kind == "text" and str(ev.get("text", "")).strip():
            texts.append(ev)
            assigned.add(ev["paint_index"])
        elif kind == "path":
            verts = ev.get("vertices_norm_tl") or []
            if ev.get("is_closed") or len(verts) >= 4:
                closed_paths.append(ev)
                assigned.add(ev["paint_index"])
            elif len(verts) == 2 and ev.get("edgecolor_hex", "none") != "none":
                open_segs.append(ev)
                assigned.add(ev["paint_index"])

    used_rects: set[int] = set()
    used_segs: set[int] = set()
    comps = []
    for te in texts:
        tbb = te["bbox_norm_tl"]
        tx, ty = _bbox_center(tbb)
        # Pair enclosing closed path (label box)
        rect = None
        best_r = None
        for ri, pe in enumerate(closed_paths):
            if ri in used_rects:
                continue
            pbb = pe["bbox_norm_tl"]
            if pbb[0] <= tx <= pbb[2] and pbb[1] <= ty <= pbb[3]:
                area = (pbb[2] - pbb[0]) * (pbb[3] - pbb[1])
                if best_r is None or area < best_r[0]:
                    best_r = (area, ri, pe)
        if best_r is not None:
            _, ri, pe = best_r
            used_rects.add(ri)
            rect = {
                "bbox_norm_tl": pe["bbox_norm_tl"],
                "is_rounded": bool(pe.get("is_rounded", False)),
                "facecolor_hex": pe.get("facecolor_hex", "none"),
                "face_alpha": float(pe.get("face_alpha", 0.0)),
                "edgecolor_hex": pe.get("edgecolor_hex", "none"),
                "edge_alpha": float(pe.get("edge_alpha", 0.0)),
                "linewidth_pt": float(pe.get("linewidth_pt", 0.0)),
            }

        box_bb = rect["bbox_norm_tl"] if rect else tbb
        # Pair nearest leader segment with an endpoint close to this box
        seg = None
        best_s = None
        for si, se in enumerate(open_segs):
            if si in used_segs:
                continue
            v0, v1 = se["vertices_norm_tl"][0], se["vertices_norm_tl"][-1]
            d0 = _point_to_bbox_pt(v0[0], v0[1], box_bb, canvas_pt)
            d1 = _point_to_bbox_pt(v1[0], v1[1], box_bb, canvas_pt)
            dmin = min(d0, d1)
            if dmin <= 12.0 and (best_s is None or dmin < best_s[0]):
                best_s = (dmin, si, se)
        if best_s is not None:
            _, si, se = best_s
            used_segs.add(si)
            v0, v1 = se["vertices_norm_tl"][0], se["vertices_norm_tl"][-1]
            seg = {
                "x0_norm": float(v0[0]), "y0_norm": float(v0[1]),
                "x1_norm": float(v1[0]), "y1_norm": float(v1[1]),
                "color_hex": se.get("edgecolor_hex", "#000000"),
                "alpha": float(se.get("edge_alpha", 1.0)),
            }

        panel = _panel_for(tx, ty, panels)
        comps.append({
            "panel": panel["panel_index"],
            "panel_bbox_norm_tl": panel["bbox_norm_tl"],
            "panel_points": panel["points"],
            "multi_panel": len(panels) > 1,
            "text": str(te["text"]).strip(),
            "bbox_norm_tl": [round(float(v), 6) for v in box_bb],
            "text_bbox_norm_tl": [round(float(v), 6) for v in tbb],
            "color_hex": te.get("color_hex", "#000000"),
            "alpha": float(te.get("alpha", 1.0)),
            "fontsize_pt": float(te.get("fontsize_pt", 11.0)),
            "fontweight": str(te.get("fontweight", "normal")).lower(),
            "fontstyle": str(te.get("fontstyle", "normal")).lower(),
            "angle_deg": float(te.get("angle_deg", 0.0)),
            "rect": rect,
            "segment": seg,
            "canvas_pt": list(canvas_pt),
        })
    return comps, assigned


def components(engine: str, obs: dict[str, Any], expect: dict[str, Any],
               canvas_pt: tuple[float, float] = (640.0, 480.0)) -> tuple[list[dict], set[int]]:
    st = obs.get("ggrepel") or {}
    panels = st.get("panels") or []
    if engine == "python":
        return _extract_python(obs, panels, canvas_pt)
    if engine != "r":
        raise ValueError(f"ggrepel adapter has no extractor for engine {engine!r}")
    by_pi = {p["panel_index"]: p for p in panels}
    comps = []
    for raw in st.get("labels") or []:
        pi = int(raw.get("panel", 1))
        panel = by_pi.get(pi, {"panel_index": pi, "bbox_norm_tl": [0.0, 0.0, 1.0, 1.0], "points": []})
        rect = raw.get("rect") or None
        tbb = [float(v) for v in raw["bbox_norm_tl"]]
        box_bb = [float(v) for v in rect["bbox_norm_tl"]] if (rect and rect.get("bbox_norm_tl")) else tbb
        comps.append({
            "panel": pi,
            "panel_bbox_norm_tl": panel["bbox_norm_tl"],
            "panel_points": panel["points"],
            "multi_panel": len(panels) > 1,
            "text": str(raw["text"]).strip(),
            "bbox_norm_tl": [round(v, 6) for v in box_bb],
            "text_bbox_norm_tl": [round(v, 6) for v in tbb],
            "color_hex": raw.get("color_hex", "#000000"),
            "alpha": float(raw.get("alpha", 1.0)),
            "fontsize_pt": float(raw.get("fontsize_pt", 11.0)),
            "fontweight": str(raw.get("fontweight", "normal")).lower(),
            "fontstyle": str(raw.get("fontstyle", "normal")).lower(),
            "angle_deg": float(raw.get("angle_deg", 0.0)),
            "rect": rect,
            "segment": raw.get("segment") or None,
            "canvas_pt": list(canvas_pt),
        })
    return comps, set()


def component_id(i: int, comp: dict) -> str:
    prefix = f"p{comp['panel']}:" if comp.get("multi_panel") else ""
    return f"label[{prefix}{comp['text'][:24]!r}]"


def describe_component(comp: dict) -> dict:
    return {"panel": comp["panel"], "text": comp["text"], "has_box": comp["rect"] is not None,
            "has_segment": comp["segment"] is not None}


def match(ref: list[dict], cand: list[dict]) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Pair repelled labels by (panel, exact text) in order — never by repelled coordinates."""
    buckets: dict[tuple[int, str], list[int]] = {}
    for j, c in enumerate(cand):
        buckets.setdefault((c["panel"], c["text"]), []).append(j)
    pairs, missing = [], []
    for i, r in enumerate(ref):
        key = (r["panel"], r["text"])
        if buckets.get(key):
            pairs.append((i, buckets[key].pop(0)))
        else:
            missing.append(i)
    used = {j for _, j in pairs}
    return pairs, missing, [j for j in range(len(cand)) if j not in used]


# ───────────────────────────── per-label checks ─────────────────────────────

def label_inside_panel(r, c, P, add, expect):
    W, H = c["canvas_pt"]
    def overflow_pt(comp):
        bb, pbb = comp["bbox_norm_tl"], comp["panel_bbox_norm_tl"]
        return max(0.0, (pbb[0] - bb[0]) * W, (bb[2] - pbb[2]) * W, (pbb[1] - bb[1]) * H, (bb[3] - pbb[3]) * H)
    ro, co = overflow_pt(r), overflow_pt(c)
    if ro > P["tol"]:
        return False           # R itself intentionally clips/overflows here
    if exceeds(co, P["tol"], "label_inside_panel"):
        add("label_inside_panel", f"<= {P['tol']}pt", round(co, 2),
            f"Label {c['text']!r} extends {co:.1f}pt outside the panel.", delta=round(co, 2))


def segment_connects(r, c, P, add, expect):
    seg = c.get("segment")
    if seg is None:
        return False
    W, H = tuple(c["canvas_pt"])
    bb = c["bbox_norm_tl"]
    d0_box = _point_to_bbox_pt(seg["x0_norm"], seg["y0_norm"], bb, (W, H))
    d1_box = _point_to_bbox_pt(seg["x1_norm"], seg["y1_norm"], bb, (W, H))
    # One end near the label box, the other near an anchor point (if panel has points)
    near_box = min(d0_box, d1_box)
    if exceeds(near_box, P["tol"], "segment_connects"):
        add("segment_connects", f"<= {P['tol']}pt", round(near_box, 2),
            f"Leader segment does not reach label {c['text']!r} (gap {near_box:.1f}pt).",
            delta=round(near_box, 2), sub="box_end")
    pts = c.get("panel_points") or []
    if pts:
        # Farther-from-box endpoint should be near a panel point
        px, py = (seg["x1_norm"], seg["y1_norm"]) if d0_box <= d1_box else (seg["x0_norm"], seg["y0_norm"])
        near_pt = min(math.hypot((px - p["x_norm"]) * W, (py - p["y_norm"]) * H) for p in pts)
        if exceeds(near_pt, P["tol"], "segment_connects"):
            add("segment_connects", f"<= {P['tol']}pt", round(near_pt, 2),
                f"Leader segment anchor end is {near_pt:.1f}pt away from nearest point.",
                delta=round(near_pt, 2), sub="point_end")


def label_text_style(r, c, P, add, expect):
    de = color_delta_e(r["color_hex"], c["color_hex"])
    if exceeds(de, P["tol"], "label_text_style"):
        add("label_text_style", r["color_hex"], c["color_hex"],
            f"Text colour {c['color_hex']}, expected {r['color_hex']} (ΔE {de}).", delta=de, sub="colour")
    da = abs(c["alpha"] - r["alpha"])
    if exceeds(da, P["alpha_tol"], "label_text_style"):
        add("label_text_style", r["alpha"], c["alpha"],
            f"Text alpha {c['alpha']}, expected {r['alpha']}.", delta=round(c["alpha"] - r["alpha"], 4), sub="alpha")
    ds = abs(c["fontsize_pt"] - r["fontsize_pt"])
    if exceeds(ds, 1.0, "label_text_style"):
        add("label_text_style", r["fontsize_pt"], c["fontsize_pt"],
            f"Font size {c['fontsize_pt']:.1f}pt, expected {r['fontsize_pt']:.1f}pt.",
            delta=round(c["fontsize_pt"] - r["fontsize_pt"], 2), sub="size")
    if r["fontweight"] != c["fontweight"]:
        add("label_text_style", r["fontweight"], c["fontweight"],
            f"Font weight {c['fontweight']}, expected {r['fontweight']}.", sub="weight")
    if r["fontstyle"] != c["fontstyle"]:
        add("label_text_style", r["fontstyle"], c["fontstyle"],
            f"Font style {c['fontstyle']}, expected {r['fontstyle']}.", sub="style")


def label_box_style(r, c, P, add, expect):
    rr, cr = r.get("rect"), c.get("rect")
    if rr is None and cr is None:
        return False
    if (rr is None) != (cr is None):
        add("label_box_style", rr is not None, cr is not None,
            "Label background box presence mismatch.", sub="present")
        return
    if rr["is_rounded"] != cr["is_rounded"]:
        add("label_box_style", rr["is_rounded"], cr["is_rounded"],
            f"Box rounded={cr['is_rounded']}, expected {rr['is_rounded']}.", sub="rounded")
    de_f = color_delta_e(rr["facecolor_hex"], cr["facecolor_hex"])
    if exceeds(de_f, P["tol"], "label_box_style"):
        add("label_box_style", rr["facecolor_hex"], cr["facecolor_hex"],
            f"Box fill {cr['facecolor_hex']}, expected {rr['facecolor_hex']} (ΔE {de_f}).", delta=de_f, sub="fill")
    da_f = abs(cr["face_alpha"] - rr["face_alpha"])
    if exceeds(da_f, P["alpha_tol"], "label_box_style"):
        add("label_box_style", rr["face_alpha"], cr["face_alpha"],
            f"Box fill alpha {cr['face_alpha']}, expected {rr['face_alpha']}.",
            delta=round(cr["face_alpha"] - rr["face_alpha"], 4), sub="fill_alpha")
    de_e = color_delta_e(rr["edgecolor_hex"], cr["edgecolor_hex"])
    if exceeds(de_e, P["tol"], "label_box_style"):
        add("label_box_style", rr["edgecolor_hex"], cr["edgecolor_hex"],
            f"Box edge {cr['edgecolor_hex']}, expected {rr['edgecolor_hex']} (ΔE {de_e}).", delta=de_e, sub="edge")


CHECKS = {f.__name__: f for f in (label_inside_panel, segment_connects, label_text_style, label_box_style)}


# ───────────────────────────── whole-plot invariant checks ─────────────────────────────

def _count_label_overlaps(comps: list[dict]) -> list[tuple[str, str]]:
    bad = []
    for i in range(len(comps)):
        for j in range(i + 1, len(comps)):
            if comps[i]["panel"] != comps[j]["panel"]:
                continue
            if _boxes_overlap_pt(comps[i]["bbox_norm_tl"], comps[j]["bbox_norm_tl"], tuple(comps[i]["canvas_pt"])):
                bad.append((comps[i]["text"], comps[j]["text"]))
    return bad


def label_no_overlap(rc, cc, pairs, P, add, expect):
    if len(rc) < 2 or len(cc) < 2:
        return False
    r_bad = _count_label_overlaps(rc)
    c_bad = _count_label_overlaps(cc)
    if len(c_bad) > len(r_bad):
        add("label_no_overlap", len(r_bad), len(c_bad),
            f"{len(c_bad)} overlapping label pair(s) (e.g. {c_bad[:3]}), expected <= {len(r_bad)}.",
            delta=len(c_bad) - len(r_bad))


def _count_point_overlaps(comps: list[dict], clearance_pt: float = 1.0) -> list[str]:
    bad: list[str] = []
    for c in comps:
        W, H = tuple(c["canvas_pt"])
        bb = c["bbox_norm_tl"]
        for pt in c.get("panel_points") or []:
            rad = 0.5 * float(pt.get("size_pt", 3.5))
            if _point_to_bbox_pt(pt["x_norm"], pt["y_norm"], bb, (W, H)) < rad + clearance_pt:
                bad.append(c["text"])
    return bad


def label_no_point_overlap(rc, cc, pairs, P, add, expect):
    if not any(c.get("panel_points") for c in cc):
        return False
    clearance = float(P.get("min", 1.0))
    r_bad = _count_point_overlaps(rc, clearance)
    c_bad = _count_point_overlaps(cc, clearance)
    if len(c_bad) > len(r_bad) + int(P.get("tol", 0)):
        sample = list(dict.fromkeys(c_bad))[:4]
        add("label_no_point_overlap", len(r_bad), len(c_bad),
            f"{len(c_bad)} label-point overlap(s) within dot radius+{clearance:g}pt (e.g. {sample}), expected <= {len(r_bad)}.",
            delta=len(c_bad) - len(r_bad))


def segment_count(rc, cc, pairs, P, add, expect):
    r_seg = sum(1 for r in rc if r.get("segment") is not None)
    c_seg = sum(1 for c in cc if c.get("segment") is not None)
    # When R draws segments for ALL labels (e.g. min.segment.length = 0), candidate must too;
    # when R draws NO segments (e.g. segment.color = NA / huge min.segment.length), candidate must draw 0.
    if r_seg == 0 and c_seg > 0:
        add("segment_count", 0, c_seg, f"Drew {c_seg} leader segment(s), expected 0.", delta=c_seg)
    elif r_seg == len(rc) and r_seg > 0 and c_seg < len(cc):
        add("segment_count", r_seg, c_seg, f"Drew {c_seg}/{len(cc)} leader segment(s), expected all {r_seg}.",
            delta=c_seg - r_seg)


CASE_CHECKS = {"label_no_overlap": label_no_overlap, "label_no_point_overlap": label_no_point_overlap,
               "segment_count": segment_count}
