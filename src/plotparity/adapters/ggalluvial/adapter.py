"""ggalluvial adapter: strata rectangles, curved flow/alluvium ribbons, and stratum labels.

Both engines extract per-panel shapes (with R x-splines evaluated via grid::xsplinePoints) and
text/label annotations in panel data units (observe.R, observe_py.py). Under coord_flip(),
coordinates are canonicalized to (u, v) = (axis coordinate, stack/weight coordinate) so all
geometric checks work identically for vertical and horizontal alluvial diagrams.

Components have `kind` in {"stratum", "flow", "label"}:
  - strata pair by (panel, axis_idx, vertical rank)
  - flows/alluvia pair by (panel, span_idx, start-to-end vertical rank)
  - labels pair by (panel, text, spatial order)
Matching never inspects color, alpha, or interior curve shape.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from ...compare import color_delta_e, exceeds

NAME = "ggalluvial"
VERSION = "0.1.0"
HERE = Path(__file__).resolve().parent
OBSERVE_R = HERE / "observe.R"
PROFILE = HERE / "profile.toml"
EXPECT_KEYS: set[str] = set()


# ───────────────────────────── geometry helpers ─────────────────────────────

def _polygon_area(u: np.ndarray, v: np.ndarray) -> float:
    return 0.5 * abs(float(np.dot(u, np.roll(v, -1)) - np.dot(v, np.roll(u, -1))))


def _is_axis_aligned_rect(u: np.ndarray, v: np.ndarray) -> bool:
    n_u = len(np.unique(np.round(u, 4)))
    n_v = len(np.unique(np.round(v, 4)))
    if n_u != 2 or n_v != 2:
        return False
    bbox_area = float((u.max() - u.min()) * (v.max() - v.min()))
    if bbox_area <= 0:
        return False
    area = _polygon_area(u, v)
    return abs(area - bbox_area) <= 1e-2 * bbox_area


def _envelope_at(u: np.ndarray, v: np.ndarray, stations: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Intersect closed polygon (u, v) with vertical lines u = s for each station s.
    Returns (v_bot, v_top) arrays of shape (len(stations),)."""
    if len(u) < 3:
        nan = np.full(len(stations), np.nan)
        return nan, nan
    # Ensure closed ring
    if abs(u[0] - u[-1]) > 1e-9 or abs(v[0] - v[-1]) > 1e-9:
        u = np.append(u, u[0])
        v = np.append(v, v[0])
    u0, u1 = u[:-1], u[1:]
    v0, v1 = v[:-1], v[1:]
    du = u1 - u0
    valid = np.abs(du) > 1e-12
    u0, u1, v0, v1, du = u0[valid], u1[valid], v0[valid], v1[valid], du[valid]
    lo = np.minimum(u0, u1)
    hi = np.maximum(u0, u1)

    v_bot = np.full(len(stations), np.nan)
    v_top = np.full(len(stations), np.nan)
    for k, s in enumerate(stations):
        m = (lo <= s + 1e-9) & (s - 1e-9 <= hi)
        if not m.any():
            continue
        t = np.clip((s - u0[m]) / du[m], 0.0, 1.0)
        vs = v0[m] + t * (v1[m] - v0[m])
        v_bot[k] = float(np.min(vs))
        v_top[k] = float(np.max(vs))
    return v_bot, v_top


def _cluster_1d(values: list[float], tol: float = 0.25) -> list[int]:
    """Assign each value to a sorted 0-based cluster index (gaps > tol start a new cluster)."""
    if not values:
        return []
    order = sorted(range(len(values)), key=lambda i: values[i])
    clusters = [0] * len(values)
    cid = 0
    prev = values[order[0]]
    for idx in order:
        if values[idx] - prev > tol:
            cid += 1
        clusters[idx] = cid
        prev = values[idx]
    return clusters


