"""Regressions for the 2026-11-19 reviews (Codex: FEEDBACK.md; Gemini: FEEDBACK-gemini.md in the workspace).

Each test names the false pass or gap it pins. Rendered mutations of the real port live in plotparity-examples
(`projects/ggtext/mutations`, run by `plotparity selftest`); these tests edit recorded observations directly, or
draw small matplotlib figures in-process, so they are fast and need no second port.
"""

from __future__ import annotations

import copy
import json
import math
import subprocess
import sys
import tomllib
from pathlib import Path

import numpy as np
import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "src"))

from matplotlib.artist import Artist  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from plotparity import adapters, references  # noqa: E402
from plotparity import report as rpt  # noqa: E402
from plotparity.compare import ProfileError, evaluate_case, load_profile, unused_known  # noqa: E402

from test_edge_cases import _Case, _project_copy, _ref_obs  # noqa: E402

ADAPTER = adapters.load("ggtext")
PKG = "plotnine_ggtext"


# ---------- shared: one clean render of the fixture's richtext_example_markup ----------

@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    from plotparity.runner import run
    tmp = tmp_path_factory.mktemp("clean")
    rep, _, _ = run(_project_copy(tmp), "richtext_example_markup", tmp / "out")
    assert rep["cases"][0]["status"] == "pass"
    d = tmp / "out" / "render" / "richtext_example_markup"
    return json.loads((d / "observations.json").read_text()), d / "candidate.png"


def _eval(obs, png=None, profile=None, known=()):
    cand = {"observations": obs, **({"png": str(png)} if png else {})}
    return evaluate_case(_Case(), ADAPTER, profile or load_profile(ADAPTER, {}), list(known), _ref_obs(), cand,
                         package=PKG)


def _fails(res) -> set[str]:
    return {f["check"] for f in res["findings"] if f["status"] == "fail" and f["severity"] == "gating"}


def _port(obs, kind):
    return [e for e in obs["events"] if e["kind"] == kind and e["owner"] is not None
            and obs["owners"][e["owner"]]["module"].startswith(PKG)]


def _owner_index(obs, cls_suffix):
    return next(i for i, o in enumerate(obs["owners"]) if o["class"].endswith(cls_suffix))


# ---------- A. every candidate draw event is accounted for (Codex §1) ----------

def test_clean_render_accounts_for_every_event(clean):
    obs, png = clean
    res = _eval(obs, png)
    assert res["status"] == "pass", res["findings"]
    d = res["drawing"]
    assert d["port_events"] == d["port_assigned"] > 0 and d["unowned_events"] == 0
    assert d["other_layer_events"] > 0                   # axes, gridlines, geom_point: permitted, counted


def test_port_markers_in_their_own_group_fail(clean):
    """Codex: an independent draw_markers call in its own group used to pass (its partition had no text)."""
    obs = copy.deepcopy(clean[0])
    t = _port(obs, "text")[0]
    k = t["groups"].index("axes")
    obs["events"].append({"kind": "draw_markers", "paint_index": 10_000, "owner": t["owner"],
                          "groups": t["groups"][:k + 1] + ["line2d"], "group_ids": t["group_ids"][:k + 1] + [10_000],
                          "clip_bbox_norm_tl": None})
    assert "unexpected_drawing" in _fails(_eval(obs))


def test_port_rejected_open_path_fails(clean):
    """Codex: a path the segmentation rejects (open, or not inside the panel) was silently dropped."""
    obs = copy.deepcopy(clean[0])
    stray = dict(_port(obs, "path")[0], paint_index=10_001, is_closed=False)
    obs["events"].append(stray)
    res = _eval(obs)
    assert "unexpected_drawing" in _fails(res)
    assert "path by plotnine_ggtext" in next(f["message"] for f in res["findings"] if f["check"] == "unexpected_drawing")


def test_other_layers_are_permitted_and_counted(clean):
    obs = copy.deepcopy(clean[0])
    obs["events"].append({"kind": "draw_markers", "paint_index": 10_002, "owner": _owner_index(obs, "PathCollection"),
                          "groups": [], "group_ids": [], "clip_bbox_norm_tl": None})
    res = _eval(obs, clean[1])
    assert res["status"] == "pass"
    assert res["drawing"]["other_layer_events"] == _eval(clean[0])["drawing"]["other_layer_events"] + 1


