"""ggridges adapter: Python-side observations (uses core panel_geometry + plotnine's built layer density)."""

from __future__ import annotations

import numpy as np

from plotparity.engines.python.panel_geometry import extract_panel_geometry


def observe(fig, plot) -> dict:
    pg = extract_panel_geometry(fig, plot, kinds=("shapes",))
    p0 = pg["panels"][0]
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
    return {"ggridges": {
        "panel_x_range": p0["x_range"],
        "panel_y_range": p0["y_range"],
        "y_labels": p0["y_labels"],
        "events": p0["shapes"],
        "build": build,
        "extracted_classes": pg["extracted_classes"],
    }}
