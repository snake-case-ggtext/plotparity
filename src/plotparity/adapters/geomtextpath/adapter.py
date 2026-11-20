"""geomtextpath adapter: curved/straight text along paths, polylines with text-gap breaks, and label boxes.

Each component represents one labelled curve `(panel, text)` assembled from:
  - its ordered per-glyph text draws (position, tangent angle, text style),
  - its associated curve polylines (0 for `text_only = TRUE`, 1 for continuous, 2+ for gapped behind text),
  - its optional label background polygon (`geom_labelpath` / `geom_labelline`).
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from ...compare import color_delta_e, exceeds
from ...engines.python.probe import point_in_bbox

NAME = "geomtextpath"
VERSION = "0.1.0"
HERE = Path(__file__).resolve().parent
OBSERVE_R = HERE / "observe.R"
PROFILE = HERE / "profile.toml"
EXPECT_KEYS: set[str] = set()


# ───────────────────────────── geometry helpers ─────────────────────────────

def _norm_text(s: str) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def _norm_angle(deg: float) -> float:
    a = ((float(deg) + 180.0) % 360.0) - 180.0
    if abs(a + 180.0) < 1e-6:
        return 180.0
    return round(a, 3)


def _angle_diff(a: float, b: float) -> float:
    """Unsigned angular difference in [0, 180] degrees (mod 360, so 0 vs 180 = 180)."""
    return abs(((float(a) - float(b) + 180.0) % 360.0) - 180.0)


def _panel_for(cx: float, cy: float, panels: list[dict]) -> dict:
    for p in panels:
        if point_in_bbox(cx, cy, p["bbox_norm_tl"], pad=0.02):
            return p
    if panels:
        return min(
            panels,
            key=lambda p: math.hypot(
                cx - 0.5 * (p["bbox_norm_tl"][0] + p["bbox_norm_tl"][2]),
                cy - 0.5 * (p["bbox_norm_tl"][1] + p["bbox_norm_tl"][3]),
            ),
        )
    return {"panel_index": 1, "bbox_norm_tl": [0.0, 0.0, 1.0, 1.0]}


def _to_panel_pt(
    x_norm: float, y_norm: float, panel_bb: list[float], ref_panel_pt: tuple[float, float]
) -> tuple[float, float]:
    pw = max(panel_bb[2] - panel_bb[0], 1e-9)
    ph = max(panel_bb[3] - panel_bb[1], 1e-9)
    return (
        (x_norm - panel_bb[0]) / pw * ref_panel_pt[0],
        (y_norm - panel_bb[1]) / ph * ref_panel_pt[1],
    )


def _panel_size_pt(comp: dict) -> tuple[float, float]:
    W, H = comp["canvas_pt"]
    pbb = comp["panel_bbox_norm_tl"]
    return max((pbb[2] - pbb[0]) * W, 1.0), max((pbb[3] - pbb[1]) * H, 1.0)


def _pt_to_seg_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _pt_to_polylines_dist(px: float, py: float, polylines_pt: list[list[tuple[float, float]]]) -> float:
    best = float("inf")
    for pts in polylines_pt:
        if len(pts) == 1:
            d = math.hypot(px - pts[0][0], py - pts[0][1])
            if d < best:
                best = d
        else:
            for i in range(len(pts) - 1):
                d = _pt_to_seg_dist(px, py, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
                if d < best:
                    best = d
    return best


def _sample_polylines(polylines_pt: list[list[tuple[float, float]]], step_pt: float = 3.0) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for pts in polylines_pt:
        if not pts:
            continue
        out.append(pts[0])
        for i in range(len(pts) - 1):
            ax, ay = pts[i]
            bx, by = pts[i + 1]
            seg_len = math.hypot(bx - ax, by - ay)
            n = max(1, int(math.ceil(seg_len / step_pt)))
            for k in range(1, n + 1):
                t = k / n
                out.append((ax + t * (bx - ax), ay + t * (by - ay)))
    return out


def _bbox_center(bb: list[float]) -> tuple[float, float]:
    return 0.5 * (bb[0] + bb[2]), 0.5 * (bb[1] + bb[3])


def _union_bbox(bbs: list[list[float]]) -> list[float]:
    if not bbs:
        return [0.0, 0.0, 0.0, 0.0]
    return [
        round(min(b[0] for b in bbs), 6),
        round(min(b[1] for b in bbs), 6),
        round(max(b[2] for b in bbs), 6),
        round(max(b[3] for b in bbs), 6),
    ]


def _non_space_glyphs(comp: dict) -> list[dict]:
    return [g for g in comp.get("glyphs") or [] if str(g.get("char", "")).strip() != ""]


def _build_component(
    panel: dict,
    multi_panel: bool,
    text: str,
    glyphs: list[dict],
    polylines: list[dict],
    box: dict | None,
    canvas_pt: tuple[float, float],
) -> dict:
    ns_glyphs = [g for g in glyphs if str(g.get("char", "")).strip() != ""]
    ref_g = ns_glyphs[0] if ns_glyphs else (glyphs[0] if glyphs else {})
    text_bb = _union_bbox([g["bbox_norm_tl"] for g in (ns_glyphs or glyphs)])
    has_line = len(polylines) > 0
    ref_p = polylines[0] if polylines else {}
    return {
        "panel": int(panel["panel_index"]),
        "panel_bbox_norm_tl": [float(v) for v in panel["bbox_norm_tl"]],
        "multi_panel": bool(multi_panel),
        "text": str(text),
        "norm_text": _norm_text(text),
        "glyphs": glyphs,
        "text_bbox_norm_tl": text_bb,
        "polylines": polylines,
        "box": box,
        "text_color_hex": str(ref_g.get("color_hex", "#000000")),
        "text_alpha": float(ref_g.get("alpha", 1.0)),
        "fontsize_pt": float(ref_g.get("fontsize_pt", 11.0)),
        "fontweight": str(ref_g.get("fontweight", "normal")).lower(),
        "fontstyle": str(ref_g.get("fontstyle", "normal")).lower(),
        "has_line": has_line,
        "line_color_hex": str(ref_p.get("color_hex", "none")) if has_line else "none",
        "line_alpha": float(ref_p.get("alpha", 0.0)) if has_line else 0.0,
        "line_linewidth_pt": float(ref_p.get("linewidth_pt", 0.0)) if has_line else 0.0,
        "line_linetype": str(ref_p.get("linetype", "solid")) if has_line else "none",
        "canvas_pt": [float(canvas_pt[0]), float(canvas_pt[1])],
    }


# ───────────────────────────── Python extraction ─────────────────────────────

def _expand_text_event(ev: dict) -> list[dict]:
    """Convert one probe draw_text event into 1 or more per-character glyph dicts."""
    s = str(ev.get("text", ""))
    if not s:
        return []
    quad = ev.get("quad_norm_tl")
    bb = [float(v) for v in ev["bbox_norm_tl"]]
    angle = _norm_angle(float(ev.get("angle_deg", 0.0)))
    base_style = {
        "angle_deg": angle,
        "color_hex": str(ev.get("color_hex", "#000000")),
        "alpha": float(ev.get("alpha", 1.0)),
        "fontsize_pt": float(ev.get("fontsize_pt", 11.0)),
        "fontweight": str(ev.get("fontweight", "normal")).lower(),
        "fontstyle": str(ev.get("fontstyle", "normal")).lower(),
    }
    if len(s) == 1:
        if quad and len(quad) == 4:
            cx = sum(p[0] for p in quad) / 4.0
            cy = sum(p[1] for p in quad) / 4.0
        else:
            cx, cy = _bbox_center(bb)
        return [{
            "char": s,
            "cx_norm": round(cx, 6),
            "cy_norm": round(cy, 6),
            "bbox_norm_tl": bb,
            **base_style,
        }]

    # Multi-character draw_text (e.g., straight=True drawn as a single string): subdivide along quad
    n = len(s)
    if quad and len(quad) == 4:
        # quad = [bl, br, tr, tl]
        left_x = 0.5 * (quad[0][0] + quad[3][0])
        left_y = 0.5 * (quad[0][1] + quad[3][1])
        right_x = 0.5 * (quad[1][0] + quad[2][0])
        right_y = 0.5 * (quad[1][1] + quad[2][1])
    else:
        left_x, right_x = bb[0], bb[2]
        left_y = right_y = 0.5 * (bb[1] + bb[3])
    half_w = 0.5 * abs(bb[2] - bb[0]) / max(n, 1)
    half_h = 0.5 * abs(bb[3] - bb[1])
    out = []
    for k, ch in enumerate(s):
        t = (k + 0.5) / n
        cx = left_x + t * (right_x - left_x)
        cy = left_y + t * (right_y - left_y)
        out.append({
            "char": ch,
            "cx_norm": round(cx, 6),
            "cy_norm": round(cy, 6),
            "bbox_norm_tl": [round(cx - half_w, 6), round(cy - half_h, 6),
                             round(cx + half_w, 6), round(cy + half_h, 6)],
            **base_style,
        })
    return out


def _match_linetype(bb: list[float], path_dashes: list[dict], used: set[int]) -> str:
    best_i = None
    best_d = 0.02
    for i, pd in enumerate(path_dashes):
        if i in used:
            continue
        pbb = pd["bbox_norm_tl"]
        d = sum(abs(bb[k] - pbb[k]) for k in range(4))
        if d < best_d:
            best_d = d
            best_i = i
    if best_i is not None:
        used.add(best_i)
        return str(path_dashes[best_i].get("linetype", "solid"))
    return "solid"


def _extract_python(
    obs: dict[str, Any], panels: list[dict], canvas_pt: tuple[float, float]
) -> tuple[list[dict], set[int]]:
    events = obs.get("events") or []
    owners = obs.get("owners") or []
    st = obs.get("geomtextpath") or {}
    path_dashes = st.get("path_dashes") or []
    layer_labels = st.get("layer_labels") or []
    used_dashes: set[int] = set()
    assigned: set[int] = set()

    def is_port_event(ev: dict) -> bool:
        if "role" in ev:
            return False
        k = ev.get("owner")
        if k is None or k >= len(owners):
            return False
        mod = owners[k].get("module", "")
        return not mod.startswith(("matplotlib.", "plotnine."))

    port_evs = [e for e in events if is_port_event(e)]
    if not port_evs and panels:
        for e in events:
            if "role" in e or "axes" not in e.get("groups", []):
                continue
            if e["kind"] == "text" and any(point_in_bbox(e["x_norm"], e["y_norm"], p["bbox_norm_tl"], pad=0.05) for p in panels):
                port_evs.append(e)

    # Group events by (panel_index, owner)
    buckets: dict[tuple[int, int | None], dict[str, list[dict]]] = {}
    bucket_order: list[tuple[int, int | None]] = []

    for ev in port_evs:
        kind = ev["kind"]
        if kind not in ("text", "path"):
            continue
        bb = ev.get("bbox_norm_tl")
        if not bb:
            continue
        cx, cy = _bbox_center(bb)
        panel = _panel_for(cx, cy, panels)
        key = (int(panel["panel_index"]), ev.get("owner"))
        if key not in buckets:
            buckets[key] = {"panel": panel, "texts": [], "open_paths": [], "closed_paths": []}
            bucket_order.append(key)
        if kind == "text":
            buckets[key]["texts"].append(ev)
            assigned.add(ev["paint_index"])
        elif kind == "path":
            verts = ev.get("vertices_norm_tl") or []
            is_closed = bool(ev.get("is_closed")) or (ev.get("facecolor_hex", "none") != "none")
            if is_closed and len(verts) >= 3:
                buckets[key]["closed_paths"].append(ev)
                assigned.add(ev["paint_index"])
            elif len(verts) >= 2 and ev.get("edgecolor_hex", "none") != "none" and float(ev.get("linewidth_pt", 0.0)) > 0:
                buckets[key]["open_paths"].append(ev)
                assigned.add(ev["paint_index"])
            else:
                assigned.add(ev["paint_index"])

    by_panel_labels: dict[int, list[str]] = {}
    for ll in layer_labels:
        by_panel_labels.setdefault(int(ll["panel"]), []).append(str(ll["label"]))

    comps: list[dict] = []
    multi_panel = len(panels) > 1

    for key in bucket_order:
        b = buckets[key]
        panel = b["panel"]
        glyphs: list[dict] = []
        for te in b["texts"]:
            glyphs.extend(_expand_text_event(te))
        if not glyphs:
            continue

        polylines: list[dict] = []
        for pe in b["open_paths"]:
            lt = str(pe["linetype"]) if pe.get("linetype") else _match_linetype(pe["bbox_norm_tl"], path_dashes, used_dashes)
            polylines.append({
                "points_norm_tl": [[float(p[0]), float(p[1])] for p in pe["vertices_norm_tl"]],
                "color_hex": str(pe.get("edgecolor_hex", "#000000")),
                "alpha": float(pe.get("edge_alpha", 1.0)),
                "linewidth_pt": float(pe.get("linewidth_pt", 1.0)),
                "linetype": lt,
            })

        boxes: list[dict] = []
        for be in b["closed_paths"]:
            verts = be["vertices_norm_tl"]
            uniq_v = len({(round(v[0], 4), round(v[1], 4)) for v in verts})
            boxes.append({
                "points_norm_tl": [[float(p[0]), float(p[1])] for p in verts],
                "bbox_norm_tl": [float(v) for v in be["bbox_norm_tl"]],
                "is_rounded": bool(be.get("is_rounded")) or (uniq_v > 6),
                "facecolor_hex": str(be.get("facecolor_hex", "none")),
                "face_alpha": float(be.get("face_alpha", 0.0)),
                "edgecolor_hex": str(be.get("edgecolor_hex", "none")),
                "edge_alpha": float(be.get("edge_alpha", 0.0)),
                "linewidth_pt": float(be.get("linewidth_pt", 0.0)),
            })

        joined = "".join(g["char"] for g in glyphs)
        # Check if a single artist emitted multiple curves in one owner bucket
        p_lbls = by_panel_labels.get(int(panel["panel_index"]), [])
        split_groups: list[tuple[str, list[dict]]] = []
        if len(p_lbls) > 1 and _norm_text(joined) == _norm_text("".join(p_lbls)):
            ns_all = [g for g in glyphs if g["char"].strip() != ""]
            pos = 0
            ok = True
            for lbl in p_lbls:
                n_ns = len(_norm_text(lbl))
                if pos + n_ns <= len(ns_all):
                    split_groups.append((lbl, ns_all[pos:pos + n_ns]))
                    pos += n_ns
                else:
                    ok = False
                    break
            if not ok or pos != len(ns_all):
                split_groups = []

        if len(split_groups) > 1:
            n_g = len(split_groups)
            g_polys: list[list[dict]] = [[] for _ in range(n_g)]
            g_boxes: list[dict | None] = [None] * n_g
            g_centers = [
                (
                    sum(g["cx_norm"] for g in gs) / max(len(gs), 1),
                    sum(g["cy_norm"] for g in gs) / max(len(gs), 1),
                )
                for _, gs in split_groups
            ]
            for pl in polylines:
                pts = pl["points_norm_tl"]
                best_i = min(
                    range(n_g),
                    key=lambda i: min(math.hypot(p[0] - g_centers[i][0], p[1] - g_centers[i][1]) for p in pts),
                )
                g_polys[best_i].append(pl)
            for bx in boxes:
                bc = _bbox_center(bx["bbox_norm_tl"])
                best_i = min(range(n_g), key=lambda i: math.hypot(bc[0] - g_centers[i][0], bc[1] - g_centers[i][1]))
                g_boxes[best_i] = bx
            for i, (lbl, gs) in enumerate(split_groups):
                comps.append(_build_component(panel, multi_panel, lbl, gs, g_polys[i], g_boxes[i], canvas_pt))
        else:
            comps.append(_build_component(panel, multi_panel, joined, glyphs, polylines, boxes[0] if boxes else None, canvas_pt))

    return comps, assigned


# ───────────────────────────── R extraction & interface ─────────────────────────────

def components(
    engine: str,
    obs: dict[str, Any],
    expect: dict[str, Any],
    canvas_pt: tuple[float, float] = (640.0, 480.0),
) -> tuple[list[dict], set[int]]:
    st = obs.get("geomtextpath") or {}
    panels = st.get("panels") or []
    if engine == "python":
        return _extract_python(obs, panels, canvas_pt)
    if engine != "r":
        raise ValueError(f"geomtextpath adapter has no extractor for engine {engine!r}")
    by_pi = {int(p["panel_index"]): p for p in panels}
    multi_panel = len(panels) > 1
    comps: list[dict] = []
    for raw in st.get("curves") or []:
        pi = int(raw.get("panel", 1))
        panel = by_pi.get(pi, {"panel_index": pi, "bbox_norm_tl": [0.0, 0.0, 1.0, 1.0]})
        glyphs = raw.get("glyphs") or []
        polylines = raw.get("polylines") or []
        box = raw.get("box") or None
        comps.append(_build_component(panel, multi_panel, raw.get("text", ""), glyphs, polylines, box, canvas_pt))
    return comps, set()


def component_id(i: int, comp: dict) -> str:
    prefix = f"p{comp['panel']}:" if comp.get("multi_panel") else ""
    return f"curve[{prefix}{comp['text'][:24]!r}]"


def describe_component(comp: dict) -> dict:
    return {
        "panel": comp["panel"],
        "text": comp["text"],
        "n_glyphs": len(_non_space_glyphs(comp)),
        "n_polylines": len(comp["polylines"]),
        "has_box": comp["box"] is not None,
    }


def _comp_centroid(comp: dict) -> tuple[float, float]:
    gs = _non_space_glyphs(comp)
    if gs:
        return sum(g["cx_norm"] for g in gs) / len(gs), sum(g["cy_norm"] for g in gs) / len(gs)
    return _bbox_center(comp["text_bbox_norm_tl"])


def match(ref: list[dict], cand: list[dict]) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Pair curved text components by (panel, normalized text), breaking duplicates by centroid distance."""
    buckets: dict[tuple[int, str], list[int]] = {}
    for j, c in enumerate(cand):
        buckets.setdefault((c["panel"], c["norm_text"]), []).append(j)

    pairs: list[tuple[int, int]] = []
    missing: list[int] = []
    for i, r in enumerate(ref):
        key = (r["panel"], r["norm_text"])
        avail = buckets.get(key)
        if avail:
            if len(avail) == 1:
                j = avail.pop(0)
            else:
                rc = _comp_centroid(r)
                best_idx = min(
                    range(len(avail)),
                    key=lambda k: math.hypot(
                        rc[0] - _comp_centroid(cand[avail[k]])[0],
                        rc[1] - _comp_centroid(cand[avail[k]])[1],
                    ),
                )
                j = avail.pop(best_idx)
            pairs.append((i, j))
        else:
            missing.append(i)
    used = {j for _, j in pairs}
    unexpected = [j for j in range(len(cand)) if j not in used]
    return pairs, missing, unexpected


