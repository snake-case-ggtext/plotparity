"""treemapify adapter: treemap tiles, nested subgroup borders, and fitted tile/subgroup labels.

Both R (observe.R) and Python (observe_py.py + probe draw calls) express every tile, subgroup
border, and text label bounding box in per-panel normalized [0, 1] x [0, 1] coordinates
(bottom-left origin) alongside physical panel dimensions in points.

Components have `kind` in {"tile", "border", "label"}:
  - tiles pair per panel by Hungarian assignment on area + spatial bbox (never color/alpha)
  - subgroup borders pair per panel by Hungarian assignment on spatial bbox (+ paint-order tiebreak)
  - visible labels pair per panel by whitespace-normalized text (+ spatial distance for duplicates)

Checks:
  - tile_area            : exact relative area conservation vs R
  - tile_geometry        : tile [x0, y0, x1, y1] layout & start-corner agreement
  - tile_style           : fill color/alpha, border color/alpha/linewidth
  - subgroup_containment : subgroup border bbox/style agreement & strict containment of child tiles
  - text_fits_tile       : strict containment inside enclosing tile/subgroup box, alignment/place,
                           reflow wrapping, and grow/shrink font sizing
  - label_style          : text color, alpha, fontweight, fontstyle
  - tiles_no_overlap     : whole-plot zero pairwise tile overlap & full canvas area conservation
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

from ...compare import color_delta_e, exceeds
from ...engines.python.probe import point_in_bbox

NAME = "treemapify"
VERSION = "0.1.0"
HERE = Path(__file__).resolve().parent
OBSERVE_R = HERE / "observe.R"
PROFILE = HERE / "profile.toml"
EXPECT_KEYS: set[str] = set()


# ───────────────────────────── coordinate helpers ─────────────────────────────

def _norm_tl_to_panel_npc(bb_tl: list[float], pbb: list[float]) -> tuple[float, float, float, float]:
    """Convert canvas norm_tl [x0, y0_top, x1, y1_bot] to panel NPC [x0, y0_bot, x1, y1_top]."""
    pw = max(float(pbb[2]) - float(pbb[0]), 1e-9)
    ph = max(float(pbb[3]) - float(pbb[1]), 1e-9)
    x0 = (float(bb_tl[0]) - float(pbb[0])) / pw
    x1 = (float(bb_tl[2]) - float(pbb[0])) / pw
    y0 = (float(pbb[3]) - float(bb_tl[3])) / ph
    y1 = (float(pbb[3]) - float(bb_tl[1])) / ph
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _norm_text(s: str) -> str:
    return " ".join(str(s).split())


def _is_axis_aligned_rect(verts: np.ndarray) -> bool:
    if verts.ndim != 2 or len(verts) < 4:
        return False
    xs, ys = verts[:, 0], verts[:, 1]
    bw, bh = float(xs.max() - xs.min()), float(ys.max() - ys.min())
    if bw < 1e-5 or bh < 1e-5:
        return False
    area = 0.5 * abs(float(np.dot(xs, np.roll(ys, -1)) - np.dot(ys, np.roll(xs, -1))))
    return abs(area - bw * bh) <= 0.08 * bw * bh


def _panel_for(cx: float, cy: float, panels: list[dict]) -> dict | None:
    for p in panels:
        if point_in_bbox(cx, cy, p["bbox_norm_tl"], pad=0.005):
            return p
    return None


def _box_overflow_pt(inner: list[float], outer: list[float], pw: float, ph: float) -> float:
    return max(
        0.0,
        (outer[0] - inner[0]) * pw,
        (inner[2] - outer[2]) * pw,
        (outer[1] - inner[1]) * ph,
        (inner[3] - outer[3]) * ph,
    )


def _find_enclosing_box(cx: float, cy: float, boxes: list[list[float]]) -> list[float] | None:
    """Return the smallest box containing (cx, cy), or the nearest box if outside all."""
    containing = [
        b for b in boxes
        if (b[0] - 1e-3 <= cx <= b[2] + 1e-3) and (b[1] - 1e-3 <= cy <= b[3] + 1e-3)
    ]
    if containing:
        return min(containing, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    if not boxes:
        return None
    def dist(b: list[float]) -> float:
        dx = max(b[0] - cx, 0.0, cx - b[2])
        dy = max(b[1] - cy, 0.0, cy - b[3])
        return math.hypot(dx, dy)
    return min(boxes, key=dist)


def _annotate_panel_components(
    tiles: list[dict],
    borders: list[dict],
    labels: list[dict],
    pw: float,
    ph: float,
) -> None:
    """Attach panel-wide totals, border child-tile containment stats, and label enclosing boxes."""
    total_tile_area = sum(t["area"] for t in tiles)
    for idx, t in enumerate(sorted(tiles, key=lambda d: (round(d["x0"], 2), round(d["y0"], 2), round(d["area"], 4)))):
        t["tile_idx"] = idx
        t["panel_tile_area_sum"] = round(total_tile_area, 6)

    tile_boxes = [[t["x0"], t["y0"], t["x1"], t["y1"]] for t in tiles]
    border_boxes = [[b["x0"], b["y0"], b["x1"], b["y1"]] for b in borders]

    for idx, b in enumerate(sorted(borders, key=lambda d: (d["paint_index"], round(d["x0"], 2), round(d["y0"], 2)))):
        b["border_idx"] = idx
        b["border_rank"] = idx
        bb = [b["x0"], b["y0"], b["x1"], b["y1"]]
        max_ov = 0.0
        child_count = 0
        for t in tiles:
            if (bb[0] - 1e-3 <= t["cx"] <= bb[2] + 1e-3) and (bb[1] - 1e-3 <= t["cy"] <= bb[3] + 1e-3):
                child_count += 1
                ov = _box_overflow_pt([t["x0"], t["y0"], t["x1"], t["y1"]], bb, pw, ph)
                if ov > max_ov:
                    max_ov = ov
        b["child_tile_count"] = child_count
        b["child_tile_max_overflow_pt"] = round(max_ov, 3)

    by_text: dict[str, list[dict]] = {}
    for lb in labels:
        et = _find_enclosing_box(lb["cx"], lb["cy"], tile_boxes)
        eb = _find_enclosing_box(lb["cx"], lb["cy"], border_boxes)
        lb["enclosing_tile"] = [round(v, 6) for v in et] if et is not None else [0.0, 0.0, 1.0, 1.0]
        lb["enclosing_border"] = [round(v, 6) for v in eb] if eb is not None else None
        lb["has_borders_in_panel"] = bool(border_boxes)
        by_text.setdefault(lb["norm_text"], []).append(lb)

    for nt in sorted(by_text):
        grp = sorted(by_text[nt], key=lambda d: (round(d["cx"], 3), round(d["cy"], 3)))
        for occ, lb in enumerate(grp):
            lb["occ_idx"] = occ


# ───────────────────────────── R extraction ─────────────────────────────

def _extract_r(obs: dict[str, Any], canvas_pt: tuple[float, float]) -> list[dict]:
    tm = obs.get("treemapify") or {}
    panels = tm.get("panels") or []
    multi_panel = len(panels) > 1
    W, H = canvas_pt
    comps: list[dict] = []

    for panel in panels:
        pi = int(panel.get("panel_index", 1))
        pbb = [float(v) for v in (panel.get("bbox_norm_tl") or [0.0, 0.0, 1.0, 1.0])]
        pw = max((pbb[2] - pbb[0]) * W, 1e-6)
        ph = max((pbb[3] - pbb[1]) * H, 1e-6)

        tiles: list[dict] = []
        for raw in panel.get("tiles") or []:
            x0, y0, x1, y1 = float(raw["x0"]), float(raw["y0"]), float(raw["x1"]), float(raw["y1"])
            w, h = max(x1 - x0, 0.0), max(y1 - y0, 0.0)
            if w < 1e-5 or h < 1e-5:
                continue
            tiles.append({
                "kind": "tile",
                "panel": pi,
                "multi_panel": multi_panel,
                "panel_w_pt": round(pw, 3),
                "panel_h_pt": round(ph, 3),
                "paint_index": int(raw.get("paint_index", 0)),
                "x0": round(x0, 6), "y0": round(y0, 6), "x1": round(x1, 6), "y1": round(y1, 6),
                "cx": round(0.5 * (x0 + x1), 6), "cy": round(0.5 * (y0 + y1), 6),
                "width": round(w, 6), "height": round(h, 6), "area": round(w * h, 6),
                "bbox_norm_tl": [round(float(v), 6) for v in (raw.get("bbox_norm_tl") or pbb)],
                "facecolor_hex": raw.get("facecolor_hex", "none"),
                "face_alpha": float(raw.get("face_alpha", 1.0)),
                "edgecolor_hex": raw.get("edgecolor_hex", "none"),
                "edge_alpha": float(raw.get("edge_alpha", 0.0)),
                "linewidth_pt": float(raw.get("linewidth_pt", 0.0)),
                "linetype": raw.get("linetype", "solid"),
            })

        borders: list[dict] = []
        for raw in panel.get("borders") or []:
            x0, y0, x1, y1 = float(raw["x0"]), float(raw["y0"]), float(raw["x1"]), float(raw["y1"])
            w, h = max(x1 - x0, 0.0), max(y1 - y0, 0.0)
            if w < 1e-5 or h < 1e-5:
                continue
            borders.append({
                "kind": "border",
                "panel": pi,
                "multi_panel": multi_panel,
                "panel_w_pt": round(pw, 3),
                "panel_h_pt": round(ph, 3),
                "paint_index": int(raw.get("paint_index", 0)),
                "x0": round(x0, 6), "y0": round(y0, 6), "x1": round(x1, 6), "y1": round(y1, 6),
                "cx": round(0.5 * (x0 + x1), 6), "cy": round(0.5 * (y0 + y1), 6),
                "width": round(w, 6), "height": round(h, 6), "area": round(w * h, 6),
                "bbox_norm_tl": [round(float(v), 6) for v in (raw.get("bbox_norm_tl") or pbb)],
                "edgecolor_hex": raw.get("edgecolor_hex", "none"),
                "edge_alpha": float(raw.get("edge_alpha", 0.0)),
                "linewidth_pt": float(raw.get("linewidth_pt", 0.0)),
                "linetype": raw.get("linetype", "solid"),
            })

        labels: list[dict] = []
        for raw in panel.get("labels") or []:
            txt = str(raw.get("text", ""))
            nt = _norm_text(txt)
            if not nt:
                continue
            x0, y0, x1, y1 = float(raw["x0"]), float(raw["y0"]), float(raw["x1"]), float(raw["y1"])
            labels.append({
                "kind": "label",
                "panel": pi,
                "multi_panel": multi_panel,
                "panel_w_pt": round(pw, 3),
                "panel_h_pt": round(ph, 3),
                "paint_index": int(raw.get("paint_index", 0)),
                "text": txt,
                "norm_text": nt,
                "label_role": str(raw.get("label_role", "tile")),
                "line_count": int(raw.get("line_count", txt.count("\n") + 1)),
                "x0": round(x0, 6), "y0": round(y0, 6), "x1": round(x1, 6), "y1": round(y1, 6),
                "cx": round(0.5 * (x0 + x1), 6), "cy": round(0.5 * (y0 + y1), 6),
                "bbox_norm_tl": [round(float(v), 6) for v in (raw.get("bbox_norm_tl") or pbb)],
                "fontsize_pt": float(raw.get("fontsize_pt", 11.0)),
                "color_hex": raw.get("color_hex", "#000000"),
                "alpha": float(raw.get("alpha", 1.0)),
                "fontweight": str(raw.get("fontweight", "normal")),
                "fontstyle": str(raw.get("fontstyle", "normal")),
            })

        _annotate_panel_components(tiles, borders, labels, pw, ph)
        comps.extend(tiles)
        comps.extend(borders)
        comps.extend(labels)

    return comps


# ───────────────────────────── Python extraction ─────────────────────────────

def _group_label_key(ev: dict) -> tuple | None:
    """Return a grouping key for multi-line text events drawn inside one logical text group."""
    groups = ev.get("groups") or []
    gids = ev.get("group_ids") or []
    for g, gid in reversed(list(zip(groups, gids))):
        if g not in ("figure", "axes", "patch", "axes_patch"):
            return (ev.get("owner"), g, gid)
    return None


def _extract_python(obs: dict[str, Any], canvas_pt: tuple[float, float]) -> tuple[list[dict], set[int]]:
    tm = obs.get("treemapify") or {}
    panels = tm.get("panels") or []
    events = obs.get("events") or []
    owners = obs.get("owners") or []
    extracted_classes = set(tm.get("extracted_classes") or [])
    W, H = canvas_pt

    if not panels:
        panels = [{"panel_index": 1, "bbox_norm_tl": [0.0, 0.0, 1.0, 1.0], "shapes": []}]
    multi_panel = len(panels) > 1

    # Mark all draw events owned by classes extracted via observe_py.py
    assigned: set[int] = set()
    if extracted_classes:
        for ev in events:
            k = ev.get("owner")
            if k is not None and k < len(owners) and owners[k].get("class") in extracted_classes:
                assigned.add(ev["paint_index"])

    by_panel_tiles: dict[int, list[dict]] = {int(p.get("panel_index", i + 1)): [] for i, p in enumerate(panels)}
    by_panel_borders: dict[int, list[dict]] = {int(p.get("panel_index", i + 1)): [] for i, p in enumerate(panels)}
    by_panel_labels: dict[int, list[dict]] = {int(p.get("panel_index", i + 1)): [] for i, p in enumerate(panels)}
    panel_map: dict[int, dict] = {int(p.get("panel_index", i + 1)): p for i, p in enumerate(panels)}

    # 1. Extract tiles and borders from observe_py.py shapes (Rectangle / Patch / PolyCollection)
    for pi, panel in panel_map.items():
        pbb = [float(v) for v in (panel.get("bbox_norm_tl") or [0.0, 0.0, 1.0, 1.0])]
        pw = max((pbb[2] - pbb[0]) * W, 1e-6)
        ph = max((pbb[3] - pbb[1]) * H, 1e-6)
        for sh in sorted(panel.get("shapes") or [], key=lambda e: e.get("paint_index", 0)):
            fc = sh.get("facecolor_hex", "none")
            fa = float(sh.get("face_alpha", 0.0))
            ec = sh.get("edgecolor_hex", "none")
            ea = float(sh.get("edge_alpha", 0.0))
            lw = float(sh.get("linewidth_pt", 0.0))
            if (fc == "none" or fa <= 0) and (ec == "none" or ea <= 0 or lw <= 0):
                continue
            for pc in sh.get("pieces") or []:
                vn = np.asarray(pc.get("norm") or [], dtype=float)
                if vn.ndim != 2 or len(vn) < 4 or not _is_axis_aligned_rect(vn):
                    continue
                bb_tl = [float(vn[:, 0].min()), float(vn[:, 1].min()),
                         float(vn[:, 0].max()), float(vn[:, 1].max())]
                x0, y0, x1, y1 = _norm_tl_to_panel_npc(bb_tl, pbb)
                w, h = max(x1 - x0, 0.0), max(y1 - y0, 0.0)
                if w < 1e-5 or h < 1e-5:
                    continue
                base = {
                    "panel": pi,
                    "multi_panel": multi_panel,
                    "panel_w_pt": round(pw, 3),
                    "panel_h_pt": round(ph, 3),
                    "paint_index": int(sh.get("paint_index", 0)),
                    "x0": round(x0, 6), "y0": round(y0, 6), "x1": round(x1, 6), "y1": round(y1, 6),
                    "cx": round(0.5 * (x0 + x1), 6), "cy": round(0.5 * (y0 + y1), 6),
                    "width": round(w, 6), "height": round(h, 6), "area": round(w * h, 6),
                    "bbox_norm_tl": [round(v, 6) for v in bb_tl],
                    "edgecolor_hex": ec if lw > 0 else "none",
                    "edge_alpha": ea if (ec != "none" and lw > 0) else 0.0,
                    "linewidth_pt": lw if (ec != "none" and lw > 0) else 0.0,
                    "linetype": sh.get("linetype", "solid") if (ec != "none" and lw > 0) else "blank",
                }
                if fc != "none" and fa > 0:
                    base.update(kind="tile", facecolor_hex=fc, face_alpha=fa)
                    by_panel_tiles[pi].append(base)
                elif ec != "none" and ea > 0 and lw > 0:
                    base.update(kind="border")
                    by_panel_borders[pi].append(base)

    # 2. Extract any custom-artist path rectangles & ALL in-panel text events from probe draw calls
    raw_text_evs: list[tuple[int, dict]] = []
    for ev in events:
        if "role" in ev:
            continue
        k = ev.get("owner")
        mod = owners[k].get("module", "") if (k is not None and k < len(owners)) else ""
        is_custom = bool(mod) and not mod.startswith(("matplotlib.", "plotnine."))
        kind = ev.get("kind")

        if kind == "path" and is_custom and ev["paint_index"] not in assigned:
            bb_tl = ev.get("bbox_norm_tl")
            if not bb_tl:
                continue
            cx_n, cy_n = 0.5 * (bb_tl[0] + bb_tl[2]), 0.5 * (bb_tl[1] + bb_tl[3])
            panel = _panel_for(cx_n, cy_n, panels)
            if panel is None:
                continue
            pi = int(panel.get("panel_index", 1))
            pbb = [float(v) for v in panel["bbox_norm_tl"]]
            pw = max((pbb[2] - pbb[0]) * W, 1e-6)
            ph = max((pbb[3] - pbb[1]) * H, 1e-6)
            x0, y0, x1, y1 = _norm_tl_to_panel_npc(bb_tl, pbb)
            w, h = max(x1 - x0, 0.0), max(y1 - y0, 0.0)
            if w < 1e-5 or h < 1e-5:
                continue
            fc = ev.get("facecolor_hex", "none")
            fa = float(ev.get("face_alpha", 0.0))
            ec = ev.get("edgecolor_hex", "none")
            ea = float(ev.get("edge_alpha", 0.0))
            lw = float(ev.get("linewidth_pt", 0.0))
            assigned.add(ev["paint_index"])
            base = {
                "panel": pi, "multi_panel": multi_panel,
                "panel_w_pt": round(pw, 3), "panel_h_pt": round(ph, 3),
                "paint_index": int(ev["paint_index"]),
                "x0": round(x0, 6), "y0": round(y0, 6), "x1": round(x1, 6), "y1": round(y1, 6),
                "cx": round(0.5 * (x0 + x1), 6), "cy": round(0.5 * (y0 + y1), 6),
                "width": round(w, 6), "height": round(h, 6), "area": round(w * h, 6),
                "bbox_norm_tl": [round(float(v), 6) for v in bb_tl],
                "edgecolor_hex": ec if lw > 0 else "none",
                "edge_alpha": ea if (ec != "none" and lw > 0) else 0.0,
                "linewidth_pt": lw if (ec != "none" and lw > 0) else 0.0,
                "linetype": ev.get("linetype", "solid") if (ec != "none" and lw > 0) else "blank",
            }
            if fc != "none" and fa > 0:
                base.update(kind="tile", facecolor_hex=fc, face_alpha=fa)
                by_panel_tiles[pi].append(base)
            elif ec != "none" and ea > 0 and lw > 0:
                base.update(kind="border")
                by_panel_borders[pi].append(base)

        elif kind == "text":
            if not str(ev.get("text", "")).strip():
                continue
            bb_tl = ev.get("bbox_norm_tl")
            if not bb_tl:
                continue
            cx_n, cy_n = 0.5 * (bb_tl[0] + bb_tl[2]), 0.5 * (bb_tl[1] + bb_tl[3])
            panel = _panel_for(cx_n, cy_n, panels)
            if panel is None:
                continue
            # Require either custom artist OR inside axes group (strictly inside panel bbox)
            if not is_custom and "axes" not in (ev.get("groups") or []):
                continue
            assigned.add(ev["paint_index"])
            raw_text_evs.append((int(panel.get("panel_index", 1)), ev))

    # 3. Group multi-line text events into logical labels
    grouped: list[tuple[int, list[dict]]] = []
    group_map: dict[tuple, int] = {}
    for pi, ev in raw_text_evs:
        gkey = _group_label_key(ev)
        if gkey is not None:
            full_key = (pi, gkey)
            if full_key in group_map:
                grouped[group_map[full_key]][1].append(ev)
            else:
                group_map[full_key] = len(grouped)
                grouped.append((pi, [ev]))
        else:
            grouped.append((pi, [ev]))

    for pi, evs in grouped:
        panel = panel_map[pi]
        pbb = [float(v) for v in panel["bbox_norm_tl"]]
        pw = max((pbb[2] - pbb[0]) * W, 1e-6)
        ph = max((pbb[3] - pbb[1]) * H, 1e-6)
        # Sort lines top-to-bottom in canvas norm_tl (increasing y_norm)
        evs_sorted = sorted(evs, key=lambda e: (round(float(e["bbox_norm_tl"][1]), 5), float(e["bbox_norm_tl"][0])))
        txt = "\n".join(str(e["text"]) for e in evs_sorted)
        nt = _norm_text(txt)
        if not nt:
            continue
        bb_tl = [
            min(float(e["bbox_norm_tl"][0]) for e in evs_sorted),
            min(float(e["bbox_norm_tl"][1]) for e in evs_sorted),
            max(float(e["bbox_norm_tl"][2]) for e in evs_sorted),
            max(float(e["bbox_norm_tl"][3]) for e in evs_sorted),
        ]
        x0, y0, x1, y1 = _norm_tl_to_panel_npc(bb_tl, pbb)
        first = evs_sorted[0]
        by_panel_labels[pi].append({
            "kind": "label",
            "panel": pi,
            "multi_panel": multi_panel,
            "panel_w_pt": round(pw, 3),
            "panel_h_pt": round(ph, 3),
            "paint_index": min(int(e["paint_index"]) for e in evs_sorted),
            "text": txt,
            "norm_text": nt,
            "label_role": "tile",  # resolved via reference pair during checks
            "line_count": len(evs_sorted),
            "x0": round(x0, 6), "y0": round(y0, 6), "x1": round(x1, 6), "y1": round(y1, 6),
            "cx": round(0.5 * (x0 + x1), 6), "cy": round(0.5 * (y0 + y1), 6),
            "bbox_norm_tl": [round(v, 6) for v in bb_tl],
            "fontsize_pt": float(first.get("fontsize_pt", 11.0)),
            "color_hex": first.get("color_hex", "#000000"),
            "alpha": float(first.get("alpha", 1.0)),
            "fontweight": str(first.get("fontweight", "normal")),
            "fontstyle": str(first.get("fontstyle", "normal")),
        })

    # 4. Also support pre-packaged synthetic Python observations (for unit tests)
    for panel in panels:
        pi = int(panel.get("panel_index", 1))
        pbb = [float(v) for v in (panel.get("bbox_norm_tl") or [0.0, 0.0, 1.0, 1.0])]
        pw = max((pbb[2] - pbb[0]) * W, 1e-6)
        ph = max((pbb[3] - pbb[1]) * H, 1e-6)
        if not by_panel_tiles[pi] and panel.get("tiles"):
            for raw in panel["tiles"]:
                x0, y0, x1, y1 = float(raw["x0"]), float(raw["y0"]), float(raw["x1"]), float(raw["y1"])
                w, h = max(x1 - x0, 0.0), max(y1 - y0, 0.0)
                by_panel_tiles[pi].append({
                    "kind": "tile", "panel": pi, "multi_panel": multi_panel,
                    "panel_w_pt": round(pw, 3), "panel_h_pt": round(ph, 3),
                    "paint_index": int(raw.get("paint_index", 0)),
                    "x0": round(x0, 6), "y0": round(y0, 6), "x1": round(x1, 6), "y1": round(y1, 6),
                    "cx": round(0.5 * (x0 + x1), 6), "cy": round(0.5 * (y0 + y1), 6),
                    "width": round(w, 6), "height": round(h, 6), "area": round(w * h, 6),
                    "bbox_norm_tl": [round(float(v), 6) for v in (raw.get("bbox_norm_tl") or pbb)],
                    "facecolor_hex": raw.get("facecolor_hex", "none"),
                    "face_alpha": float(raw.get("face_alpha", 1.0)),
                    "edgecolor_hex": raw.get("edgecolor_hex", "none"),
                    "edge_alpha": float(raw.get("edge_alpha", 0.0)),
                    "linewidth_pt": float(raw.get("linewidth_pt", 0.0)),
                    "linetype": raw.get("linetype", "solid"),
                })
        if not by_panel_borders[pi] and panel.get("borders"):
            for raw in panel["borders"]:
                x0, y0, x1, y1 = float(raw["x0"]), float(raw["y0"]), float(raw["x1"]), float(raw["y1"])
                w, h = max(x1 - x0, 0.0), max(y1 - y0, 0.0)
                by_panel_borders[pi].append({
                    "kind": "border", "panel": pi, "multi_panel": multi_panel,
                    "panel_w_pt": round(pw, 3), "panel_h_pt": round(ph, 3),
                    "paint_index": int(raw.get("paint_index", 0)),
                    "x0": round(x0, 6), "y0": round(y0, 6), "x1": round(x1, 6), "y1": round(y1, 6),
                    "cx": round(0.5 * (x0 + x1), 6), "cy": round(0.5 * (y0 + y1), 6),
                    "width": round(w, 6), "height": round(h, 6), "area": round(w * h, 6),
                    "bbox_norm_tl": [round(float(v), 6) for v in (raw.get("bbox_norm_tl") or pbb)],
                    "edgecolor_hex": raw.get("edgecolor_hex", "none"),
                    "edge_alpha": float(raw.get("edge_alpha", 0.0)),
                    "linewidth_pt": float(raw.get("linewidth_pt", 0.0)),
                    "linetype": raw.get("linetype", "solid"),
                })
        if not by_panel_labels[pi] and panel.get("labels"):
            for raw in panel["labels"]:
                txt = str(raw.get("text", ""))
                nt = _norm_text(txt)
                if not nt:
                    continue
                x0, y0, x1, y1 = float(raw["x0"]), float(raw["y0"]), float(raw["x1"]), float(raw["y1"])
                by_panel_labels[pi].append({
                    "kind": "label", "panel": pi, "multi_panel": multi_panel,
                    "panel_w_pt": round(pw, 3), "panel_h_pt": round(ph, 3),
                    "paint_index": int(raw.get("paint_index", 0)),
                    "text": txt, "norm_text": nt,
                    "label_role": str(raw.get("label_role", "tile")),
                    "line_count": int(raw.get("line_count", txt.count("\n") + 1)),
                    "x0": round(x0, 6), "y0": round(y0, 6), "x1": round(x1, 6), "y1": round(y1, 6),
                    "cx": round(0.5 * (x0 + x1), 6), "cy": round(0.5 * (y0 + y1), 6),
                    "bbox_norm_tl": [round(float(v), 6) for v in (raw.get("bbox_norm_tl") or pbb)],
                    "fontsize_pt": float(raw.get("fontsize_pt", 11.0)),
                    "color_hex": raw.get("color_hex", "#000000"),
                    "alpha": float(raw.get("alpha", 1.0)),
                    "fontweight": str(raw.get("fontweight", "normal")),
                    "fontstyle": str(raw.get("fontstyle", "normal")),
                })

    comps: list[dict] = []
    for pi in sorted(panel_map):
        pbb = [float(v) for v in (panel_map[pi].get("bbox_norm_tl") or [0.0, 0.0, 1.0, 1.0])]
        pw = max((pbb[2] - pbb[0]) * W, 1e-6)
        ph = max((pbb[3] - pbb[1]) * H, 1e-6)
        _annotate_panel_components(by_panel_tiles[pi], by_panel_borders[pi], by_panel_labels[pi], pw, ph)
        comps.extend(by_panel_tiles[pi])
        comps.extend(by_panel_borders[pi])
        comps.extend(by_panel_labels[pi])

    return comps, assigned


def components(engine: str, obs: dict[str, Any], expect: dict[str, Any],
               canvas_pt: tuple[float, float] = (640.0, 480.0)) -> tuple[list[dict], set[int]]:
    if engine == "r":
        return _extract_r(obs, canvas_pt), set()
    return _extract_python(obs, canvas_pt)


def component_id(i: int, comp: dict) -> str:
    prefix = f"p{comp['panel']}:" if comp.get("multi_panel") else ""
    kind = comp["kind"]
    if kind == "tile":
        return f"tile[{prefix}#{comp.get('tile_idx', i) + 1}]"
    if kind == "border":
        return f"border[{prefix}#{comp.get('border_idx', i) + 1}]"
    occ = f"#{comp['occ_idx'] + 1}" if comp.get("occ_idx", 0) > 0 else ""
    return f"label[{prefix}{comp['norm_text'][:24]!r}{occ}]"


def describe_component(comp: dict) -> dict:
    kind = comp["kind"]
    if kind == "tile":
        return {
            "kind": "tile", "panel": comp["panel"],
            "box": [round(comp["x0"], 3), round(comp["y0"], 3), round(comp["x1"], 3), round(comp["y1"], 3)],
            "area": round(comp["area"], 4), "fill": comp["facecolor_hex"],
        }
    if kind == "border":
        return {
            "kind": "border", "panel": comp["panel"],
            "box": [round(comp["x0"], 3), round(comp["y0"], 3), round(comp["x1"], 3), round(comp["y1"], 3)],
            "edge": comp["edgecolor_hex"], "lwd": comp["linewidth_pt"],
        }
    return {
        "kind": "label", "panel": comp["panel"], "text": comp["norm_text"],
        "box": [round(comp["x0"], 3), round(comp["y0"], 3), round(comp["x1"], 3), round(comp["y1"], 3)],
        "fontsize_pt": comp["fontsize_pt"],
    }


# ───────────────────────────── matching ─────────────────────────────

def _hungarian_pairs(ref_idx: list[int], cand_idx: list[int], cost_fn) -> list[tuple[int, int]]:
    if not ref_idx or not cand_idx:
        return []
    mat = np.zeros((len(ref_idx), len(cand_idx)), dtype=float)
    for a, ri in enumerate(ref_idx):
        for b, ci in enumerate(cand_idx):
            mat[a, b] = cost_fn(ri, ci)
    rows, cols = linear_sum_assignment(mat)
    return [(ref_idx[r], cand_idx[c]) for r, c in zip(rows, cols)]


def match(ref: list[dict], cand: list[dict]) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Pair tiles by area + geometry, borders by geometry (+ paint-order tiebreak),
    and labels by (panel, normalized text) with spatial tiebreak."""
    pairs: list[tuple[int, int]] = []
    panels = sorted({c["panel"] for c in ref} | {c["panel"] for c in cand})

    for pi in panels:
        # 1. Tiles: pair by area + bbox distance (never style/color)
        r_tiles = [i for i, c in enumerate(ref) if c["panel"] == pi and c["kind"] == "tile"]
        c_tiles = [j for j, c in enumerate(cand) if c["panel"] == pi and c["kind"] == "tile"]
        def tile_cost(ri: int, ci: int) -> float:
            r, c = ref[ri], cand[ci]
            da = abs(r["area"] - c["area"])
            db = (abs(r["x0"] - c["x0"]) + abs(r["y0"] - c["y0"])
                  + abs(r["x1"] - c["x1"]) + abs(r["y1"] - c["y1"]))
            return 2.0 * da + 0.5 * db
        pairs.extend(_hungarian_pairs(r_tiles, c_tiles, tile_cost))

        # 2. Borders: pair by bbox distance + gentle paint-order tiebreak for coincident nested levels
        r_bords = [i for i, c in enumerate(ref) if c["panel"] == pi and c["kind"] == "border"]
        c_bords = [j for j, c in enumerate(cand) if c["panel"] == pi and c["kind"] == "border"]
        def border_cost(ri: int, ci: int) -> float:
            r, c = ref[ri], cand[ci]
            db = (abs(r["x0"] - c["x0"]) + abs(r["y0"] - c["y0"])
                  + abs(r["x1"] - c["x1"]) + abs(r["y1"] - c["y1"]))
            dr = abs(r.get("border_rank", 0) - c.get("border_rank", 0))
            return db + 0.01 * dr
        pairs.extend(_hungarian_pairs(r_bords, c_bords, border_cost))

        # 3. Labels: exact whitespace-normalized text match within panel, spatial tiebreak for duplicates
        r_labs_by_txt: dict[str, list[int]] = {}
        c_labs_by_txt: dict[str, list[int]] = {}
        for i, c in enumerate(ref):
            if c["panel"] == pi and c["kind"] == "label":
                r_labs_by_txt.setdefault(c["norm_text"], []).append(i)
        for j, c in enumerate(cand):
            if c["panel"] == pi and c["kind"] == "label":
                c_labs_by_txt.setdefault(c["norm_text"], []).append(j)
        for nt in sorted(set(r_labs_by_txt) & set(c_labs_by_txt)):
            def lab_cost(ri: int, ci: int) -> float:
                r, c = ref[ri], cand[ci]
                return math.hypot(r["cx"] - c["cx"], r["cy"] - c["cy"])
            pairs.extend(_hungarian_pairs(r_labs_by_txt[nt], c_labs_by_txt[nt], lab_cost))

    used_r = {i for i, _ in pairs}
    used_c = {j for _, j in pairs}
    missing = [i for i in range(len(ref)) if i not in used_r]
    unexpected = [j for j in range(len(cand)) if j not in used_c]
    return pairs, missing, unexpected


