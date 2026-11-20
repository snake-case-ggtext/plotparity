"""ggforce adapter: mark enclosure geoms (hull, ellipse, circle, rect) with callouts and connectors.

Combines:
  - exact parity on mark count, enclosure style (fill/edge color & alpha, linewidth, roundedness),
    callout text content (title + wrapped description), callout text/box style, and connector presence/style;
  - geometric parity & invariants: enclosure covers target cluster points with matching expand padding,
    area ratio, and boundary proximity; callouts stay inside the panel, title sits above description,
    no callout-callout overlap, no callout-point overlap (respecting actual dot radius + 1pt clearance
    compared against R), and connectors join each callout box/underline to its mark enclosure.

Python enclosures, callouts, and connectors are extracted directly from probe.py's draw calls
(including native linetype and sampled_vertices_norm_tl for curved paths).
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from ...compare import color_delta_e, exceeds
from ...engines.python.probe import point_in_bbox

NAME = "ggforce"
VERSION = "0.1.0"
HERE = Path(__file__).resolve().parent
OBSERVE_R = HERE / "observe.R"
PROFILE = HERE / "profile.toml"
EXPECT_KEYS: set[str] = set()


# ───────────────────────────── geometry helpers ─────────────────────────────

def _norm_words(text: str | None) -> list[str]:
    if not text:
        return []
    return re.findall(r"\S+", str(text).strip())


def _bbox_center(bb: list[float]) -> tuple[float, float]:
    return 0.5 * (bb[0] + bb[2]), 0.5 * (bb[1] + bb[3])


def _bbox_union(bbs: list[list[float]]) -> list[float]:
    return [
        min(b[0] for b in bbs),
        min(b[1] for b in bbs),
        max(b[2] for b in bbs),
        max(b[3] for b in bbs),
    ]


def _point_to_bbox_pt(px: float, py: float, bb: list[float], canvas_pt: tuple[float, float]) -> float:
    dx = max(bb[0] - px, 0.0, px - bb[2]) * canvas_pt[0]
    dy = max(bb[1] - py, 0.0, py - bb[3]) * canvas_pt[1]
    return math.hypot(dx, dy)


def _boxes_overlap_pt(a: list[float], b: list[float], canvas_pt: tuple[float, float], pad_pt: float = 0.0) -> bool:
    W, H = canvas_pt
    ax0, ay0, ax1, ay1 = a[0] * W - pad_pt, a[1] * H - pad_pt, a[2] * W + pad_pt, a[3] * H + pad_pt
    bx0, by0, bx1, by1 = b[0] * W, b[1] * H, b[2] * W, b[3] * H
    return (ax0 < bx1) and (ax1 > bx0) and (ay0 < by1) and (ay1 > by0)


def _clean_poly_pt(verts_norm: list[list[float]], canvas_pt: tuple[float, float]) -> list[tuple[float, float]]:
    W, H = canvas_pt
    pts: list[tuple[float, float]] = []
    for v in verts_norm:
        x, y = float(v[0]) * W, float(v[1]) * H
        if not pts or math.hypot(x - pts[-1][0], y - pts[-1][1]) > 0.15:
            pts.append((x, y))
    if len(pts) >= 2 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) <= 0.15:
        pts.pop()
    return pts


def _poly_area_pt2(pts: list[tuple[float, float]]) -> float:
    n = len(pts)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return 0.5 * abs(s)


def _point_to_segment_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    vx, vy = bx - ax, by - ay
    l2 = vx * vx + vy * vy
    if l2 <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / l2))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def _point_to_poly_boundary_pt(px: float, py: float, pts: list[tuple[float, float]]) -> float:
    n = len(pts)
    if n == 0:
        return float("inf")
    if n == 1:
        return math.hypot(px - pts[0][0], py - pts[0][1])
    best = float("inf")
    for i in range(n):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % n]
        d = _point_to_segment_dist(px, py, ax, ay, bx, by)
        if d < best:
            best = d
    return best


def _point_in_poly_pt(px: float, py: float, pts: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test in pt coordinates."""
    n = len(pts)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi + 1e-15) + xi):
            inside = not inside
        j = i
    return inside


def _point_signed_dist_poly_pt(px: float, py: float, pts: list[tuple[float, float]]) -> float:
    """Positive if inside polygon (distance to boundary), negative if outside."""
    d = _point_to_poly_boundary_pt(px, py, pts)
    return d if _point_in_poly_pt(px, py, pts) else -d


def _sample_poly_boundary(pts: list[tuple[float, float]], step_pt: float = 4.0) -> list[tuple[float, float]]:
    """Uniformly densify polygon boundary so Hausdorff distance is accurate even on 4-vertex rects."""
    n = len(pts)
    if n < 2:
        return list(pts)
    out: list[tuple[float, float]] = []
    for i in range(n):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % n]
        seg_len = math.hypot(bx - ax, by - ay)
        k = max(1, int(math.ceil(seg_len / max(step_pt, 1.0))))
        for s in range(k):
            t = s / k
            out.append((ax + t * (bx - ax), ay + t * (by - ay)))
    return out


def _poly_hausdorff_pt(pts_a: list[tuple[float, float]], pts_b: list[tuple[float, float]]) -> float:
    if not pts_a or not pts_b:
        return float("inf")
    sa = _sample_poly_boundary(pts_a, 5.0)
    sb = _sample_poly_boundary(pts_b, 5.0)
    d_ab = max(_point_to_poly_boundary_pt(x, y, pts_b) for x, y in sa)
    d_ba = max(_point_to_poly_boundary_pt(x, y, pts_a) for x, y in sb)
    return max(d_ab, d_ba)


def _is_rounded_polygon(verts_norm: list[list[float]], canvas_pt: tuple[float, float], has_curve: bool = False) -> bool:
    if has_curve:
        return True
    pts = _clean_poly_pt(verts_norm, canvas_pt)
    n = len(pts)
    if n < 12:
        return False
    # Detect runs of >= 3 consecutive short-step smooth turns in the same curvature direction
    signs: list[int] = []
    for i in range(n):
        x0, y0 = pts[(i - 1) % n]
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        v1x, v1y = x1 - x0, y1 - y0
        v2x, v2y = x2 - x1, y2 - y1
        l1, l2 = math.hypot(v1x, v1y), math.hypot(v2x, v2y)
        if l1 <= 1e-6 or l2 <= 1e-6:
            signs.append(0)
            continue
        cos_a = max(-1.0, min(1.0, (v1x * v2x + v1y * v2y) / (l1 * l2)))
        ang = math.degrees(math.acos(cos_a))
        cross = v1x * v2y - v1y * v2x
        if 0.5 <= ang <= 35.0 and min(l1, l2) <= 18.0 and abs(cross) > 1e-9:
            signs.append(1 if cross > 0 else -1)
        else:
            signs.append(0)
    # Check circular runs of identical non-zero sign of length >= 3
    doubled = signs + signs
    run = 0
    prev = 0
    for s in doubled:
        if s != 0 and s == prev:
            run += 1
        elif s != 0:
            run = 1
            prev = s
        else:
            run = 0
            prev = 0
        if run >= 3:
            return True
    return False


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
    return {"panel_index": 1, "bbox_norm_tl": [0.0, 0.0, 1.0, 1.0], "points": []}