# ───────────────────────────── per-component checks ─────────────────────────────

def _polylines_panel_pt(comp: dict, ref_panel_pt: tuple[float, float]) -> list[list[tuple[float, float]]]:
    pbb = comp["panel_bbox_norm_tl"]
    return [
        [_to_panel_pt(p[0], p[1], pbb, ref_panel_pt) for p in pl["points_norm_tl"]]
        for pl in comp.get("polylines") or []
        if pl.get("points_norm_tl")
    ]


def _glyphs_panel_pt(comp: dict, ref_panel_pt: tuple[float, float]) -> list[tuple[float, float]]:
    pbb = comp["panel_bbox_norm_tl"]
    return [_to_panel_pt(g["cx_norm"], g["cy_norm"], pbb, ref_panel_pt) for g in _non_space_glyphs(comp)]


def curve_trajectory(r, c, P, add, expect):
    r_has, c_has = bool(r["has_line"]), bool(c["has_line"])
    if not r_has and not c_has:
        return False
    if r_has and not c_has:
        add("curve_trajectory", True, False,
            f"Missing curve line for {c['text']!r} (R drew {len(r['polylines'])} polyline(s)).", sub="missing_line")
        return
    if not r_has and c_has:
        add("curve_trajectory", False, True,
            f"Unexpected curve line for {c['text']!r} (R has text_only / no line).", sub="unexpected_line")
        return

    ref_panel_pt = _panel_size_pt(r)
    r_polys = _polylines_panel_pt(r, ref_panel_pt)
    c_polys = _polylines_panel_pt(c, ref_panel_pt)
    if not r_polys or not c_polys:
        return False

    # Bridge across the text gap using glyph centers when checking candidate->R so small gap-width
    # differences are tested by line_gap rather than double-counted in curve_trajectory.
    r_glyphs = _glyphs_panel_pt(r, ref_panel_pt)
    r_bridged = list(r_polys) + ([r_glyphs] if len(r_glyphs) >= 2 else [])
    c_glyphs = _glyphs_panel_pt(c, ref_panel_pt)
    c_bridged = list(c_polys) + ([c_glyphs] if len(c_glyphs) >= 2 else [])

    c_pts = _sample_polylines(c_polys, step_pt=3.0)
    r_pts = _sample_polylines(r_polys, step_pt=3.0)

    d_c_to_r = max(_pt_to_polylines_dist(px, py, r_bridged) for px, py in c_pts) if c_pts else 0.0
    d_r_to_c = max(_pt_to_polylines_dist(px, py, c_bridged) for px, py in r_pts) if r_pts else 0.0
    worst = max(d_c_to_r, d_r_to_c)
    if exceeds(worst, P["tol"], "curve_trajectory"):
        add("curve_trajectory", f"<= {P['tol']}pt", round(worst, 2),
            f"Curve trajectory for {c['text']!r} deviates by {worst:.1f}pt (cand->ref {d_c_to_r:.1f}pt, ref->cand {d_r_to_c:.1f}pt).",
            delta=round(worst, 2))


