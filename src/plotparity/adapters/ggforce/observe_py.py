"""ggforce adapter: Python-side panel & anchor-point observer via core panel_geometry.

Mark enclosure polygons, callout text/boxes, and connector polylines are captured directly
by probe.py's draw calls (including native linetype and sampled_vertices_norm_tl).
"""

from __future__ import annotations

from plotparity.engines.python.panel_geometry import extract_panel_geometry


def observe(fig, plot) -> dict:
    pg = extract_panel_geometry(fig, plot, kinds=("points",))
    return {
        "ggforce": {
            "panels": [
                {
                    "panel_index": p["panel_index"],
                    "bbox_norm_tl": p["bbox_norm_tl"],
                    "points": p["points"],
                }
                for p in pg["panels"]
            ]
        }
    }