def _contained_point_indices(verts_norm: list[list[float]], panel_points: list[dict],
                             canvas_pt: tuple[float, float], pad_pt: float = 1.5) -> list[int]:
    pts = _clean_poly_pt(verts_norm, canvas_pt)
    if len(pts) < 3 or not panel_points:
        return []
    W, H = canvas_pt
    out = []
    for idx, pt in enumerate(panel_points):
        px, py = float(pt["x_norm"]) * W, float(pt["y_norm"]) * H
        if _point_signed_dist_poly_pt(px, py, pts) >= -pad_pt:
            out.append(idx)
    return out


# ───────────────────────────── extraction ─────────────────────────────

def _event_vertices(ev: dict) -> list[list[float]]:
    sampled = ev.get("sampled_vertices_norm_tl")
    if sampled and isinstance(sampled, list) and sampled[0]:
        # Pick the longest contour if multiple
        best = max(sampled, key=len)
        return best
    return ev.get("vertices_norm_tl") or []


def _merge_split_fill_stroke(closed_paths: list[dict]) -> list[dict]:
    """Merge pairs of identical-bbox fill-only + stroke-only draw_path events into one polygon event."""
    merged: list[dict] = []
    used: set[int] = set()
    for i, a in enumerate(closed_paths):
        if i in used:
            continue
        abb = a["bbox_norm_tl"]
        a_fill = a.get("facecolor_hex", "none") != "none" and float(a.get("face_alpha", 0.0)) > 0
        a_edge = a.get("edgecolor_hex", "none") != "none" and float(a.get("edge_alpha", 0.0)) > 0
        found = None
        if a_fill != a_edge:
            for j in range(i + 1, len(closed_paths)):
                if j in used:
                    continue
                b = closed_paths[j]
                bbb = b["bbox_norm_tl"]
                if max(abs(abb[k] - bbb[k]) for k in range(4)) > 5e-4:
                    continue
                b_fill = b.get("facecolor_hex", "none") != "none" and float(b.get("face_alpha", 0.0)) > 0
                b_edge = b.get("edgecolor_hex", "none") != "none" and float(b.get("edge_alpha", 0.0)) > 0
                if (a_fill and b_edge and not b_fill) or (a_edge and b_fill and not b_edge):
                    found = j
                    break
        if found is not None:
            b = closed_paths[found]
            used.add(found)
            comb = dict(a)
            if not a_fill:
                comb["facecolor_hex"] = b.get("facecolor_hex", "none")
                comb["face_alpha"] = b.get("face_alpha", 0.0)
            if not a_edge:
                comb["edgecolor_hex"] = b.get("edgecolor_hex", "none")
                comb["edge_alpha"] = b.get("edge_alpha", 0.0)
                comb["linewidth_pt"] = b.get("linewidth_pt", 0.0)
                comb["linetype"] = b.get("linetype", "solid")
            comb["is_rounded"] = bool(a.get("is_rounded")) or bool(b.get("is_rounded"))
            merged.append(comb)
        else:
            merged.append(a)
    return merged