def glyph_position(r, c, P, add, expect):
    rg = _non_space_glyphs(r)
    cg = _non_space_glyphs(c)
    if not rg and not cg:
        return False
    r_chars = "".join(g["char"] for g in rg)
    c_chars = "".join(g["char"] for g in cg)
    if r_chars != c_chars:
        add("glyph_position", r_chars, c_chars,
            f"Glyph characters {c_chars!r} do not match expected {r_chars!r}.", sub="characters")
        return

    ref_panel_pt = _panel_size_pt(r)
    r_pts = _glyphs_panel_pt(r, ref_panel_pt)
    c_pts = _glyphs_panel_pt(c, ref_panel_pt)

    dists = [math.hypot(rp[0] - cp[0], rp[1] - cp[1]) for rp, cp in zip(r_pts, c_pts)]
    mean_d = sum(dists) / len(dists)
    max_d = max(dists)
    if exceeds(mean_d, P["tol"], "glyph_position"):
        add("glyph_position", f"<= {P['tol']}pt", round(mean_d, 2),
            f"Mean glyph position shift for {c['text']!r} is {mean_d:.1f}pt (max {max_d:.1f}pt).",
            delta=round(mean_d, 2), sub="mean")
    elif exceeds(max_d, max(P["tol"] * 2.0, 18.0), "glyph_position"):
        add("glyph_position", f"<= {max(P['tol'] * 2.0, 18.0):.1f}pt", round(max_d, 2),
            f"Max glyph position shift for {c['text']!r} is {max_d:.1f}pt (mean {mean_d:.1f}pt).",
            delta=round(max_d, 2), sub="max")

    # Label arc-length span (catches wrong letter spacing / tracking or font scaling)
    if len(r_pts) >= 2:
        r_span = sum(math.hypot(r_pts[k + 1][0] - r_pts[k][0], r_pts[k + 1][1] - r_pts[k][1]) for k in range(len(r_pts) - 1))
        c_span = sum(math.hypot(c_pts[k + 1][0] - c_pts[k][0], c_pts[k + 1][1] - c_pts[k][1]) for k in range(len(c_pts) - 1))
        if r_span >= 8.0:
            rel_err = abs(c_span - r_span) / r_span
            abs_err = abs(c_span - r_span)
            if exceeds(rel_err, P["rel_tol"], "glyph_position") and abs_err > 6.0:
                add("glyph_position", round(r_span, 1), round(c_span, 1),
                    f"Label arc-length span for {c['text']!r} is {c_span:.1f}pt, expected {r_span:.1f}pt ({rel_err:.0%} diff).",
                    delta=round(c_span - r_span, 2), sub="span")


