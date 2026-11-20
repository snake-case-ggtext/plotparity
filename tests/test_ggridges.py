"""ggridges adapter on small synthetic observations (the shape observe.R and observe_py.py produce)."""

import copy

from plotparity.adapters import ggridges as _pkg  # noqa: F401  (package import)
from plotparity.adapters.ggridges import adapter as A
from plotparity.compare import load_profile


def _ridge(pos, peak, paint, x0=0.0, x1=4.0):
    """A triangle ridge at level `pos`: an unstroked fill plus an open top outline."""
    top = [[x0, pos], [(x0 + x1) / 2, pos + peak], [x1, pos]]
    fill = [[x0, pos]] + top[1:] + [[x0, pos]]
    norm = lambda pts: [[x / 10, 1 - y / 5] for x, y in pts]      # noqa: E731
    return [
        {"paint_index": paint, "filled": True, "facecolor_hex": "#b3b3b3", "face_alpha": 1.0,
         "edgecolor_hex": "none", "edge_alpha": 0.0, "linewidth_pt": 0.0,
         "pieces": [{"data": fill, "norm": norm(fill)}]},
        {"paint_index": paint + 1, "filled": False, "facecolor_hex": "none", "face_alpha": 0.0,
         "edgecolor_hex": "#000000", "edge_alpha": 1.0, "linewidth_pt": 0.375,
         "pieces": [{"data": top, "norm": norm(top)}]},
    ]


def _obs(order=(2, 1)):
    """Two ridges; ridge 1 rises into ridge 2 (they overlap). `order`: which level is painted first."""
    peaks = {1: 1.6, 2: 1.0}
    events = []
    for k, pos in enumerate(order):
        events += _ridge(pos, peaks[pos], 2 * k + 1)
    return {"ggridges": {"panel_x_range": [0, 4], "panel_y_range": [0.4, 3.4], "y_labels": ["a", "b"],
                         "events": events, "build": []}}


def _compare(ref_obs, cand_obs):
    rc, _ = A.components("r", ref_obs, {})
    cc, _ = A.components("python", cand_obs, {})
    pairs, missing, unexpected = A.match(rc, cc)
    P = load_profile(A, {})
    found = []
    add = lambda check, *a, **k: found.append((check, k.get("sub")))     # noqa: E731
    for i, j in pairs:
        for name, fn in A.CHECKS.items():
            fn(rc[i], cc[j], P[name], add, {})
    A.paint_order(rc, cc, pairs, P["paint_order"], add, {})
    return found, missing, unexpected


def test_profile_loads_and_clean_pair_passes():
    found, missing, unexpected = _compare(_obs(), _obs())
    assert (found, missing, unexpected) == ([], [], [])


def test_components_group_by_level():
    comps, _ = A.components("r", _obs(), {})
    assert [(c["label"], len(c["fills"]), len(c["strokes"])) for c in comps] == [("a", 1, 1), ("b", 1, 1)]


def test_reversed_paint_order_fails():
    found, _, _ = _compare(_obs((2, 1)), _obs((1, 2)))
    assert ("paint_order", "a/b") in found


def test_alpha_and_missing_outline_fail():
    cand = copy.deepcopy(_obs())
    cand["ggridges"]["events"][0]["face_alpha"] = 0.5
    cand["ggridges"]["events"] = [e for e in cand["ggridges"]["events"] if not (e["paint_index"] == 4)]
    found, _, _ = _compare(_obs(), cand)
    assert ("ridge_fill", "alpha") in found and ("ridge_pieces", "lines") in found


def test_taller_ridge_and_stray_shape_fail():
    cand = copy.deepcopy(_obs())
    for e in cand["ggridges"]["events"]:
        for p in e["pieces"]:
            p["data"] = [[x, y if y in (1, 2) else y * 1.2] for x, y in p["data"]]
    stray = _ridge(1, 0.5, 9)[0]
    stray["pieces"][0]["data"] = [[x, y + 0.5] for x, y in stray["pieces"][0]["data"]]   # sits on no level
    cand["ggridges"]["events"].append(stray)
    found, _, unexpected = _compare(_obs(), cand)
    assert ("ridge_position", "height") in found
    assert len(unexpected) == 1