# ───────────────────────────── per-component checks ─────────────────────────────

def tile_area(r, c, P, add, expect):
    if r["kind"] != "tile":
        return False
    ra, ca = r["area"], c["area"]
    rel_tol = P.get("rel_tol", 0.015)
    abs_tol = P.get("tol", 0.002)
    diff = abs(ca - ra)
    allowed = max(abs_tol, rel_tol * max(ra, 1e-6))
    if exceeds(diff, allowed, "tile_area"):
        add("tile_area", round(ra, 4), round(ca, 4),
            f"Tile area is {ca:.4f}, expected {ra:.4f} (Δ={diff:.4f}, allowed={allowed:.4f}).",
            delta=round(ca - ra, 4), sub="area")


def tile_geometry(r, c, P, add, expect):
    if r["kind"] != "tile":
        return False
    tol, pw, ph = P["tol"], r["panel_w_pt"], r["panel_h_pt"]
    for edge, scale in (("x0", pw), ("x1", pw), ("y0", ph), ("y1", ph)):
        d_pt = abs(c[edge] - r[edge]) * scale
        if exceeds(d_pt, tol, "tile_geometry"):
            add("tile_geometry", round(r[edge], 4), round(c[edge], 4),
                f"Tile {edge} is {d_pt:.1f}pt off ({c[edge]:.4f} vs {r[edge]:.4f}).",
                delta=round(d_pt, 2), sub=edge)


