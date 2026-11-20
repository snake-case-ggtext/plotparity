"""ggstream adapter: stacked smooth stream ribbons and stream labels.

Both engines extract per-panel stream polygons and text/label annotations in panel data units
(observe.R, observe_py.py). Under coord_flip(), coordinates are canonicalized to
(u, v) = (timeline axis, stacked axis) so all geometric checks work identically for horizontal
and vertical streamgraphs.

Components have `kind` in {"stream", "label"}:
  - streams pair by (panel, stack_rank) where stack_rank 0 is bottom-most and K-1 is top-most
    (structural vertical slot; never matches on color, alpha, or exact y-offset)
  - labels pair by (panel, text, occurrence_index)
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from ...compare import color_delta_e, exceeds

NAME = "ggstream"
VERSION = "0.1.0"
HERE = Path(__file__).resolve().parent
OBSERVE_R = HERE / "observe.R"
PROFILE = HERE / "profile.toml"
EXPECT_KEYS: set[str] = set()

N_STATIONS = 21


# ───────────────────────────── geometry helpers ─────────────────────────────

def _envelope_at(u: np.ndarray, v: np.ndarray, stations: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Intersect closed polygon (u, v) with vertical lines u = s for each station s.
    Returns (v_bot, v_top) arrays of shape (len(stations),)."""
    if len(u) < 3:
        nan = np.full(len(stations), np.nan)
        return nan, nan
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


