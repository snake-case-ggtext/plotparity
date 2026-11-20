"""Unit tests for the geomtextpath plotparity adapter."""

from __future__ import annotations

import copy
import math
from types import SimpleNamespace

from plotparity.adapters.geomtextpath import adapter as GTP
from plotparity.compare import evaluate_case, load_profile


def _make_panel():
    return {"panel_index": 1, "bbox_norm_tl": [0.1, 0.1, 0.9, 0.9]}


def _make_clean_pair(
    *,
    text: str = "Wave",
    gapped: bool = True,
    with_box: bool = False,
    text_only: bool = False,
    cand_angles: list[float] | None = None,
    cand_x_shift: float = 0.0,
    cand_text_color: str = "#112233",
    cand_line_color: str = "#112233",
    cand_linetype: str = "solid",
    cand_cuts_text: bool = False,
    cand_box_fill: str = "#ffffe0",
):
    """Build synthetic R + Python observations for a sloped/curved labelled line."""
    panel = _make_panel()
    # Curve from (0.2, 0.7) to (0.8, 0.3) with slope angle ~ 30..40 deg
    r_angles = [25.0, 32.0, 38.0, 44.0][: len(text)]
    xs = [0.46 + 0.025 * i for i in range(len(text))]
    ys = [0.54 - 0.018 * i for i in range(len(text))]

    r_glyphs = [
        {
            "char": ch,
            "cx_norm": xs[i],
            "cy_norm": ys[i],
            "angle_deg": r_angles[i],
            "color_hex": "#112233",
            "alpha": 1.0,
            "fontsize_pt": 11.0,
            "fontweight": "normal",
            "fontstyle": "normal",
            "bbox_norm_tl": [xs[i] - 0.008, ys[i] - 0.012, xs[i] + 0.008, ys[i] + 0.012],
        }
        for i, ch in enumerate(text)
    ]

    seg1 = [[0.20, 0.72], [0.32, 0.64], [0.42, 0.57]]
    seg2 = [[0.58, 0.45], [0.69, 0.38], [0.80, 0.30]]
    full_seg = [[0.20, 0.72], [0.35, 0.62], [0.50, 0.51], [0.65, 0.40], [0.80, 0.30]]

    if text_only:
        r_polys = []
    elif gapped:
        r_polys = [
            {"points_norm_tl": seg1, "color_hex": "#112233", "alpha": 1.0, "linewidth_pt": 1.07, "linetype": "solid"},
            {"points_norm_tl": seg2, "color_hex": "#112233", "alpha": 1.0, "linewidth_pt": 1.07, "linetype": "solid"},
        ]
    else:
        r_polys = [
            {"points_norm_tl": full_seg, "color_hex": "#112233", "alpha": 1.0, "linewidth_pt": 1.07, "linetype": "solid"},
        ]

    box_verts = [[0.44, 0.46], [0.56, 0.46], [0.57, 0.50], [0.56, 0.56], [0.50, 0.57], [0.44, 0.56], [0.43, 0.50]]
    r_box = (
        {
            "points_norm_tl": box_verts,
            "bbox_norm_tl": [0.43, 0.46, 0.57, 0.57],
            "is_rounded": True,
            "facecolor_hex": "#ffffe0",
            "face_alpha": 1.0,
            "edgecolor_hex": "#112233",
            "edge_alpha": 1.0,
            "linewidth_pt": 1.07,
        }
        if with_box
        else None
    )

    ref_obs = {
        "geomtextpath": {
            "panels": [panel],
            "curves": [{"panel": 1, "text": text, "glyphs": r_glyphs, "polylines": r_polys, "box": r_box}],
        }
    }

    # Build Python probe events
    c_angs = cand_angles if cand_angles is not None else r_angles
    events = []
    path_dashes = []
    pi = 0

    cand_segs = []
    if not text_only:
        cand_segs = [full_seg] if (cand_cuts_text or not gapped) else [seg1, seg2]

    for seg in cand_segs:
        pi += 1
        x0 = min(p[0] for p in seg)
        y0 = min(p[1] for p in seg)
        x1 = max(p[0] for p in seg)
        y1 = max(p[1] for p in seg)
        bb = [x0, y0, x1, y1]
        events.append({
            "kind": "path",
            "paint_index": pi,
            "owner": 0,
            "is_closed": False,
            "is_rounded": False,
            "bbox_norm_tl": bb,
            "vertices_norm_tl": seg,
            "facecolor_hex": "none",
            "face_alpha": 0.0,
            "edgecolor_hex": cand_line_color,
            "edge_alpha": 1.0,
            "linewidth_pt": 1.07,
        })
        path_dashes.append({"bbox_norm_tl": bb, "linetype": cand_linetype})

    if with_box:
        pi += 1
        events.append({
            "kind": "path",
            "paint_index": pi,
            "owner": 0,
            "is_closed": True,
            "is_rounded": True,
            "bbox_norm_tl": [0.43, 0.46, 0.57, 0.57],
            "vertices_norm_tl": box_verts,
            "facecolor_hex": cand_box_fill,
            "face_alpha": 1.0,
            "edgecolor_hex": "#112233",
            "edge_alpha": 1.0,
            "linewidth_pt": 1.07,
        })

    for i, ch in enumerate(text):
        pi += 1
        cx = xs[i] + cand_x_shift
        cy = ys[i]
        ang = c_angs[i]
        hw, hh = 0.008, 0.012
        events.append({
            "kind": "text",
            "paint_index": pi,
            "owner": 0,
            "text": ch,
            "x_norm": cx - hw,
            "y_norm": cy + hh,
            "bbox_norm_tl": [cx - hw, cy - hh, cx + hw, cy + hh],
            "quad_norm_tl": [[cx - hw, cy + hh], [cx + hw, cy + hh], [cx + hw, cy - hh], [cx - hw, cy - hh]],
            "color_hex": cand_text_color,
            "alpha": 1.0,
            "fontsize_pt": 11.0,
            "fontweight": "normal",
            "fontstyle": "normal",
            "angle_deg": ang,
        })

    cand_obs = {
        "geomtextpath": {"panels": [panel], "path_dashes": path_dashes, "layer_labels": []},
        "owners": [{"class": "plotnine_geomtextpath._artist._TextPathArtist", "module": "plotnine_geomtextpath._artist", "parent": "axes"}],
        "events": events,
        "port_ink_px": 200,
    }
    return ref_obs, cand_obs