def tile_style(r, c, P, add, expect):
    if r["kind"] != "tile":
        return False
    de_f = color_delta_e(r["facecolor_hex"], c["facecolor_hex"])
    if exceeds(de_f, P["tol"], "tile_style"):
        add("tile_style", r["facecolor_hex"], c["facecolor_hex"],
            f"Tile fill {c['facecolor_hex']}, expected {r['facecolor_hex']} (ΔE {de_f}).",
            delta=de_f, sub="fill")
    da_f = abs(c["face_alpha"] - r["face_alpha"])
    if exceeds(da_f, P["alpha_tol"], "tile_style"):
        add("tile_style", r["face_alpha"], c["face_alpha"],
            f"Tile fill alpha {c['face_alpha']}, expected {r['face_alpha']}.",
            delta=round(c["face_alpha"] - r["face_alpha"], 4), sub="fill_alpha")
    de_e = color_delta_e(r["edgecolor_hex"], c["edgecolor_hex"])
    if exceeds(de_e, P["tol"], "tile_style"):
        add("tile_style", r["edgecolor_hex"], c["edgecolor_hex"],
            f"Tile border colour {c['edgecolor_hex']}, expected {r['edgecolor_hex']} (ΔE {de_e}).",
            delta=de_e, sub="edge")
    da_e = abs(c["edge_alpha"] - r["edge_alpha"])
    if exceeds(da_e, P["alpha_tol"], "tile_style"):
        add("tile_style", r["edge_alpha"], c["edge_alpha"],
            f"Tile border alpha {c['edge_alpha']}, expected {r['edge_alpha']}.",
            delta=round(c["edge_alpha"] - r["edge_alpha"], 4), sub="edge_alpha")
    if r["edgecolor_hex"] != "none" and c["edgecolor_hex"] != "none":
        dlw = abs(c["linewidth_pt"] - r["linewidth_pt"])
        lw_tol = max(0.75, 0.35 * r["linewidth_pt"])
        if exceeds(dlw, lw_tol, "tile_style"):
            add("tile_style", round(r["linewidth_pt"], 2), round(c["linewidth_pt"], 2),
                f"Tile border linewidth {c['linewidth_pt']:.2f}pt, expected {r['linewidth_pt']:.2f}pt.",
                delta=round(c["linewidth_pt"] - r["linewidth_pt"], 2), sub="linewidth")