def _stream_envelope_at(stream: dict, stations: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Evaluate combined (v_bot, v_top) across all polygon pieces of a stream."""
    vb_all = np.full(len(stations), np.inf)
    vt_all = np.full(len(stations), -np.inf)
    for pu, pv in zip(stream["pieces_u"], stream["pieces_v"]):
        vb, vt = _envelope_at(np.asarray(pu, float), np.asarray(pv, float), stations)
        fin = np.isfinite(vb) & np.isfinite(vt)
        vb_all[fin] = np.minimum(vb_all[fin], vb[fin])
        vt_all[fin] = np.maximum(vt_all[fin], vt[fin])
    missing = ~np.isfinite(vb_all) | ~np.isfinite(vt_all)
    vb_all[missing] = np.nan
    vt_all[missing] = np.nan
    return vb_all, vt_all


def _assign_stack_ranks(streams: list[dict]) -> None:
    """Assign 0-based bottom-to-top vertical stack_rank within a panel."""
    n = len(streams)
    if n == 0:
        return
    u_lo = min(s["u0"] for s in streams)
    u_hi = max(s["u1"] for s in streams)
    eps = 0.005 * max(u_hi - u_lo, 1e-9)
    stations = np.linspace(u_lo + eps, u_hi - eps, N_STATIONS)
    mids = []
    for s in streams:
        vb, vt = _stream_envelope_at(s, stations)
        thick = vt - vb
        active = np.isfinite(thick) & (thick > 1e-7)
        if active.any():
            mids.append(0.5 * (vb + vt))
        else:
            fin = np.isfinite(vb) & np.isfinite(vt)
            mids.append(0.5 * (vb + vt) if fin.any() else np.full(N_STATIONS, 0.5 * (s["v0"] + s["v1"])))

    # Pairwise vertical dominance score (number of other streams strictly below)
    # handles streams that are zero on parts of the domain without ambiguity.
    scores = [0.0] * n
    mean_v = [float(np.nanmean(m)) for m in mids]
    for i in range(n):
        for j in range(i + 1, n):
            both = np.isfinite(mids[i]) & np.isfinite(mids[j])
            diff = mids[i][both] - mids[j][both] if both.any() else np.array([mean_v[i] - mean_v[j]])
            # Weight by magnitude so tiny floating-point ties at zero thickness don't dominate
            net = float(np.sum(diff))
            if net > 0:
                scores[i] += 1.0
            elif net < 0:
                scores[j] += 1.0
            elif mean_v[i] >= mean_v[j]:
                scores[i] += 0.5
            else:
                scores[j] += 0.5

    order = sorted(range(n), key=lambda idx: (scores[idx], mean_v[idx], streams[idx]["v0"], streams[idx]["v1"]))
    for rank, idx in enumerate(order):
        streams[idx]["stack_rank"] = rank


# ───────────────────────────── components ─────────────────────────────

def components(engine: str, obs: dict[str, Any], expect: dict[str, Any],
               canvas_pt: tuple[float, float] = (640.0, 480.0)) -> tuple[list[dict], set[int]]:
    st = obs.get("ggstream") or {}
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

        # Extract filled polygon streams
        stream_comps: list[dict] = []
        for sh in sorted(panel.get("shapes") or [], key=lambda e: e.get("paint_index", 0)):
            fc = sh.get("facecolor_hex", "none")
            ec = sh.get("edgecolor_hex", "none")
            if fc == "none" and ec == "none":
                continue
            if not sh.get("filled", fc != "none"):
                continue

            pieces_u: list[list[float]] = []
            pieces_v: list[list[float]] = []
            bbs: list[list[float]] = []
            for pc in sh.get("pieces") or []:
                vd = np.asarray(pc.get("data") or [], dtype=float)
                vn = np.asarray(pc.get("norm") or [], dtype=float)
                if vd.ndim != 2 or len(vd) < 3:
                    continue
                u = vd[:, 1] if is_flip else vd[:, 0]
                v = vd[:, 0] if is_flip else vd[:, 1]
                if (float(u.max()) - float(u.min())) < 1e-6 or (float(v.max()) - float(v.min())) < 1e-6:
                    continue
                pieces_u.append(u.tolist())
                pieces_v.append(v.tolist())
                if len(vn):
                    bbs.append([float(vn[:, 0].min()), float(vn[:, 1].min()),
                                float(vn[:, 0].max()), float(vn[:, 1].max())])
            if not pieces_u:
                continue

            all_u = np.concatenate([np.asarray(pu, float) for pu in pieces_u])
            all_v = np.concatenate([np.asarray(pv, float) for pv in pieces_v])
            bb = [
                min(b[0] for b in bbs), min(b[1] for b in bbs),
                max(b[2] for b in bbs), max(b[3] for b in bbs),
            ] if bbs else list(pbb)

            stream_comps.append({
                "panel": pi,
                "multi_panel": multi_panel,
                "is_flip": is_flip,
                "kind": "stream",
                "pt_u": float(pt_u),
                "pt_v": float(pt_v),
                "paint_index": int(sh.get("paint_index", 0)),
                "facecolor_hex": fc,
                "face_alpha": float(sh.get("face_alpha", 0.0)),
                "edgecolor_hex": ec,
                "edge_alpha": float(sh.get("edge_alpha", 0.0)),
                "linewidth_pt": float(sh.get("linewidth_pt", 0.0)),
                "u0": float(all_u.min()),
                "u1": float(all_u.max()),
                "v0": float(all_v.min()),
                "v1": float(all_v.max()),
                "bbox_norm_tl": [round(val, 6) for val in bb],
                "pieces_u": pieces_u,
                "pieces_v": pieces_v,
            })

        _assign_stack_ranks(stream_comps)
        stream_comps.sort(key=lambda s: s["stack_rank"])

        # Extract stream labels and determine which stream polygon contains each label
        label_comps: list[dict] = []
        raw_labels = panel.get("labels") or []
        by_text: dict[str, list[dict]] = {}
        margin_u = 1.5 / pt_u
        margin_v = 1.5 / pt_v
        for lb in raw_labels:
            txt = str(lb.get("text", "")).strip()
            if not txt:
                continue
            lx, ly = float(lb["x"]), float(lb["y"])
            lu, lv = (ly, lx) if is_flip else (lx, ly)

            best_rank = None
            best_dist = float("inf")
            thickness_ratio = None
            v_center_frac = None
            v_center_offset_pt = None
            for s in stream_comps:
                if not (s["u0"] - margin_u <= lu <= s["u1"] + margin_u):
                    continue
                u_eval = float(np.clip(lu, s["u0"], s["u1"]))
                vb, vt = _stream_envelope_at(s, np.array([u_eval], dtype=float))
                if not (np.isfinite(vb[0]) and np.isfinite(vt[0])):
                    continue
                if vb[0] - margin_v <= lv <= vt[0] + margin_v:
                    mid = 0.5 * (vb[0] + vt[0])
                    dist = abs(lv - mid)
                    if dist < best_dist:
                        best_dist = dist
                        best_rank = s["stack_rank"]
                        eps = 0.005 * max(s["u1"] - s["u0"], 1e-9)
                        st_u = np.linspace(s["u0"] + eps, s["u1"] - eps, N_STATIONS)
                        sb, st_top = _stream_envelope_at(s, st_u)
                        hs = st_top - sb
                        # Exclude outermost boundary-horn stations (first/last 2 of 21) so sharp
                        # edge flares don't skew interior cross-section thickness ratios.
                        hs_int = hs[2:-2] if len(hs) > 4 else hs
                        max_h = float(np.nanmax(hs_int)) if np.isfinite(hs_int).any() else (
                            float(np.nanmax(hs)) if np.isfinite(hs).any() else 0.0
                        )
                        cur_h = max(float(vt[0] - vb[0]), 0.0)
                        thickness_ratio = (cur_h / max_h) if max_h > 1e-9 else 1.0
                        v_center_frac = dist / max(cur_h, 1e-9)
                        v_center_offset_pt = dist * float(pt_v)

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
                "inside_stream": best_rank is not None,
                "containing_stream_rank": best_rank,
                "thickness_ratio": thickness_ratio,
                "v_center_frac": v_center_frac,
                "v_center_offset_pt": v_center_offset_pt,
                "has_streams_in_panel": bool(stream_comps),
            }
            by_text.setdefault(txt, []).append(rec)

        for txt in sorted(by_text):
            group = sorted(by_text[txt], key=lambda l: (round(l["u"], 3), round(l["v"], 3)))
            for occ, rec in enumerate(group):
                rec["occ_idx"] = occ
                label_comps.append(rec)

        comps.extend(stream_comps)
        comps.extend(label_comps)

    extracted = set(st.get("extracted_classes") or [])
    owners = obs.get("owners") or []
    assigned = {e["paint_index"] for e in obs.get("events") or []
                if e.get("owner") is not None and owners[e["owner"]]["class"] in extracted} if extracted else set()
    return comps, assigned


def component_id(i: int, comp: dict) -> str:
    prefix = f"p{comp['panel']}:" if comp.get("multi_panel") else ""
    if comp["kind"] == "stream":
        return f"stream[{prefix}#{comp['stack_rank'] + 1}]"
    occ = f"#{comp['occ_idx'] + 1}" if comp.get("occ_idx", 0) > 0 else ""
    return f"label[{prefix}{comp['text'][:20]!r}{occ}]"


def describe_component(comp: dict) -> dict:
    if comp["kind"] == "stream":
        return {
            "kind": "stream",
            "panel": comp["panel"],
            "stack_rank": comp["stack_rank"] + 1,
            "u": [round(comp["u0"], 3), round(comp["u1"], 3)],
            "v": [round(comp["v0"], 3), round(comp["v1"], 3)],
            "fill": comp["facecolor_hex"],
        }
    return {
        "kind": "label",
        "panel": comp["panel"],
        "text": comp["text"],
        "u": round(comp["u"], 3),
        "v": round(comp["v"], 3),
    }


def _match_key(comp: dict) -> tuple:
    if comp["kind"] == "stream":
        return ("stream", comp["panel"], comp["stack_rank"])
    return ("label", comp["panel"], comp["text"], comp["occ_idx"])


def match(ref: list[dict], cand: list[dict]) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Pair streams by (panel, vertical stack_rank) and labels by (panel, exact text, occurrence)."""
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

def stream_envelope(r, c, P, add, expect):
    """Compare stream x-span, top/bottom envelopes, and thickness across 21 stations in pt."""
    if r["kind"] != "stream":
        return False
    tol, ku, kv = P["tol"], r["pt_u"], r["pt_v"]

    du0 = abs(c["u0"] - r["u0"]) * ku
    du1 = abs(c["u1"] - r["u1"]) * ku
    d_span = max(du0, du1)
    if exceeds(d_span, tol, "stream_envelope"):
        add("stream_envelope", [round(r["u0"], 4), round(r["u1"], 4)],
            [round(c["u0"], 4), round(c["u1"], 4)],
            f"Stream x-span [{c['u0']:.3g}, {c['u1']:.3g}] differs from R [{r['u0']:.3g}, {r['u1']:.3g}] "
            f"by {d_span:.1f}pt.",
            delta=round(d_span, 2), sub="u_span")

    lo = max(r["u0"], c["u0"])
    hi = min(r["u1"], c["u1"])
    if hi - lo <= 1e-6:
        add("stream_envelope", [round(r["u0"], 3), round(r["u1"], 3)],
            [round(c["u0"], 3), round(c["u1"], 3)],
            "Stream horizontal spans do not overlap.", sub="overlap")
        return

    eps = 0.005 * (hi - lo)
    stations = np.linspace(lo + eps, hi - eps, N_STATIONS)
    rb, rt = _stream_envelope_at(r, stations)
    cb, ct = _stream_envelope_at(c, stations)

    mb = np.isfinite(rb) & np.isfinite(cb)
    mt = np.isfinite(rt) & np.isfinite(ct)
    if not (mb.any() and mt.any()):
        add("stream_envelope", "valid ribbon envelope", "empty envelope",
            "Could not evaluate stream top/bottom envelope across span.", sub="envelope")
        return

    dev_top = np.abs(ct[mt] - rt[mt]) * kv
    kt = int(np.argmax(dev_top))
    dt = float(dev_top[kt])
    if exceeds(dt, tol, "stream_envelope"):
        add("stream_envelope", round(float(rt[mt][kt]), 3), round(float(ct[mt][kt]), 3),
            f"Stream top boundary is up to {dt:.1f}pt off (at u={stations[mt][kt]:.3g}).",
            delta=round(dt, 2), sub="top")

    dev_bot = np.abs(cb[mb] - rb[mb]) * kv
    kb = int(np.argmax(dev_bot))
    db = float(dev_bot[kb])
    if exceeds(db, tol, "stream_envelope"):
        add("stream_envelope", round(float(rb[mb][kb]), 3), round(float(cb[mb][kb]), 3),
            f"Stream bottom boundary is up to {db:.1f}pt off (at u={stations[mb][kb]:.3g}).",
            delta=round(db, 2), sub="bottom")

    m_both = mb & mt
    if m_both.any():
        r_thick = rt[m_both] - rb[m_both]
        c_thick = ct[m_both] - cb[m_both]
        dev_th = np.abs(c_thick - r_thick) * kv
        kth = int(np.argmax(dev_th))
        dth = float(dev_th[kth])
        if exceeds(dth, tol, "stream_envelope"):
            add("stream_envelope", round(float(r_thick[kth]), 3), round(float(c_thick[kth]), 3),
                f"Stream thickness is up to {dth:.1f}pt off (at u={stations[m_both][kth]:.3g}).",
                delta=round(dth, 2), sub="thickness")


def stream_style(r, c, P, add, expect):
    """Compare stream fill colour, fill alpha, outline colour, outline alpha, and outline width."""
    if r["kind"] != "stream":
        return False
    de_f = color_delta_e(r["facecolor_hex"], c["facecolor_hex"])
    if exceeds(de_f, P["tol"], "stream_style"):
        add("stream_style", r["facecolor_hex"], c["facecolor_hex"],
            f"Stream fill {c['facecolor_hex']}, expected {r['facecolor_hex']} (ΔE {de_f}).",
            delta=de_f, sub="fill")
    da_f = abs(c["face_alpha"] - r["face_alpha"])
    if exceeds(da_f, P["alpha_tol"], "stream_style"):
        add("stream_style", r["face_alpha"], c["face_alpha"],
            f"Stream fill alpha {c['face_alpha']}, expected {r['face_alpha']}.",
            delta=round(c["face_alpha"] - r["face_alpha"], 4), sub="fill_alpha")
    de_e = color_delta_e(r["edgecolor_hex"], c["edgecolor_hex"])
    if exceeds(de_e, P["tol"], "stream_style"):
        add("stream_style", r["edgecolor_hex"], c["edgecolor_hex"],
            f"Stream outline {c['edgecolor_hex']}, expected {r['edgecolor_hex']} (ΔE {de_e}).",
            delta=de_e, sub="edge")
    da_e = abs(c["edge_alpha"] - r["edge_alpha"])
    if exceeds(da_e, P["alpha_tol"], "stream_style"):
        add("stream_style", r["edge_alpha"], c["edge_alpha"],
            f"Stream outline alpha {c['edge_alpha']}, expected {r['edge_alpha']}.",
            delta=round(c["edge_alpha"] - r["edge_alpha"], 4), sub="edge_alpha")
    if r["edgecolor_hex"] != "none" and c["edgecolor_hex"] != "none" and r["linewidth_pt"] > 0:
        rel = abs(c["linewidth_pt"] - r["linewidth_pt"]) / max(r["linewidth_pt"], 1e-9)
        if exceeds(rel, P["rel_tol"], "stream_style"):
            add("stream_style", r["linewidth_pt"], c["linewidth_pt"],
                f"Stream outline width {c['linewidth_pt']:.2f}pt, expected {r['linewidth_pt']:.2f}pt.",
                delta=round(c["linewidth_pt"] - r["linewidth_pt"], 3), sub="linewidth")


def stream_label_position(r, c, P, add, expect):
    """Verify each stream label sits vertically centered inside its corresponding stream polygon,
    either near R's anchor or at an equally wide cross-section on multimodal/flat ribbons."""
    if r["kind"] != "label":
        return False
    tol, ku, kv = P["tol"], r["pt_u"], r["pt_v"]

    same_stream = False
    if r.get("inside_stream") and c.get("has_streams_in_panel"):
        if not c.get("inside_stream"):
            add("stream_label_position", True, False,
                f"Label {c['text']!r} center ({c['u']:.3g}, {c['v']:.3g}) falls outside every stream polygon.",
                sub="inside_stream")
        elif c.get("containing_stream_rank") != r.get("containing_stream_rank"):
            add("stream_label_position",
                f"stream #{r['containing_stream_rank'] + 1}",
                f"stream #{c['containing_stream_rank'] + 1}",
                f"Label {c['text']!r} sits inside stream #{c['containing_stream_rank'] + 1}, "
                f"expected stream #{r['containing_stream_rank'] + 1}.",
                sub="wrong_stream")
        else:
            same_stream = True
            vcf = c.get("v_center_frac")
            vco = c.get("v_center_offset_pt")
            if vcf is not None and vco is not None and vcf > 0.35 and vco > 3.0:
                add("stream_label_position", "centered", round(float(vcf), 3),
                    f"Label {c['text']!r} is vertically off-center inside its stream ({vco:.1f}pt, {vcf:.0%} of thickness).",
                    delta=round(float(vco), 2), sub="vertical_center")

    du = abs(c["u"] - r["u"]) * ku
    dv = abs(c["v"] - r["v"]) * kv
    dist = math.hypot(du, dv)
    if exceeds(dist, tol, "stream_label_position"):
        # On flat or bimodal ribbons, two x-positions can have nearly equal inscribed circles;
        # accept an alternate placement only if it sits inside the same stream at a genuinely wide section.
        r_tr = r.get("thickness_ratio")
        c_tr = c.get("thickness_ratio")
        wide_alternate = (
            same_stream
            and r_tr is not None
            and c_tr is not None
            and c_tr >= 0.72
            and c_tr >= 0.78 * r_tr
        )
        if not wide_alternate:
            add("stream_label_position", [round(r["u"], 3), round(r["v"], 3)],
                [round(c["u"], 3), round(c["v"], 3)],
                f"Label {c['text']!r} is {dist:.1f}pt off R's anchor (Δu={du:.1f}pt, Δv={dv:.1f}pt).",
                delta=round(dist, 2), sub="offset")


def stream_label_style(r, c, P, add, expect):
    """Compare stream label colour, alpha, fontsize, and optional label box."""
    if r["kind"] != "label":
        return False
    de = color_delta_e(r["color_hex"], c["color_hex"])
    if exceeds(de, P["tol"], "stream_label_style"):
        add("stream_label_style", r["color_hex"], c["color_hex"],
            f"Label colour {c['color_hex']}, expected {r['color_hex']} (ΔE {de}).",
            delta=de, sub="colour")
    da = abs(c["alpha"] - r["alpha"])
    if exceeds(da, P["alpha_tol"], "stream_label_style"):
        add("stream_label_style", r["alpha"], c["alpha"],
            f"Label alpha {c['alpha']}, expected {r['alpha']}.",
            delta=round(c["alpha"] - r["alpha"], 4), sub="alpha")
    ds = abs(c["fontsize_pt"] - r["fontsize_pt"])
    if exceeds(ds, 1.5, "stream_label_style"):
        add("stream_label_style", r["fontsize_pt"], c["fontsize_pt"],
            f"Label fontsize {c['fontsize_pt']:.1f}pt, expected {r['fontsize_pt']:.1f}pt.",
            delta=round(c["fontsize_pt"] - r["fontsize_pt"], 2), sub="size")
    if r["has_box"] != c["has_box"]:
        add("stream_label_style", r["has_box"], c["has_box"],
            f"Label box presence mismatch (candidate has_box={c['has_box']}, expected {r['has_box']}).",
            sub="box")
    elif r["has_box"]:
        de_bf = color_delta_e(r["box_fill_hex"], c["box_fill_hex"])
        if exceeds(de_bf, P["tol"], "stream_label_style"):
            add("stream_label_style", r["box_fill_hex"], c["box_fill_hex"],
                f"Label box fill {c['box_fill_hex']}, expected {r['box_fill_hex']} (ΔE {de_bf}).",
                delta=de_bf, sub="box_fill")
        de_be = color_delta_e(r["box_edge_hex"], c["box_edge_hex"])
        if exceeds(de_be, P["tol"], "stream_label_style"):
            add("stream_label_style", r["box_edge_hex"], c["box_edge_hex"],
                f"Label box edge {c['box_edge_hex']}, expected {r['box_edge_hex']} (ΔE {de_be}).",
                delta=de_be, sub="box_edge")


CHECKS = {f.__name__: f for f in (
    stream_envelope, stream_style, stream_label_position, stream_label_style,
)}


# ───────────────────────────── case-level checks ─────────────────────────────

def stream_stacking(rc, cc, pairs, P, add, expect):
    """Verify per-panel stream stacking contiguity (no gaps/overlaps between adjacent streams),
    total stacked thickness conservation vs R, and vertical stream ordering."""
    ref_by_panel: dict[int, list[dict]] = {}
    cand_by_panel: dict[int, list[dict]] = {}
    for s in rc:
        if s["kind"] == "stream":
            ref_by_panel.setdefault(s["panel"], []).append(s)
    for s in cc:
        if s["kind"] == "stream":
            cand_by_panel.setdefault(s["panel"], []).append(s)
    if not ref_by_panel:
        return False

    tol = P["tol"]
    for pi in sorted(ref_by_panel):
        r_list = sorted(ref_by_panel[pi], key=lambda s: s["stack_rank"])
        c_list = sorted(cand_by_panel.get(pi) or [], key=lambda s: s["stack_rank"])
        if not c_list:
            continue
        kv = r_list[0]["pt_v"]

        r_lo = min(s["u0"] for s in r_list)
        r_hi = max(s["u1"] for s in r_list)
        c_lo = min(s["u0"] for s in c_list)
        c_hi = max(s["u1"] for s in c_list)
        lo, hi = max(r_lo, c_lo), min(r_hi, c_hi)
        if hi - lo <= 1e-6:
            continue

        eps = 0.005 * (hi - lo)
        stations = np.linspace(lo + eps, hi - eps, N_STATIONS)

        r_envs = [_stream_envelope_at(s, stations) for s in r_list]
        c_envs = [_stream_envelope_at(s, stations) for s in c_list]

        # 1. Contiguity: adjacent candidate streams must touch with zero gap and zero overlap
        if len(c_envs) >= 2:
            max_gap = 0.0
            worst_u = float(stations[0])
            worst_kind = "gap"
            for st_idx in range(N_STATIONS):
                intervals = []
                for cb, ct in c_envs:
                    if np.isfinite(cb[st_idx]) and np.isfinite(ct[st_idx]):
                        intervals.append((float(cb[st_idx]), float(ct[st_idx])))
                intervals.sort(key=lambda iv: (iv[0], iv[1]))
                for k in range(len(intervals) - 1):
                    diff_pt = (intervals[k + 1][0] - intervals[k][1]) * kv
                    if abs(diff_pt) > max_gap:
                        max_gap = abs(diff_pt)
                        worst_u = float(stations[st_idx])
                        worst_kind = "gap" if diff_pt > 0 else "overlap"
            if exceeds(max_gap, tol, "stream_stacking"):
                add("stream_stacking", 0.0, round(max_gap, 2),
                    f"Panel {pi}: adjacent streams have {max_gap:.1f}pt {worst_kind} (at u={worst_u:.3g}).",
                    delta=round(max_gap, 2), sub=f"p{pi}_contiguity")

        # 2. Total stacked thickness conservation vs R across stations
        r_tot = np.zeros(N_STATIONS)
        c_tot = np.zeros(N_STATIONS)
        r_valid = np.zeros(N_STATIONS, dtype=bool)
        c_valid = np.zeros(N_STATIONS, dtype=bool)
        for rb, rt in r_envs:
            m = np.isfinite(rb) & np.isfinite(rt)
            r_tot[m] += np.maximum(rt[m] - rb[m], 0.0)
            r_valid |= m
        for cb, ct in c_envs:
            m = np.isfinite(cb) & np.isfinite(ct)
            c_tot[m] += np.maximum(ct[m] - cb[m], 0.0)
            c_valid |= m
        both_v = r_valid & c_valid
        if both_v.any():
            # Enforce tight mean conservation across all 21 stations (<= 8pt) while allowing
            # modest worst-case accumulation across K stacked streams at sharp boundary flares (<= 15pt).
            mean_tol = max(tol * 2.5, 8.0)
            max_tol = max(tol * 5.0, 15.0)
            dev_tot = np.abs(c_tot[both_v] - r_tot[both_v]) * kv
            mean_dt = float(np.mean(dev_tot))
            if exceeds(mean_dt, mean_tol, "stream_stacking"):
                add("stream_stacking", round(float(np.mean(r_tot[both_v])), 3),
                    round(float(np.mean(c_tot[both_v])), 3),
                    f"Panel {pi}: mean total stacked thickness deviates by {mean_dt:.1f}pt across span.",
                    delta=round(mean_dt, 2), sub=f"p{pi}_total_mean")
            kt = int(np.argmax(dev_tot))
            dt = float(dev_tot[kt])
            if exceeds(dt, max_tol, "stream_stacking"):
                add("stream_stacking", round(float(r_tot[both_v][kt]), 3), round(float(c_tot[both_v][kt]), 3),
                    f"Panel {pi}: total stacked thickness is up to {dt:.1f}pt off "
                    f"(at u={stations[both_v][kt]:.3g}).",
                    delta=round(dt, 2), sub=f"p{pi}_total")

        # 3. Vertical stacking order check (sorting = none | onset | inside_out):
        # Compare bottom-to-top stream thickness profiles (and colors when distinct) between R and candidate.
        if len(r_list) == len(c_list) and len(r_list) >= 2:
            n = len(r_list)
            r_h = [np.nan_to_num(rt - rb, nan=0.0) * kv for rb, rt in r_envs]
            c_h = [np.nan_to_num(ct - cb, nan=0.0) * kv for cb, ct in c_envs]
            # Cost matrix: mean thickness L1 error in pt + small tie-break on color ΔE
            cost = np.zeros((n, n), dtype=float)
            for i in range(n):
                for j in range(n):
                    h_err = float(np.mean(np.abs(r_h[i] - c_h[j])))
                    c_err = 0.05 * float(color_delta_e(r_list[i]["facecolor_hex"], c_list[j]["facecolor_hex"]))
                    cost[i, j] = h_err + c_err
            # Greedy/exact permutation check (n is small, typically <= 10; use brute force for n <= 8, greedy else)
            ident_cost = float(sum(cost[i, i] for i in range(n)))
            best_perm = list(range(n))
            best_cost = ident_cost
            if n <= 8:
                from itertools import permutations
                for perm in permutations(range(n)):
                    sc = float(sum(cost[i, perm[i]] for i in range(n)))
                    if sc + 1e-6 < best_cost:
                        best_cost = sc
                        best_perm = list(perm)
            else:
                # Row-wise minimum check for large n
                rem = set(range(n))
                perm_g = []
                for i in range(n):
                    j_best = min(rem, key=lambda j: cost[i, j])
                    perm_g.append(j_best)
                    rem.remove(j_best)
                sc = float(sum(cost[i, perm_g[i]] for i in range(n)))
                if sc + 1e-6 < best_cost:
                    best_cost = sc
                    best_perm = perm_g

            # Flag if a non-identity permutation improves matching by > 2.5pt average per swapped stream
            n_swapped = sum(1 for i, j in enumerate(best_perm) if i != j)
            if n_swapped > 0 and (ident_cost - best_cost) / n_swapped > 2.5:
                add("stream_stacking",
                    [i + 1 for i in range(n)],
                    [j + 1 for j in best_perm],
                    f"Panel {pi}: vertical stream stacking order differs from R "
                    f"(candidate slots {[j + 1 for j in best_perm]} match R bottom-to-top slots "
                    f"{[i + 1 for i in range(n)]}).",
                    delta=round(ident_cost - best_cost, 2),
                    sub=f"p{pi}_order")


CASE_CHECKS = {"stream_stacking": stream_stacking}