def glyph_angle(r, c, P, add, expect):
    rg = _non_space_glyphs(r)
    cg = _non_space_glyphs(c)
    if not rg or len(rg) != len(cg):
        return False

    r_angs = [float(g["angle_deg"]) for g in rg]
    c_angs = [float(g["angle_deg"]) for g in cg]

    # Uprightness check: upright=TRUE keeps glyphs in [-95, 95]; upright=FALSE on reversed paths goes > 110
    r_all_upright = all(abs(a) <= 95.0 for a in r_angs)
    c_upside_down = [a for a in c_angs if abs(a) > 105.0]
    if r_all_upright and c_upside_down:
        add("glyph_angle", "upright (|angle| <= 95 deg)", round(max(c_upside_down, key=abs), 1),
            f"{len(c_upside_down)}/{len(c_angs)} glyph(s) of {c['text']!r} are upside-down (e.g. {c_upside_down[0]:.1f} deg).",
            delta=round(max(abs(a) for a in c_upside_down) - 90.0, 1), sub="upright")
        return

    r_mean_abs = sum(abs(a) for a in r_angs) / len(r_angs)
    c_mean_abs = sum(abs(a) for a in c_angs) / len(c_angs)
    if r_mean_abs >= 110.0 and c_mean_abs < 85.0:
        add("glyph_angle", f"inverted (~{r_mean_abs:.0f} deg)", round(c_mean_abs, 1),
            f"Glyphs of {c['text']!r} were flipped upright (mean |angle| {c_mean_abs:.1f} deg), expected inverted like R ({r_mean_abs:.1f} deg).",
            delta=round(r_mean_abs - c_mean_abs, 1), sub="upright")
        return

    diffs = [_angle_diff(ra, ca) for ra, ca in zip(r_angs, c_angs)]
    mean_err = sum(diffs) / len(diffs)
    max_err = max(diffs)
    if exceeds(mean_err, P["tol"], "glyph_angle"):
        add("glyph_angle", f"<= {P['tol']} deg", round(mean_err, 2),
            f"Mean glyph rotation error for {c['text']!r} is {mean_err:.1f} deg (max {max_err:.1f} deg).",
            delta=round(mean_err, 2), sub="mean")
    elif exceeds(max_err, max(P["tol"] * 2.2, 26.0), "glyph_angle"):
        add("glyph_angle", f"<= {max(P['tol'] * 2.2, 26.0):.1f} deg", round(max_err, 2),
            f"Max glyph rotation error for {c['text']!r} is {max_err:.1f} deg (mean {mean_err:.1f} deg).",
            delta=round(max_err, 2), sub="max")