def _cluster_spans(spans: list[tuple[float, float]], tol: float = 0.35) -> list[int]:
    """Assign each (u_mid, u_span) flow span to a sorted 0-based span index."""
    if not spans:
        return []
    reps: list[list[float]] = []  # [mean_mid, mean_span, count]
    raw_ids: list[int] = []
    for mid, sp in spans:
        best = None
        for ci, (rm, rs, _) in enumerate(reps):
            if abs(mid - rm) <= tol and abs(sp - rs) <= tol:
                d = abs(mid - rm) + abs(sp - rs)
                if best is None or d < best[0]:
                    best = (d, ci)
        if best is None:
            raw_ids.append(len(reps))
            reps.append([mid, sp, 1.0])
        else:
            ci = best[1]
            n = reps[ci][2] + 1.0
            reps[ci][0] = (reps[ci][0] * reps[ci][2] + mid) / n
            reps[ci][1] = (reps[ci][1] * reps[ci][2] + sp) / n
            reps[ci][2] = n
            raw_ids.append(ci)
    sorted_order = sorted(range(len(reps)), key=lambda ci: (round(reps[ci][0], 3), round(reps[ci][1], 3)))
    remap = {old: new for new, old in enumerate(sorted_order)}
    return [remap[cid] for cid in raw_ids]


# ───────────────────────────── components ─────────────────────────────