def test_drawing_by_no_artist_fails(clean):
    obs = copy.deepcopy(clean[0])
    obs["events"].append({"kind": "draw_markers", "paint_index": 10_003, "owner": None, "groups": [], "group_ids": [],
                          "clip_bbox_norm_tl": None})
    assert "unexpected_drawing" in _fails(_eval(obs))


def test_observations_without_owners_are_an_infrastructure_error(clean):
    obs = copy.deepcopy(clean[0])
    del obs["owners"]
    res = _eval(obs)
    assert res["status"] == "infrastructure_error" and _fails(res) == set()


class _PortArtist(Artist):
    """Stands in for a port's artist: its module (this test module) is the 'port package' below."""

    def draw(self, renderer):
        r = Rectangle((10, 10), 30, 30, facecolor="red")      # a sub-artist drawn by the port artist
        r.set_figure(self.figure)
        r.draw(renderer)


def test_probe_attributes_events_to_the_top_level_artist():
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    from plotparity.engines.python.probe import is_port_module, record
    fig = Figure(figsize=(2, 2), dpi=72)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot()
    ax.plot([0, 1], [0, 1])
    ax.add_artist(_PortArtist())
    fig.canvas.draw()
    obs, rgba = record(fig)
    assert rgba.shape == (144, 144, 4)
    assert all(e["owner"] is not None for e in obs["events"])
    mine = [e for e in obs["events"] if is_port_module(obs["owners"][e["owner"]]["module"], __name__)]
    assert [e["kind"] for e in mine] == ["path"]            # the Rectangle, attributed to the artist that drew it
    assert obs["owners"][mine[0]["owner"]]["class"].endswith("_PortArtist")


# ---------- B. visibility (Codex §2, Gemini 1.1) ----------

@pytest.mark.parametrize("alpha", [0.0, 0.05])
def test_transparent_text_fails_text_alpha(clean, alpha):
    obs = copy.deepcopy(clean[0])
    for e in _port(obs, "text"):
        e["alpha"] = alpha
    res = _eval(obs)
    assert "text_alpha" in _fails(res)
    assert "text_color" not in _fails(res)              # colour kept: alpha 0 no longer turns into "#000000"


def test_alpha_is_effective_alpha_on_both_engines():
    """R puts alpha into #RRGGBBAA (times gpar alpha); the probe records an opaque colour and the effective alpha."""
    from plotparity.adapters.ggtext.adapter import _normalize_box
    run = {"text": "w", "x_norm": 0.5, "y_norm": 0.5, "fontsize_pt": 11.0, "angle_deg": 0.0}
    r = _normalize_box({"text_runs": [dict(run, color="#00008B99", alpha=1.0)]}, "r")["text_runs"][0]
    p = _normalize_box({"text_runs": [dict(run, color_hex="#00008b", alpha=0.6)]}, "python")["text_runs"][0]
    assert (r["color_hex"], r["alpha"]) == (p["color_hex"], p["alpha"]) == ("#00008b", 0.6)
    none = _normalize_box({"text_runs": [dict(run, color="none", alpha=1.0)]}, "r")["text_runs"][0]
    assert (none["color_hex"], none["alpha"]) == ("none", 0.0)          # Gemini 1.1: never coerced to #000000


