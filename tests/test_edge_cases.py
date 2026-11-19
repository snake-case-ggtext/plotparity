"""Edge cases that bit us during the research (see AGENTS.md log). Fast: unit-level unless marked."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "src"))

from plotparity import adapters, references  # noqa: E402
from plotparity.cases import CaseError, expand_data, load_yaml, parse_case, select  # noqa: E402
from plotparity.compare import ProfileError, evaluate_case, load_profile, style_checks  # noqa: E402
from plotparity.engines.python.worker import classify  # noqa: E402
from plotparity.project import Library, Project  # noqa: E402

FIXTURES = TESTS / "fixtures"                     # a 5-case slice of plotparity-examples' ggtext library
PROJECT = FIXTURES / "projects" / "ggtext"
LIBRARY = FIXTURES / "cases" / "ggtext"
# The port the fixture project renders: a local checkout (offline, fast) or the pinned upstream commit.
PORT = os.environ.get("PLOTPARITY_TEST_PORT",
                      "git+https://github.com/snake-case-ggtext/plotnine-ggtext@294826676a22e53b9dec06f0789b16610ddccfc1")
ADAPTER = adapters.load("ggtext")


# ---------- YAML / data ----------

def test_yaml_only_true_false_are_booleans():
    d = load_yaml("y: [yes, no, on, off, y, n]\nn: 1\nb: true\nc: False")
    assert d == {"y": ["yes", "no", "on", "off", "y", "n"], "n": 1, "b": True, "c": False}


def test_recycling_and_types():
    assert expand_data({"x": 1, "y": [1, 2]}, Path("/a")) == {"x": [1, 1], "y": [1, 2]}
    assert expand_data({"p": "{assets}/f.png"}, Path("/a")) == {"p": ["/a/f.png"]}
    with pytest.raises(CaseError, match="does not divide"):
        expand_data({"x": [1, 2], "y": [1, 2, 3]}, Path("."))
    with pytest.raises(CaseError, match="str/int/float/bool/null"):
        expand_data({"x": [{"a": 1}]}, Path("."))


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R not installed")
def test_r_receives_identical_table(tmp_path):
    """Columns named y / n / yes and null values must arrive in R exactly as Python sees them."""
    table = expand_data(load_yaml("y: [1, 2]\nn: [yes, no]\n'yes': [null, 3]"), Path("."))
    job = tmp_path / "t.json"
    job.write_text(json.dumps(table))
    code = ('library(jsonlite); d <- fromJSON(commandArgs(TRUE)[1], simplifyVector=TRUE, simplifyDataFrame=FALSE);'
            'df <- as.data.frame(lapply(d, function(v) unlist(lapply(v, function(x) if (is.null(x)) NA else x))),'
            'stringsAsFactors=FALSE, check.names=FALSE); cat(toJSON(list(names=names(df), n=df$n, yes=df$yes)))')
    out = subprocess.run(["Rscript", "-e", code, str(job)], capture_output=True, text=True, check=True).stdout
    got = json.loads(out)
    assert got["names"] == ["y", "n", "yes"]
    assert got["n"] == ["yes", "no"]
    assert got["yes"] == ["NA", 3] or got["yes"][1] == 3


def _write_case(tmp_path, front: str, body="\n```r\n1\n```\n\n```python plotnine_ggtext\n1\n```\n", name="c1"):
    p = tmp_path / f"{name}.md"
    p.write_text(f"---\n{front}---\n# x\nwhy\n{body}")
    return p


def test_case_file_errors(tmp_path):
    with pytest.raises(CaseError, match="must equal the file name"):
        parse_case(_write_case(tmp_path, "id: other\n"))
    with pytest.raises(CaseError, match="unknown front-matter keys"):
        parse_case(_write_case(tmp_path, "id: c1\nfeatures: [a]\n"))
    with pytest.raises(CaseError, match="projects assign splits"):
        parse_case(_write_case(tmp_path, "id: c1\nsplit: dev\n"))
    with pytest.raises(CaseError, match="needs a ```r block"):
        parse_case(_write_case(tmp_path, "id: c1\n", body="\n```python\n1\n```\n"))


def test_python_blocks_tagged_by_port(tmp_path):
    body = "\n```r\nR\n```\n\n```python plotnine_ggtext\nA\n```\n\n```python\nDEFAULT\n```\n"
    c = parse_case(_write_case(tmp_path, "id: c1\n", body=body))
    assert c.python_for("plotnine_ggtext") == "A"
    assert c.python_for("ggtext") == "DEFAULT"
    c2 = parse_case(_write_case(tmp_path, "id: c1\n", body="\n```r\nR\n```\n\n```python plotnine_ggtext\nA\n```\n"))
    assert c2.python_for("ggtext") is None            # → needs_translation


def test_selection():
    proj = Project.load(PROJECT)
    cases = proj.cases()
    dev = proj.select("dev")
    assert dev and all(proj.split_of(cases[c]) == "dev" for c in dev)
    assert proj.select("richtext_feature_*") == ["richtext_feature_matrix_css", "richtext_feature_matrix_scripts"]
    assert set(proj.select("geom_textbox/*")) == {c for c, v in cases.items() if v.relpath.startswith("geom_textbox/")}
    with pytest.raises(CaseError, match="matches no case"):
        select(cases, "dev,typo_case", proj.splits)


# ---------- error attribution ----------

def _raise(exc):
    try:
        raise exc
    except BaseException as e:  # noqa: BLE001
        return e


def _classify(exc, stage):
    try:
        raise exc
    except BaseException as e:  # noqa: BLE001
        return classify(e, stage, "/ports/plotnine_ggtext", "plotnine_ggtext", {"element_markdown"})["kind"]


def test_error_attribution():
    assert _classify(NameError("name 'element_markdown' is not defined", name="element_markdown"), "build") == "missing_api"
    assert _classify(NameError("name 'typo' is not defined", name="typo"), "build") == "case_error"
    # the `margin` case-authoring bug: an import error from plotnine, no port frame -> case_error, not a port finding
    assert _classify(ImportError("cannot import name 'margin' from 'plotnine'"), "build") == "case_error"
    assert _classify(ModuleNotFoundError("No module named 'markdown'", name="markdown"), "draw") == "environment_error"
    assert _classify(ValueError("Invalid RGBA argument: 'red3'"), "draw") == "render_error"
    assert _classify(ValueError("plotnine-ggtext has encountered a tag that isn't supported yet: <img>"), "build") == "render_error"


# ---------- comparison ----------

def _profile():
    return load_profile(ADAPTER, {})


def test_profile_rejects_unknown_check():
    with pytest.raises(ProfileError, match="unknown check"):
        load_profile(ADAPTER, {"txt_color": {"tol": 1}})


def _run(text, color="#000000", weight="normal"):
    return {"text": text, "color_hex": color, "alpha": 1.0, "fontweight": weight, "fontstyle": "normal",
            "fontsize_pt": 11.0}


def test_run_alignment_by_text_not_index():
    ref = {"text_runs": [_run("a"), _run("b", weight="bold"), _run("c")]}
    cand = {"text_runs": [_run("extra"), _run("a"), _run("b", weight="bold"), _run("c")]}   # one extra run in front
    found = []
    style_checks(ref, cand, _profile(), lambda check, *a, **k: found.append(check), {})
    assert found == []          # index alignment would have compared a↔extra, b↔a (weight!) …


def test_same_text_sup_sub_pairing():
    def line(tokens):
        return [{"tokens": [{"text": t, "voff_pt": v, "is_small_script": s} for t, v, s in tokens]}]
    ref = {"lines": line([("H", 0, False), ("2", -3.0, True), ("mc", 0, False), ("2", 3.0, True)])}
    good = {"lines": line([("H", 0, False), ("2", -3.2, True), ("mc", 0, False), ("2", 3.1, True)])}
    found = []
    ADAPTER.sup_sub_offset(ref, good, {"tol": 1.5}, lambda *a, **k: found.append(a), {})
    assert found == []


class _Case:
    id, relpath, why = "c", "x/c", ""
    expect = {"components": 1, "frame": True}
    measurable = True


def _ref_obs():
    obs = json.loads((LIBRARY / "_references" / "richtext_example_markup" / "observations.json").read_text())
    return {"observations": obs}


def test_unassigned_candidate_drawing_fails_closed(tmp_path):
    from plotparity.runner import run
    rep, _, _ = run(_project_copy(tmp_path), "richtext_example_markup", tmp_path / "out")
    assert rep["cases"][0]["status"] == "pass"
    obs = json.loads((tmp_path / "out" / "render" / "richtext_example_markup" / "observations.json").read_text())
    rich = next(e for e in obs["events"] if e["kind"] == "text" and "richtext" in e["groups"])
    obs["events"].append({"kind": "draw_markers", "paint_index": 9999, "groups": rich["groups"],
                          "group_ids": rich["group_ids"], "owner": rich["owner"], "clip_bbox_norm_tl": None})
    res = evaluate_case(_Case(), ADAPTER, _profile(), [], _ref_obs(), {"observations": obs}, package="plotnine_ggtext")
    assert res["status"] == "fail"
    assert any(f["check"] == "unexpected_drawing" for f in res["findings"])


def _empty_obs(port_ink_px):
    return {"events": [], "owners": [], "figure_px": [640, 480], "dpi": 72.0, "panels_norm_tl": [[0.1, 0.1, 0.9, 0.9]],
            "port_ink_px": port_ink_px}


def test_empty_candidate_without_port_ink_is_a_fail():
    res = evaluate_case(_Case(), ADAPTER, _profile(), [], _ref_obs(), {"observations": _empty_obs(0)},
                        package="plotnine_ggtext")
    assert res["status"] == "fail"
    assert {"component_count", "missing_component"} <= {f["check"] for f in res["findings"]}


def test_empty_candidate_with_port_ink_is_extractor_blind():
    """The port's artists changed panel pixels but nothing was extracted: unmeasured, never pass, not blamed on the port."""
    for ink in (37, None):                              # None: the worker could not measure it -> also unmeasured
        res = evaluate_case(_Case(), ADAPTER, _profile(), [], _ref_obs(), {"observations": _empty_obs(ink)},
                            package="plotnine_ggtext")
        assert res["status"] == "unmeasured"
        assert [f["check"] for f in res["findings"]] == ["extractor_blind"]