def _gap_metrics(comp: dict, ref_panel_pt: tuple[float, float]) -> dict:
    polys = _polylines_panel_pt(comp, ref_panel_pt)
    g_pts = _glyphs_panel_pt(comp, ref_panel_pt)
    fs = max(float(comp.get("fontsize_pt", 11.0)), 6.0)
    hit_r = max(2.8, 0.35 * fs)
    dists = [_pt_to_polylines_dist(px, py, polys) for px, py in g_pts]
    n_hit = sum(1 for d in dists if d <= hit_r)
    cuts_text = (n_hit / max(len(dists), 1)) >= 0.35
    # Effective clearance from outer glyph ink to nearest polyline point
    min_center_dist = min(dists) if dists else 0.0
    clearance = max(0.0, min_center_dist - 0.35 * fs)

    # Check if there is an actual break between polylines near the text centroid
    has_break = False
    if len(polys) >= 2 and g_pts:
        cx = sum(p[0] for p in g_pts) / len(g_pts)
        cy = sum(p[1] for p in g_pts) / len(g_pts)
        endpoints = []
        for pl in polys:
            if len(pl) >= 2:
                endpoints.append(pl[0])
                endpoints.append(pl[-1])
        # Sort endpoints by distance to text centroid; two closest from different segments indicate a break
        if len(polys) >= 2:
            seg_near = [
                min((pl[0], pl[-1]), key=lambda e: math.hypot(e[0] - cx, e[1] - cy))
                for pl in polys if len(pl) >= 2
            ]
            seg_near.sort(key=lambda e: math.hypot(e[0] - cx, e[1] - cy))
            if len(seg_near) >= 2:
                gap_w = math.hypot(seg_near[0][0] - seg_near[1][0], seg_near[0][1] - seg_near[1][1])
                has_break = gap_w > 4.0

    return {
        "cuts_text": cuts_text,
        "has_break": has_break,
        "n_polys": len(polys),
        "min_center_dist": min_center_dist,
        "clearance": clearance,
    }