def test_observe_py_reads_plotnine_internals():
    """observe_py relies on plotnine internals: built data in plot.layers[0].data after draw, trained ranges and
    labels in plot.layout.panel_params, artists in data units. A plotnine upgrade that moves them must fail here,
    not silently weaken the checks."""
    import pandas as pd
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from plotnine import aes, geom_point, geom_ribbon, ggplot

    from plotparity.adapters.ggridges.observe_py import observe

    rib = pd.DataFrame({"x": [0.0, 1.0, 2.0], "lo": [1.0, 1.0, 1.0], "hi": [1.0, 1.5, 1.0]})
    pts = pd.DataFrame({"x": [0.0, 2.0], "y": ["a", "b"]})
    p = ggplot() + geom_point(aes("x", "y"), pts) + geom_ribbon(aes("x", ymin="lo", ymax="hi"), rib)
    fig = p.draw()
    FigureCanvasAgg(fig)
    fig.canvas.draw()
    st = observe(fig, p)["ggridges"]
    assert st["y_labels"] == ["a", "b"]
    fills = [e for e in st["events"] if e["filled"]]
    assert len(fills) == 1
    ys = [y for x, y in fills[0]["pieces"][0]["data"]]
    assert abs(min(ys) - 1.0) < 1e-6 and abs(max(ys) - 1.5) < 1e-6          # data units, not pixels
    assert st["build"] and {"x"} <= set(st["build"][0])                      # built layer data was read


def test_outline_only_ridge_and_missing_fill_do_not_crash_evaluate_case():
    from types import SimpleNamespace

    from plotparity.compare import evaluate_case

    P = load_profile(A, {})
    case = SimpleNamespace(id="no_fill", relpath="no_fill", expect={}, measurable=True, why="")
    outline_only = copy.deepcopy(_obs())
    outline_only["ggridges"]["events"] = [e for e in outline_only["ggridges"]["events"] if not e["filled"]]
    outline_only.update(owners=[], events=[], port_ink_px=10)
    full = copy.deepcopy(_obs())
    full.update(owners=[], events=[], port_ink_px=10)

    res_same = evaluate_case(case, A, P, [], {"observations": outline_only}, {"observations": outline_only},
                             package="ridgenine")
    assert res_same["status"] == "pass", res_same["findings"]

    res_missing = evaluate_case(case, A, P, [], {"observations": full}, {"observations": outline_only},
                                package="ridgenine")
    assert res_missing["status"] == "fail"
    assert any(f["check"] == "ridge_pieces" for f in res_missing["findings"])
    assert not any(f["status"] == "infrastructure_error" for f in res_missing["findings"])


def test_custom_port_artist_assigned_and_unextracted_artist_fails_closed():
    from types import SimpleNamespace

    from plotparity.compare import evaluate_case

    P = load_profile(A, {})
    case = SimpleNamespace(id="custom_artist", relpath="custom_artist", expect={}, measurable=True, why="")
    ref = _obs()
    cand = copy.deepcopy(_obs())
    cand["ggridges"]["extracted_classes"] = ["ridgenine.geom.RidgeCollection"]
    cand["owners"] = [{"class": "ridgenine.geom.RidgeCollection", "module": "ridgenine.geom", "parent": "axes"}]
    cand["events"] = [{"kind": "draw_path_collection", "paint_index": 1, "owner": 0}]
    cand["port_ink_px"] = 100
    res_ok = evaluate_case(case, A, P, [], {"observations": ref}, {"observations": cand}, package="ridgenine")
    assert res_ok["status"] == "pass", res_ok["findings"]

    cand_bad = copy.deepcopy(cand)
    cand_bad["owners"].append({"class": "ridgenine.geom.UnknownArtist", "module": "ridgenine.geom", "parent": "axes"})
    cand_bad["events"].append({"kind": "draw_quad_mesh", "paint_index": 2, "owner": 1})
    res_bad = evaluate_case(case, A, P, [], {"observations": ref}, {"observations": cand_bad}, package="ridgenine")
    assert res_bad["status"] == "fail"
    assert any(f["check"] == "unexpected_drawing" for f in res_bad["findings"])


def test_observe_py_picks_ridge_layer_over_layer_0():
    import pandas as pd
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from plotnine import aes, geom_point, geom_ribbon, ggplot

    from plotparity.adapters.ggridges.observe_py import observe

    pts = pd.DataFrame({"x": [0.0, 2.0], "y": ["a", "b"]})
    rib = pd.DataFrame({"x": [0.0, 1.0, 2.0], "lo": [1.0, 1.0, 1.0], "hi": [1.0, 1.5, 1.0]})
    p = ggplot() + geom_point(aes("x", "y"), pts) + geom_ribbon(aes("x", ymin="lo", ymax="hi"), rib)
    fig = p.draw()
    FigureCanvasAgg(fig)
    fig.canvas.draw()
    # simulate a ridge stat layer at index 1 carrying `density`
    p.layers[1].data["y"] = 1.0
    p.layers[1].data["density"] = [0.1, 0.5, 0.1]
    st = observe(fig, p)["ggridges"]
    assert st["build"] and st["build"][0]["density"] == [0.1, 0.5, 0.1]