# ---------- references ----------

def _project_copy(tmp_path) -> Project:
    """Copy of the fixture project AND its library (so tests may edit cases), rendering PORT."""
    dst = tmp_path / "fx"
    shutil.copytree(FIXTURES, dst)
    proj = dst / "projects" / "ggtext"
    toml = (proj / "parity.toml").read_text().replace('"PLOTPARITY_TEST_PORT"', json.dumps(PORT))
    (proj / "parity.toml").write_text(toml)
    return Project.load(proj)


def test_stale_reference_detected(tmp_path):
    proj = _project_copy(tmp_path)
    lib = proj.library
    case_file = lib.root / "geom_richtext" / "richtext_rotated_colors.md"
    case_file.write_text(case_file.read_text().replace("hjust = 0, vjust = 0.5", "hjust = 0.5, vjust = 0.5"))
    c = lib.cases()["richtext_rotated_colors"]
    assert references.load(lib, ADAPTER, c, {})["error"]["kind"] == "stale_reference"
    # changing only a python block does NOT invalidate the R reference (references belong to the R side)
    c2f = lib.root / "geom_richtext" / "richtext_example_markup.md"
    c2f.write_text(c2f.read_text().replace('fill="black", size=2', 'fill="black", size=2.0'))
    assert "error" not in references.load(lib, ADAPTER, lib.cases()["richtext_example_markup"], {})