def subgroup_containment(r, c, P, add, expect):
    if r["kind"] != "border":
        return False
    tol, pw, ph = P["tol"], r["panel_w_pt"], r["panel_h_pt"]
    for edge, scale in (("x0", pw), ("x1", pw), ("y0", ph), ("y1", ph)):
        d_pt = abs(c[edge] - r[edge]) * scale
        if exceeds(d_pt, tol, "subgroup_containment"):
            add("subgroup_containment", round(r[edge], 4), round(c[edge], 4),
                f"Subgroup border {edge} is {d_pt:.1f}pt off ({c[edge]:.4f} vs {r[edge]:.4f}).",
                delta=round(d_pt, 2), sub=edge)
    de_e = color_delta_e(r["edgecolor_hex"], c["edgecolor_hex"])
    if exceeds(de_e, 6.0, "subgroup_containment"):
        add("subgroup_containment", r["edgecolor_hex"], c["edgecolor_hex"],
            f"Subgroup border colour {c['edgecolor_hex']}, expected {r['edgecolor_hex']} (ΔE {de_e}).",
            delta=de_e, sub="edge")
    da_e = abs(c["edge_alpha"] - r["edge_alpha"])
    if exceeds(da_e, P.get("alpha_tol", 0.05), "subgroup_containment"):
        add("subgroup_containment", r["edge_alpha"], c["edge_alpha"],
            f"Subgroup border alpha {c['edge_alpha']}, expected {r['edge_alpha']}.",
            delta=round(c["edge_alpha"] - r["edge_alpha"], 4), sub="edge_alpha")
    dlw = abs(c["linewidth_pt"] - r["linewidth_pt"])
    lw_tol = max(1.0, 0.35 * r["linewidth_pt"])
    if exceeds(dlw, lw_tol, "subgroup_containment"):
        add("subgroup_containment", round(r["linewidth_pt"], 2), round(c["linewidth_pt"], 2),
            f"Subgroup border linewidth {c['linewidth_pt']:.2f}pt, expected {r['linewidth_pt']:.2f}pt.",
            delta=round(c["linewidth_pt"] - r["linewidth_pt"], 2), sub="linewidth")
    ov = float(c.get("child_tile_max_overflow_pt", 0.0))
    if exceeds(ov, max(1.5, 0.5 * tol), "subgroup_containment"):
        add("subgroup_containment", "<= 1.5pt", round(ov, 2),
            f"Child tile pokes {ov:.1f}pt outside its enclosing subgroup border.",
            delta=round(ov, 2), sub="child_tiles")