def components(engine: str, obs: dict[str, Any], expect: dict[str, Any],
               canvas_pt: tuple[float, float] = (640.0, 480.0)) -> tuple[list[dict], set[int]]:
    st = obs.get("ggalluvial") or {}
    panels = st.get("panels") or []
    multi_panel = len(panels) > 1
    W, H = canvas_pt

    comps: list[dict] = []

    for panel in panels:
        pi = int(panel.get("panel_index", 1))
        is_flip = bool(panel.get("is_flip", False))
        xr = panel.get("x_range") or [0.0, 1.0]
        yr = panel.get("y_range") or [0.0, 1.0]
        pbb = panel.get("bbox_norm_tl") or [0.0, 0.0, 1.0, 1.0]

        dx = max(float(xr[1]) - float(xr[0]), 1e-9)
        dy = max(float(yr[1]) - float(yr[0]), 1e-9)
        pt_x = max((float(pbb[2]) - float(pbb[0])) * W / dx, 1e-6)
        pt_y = max((float(pbb[3]) - float(pbb[1])) * H / dy, 1e-6)
        pt_u, pt_v = (pt_y, pt_x) if is_flip else (pt_x, pt_y)

        # First pass: parse all non-degenerate shapes into rects vs non-rects
        raw_rects: list[dict] = []
        raw_flows: list[dict] = []

        for sh in sorted(panel.get("shapes") or [], key=lambda e: e.get("paint_index", 0)):
            fc = sh.get("facecolor_hex", "none")
            ec = sh.get("edgecolor_hex", "none")
            if fc == "none" and ec == "none":
                continue
            for pc in sh.get("pieces") or []:
                vd = np.asarray(pc.get("data") or [], dtype=float)
                vn = np.asarray(pc.get("norm") or [], dtype=float)
                if vd.ndim != 2 or len(vd) < 3:
                    continue
                u = vd[:, 1] if is_flip else vd[:, 0]
                v = vd[:, 0] if is_flip else vd[:, 1]
                u0, u1 = float(u.min()), float(u.max())
                v0, v1 = float(v.min()), float(v.max())
                if (u1 - u0) < 1e-6 or (v1 - v0) < 1e-6:
                    continue
                bb = [float(vn[:, 0].min()), float(vn[:, 1].min()),
                      float(vn[:, 0].max()), float(vn[:, 1].max())] if len(vn) else list(pbb)
                base = {
                    "panel": pi,
                    "multi_panel": multi_panel,
                    "is_flip": is_flip,
                    "pt_u": float(pt_u),
                    "pt_v": float(pt_v),
                    "paint_index": int(sh.get("paint_index", 0)),
                    "facecolor_hex": fc,
                    "face_alpha": float(sh.get("face_alpha", 0.0)),
                    "edgecolor_hex": ec,
                    "edge_alpha": float(sh.get("edge_alpha", 0.0)),
                    "linewidth_pt": float(sh.get("linewidth_pt", 0.0)),
                    "u0": u0,
                    "u1": u1,
                    "v0": v0,
                    "v1": v1,
                    "bbox_norm_tl": [round(val, 6) for val in bb],
                    "_u": u,
                    "_v": v,
                }
                if _is_axis_aligned_rect(u, v):
                    raw_rects.append(base)
                else:
                    raw_flows.append(base)

        # Disambiguate any 4-vertex horizontal linear flow from strata:
        # if a rect shares [u0, u1] with a non-rect flow in the same panel, treat it as a flow.
        flow_bounds = [(f["u0"], f["u1"]) for f in raw_flows]
        strata_raw: list[dict] = []
        for r in raw_rects:
            matches_flow_gap = any(abs(r["u0"] - fu0) < 0.05 and abs(r["u1"] - fu1) < 0.05
                                   for fu0, fu1 in flow_bounds)
            if matches_flow_gap:
                raw_flows.append(r)
            else:
                strata_raw.append(r)

        # Build stratum components
        strata_comps: list[dict] = []
        if strata_raw:
            centers = [0.5 * (s["u0"] + s["u1"]) for s in strata_raw]
            axis_ids = _cluster_1d(centers, tol=0.25)
            by_axis: dict[int, list[dict]] = {}
            for s, aid in zip(strata_raw, axis_ids):
                s["axis_idx"] = aid
                s["u_center"] = 0.5 * (s["u0"] + s["u1"])
                s["u_width"] = s["u1"] - s["u0"]
                s["height"] = s["v1"] - s["v0"]
                by_axis.setdefault(aid, []).append(s)
            for aid in sorted(by_axis):
                group = sorted(by_axis[aid], key=lambda s: (s["v0"], s["v1"]))
                for rank, s in enumerate(group):
                    s["kind"] = "stratum"
                    s["v_rank"] = rank
                    s.pop("_u", None)
                    s.pop("_v", None)
                    strata_comps.append(s)

        # Build flow/alluvium components
        flow_comps: list[dict] = []
        if raw_flows:
            spans = [(0.5 * (f["u0"] + f["u1"]), f["u1"] - f["u0"]) for f in raw_flows]
            span_ids = _cluster_spans(spans, tol=0.35)
            by_span: dict[int, list[dict]] = {}
            for f, sid in zip(raw_flows, span_ids):
                u = f.pop("_u")
                v = f.pop("_v")
                du = f["u1"] - f["u0"]
                eps = 0.002 * du
                end_stations = np.array([f["u0"] + eps, f["u1"] - eps], dtype=float)
                eb, et = _envelope_at(u, v, end_stations)
                vb0 = float(eb[0]) if np.isfinite(eb[0]) else f["v0"]
                vt0 = float(et[0]) if np.isfinite(et[0]) else f["v1"]
                vb1 = float(eb[1]) if np.isfinite(eb[1]) else f["v0"]
                vt1 = float(et[1]) if np.isfinite(et[1]) else f["v1"]
                f["kind"] = "flow"
                f["span_idx"] = sid
                f["v_bot_start"] = vb0
                f["v_top_start"] = vt0
                f["v_bot_end"] = vb1
                f["v_top_end"] = vt1
                f["v_mid_start"] = 0.5 * (vb0 + vt0)
                f["v_mid_end"] = 0.5 * (vb1 + vt1)
                f["verts_u"] = u.tolist()
                f["verts_v"] = v.tolist()
                by_span.setdefault(sid, []).append(f)
            for sid in sorted(by_span):
                group = sorted(
                    by_span[sid],
                    key=lambda f: (round(f["v_mid_start"], 4), round(f["v_mid_end"], 4), round(f["v_bot_start"], 4)),
                )
                for rank, f in enumerate(group):
                    f["flow_rank"] = rank
                    flow_comps.append(f)

        # Build label components
        label_comps: list[dict] = []
        raw_labels = panel.get("labels") or []
        by_text: dict[str, list[dict]] = {}
        for lb in raw_labels:
            txt = str(lb.get("text", "")).strip()
            if not txt:
                continue
            lx, ly = float(lb["x"]), float(lb["y"])
            lu, lv = (ly, lx) if is_flip else (lx, ly)
            # Check if label center sits inside any stratum in this panel
            inside_stratum = any(
                (s["u0"] - 1.5 / pt_u <= lu <= s["u1"] + 1.5 / pt_u) and
                (s["v0"] - 1.5 / pt_v <= lv <= s["v1"] + 1.5 / pt_v)
                for s in strata_comps
            )
            rec = {
                "panel": pi,
                "multi_panel": multi_panel,
                "is_flip": is_flip,
                "kind": "label",
                "text": txt,
                "u": lu,
                "v": lv,
                "pt_u": float(pt_u),
                "pt_v": float(pt_v),
                "paint_index": int(lb.get("paint_index", 0)),
                "color_hex": lb.get("color_hex", "#000000"),
                "alpha": float(lb.get("alpha", 1.0)),
                "fontsize_pt": float(lb.get("fontsize_pt", 11.0)),
                "has_box": bool(lb.get("has_box", False)),
                "box_fill_hex": lb.get("box_fill_hex", "none"),
                "box_face_alpha": float(lb.get("box_face_alpha", 0.0)),
                "box_edge_hex": lb.get("box_edge_hex", "none"),
                "box_edge_alpha": float(lb.get("box_edge_alpha", 0.0)),
                "inside_stratum": inside_stratum,
                "has_strata_in_panel": bool(strata_comps),
            }
            by_text.setdefault(txt, []).append(rec)
        for txt in sorted(by_text):
            group = sorted(by_text[txt], key=lambda l: (round(l["u"], 3), round(l["v"], 3)))
            for occ, rec in enumerate(group):
                rec["occ_idx"] = occ
                label_comps.append(rec)

        comps.extend(strata_comps)
        comps.extend(flow_comps)
        comps.extend(label_comps)

    extracted = set(st.get("extracted_classes") or [])
    owners = obs.get("owners") or []
    assigned = {e["paint_index"] for e in obs.get("events") or []
                if e.get("owner") is not None and owners[e["owner"]]["class"] in extracted} if extracted else set()
    return comps, assigned