def test_probe_records_graphics_context_alpha_overrides():
    """A forced artist alpha overrides the colour's alpha in Agg, for text, stroke and fill alike."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    from plotparity.engines.python.probe import record
    fig = Figure(figsize=(2, 2), dpi=72)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot()
    ax.text(0.2, 0.2, "own", color=(0, 0, 1, 0.3))
    ax.text(0.2, 0.6, "forced", color=(0, 0, 1, 0.3), alpha=0.5)
    ax.add_patch(Rectangle((0.5, 0.1), 0.2, 0.2, facecolor=(1, 0, 0, 0.4), edgecolor="black"))
    ax.add_patch(Rectangle((0.5, 0.5), 0.2, 0.2, facecolor=(1, 0, 0, 0.4), edgecolor="black", alpha=0.7))
    fig.canvas.draw()
    obs, _ = record(fig)
    text = {e["text"]: e["alpha"] for e in obs["events"] if e["kind"] == "text"}
    assert text["own"] == 0.3 and text["forced"] == 0.5
    faces = sorted((e["face_alpha"], e["edge_alpha"]) for e in obs["events"]
                   if e["kind"] == "path" and e["facecolor_hex"] == "#ff0000")
    assert faces == [(0.4, 1.0), (0.7, 0.7)]


def test_frame_alpha_change_fails_fill_color(clean):
    obs = copy.deepcopy(clean[0])
    for e in _port(obs, "path"):
        e["face_alpha"] = 0.25
    res = _eval(obs)
    assert "fill_color" in _fails(res)
    assert all(f["component"].endswith("/alpha") for f in res["findings"] if f["check"] == "fill_color")


def test_clipped_text_fails_text_visible(clean):
    """Codex: every text artist clipped to a 1x1 px box (nothing shows) passed."""
    obs = copy.deepcopy(clean[0])
    for e in _port(obs, "text"):
        e["clip_bbox_norm_tl"] = [0.0, 1.0 - 1 / 480, 1 / 640, 1.0]
    assert "text_visible" in _fails(_eval(obs))


def test_ink_present_needs_ink_in_the_candidate_png(clean, tmp_path):
    from PIL import Image
    obs, png = clean
    blank = tmp_path / "blank.png"
    Image.new("RGB", Image.open(png).size, "white").save(blank)
    assert _eval(obs, png)["status"] == "pass"
    assert "ink_present" in _fails(_eval(obs, blank))
    no_png = _eval(obs, tmp_path / "missing.png")
    assert any(f["check"] == "ink_present" and f["status"] == "unmeasured" for f in no_png["findings"])


# ---------- C1. non-finite numbers (Codex §3) ----------

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, "6", True])
def test_profile_rejects_non_finite_or_negative_numbers(bad):
    with pytest.raises(ProfileError, match="finite number"):
        load_profile(ADAPTER, {"text_color": {"tol": bad}})


def test_profile_nan_from_toml_is_rejected():
    override = tomllib.loads("[checks.text_color]\ntol = nan\n")["checks"]      # TOML accepts `nan`
    with pytest.raises(ProfileError):
        load_profile(ADAPTER, override)


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
@pytest.mark.parametrize("field", ["fontsize_pt", "x_norm"])
def test_nan_in_candidate_observations_is_an_infrastructure_error(clean, field):
    obs = copy.deepcopy(clean[0])
    _port(obs, "text")[0][field] = float("nan")
    res = _eval(obs)
    assert res["status"] == "infrastructure_error"
    assert [f["check"] for f in res["findings"] if f["status"] == "infrastructure_error"] == ["check_error"]


# ---------- C2. reference freshness covers asset contents (Codex §4) ----------

def test_asset_edit_makes_references_stale(tmp_path):
    proj = _project_copy(tmp_path)
    lib = proj.library
    case = lib.cases()["richtext_example_markup"]
    assert "error" not in references.load(lib, ADAPTER, case, {})
    (lib.assets_dir / ".DS_Store").write_bytes(b"finder noise")              # dotfiles are ignored
    assert "error" not in references.load(lib, ADAPTER, case, {})
    asset = next(p for p in lib.assets_dir.iterdir() if not p.name.startswith("."))
    asset.write_bytes(asset.read_bytes() + b"\0")
    assert references.load(lib, ADAPTER, case, {})["error"]["kind"] == "stale_reference"


# ---------- C3. --previous semantics (Codex §5) ----------

def _recoloured(obs, word="bold.", colour="#ff00ff"):
    obs = copy.deepcopy(obs)
    for e in _port(obs, "text"):
        if e["text"].endswith(word):
            e["color_hex"] = colour
    return obs


def _report(res, harness="h1"):
    return {"cases": [res], "harness": {"all": harness, "tool": harness}}


def test_previous_failure_now_accepted_or_disabled_is_not_fixed(clean):
    obs = _recoloured(clean[0])
    prev = _report(_eval(obs))
    assert "text_color" in _fails(prev["cases"][0])
    known = [{"case": "c", "check": "text_color", "reason": "accepted"}]
    now = _report(_eval(obs, known=known))
    rpt.tag_against_previous(now, prev)
    assert {r["change"] for r in now["resolved_since_previous"]} == {"accepted"}
    off = load_profile(ADAPTER, {"text_color": {"severity": "off"}})
    now = _report(_eval(obs, profile=off))
    rpt.tag_against_previous(now, prev)
    assert {r["change"] for r in now["resolved_since_previous"]} == {"disabled"}


def test_previous_fix_next_to_an_inventory_failure_is_fixed(clean):
    """Codex: any inventory failure in the case turned every resolved finding into `gone`."""
    prev = _report(_eval(_recoloured(clean[0])))
    obs = copy.deepcopy(clean[0])                       # colour fixed, but an extra label appears elsewhere
    t = _port(obs, "text")[0]
    k = t["groups"].index("axes")
    obs["events"].append(dict(t, text="extra", paint_index=10_004, x_norm=0.6, y_norm=0.5,
                              groups=t["groups"][:k + 1] + ["text"], group_ids=t["group_ids"][:k + 1] + [10_004]))
    now = _report(_eval(obs), harness="h2")
    assert {"unexpected_component", "unexpected_drawing"} & _fails(now["cases"][0]) or "component_count" in _fails(now["cases"][0])
    rpt.tag_against_previous(now, prev)
    assert {r["change"] for r in now["resolved_since_previous"]} == {"fixed"}
    assert now["previous_harness_changed"] == ["tool"]


# ---------- C4. git port cache is atomic (Codex §6) ----------

def test_git_cache_recovers_after_a_failed_fetch(tmp_path, monkeypatch):
    """Codex's offline repro: resolve @v1 before the tag exists, create it, resolve again with the same cache."""
    from plotparity import ports
    repo = tmp_path / "repo"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "src" / "pkg" / "__init__.py").write_text("X = 1\n")
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    for cmd in (["init", "-q"], ["add", "."], ["commit", "-qm", "c"]):
        subprocess.run([*git, *cmd], cwd=repo, check=True)
    monkeypatch.setattr(ports, "CACHE", tmp_path / "cache")
    with pytest.raises(ports.PortError, match="could not fetch"):
        ports.resolve(f"git+file://{repo}@v1", "pkg", tmp_path)
    subprocess.run([*git, "tag", "v1"], cwd=repo, check=True)
    port = ports.resolve(f"git+file://{repo}@v1", "pkg", tmp_path)
    assert (port.package_parent / "pkg" / "__init__.py").exists() and port.dirty is False
    assert not list((tmp_path / "cache").rglob(".tmp-*"))