def line_gap(r, c, P, add, expect):
    if not r["has_line"] or not c["has_line"]:
        return False
    if not _non_space_glyphs(r) or not _non_space_glyphs(c):
        return False

    ref_panel_pt = _panel_size_pt(r)
    rm = _gap_metrics(r, ref_panel_pt)
    cm = _gap_metrics(c, ref_panel_pt)

    # 1. Line cutting directly through text vs broken around text
    if not rm["cuts_text"] and cm["cuts_text"]:
        add("line_gap", "no line through text", "line cuts through text",
            f"Curve line cuts through text {c['text']!r} where R leaves a clear gap.",
            sub="cuts_text")
        return
    if rm["cuts_text"] and not cm["cuts_text"]:
        add("line_gap", "continuous line through text", "gapped/missing line behind text",
            f"Curve line is broken behind {c['text']!r}, expected continuous line like R.",
            sub="missing_line_through_text")
        return

    # 2. Text lifted off the line (e.g. vjust = -0.3 or 1.3): line must stay continuous (no break)
    if not rm["cuts_text"] and not rm["has_break"] and rm["n_polys"] == 1 and cm["has_break"]:
        add("line_gap", "continuous line (no break)", f"broken ({cm['n_polys']} segments)",
            f"Curve line for {c['text']!r} was broken even though R keeps a continuous line.",
            sub="unexpected_gap")
        return

    # 3. When R explicitly breaks the line around text, verify candidate also breaks it and matches padding clearance
    if rm["has_break"] and not cm["has_break"]:
        add("line_gap", "broken line behind text", f"unbroken ({cm['n_polys']} segment)",
            f"Curve line for {c['text']!r} was not split into segments around the text gap.",
            sub="missing_gap")
        return

    if rm["has_break"] and cm["has_break"]:
        dc = abs(cm["clearance"] - rm["clearance"])
        if exceeds(dc, P["tol"], "line_gap"):
            add("line_gap", f"{rm['clearance']:.1f}pt", round(cm["clearance"], 2),
                f"Text gap clearance for {c['text']!r} is {cm['clearance']:.1f}pt, expected {rm['clearance']:.1f}pt.",
                delta=round(cm["clearance"] - rm["clearance"], 2), sub="padding")