def _zone_1d(lo: float, hi: float, box_lo: float, box_hi: float) -> str:
    """Classify a label interval [lo, hi] inside [box_lo, box_hi] as low/mid/high
    using normalized slack position gap_lo / (gap_lo + gap_hi)."""
    gap_lo = max(0.0, lo - box_lo)
    gap_hi = max(0.0, box_hi - hi)
    slack = gap_lo + gap_hi
    if slack <= 1e-6:
        return "mid"
    pos = gap_lo / slack
    if pos < 0.30:
        return "low"
    if pos > 0.70:
        return "high"
    return "mid"


def text_fits_tile(r, c, P, add, expect):
    if r["kind"] != "label":
        return False
    tol = P["tol"]
    rel_tol = P.get("rel_tol", 0.35)
    pw, ph = r["panel_w_pt"], r["panel_h_pt"]
    role = r.get("label_role", "tile")

    if role == "subgroup" and r.get("enclosing_border") is not None:
        r_box = r["enclosing_border"]
        c_box = c.get("enclosing_border") or [0.0, 0.0, 1.0, 1.0]
    elif role == "subgroup":
        r_box = [0.0, 0.0, 1.0, 1.0]
        c_box = [0.0, 0.0, 1.0, 1.0]
    else:
        r_box = r.get("enclosing_tile") or [0.0, 0.0, 1.0, 1.0]
        c_box = c.get("enclosing_tile") or [0.0, 0.0, 1.0, 1.0]

    # 1. Strict containment inside enclosing tile / subgroup box
    r_ov = _box_overflow_pt([r["x0"], r["y0"], r["x1"], r["y1"]], r_box, pw, ph)
    c_ov = _box_overflow_pt([c["x0"], c["y0"], c["x1"], c["y1"]], c_box, pw, ph)
    allowed_ov = max(tol, r_ov + 1.5)
    if exceeds(c_ov, allowed_ov, "text_fits_tile"):
        add("text_fits_tile", f"<= {allowed_ov:.1f}pt overflow", round(c_ov, 2),
            f"Label {c['norm_text']!r} overflows its enclosing {role} box by {c_ov:.1f}pt.",
            delta=round(c_ov, 2), sub="overflow")

    # 2. Verify label sits inside the corresponding box (not placed in a completely different tile)
    box_err = max(
        abs(c_box[0] - r_box[0]) * pw, abs(c_box[2] - r_box[2]) * pw,
        abs(c_box[1] - r_box[1]) * ph, abs(c_box[3] - r_box[3]) * ph,
    )
    if exceeds(box_err, 12.0, "text_fits_tile"):
        add("text_fits_tile", [round(v, 3) for v in r_box], [round(v, 3) for v in c_box],
            f"Label {c['norm_text']!r} sits inside a different {role} box ({box_err:.1f}pt away).",
            delta=round(box_err, 2), sub="wrong_box")

    # 3. Alignment / place agreement (horizontal and vertical zone inside enclosing box)
    r_zx = _zone_1d(r["x0"], r["x1"], r_box[0], r_box[2])
    c_zx = _zone_1d(c["x0"], c["x1"], c_box[0], c_box[2])
    r_zy = _zone_1d(r["y0"], r["y1"], r_box[1], r_box[3])
    c_zy = _zone_1d(c["y0"], c["y1"], c_box[1], c_box[3])
    # Only enforce zone when the label leaves meaningful slack (> 12pt) in that axis
    r_slack_x = (r_box[2] - r_box[0] - (r["x1"] - r["x0"])) * pw
    r_slack_y = (r_box[3] - r_box[1] - (r["y1"] - r["y0"])) * ph
    if r_slack_x > 16.0 and r_zx != c_zx:
        add("text_fits_tile", r_zx, c_zx,
            f"Label {c['norm_text']!r} horizontal place mismatch: {c_zx} vs expected {r_zx}.",
            sub="place_x")
    if r_slack_y > 16.0 and r_zy != c_zy:
        add("text_fits_tile", r_zy, c_zy,
            f"Label {c['norm_text']!r} vertical place mismatch: {c_zy} vs expected {r_zy}.",
            sub="place_y")

    # 4. Reflow check: if R wrapped into >= 2 lines, candidate should also wrap (unless single-line fits at same size)
    if r["line_count"] >= 2 and c["line_count"] < 2:
        if c["fontsize_pt"] < 0.85 * r["fontsize_pt"]:
            add("text_fits_tile", f">= 2 lines (R={r['line_count']})", c["line_count"],
                f"Label {c['norm_text']!r} did not reflow (1 line at {c['fontsize_pt']:.1f}pt vs R {r['line_count']} lines at {r['fontsize_pt']:.1f}pt).",
                sub="reflow")

    # 5. Font size / grow / shrink check
    rfs, cfs = r["fontsize_pt"], c["fontsize_pt"]
    fs_diff = abs(cfs - rfs)
    fs_allowed = max(3.5, rel_tol * rfs)
    if exceeds(fs_diff, fs_allowed, "text_fits_tile"):
        add("text_fits_tile", round(rfs, 2), round(cfs, 2),
            f"Label {c['norm_text']!r} fontsize {cfs:.1f}pt vs expected {rfs:.1f}pt (allowed +/-{fs_allowed:.1f}pt).",
            delta=round(cfs - rfs, 2), sub="fontsize")


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
    if r["fontweight"] != c["fontweight"]:
        add("label_style", r["fontweight"], c["fontweight"],
            f"Label fontweight {c['fontweight']!r}, expected {r['fontweight']!r}.",
            sub="weight")
    if r["fontstyle"] != c["fontstyle"]:
        add("label_style", r["fontstyle"], c["fontstyle"],
            f"Label fontstyle {c['fontstyle']!r}, expected {r['fontstyle']!r}.",
            sub="style")


