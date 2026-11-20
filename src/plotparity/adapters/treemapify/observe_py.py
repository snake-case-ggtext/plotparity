"""treemapify adapter: Python-side observation hook.

Uses shared `extract_panel_geometry` to capture per-panel bounding boxes and
any matplotlib Patch/PolyCollection shapes (tiles and subgroup borders).
Text labels (including draw-time fitted text artists) and any custom-artist
rectangles are also read directly from `probe.py`'s draw events in `adapter.py`.
"""

from __future__ import annotations

from plotparity.engines.python.panel_geometry import extract_panel_geometry


def observe(fig, plot) -> dict:
    pg = extract_panel_geometry(fig, plot, kinds=("shapes",))
    return {"treemapify": pg}