def text_style(r, c, P, add, expect):
    de = color_delta_e(r["text_color_hex"], c["text_color_hex"])
    if exceeds(de, P["tol"], "text_style"):
        add("text_style", r["text_color_hex"], c["text_color_hex"],
            f"Text colour {c['text_color_hex']}, expected {r['text_color_hex']} (ΔE {de}).", delta=de, sub="colour")
    da = abs(c["text_alpha"] - r["text_alpha"])
    if exceeds(da, P["alpha_tol"], "text_style"):
        add("text_style", r["text_alpha"], c["text_alpha"],
            f"Text alpha {c['text_alpha']}, expected {r['text_alpha']}.",
            delta=round(c["text_alpha"] - r["text_alpha"], 4), sub="alpha")
    ds = abs(c["fontsize_pt"] - r["fontsize_pt"])
    if exceeds(ds, 1.2, "text_style"):
        add("text_style", r["fontsize_pt"], c["fontsize_pt"],
            f"Font size {c['fontsize_pt']:.1f}pt, expected {r['fontsize_pt']:.1f}pt.",
            delta=round(c["fontsize_pt"] - r["fontsize_pt"], 2), sub="size")
    if r["fontweight"] != c["fontweight"]:
        add("text_style", r["fontweight"], c["fontweight"],
            f"Font weight {c['fontweight']}, expected {r['fontweight']}.", sub="weight")
    if r["fontstyle"] != c["fontstyle"]:
        add("text_style", r["fontstyle"], c["fontstyle"],
            f"Font style {c['fontstyle']}, expected {r['fontstyle']}.", sub="style")