CHECKS = {f.__name__: f for f in (
    tile_area, tile_geometry, tile_style, subgroup_containment, text_fits_tile, label_style,
)}


# ───────────────────────────── case-level checks ─────────────────────────────

def tiles_no_overlap(rc, cc, pairs, P, add, expect):
    """Verify zero pairwise tile overlap and full treemap canvas area conservation per panel."""
    panels = sorted({c["panel"] for c in rc if c["kind"] == "tile"})
    if not panels:
        return False
    tol = P["tol"]
    for pi in panels:
        r_tiles = [t for t in rc if t["panel"] == pi and t["kind"] == "tile"]
        c_tiles = [t for t in cc if t["panel"] == pi and t["kind"] == "tile"]
        if not c_tiles:
            continue
        pw, ph = r_tiles[0]["panel_w_pt"], r_tiles[0]["panel_h_pt"]
        worst_ov = 0.0
        overlap_pairs = 0
        for a_i in range(len(c_tiles)):
            a = c_tiles[a_i]
            for b_i in range(a_i + 1, len(c_tiles)):
                b = c_tiles[b_i]
                iw = (min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])) * pw
                ih = (min(a["y1"], b["y1"]) - max(a["y0"], b["y0"])) * ph
                if iw > tol and ih > tol:
                    overlap_pairs += 1
                    worst_ov = max(worst_ov, min(iw, ih))
        if overlap_pairs > 0:
            add("tiles_no_overlap", 0, overlap_pairs,
                f"Panel {pi}: {overlap_pairs} overlapping tile pair(s) (worst overlap {worst_ov:.1f}pt).",
                delta=round(worst_ov, 2), sub=f"p{pi}_overlap")

        r_sum = sum(t["area"] for t in r_tiles)
        c_sum = sum(t["area"] for t in c_tiles)
        area_gap = abs(c_sum - r_sum)
        if exceeds(area_gap, 0.025, "tiles_no_overlap"):
            add("tiles_no_overlap", round(r_sum, 4), round(c_sum, 4),
                f"Panel {pi}: total tile area is {c_sum:.4f}, expected {r_sum:.4f} (canvas coverage gap/excess).",
                delta=round(c_sum - r_sum, 4), sub=f"p{pi}_coverage")


CASE_CHECKS = {"tiles_no_overlap": tiles_no_overlap}
