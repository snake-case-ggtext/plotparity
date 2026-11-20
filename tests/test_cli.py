"""The command line: package inference, case selection, init, comparison defaults, reference handling."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "src"))

from plotparity.cli import _comparable, main  # noqa: E402
from plotparity.project import Library, Project, UsageError, infer_package, match_k  # noqa: E402

FIXTURES = TESTS / "fixtures"
PORT = os.environ.get("PLOTPARITY_TEST_PORT",
                      "git+https://github.com/snake-case-ggtext/plotnine-ggtext@294826676a22e53b9dec06f0789b16610ddccfc1")


def _pkg(root: Path, *names: str, src: bool = False) -> Path:
    for n in names:
        d = (root / "src" / n) if src else (root / n)
        d.mkdir(parents=True)
        (d / "__init__.py").write_text("")
    return root


def test_infer_package_layouts(tmp_path):
    assert infer_package(_pkg(tmp_path / "a", "plotnine_foo", src=True)) == "plotnine_foo"
    assert infer_package(_pkg(tmp_path / "b", "plotnine_bar")) == "plotnine_bar"
    with pytest.raises(UsageError, match="no Python package found"):
        infer_package(tmp_path / "b" / "plotnine_bar")      # a dir with no package inside it
    with pytest.raises(UsageError, match="several packages"):
        infer_package(_pkg(tmp_path / "c", "one", "two"))


def test_no_package_here_is_a_usage_error(tmp_path, monkeypatch, capsys):
    shutil.copytree(FIXTURES / "cases" / "ggtext", tmp_path / "parity")
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--no-html"]) == 3
    assert "pass --from" in capsys.readouterr().err


def test_k_expressions():
    assert match_k("markup", "richtext_example_markup")
    assert match_k("css or scripts", "richtext_feature_matrix_scripts")
    assert not match_k("richtext and not markup", "richtext_example_markup")
    assert match_k("richtext and not markup", "richtext_rotated_colors")


def test_paths_k_and_split_select_cases(tmp_path):
    lib = FIXTURES / "cases" / "ggtext"
    p = Project.build(paths=[str(lib / "geom_richtext")], package="plotnine_ggtext", from_=str(tmp_path))
    assert set(p.pick()) == {"richtext_example_markup", "richtext_rotated_colors"}
    assert p.pick(k="markup") == ["richtext_example_markup"]
    with pytest.raises(UsageError, match="--split needs"):
        p.pick(split="dev")                                  # no parity.toml → no splits
    cfg = Project.load(FIXTURES / "projects" / "ggtext")
    assert set(cfg.pick()) == set(cfg.select("dev"))         # parity.toml default_split
    with pytest.raises(UsageError, match="unknown split"):
        cfg.pick(split="nope")


def test_library_toml_is_still_read(tmp_path):
    lib = tmp_path / "cases"
    shutil.copytree(FIXTURES / "cases" / "ggtext", lib)
    (lib / "cases.toml").unlink()
    (lib / "library.toml").write_text('[library]\nadapter = "ggtext"\n[reference]\npackages = ["ggplot2", "ggtext"]\n')
    assert Library.load(lib).adapter_name == "ggtext"


def test_init_writes_config_cases_toml_and_gitignore(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text("build/")
    assert main(["init", "--package", "plotnine_foo"]) == 0
    assert 'cases = "parity"' in (tmp_path / "parity.toml").read_text()
    assert (tmp_path / "parity" / "cases.toml").exists()
    assert ".plotparity/" in (tmp_path / ".gitignore").read_text().split()
    assert main(["init"]) == 3                               # refuses to overwrite


def test_compare_default_only_matches_same_package_and_cases(tmp_path):
    runs = tmp_path / "runs"
    def write(name, pkg, src, ids):
        (runs / name).mkdir(parents=True)
        (runs / name / "report.json").write_text(json.dumps(
            {"package": pkg, "candidate": {"source": src}, "case_ids": ids}))
    write("20260101-000001", "pkg", "/a", ["x", "y"])
    write("20260101-000002", "pkg", "/b", ["x", "y"])        # different source
    write("20260101-000003", "pkg", "/a", ["x"])             # different cases
    write("20260101-000004", "other", "/a", ["x", "y"])      # different package
    now = {"package": "pkg", "candidate": {"source": "/a"}, "case_ids": ["y", "x"]}
    assert _comparable(runs, now) == runs / "20260101-000001" / "report.json"
    assert _comparable(runs, {**now, "package": "nope"}) is None


def test_no_render_r_reports_stale_references(tmp_path, monkeypatch, capsys):
    shutil.copytree(FIXTURES, tmp_path / "fx")
    case = tmp_path / "fx" / "cases" / "ggtext" / "geom_richtext" / "richtext_rotated_colors.md"
    case.write_text(case.read_text().replace("hjust = 0, vjust = 0.5", "hjust = 0.5, vjust = 0.5"))  # R block edit → stale
    cfg = tmp_path / "fx" / "projects" / "ggtext" / "parity.toml"
    cfg.write_text(cfg.read_text().replace('"PLOTPARITY_TEST_PORT"', json.dumps(PORT)))
    code = main(["run", "--config", str(cfg), "-k", "rotated_colors", "--no-render-r", "--no-html", "-q",
                 "--out", str(tmp_path / "out")])
    assert code == 2
    assert "missing or stale" in capsys.readouterr().err


def test_view_build_config_free_filters_in_scope_and_aligns_unmatched_key(tmp_path):
    import re

    from plotparity.view.build import build

    lib = FIXTURES / "cases" / "ggtext"
    cid = "richtext_example_markup"
    run_dir = tmp_path / "run"
    cdir = run_dir / "render" / cid
    cdir.mkdir(parents=True)
    # Candidate observations: 5 in-panel boxes (R has 4 -> 1 unmatched) + 1 out-of-scope plain plot.title
    cand_obs = {
        "figure_px": [640, 480],
        "dpi": 72.0,
        "panels_norm_tl": [[0.1, 0.1, 0.9, 0.9]],
        "owners": [],
        "roles": [{"role": "plot.title", "role_index": 0, "class": "Text"}],
        "events": [
            {"kind": "text", "paint_index": 1, "groups": ["figure"], "group_ids": [1], "owner": None,
             "role": "plot.title", "role_index": 0, "role_artist": 0, "text": "Plain title",
             "x_norm": 0.5, "y_norm": 0.05, "color_hex": "#000000", "alpha": 1.0,
             "fontsize_pt": 12.0, "fontweight": "normal", "fontstyle": "normal", "angle_deg": 0.0},
            *[
                {"kind": "text", "paint_index": i + 2, "groups": ["axes", "richtext"], "group_ids": [1, i + 10],
                 "owner": None, "text": f"Box{i}", "x_norm": 0.2 + 0.15 * i, "y_norm": 0.5,
                 "color_hex": "#000000", "alpha": 1.0, "fontsize_pt": 11.0,
                 "fontweight": "normal", "fontstyle": "normal", "angle_deg": 0.0}
                for i in range(5)
            ],
        ],
    }
    (cdir / "observations.json").write_text(json.dumps(cand_obs))
    rep = {
        "status": "fail",
        "created": "2026-01-01T00:00:00",
        "project": str(tmp_path),            # no parity.toml here (config-free run)
        "config": None,
        "package": "plotnine_ggtext",
        "cases_root": str(lib),
        "case_ids": [cid],
        "selection": "1 cases",
        "candidate": {"source": str(tmp_path), "commit": "abcdef123456"},
        "profile": {},
        "cases": [{"case": cid, "relpath": f"geom_richtext/{cid}", "split": None, "status": "fail",
                   "findings": [], "reference_png": str(lib / "_references" / cid / "reference.png"),
                   "candidate_png": None}],
    }
    rf = run_dir / "report.json"
    rf.write_text(json.dumps(rep))
    html = build([rf], run_dir / "report.html").read_text()
    m = re.search(r"const D = (\{.*?\});\nconst IMG =", html)
    assert m is not None
    data = json.loads(m.group(1))
    r0 = data["runs"][0]
    assert r0["package"] == "plotnine_ggtext"
    assert "plotnine_ggtext@abcdef1" in r0["label"]
    assert cid in data["cases"] and data["cases"][cid]["r"]
    assert r0["settings"]["files"].get("defaults", "").endswith("profile.toml")
    geom = r0["cases"][0]["geom"]
    assert "_error" not in geom
    assert not any(k.startswith("box[unmatched_cand_") for k in geom)
    # Out-of-scope plain plot.title must not leak into unmatched_candidate entries; only the 1 extra panel box does
    unmatched = [k for k in geom if k.startswith("unmatched_candidate[")]
    assert len(unmatched) == 1


def test_port_ink_counts_ink_outside_first_panel(tmp_path):
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.text import Text

    from plotparity.engines.python.probe import record
    from plotparity.engines.python.worker import _port_ink

    class PortTitle(Text):
        pass

    PortTitle.__module__ = "fake_port.theme"
    fig, ax = plt.subplots(figsize=(4, 3), dpi=72)
    FigureCanvasAgg(fig)
    title = PortTitle(0.5, 0.95, "Rich Title", transform=fig.transFigure, ha="center", va="top", color="red")
    fig.add_artist(title)
    fig.canvas.draw()
    obs, rgba = record(fig)
    ink = _port_ink(fig, "fake_port", rgba, obs)
    plt.close(fig)
    assert ink is not None and ink > 0
