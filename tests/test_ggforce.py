"""Unit tests for the ggforce mark-geoms plotparity adapter."""

from __future__ import annotations

import math
from types import SimpleNamespace

from plotparity.adapters.ggforce import adapter as GGF
from plotparity.compare import evaluate_case, load_profile


def _circle_verts(cx: float, cy: float, rx: float, ry: float, n: int = 36) -> list[list[float]]:
    return [
        [round(cx + rx * math.cos(2 * math.pi * i / n), 5), round(cy + ry * math.sin(2 * math.pi * i / n), 5)]
        for i in range(n)
    ]


def _rect_verts(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _bbox_of(verts: list[list[float]]) -> list[float]:
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    return [min(xs), min(ys), max(xs), max(ys)]


def _make_pair(
    *,
    cand_verts: list[list[float]] | None = None,
    cand_is_rounded: bool = True,
    cand_fill: str = "#f8766d",
    cand_face_alpha: float = 0.3,
    cand_edge: str = "#000000",
    cand_callout_bb: list[float] | None = None,
    cand_title: str = "Alpha",
    cand_title_weight: str = "bold",
    cand_desc: str | None = "First cluster summary text",
    cand_box_fill: str = "#ffffff",
    cand_box_alpha: float = 1.0,
    with_connector: bool = True,
    cand_with_connector: bool = True,
    cand_con_pts: list[list[float]] | None = None,
    cand_con_color: str = "#000000",
    cand_con_linetype: str = "solid",
    drop_cand_mark: bool = False,
    second_callout_overlap: bool = False,
):
    panel = {
        "panel_index": 1,
        "bbox_norm_tl": [0.10, 0.10, 0.90, 0.90],
        "points": [
            {"x_norm": 0.25, "y_norm": 0.65, "size_pt": 4.0},
            {"x_norm": 0.29, "y_norm": 0.68, "size_pt": 4.0},
            {"x_norm": 0.27, "y_norm": 0.62, "size_pt": 4.0},
            # External point far away in upper-right
            {"x_norm": 0.72, "y_norm": 0.28, "size_pt": 4.0},
        ],
    }

    r_verts = _circle_verts(0.27, 0.65, 0.06, 0.08, n=36)
    r_bb = _bbox_of(r_verts)
    r_co_bb = [0.45, 0.55, 0.62, 0.66]
    r_con_pts = [[0.62, 0.66], [0.45, 0.66], [0.39, 0.66], [0.34, 0.65]]

    r_mark = {
        "panel": 1,
        "polygon": {
            "geom_kind": "hull",
            "vertices_norm_tl": r_verts,
            "bbox_norm_tl": r_bb,
            "facecolor_hex": "#f8766d",
            "face_alpha": 0.3,
            "edgecolor_hex": "#000000",
            "edge_alpha": 1.0,
            "linewidth_pt": 1.067,
            "linetype": "solid",
        },
        "callout": {
            "bbox_norm_tl": r_co_bb,
            "title": {
                "text": "Alpha",
                "bbox_norm_tl": [0.46, 0.56, 0.52, 0.59],
                "color_hex": "#000000",
                "alpha": 1.0,
                "fontsize_pt": 12.0,
                "fontweight": "bold",
                "fontstyle": "normal",
            },
            "description": (
                {
                    "text": "First cluster\nsummary text",
                    "bbox_norm_tl": [0.46, 0.60, 0.61, 0.65],
                    "color_hex": "#000000",
                    "alpha": 1.0,
                    "fontsize_pt": 12.0,
                    "fontweight": "normal",
                    "fontstyle": "normal",
                }
                if cand_desc is not None
                else None
            ),
            "box": {
                "bbox_norm_tl": r_co_bb,
                "facecolor_hex": "#ffffff",
                "face_alpha": 1.0,
                "edgecolor_hex": "none",
                "edge_alpha": 0.0,
                "linewidth_pt": 0.0,
            },
        },
        "connector": (
            {
                "points_norm_tl": r_con_pts,
                "color_hex": "#000000",
                "alpha": 1.0,
                "linewidth_pt": 1.067,
                "linetype": "solid",
            }
            if with_connector
            else None
        ),
    }

    r_marks = [r_mark]
    if second_callout_overlap:
        r_verts2 = _circle_verts(0.72, 0.28, 0.06, 0.08, n=36)
        r_marks.append({
            "panel": 1,
            "polygon": {
                "geom_kind": "hull",
                "vertices_norm_tl": r_verts2,
                "bbox_norm_tl": _bbox_of(r_verts2),
                "facecolor_hex": "#00ba38",
                "face_alpha": 0.3,
                "edgecolor_hex": "#000000",
                "edge_alpha": 1.0,
                "linewidth_pt": 1.067,
                "linetype": "solid",
            },
            "callout": {
                "bbox_norm_tl": [0.45, 0.20, 0.60, 0.28],
                "title": {
                    "text": "Beta",
                    "bbox_norm_tl": [0.46, 0.22, 0.52, 0.25],
                    "color_hex": "#000000",
                    "alpha": 1.0,
                    "fontsize_pt": 12.0,
                    "fontweight": "bold",
                    "fontstyle": "normal",
                },
                "description": None,
                "box": {
                    "bbox_norm_tl": [0.45, 0.20, 0.60, 0.28],
                    "facecolor_hex": "#ffffff",
                    "face_alpha": 1.0,
                    "edgecolor_hex": "none",
                    "edge_alpha": 0.0,
                    "linewidth_pt": 0.0,
                },
            },
            "connector": {
                "points_norm_tl": [[0.45, 0.28], [0.60, 0.28], [0.65, 0.28]],
                "color_hex": "#000000",
                "alpha": 1.0,
                "linewidth_pt": 1.067,
                "linetype": "solid",
            },
        })

    ref_obs = {"ggforce": {"panels": [panel], "marks": r_marks}}

    # Build Python probe events
    events = []
    pi = 0

    def add_mark_events(verts, is_rounded, fill_hex, face_alpha, edge_hex, co_bb, title, t_weight, desc, b_fill, b_alpha, con_pts, con_col, con_lty):
        nonlocal pi
        pi += 1
        events.append({
            "kind": "path",
            "paint_index": pi,
            "owner": 0,
            "is_closed": True,
            "is_rounded": is_rounded,
            "bbox_norm_tl": _bbox_of(verts),
            "vertices_norm_tl": verts,
            "facecolor_hex": fill_hex,
            "face_alpha": face_alpha,
            "edgecolor_hex": edge_hex,
            "edge_alpha": 1.0,
            "linewidth_pt": 1.067,
            "linetype": "solid",
        })
        if b_fill != "none" and b_alpha > 0:
            pi += 1
            events.append({
                "kind": "path",
                "paint_index": pi,
                "owner": 0,
                "is_closed": True,
                "is_rounded": False,
                "bbox_norm_tl": co_bb,
                "vertices_norm_tl": _rect_verts(*co_bb),
                "facecolor_hex": b_fill,
                "face_alpha": b_alpha,
                "edgecolor_hex": "none",
                "edge_alpha": 0.0,
                "linewidth_pt": 0.0,
                "linetype": "blank",
            })
        pi += 1
        events.append({
            "kind": "text",
            "paint_index": pi,
            "owner": 0,
            "text": title,
            "x_norm": co_bb[0] + 0.01,
            "y_norm": co_bb[1] + 0.03,
            "bbox_norm_tl": [co_bb[0] + 0.01, co_bb[1] + 0.01, co_bb[0] + 0.07, co_bb[1] + 0.04],
            "color_hex": "#000000",
            "alpha": 1.0,
            "fontsize_pt": 12.0,
            "fontweight": t_weight,
            "fontstyle": "normal",
        })
        if desc:
            lines = desc.split("\n")
            for li, line in enumerate(lines):
                pi += 1
                y0 = co_bb[1] + 0.05 + li * 0.025
                events.append({
                    "kind": "text",
                    "paint_index": pi,
                    "owner": 0,
                    "text": line,
                    "x_norm": co_bb[0] + 0.01,
                    "y_norm": y0 + 0.02,
                    "bbox_norm_tl": [co_bb[0] + 0.01, y0, co_bb[2] - 0.01, y0 + 0.022],
                    "color_hex": "#000000",
                    "alpha": 1.0,
                    "fontsize_pt": 12.0,
                    "fontweight": "normal",
                    "fontstyle": "normal",
                })
        if con_pts is not None:
            pi += 1
            events.append({
                "kind": "path",
                "paint_index": pi,
                "owner": 0,
                "is_closed": False,
                "is_rounded": False,
                "bbox_norm_tl": _bbox_of(con_pts),
                "vertices_norm_tl": con_pts,
                "facecolor_hex": "none",
                "face_alpha": 0.0,
                "edgecolor_hex": con_col,
                "edge_alpha": 1.0,
                "linewidth_pt": 1.067,
                "linetype": con_lty,
            })

    if not drop_cand_mark:
        c_verts = cand_verts if cand_verts is not None else r_verts
        c_co_bb = cand_callout_bb if cand_callout_bb is not None else r_co_bb
        c_con = (cand_con_pts if cand_con_pts is not None else r_con_pts) if cand_with_connector else None
        add_mark_events(
            c_verts, cand_is_rounded, cand_fill, cand_face_alpha, cand_edge,
            c_co_bb, cand_title, cand_title_weight, cand_desc, cand_box_fill, cand_box_alpha,
            c_con, cand_con_color, cand_con_linetype,
        )

    if second_callout_overlap:
        r_verts2 = _circle_verts(0.72, 0.28, 0.06, 0.08, n=36)
        # Deliberately overlap the first callout at [0.45, 0.55, 0.62, 0.66]
        add_mark_events(
            r_verts2, True, "#00ba38", 0.3, "#000000",
            [0.50, 0.58, 0.66, 0.65], "Beta", "bold", None, "#ffffff", 1.0,
            [[0.50, 0.58], [0.66, 0.58], [0.68, 0.34]], "#000000", "solid",
        )

    cand_obs = {
        "ggforce": {"panels": [panel]},
        "owners": [{"class": "plotnine_ggforce.mark._MarkArtist", "module": "plotnine_ggforce.mark", "parent": "axes"}],
        "events": events,
    }
    return ref_obs, cand_obs


def _eval(ref_obs, cand_obs):
    profile = load_profile(GGF, {})
    case = SimpleNamespace(id="test_case", relpath="test_case", expect={}, measurable=True)
    return evaluate_case(
        case, GGF, profile, [],
        {"observations": ref_obs}, {"observations": cand_obs},
        package="plotnine_ggforce", canvas_pt=(640.0, 480.0),
    )


def test_clean_pass():
    ref, cand = _make_pair()
    res = _eval(ref, cand)
    assert res["status"] == "pass", res["findings"]


def test_catches_missing_mark():
    # Two marks in reference, only one drawn by candidate -> missing_component + component_count fail
    ref, cand = _make_pair(second_callout_overlap=True)
    # Remove second mark's events (keep only first mark's 5 events)
    cand["events"] = cand["events"][:5]
    res = _eval(ref, cand)
    assert res["status"] == "fail"
    checks = {f["check"] for f in res["findings"]}
    assert "missing_component" in checks and "component_count" in checks


def test_catches_wrong_mark_shape_and_sharp_corners():
    # Tiny sharp square that misses points and has wrong area & roundedness
    bad_rect = _rect_verts(0.24, 0.63, 0.27, 0.66)
    ref, cand = _make_pair(cand_verts=bad_rect, cand_is_rounded=False)
    res = _eval(ref, cand)
    assert res["status"] == "fail"
    checks = {f["check"] for f in res["findings"]}
    assert "mark_enclosure" in checks
    assert "mark_style" in checks


def test_catches_callout_point_collision():
    # Place callout directly over the external scatter point at (0.72, 0.28)
    ref, cand = _make_pair(
        cand_callout_bb=[0.68, 0.24, 0.84, 0.35],
        cand_con_pts=[[0.68, 0.35], [0.34, 0.65]],
    )
    res = _eval(ref, cand)
    assert res["status"] == "fail"
    checks = {f["check"] for f in res["findings"]}
    assert "callout_no_point_overlap" in checks


def test_catches_callout_outside_panel():
    # Extend callout well past right panel edge (0.90)
    ref, cand = _make_pair(
        cand_callout_bb=[0.84, 0.55, 0.98, 0.66],
        cand_con_pts=[[0.84, 0.66], [0.34, 0.65]],
    )
    res = _eval(ref, cand)
    assert res["status"] == "fail"
    checks = {f["check"] for f in res["findings"]}
    assert "callout_layout" in checks


def test_catches_callout_callout_overlap():
    ref, cand = _make_pair(second_callout_overlap=True)
    res = _eval(ref, cand)
    assert res["status"] == "fail"
    checks = {f["check"] for f in res["findings"]}
    assert "callout_no_overlap" in checks


def test_catches_disconnected_connector_and_linetype():
    # Connector ends in upper-right corner far from the mark at (0.27, 0.65)
    ref, cand = _make_pair(
        cand_con_pts=[[0.62, 0.66], [0.45, 0.66], [0.80, 0.20]],
        cand_con_linetype="dashed",
    )
    res = _eval(ref, cand)
    assert res["status"] == "fail"
    subs = {(f["check"], f.get("sub", "")) for f in res["findings"]}
    assert any(c == "connector_connects" for c, _ in subs)
    assert any("far end" in f["message"] for f in res["findings"])
    assert any("linetype" in f["message"] for f in res["findings"])


def test_catches_wrong_fill_and_alpha():
    ref, cand = _make_pair(cand_fill="#0000ff", cand_face_alpha=0.85)
    res = _eval(ref, cand)
    assert res["status"] == "fail"
    msgs = [f["message"] for f in res["findings"] if f["check"] == "mark_style"]
    assert any("Mark fill " in m for m in msgs)
    assert any("alpha" in m for m in msgs)


def test_catches_callout_style_and_missing_connector():
    ref, cand = _make_pair(
        cand_title_weight="normal",
        cand_box_fill="#fff8dc",
        cand_with_connector=False,
    )
    res = _eval(ref, cand)
    assert res["status"] == "fail"
    checks = {f["check"] for f in res["findings"]}
    assert "callout_style" in checks
    assert "connector_connects" in checks