def test_needs_translation_is_unmeasured(tmp_path):
    proj = _project_copy(tmp_path)
    f = proj.library.root / "geom_richtext" / "richtext_rotated_colors.md"
    f.write_text(f.read_text().replace("```python plotnine_ggtext", "```python some_other_port"))
    rep, md, code = __import__("plotparity.runner", fromlist=["run"]).run(
        proj, "richtext_rotated_colors", tmp_path / "out")
    assert code == 2 and rep["cases"][0]["status"] == "unmeasured"
    assert rep["cases"][0]["findings"][0]["check"] == "needs_translation"


@pytest.mark.slow
def test_loop_fails_iteration_when_harness_changes(tmp_path):
    from argparse import Namespace

    from plotparity import loop
    proj = _project_copy(tmp_path)
    ns = Namespace(action="init", name="t", note="", goal="", summary="", source=None)
    assert loop.dispatch(proj, ns) == 0
    kd = proj.known_differences_path()
    kd.write_text(kd.read_text() + '\n[[difference]]\ncase = "richtext_example_markup"\ncheck = "text_color"\nreason = "agent"\n')
    ns.action, ns.note = "iterate", "edited the allowlist"
    assert loop.dispatch(proj, ns) == 1
    last = json.loads((proj.runs_dir / "loops" / "t" / "loop.jsonl").read_text().splitlines()[-1])
    assert "known_differences" in last["harness_changed"]


def test_git_port_resolves_pinned_tag(tmp_path, monkeypatch):
    from plotparity import ports
    repo = tmp_path / "repo"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "src" / "pkg" / "__init__.py").write_text("X = 1\n")
    for cmd in (["init", "-q"], ["add", "."], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c"], ["tag", "v1"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True)
    monkeypatch.setattr(ports, "CACHE", tmp_path / "cache")
    port = ports.resolve(f"git+file://{repo}@v1", "pkg", tmp_path)
    assert (port.package_parent / "pkg" / "__init__.py").read_text() == "X = 1\n"
    assert port.commit and port.dirty is False
    with pytest.raises(ports.PortError):
        ports.resolve("git+file://nowhere", "pkg", tmp_path)


def test_insets_use_the_library_canvas_not_a_constant():
    """A 1280x960 canvas must give twice the pt insets of 640x480 for the same normalized geometry."""
    from plotparity.adapters.ggtext.adapter import _text_insets
    rect = {"bbox_norm_tl": [0.4, 0.4, 0.6, 0.5], "unrotated_width_pt": 128.0, "unrotated_height_pt": 48.0}
    runs = [{"x_norm": 0.41, "y_norm": 0.46, "fontsize_pt": 11.0}]
    small = _text_insets(rect, runs, 0.0, (640.0, 480.0))
    big = _text_insets({**rect, "unrotated_width_pt": 256.0, "unrotated_height_pt": 96.0}, runs, 0.0, (1280.0, 960.0))
    assert all(abs(big[k] - 2 * small[k]) < 1e-6 for k in small)
