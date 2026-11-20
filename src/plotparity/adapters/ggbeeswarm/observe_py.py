"""ggbeeswarm adapter: Python-side multi-panel point extractor via core panel_geometry."""

from __future__ import annotations

from plotparity.engines.python.panel_geometry import extract_panel_geometry


def observe(fig, plot) -> dict:
    return {"ggbeeswarm": extract_panel_geometry(fig, plot, kinds=("points",))}
