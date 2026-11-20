"""ggridges adapter: Python-side observations, the counterpart of observe.R (runs in the package's interpreter).

observe(fig, plot) -> {"ggridges": {...}} with the same shape as the R stream:
    panel_x_range / panel_y_range / y_labels, events (panel polygons and lines in draw order, data units),
    build (the first layer's built data per ridge). plotnine builds in place on draw, so after `plot.draw()` the
    drawn data is `plot.layers[0].data` and the trained ranges are `plot.layout.panel_params[0]`.
Only plotnine/matplotlib/numpy are imported here.
"""

from __future__ import annotations

import numpy as np
from matplotlib.colors import to_hex, to_rgba

# PathCollection = point markers (geom_point, jittered points): not ridge shapes, and R's walker skips points too.
_SKIP = ("Spine", "XAxis", "YAxis", "Text", "Legend", "Annotation", "AxesImage", "PathCollection")


def _col(rgba) -> tuple[str, float]:
    r = to_rgba(rgba)
    return ("none", 0.0) if r[3] == 0 else (to_hex(r[:3]), round(float(r[3]), 4))


def _pieces(paths, transform, ax, fig_w, fig_h):
    to_data = ax.transData.inverted()
    out = []
    for path in paths:
        for poly in path.to_polygons(closed_only=False):
            if len(poly) < 2 or (np.ptp(poly[:, 0]) == 0 and np.ptp(poly[:, 1]) == 0):
                continue
            disp = transform.transform(poly)
            data = to_data.transform(disp)
            norm = np.column_stack([disp[:, 0] / fig_w, 1 - disp[:, 1] / fig_h])
            out.append({"data": np.round(data, 9).tolist(), "norm": np.round(norm, 6).tolist()})
    return out


def observe(fig, plot) -> dict:
    ax = fig.axes[0]
    fig_w, fig_h = fig.canvas.get_width_height()
    pp = plot.layout.panel_params[0]
    events, paint = [], 0
    extracted_classes: set[str] = set()
    artists = [a for a in ax.get_children() if a.get_visible() and type(a).__name__ not in _SKIP
               and a is not ax.patch]
    for a in sorted(artists, key=lambda a: a.get_zorder()):      # matplotlib draws by zorder, stably
        cls = type(a)
        name = cls.__name__
        if name == "Line2D":
            extracted_classes.add(f"{cls.__module__}.{cls.__qualname__}")
            paint += 1
            ec, ea = _col(a.get_color())
            ea = round(ea * (a.get_alpha() if a.get_alpha() is not None else 1.0), 4)
            events.append({"paint_index": paint, "filled": False, "facecolor_hex": "none", "face_alpha": 0.0,
                           "edgecolor_hex": ec, "edge_alpha": ea, "linewidth_pt": float(a.get_linewidth()),
                           "pieces": _pieces([a.get_path()], a.get_transform(), ax, fig_w, fig_h)})
        elif hasattr(a, "get_paths") or hasattr(a, "get_path"):
            extracted_classes.add(f"{cls.__module__}.{cls.__qualname__}")
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
                events.append({"paint_index": paint, "filled": fc != "none", "facecolor_hex": fc, "face_alpha": fa,
                               "edgecolor_hex": ec, "edge_alpha": ea, "linewidth_pt": lw if ec != "none" else 0.0,
                               "pieces": _pieces([path], transform, ax, fig_w, fig_h)})
    build = []
    d = next((ly.data for ly in plot.layers if ly.data is not None and ({"density", "height"} & set(ly.data.columns))),
             plot.layers[0].data if plot.layers else None)
    if d is not None and {"x", "y"} <= set(d.columns):
        if "datatype" in d.columns:
            d = d[d["datatype"] == "ridgeline"]
        for pos in sorted(set(np.round(d["y"].astype(float)).astype(int))):
            s = d[np.round(d["y"].astype(float)).astype(int) == pos].sort_values("x")
            build.append({"pos": int(pos), "x": s["x"].astype(float).tolist(),
                          "density": s["density"].astype(float).tolist() if "density" in s.columns else None})
    labels = list(getattr(pp.y, "labels", []) or [])
    return {"ggridges": {"panel_x_range": [float(v) for v in pp.x.range],
                         "panel_y_range": [float(v) for v in pp.y.range],
                         "y_labels": [str(v) for v in labels], "events": events, "build": build,
                         "extracted_classes": sorted(extracted_classes)}}