def test_package_search_only_uses_the_parent_when_pointed_at_the_package(tmp_path):
    from plotparity import ports
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "unrelated").mkdir()
    with pytest.raises(ports.PortError):               # a sibling folder must not be picked up
        ports.resolve(str(tmp_path / "unrelated"), "pkg", tmp_path)
    assert ports.resolve(str(tmp_path / "pkg"), "pkg", tmp_path).package_parent == tmp_path


# ---------- C5. loop holdout checks the harness (Gemini 1.2) ----------

@pytest.mark.slow
def test_loop_holdout_fails_when_harness_changes(tmp_path, capsys):
    from argparse import Namespace

    from plotparity import loop
    proj = _project_copy(tmp_path)
    ns = Namespace(action="init", name="t", note="", goal="", summary="", source=None)
    assert loop.dispatch(proj, ns) == 0
    ns.action = "holdout"
    assert loop.dispatch(proj, ns) == 0
    kd = proj.known_differences_path()
    kd.write_text(kd.read_text() + '\n[[difference]]\ncase = "textbox_native_no_wrap"\ncheck = "padding"\nreason = "x"\n')
    assert loop.dispatch(proj, ns) == 1
    last = json.loads((proj.runs_dir / "loops" / "t" / "loop.jsonl").read_text().splitlines()[-1])
    assert last["holdout"] == 2 and "known_differences" in last["harness_changed"] and last["status"] == "fail"


