"""ggtext adapter: rich-text boxes (geom_richtext / geom_textbox).

Python side: geometry-first segmentation (see _segment_python); works for ports that group each label
(ggtext_ShanEllis) and ports that group the whole layer (plotnine-ggtext) without relying on group names.
R side: forced gridtext grobs from observe.R.
Both are normalized to the same component dict; matching is Hungarian on (centroid distance, word
Jaccard), which never uses the properties under test (colour, weight, angle, corners), with a max cost.

The geometry/normalization functions at the bottom are copied verbatim from the research code
(experiment/compare.py, experiment/probes.py) so findings stay identical (golden test).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

from ...engines.python.probe import point_in_bbox, rgba_to_hex

NAME = "ggtext"
VERSION = "0.1.0"
HERE = Path(__file__).resolve().parent
OBSERVE_R = HERE / "observe.R"
PROFILE = HERE / "profile.toml"
EXPECT_KEYS = {"frame"}             # adapter-specific `expect` keys

# Normalized coords * CANVAS_PT = points (640x480 px @ 72 dpi). Used by the padding insets.
CANVAS_PT = (640.0, 480.0)
# Pairs costing more than this are reported as missing + unexpected instead of being force-matched.
# Cost = 2*centroid distance + 1.5*(1 - word Jaccard); 2.5 ≈ "different words AND > half a canvas apart".
MAX_MATCH_COST = 2.5
# Python routes this adapter decodes. Other routes sharing a partition with rich text are unassigned (fail closed).
DECODED = {"path", "text", "image"}


# ───────────────────────────── components ─────────────────────────────

def _partition_key(ev: dict) -> tuple:
    """Group instance directly below the axes group: one per label in ports that wrap each label in its own
    group (e.g. ggtext_ShanEllis: open_group("text") per RichText), one for all labels in ports that wrap the whole
    layer (plotnine-ggtext: open_group("richtext")). Group NAMES are never used (matplotlib's own Text/Patch also
    open "text"/"patch" groups)."""
    names, ids = ev.get("groups", []), ev.get("group_ids", [])
    if "axes" in names:
        k = len(names) - 1 - names[::-1].index("axes")
        return tuple(ids[k + 1:k + 2])          # () = drawn directly under the axes
    return ("outside-axes",)


def _segment_python(obs: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Geometry-first segmentation, no dependence on the port's group names:
    1. partition draw events by group instance below the axes (see _partition_key); a partition is rich text if
       it has text anchored inside the panel;
    2. keep its text and images, and closed paths strictly inside the panel (excludes the panel background);
    3. within a partition, each closed path starts a box and following text/images belong to it.
    Undecoded routes (collections, markers, meshes) sharing a partition with rich text are unassigned → fail closed."""
    events = obs["events"]
    fig_w, fig_h = obs["figure_px"]
    dpi = obs["dpi"]
    panel = obs["panels_norm_tl"][0] if obs.get("panels_norm_tl") else None
    if panel is None:
        return [], []

    def inside(ev) -> bool:
        if ev["kind"] == "text":
            return point_in_bbox(ev["x_norm"], ev["y_norm"], panel, pad=0.0)
        if ev["kind"] == "path":
            return ev["is_closed"] and ev["bbox_norm_tl"][0] > panel[0] + 0.001 and ev["bbox_norm_tl"][2] < panel[2] - 0.001
        if ev["kind"] == "image":
            bb = ev["bbox_norm_tl"]
            return point_in_bbox(0.5 * (bb[0] + bb[2]), 0.5 * (bb[1] + bb[3]), panel, pad=0.0)
        return False

    parts: dict[tuple, list[dict]] = {}
    for ev in events:
        parts.setdefault(_partition_key(ev), []).append(ev)
    boxes, unassigned = [], []
    for key, evs in parts.items():
        if key == ("outside-axes",):
            continue
        if not any(e["kind"] == "text" and inside(e) for e in evs):
            continue                                   # no rich text here (gridlines, points, panel background…)
        # A rich-text partition keeps ALL its text/images (text pushed outside the panel by a bug is still the
        # label's text); paths must be closed and strictly inside the panel (never the panel background).
        decoded = [e for e in evs if e["kind"] in ("text", "image") or (e["kind"] == "path" and inside(e))]
        unassigned += [e for e in evs if e["kind"] not in DECODED]
        cur = None
        for ev in decoded:
            if ev["kind"] == "path":
                if cur is not None:
                    boxes.append(cur)
                cur = {"box_index": len(boxes) + 1, "angle_deg": 0.0,
                       "rect": {k: v for k, v in ev.items() if k != "vertices_norm_tl"},
                       "rect_vertices_norm_tl": ev["vertices_norm_tl"], "text_runs": [], "images": []}
            elif ev["kind"] == "text":
                if cur is None:
                    cur = {"box_index": len(boxes) + 1, "angle_deg": ev["angle_deg"], "rect": None,
                           "rect_vertices_norm_tl": None, "text_runs": [], "images": []}
                cur["text_runs"].append(ev)
                cur["angle_deg"] = ev["angle_deg"]
            elif ev["kind"] == "image":
                if cur is None:
                    unassigned.append(ev)
                else:
                    cur["images"].append(ev)
        if cur is not None:
            boxes.append(cur)
    for i, b in enumerate(boxes):
        b["box_index"] = i + 1
        _enrich_box_geometry(b, fig_w, fig_h, dpi)
    return boxes, unassigned


