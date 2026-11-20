"""Shared multi-panel Python geometry extractor (counterpart of engines/r/panel_geometry.R).

Opt-in helper for geom adapters (`observe_py.py`):
    from plotparity.engines.python.panel_geometry import extract_panel_geometry
Extracts per-panel ranges, discrete labels (including plotnine `scale_x_ordinal`), `coord_flip` orientation,
`bbox_norm_tl`, points (`PathCollection`), polygons/lines/segments (`PolyCollection`, `LineCollection`, `Line2D`,
`Patch`), rasters (`AxesImage`), and `extracted_classes` for `account_for_drawing()`.
Only plotnine/matplotlib/numpy are imported here (safe in any candidate interpreter).
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np
from matplotlib.colors import to_hex, to_rgba

_SKIP_SHAPES = ("Spine", "XAxis", "YAxis", "Text", "Legend", "Annotation", "AxesImage", "PathCollection")


def _col(rgba) -> tuple[str, float]:
    r = to_rgba(rgba)
    return ("none", 0.0) if r[3] == 0 else (to_hex(r[:3]), round(float(r[3]), 4))


def _is_discrete_scale(scale) -> bool:
    if scale is None:
        return False
    try:
        from plotnine.scales.scale_discrete import scale_discrete
        if isinstance(scale, scale_discrete):
            return True
    except Exception:
        pass
    return hasattr(scale, "is_discrete") and bool(scale.is_discrete())


def _pieces(paths, transform, ax, fig_w: float, fig_h: float) -> list[dict]:
    to_data = ax.transData.inverted()
    out = []
    for path in paths:
        for poly in path.to_polygons(closed_only=False):
            if len(poly) < 2 or (np.ptp(poly[:, 0]) == 0 and np.ptp(poly[:, 1]) == 0):
                continue
            disp = transform.transform(poly)
            data = to_data.transform(disp)
            norm = np.column_stack([disp[:, 0] / fig_w, 1.0 - disp[:, 1] / fig_h])
            out.append({"data": np.round(data, 6).tolist(), "norm": np.round(norm, 6).tolist()})
    return out


def extract_panel_geometry(fig, plot, kinds: Iterable[str] = ("points", "shapes", "rasters")) -> dict:
    kinds_set = set(kinds)
    fig_w, fig_h = fig.canvas.get_width_height()
    layout = getattr(plot, "layout", None)
    panel_params = getattr(layout, "panel_params", []) if layout is not None else []
    is_flip = "flip" in type(getattr(plot, "coordinates", None)).__name__.lower()

    panels = []
    extracted_classes: set[str] = set()

    for pi, ax in enumerate(fig.axes):
        bb = ax.bbox
        bbox_norm = [round(float(bb.x0) / fig_w, 6), round(1.0 - float(bb.y1) / fig_h, 6),
                     round(float(bb.x1) / fig_w, 6), round(1.0 - float(bb.y0) / fig_h, 6)]
        if pi < len(panel_params):
            pp = panel_params[pi]
            xr = [float(v) for v in pp.x.range]
            yr = [float(v) for v in pp.y.range]
            row = layout.layout.iloc[pi]
            sc_x = layout.panel_scales_x[int(row["SCALE_X"]) - 1] if layout.panel_scales_x else None
            sc_y = layout.panel_scales_y[int(row["SCALE_Y"]) - 1] if layout.panel_scales_y else None
            x_disc, y_disc = _is_discrete_scale(sc_x), _is_discrete_scale(sc_y)
            if is_flip:
                x_disc, y_disc = y_disc, x_disc
            x_labs = [str(v) for v in (getattr(pp.x, "labels", []) or [])] if x_disc else []
            y_labs = [str(v) for v in (getattr(pp.y, "labels", []) or [])] if y_disc else []
        else:
            xr, yr = [float(v) for v in ax.get_xlim()], [float(v) for v in ax.get_ylim()]
            x_disc = y_disc = False
            x_labs, y_labs = [], []

        pts, shapes, rasters = [], [], []
        paint = 0
        for a in sorted(ax.get_children(), key=lambda x: x.get_zorder()):
            if not a.get_visible() or a is ax.patch:
                continue
            cls = type(a)
            name = cls.__name__
            fqn = f"{cls.__module__}.{cls.__qualname__}"

            if "points" in kinds_set and name == "PathCollection":
                extracted_classes.add(fqn)
                paint += 1
                offsets = np.asarray(a.get_offsets(), float)
                if offsets.ndim != 2 or len(offsets) == 0:
                    continue
                disp = a.get_offset_transform().transform(offsets)
                data = ax.transData.inverted().transform(disp)
                fcs = np.atleast_2d(a.get_facecolor())
                ecs = np.atleast_2d(a.get_edgecolor())
                szs = np.atleast_1d(a.get_sizes())
                for k in range(len(offsets)):
                    if not (np.isfinite(data[k, 0]) and np.isfinite(data[k, 1])):
                        continue
                    fc, fa = _col(fcs[k % len(fcs)]) if len(fcs) else ("none", 0.0)
                    ec, ea = _col(ecs[k % len(ecs)]) if len(ecs) else ("none", 0.0)
                    eff_hex = fc if fc != "none" else ec
                    eff_alpha = fa if fc != "none" else ea
                    area = float(szs[k % len(szs)]) if len(szs) else 12.566
                    pts.append({
                        "x": round(float(data[k, 0]), 6),
                        "y": round(float(data[k, 1]), 6),
                        "x_norm": round(float(disp[k, 0]) / fig_w, 6),
                        "y_norm": round(1.0 - float(disp[k, 1]) / fig_h, 6),
                        "color_hex": eff_hex,
                        "alpha": eff_alpha,
                        "size_pt": round(2.0 * math.sqrt(max(area, 0.0) / math.pi), 3),
                        "paint_index": paint,
                    })
            elif "rasters" in kinds_set and name == "AxesImage":
                extracted_classes.add(fqn)
                paint += 1
                ext = a.get_extent()
                p0 = ax.transData.transform((ext[0], ext[2]))
                p1 = ax.transData.transform((ext[1], ext[3]))
                rasters.append({
                    "paint_index": paint,
                    "bbox_norm_tl": [round(min(p0[0], p1[0]) / fig_w, 6), round(1.0 - max(p0[1], p1[1]) / fig_h, 6),
                                     round(max(p0[0], p1[0]) / fig_w, 6), round(1.0 - min(p0[1], p1[1]) / fig_h, 6)],
                    "bbox_data": [float(min(ext[0], ext[1])), float(min(ext[2], ext[3])),
                                  float(max(ext[0], ext[1])), float(max(ext[2], ext[3]))],
                })
            elif "shapes" in kinds_set and name not in _SKIP_SHAPES:
                if name == "Line2D":
                    extracted_classes.add(fqn)
                    paint += 1
                    ec, ea = _col(a.get_color())
                    ea = round(ea * (a.get_alpha() if a.get_alpha() is not None else 1.0), 4)
                    pcs = _pieces([a.get_path()], a.get_transform(), ax, fig_w, fig_h)
                    if pcs:
                        shapes.append({"kind": "polyline", "paint_index": paint, "filled": False, "is_rounded": False,
                                       "facecolor_hex": "none", "face_alpha": 0.0,
                                       "edgecolor_hex": ec, "edge_alpha": ea,
                                       "linewidth_pt": float(a.get_linewidth()), "pieces": pcs})
                elif hasattr(a, "get_paths") or hasattr(a, "get_path"):
                    extracted_classes.add(fqn)
                    paths = a.get_paths() if hasattr(a, "get_paths") else [a.get_path()]
                    fcs = np.atleast_2d(a.get_facecolor()) if hasattr(a, "get_facecolor") else np.zeros((1, 4))
                    ecs = np.atleast_2d(a.get_edgecolor()) if hasattr(a, "get_edgecolor") else np.zeros((1, 4))
                    lws = np.atleast_1d(a.get_linewidth()) if hasattr(a, "get_linewidth") else np.zeros(1)
                    transform = a.get_transform()
                    for k, path in enumerate(paths):
                        paint += 1
                        fc, fa = _col(fcs[k % len(fcs)]) if len(fcs) else ("none", 0.0)
                        ec, ea = _col(ecs[k % len(ecs)]) if len(ecs) else ("none", 0.0)
                        lw = float(lws[k % len(lws)]) if len(lws) else 0.0
                        if lw == 0:
                            ec, ea = "none", 0.0
                        pcs = _pieces([path], transform, ax, fig_w, fig_h)
                        if pcs:
                            shapes.append({"kind": "polygon" if fc != "none" else "polyline",
                                           "paint_index": paint, "filled": fc != "none", "is_rounded": False,
                                           "facecolor_hex": fc, "face_alpha": fa,
                                           "edgecolor_hex": ec, "edge_alpha": ea,
                                           "linewidth_pt": lw if ec != "none" else 0.0, "pieces": pcs})

        panels.append({
            "panel_index": pi + 1,
            "x_range": xr,
            "y_range": yr,
            "x_labels": x_labs,
            "y_labels": y_labs,
            "x_is_discrete": x_disc,
            "y_is_discrete": y_disc,
            "bbox_norm_tl": bbox_norm,
            "points": pts,
            "shapes": shapes,
            "rasters": rasters,
        })

    return {"panels": panels, "extracted_classes": sorted(extracted_classes)}
