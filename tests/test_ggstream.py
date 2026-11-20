"""Unit tests for the ggstream plotparity adapter."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from plotparity.adapters.ggstream import adapter as GS
from plotparity.compare import evaluate_case, load_profile


def _ribbon_poly(
    xs: np.ndarray,
    y_bot: np.ndarray,
    y_top: np.ndarray,
    *,
    flip: bool = False,
) -> dict:
    pts_top = [[float(x), float(y)] for x, y in zip(xs, y_top)]
    pts_bot = [[float(x), float(y)] for x, y in zip(xs[::-1], y_bot[::-1])]
    pts = pts_top + pts_bot + [pts_top[0]]
    if flip:
        pts = [[y, x] for x, y in pts]
    norm = [[0.1 + 0.08 * p[0], 0.5 - 0.03 * p[1]] for p in pts]
    return {"data": pts, "norm": norm}


def _make_obs(
    *,
    mode: str = "mirror",
    swap_order: bool = False,
    gap: float = 0.0,
    x_extend: float = 0.0,
    fill0: str = "#f8766d",
    alpha: float = 1.0,
    edge_color: str = "none",
    edge_alpha: float = 0.0,
    linewidth_pt: float = 0.0,
    drop_stream: bool = False,
    with_labels: bool = True,
    label_shift_y: float = 0.0,
    label_shift_x: float = 0.0,
    label_has_box: bool = False,
    flip: bool = False,
    is_cand: bool = False,
) -> dict:
    xs = np.linspace(1.0 - x_extend, 10.0 + x_extend, 61)
    # Three distinct smooth non-negative thickness profiles
    h0 = 1.0 + 3.5 * np.exp(-0.5 * ((xs - 3.0) / 1.4) ** 2)
    h1 = 1.5 + 4.0 * np.exp(-0.5 * ((xs - 6.0) / 1.6) ** 2)
    h2 = 0.8 + 2.5 * np.exp(-0.5 * ((xs - 8.5) / 1.3) ** 2)

    series = [
        ("A", fill0, h0),
        ("B", "#00ba38", h1),
        ("C", "#619cff", h2),
    ]
    if swap_order:
        series = [series[2], series[1], series[0]]
    if drop_stream:
        series = series[:-1]

    hs = [s[2] for s in series]
    total = np.sum(hs, axis=0)

    if mode == "proportional":
        hs = [h / np.maximum(total, 1e-9) for h in hs]
        base = np.zeros_like(xs)
    elif mode == "ridge":
        base = np.zeros_like(xs)
    elif mode == "mirror":
        base = -0.5 * total
    else:
        raise ValueError(mode)

    shapes = []
    labels = []
    cur = base.copy()
    for idx, ((name, fc, _), h) in enumerate(zip(series, hs)):
        yb = cur.copy()
        yt = yb + h
        shapes.append({
            "kind": "polygon",
            "paint_index": idx + 1,
            "filled": True,
            "facecolor_hex": fc,
            "face_alpha": alpha,
            "edgecolor_hex": edge_color,
            "edge_alpha": edge_alpha,
            "linewidth_pt": linewidth_pt,
            "pieces": [_ribbon_poly(xs, yb, yt, flip=flip)],
        })
        if with_labels:
            kmax = int(np.argmax(h))
            lx = float(xs[kmax]) + (label_shift_x if idx == 0 else 0.0)
            ly = float(0.5 * (yb[kmax] + yt[kmax])) + (label_shift_y if idx == 0 else 0.0)
            px, py = (ly, lx) if flip else (lx, ly)
            rec = {
                "text": name,
                "x": px,
                "y": py,
                "x_norm": 0.5,
                "y_norm": 0.5,
                "color_hex": "#000000",
                "alpha": 1.0,
                "fontsize_pt": 11.0,
                "paint_index": 20 + idx,
                "has_box": label_has_box,
            }
            if label_has_box:
                rec.update(
                    box_fill_hex="#ffffff",
                    box_face_alpha=1.0,
                    box_edge_hex="#000000",
                    box_edge_alpha=1.0,
                )
            labels.append(rec)
        cur = yt + (gap if idx == 0 else 0.0)

    yr = [-1.0, 2.0] if mode == "proportional" else [-10.0, 14.0]
    xr = [0.0, 11.0]
    panel = {
        "panel_index": 1,
        "is_flip": flip,
        "x_range": yr if flip else xr,
        "y_range": xr if flip else yr,
        "x_labels": [],
        "y_labels": [],
        "x_is_discrete": False,
        "y_is_discrete": False,
        "bbox_norm_tl": [0.1, 0.1, 0.9, 0.9],
        "shapes": shapes,
        "labels": labels,
    }
    obs: dict = {
        "ggstream": {
            "panels": [panel],
            "extracted_classes": ["matplotlib.collections.PolyCollection", "matplotlib.text.Text"],
        }
    }
    if is_cand:
        obs["owners"] = [
            {"class": "matplotlib.collections.PolyCollection", "module": "matplotlib.collections", "parent": "axes"},
        ]
        obs["events"] = [{"kind": "path", "paint_index": 1, "owner": 0}]
        obs["port_ink_px"] = 200
    return obs


def _eval(ref_obs: dict, cand_obs: dict) -> dict:
    case = SimpleNamespace(id="test_stream", relpath="test_stream", expect={}, measurable=True, why="")
    return evaluate_case(
        case,
        GS,
        load_profile(GS, {}),
        [],
        {"observations": ref_obs},
        {"observations": cand_obs},
        package="plotnine_ggstream",
    )


def test_profile_loads_cleanly():
    profile = load_profile(GS, {})
    assert profile["stream_envelope"]["severity"] == "gating"
    assert profile["stream_stacking"]["severity"] == "gating"
    assert profile["stream_style"]["severity"] == "gating"
    assert profile["stream_label_position"]["severity"] == "gating"
    assert profile["stream_label_style"]["severity"] == "gating"


def test_clean_pass_mirror_ridge_proportional_and_coord_flip():
    for mode in ("mirror", "ridge", "proportional"):
        for flip in (False, True):
            res = _eval(_make_obs(mode=mode, flip=flip), _make_obs(mode=mode, flip=flip, is_cand=True))
            assert res["status"] == "pass", f"mode={mode} flip={flip}: {res['findings']}"
            assert len(res["findings"]) == 0


def test_catches_wrong_baseline_ridge_vs_mirror():
    res = _eval(_make_obs(mode="mirror"), _make_obs(mode="ridge", is_cand=True))
    assert res["status"] == "fail"
    checks = {f["check"] for f in res["findings"]}
    assert "stream_envelope" in checks


def test_catches_wrong_stacking_order():
    res = _eval(_make_obs(swap_order=False), _make_obs(swap_order=True, is_cand=True))
    assert res["status"] == "fail"
    checks = {f["check"] for f in res["findings"]}
    assert "stream_stacking" in checks
    assert any("order" in f["fingerprint"] for f in res["findings"] if f["check"] == "stream_stacking")


def test_catches_gap_and_overlap_between_streams():
    res_gap = _eval(_make_obs(gap=0.0), _make_obs(gap=1.2, is_cand=True))
    assert res_gap["status"] == "fail"
    assert any(
        f["check"] == "stream_stacking" and "contiguity" in f["fingerprint"]
        for f in res_gap["findings"]
    )

    res_ov = _eval(_make_obs(gap=0.0), _make_obs(gap=-1.2, is_cand=True))
    assert res_ov["status"] == "fail"
    assert any(
        f["check"] == "stream_stacking" and "contiguity" in f["fingerprint"]
        for f in res_ov["findings"]
    )


def test_catches_wrong_x_span():
    res = _eval(_make_obs(x_extend=0.0), _make_obs(x_extend=1.5, is_cand=True))
    assert res["status"] == "fail"
    assert any(
        f["check"] == "stream_envelope" and "u_span" in f["fingerprint"]
        for f in res["findings"]
    )


def test_catches_wrong_fill_alpha_and_outline():
    res = _eval(
        _make_obs(fill0="#f8766d", alpha=0.85, edge_color="#ffffff", edge_alpha=1.0, linewidth_pt=1.0),
        _make_obs(fill0="#1122aa", alpha=0.5, edge_color="#000000", edge_alpha=1.0, linewidth_pt=2.5, is_cand=True),
    )
    assert res["status"] == "fail"
    style_findings = [f for f in res["findings"] if f["check"] == "stream_style"]
    assert len(style_findings) >= 4


def test_catches_label_outside_stream_and_box_mismatch():
    res_out = _eval(_make_obs(label_shift_y=0.0), _make_obs(label_shift_y=-8.0, is_cand=True))
    assert res_out["status"] == "fail"
    assert any(f["check"] == "stream_label_position" for f in res_out["findings"])
    assert any("inside_stream" in f["fingerprint"] for f in res_out["findings"])

    res_box = _eval(_make_obs(label_has_box=True), _make_obs(label_has_box=False, is_cand=True))
    assert res_box["status"] == "fail"
    assert any(f["check"] == "stream_label_style" for f in res_box["findings"])


def test_catches_missing_stream():
    res = _eval(_make_obs(drop_stream=False), _make_obs(drop_stream=True, is_cand=True))
    assert res["status"] == "fail"
    checks = {f["check"] for f in res["findings"]}
    assert "component_count" in checks or "missing_component" in checks


def test_catches_label_in_narrow_tail_and_off_center():
    # Stream A peaks at x=3 (thickness 4.5) and narrows to thickness 1.0 at x=8
    cand_narrow = _make_obs(is_cand=True)
    # Move label A to x=8.0, centered vertically inside stream A at x=8.0
    poly_a = np.asarray(cand_narrow["ggstream"]["panels"][0]["shapes"][0]["pieces"][0]["data"])
    near8 = poly_a[np.abs(poly_a[:, 0] - 8.0) < 0.2, 1]
    cand_narrow["ggstream"]["panels"][0]["labels"][0]["x"] = 8.0
    cand_narrow["ggstream"]["panels"][0]["labels"][0]["y"] = float(0.5 * (near8.min() + near8.max()))
    res_narrow = _eval(_make_obs(), cand_narrow)
    assert res_narrow["status"] == "fail"
    assert any(f["check"] == "stream_label_position" and "offset" in f["fingerprint"] for f in res_narrow["findings"])

    # Move label A vertically near its top border (off-center inside stream A at peak x=3)
    res_off = _eval(_make_obs(), _make_obs(label_shift_y=1.9, is_cand=True))
    assert res_off["status"] == "fail"
    assert any(f["check"] == "stream_label_position" and "vertical_center" in f["fingerprint"] for f in res_off["findings"])


def test_accepts_bimodal_alternate_wide_bulge():
    xs = np.linspace(1.0, 10.0, 61)
    # Symmetric bimodal stream peaking equally at x=3.0 and x=8.0
    h = 1.0 + 3.5 * np.exp(-0.5 * ((xs - 3.0) / 1.1) ** 2) + 3.5 * np.exp(-0.5 * ((xs - 8.0) / 1.1) ** 2)
    yb, yt = -0.5 * h, 0.5 * h

    def _bimodal(lx: float, is_cand: bool = False) -> dict:
        panel = {
            "panel_index": 1,
            "is_flip": False,
            "x_range": [0.0, 11.0],
            "y_range": [-5.0, 5.0],
            "x_labels": [],
            "y_labels": [],
            "x_is_discrete": False,
            "y_is_discrete": False,
            "bbox_norm_tl": [0.1, 0.1, 0.9, 0.9],
            "shapes": [{
                "kind": "polygon",
                "paint_index": 1,
                "filled": True,
                "facecolor_hex": "#00ba38",
                "face_alpha": 1.0,
                "edgecolor_hex": "none",
                "edge_alpha": 0.0,
                "linewidth_pt": 0.0,
                "pieces": [_ribbon_poly(xs, yb, yt)],
            }],
            "labels": [{
                "text": "Drama",
                "x": lx,
                "y": 0.0,
                "x_norm": 0.5,
                "y_norm": 0.5,
                "color_hex": "#000000",
                "alpha": 1.0,
                "fontsize_pt": 11.0,
                "paint_index": 10,
                "has_box": False,
            }],
        }
        obs: dict = {
            "ggstream": {
                "panels": [panel],
                "extracted_classes": ["matplotlib.collections.PolyCollection", "matplotlib.text.Text"],
            }
        }
        if is_cand:
            obs["owners"] = [
                {"class": "matplotlib.collections.PolyCollection", "module": "matplotlib.collections", "parent": "axes"},
            ]
            obs["events"] = [{"kind": "path", "paint_index": 1, "owner": 0}]
            obs["port_ink_px"] = 200
        return obs

    res = _eval(_bimodal(3.0), _bimodal(8.0, is_cand=True))
    assert res["status"] == "pass", f"Unexpected bimodal failure: {res['findings']}"