def components(engine: str, obs: dict[str, Any], expect: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Return (components, unassigned candidate events)."""
    if engine == "python":
        boxes, unassigned = _segment_python(obs)
    elif engine == "r":
        boxes, unassigned = (obs.get("ggtext_grobs") or {}).get("boxes") or [], []
    else:
        raise ValueError(f"ggtext adapter has no extractor for engine {engine!r}")
    comps = []
    for b in boxes:
        c = _normalize_box(b, engine)
        c["images"] = b.get("images", [])
        comps.append(c)
    return comps, unassigned


def component_id(i: int, comp: dict) -> str:
    return f"box[{i + 1}:{comp['full_text'][:24]!r}]"


def match(ref: list[dict], cand: list[dict]) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    if not ref or not cand:
        return [], list(range(len(ref))), list(range(len(cand)))
    cost = np.zeros((len(ref), len(cand)))
    for i, rb in enumerate(ref):
        rw = set(rb["words"])
        for j, cb in enumerate(cand):
            cw = set(cb["words"])
            jacc = len(rw & cw) / max(1, len(rw | cw))
            dist = math.hypot(rb["centroid_norm_tl"][0] - cb["centroid_norm_tl"][0],
                              rb["centroid_norm_tl"][1] - cb["centroid_norm_tl"][1])
            cost[i, j] = dist * 2.0 + (1.0 - jacc) * 1.5
    rows, cols = linear_sum_assignment(cost)
    pairs = [(int(i), int(j)) for i, j in zip(rows, cols) if cost[i, j] <= MAX_MATCH_COST]
    mi, mj = {i for i, _ in pairs}, {j for _, j in pairs}
    return pairs, [i for i in range(len(ref)) if i not in mi], [j for j in range(len(cand)) if j not in mj]


# ───────────────────────────── ggtext checks ─────────────────────────────
# Signature: fn(ref, cand, P, add, expect). P = merged profile entry. add(check, expected, observed, message, delta=None)

RAW_MARKUP = ("**", "<span", "<br", "<sup", "<sub", "<b>", "<i>")


def raw_markup_leak(r, c, P, add, expect):
    for tok in c["words"]:
        if any(m in tok for m in RAW_MARKUP):
            add("raw_markup_leak", "parsed rich text", tok, f"Raw markup token {tok!r} leaked into rendered text.")


def sup_sub_offset(r, c, P, add, expect):
    tol = P["tol"]
    ref_scripts = [t for ln in r["lines"] for t in ln["tokens"] if t["is_small_script"] and abs(t["voff_pt"]) >= tol]
    ref_all = [t for ln in r["lines"] for t in ln["tokens"] if t["is_small_script"]]
    cand_scripts = [t for ln in c["lines"] for t in ln["tokens"] if t["is_small_script"]]
    for rs in ref_scripts:
        occ = [t for t in ref_all if t["text"] == rs["text"]].index(rs)   # pair same-text scripts in order
        same = [cs for cs in cand_scripts if cs["text"] == rs["text"]][occ:occ + 1]
        if not same:
            add("sup_sub_offset", rs["voff_pt"], None,
                f"Expected shrunk script token {rs['text']!r} (voff={rs['voff_pt']}pt) not found as small script.",
                sub=f"script[{rs['text']!r}]")
            continue
        cs = same[0]
        if (rs["voff_pt"] > 0 and cs["voff_pt"] < 1.0) or (rs["voff_pt"] < 0 and cs["voff_pt"] > -1.0):
            kind = "Superscript" if rs["voff_pt"] > 0 else "Subscript"
            add("sup_sub_offset", rs["voff_pt"], cs["voff_pt"],
                f"{kind} {rs['text']!r} vertical baseline offset lost or wrong sign: expected {rs['voff_pt']:+.2f}pt, "
                f"observed {cs['voff_pt']:+.2f}pt.", delta=round(cs["voff_pt"] - rs["voff_pt"], 2),
                sub=f"script[{rs['text']!r}]")


def line_count(r, c, P, add, expect):
    if len(r["lines"]) != len(c["lines"]):
        add("line_count", len(r["lines"]), len(c["lines"]),
            f"Line wrap count mismatch: expected {len(r['lines'])} lines, observed {len(c['lines'])}.",
            delta=len(c["lines"]) - len(r["lines"]))


def text_overflow(r, c, P, add, expect):
    lm = c.get("layout_metrics")
    if expect.get("frame", True) and lm is not None and lm["text_overflow_x_pt"] > P["tol"]:
        add("text_overflow", f"<= {P['tol']}pt", lm["text_overflow_x_pt"],
            f"Text overflows box horizontal bounds by {lm['text_overflow_x_pt']}pt (wrapping or width constraint broken).",
            delta=lm["text_overflow_x_pt"])


def rounded_corners(r, c, P, add, expect):
    rr, cr = r.get("rect"), c.get("rect")
    if expect.get("frame", True) and rr and cr and rr["is_rounded"] != cr["is_rounded"]:
        add("rounded_corners", rr["is_rounded"], cr["is_rounded"],
            f"Box corner rounding mismatch: expected rounded={rr['is_rounded']}, observed rounded={cr['is_rounded']}.")


def padding(r, c, P, add, expect):
    rr, cr = r.get("rect"), c.get("rect")
    if not (expect.get("frame", True) and rr and cr and rr.get("has_visible_frame") and r.get("insets_pt") and c.get("insets_pt")):
        return
    for key in ("left", "bottom_to_last_baseline", "top_to_first_baseline"):
        ev, ov = r["insets_pt"][key], c["insets_pt"][key]
        if abs(ov - ev) > P["tol"]:
            add("padding", ev, ov, f"Text-to-frame inset ({key.replace('_', ' ')}) is {ov:.1f}pt, expected {ev:.1f}pt.",
                delta=round(ov - ev, 2), sub=key)


CHECKS = {f.__name__: f for f in (raw_markup_leak, sup_sub_offset, line_count, text_overflow, rounded_corners, padding)}


# ───────────────── copied verbatim from experiment/compare.py + experiment/probes.py ─────────────────

def _split_words(run: dict[str, Any], advance_pt: float | None) -> list[dict[str, Any]]:
    """Split a multi-word run into one run per word (same style, same baseline).

    Ports differ in how they batch text: R/gridtext draws word by word, other ports draw a whole styled
    run ("Some text") in one draw_text call. Comparing at word granularity makes both comparable.
    A word's position is estimated from its character offset's share of the run's advance width
    (exact for the first word; the checks that use positions only need the first word and baselines).
    """
    words = run["text"].split()
    if len(words) <= 1:
        return [run]
    out, pos, n = [], 0, len(run["text"])
    W, H = CANVAS_PT
    rad = math.radians(run["angle_deg"])
    for w in words:
        start = run["text"].index(w, pos)
        pos = start + len(w)
        d = (float(advance_pt) * start / n) if advance_pt else 0.0  # pt along the baseline
        wr = dict(run, text=w)
        wr["x_norm"] = run["x_norm"] + d * math.cos(rad) / W
        wr["y_norm"] = run["y_norm"] - d * math.sin(rad) / H
        if run.get("local_x_pt") is not None:
            wr["local_x_pt"] = float(run["local_x_pt"]) + d
        out.append(wr)
    return out


def _normalize_box(box: dict[str, Any], engine: str) -> dict[str, Any]:
    """Normalize a box from Probe 2D (R grob), Probe 2B (SVG), or Probe 2C (Python intercept)."""
    runs = []
    for r in box.get("text_runs", []):
        txt = str(r.get("text", "")).strip()
        if not txt:
            continue
        col_raw = r.get("color_hex") or r.get("color") or "#000000"
        col_hex, _ = rgba_to_hex(col_raw)
        if col_hex == "none":
            col_hex = "#000000"
        base_run = {
            "text": txt,
            "x_norm": float(r["x_norm"]),
            "y_norm": float(r["y_norm"]),
            "local_x_pt": r.get("local_x_pt", r.get("x_local_pt")),
            "local_y_pt": r.get("local_y_pt", r.get("y_local_pt")),
            "color_hex": col_hex,
            "alpha": float(r.get("alpha", 1.0)),
            "fontsize_pt": float(r.get("fontsize_pt") or 11.0),
            "fontweight": str(r.get("fontweight", "normal")).lower(),
            "fontstyle": str(r.get("fontstyle", "normal")).lower(),
            "angle_deg": float(r.get("angle_deg", box.get("angle_deg", 0.0))),
            "partially_clipped": bool(r.get("partially_clipped", False)),
            "resolved_font_file": r.get("resolved_font_file"),
        }
        runs.extend(_split_words(base_run, r.get("advance_width_pt")))

    rect = box.get("rect") or None
    norm_rect = None
    if rect and isinstance(rect, dict) and rect.get("bbox_norm_tl"):
        fc = rect.get("facecolor_hex") or rect.get("fill") or "none"
        ec = rect.get("edgecolor_hex") or rect.get("col") or "none"
        fc = fc.lower() if isinstance(fc, str) else "none"
        ec = ec.lower() if isinstance(ec, str) else "none"
        has_visible_frame = (fc != "none") or (ec != "none")
        norm_rect = {
            "bbox_norm_tl": [float(v) for v in rect["bbox_norm_tl"]],
            "facecolor_hex": fc,
            "edgecolor_hex": ec,
            "is_rounded": bool(rect.get("is_rounded", False)),
            "has_visible_frame": has_visible_frame,
            "unrotated_width_pt": rect.get("unrotated_width_pt") or rect.get("width_pt"),
            "unrotated_height_pt": rect.get("unrotated_height_pt") or rect.get("height_pt"),
            "linewidth_pt": rect.get("linewidth_pt") or rect.get("lwd"),
        }

    # Centroid from a VISIBLE rect if present, else mean of text runs.
    # (v2 fix: Python emits an invisible fill=none/stroke=none patch for frameless boxes while
    # R emits none at all; using the invisible patch made the two engines measure different things.)
    if norm_rect and norm_rect["bbox_norm_tl"] and norm_rect["has_visible_frame"]:
        bb = norm_rect["bbox_norm_tl"]
        cx, cy = 0.5 * (bb[0] + bb[2]), 0.5 * (bb[1] + bb[3])
    elif runs:
        cx = sum(r["x_norm"] for r in runs) / len(runs)
        cy = sum(r["y_norm"] for r in runs) / len(runs)
        bb = [
            min(r["x_norm"] for r in runs), min(r["y_norm"] for r in runs),
            max(r["x_norm"] for r in runs), max(r["y_norm"] for r in runs),
        ]
    else:
        cx, cy, bb = 0.0, 0.0, [0.0, 0.0, 0.0, 0.0]

    # Cluster runs into lines in local_y_pt (if available; else project by -angle)
    angle_deg = float(box.get("angle_deg", runs[0]["angle_deg"] if runs else 0.0))
    lines = _cluster_lines(runs, angle_deg)

    pads = _text_insets(norm_rect, runs, angle_deg)

    return {
        "box_index": box.get("box_index", 0),
        "engine": engine,
        "insets_pt": pads,
        "angle_deg": angle_deg,
        "centroid_norm_tl": [round(cx, 5), round(cy, 5)],
        "bbox_norm_tl": [round(v, 5) for v in bb],
        "rect": norm_rect,
        "layout_metrics": box.get("layout_metrics"),
        "text_runs": runs,
        "words": [r["text"] for r in runs],
        "full_text": " ".join(r["text"] for r in runs),
        "lines": lines,
    }


def _text_insets(norm_rect: dict[str, Any] | None, runs: list[dict[str, Any]], angle_deg: float) -> dict[str, float] | None:
    """Text-to-frame insets in unrotated box-local pt, derived identically for both engines.

    Rotates each run's anchor (left baseline) around the frame centre by -angle using only
    normalized canvas coords + the frame's unrotated width/height, so R grobs and Python draw
    calls are measured the same way. Returns left inset, last-baseline-to-bottom, top-to-first-baseline.
    """
    if not norm_rect or not runs:
        return None
    w, h = norm_rect.get("unrotated_width_pt"), norm_rect.get("unrotated_height_pt")
    if not w or not h:
        return None
    W, H = CANVAS_PT
    bb = norm_rect["bbox_norm_tl"]
    cx, cy = 0.5 * (bb[0] + bb[2]) * W, (1.0 - 0.5 * (bb[1] + bb[3])) * H
    a = math.radians(-angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    xs, ys = [], []
    med_fs = float(np.median([rr.get("fontsize_pt", 11.0) for rr in runs]))
    for r in runs:
        if r.get("fontsize_pt", 11.0) < 0.88 * med_fs and len(runs) > 2:
            continue  # shrunk sup/sub scripts would distort baseline insets
        x = r["x_norm"] * W - cx
        y = (1.0 - r["y_norm"]) * H - cy
        xs.append(x * ca - y * sa)
        ys.append(x * sa + y * ca)
    if not xs:
        return None
    w, h = float(w), float(h)
    return {
        "left": round(min(xs) + w / 2, 2),
        "bottom_to_last_baseline": round(min(ys) + h / 2, 2),
        "top_to_first_baseline": round(h / 2 - max(ys), 2),
    }


def _cluster_lines(runs: list[dict[str, Any]], angle_deg: float) -> list[dict[str, Any]]:
    """Group text runs into visual lines using unrotated local y coordinates.

    Subscripts/superscripts (fontsize < 0.9 * max line fontsize) are attached to their
    nearest baseline within 8pt so they don't create spurious extra lines, and their
    signed vertical offset (`voff_pt`) relative to that line's baseline is recorded!
    """
    if not runs:
        return []
    # Compute local (x, y) in pt72 (y-up) if not already present
    rad = math.radians(-angle_deg)
    ca, sa = math.cos(rad), math.sin(rad)
    enriched = []
    for r in runs:
        if r.get("local_x_pt") is not None and r.get("local_y_pt") is not None:
            lx, ly = float(r["local_x_pt"]), float(r["local_y_pt"])
        else:
            # Approximate from norm_tl assuming 640x480 pt72 canvas
            x_pt = r["x_norm"] * 640.0
            y_pt = (1.0 - r["y_norm"]) * 480.0
            lx = x_pt * ca - y_pt * sa
            ly = x_pt * sa + y_pt * ca
        enriched.append((lx, ly, r))

    # First cluster normal-sized tokens (not shrunk sub/sup) by y within 4.5pt.
    # Use box median fontsize (not max!) so a single 18pt span doesn't mark all 11pt words as scripts.
    med_fs = float(np.median([r["fontsize_pt"] for _, _, r in enriched]))
    base_tokens = [e for e in enriched if e[2]["fontsize_pt"] >= 0.88 * med_fs or len(enriched) <= 2]
    if not base_tokens:
        base_tokens = list(enriched)

    # Sort descending y (top line first in y-up coords)
    base_tokens.sort(key=lambda e: -e[1])
    line_ys: list[float] = []
    for _, ly, _ in base_tokens:
        if not any(abs(ly - y0) <= 4.5 for y0 in line_ys):
            line_ys.append(ly)
    line_ys.sort(reverse=True)

    # Assign every token to nearest line_y
    lines: list[list[tuple[float, float, dict]]] = [[] for _ in line_ys]
    for lx, ly, r in enriched:
        best_i = min(range(len(line_ys)), key=lambda i: abs(ly - line_ys[i]))
        lines[best_i].append((lx, ly, r))

    out_lines = []
    for i, grp in enumerate(lines):
        grp.sort(key=lambda e: e[0])
        # Baseline y = median of non-shrunk tokens on this line (relative to line median fontsize)
        line_med_fs = float(np.median([r["fontsize_pt"] for _, _, r in grp]))
        normals = [ly for _, ly, r in grp if r["fontsize_pt"] >= 0.88 * line_med_fs]
        base_y = float(np.median(normals)) if normals else line_ys[i]
        tok_out = []
        for lx, ly, r in grp:
            voff = round(ly - base_y, 2)
            tok_out.append({
                "text": r["text"],
                "fontsize_pt": r["fontsize_pt"],
                "voff_pt": voff,
                "is_small_script": r["fontsize_pt"] < 0.88 * line_med_fs,
            })
        out_lines.append({
            "line_index": i,
            "baseline_y_pt": round(base_y, 2),
            "text": " ".join(t["text"] for t in tok_out),
            "tokens": tok_out,
        })
    return out_lines


def _circular_angle_diff(a: float, b: float) -> float:
    d = abs((a - b + 180.0) % 360.0 - 180.0)
    return round(d, 3)


def _enrich_box_geometry(box: dict[str, Any], fig_w: float, fig_h: float, dpi: float):
    """Rotate rect vertices and text positions by -angle to recover local width/height/padding/lines."""
    angle_deg = float(box.get("angle_deg", 0.0))
    rad = math.radians(-angle_deg)
    ca, sa = math.cos(rad), math.sin(rad)

    def to_pt_bl(xn: float, yn: float) -> tuple[float, float]:
        # Convert norm_tl to pt72 bottom-left so standard CCW rotation math works cleanly
        return xn * fig_w * 72.0 / dpi, (1.0 - yn) * fig_h * 72.0 / dpi

    def rot(x: float, y: float) -> tuple[float, float]:
        return x * ca - y * sa, x * sa + y * ca

    rect_verts = box.pop("rect_vertices_norm_tl", None)
    rect_local = None
    if rect_verts and box.get("rect") is not None:
        pts = [rot(*to_pt_bl(vx, vy)) for vx, vy in rect_verts]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        rx0, ry0, rx1, ry1 = min(xs), min(ys), max(xs), max(ys)
        rect_local = [round(rx0, 3), round(ry0, 3), round(rx1, 3), round(ry1, 3)]
        box["rect"]["unrotated_width_pt"] = round(rx1 - rx0, 3)
        box["rect"]["unrotated_height_pt"] = round(ry1 - ry0, 3)

    # Project each text run into unrotated box-local pt coordinates
    text_xs0, text_xs1, text_ys_base, text_ys_top, text_ys_bot = [], [], [], [], []
    for tr in box.get("text_runs", []):
        px, py = rot(*to_pt_bl(tr["x_norm"], tr["y_norm"]))
        tr["local_x_pt"] = round(px, 3)
        tr["local_y_pt"] = round(py, 3)
        adv = float(tr.get("advance_width_pt", 0.0))
        h = float(tr.get("height_pt", tr.get("fontsize_pt", 11.0)))
        d = float(tr.get("descent_pt", 2.0))
        text_xs0.append(px)
        text_xs1.append(px + adv)
        text_ys_base.append(py)
        text_ys_top.append(py + (h - d))
        text_ys_bot.append(py - d)

    if rect_local is not None and text_xs0:
        rx0, ry0, rx1, ry1 = rect_local
        pad_left = round(min(text_xs0) - rx0, 2)
        pad_right = round(rx1 - max(text_xs1), 2)
        pad_bottom = round(min(text_ys_bot) - ry0, 2)
        pad_top = round(ry1 - max(text_ys_top), 2)
        overflow_x = round(max(0.0, rx0 - min(text_xs0), max(text_xs1) - rx1), 2)
        overflow_y = round(max(0.0, ry0 - min(text_ys_bot), max(text_ys_top) - ry1), 2)
        box["layout_metrics"] = {
            "pad_left_pt": pad_left,
            "pad_right_pt": pad_right,
            "pad_bottom_pt": pad_bottom,
            "pad_top_pt": pad_top,
            "text_overflow_x_pt": overflow_x,
            "text_overflow_y_pt": overflow_y,
            "all_text_contained": bool(overflow_x <= 2.5 and overflow_y <= 2.5),
        }