def _build_callout_from_texts(group_texts: list[dict], box_ev: dict | None, canvas_pt: tuple[float, float]) -> dict:
    """Split a callout's text events (top-to-bottom) into title + optional description."""
    W, H = canvas_pt
    ordered = sorted(group_texts, key=lambda e: (e["bbox_norm_tl"][1], e["bbox_norm_tl"][0]))
    if len(ordered) == 1:
        title_evs = ordered
        desc_evs: list[dict] = []
    else:
        w0 = str(ordered[0].get("fontweight", "normal")).lower()
        s0 = float(ordered[0].get("fontsize_pt", 12.0))
        # Split where weight or fontsize changes, or if there is a clear paragraph gap
        split_idx = None
        for idx in range(1, len(ordered)):
            wi = str(ordered[idx].get("fontweight", "normal")).lower()
            si = float(ordered[idx].get("fontsize_pt", 12.0))
            if wi != w0 or abs(si - s0) >= 0.8:
                split_idx = idx
                break
        if split_idx is None:
            # Check for a larger vertical gap between lines (title vs description with same fontface)
            gaps = []
            for idx in range(1, len(ordered)):
                prev_bb = ordered[idx - 1]["bbox_norm_tl"]
                cur_bb = ordered[idx]["bbox_norm_tl"]
                gaps.append(((cur_bb[1] - prev_bb[3]) * H, idx))
            if len(gaps) >= 2:
                max_gap, max_idx = max(gaps, key=lambda g: g[0])
                med_gap = sorted(g[0] for g in gaps)[len(gaps) // 2]
                if max_gap > med_gap + 2.5:
                    split_idx = max_idx
        if split_idx is not None:
            title_evs = ordered[:split_idx]
            desc_evs = ordered[split_idx:]
        else:
            title_evs = ordered
            desc_evs = []

    def _pack_text(evs: list[dict]) -> dict:
        first = evs[0]
        return {
            "text": "\n".join(str(e["text"]).strip() for e in evs),
            "bbox_norm_tl": [round(v, 6) for v in _bbox_union([e["bbox_norm_tl"] for e in evs])],
            "color_hex": first.get("color_hex", "#000000"),
            "alpha": float(first.get("alpha", 1.0)),
            "fontsize_pt": float(first.get("fontsize_pt", 12.0)),
            "fontweight": str(first.get("fontweight", "normal")).lower(),
            "fontstyle": str(first.get("fontstyle", "normal")).lower(),
        }

    title_info = _pack_text(title_evs)
    desc_info = _pack_text(desc_evs) if desc_evs else None
    text_union = _bbox_union([e["bbox_norm_tl"] for e in ordered])

    box_info = None
    if box_ev is not None:
        box_info = {
            "bbox_norm_tl": [round(float(v), 6) for v in box_ev["bbox_norm_tl"]],
            "facecolor_hex": box_ev.get("facecolor_hex", "none"),
            "face_alpha": float(box_ev.get("face_alpha", 0.0)),
            "edgecolor_hex": box_ev.get("edgecolor_hex", "none"),
            "edge_alpha": float(box_ev.get("edge_alpha", 0.0)),
            "linewidth_pt": float(box_ev.get("linewidth_pt", 0.0)),
        }
        callout_bb = box_info["bbox_norm_tl"]
    else:
        callout_bb = [round(v, 6) for v in text_union]

    return {
        "bbox_norm_tl": callout_bb,
        "title": title_info,
        "description": desc_info,
        "box": box_info,
    }


def _extract_python(obs: dict[str, Any], panels: list[dict], canvas_pt: tuple[float, float]) -> tuple[list[dict], set[int]]:
    events = obs.get("events") or []
    owners = obs.get("owners") or []
    assigned: set[int] = set()
    W, H = canvas_pt

    def is_port_event(ev: dict) -> bool:
        if "role" in ev:
            return False
        k = ev.get("owner")
        if k is None or k >= len(owners):
            return False
        mod = owners[k].get("module", "")
        return not mod.startswith(("matplotlib.", "plotnine."))

    port_evs = [e for e in events if is_port_event(e)]
    texts: list[dict] = []
    closed_paths: list[dict] = []
    open_paths: list[dict] = []

    for ev in port_evs:
        kind = ev.get("kind")
        if kind == "text":
            assigned.add(ev["paint_index"])
            if str(ev.get("text", "")).strip():
                texts.append(ev)
        elif kind == "path":
            verts = _event_vertices(ev)
            has_fill = ev.get("facecolor_hex", "none") != "none" and float(ev.get("face_alpha", 0.0)) > 0.0
            has_edge = ev.get("edgecolor_hex", "none") != "none" and float(ev.get("edge_alpha", 0.0)) > 0.0
            is_closed = bool(ev.get("is_closed"))
            if is_closed or has_fill:
                if len(verts) >= 3:
                    closed_paths.append(ev)
                assigned.add(ev["paint_index"])
            elif has_edge and len(verts) >= 2:
                open_paths.append(ev)
                assigned.add(ev["paint_index"])
            else:
                # Invisible/empty path emitted by port artist (e.g. label.fill=NA rect)
                assigned.add(ev["paint_index"])

    closed_paths = _merge_split_fill_stroke(closed_paths)

    # Step 1: Identify label background boxes vs enclosure polygons.
    # A closed path is a label box if it encloses >=1 text event, is tight around its enclosed texts,
    # and is NOT a mark enclosure (specifically: has no stroke or <=2 enclosed panel points and tight margins).
    text_centers = [_bbox_center(t["bbox_norm_tl"]) for t in texts]
    label_box_indices: set[int] = set()
    box_to_text_indices: dict[int, list[int]] = {}

    for ci, pe in enumerate(closed_paths):
        pbb = pe["bbox_norm_tl"]
        inside_t = [
            ti for ti, (tx, ty) in enumerate(text_centers)
            if (pbb[0] - 0.003) <= tx <= (pbb[2] + 0.003) and (pbb[1] - 0.003) <= ty <= (pbb[3] + 0.003)
        ]
        if not inside_t:
            continue
        t_union = _bbox_union([texts[ti]["bbox_norm_tl"] for ti in inside_t])
        pad_l = (t_union[0] - pbb[0]) * W
        pad_r = (pbb[2] - t_union[2]) * W
        pad_t = (t_union[1] - pbb[1]) * H
        pad_b = (pbb[3] - t_union[3]) * H
        # Tight around texts (ggforce label.margin is 2mm ≈ 5.7pt, plus possible minwidth slack on right)
        has_edge = pe.get("edgecolor_hex", "none") != "none" and float(pe.get("edge_alpha", 0.0)) > 0.0
        verts = _event_vertices(pe)
        pts_pt = _clean_poly_pt(verts, canvas_pt)
        is_small_rect = (len(pts_pt) <= 8 or not has_edge) and (max(pad_t, pad_b) <= 30.0) and (pad_l <= 30.0) and (pad_r <= 140.0)
        if is_small_rect and (not has_edge or _poly_area_pt2(pts_pt) < 40000.0):
            # Also verify it isn't actually a huge enclosure containing a scatter cluster
            panel = _panel_for(*_bbox_center(pbb), panels)
            n_pts_in = len(_contained_point_indices(verts, panel["points"], canvas_pt, pad_pt=0.0))
            if n_pts_in <= 1 or not has_edge:
                label_box_indices.add(ci)
                box_to_text_indices[ci] = inside_t

    # Step 2: Cluster texts into callouts (by shared label box first, then vertical proximity for boxless texts)
    used_texts: set[int] = set()
    callouts: list[dict] = []
    # Sort label boxes smallest area first so nested boxes don't steal texts
    for ci in sorted(label_box_indices, key=lambda idx: (closed_paths[idx]["bbox_norm_tl"][2] - closed_paths[idx]["bbox_norm_tl"][0]) *
                                                        (closed_paths[idx]["bbox_norm_tl"][3] - closed_paths[idx]["bbox_norm_tl"][1])):
        t_idxs = [ti for ti in box_to_text_indices[ci] if ti not in used_texts]
        if not t_idxs:
            continue
        used_texts.update(t_idxs)
        co = _build_callout_from_texts([texts[ti] for ti in t_idxs], closed_paths[ci], canvas_pt)
        cx, cy = _bbox_center(co["bbox_norm_tl"])
        co["_panel"] = _panel_for(cx, cy, panels)["panel_index"]
        callouts.append(co)

    # Cluster remaining boxless texts (e.g. label.fill = NA)
    rem_t = [ti for ti in range(len(texts)) if ti not in used_texts]
    rem_t.sort(key=lambda ti: (texts[ti]["bbox_norm_tl"][1], texts[ti]["bbox_norm_tl"][0]))
    clusters: list[list[int]] = []
    for ti in rem_t:
        tbb = texts[ti]["bbox_norm_tl"]
        t_panel = _panel_for(*_bbox_center(tbb), panels)["panel_index"]
        fs = float(texts[ti].get("fontsize_pt", 12.0))
        placed = False
        for cl in clusters:
            cbb = _bbox_union([texts[k]["bbox_norm_tl"] for k in cl])
            c_panel = _panel_for(*_bbox_center(cbb), panels)["panel_index"]
            if c_panel != t_panel:
                continue
            # Horizontally overlapping (or left-aligned within 12pt) and vertically close
            h_overlap = min(tbb[2], cbb[2]) - max(tbb[0], cbb[0])
            left_close = abs(tbb[0] - cbb[0]) * W <= 15.0
            v_gap = max(0.0, (tbb[1] - cbb[3]) * H, (cbb[1] - tbb[3]) * H)
            if (h_overlap > -5.0 / W or left_close) and v_gap <= max(18.0, 1.8 * fs):
                cl.append(ti)
                placed = True
                break
        if not placed:
            clusters.append([ti])

    for cl in clusters:
        co = _build_callout_from_texts([texts[ti] for ti in cl], None, canvas_pt)
        cx, cy = _bbox_center(co["bbox_norm_tl"])
        co["_panel"] = _panel_for(cx, cy, panels)["panel_index"]
        callouts.append(co)

    # Step 3: Remaining closed paths are mark enclosure polygons
    enclosures: list[dict] = []
    for ci, pe in enumerate(closed_paths):
        if ci in label_box_indices:
            continue
        verts = _event_vertices(pe)
        pts_pt = _clean_poly_pt(verts, canvas_pt)
        if len(pts_pt) < 3:
            continue
        bb = [round(float(v), 6) for v in pe["bbox_norm_tl"]]
        cx, cy = _bbox_center(bb)
        panel = _panel_for(cx, cy, panels)
        enclosures.append({
            "panel": panel["panel_index"],
            "panel_obj": panel,
            "polygon": {
                "vertices_norm_tl": [[round(float(v[0]), 6), round(float(v[1]), 6)] for v in verts],
                "bbox_norm_tl": bb,
                "facecolor_hex": pe.get("facecolor_hex", "none"),
                "face_alpha": float(pe.get("face_alpha", 0.0)),
                "edgecolor_hex": pe.get("edgecolor_hex", "none"),
                "edge_alpha": float(pe.get("edge_alpha", 0.0)),
                "linewidth_pt": float(pe.get("linewidth_pt", 0.0)),
                "linetype": str(pe.get("linetype", "solid")),
                "is_rounded": _is_rounded_polygon(verts, canvas_pt, has_curve=bool(pe.get("is_rounded"))),
            },
        })

    # Step 4: Merge/attach open paths (connectors + optional separate underline segment) to callouts
    conns: list[dict] = []
    for oe in open_paths:
        verts = _event_vertices(oe)
        if len(verts) < 2:
            continue
        cx, cy = _bbox_center(oe["bbox_norm_tl"])
        conns.append({
            "_panel": _panel_for(cx, cy, panels)["panel_index"],
            "points_norm_tl": [[round(float(v[0]), 6), round(float(v[1]), 6)] for v in verts],
            "color_hex": oe.get("edgecolor_hex", "#000000"),
            "alpha": float(oe.get("edge_alpha", 1.0)),
            "linewidth_pt": float(oe.get("linewidth_pt", 0.5)),
            "linetype": str(oe.get("linetype", "solid")),
        })

    # Pair connectors to callouts (if a callout has a separate underline segment + connector segment, keep the connector)
    callout_conns: dict[int, dict] = {}
    used_conns: set[int] = set()
    for coi, co in enumerate(callouts):
        bb = co["bbox_norm_tl"]
        candidates = []
        for ci, cn in enumerate(conns):
            if ci in used_conns or cn["_panel"] != co["_panel"]:
                continue
            pts = cn["points_norm_tl"]
            d0 = _point_to_bbox_pt(pts[0][0], pts[0][1], bb, canvas_pt)
            d1 = _point_to_bbox_pt(pts[-1][0], pts[-1][1], bb, canvas_pt)
            dmin = min(d0, d1)
            dmax = max(d0, d1)
            if dmin <= 14.0:
                candidates.append((dmax, dmin, ci))
        if candidates:
            # Prefer the segment whose other end actually leaves the callout box (dmax largest)
            candidates.sort(key=lambda t: (-t[0], t[1]))
            _, _, best_ci = candidates[0]
            used_conns.add(best_ci)
            callout_conns[coi] = {k: v for k, v in conns[best_ci].items() if not k.startswith("_")}
            # Also mark any pure underline segment along the same callout box as consumed
            for dmax, dmin, ci in candidates[1:]:
                if dmax <= 6.0:
                    used_conns.add(ci)

    # Step 5: Pair callouts (with their connectors) to enclosures in the same panel
    paired_co: dict[int, dict] = {}
    paired_cn: dict[int, dict] = {}
    used_enc: set[int] = set()

    for coi, co in enumerate(callouts):
        cn = callout_conns.get(coi)
        bb = co["bbox_norm_tl"]
        best_ei = None
        best_dist = float("inf")
        for ei, enc in enumerate(enclosures):
            if ei in used_enc or enc["panel"] != co["_panel"]:
                continue
            poly_pts = _clean_poly_pt(enc["polygon"]["vertices_norm_tl"], canvas_pt)
            if cn is not None:
                pts = cn["points_norm_tl"]
                d0 = _point_to_bbox_pt(pts[0][0], pts[0][1], bb, canvas_pt)
                d1 = _point_to_bbox_pt(pts[-1][0], pts[-1][1], bb, canvas_pt)
                far = pts[-1] if d0 <= d1 else pts[0]
                fx, fy = far[0] * W, far[1] * H
                d = 0.0 if _point_in_poly_pt(fx, fy, poly_pts) else _point_to_poly_boundary_pt(fx, fy, poly_pts)
            else:
                cx, cy = _bbox_center(bb)
                d = _point_to_poly_boundary_pt(cx * W, cy * H, poly_pts)
            if d < best_dist:
                best_dist = d
                best_ei = ei
        if best_ei is not None:
            used_enc.add(best_ei)
            paired_co[best_ei] = {k: v for k, v in co.items() if not k.startswith("_")}
            if cn is not None:
                paired_cn[best_ei] = cn

    comps: list[dict] = []
    for ei, enc in enumerate(enclosures):
        panel = enc["panel_obj"]
        poly = enc["polygon"]
        c_idx = _contained_point_indices(poly["vertices_norm_tl"], panel["points"], canvas_pt, pad_pt=1.5)
        comps.append({
            "panel": enc["panel"],
            "panel_bbox_norm_tl": panel["bbox_norm_tl"],
            "panel_points": panel["points"],
            "multi_panel": len(panels) > 1,
            "polygon": poly,
            "contained_points": c_idx,
            "callout": paired_co.get(ei),
            "connector": paired_cn.get(ei),
            "canvas_pt": list(canvas_pt),
        })

    return comps, assigned


def components(engine: str, obs: dict[str, Any], expect: dict[str, Any],
               canvas_pt: tuple[float, float] = (640.0, 480.0)) -> tuple[list[dict], set[int]]:
    st = obs.get("ggforce") or {}
    panels = st.get("panels") or []
    if engine == "python":
        return _extract_python(obs, panels, canvas_pt)
    if engine != "r":
        raise ValueError(f"ggforce adapter has no extractor for engine {engine!r}")
    by_pi = {p["panel_index"]: p for p in panels}
    comps: list[dict] = []
    for raw in st.get("marks") or []:
        pi = int(raw.get("panel", 1))
        panel = by_pi.get(pi, {"panel_index": pi, "bbox_norm_tl": [0.0, 0.0, 1.0, 1.0], "points": []})
        poly = dict(raw["polygon"])
        verts = poly.get("vertices_norm_tl") or []
        poly["is_rounded"] = _is_rounded_polygon(verts, canvas_pt, has_curve=False)
        c_idx = _contained_point_indices(verts, panel["points"], canvas_pt, pad_pt=1.5)
        comps.append({
            "panel": pi,
            "panel_bbox_norm_tl": panel["bbox_norm_tl"],
            "panel_points": panel["points"],
            "multi_panel": len(panels) > 1,
            "polygon": poly,
            "contained_points": c_idx,
            "callout": raw.get("callout") or None,
            "connector": raw.get("connector") or None,
            "canvas_pt": list(canvas_pt),
        })
    return comps, set()


def _title_key(comp: dict) -> str:
    co = comp.get("callout")
    if co and co.get("title"):
        return " ".join(_norm_words(co["title"].get("text")))
    return ""


def component_id(i: int, comp: dict) -> str:
    prefix = f"p{comp['panel']}:" if comp.get("multi_panel") else ""
    t = _title_key(comp)
    if t:
        return f"mark[{prefix}{t[:24]!r}]"
    kind = comp["polygon"].get("geom_kind", "shape")
    return f"mark[{prefix}{kind}#{i + 1}]"


def describe_component(comp: dict) -> dict:
    return {
        "panel": comp["panel"],
        "title": _title_key(comp) or None,
        "has_callout": comp.get("callout") is not None,
        "has_connector": comp.get("connector") is not None,
        "bbox": comp["polygon"]["bbox_norm_tl"],
    }


def _cluster_center_norm(comp: dict) -> tuple[float, float]:
    pts = comp.get("panel_points") or []
    idxs = comp.get("contained_points") or []
    if pts and idxs:
        xs = [float(pts[k]["x_norm"]) for k in idxs if k < len(pts)]
        ys = [float(pts[k]["y_norm"]) for k in idxs if k < len(pts)]
        if xs:
            return sum(xs) / len(xs), sum(ys) / len(ys)
    return _bbox_center(comp["polygon"]["bbox_norm_tl"])


def match(ref: list[dict], cand: list[dict]) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Pair marks within the same panel by (1) callout title text when present, then (2) enclosed cluster overlap / centroid."""
    pairs: list[tuple[int, int]] = []
    used_r: set[int] = set()
    used_c: set[int] = set()

    # Pass 1: exact (panel, normalized title) matching for labelled marks
    buckets: dict[tuple[int, str], list[int]] = {}
    for j, c in enumerate(cand):
        tk = _title_key(c)
        if tk:
            buckets.setdefault((c["panel"], tk), []).append(j)
    for i, r in enumerate(ref):
        tk = _title_key(r)
        if not tk:
            continue
        cands = buckets.get((r["panel"], tk))
        if cands:
            rx, ry = _cluster_center_norm(r)
            best_j = min(cands, key=lambda j: math.hypot(_cluster_center_norm(cand[j])[0] - rx,
                                                         _cluster_center_norm(cand[j])[1] - ry))
            cands.remove(best_j)
            pairs.append((i, best_j))
            used_r.add(i)
            used_c.add(best_j)

    # Pass 2: greedy min-cost pairing on remaining marks within same panel by cluster point overlap & centroid distance
    edges: list[tuple[float, int, int]] = []
    for i, r in enumerate(ref):
        if i in used_r:
            continue
        rx, ry = _cluster_center_norm(r)
        r_set = set(r.get("contained_points") or [])
        for j, c in enumerate(cand):
            if j in used_c or c["panel"] != r["panel"]:
                continue
            cx, cy = _cluster_center_norm(c)
            dist = math.hypot(cx - rx, cy - ry)
            c_set = set(c.get("contained_points") or [])
            if r_set or c_set:
                inter = len(r_set & c_set)
                union = max(1, len(r_set | c_set))
                jacc_cost = 1.0 - inter / union
                cost = 0.6 * jacc_cost + 0.4 * dist
                if inter == 0 and dist > 0.25:
                    continue
            else:
                cost = dist
                if dist > 0.25:
                    continue
            if cost <= 0.65:
                edges.append((cost, i, j))
    edges.sort(key=lambda e: e[0])
    for _, i, j in edges:
        if i not in used_r and j not in used_c:
            pairs.append((i, j))
            used_r.add(i)
            used_c.add(j)

    missing = [i for i in range(len(ref)) if i not in used_r]
    unexpected = [j for j in range(len(cand)) if j not in used_c]
    return pairs, missing, unexpected


# ───────────────────────────── per-mark checks ─────────────────────────────

def callout_text(r, c, P, add, expect):
    rc, cc = r.get("callout"), c.get("callout")
    if rc is None and cc is None:
        return False
    if (rc is None) != (cc is None):
        add("callout_text", rc is not None, cc is not None,
            "Callout presence mismatch.", sub="present")
        return
    r_tw = _norm_words(rc["title"]["text"]) if rc.get("title") else []
    c_tw = _norm_words(cc["title"]["text"]) if cc.get("title") else []
    r_dw = _norm_words(rc["description"]["text"]) if rc.get("description") else []
    c_dw = _norm_words(cc["description"]["text"]) if cc.get("description") else []
    if r_tw != c_tw:
        add("callout_text", " ".join(r_tw), " ".join(c_tw),
            f"Callout title {' '.join(c_tw)!r}, expected {' '.join(r_tw)!r}.", sub="title")
    if r_dw != c_dw:
        add("callout_text", " ".join(r_dw), " ".join(c_dw),
            f"Callout description {' '.join(c_dw)!r}, expected {' '.join(r_dw)!r}.", sub="description")


def _poly_panel_local_pt(verts_norm: list[list[float]], panel_bb: list[float], ref_panel_wh_pt: tuple[float, float]) -> list[tuple[float, float]]:
    """Map normalized polygon vertices into panel-local coordinates scaled to ref_panel_wh_pt.
    Eliminates false Hausdorff shifts caused by slight axis-label margin differences between R and Python."""
    px0, py0, px1, py1 = panel_bb
    pw = max(px1 - px0, 1e-6)
    ph = max(py1 - py0, 1e-6)
    W_p, H_p = ref_panel_wh_pt
    pts: list[tuple[float, float]] = []
    for v in verts_norm:
        x = ((float(v[0]) - px0) / pw) * W_p
        y = ((float(v[1]) - py0) / ph) * H_p
        if not pts or math.hypot(x - pts[-1][0], y - pts[-1][1]) > 0.15:
            pts.append((x, y))
    if len(pts) >= 2 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) <= 0.15:
        pts.pop()
    return pts


def mark_enclosure(r, c, P, add, expect):
    W, H = tuple(c["canvas_pt"])
    r_pts = _clean_poly_pt(r["polygon"]["vertices_norm_tl"], (W, H))
    c_pts = _clean_poly_pt(c["polygon"]["vertices_norm_tl"], (W, H))
    if len(r_pts) < 3 or len(c_pts) < 3:
        add("mark_enclosure", ">= 3 vertices", len(c_pts), "Degenerate enclosure polygon.", sub="vertices")
        return

    # 1. Target point containment & expand padding — each engine measured against its OWN panel_points!
    r_panel_pts = r.get("panel_points") or []
    c_panel_pts = c.get("panel_points") or []
    if r_panel_pts and len(r_panel_pts) == len(c_panel_pts):
        missed = 0
        extra = 0
        r_min_pad = float("inf")
        c_min_pad = float("inf")
        for rpt, cpt in zip(r_panel_pts, c_panel_pts):
            rx, ry = float(rpt["x_norm"]) * W, float(rpt["y_norm"]) * H
            cx, cy = float(cpt["x_norm"]) * W, float(cpt["y_norm"]) * H
            rd = _point_signed_dist_poly_pt(rx, ry, r_pts)
            cd = _point_signed_dist_poly_pt(cx, cy, c_pts)
            if rd >= 1.5:
                if cd < -2.0:
                    missed += 1
                if rd < r_min_pad:
                    r_min_pad = rd
                if cd < c_min_pad:
                    c_min_pad = cd
            elif rd < -5.0 and cd > 3.0:
                extra += 1
        if missed > 0:
            add("mark_enclosure", 0, missed,
                f"Enclosure misses {missed} point(s) covered by R's mark.", delta=missed, sub="missed_points")
        if extra > 0:
            add("mark_enclosure", 0, extra,
                f"Enclosure swallows {extra} external point(s) excluded by R's mark.", delta=extra, sub="extra_points")
        if math.isfinite(r_min_pad) and math.isfinite(c_min_pad):
            d_pad = abs(c_min_pad - r_min_pad)
            if exceeds(d_pad, 6.5, "mark_enclosure"):
                add("mark_enclosure", round(r_min_pad, 1), round(c_min_pad, 1),
                    f"Enclosure point padding {c_min_pad:.1f}pt vs R's {r_min_pad:.1f}pt (expand mismatch).",
                    delta=round(c_min_pad - r_min_pad, 2), sub="expand_pad")

    # 2. Area parity (catches wrong shape family like rect vs ellipse/circle, or convex vs concave hull)
    ra = _poly_area_pt2(r_pts)
    ca = _poly_area_pt2(c_pts)
    rel_tol = float(P.get("rel_tol", 0.35))
    if ra > 10.0:
        ratio = ca / ra
        lo = max(0.1, 1.0 - rel_tol)
        hi = 1.0 / lo
        if ratio < lo or ratio > hi:
            add("mark_enclosure", round(ra, 1), round(ca, 1),
                f"Enclosure area {ca:.0f}pt² vs R's {ra:.0f}pt² (ratio {ratio:.2f}, expected {lo:.2f}..{hi:.2f}).",
                delta=round(ratio - 1.0, 3), sub="area")

    # 3. Boundary Hausdorff distance in panel-local coordinates
    rpbb = r["panel_bbox_norm_tl"]
    cpbb = c["panel_bbox_norm_tl"]
    ref_panel_wh = ((rpbb[2] - rpbb[0]) * W, (rpbb[3] - rpbb[1]) * H)
    r_loc = _poly_panel_local_pt(r["polygon"]["vertices_norm_tl"], rpbb, ref_panel_wh)
    c_loc = _poly_panel_local_pt(c["polygon"]["vertices_norm_tl"], cpbb, ref_panel_wh)
    rbb = r["polygon"]["bbox_norm_tl"]
    diag_pt = math.hypot((rbb[2] - rbb[0]) * W, (rbb[3] - rbb[1]) * H)
    h_tol = max(float(P["tol"]), 0.14 * diag_pt)
    hd = _poly_hausdorff_pt(r_loc, c_loc)
    if exceeds(hd, h_tol, "mark_enclosure"):
        add("mark_enclosure", f"<= {h_tol:.1f}pt", round(hd, 2),
            f"Enclosure boundary differs by {hd:.1f}pt Hausdorff (limit {h_tol:.1f}pt).",
            delta=round(hd, 2), sub="shape")


def mark_style(r, c, P, add, expect):
    rp, cp = r["polygon"], c["polygon"]
    de_f = color_delta_e(rp["facecolor_hex"], cp["facecolor_hex"])
    if exceeds(de_f, P["tol"], "mark_style"):
        add("mark_style", rp["facecolor_hex"], cp["facecolor_hex"],
            f"Mark fill {cp['facecolor_hex']}, expected {rp['facecolor_hex']} (ΔE {de_f}).",
            delta=de_f, sub="fill")
    da_f = abs(cp["face_alpha"] - rp["face_alpha"])
    if exceeds(da_f, P["alpha_tol"], "mark_style"):
        add("mark_style", rp["face_alpha"], cp["face_alpha"],
            f"Mark fill alpha {cp['face_alpha']}, expected {rp['face_alpha']}.",
            delta=round(cp["face_alpha"] - rp["face_alpha"], 4), sub="fill_alpha")
    de_e = color_delta_e(rp["edgecolor_hex"], cp["edgecolor_hex"])
    if exceeds(de_e, P["tol"], "mark_style"):
        add("mark_style", rp["edgecolor_hex"], cp["edgecolor_hex"],
            f"Mark edge {cp['edgecolor_hex']}, expected {rp['edgecolor_hex']} (ΔE {de_e}).",
            delta=de_e, sub="edge")
    da_e = abs(cp["edge_alpha"] - rp["edge_alpha"])
    if exceeds(da_e, P["alpha_tol"], "mark_style"):
        add("mark_style", rp["edge_alpha"], cp["edge_alpha"],
            f"Mark edge alpha {cp['edge_alpha']}, expected {rp['edge_alpha']}.",
            delta=round(cp["edge_alpha"] - rp["edge_alpha"], 4), sub="edge_alpha")
    if rp["edgecolor_hex"] != "none" and cp["edgecolor_hex"] != "none":
        dlw = abs(cp["linewidth_pt"] - rp["linewidth_pt"])
        lw_tol = max(0.6, 0.35 * rp["linewidth_pt"])
        if exceeds(dlw, lw_tol, "mark_style"):
            add("mark_style", rp["linewidth_pt"], cp["linewidth_pt"],
                f"Mark linewidth {cp['linewidth_pt']:.2f}pt, expected {rp['linewidth_pt']:.2f}pt.",
                delta=round(cp["linewidth_pt"] - rp["linewidth_pt"], 3), sub="linewidth")
    if bool(rp["is_rounded"]) != bool(cp["is_rounded"]):
        add("mark_style", bool(rp["is_rounded"]), bool(cp["is_rounded"]),
            f"Mark rounded={cp['is_rounded']}, expected {rp['is_rounded']}.", sub="rounded")


def callout_layout(r, c, P, add, expect):
    rc, cc = r.get("callout"), c.get("callout")
    if rc is None or cc is None:
        return False
    W, H = tuple(c["canvas_pt"])

    def overflow_pt(co, pbb):
        bb = co["bbox_norm_tl"]
        return max(0.0, (pbb[0] - bb[0]) * W, (bb[2] - pbb[2]) * W, (pbb[1] - bb[1]) * H, (bb[3] - pbb[3]) * H)

    ro = overflow_pt(rc, r["panel_bbox_norm_tl"])
    co_ov = overflow_pt(cc, c["panel_bbox_norm_tl"])
    if ro <= P["tol"] and exceeds(co_ov, P["tol"], "callout_layout"):
        add("callout_layout", f"<= {P['tol']}pt", round(co_ov, 2),
            f"Callout extends {co_ov:.1f}pt outside panel bounds.", delta=round(co_ov, 2), sub="inside_panel")

    # Title must sit above description when both exist
    if cc.get("title") and cc.get("description"):
        _, ty = _bbox_center(cc["title"]["bbox_norm_tl"])
        _, dy = _bbox_center(cc["description"]["bbox_norm_tl"])
        if ty >= dy:
            add("callout_layout", "title above description", f"title_y={ty:.3f}, desc_y={dy:.3f}",
                "Callout title does not sit above its description.", sub="title_above_desc")


def callout_style(r, c, P, add, expect):
    rc, cc = r.get("callout"), c.get("callout")
    if rc is None or cc is None:
        return False

    def _check_text(rt: dict, ct: dict, prefix: str):
        de = color_delta_e(rt["color_hex"], ct["color_hex"])
        if exceeds(de, P["tol"], "callout_style"):
            add("callout_style", rt["color_hex"], ct["color_hex"],
                f"Callout {prefix} color {ct['color_hex']}, expected {rt['color_hex']} (ΔE {de}).",
                delta=de, sub=f"{prefix}_color")
        da = abs(ct["alpha"] - rt["alpha"])
        if exceeds(da, P["alpha_tol"], "callout_style"):
            add("callout_style", rt["alpha"], ct["alpha"],
                f"Callout {prefix} alpha {ct['alpha']}, expected {rt['alpha']}.",
                delta=round(ct["alpha"] - rt["alpha"], 4), sub=f"{prefix}_alpha")
        ds = abs(ct["fontsize_pt"] - rt["fontsize_pt"])
        if exceeds(ds, 1.5, "callout_style"):
            add("callout_style", rt["fontsize_pt"], ct["fontsize_pt"],
                f"Callout {prefix} fontsize {ct['fontsize_pt']:.1f}pt, expected {rt['fontsize_pt']:.1f}pt.",
                delta=round(ct["fontsize_pt"] - rt["fontsize_pt"], 2), sub=f"{prefix}_size")
        if rt["fontweight"] != ct["fontweight"]:
            add("callout_style", rt["fontweight"], ct["fontweight"],
                f"Callout {prefix} weight {ct['fontweight']}, expected {rt['fontweight']}.",
                sub=f"{prefix}_weight")
        if rt["fontstyle"] != ct["fontstyle"]:
            add("callout_style", rt["fontstyle"], ct["fontstyle"],
                f"Callout {prefix} style {ct['fontstyle']}, expected {rt['fontstyle']}.",
                sub=f"{prefix}_style")

    if rc.get("title") and cc.get("title"):
        _check_text(rc["title"], cc["title"], "title")
    if rc.get("description") and cc.get("description"):
        _check_text(rc["description"], cc["description"], "desc")

    # Label box fill check
    rb = rc.get("box")
    cb = cc.get("box")
    r_has_fill = bool(rb and rb.get("facecolor_hex", "none") != "none" and float(rb.get("face_alpha", 0.0)) > 0.01)
    c_has_fill = bool(cb and cb.get("facecolor_hex", "none") != "none" and float(cb.get("face_alpha", 0.0)) > 0.01)
    if r_has_fill != c_has_fill:
        add("callout_style", r_has_fill, c_has_fill,
            f"Callout box fill present={c_has_fill}, expected {r_has_fill}.", sub="box_fill_present")
    elif r_has_fill and c_has_fill:
        de_b = color_delta_e(rb["facecolor_hex"], cb["facecolor_hex"])
        if exceeds(de_b, P["tol"], "callout_style"):
            add("callout_style", rb["facecolor_hex"], cb["facecolor_hex"],
                f"Callout box fill {cb['facecolor_hex']}, expected {rb['facecolor_hex']} (ΔE {de_b}).",
                delta=de_b, sub="box_fill")
        da_b = abs(float(cb["face_alpha"]) - float(rb["face_alpha"]))
        if exceeds(da_b, P["alpha_tol"], "callout_style"):
            add("callout_style", rb["face_alpha"], cb["face_alpha"],
                f"Callout box fill alpha {cb['face_alpha']}, expected {rb['face_alpha']}.",
                delta=round(float(cb["face_alpha"]) - float(rb["face_alpha"]), 4), sub="box_alpha")


def connector_connects(r, c, P, add, expect):
    rcon, ccon = r.get("connector"), c.get("connector")
    if rcon is None and ccon is None:
        return False
    if (rcon is None) != (ccon is None):
        add("connector_connects", rcon is not None, ccon is not None,
            f"Connector present={ccon is not None}, expected {rcon is not None}.", sub="present")
        return

    W, H = tuple(c["canvas_pt"])
    cc = c.get("callout")
    pts = ccon.get("points_norm_tl") or []
    if len(pts) < 2:
        add("connector_connects", ">= 2 points", len(pts), "Connector polyline has fewer than 2 points.", sub="points")
        return

    # 1. One end touches the callout box/underline
    if cc is not None:
        bb = cc["bbox_norm_tl"]
        d0 = _point_to_bbox_pt(pts[0][0], pts[0][1], bb, (W, H))
        d1 = _point_to_bbox_pt(pts[-1][0], pts[-1][1], bb, (W, H))
        near_box = min(d0, d1)
        if exceeds(near_box, P["tol"], "connector_connects"):
            add("connector_connects", f"<= {P['tol']}pt", round(near_box, 2),
                f"Connector does not reach callout box (gap {near_box:.1f}pt).",
                delta=round(near_box, 2), sub="callout_end")
        far_pt = pts[-1] if d0 <= d1 else pts[0]
    else:
        far_pt = pts[-1]

    # 2. Far end reaches near (or inside) the mark enclosure polygon (allowing con.cap ≈ 8.5pt + slack)
    c_poly = _clean_poly_pt(c["polygon"]["vertices_norm_tl"], (W, H))
    fx, fy = far_pt[0] * W, far_pt[1] * H
    dist_mark = 0.0 if _point_in_poly_pt(fx, fy, c_poly) else _point_to_poly_boundary_pt(fx, fy, c_poly)
    mark_tol = max(16.0, float(P["tol"]) * 2.5)
    if exceeds(dist_mark, mark_tol, "connector_connects"):
        add("connector_connects", f"<= {mark_tol:.1f}pt", round(dist_mark, 2),
            f"Connector far end is {dist_mark:.1f}pt away from mark enclosure.",
            delta=round(dist_mark, 2), sub="mark_end")

    # 3. Connector style: color, alpha, linetype, linewidth
    de = color_delta_e(rcon["color_hex"], ccon["color_hex"])
    if exceeds(de, 6.0, "connector_connects"):
        add("connector_connects", rcon["color_hex"], ccon["color_hex"],
            f"Connector color {ccon['color_hex']}, expected {rcon['color_hex']} (ΔE {de}).",
            delta=de, sub="color")
    da = abs(float(ccon["alpha"]) - float(rcon["alpha"]))
    if exceeds(da, P.get("alpha_tol", 0.05), "connector_connects"):
        add("connector_connects", rcon["alpha"], ccon["alpha"],
            f"Connector alpha {ccon['alpha']}, expected {rcon['alpha']}.",
            delta=round(float(ccon["alpha"]) - float(rcon["alpha"]), 4), sub="alpha")
    if str(rcon.get("linetype", "solid")) != str(ccon.get("linetype", "solid")):
        add("connector_connects", rcon.get("linetype", "solid"), ccon.get("linetype", "solid"),
            f"Connector linetype {ccon.get('linetype')!r}, expected {rcon.get('linetype')!r}.",
            sub="linetype")
    dlw = abs(float(ccon["linewidth_pt"]) - float(rcon["linewidth_pt"]))
    if exceeds(dlw, max(0.7, 0.4 * float(rcon["linewidth_pt"])), "connector_connects"):
        add("connector_connects", rcon["linewidth_pt"], ccon["linewidth_pt"],
            f"Connector linewidth {ccon['linewidth_pt']:.2f}pt, expected {rcon['linewidth_pt']:.2f}pt.",
            delta=round(float(ccon["linewidth_pt"]) - float(rcon["linewidth_pt"]), 3), sub="linewidth")


CHECKS = {
    f.__name__: f
    for f in (callout_text, mark_enclosure, mark_style, callout_layout, callout_style, connector_connects)
}


# ───────────────────────────── whole-plot invariant checks ─────────────────────────────

def _count_callout_overlaps(comps: list[dict]) -> list[tuple[str, str]]:
    with_co = [c for c in comps if c.get("callout") is not None]
    bad = []
    for i in range(len(with_co)):
        for j in range(i + 1, len(with_co)):
            if with_co[i]["panel"] != with_co[j]["panel"]:
                continue
            if _boxes_overlap_pt(
                with_co[i]["callout"]["bbox_norm_tl"],
                with_co[j]["callout"]["bbox_norm_tl"],
                tuple(with_co[i]["canvas_pt"]),
            ):
                bad.append((_title_key(with_co[i]), _title_key(with_co[j])))
    return bad


def callout_no_overlap(rc, cc, pairs, P, add, expect):
    r_with = [c for c in rc if c.get("callout") is not None]
    c_with = [c for c in cc if c.get("callout") is not None]
    if len(r_with) < 2 or len(c_with) < 2:
        return False
    r_bad = _count_callout_overlaps(rc)
    c_bad = _count_callout_overlaps(cc)
    if len(c_bad) > len(r_bad):
        add("callout_no_overlap", len(r_bad), len(c_bad),
            f"{len(c_bad)} overlapping callout pair(s) (e.g. {c_bad[:3]}), expected <= {len(r_bad)}.",
            delta=len(c_bad) - len(r_bad))


def _count_callout_point_overlaps(comps: list[dict], clearance_pt: float = 1.0) -> list[str]:
    bad: list[str] = []
    for c in comps:
        co = c.get("callout")
        if co is None:
            continue
        W, H = tuple(c["canvas_pt"])
        bb = co["bbox_norm_tl"]
        for pt in c.get("panel_points") or []:
            rad = 0.5 * float(pt.get("size_pt", 3.5))
            if _point_to_bbox_pt(pt["x_norm"], pt["y_norm"], bb, (W, H)) < rad + clearance_pt:
                bad.append(_title_key(c) or "callout")
    return bad


def callout_no_point_overlap(rc, cc, pairs, P, add, expect):
    if not any(c.get("callout") is not None and c.get("panel_points") for c in cc):
        return False
    clearance = float(P.get("min", 1.0))
    r_bad = _count_callout_point_overlaps(rc, clearance)
    c_bad = _count_callout_point_overlaps(cc, clearance)
    if len(c_bad) > len(r_bad) + int(P.get("tol", 0)):
        sample = list(dict.fromkeys(c_bad))[:4]
        add("callout_no_point_overlap", len(r_bad), len(c_bad),
            f"{len(c_bad)} callout-point overlap(s) within dot radius+{clearance:g}pt (e.g. {sample}), expected <= {len(r_bad)}.",
            delta=len(c_bad) - len(r_bad))


CASE_CHECKS = {
    "callout_no_overlap": callout_no_overlap,
    "callout_no_point_overlap": callout_no_point_overlap,
}
