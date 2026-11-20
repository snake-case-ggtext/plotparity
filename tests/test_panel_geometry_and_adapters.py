"""Tests for shared panel_geometry, probe draw_path_collection decoding, off-check skipping, ggbeeswarm & ggrepel."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
from matplotlib.backends.backend_agg import FigureCanvasAgg
from plotnine import aes, coord_flip, facet_wrap, geom_point, geom_ribbon, ggplot

from plotparity.adapters.ggbeeswarm import adapter as BS
from plotparity.adapters.ggrepel import adapter as RP
from plotparity.compare import evaluate_case, load_profile
from plotparity.engines.python.panel_geometry import extract_panel_geometry
from plotparity.engines.python.probe import record


def test_panel_geometry_facets_ordinal_and_coord_flip():
    df = pd.DataFrame({
        "g": pd.Categorical(["Low", "High", "Low", "High"], categories=["Low", "High"], ordered=True),
        "y": [1.0, 2.0, 1.5, 2.5],
        "f": ["p1", "p1", "p2", "p2"],
    })
    p = ggplot(df, aes("g", "y")) + geom_point() + facet_wrap("~f")
    fig = p.draw()
    FigureCanvasAgg(fig)
    fig.canvas.draw()
    pg = extract_panel_geometry(fig, p)
    assert len(pg["panels"]) == 2
    assert pg["panels"][0]["x_is_discrete"] is True
    assert pg["panels"][0]["x_labels"] == ["Low", "High"]
    assert len(pg["panels"][0]["points"]) == 2
    assert len(pg["panels"][1]["points"]) == 2
    assert "matplotlib.collections.PathCollection" in pg["extracted_classes"]

    p_flip = ggplot(df[df["f"] == "p1"], aes("g", "y")) + geom_point() + coord_flip()
    fig2 = p_flip.draw()
    FigureCanvasAgg(fig2)
    fig2.canvas.draw()
    pg2 = extract_panel_geometry(fig2, p_flip)
    assert pg2["panels"][0]["y_is_discrete"] is True
    assert pg2["panels"][0]["x_is_discrete"] is False
    assert pg2["panels"][0]["y_labels"] == ["Low", "High"]


def test_panel_geometry_extracts_shapes():
    rib = pd.DataFrame({"x": [0.0, 1.0, 2.0], "lo": [0.0, 0.0, 0.0], "hi": [0.0, 1.0, 0.0]})
    p = ggplot(rib, aes("x", ymin="lo", ymax="hi")) + geom_ribbon(fill="#336699")
    fig = p.draw()
    FigureCanvasAgg(fig)
    fig.canvas.draw()
    pg = extract_panel_geometry(fig, p, kinds=("shapes",))
    shapes = pg["panels"][0]["shapes"]
    assert any(s["filled"] and s["facecolor_hex"] == "#336699" for s in shapes)


def test_probe_decodes_draw_path_collection():
    df = pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [1.0, 3.0, 2.0]})
    p = ggplot(df, aes("x", "y")) + geom_point(color="#cc0000", size=3)
    fig = p.draw()
    FigureCanvasAgg(fig)
    fig.canvas.draw()
    obs, _ = record(fig)
    pcs = [e for e in obs["events"] if e["kind"] == "draw_path_collection" and "marker_size_pt" in e]
    assert pcs
    ev = pcs[0]
    assert ev["n_offsets"] == 3
    assert len(ev["offsets_norm_tl"]) == 3
    assert ev["facecolors_hex"][0] == "#cc0000"
    assert ev["marker_size_pt"][0] > 0


def test_disabled_core_checks_do_not_crash_without_tol():
    """ggrepel sets rotation/fill_color/edge_color severity='off' without `tol`, even though components carry
    `angle_deg` and `rect`. evaluate_case must skip off checks instead of raising KeyError('tol')."""
    lbl = {
        "panel": 1, "idx": "1", "text": "Hello", "x_norm": 0.3, "y_norm": 0.3,
        "bbox_norm_tl": [0.25, 0.28, 0.35, 0.32], "color_hex": "#000000", "alpha": 1.0,
        "fontsize_pt": 11.0, "fontweight": "normal", "fontstyle": "normal", "angle_deg": 0.0,
        "rect": {"bbox_norm_tl": [0.24, 0.27, 0.36, 0.33], "is_rounded": True,
                 "facecolor_hex": "#ffffff", "face_alpha": 1.0, "edgecolor_hex": "#000000",
                 "edge_alpha": 1.0, "linewidth_pt": 0.5},
        "segment": None,
    }
    panel = {"panel_index": 1, "bbox_norm_tl": [0.1, 0.1, 0.9, 0.9],
             "points": [{"x_norm": 0.2, "y_norm": 0.2, "size_pt": 4.0}]}
    ref_obs = {"ggrepel": {"panels": [panel], "labels": [lbl]}}
    cand_obs = {
        "ggrepel": {"panels": [panel]},
        "owners": [{"class": "plotnine_ggrepel._artist._RepelArtist", "module": "plotnine_ggrepel._artist", "parent": "axes"}],
        "events": [
            {"kind": "path", "paint_index": 1, "owner": 0, "is_closed": True, "is_rounded": True,
             "bbox_norm_tl": [0.24, 0.27, 0.36, 0.33], "vertices_norm_tl": [[0.24, 0.27], [0.36, 0.27], [0.36, 0.33], [0.24, 0.33]],
             "facecolor_hex": "#ffffff", "face_alpha": 1.0, "edgecolor_hex": "#000000", "edge_alpha": 1.0, "linewidth_pt": 0.5},
            {"kind": "text", "paint_index": 2, "owner": 0, "text": "Hello", "x_norm": 0.25, "y_norm": 0.31,
             "bbox_norm_tl": [0.25, 0.28, 0.35, 0.32], "color_hex": "#000000", "alpha": 1.0,
             "fontsize_pt": 11.0, "fontweight": "normal", "fontstyle": "normal", "angle_deg": 0.0},
        ],
        "port_ink_px": 100,
    }
    case = SimpleNamespace(id="repel_clean", relpath="repel_clean", expect={}, measurable=True, why="")
    res = evaluate_case(case, RP, load_profile(RP, {}), [], {"observations": ref_obs}, {"observations": cand_obs},
                        package="plotnine_ggrepel")
    assert res["status"] == "pass", res["findings"]


def test_ggrepel_invariants_catch_label_overlap_and_outside_panel():
    p_bb = [0.1, 0.1, 0.9, 0.9]
    panel = {"panel_index": 1, "bbox_norm_tl": p_bb, "points": [{"x_norm": 0.5, "y_norm": 0.5, "size_pt": 4.0}]}
    mk_r = lambda txt, x0: {"panel": 1, "idx": txt, "text": txt, "x_norm": x0, "y_norm": 0.3,
                            "bbox_norm_tl": [x0, 0.28, x0 + 0.08, 0.32], "color_hex": "#000000", "alpha": 1.0,
                            "fontsize_pt": 11.0, "fontweight": "normal", "fontstyle": "normal", "angle_deg": 0.0,
                            "rect": None, "segment": None}
    ref_obs = {"ggrepel": {"panels": [panel], "labels": [mk_r("A", 0.2), mk_r("B", 0.6)]}}
    # Candidate puts A and B overlapping AND A extending outside the left edge of the panel
    cand_obs = {
        "ggrepel": {"panels": [panel]},
        "owners": [{"class": "plotnine_ggrepel._artist._RepelArtist", "module": "plotnine_ggrepel._artist", "parent": "axes"}],
        "events": [
            {"kind": "text", "paint_index": 1, "owner": 0, "text": "A", "x_norm": 0.05, "y_norm": 0.3,
             "bbox_norm_tl": [0.05, 0.28, 0.15, 0.32], "color_hex": "#000000", "alpha": 1.0,
             "fontsize_pt": 11.0, "fontweight": "normal", "fontstyle": "normal", "angle_deg": 0.0},
            {"kind": "text", "paint_index": 2, "owner": 0, "text": "B", "x_norm": 0.10, "y_norm": 0.3,
             "bbox_norm_tl": [0.10, 0.28, 0.20, 0.32], "color_hex": "#000000", "alpha": 1.0,
             "fontsize_pt": 11.0, "fontweight": "normal", "fontstyle": "normal", "angle_deg": 0.0},
        ],
        "port_ink_px": 50,
    }
    case = SimpleNamespace(id="repel_bad", relpath="repel_bad", expect={}, measurable=True, why="")
    res = evaluate_case(case, RP, load_profile(RP, {}), [], {"observations": ref_obs}, {"observations": cand_obs},
                        package="plotnine_ggrepel")
    checks = {f["check"] for f in res["findings"] if f["status"] == "fail"}
    assert "label_no_overlap" in checks and "label_inside_panel" in checks


def test_ggbeeswarm_adapter_catches_overlap_and_shifted_values():
    def obs_with(xs, ys):
        pts = [{"x": x, "y": y, "x_norm": 0.2 + x * 0.2, "y_norm": 0.8 - y * 0.2,
                "color_hex": "#000000", "alpha": 1.0, "size_pt": 4.0, "paint_index": 1} for x, y in zip(xs, ys)]
        return {
            "ggbeeswarm": {"panels": [{"panel_index": 1, "x_range": [0.4, 2.6], "y_range": [0.0, 3.0],
                                       "x_labels": ["A"], "y_labels": [], "x_is_discrete": True, "y_is_discrete": False,
                                       "bbox_norm_tl": [0.1, 0.1, 0.9, 0.9], "points": pts}]},
            "owners": [], "events": [], "port_ink_px": 20,
        }
    ref = obs_with([0.95, 1.0, 1.05, 1.10], [1.0, 1.0, 1.0, 1.0])
    cand_bad = obs_with([1.0, 1.0, 1.0, 1.0], [1.5, 1.5, 1.5, 1.5])     # piled on top AND shifted y
    case = SimpleNamespace(id="bs_bad", relpath="bs_bad", expect={}, measurable=True, why="")
    res = evaluate_case(case, BS, load_profile(BS, {}), [], {"observations": ref}, {"observations": cand_bad},
                        package="plotnine_ggbeeswarm")
    checks = {f["check"] for f in res["findings"] if f["status"] == "fail"}
    assert "value_axis" in checks and "point_overlap" in checks