def _eval(ref_obs, cand_obs, case_id="gtp_test"):
    case = SimpleNamespace(id=case_id, relpath=case_id, expect={}, measurable=True, why="")
    return evaluate_case(
        case, GTP, load_profile(GTP, {}), [],
        {"observations": ref_obs}, {"observations": cand_obs},
        package="plotnine_geomtextpath",
    )


def test_clean_pass_curved_gapped_and_labelbox():
    ref_obs, cand_obs = _make_clean_pair(gapped=True, with_box=False)
    res = _eval(ref_obs, cand_obs, "clean_curved")
    assert res["status"] == "pass", res["findings"]

    ref_obs2, cand_obs2 = _make_clean_pair(gapped=False, with_box=True)
    res2 = _eval(ref_obs2, cand_obs2, "clean_labelbox")
    assert res2["status"] == "pass", res2["findings"]


def test_catches_unrotated_flat_text():
    ref_obs, cand_obs = _make_clean_pair(cand_angles=[0.0, 0.0, 0.0, 0.0])
    res = _eval(ref_obs, cand_obs, "flat_text")
    checks = {f["check"] for f in res["findings"] if f["status"] == "fail"}
    assert "glyph_angle" in checks, res["findings"]


def test_catches_upside_down_glyphs():
    ref_obs, cand_obs = _make_clean_pair(cand_angles=[-155.0, -148.0, -142.0, -136.0])
    res = _eval(ref_obs, cand_obs, "upside_down")
    checks = {f["check"] for f in res["findings"] if f["status"] == "fail"}
    assert "glyph_angle" in checks, res["findings"]
    assert any("upside-down" in f["message"] for f in res["findings"] if f["check"] == "glyph_angle")


def test_catches_line_cutting_through_text_when_r_gaps():
    ref_obs, cand_obs = _make_clean_pair(gapped=True, cand_cuts_text=True)
    res = _eval(ref_obs, cand_obs, "cuts_text")
    checks = {f["check"] for f in res["findings"] if f["status"] == "fail"}
    assert "line_gap" in checks, res["findings"]


def test_catches_shifted_label_position():
    ref_obs, cand_obs = _make_clean_pair(cand_x_shift=0.12)
    res = _eval(ref_obs, cand_obs, "shifted_label")
    checks = {f["check"] for f in res["findings"] if f["status"] == "fail"}
    assert "glyph_position" in checks, res["findings"]


def test_catches_wrong_text_line_and_box_styles():
    ref_obs, cand_obs = _make_clean_pair(
        gapped=False, with_box=True,
        cand_text_color="#ff0000", cand_line_color="#00aa00",
        cand_linetype="dashed", cand_box_fill="#0000ff",
    )
    res = _eval(ref_obs, cand_obs, "wrong_styles")
    checks = {f["check"] for f in res["findings"] if f["status"] == "fail"}
    assert {"text_style", "line_style", "label_box_style"} <= checks, res["findings"]


def test_catches_unexpected_line_on_text_only():
    ref_obs, _ = _make_clean_pair(text_only=True)
    _, cand_obs = _make_clean_pair(text_only=False)
    res = _eval(ref_obs, cand_obs, "unexpected_line")
    checks = {f["check"] for f in res["findings"] if f["status"] == "fail"}
    assert "curve_trajectory" in checks, res["findings"]