def component_id(i: int, comp: dict) -> str:
    prefix = f"p{comp['panel']}:" if comp.get("multi_panel") else ""
    kind = comp["kind"]
    if kind == "stratum":
        return f"stratum[{prefix}a{comp['axis_idx'] + 1}#{comp['v_rank'] + 1}]"
    if kind == "flow":
        return f"flow[{prefix}s{comp['span_idx'] + 1}#{comp['flow_rank'] + 1}]"
    occ = f"#{comp['occ_idx'] + 1}" if comp.get("occ_idx", 0) > 0 else ""
    return f"label[{prefix}{comp['text'][:20]!r}{occ}]"


def describe_component(comp: dict) -> dict:
    kind = comp["kind"]
    if kind == "stratum":
        return {
            "kind": "stratum", "panel": comp["panel"], "axis": comp["axis_idx"] + 1,
            "v": [round(comp["v0"], 2), round(comp["v1"], 2)], "fill": comp["facecolor_hex"],
        }
    if kind == "flow":
        return {
            "kind": "flow", "panel": comp["panel"], "span": comp["span_idx"] + 1,
            "u": [round(comp["u0"], 3), round(comp["u1"], 3)],
            "v_start": [round(comp["v_bot_start"], 2), round(comp["v_top_start"], 2)],
            "v_end": [round(comp["v_bot_end"], 2), round(comp["v_top_end"], 2)],
        }
    return {"kind": "label", "panel": comp["panel"], "text": comp["text"],
            "u": round(comp["u"], 3), "v": round(comp["v"], 2)}


def _match_key(comp: dict) -> tuple:
    kind = comp["kind"]
    if kind == "stratum":
        return ("stratum", comp["panel"], comp["axis_idx"], comp["v_rank"])
    if kind == "flow":
        return ("flow", comp["panel"], comp["span_idx"], comp["flow_rank"])
    return ("label", comp["panel"], comp["text"], comp["occ_idx"])


