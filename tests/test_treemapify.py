"""Unit tests for the treemapify plotparity adapter."""

from __future__ import annotations

from types import SimpleNamespace

from plotparity.adapters.treemapify import adapter as TM
from plotparity.compare import evaluate_case, load_profile


def _npc_to_norm_tl(x0: float, y0: float, x1: float, y1: float, pbb: list[float]) -> list[float]:
    pw = pbb[2] - pbb[0]
    ph = pbb[3] - pbb[1]
    return [
        round(pbb[0] + x0 * pw, 6),
        round(pbb[3] - y1 * ph, 6),
        round(pbb[0] + x1 * pw, 6),
        round(pbb[3] - y0 * ph, 6),
    ]


def _make_obs(
    *,
    tiles: list[tuple[float, float, float, float, str]] | None = None,
    borders: list[tuple[float, float, float, float, str, float]] | None = None,
    labels: list[dict] | None = None,
    is_cand: bool = False,
) -> dict:
    pbb = [0.1, 0.1, 0.9, 0.9]
    if tiles is None:
        # 2 subgroups: G1=[0,0,0.6,1] (tiles A=[0,0,0.6,0.6], B=[0,0.6,0.6,1.0])
        #              G2=[0.6,0,1,1] (tiles C=[0.6,0,1,0.5], D=[0.6,0.5,1,1])
        tiles = [
            (0.0, 0.0, 0.6, 0.6, "#f8766d"),
            (0.0, 0.6, 0.6, 1.0, "#f8766d"),
            (0.6, 0.0, 1.0, 0.5, "#00bfc4"),
            (0.6, 0.5, 1.0, 1.0, "#00bfc4"),
        ]
    if borders is None:
        borders = [
            (0.0, 0.0, 0.6, 1.0, "#7f7f7f", 3.0),
            (0.6, 0.0, 1.0, 1.0, "#7f7f7f", 3.0),
        ]
    if labels is None:
        labels = [
            {"text": "Alpha", "x0": 0.20, "y0": 0.25, "x1": 0.40, "y1": 0.35, "fontsize_pt": 18.0,
             "color_hex": "#000000", "alpha": 1.0, "fontweight": "normal", "fontstyle": "normal", "label_role": "tile"},
            {"text": "Beta", "x0": 0.20, "y0": 0.75, "x1": 0.40, "y1": 0.85, "fontsize_pt": 18.0,
             "color_hex": "#000000", "alpha": 1.0, "fontweight": "normal", "fontstyle": "normal", "label_role": "tile"},
            {"text": "G1", "x0": 0.25, "y0": 0.02, "x1": 0.35, "y1": 0.10, "fontsize_pt": 36.0,
             "color_hex": "#ffffff", "alpha": 0.5, "fontweight": "normal", "fontstyle": "italic", "label_role": "subgroup"},
        ]

    tile_recs = []
    for k, (x0, y0, x1, y1, fc) in enumerate(tiles):
        tile_recs.append({
            "paint_index": k + 1,
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "bbox_norm_tl": _npc_to_norm_tl(x0, y0, x1, y1, pbb),
            "facecolor_hex": fc, "face_alpha": 1.0,
            "edgecolor_hex": "#bebebe", "edge_alpha": 1.0,
            "linewidth_pt": 0.375, "linetype": "solid",
        })

    border_recs = []
    for k, (x0, y0, x1, y1, ec, lw) in enumerate(borders):
        border_recs.append({
            "paint_index": 20 + k,
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "bbox_norm_tl": _npc_to_norm_tl(x0, y0, x1, y1, pbb),
            "edgecolor_hex": ec, "edge_alpha": 1.0,
            "linewidth_pt": lw, "linetype": "solid",
        })

    label_recs = []
    for k, lb in enumerate(labels):
        rec = dict(lb)
        rec["paint_index"] = 40 + k
        rec["bbox_norm_tl"] = _npc_to_norm_tl(lb["x0"], lb["y0"], lb["x1"], lb["y1"], pbb)
        rec["line_count"] = lb["text"].count("\n") + 1
        label_recs.append(rec)

    panel = {
        "panel_index": 1,
        "bbox_norm_tl": pbb,
        "tiles": tile_recs,
        "borders": border_recs,
        "labels": label_recs,
    }
    obs = {"treemapify": {"panels": [panel]}}
    if is_cand:
        obs["events"] = []
        obs["owners"] = []
        obs["port_ink_px"] = 100
    return obs


def _eval(ref_obs: dict, cand_obs: dict) -> dict:
    case = SimpleNamespace(id="test_tm", relpath="test_tm", expect={}, measurable=True, why="")
    profile = load_profile(TM, {})
    return evaluate_case(
        case, TM, profile, [],
        {"observations": ref_obs}, {"observations": cand_obs},
        package="plotnine_treemapify",
    )


def _failing_checks(res: dict) -> set[str]:
    return {f["check"] for f in res["findings"] if f["status"] == "fail"}


def test_clean_pass():
    res = _eval(_make_obs(), _make_obs(is_cand=True))
    assert res["status"] == "pass", res["findings"]


def test_catches_overlapping_tiles():
    bad_tiles = [
        (0.0, 0.0, 0.7, 0.6, "#f8766d"),  # overlaps tile C horizontally!
        (0.0, 0.6, 0.6, 1.0, "#f8766d"),
        (0.6, 0.0, 1.0, 0.5, "#00bfc4"),
        (0.6, 0.5, 1.0, 1.0, "#00bfc4"),
    ]
    res = _eval(_make_obs(), _make_obs(tiles=bad_tiles, is_cand=True))
    assert res["status"] == "fail"
    assert "tiles_no_overlap" in _failing_checks(res)


