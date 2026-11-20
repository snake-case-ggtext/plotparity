"""geomtextpath adapter: Python-side panel geometry & layer label metadata observer.

Text glyphs, curve polylines (with native `linetype`/`dashes`), and label boxes are
captured directly by core `probe.py`'s draw calls.
"""

from __future__ import annotations

from plotparity.engines.python.panel_geometry import extract_panel_geometry


def observe(fig, plot) -> dict:
    pg = extract_panel_geometry(fig, plot, kinds=())
    panels = [{"panel_index": p["panel_index"], "bbox_norm_tl": p["bbox_norm_tl"]} for p in pg["panels"]]

    # Capture built layer group labels per panel as fallback metadata for single-artist ports
    layer_labels: list[dict] = []
    try:
        for ly in getattr(plot, "layers", []):
            d = getattr(ly, "data", None)
            if d is None or "label" not in d.columns or "PANEL" not in d.columns or "group" not in d.columns:
                continue
            for pi, gdf in d.groupby("PANEL", sort=True):
                for grp, sub in gdf.groupby("group", sort=False):
                    lbl = str(sub["label"].iloc[0])
                    layer_labels.append({"panel": int(pi), "group": int(grp), "label": lbl})
    except Exception:
        layer_labels = []

    return {"geomtextpath": {"panels": panels, "layer_labels": layer_labels}}
