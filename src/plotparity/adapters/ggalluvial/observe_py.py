"""ggalluvial adapter: Python-side extractor using core panel_geometry + panel text/label annotations."""

from __future__ import annotations

import numpy as np
from matplotlib.colors import to_hex, to_rgba
from matplotlib.text import Text

from plotparity.engines.python.panel_geometry import extract_panel_geometry


def _col(rgba) -> tuple[str, float]:
    if rgba is None:
        return ("none", 0.0)
    try:
        r = to_rgba(rgba)
    except Exception:
        return ("none", 0.0)
    return ("none", 0.0) if r[3] == 0 else (to_hex(r[:3]), round(float(r[3]), 4))


def observe(fig, plot) -> dict:
    pg = extract_panel_geometry(fig, plot, kinds=("shapes",))
    fig_w, fig_h = fig.canvas.get_width_height()
    is_flip = "flip" in type(getattr(plot, "coordinates", None)).__name__.lower()
    extracted = set(pg.get("extracted_classes") or [])

    for pi, ax in enumerate(fig.axes):
        if pi >= len(pg["panels"]):
            continue
        panel = pg["panels"][pi]
        panel["is_flip"] = is_flip

        skip_texts = {ax.title, getattr(ax, "_left_title", None), getattr(ax, "_right_title", None)}
        labels = []
        paint_base = len(panel["shapes"])
        for a in sorted(ax.get_children(), key=lambda x: x.get_zorder()):
            if not a.get_visible() or not isinstance(a, Text) or a in skip_texts:
                continue
            txt = str(a.get_text()).strip()
            if not txt:
                continue
            extracted.add(f"{type(a).__module__}.{type(a).__qualname__}")
            pos = a.get_position()
            disp = a.get_transform().transform(pos)
            data = ax.transData.inverted().transform(disp)
            if not (np.isfinite(data[0]) and np.isfinite(data[1])):
                continue
            tc, ta = _col(a.get_color())
            eff_a = round(ta * (a.get_alpha() if a.get_alpha() is not None else 1.0), 4)
            paint_base += 1
            rec = {
                "text": txt,
                "x": round(float(data[0]), 6),
                "y": round(float(data[1]), 6),
                "x_norm": round(float(disp[0]) / fig_w, 6),
                "y_norm": round(1.0 - float(disp[1]) / fig_h, 6),
                "color_hex": "#000000" if tc == "none" else tc,
                "alpha": eff_a,
                "fontsize_pt": round(float(a.get_fontsize()), 3),
                "paint_index": paint_base,
                "has_box": False,
            }
            bp = a.get_bbox_patch()
            if bp is not None:
                rec["has_box"] = True
                bfc, bfa = _col(bp.get_facecolor())
                bec, bea = _col(bp.get_edgecolor())
                rec["box_fill_hex"] = bfc
                rec["box_face_alpha"] = bfa
                rec["box_edge_hex"] = bec
                rec["box_edge_alpha"] = bea
            labels.append(rec)
        panel["labels"] = labels

    pg["extracted_classes"] = sorted(extracted)
    return {"ggalluvial": pg}
