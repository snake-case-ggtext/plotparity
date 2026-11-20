"""Unit tests for the ggalluvial plotparity adapter."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from plotparity.adapters.ggalluvial import adapter as GA
from plotparity.compare import evaluate_case, load_profile


def _rect_poly(x0: float, x1: float, y0: float, y1: float, flip: bool = False) -> dict:
    pts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
    if flip:
        pts = [[y, x] for x, y in pts]
    norm = [[0.1 + 0.3 * p[0], 0.9 - 0.01 * p[1]] for p in pts]
    return {"data": pts, "norm": norm}


def _curve_poly(x0: float, x1: float, yb0: float, yt0: float, yb1: float, yt1: float,
                kind: str = "cubic", n: int = 33, flip: bool = False) -> dict:
    t = np.linspace(0.0, 1.0, n)
    xs = x0 + (x1 - x0) * t
    if kind == "linear":
        f = t
    elif kind == "cubic":
        f = 3 * t**2 - 2 * t**3
    elif kind == "sigmoid":
        s = 1.0 / (1.0 + np.exp(-6.0 * (2 * t - 1)))
        f = (s - s[0]) / (s[-1] - s[0])
    else:
        raise ValueError(kind)
    top_y = yt0 + (yt1 - yt0) * f
    bot_y = yb0 + (yb1 - yb0) * f
    pts_top = [[float(x), float(y)] for x, y in zip(xs, top_y)]
    pts_bot = [[float(x), float(y)] for x, y in zip(xs[::-1], bot_y[::-1])]
    pts = pts_top + pts_bot + [pts_top[0]]
    if flip:
        pts = [[y, x] for x, y in pts]
    norm = [[0.1 + 0.3 * p[0], 0.9 - 0.01 * p[1]] for p in pts]
    return {"data": pts, "norm": norm}


def _make_obs(
    *,
    curve_kind: str = "cubic",
    flow_fill: str = "#f8766d",
    flow_alpha: float = 0.5,
    stratum_shift_y: float = 0.0,
    drop_stratum: bool = False,
    label_shift_y: float = 0.0,
    label_has_box: bool = False,
    strata_on_top: bool = True,
    flip: bool = False,
    is_cand: bool = False,
) -> dict:
    flow_paint = 1 if strata_on_top else 10
    stratum_paint = 5 if strata_on_top else 1

    # Two axes (x=1, x=2), total weight 40:
    # Axis 1: B=[0, 15], A=[15, 40]
    # Axis 2: Y=[0, 25], X=[25, 40]
    # Two flows: (B->X: [0,15]->[25,40]) and (A->Y: [15,40]->[0,25])
    shapes = [
        {
            "kind": "polygon", "paint_index": flow_paint, "filled": True,
            "facecolor_hex": flow_fill, "face_alpha": flow_alpha,
            "edgecolor_hex": "none", "edge_alpha": 0.0, "linewidth_pt": 0.0,
            "pieces": [_curve_poly(1 + 1/6, 2 - 1/6, 0.0, 15.0, 25.0, 40.0, kind=curve_kind, flip=flip)],
        },
        {
            "kind": "polygon", "paint_index": flow_paint + 1, "filled": True,
            "facecolor_hex": "#00bfc4", "face_alpha": flow_alpha,
            "edgecolor_hex": "none", "edge_alpha": 0.0, "linewidth_pt": 0.0,
            "pieces": [_curve_poly(1 + 1/6, 2 - 1/6, 15.0, 40.0, 0.0, 25.0, kind=curve_kind, flip=flip)],
        },
    ]

    strata_specs = [
        (1 - 1/6, 1 + 1/6, 0.0, 15.0),
        (1 - 1/6, 1 + 1/6, 15.0 + stratum_shift_y, 40.0 + stratum_shift_y),
        (2 - 1/6, 2 + 1/6, 0.0, 25.0),
        (2 - 1/6, 2 + 1/6, 25.0, 40.0),
    ]
    if drop_stratum:
        strata_specs = strata_specs[:-1]
    for k, (x0, x1, y0, y1) in enumerate(strata_specs):
        shapes.append({
            "kind": "polygon", "paint_index": stratum_paint + k, "filled": True,
            "facecolor_hex": "#ffffff", "face_alpha": 1.0,
            "edgecolor_hex": "#000000", "edge_alpha": 1.0, "linewidth_pt": 0.75,
            "pieces": [_rect_poly(x0, x1, y0, y1, flip=flip)],
        })

    label_specs = [
        ("B", 1.0, 7.5 + label_shift_y),
        ("A", 1.0, 27.5),
        ("Y", 2.0, 12.5),
        ("X", 2.0, 32.5),
    ]
    labels = []
    for k, (txt, lx, ly) in enumerate(label_specs):
        x, y = (ly, lx) if flip else (lx, ly)
        rec = {
            "text": txt, "x": x, "y": y,
            "x_norm": 0.5, "y_norm": 0.5,
            "color_hex": "#000000", "alpha": 1.0, "fontsize_pt": 11.0,
            "paint_index": 20 + k, "has_box": label_has_box,
        }
        if label_has_box:
            rec.update(box_fill_hex="#ffffff", box_face_alpha=1.0, box_edge_hex="#000000", box_edge_alpha=1.0)
        labels.append(rec)

    panel = {
        "panel_index": 1,
        "is_flip": flip,
        "x_range": [-2.0, 42.0] if flip else [0.5, 2.5],
        "y_range": [0.5, 2.5] if flip else [-2.0, 42.0],
        "x_labels": [], "y_labels": [],
        "x_is_discrete": False, "y_is_discrete": False,
        "bbox_norm_tl": [0.1, 0.1, 0.9, 0.9],
        "shapes": shapes,
        "labels": labels,
    }
    obs: dict = {"ggalluvial": {"panels": [panel], "extracted_classes": ["matplotlib.collections.PolyCollection"]}}
    if is_cand:
        obs["owners"] = [{"class": "matplotlib.collections.PolyCollection", "module": "matplotlib.collections", "parent": "axes"}]
        obs["events"] = [{"kind": "path", "paint_index": 1, "owner": 0}]
        obs["port_ink_px"] = 200
    return obs


def _eval(ref_obs: dict, cand_obs: dict) -> dict:
    case = SimpleNamespace(id="test_alluvial", relpath="test_alluvial", expect={}, measurable=True, why="")
    return evaluate_case(case, GA, load_profile(GA, {}), [], {"observations": ref_obs}, {"observations": cand_obs},
                         package="plotnine_ggalluvial")


def test_profile_loads_cleanly():
    profile = load_profile(GA, {})
    assert profile["flow_trajectory"]["severity"] == "gating"
    assert profile["stratum_geometry"]["severity"] == "gating"
    assert profile["height_conservation"]["severity"] == "gating"


def test_clean_pass_vertical_and_coord_flip():
    for flip in (False, True):
        res = _eval(_make_obs(flip=flip), _make_obs(flip=flip, is_cand=True))
        assert res["status"] == "pass", f"flip={flip}: {res['findings']}"
        assert len(res["findings"]) == 0


def test_catches_broken_curve_trajectory():
    res = _eval(_make_obs(curve_kind="cubic"), _make_obs(curve_kind="linear", is_cand=True))
    assert res["status"] == "fail"
    checks = {f["check"] for f in res["findings"]}
    assert "flow_trajectory" in checks


def test_catches_shifted_and_missing_stratum():
    res_shift = _eval(_make_obs(), _make_obs(stratum_shift_y=3.0, is_cand=True))
    assert res_shift["status"] == "fail"
    checks_shift = {f["check"] for f in res_shift["findings"]}
    assert "stratum_geometry" in checks_shift
    assert "height_conservation" in checks_shift

    res_miss = _eval(_make_obs(), _make_obs(drop_stratum=True, is_cand=True))
    assert res_miss["status"] == "fail"
    checks_miss = {f["check"] for f in res_miss["findings"]}
    assert "missing_component" in checks_miss or "component_count" in checks_miss


def test_catches_wrong_fill_and_alpha():
    res = _eval(_make_obs(flow_fill="#f8766d", flow_alpha=0.5),
                _make_obs(flow_fill="#2244aa", flow_alpha=0.9, is_cand=True))
    assert res["status"] == "fail"
    flow_findings = [f for f in res["findings"] if f["check"] == "flow_style"]
    assert len(flow_findings) >= 2


def test_catches_miscentered_label_and_box_mismatch():
    res_pos = _eval(_make_obs(), _make_obs(label_shift_y=20.0, is_cand=True))
    assert res_pos["status"] == "fail"
    assert any(f["check"] == "label_position" for f in res_pos["findings"])

    res_box = _eval(_make_obs(label_has_box=True), _make_obs(label_has_box=False, is_cand=True))
    assert res_box["status"] == "fail"
    assert any(f["check"] == "label_style" for f in res_box["findings"])


def test_catches_reversed_layer_order():
    res = _eval(_make_obs(strata_on_top=True), _make_obs(strata_on_top=False, is_cand=True))
    assert res["status"] == "fail"
    assert any(f["check"] == "layer_order" for f in res["findings"])