def match(ref: list[dict], cand: list[dict]) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Pair strata by (panel, axis, vertical rank), flows by (panel, span, vertical rank),
    and labels by (panel, text, spatial order)."""
    ci = {_match_key(c): j for j, c in enumerate(cand)}
    pairs, missing = [], []
    for i, r in enumerate(ref):
        key = _match_key(r)
        if key in ci:
            pairs.append((i, ci.pop(key)))
        else:
            missing.append(i)
    used = {j for _, j in pairs}
    return pairs, missing, [j for j in range(len(cand)) if j not in used]


# ───────────────────────────── per-component checks ─────────────────────────────

def stratum_geometry(r, c, P, add, expect):
    if r["kind"] != "stratum":
        return False
    tol, ku, kv = P["tol"], r["pt_u"], r["pt_v"]
    du_c = abs(c["u_center"] - r["u_center"]) * ku
    if exceeds(du_c, tol, "stratum_geometry"):
        add("stratum_geometry", round(r["u_center"], 4), round(c["u_center"], 4),
            f"Stratum axis center is {du_c:.1f}pt off.", delta=round(du_c, 2), sub="u_center")
    du_w = abs(c["u_width"] - r["u_width"]) * ku
    if exceeds(du_w, tol, "stratum_geometry"):
        add("stratum_geometry", round(r["u_width"], 4), round(c["u_width"], 4),
            f"Stratum width is {du_w:.1f}pt off ({c['u_width']:.3f} vs {r['u_width']:.3f}).",
            delta=round(du_w, 2), sub="u_width")
    dv0 = abs(c["v0"] - r["v0"]) * kv
    if exceeds(dv0, tol, "stratum_geometry"):
        add("stratum_geometry", round(r["v0"], 3), round(c["v0"], 3),
            f"Stratum bottom is {dv0:.1f}pt off ({c['v0']:.2f} vs {r['v0']:.2f}).",
            delta=round(dv0, 2), sub="v0")
    dv1 = abs(c["v1"] - r["v1"]) * kv
    if exceeds(dv1, tol, "stratum_geometry"):
        add("stratum_geometry", round(r["v1"], 3), round(c["v1"], 3),
            f"Stratum top is {dv1:.1f}pt off ({c['v1']:.2f} vs {r['v1']:.2f}).",
            delta=round(dv1, 2), sub="v1")


def stratum_style(r, c, P, add, expect):
    if r["kind"] != "stratum":
        return False
    de_f = color_delta_e(r["facecolor_hex"], c["facecolor_hex"])
    if exceeds(de_f, P["tol"], "stratum_style"):
        add("stratum_style", r["facecolor_hex"], c["facecolor_hex"],
            f"Stratum fill {c['facecolor_hex']}, expected {r['facecolor_hex']} (ΔE {de_f}).",
            delta=de_f, sub="fill")
    da_f = abs(c["face_alpha"] - r["face_alpha"])
    if exceeds(da_f, P["alpha_tol"], "stratum_style"):
        add("stratum_style", r["face_alpha"], c["face_alpha"],
            f"Stratum fill alpha {c['face_alpha']}, expected {r['face_alpha']}.",
            delta=round(c["face_alpha"] - r["face_alpha"], 4), sub="fill_alpha")
    de_e = color_delta_e(r["edgecolor_hex"], c["edgecolor_hex"])
    if exceeds(de_e, P["tol"], "stratum_style"):
        add("stratum_style", r["edgecolor_hex"], c["edgecolor_hex"],
            f"Stratum edge {c['edgecolor_hex']}, expected {r['edgecolor_hex']} (ΔE {de_e}).",
            delta=de_e, sub="edge")
    da_e = abs(c["edge_alpha"] - r["edge_alpha"])
    if exceeds(da_e, P["alpha_tol"], "stratum_style"):
        add("stratum_style", r["edge_alpha"], c["edge_alpha"],
            f"Stratum edge alpha {c['edge_alpha']}, expected {r['edge_alpha']}.",
            delta=round(c["edge_alpha"] - r["edge_alpha"], 4), sub="edge_alpha")


def flow_span(r, c, P, add, expect):
    if r["kind"] != "flow":
        return False
    tol, ku = P["tol"], r["pt_u"]
    d0 = abs(c["u0"] - r["u0"]) * ku
    if exceeds(d0, tol, "flow_span"):
        add("flow_span", round(r["u0"], 4), round(c["u0"], 4),
            f"Flow start axis-edge is {d0:.1f}pt off ({c['u0']:.3f} vs {r['u0']:.3f}).",
            delta=round(d0, 2), sub="u0")
    d1 = abs(c["u1"] - r["u1"]) * ku
    if exceeds(d1, tol, "flow_span"):
        add("flow_span", round(r["u1"], 4), round(c["u1"], 4),
            f"Flow end axis-edge is {d1:.1f}pt off ({c['u1']:.3f} vs {r['u1']:.3f}).",
            delta=round(d1, 2), sub="u1")


def flow_trajectory(r, c, P, add, expect):
    """Compare top and bottom envelope y(x) of the curved ribbon across 17 interior stations."""
    if r["kind"] != "flow":
        return False
    tol, kv = P["tol"], r["pt_v"]
    lo = max(r["u0"], c["u0"])
    hi = min(r["u1"], c["u1"])
    if hi - lo <= 1e-6:
        add("flow_trajectory", [round(r["u0"], 3), round(r["u1"], 3)],
            [round(c["u0"], 3), round(c["u1"], 3)],
            "Flow horizontal spans do not overlap.", sub="overlap")
        return

    eps = 0.005 * (hi - lo)
    stations = np.linspace(lo + eps, hi - eps, 17)
    ru, rv = np.asarray(r["verts_u"], float), np.asarray(r["verts_v"], float)
    cu, cv = np.asarray(c["verts_u"], float), np.asarray(c["verts_v"], float)
    rb, rt = _envelope_at(ru, rv, stations)
    cb, ct = _envelope_at(cu, cv, stations)

    mb = np.isfinite(rb) & np.isfinite(cb)
    mt = np.isfinite(rt) & np.isfinite(ct)
    if not (mb.any() and mt.any()):
        add("flow_trajectory", "valid ribbon envelope", "empty envelope",
            "Could not evaluate ribbon top/bottom envelope across span.", sub="envelope")
        return

    dev_top = np.abs(ct[mt] - rt[mt]) * kv
    kt = int(np.argmax(dev_top))
    dt = float(dev_top[kt])
    if exceeds(dt, tol, "flow_trajectory"):
        add("flow_trajectory", round(float(rt[mt][kt]), 3), round(float(ct[mt][kt]), 3),
            f"Flow top boundary is up to {dt:.1f}pt off (at axis={stations[mt][kt]:.3f}).",
            delta=round(dt, 2), sub="top")

    dev_bot = np.abs(cb[mb] - rb[mb]) * kv
    kb = int(np.argmax(dev_bot))
    db = float(dev_bot[kb])
    if exceeds(db, tol, "flow_trajectory"):
        add("flow_trajectory", round(float(rb[mb][kb]), 3), round(float(cb[mb][kb]), 3),
            f"Flow bottom boundary is up to {db:.1f}pt off (at axis={stations[mb][kb]:.3f}).",
            delta=round(db, 2), sub="bottom")


def flow_style(r, c, P, add, expect):
    if r["kind"] != "flow":
        return False
    de_f = color_delta_e(r["facecolor_hex"], c["facecolor_hex"])
    if exceeds(de_f, P["tol"], "flow_style"):
        add("flow_style", r["facecolor_hex"], c["facecolor_hex"],
            f"Flow fill {c['facecolor_hex']}, expected {r['facecolor_hex']} (ΔE {de_f}).",
            delta=de_f, sub="fill")
    da_f = abs(c["face_alpha"] - r["face_alpha"])
    if exceeds(da_f, P["alpha_tol"], "flow_style"):
        add("flow_style", r["face_alpha"], c["face_alpha"],
            f"Flow fill alpha {c['face_alpha']}, expected {r['face_alpha']}.",
            delta=round(c["face_alpha"] - r["face_alpha"], 4), sub="fill_alpha")
    de_e = color_delta_e(r["edgecolor_hex"], c["edgecolor_hex"])
    if exceeds(de_e, P["tol"], "flow_style"):
        add("flow_style", r["edgecolor_hex"], c["edgecolor_hex"],
            f"Flow edge {c['edgecolor_hex']}, expected {r['edgecolor_hex']} (ΔE {de_e}).",
            delta=de_e, sub="edge")
    da_e = abs(c["edge_alpha"] - r["edge_alpha"])
    if exceeds(da_e, P["alpha_tol"], "flow_style"):
        add("flow_style", r["edge_alpha"], c["edge_alpha"],
            f"Flow edge alpha {c['edge_alpha']}, expected {r['edge_alpha']}.",
            delta=round(c["edge_alpha"] - r["edge_alpha"], 4), sub="edge_alpha")


def label_position(r, c, P, add, expect):
    if r["kind"] != "label":
        return False
    tol, ku, kv = P["tol"], r["pt_u"], r["pt_v"]
    du = abs(c["u"] - r["u"]) * ku
    dv = abs(c["v"] - r["v"]) * kv
    dist = math.hypot(du, dv)
    if exceeds(dist, tol, "label_position"):
        add("label_position", [round(r["u"], 3), round(r["v"], 2)],
            [round(c["u"], 3), round(c["v"], 2)],
            f"Label {c['text']!r} is {dist:.1f}pt off (Δaxis={du:.1f}pt, Δstack={dv:.1f}pt).",
            delta=round(dist, 2), sub="offset")
    if r.get("inside_stratum") and c.get("has_strata_in_panel") and not c.get("inside_stratum"):
        add("label_position", True, False,
            f"Label {c['text']!r} center falls outside every stratum box in panel {c['panel']}.",
            sub="inside_stratum")


def label_style(r, c, P, add, expect):
    if r["kind"] != "label":
        return False
    de = color_delta_e(r["color_hex"], c["color_hex"])
    if exceeds(de, P["tol"], "label_style"):
        add("label_style", r["color_hex"], c["color_hex"],
            f"Label colour {c['color_hex']}, expected {r['color_hex']} (ΔE {de}).",
            delta=de, sub="colour")
    da = abs(c["alpha"] - r["alpha"])
    if exceeds(da, P["alpha_tol"], "label_style"):
        add("label_style", r["alpha"], c["alpha"],
            f"Label alpha {c['alpha']}, expected {r['alpha']}.",
            delta=round(c["alpha"] - r["alpha"], 4), sub="alpha")
    ds = abs(c["fontsize_pt"] - r["fontsize_pt"])
    if exceeds(ds, 1.5, "label_style"):
        add("label_style", r["fontsize_pt"], c["fontsize_pt"],
            f"Label fontsize {c['fontsize_pt']:.1f}pt, expected {r['fontsize_pt']:.1f}pt.",
            delta=round(c["fontsize_pt"] - r["fontsize_pt"], 2), sub="size")
    if r["has_box"] != c["has_box"]:
        add("label_style", r["has_box"], c["has_box"],
            f"Label box presence mismatch (candidate has_box={c['has_box']}, expected {r['has_box']}).",
            sub="box")
    elif r["has_box"]:
        de_bf = color_delta_e(r["box_fill_hex"], c["box_fill_hex"])
        if exceeds(de_bf, P["tol"], "label_style"):
            add("label_style", r["box_fill_hex"], c["box_fill_hex"],
                f"Label box fill {c['box_fill_hex']}, expected {r['box_fill_hex']} (ΔE {de_bf}).",
                delta=de_bf, sub="box_fill")
        de_be = color_delta_e(r["box_edge_hex"], c["box_edge_hex"])
        if exceeds(de_be, P["tol"], "label_style"):
            add("label_style", r["box_edge_hex"], c["box_edge_hex"],
                f"Label box edge {c['box_edge_hex']}, expected {r['box_edge_hex']} (ΔE {de_be}).",
                delta=de_be, sub="box_edge")


CHECKS = {f.__name__: f for f in (
    stratum_geometry, stratum_style, flow_span, flow_trajectory, flow_style, label_position, label_style
)}


# ───────────────────────────── case-level checks ─────────────────────────────

def height_conservation(rc, cc, pairs, P, add, expect):
    """Verify strata on each axis stack contiguously and conserve total weight matching R."""
    ref_axes: dict[tuple[int, int], list[dict]] = {}
    cand_axes: dict[tuple[int, int], list[dict]] = {}
    for s in rc:
        if s["kind"] == "stratum":
            ref_axes.setdefault((s["panel"], s["axis_idx"]), []).append(s)
    for s in cc:
        if s["kind"] == "stratum":
            cand_axes.setdefault((s["panel"], s["axis_idx"]), []).append(s)
    if not ref_axes:
        return False
    tol = P["tol"]
    for key, r_list in sorted(ref_axes.items()):
        c_list = cand_axes.get(key)
        if not c_list:
            continue
        kv = r_list[0]["pt_v"]
        r_total = sum(s["height"] for s in r_list)
        c_total = sum(s["height"] for s in c_list)
        c_span = max(s["v1"] for s in c_list) - min(s["v0"] for s in c_list)
        gap_pt = abs(c_span - c_total) * kv
        if exceeds(gap_pt, tol, "height_conservation"):
            add("height_conservation", round(c_total, 3), round(c_span, 3),
                f"Panel {key[0]} axis {key[1] + 1}: strata have {gap_pt:.1f}pt vertical gap/overlap.",
                delta=round(gap_pt, 2), sub=f"p{key[0]}a{key[1] + 1}_contig")
        diff_pt = abs(c_total - r_total) * kv
        if exceeds(diff_pt, tol, "height_conservation"):
            add("height_conservation", round(r_total, 3), round(c_total, 3),
                f"Panel {key[0]} axis {key[1] + 1}: total stratum height is {diff_pt:.1f}pt off "
                f"({c_total:.2f} vs {r_total:.2f}).",
                delta=round(diff_pt, 2), sub=f"p{key[0]}a{key[1] + 1}_total")


def layer_order(rc, cc, pairs, P, add, expect):
    """Verify relative paint order between flows and strata matches R in each panel."""
    panels = sorted({c["panel"] for c in rc})
    checked = False
    for pi in panels:
        rf = [c["paint_index"] for c in rc if c["panel"] == pi and c["kind"] == "flow"]
        rs = [c["paint_index"] for c in rc if c["panel"] == pi and c["kind"] == "stratum"]
        cf = [c["paint_index"] for c in cc if c["panel"] == pi and c["kind"] == "flow"]
        cs = [c["paint_index"] for c in cc if c["panel"] == pi and c["kind"] == "stratum"]
        if not (rf and rs and cf and cs):
            continue
        r_strata_on_top = min(rs) > max(rf)
        r_flows_on_top = min(rf) > max(rs)
        if not (r_strata_on_top or r_flows_on_top):
            continue
        checked = True
        c_strata_on_top = min(cs) > max(cf)
        c_flows_on_top = min(cf) > max(cs)
        if r_strata_on_top and not c_strata_on_top:
            add("layer_order", "strata on top of flows", "flows overlap strata",
                f"Panel {pi}: R paints strata on top of flows, candidate does not.", sub=f"p{pi}")
        elif r_flows_on_top and not c_flows_on_top:
            add("layer_order", "flows on top of strata", "strata overlap flows",
                f"Panel {pi}: R paints flows on top of strata, candidate does not.", sub=f"p{pi}")
    return checked or False


CASE_CHECKS = {"height_conservation": height_conservation, "layer_order": layer_order}