# ---------- C6. one draw gives the PNG and the observations (Gemini 1.4) ----------

def test_single_pass_png_matches_savefig(tmp_path):
    import pandas as pd
    from PIL import Image
    from plotnine import aes, geom_point, geom_text, ggplot

    from plotparity.engines.python.worker import configure_mpl, draw_plot

    def plot():
        df = pd.DataFrame({"x": [1, 2, 3], "y": [1, 3, 2], "l": ["a", "bb", "ccc"]})
        return ggplot(df, aes("x", "y", label="l")) + geom_point() + geom_text(nudge_y=0.1)

    configure_mpl(72, 640, 480)
    obs = draw_plot(plot(), tmp_path / "one.png", 72, 640, 480, package="")
    fig = plot().draw()
    fig.set_dpi(72)
    fig.set_size_inches(640 / 72, 480 / 72)
    fig.canvas.draw()
    fig.savefig(tmp_path / "two.png", dpi=72, format="png")
    a, b = (np.asarray(Image.open(tmp_path / n)) for n in ("one.png", "two.png"))
    assert a.shape == b.shape and (a == b).all()
    assert obs["events"] and obs["port_ink_px"] == 0         # no port artists: nothing to hide


# ---------- C8. unused known differences, per exact entry (Gemini 2.2) ----------

def test_unused_known_difference_is_tracked_per_entry(clean):
    known = [{"case": "c", "check": "text_color", "component": "box[1:*", "reason": "used"},
             {"case": "c", "check": "text_color", "component": "box[9:*", "reason": "typo"}]
    res = _eval(_recoloured(clean[0], word="bold.", colour="#ff00ff"), known=known)
    assert any(f["status"] == "known_difference" for f in res["findings"])
    assert unused_known(known, [res], ["c"]) == [known[1]]


# ---------- C9 / C10. small items ----------

def test_raw_markup_leak_patterns():
    from plotparity.adapters.ggtext.adapter import RAW_MARKUP
    for tok in ("<strong>x", "</span>", "<em>", "<img", "**bold", "H~2~O", "E=mc^2^", "<BR>"):
        assert RAW_MARKUP.search(tok), tok
    for tok in ("x<2", "~5", "a^b", "*italic", "bold."):
        assert not RAW_MARKUP.search(tok), tok


def test_raw_markup_leak_ignores_tokens_the_reference_draws_too():
    found = []
    ADAPTER.raw_markup_leak({"words": ["E=mc^2^"]}, {"words": ["E=mc^2^", "<b>x"]}, {}, lambda *a, **k: found.append(a), {})
    assert [a[2] for a in found] == ["<b>x"]


def test_line_breaks_compares_per_line_words():
    def lines(*ls):
        return [{"tokens": [{"text": w} for w in ln.split()]} for ln in ls]
    found = []
    words = "a b c d".split()
    ADAPTER.line_breaks({"lines": lines("a b", "c d"), "words": words}, {"lines": lines("a b c", "d"), "words": words},
                        {}, lambda *a, **k: found.append(k.get("sub")), {})
    assert found == ["line[1]", "line[2]"]
    assert ADAPTER.line_breaks({"lines": lines("a b"), "words": words}, {"lines": lines("a", "b"), "words": words},
                               {}, None, {}) is False            # different line counts: line_count's job


def test_usage_errors_exit_3(tmp_path):
    from plotparity.cli import main
    proj = _project_copy(tmp_path)
    assert main(["new-case", str(tmp_path / "no_cases_here" / "x.md")]) == 3          # not inside a cases folder
    assert main(["loop", "iterate", "--config", str(proj.config_path), "--name", "nope", "--note", "x"]) == 3


def test_non_finite_delta_is_never_a_silent_pass():
    from plotparity.compare import CheckError, exceeds
    assert exceeds(2.0, 1.0, "x") and not exceeds(1.0, 1.0, "x")
    with pytest.raises(CheckError):
        exceeds(math.nan, 1.0, "x")