def line_style(r, c, P, add, expect):
    if not r["has_line"] or not c["has_line"]:
        return False
    de = color_delta_e(r["line_color_hex"], c["line_color_hex"])
    if exceeds(de, P["tol"], "line_style"):
        add("line_style", r["line_color_hex"], c["line_color_hex"],
            f"Line colour {c['line_color_hex']}, expected {r['line_color_hex']} (ΔE {de}).", delta=de, sub="colour")
    da = abs(c["line_alpha"] - r["line_alpha"])
    if exceeds(da, P["alpha_tol"], "line_style"):
        add("line_style", r["line_alpha"], c["line_alpha"],
            f"Line alpha {c['line_alpha']}, expected {r['line_alpha']}.",
            delta=round(c["line_alpha"] - r["line_alpha"], 4), sub="alpha")
    dlw = abs(c["line_linewidth_pt"] - r["line_linewidth_pt"])
    if exceeds(dlw, 0.6, "line_style"):
        add("line_style", r["line_linewidth_pt"], c["line_linewidth_pt"],
            f"Line width {c['line_linewidth_pt']:.2f}pt, expected {r['line_linewidth_pt']:.2f}pt.",
            delta=round(c["line_linewidth_pt"] - r["line_linewidth_pt"], 3), sub="linewidth")
    r_lt = r.get("line_linetype", "solid")
    c_lt = c.get("line_linetype", "solid")
    if r_lt != c_lt:
        add("line_style", r_lt, c_lt,
            f"Line type {c_lt!r}, expected {r_lt!r}.", sub="linetype")


def label_box_style(r, c, P, add, expect):
    rb, cb = r.get("box"), c.get("box")
    if rb is None and cb is None:
        return False
    if (rb is None) != (cb is None):
        add("label_box_style", rb is not None, cb is not None,
            "Label background box presence mismatch.", sub="present")
        return
    if bool(rb.get("is_rounded")) != bool(cb.get("is_rounded")):
        add("label_box_style", rb["is_rounded"], cb["is_rounded"],
            f"Box rounded={cb['is_rounded']}, expected {rb['is_rounded']}.", sub="rounded")
    de_f = color_delta_e(rb["facecolor_hex"], cb["facecolor_hex"])
    if exceeds(de_f, P["tol"], "label_box_style"):
        add("label_box_style", rb["facecolor_hex"], cb["facecolor_hex"],
            f"Box fill {cb['facecolor_hex']}, expected {rb['facecolor_hex']} (ΔE {de_f}).", delta=de_f, sub="fill")
    da_f = abs(float(cb["face_alpha"]) - float(rb["face_alpha"]))
    if exceeds(da_f, P["alpha_tol"], "label_box_style"):
        add("label_box_style", rb["face_alpha"], cb["face_alpha"],
            f"Box fill alpha {cb['face_alpha']}, expected {rb['face_alpha']}.",
            delta=round(float(cb["face_alpha"]) - float(rb["face_alpha"]), 4), sub="fill_alpha")
    de_e = color_delta_e(rb["edgecolor_hex"], cb["edgecolor_hex"])
    if exceeds(de_e, P["tol"], "label_box_style"):
        add("label_box_style", rb["edgecolor_hex"], cb["edgecolor_hex"],
            f"Box edge {cb['edgecolor_hex']}, expected {rb['edgecolor_hex']} (ΔE {de_e}).", delta=de_e, sub="edge")
    da_e = abs(float(cb["edge_alpha"]) - float(rb["edge_alpha"]))
    if exceeds(da_e, P["alpha_tol"], "label_box_style"):
        add("label_box_style", rb["edge_alpha"], cb["edge_alpha"],
            f"Box edge alpha {cb['edge_alpha']}, expected {rb['edge_alpha']}.",
            delta=round(float(cb["edge_alpha"]) - float(rb["edge_alpha"]), 4), sub="edge_alpha")


CHECKS = {
    f.__name__: f
    for f in (curve_trajectory, glyph_position, glyph_angle, line_gap, text_style, line_style, label_box_style)
}