def test_catches_wrong_tile_area():
    # Tile A is 0.42 instead of 0.36; tile B is 0.18 instead of 0.24
    bad_tiles = [
        (0.0, 0.0, 0.6, 0.7, "#f8766d"),
        (0.0, 0.7, 0.6, 1.0, "#f8766d"),
        (0.6, 0.0, 1.0, 0.5, "#00bfc4"),
        (0.6, 0.5, 1.0, 1.0, "#00bfc4"),
    ]
    res = _eval(_make_obs(), _make_obs(tiles=bad_tiles, is_cand=True))
    assert res["status"] == "fail"
    assert "tile_area" in _failing_checks(res)


def test_catches_wrong_corner_orientation():
    # Vertically flipped (topleft instead of bottomleft) with same tile areas
    flipped_tiles = [
        (0.0, 0.4, 0.6, 1.0, "#f8766d"),
        (0.0, 0.0, 0.6, 0.4, "#f8766d"),
        (0.6, 0.5, 1.0, 1.0, "#00bfc4"),
        (0.6, 0.0, 1.0, 0.5, "#00bfc4"),
    ]
    res = _eval(_make_obs(), _make_obs(tiles=flipped_tiles, is_cand=True))
    assert res["status"] == "fail"
    assert "tile_geometry" in _failing_checks(res)


def test_catches_text_overflowing_tile():
    bad_labels = [
        {"text": "Alpha", "x0": 0.20, "y0": 0.25, "x1": 0.72, "y1": 0.35, "fontsize_pt": 18.0,  # overflows x1=0.6!
         "color_hex": "#000000", "alpha": 1.0, "fontweight": "normal", "fontstyle": "normal", "label_role": "tile"},
        {"text": "Beta", "x0": 0.20, "y0": 0.75, "x1": 0.40, "y1": 0.85, "fontsize_pt": 18.0,
         "color_hex": "#000000", "alpha": 1.0, "fontweight": "normal", "fontstyle": "normal", "label_role": "tile"},
        {"text": "G1", "x0": 0.25, "y0": 0.02, "x1": 0.35, "y1": 0.10, "fontsize_pt": 36.0,
         "color_hex": "#ffffff", "alpha": 0.5, "fontweight": "normal", "fontstyle": "italic", "label_role": "subgroup"},
    ]
    res = _eval(_make_obs(), _make_obs(labels=bad_labels, is_cand=True))
    assert res["status"] == "fail"
    assert "text_fits_tile" in _failing_checks(res)


def test_catches_wrong_subgroup_border():
    bad_borders = [
        (0.0, 0.0, 0.5, 1.0, "#7f7f7f", 3.0),  # too narrow, child tiles poke outside!
        (0.6, 0.0, 1.0, 1.0, "#ff0000", 3.0),  # wrong color!
    ]
    res = _eval(_make_obs(), _make_obs(borders=bad_borders, is_cand=True))
    assert res["status"] == "fail"
    assert "subgroup_containment" in _failing_checks(res)


def test_catches_wrong_fill_color():
    bad_tiles = [
        (0.0, 0.0, 0.6, 0.6, "#0000ff"),  # wrong fill color!
        (0.0, 0.6, 0.6, 1.0, "#f8766d"),
        (0.6, 0.0, 1.0, 0.5, "#00bfc4"),
        (0.6, 0.5, 1.0, 1.0, "#00bfc4"),
    ]
    res = _eval(_make_obs(), _make_obs(tiles=bad_tiles, is_cand=True))
    assert res["status"] == "fail"
    assert "tile_style" in _failing_checks(res)


def test_catches_label_style_and_multiline_probe_grouping():
    # Test real probe draw_text grouping for multi-line wrapped text + italic mismatch
    pbb = [0.1, 0.1, 0.9, 0.9]
    ref_labels = [
        {"text": "Long Label\nWrapped", "x0": 0.10, "y0": 0.20, "x1": 0.50, "y1": 0.40, "fontsize_pt": 18.0,
         "color_hex": "#000000", "alpha": 1.0, "fontweight": "normal", "fontstyle": "italic", "label_role": "tile"},
    ]
    ref_obs = _make_obs(borders=[], labels=ref_labels)
    cand_obs = _make_obs(borders=[], labels=[], is_cand=True)
    # Feed candidate text via probe events (2 lines in one treemap_text group, wrong fontstyle='normal')
    cand_obs["owners"] = [{"module": "plotnine_treemapify.text", "class": "plotnine_treemapify.text._FitTextArtist"}]
    cand_obs["events"] = [
        {
            "kind": "text", "paint_index": 101, "owner": 0,
            "groups": ["figure", "axes", "treemap_text"], "group_ids": [1, 2, 7],
            "text": "Long Label", "bbox_norm_tl": _npc_to_norm_tl(0.10, 0.30, 0.50, 0.40, pbb),
            "fontsize_pt": 18.0, "color_hex": "#000000", "alpha": 1.0,
            "fontweight": "normal", "fontstyle": "normal",
        },
        {
            "kind": "text", "paint_index": 102, "owner": 0,
            "groups": ["figure", "axes", "treemap_text"], "group_ids": [1, 2, 7],
            "text": "Wrapped", "bbox_norm_tl": _npc_to_norm_tl(0.10, 0.20, 0.42, 0.30, pbb),
            "fontsize_pt": 18.0, "color_hex": "#000000", "alpha": 1.0,
            "fontweight": "normal", "fontstyle": "normal",
        },
    ]
    res = _eval(ref_obs, cand_obs)
    assert res["status"] == "fail"
    assert _failing_checks(res) == {"label_style"}
