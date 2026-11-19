"""Theme-element (chrome) extraction: pins the plotnine internals the probe's role tagging relies on
(theme.targets, guides._lookup, tick labels, the layout engine's plot), and the coarse position check."""

import pandas as pd
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from plotnine import aes, facet_wrap, geom_point, ggplot, labs

from plotparity.adapters.ggtext import adapter as A
from plotparity.engines.python.probe import record, role_artists


@pytest.fixture(scope="module")
def recorded():
    df = pd.DataFrame({"x": [1, 2, 3, 4], "y": [1, 3, 2, 4], "g": ["a", "a", "b", "b"], "k": ["p", "q", "p", "q"]})
    p = ggplot(df, aes("x", "y", color="k")) + geom_point() + facet_wrap("g") + labs(title="T", subtitle="S",
                                                                                     caption="C")
    fig = p.draw()
    FigureCanvasAgg(fig)
    fig.canvas.draw()
    obs, _ = record(fig)
    return fig, obs


def test_plotnine_internals_still_map_roles(recorded):
    fig, _ = recorded
    roles = {r for _, r, _ in role_artists(fig)}
    for role in ("plot.title", "plot.subtitle", "plot.caption", "axis.title.x.bottom", "axis.title.y.left",
                 "strip.text.x.top", "legend.title", "legend.text", "axis.text.x.bottom", "axis.text.y.left"):
        assert role in roles, f"{role} not found: plotnine internals changed (see probe.role_artists)"


def test_draw_events_carry_roles(recorded):
    _, obs = recorded
    tagged = {(e["role"], e["text"]) for e in obs["events"] if e["kind"] == "text" and "role" in e}
    assert ("plot.title", "T") in tagged and ("strip.text.x.top", "b") in tagged and ("legend.text", "q") in tagged
    assert not any("error" in r for r in obs["roles"])


def test_strips_ordered_along_the_canvas(recorded):
    _, obs = recorded
    comps, _ = A._chrome_python(obs, (640.0, 480.0))
    strips = sorted((c for c in comps if c["role"] == "strip.text.x.top"), key=lambda c: c["ordinal"])
    assert [c["full_text"] for c in strips] == ["a", "b"]


def _chrome(along, perp=0.05):
    return {"role": "plot.title", "layout_rel": {"side": "top", "along": along, "perp": perp}}


def test_chrome_position_is_coarse():
    found = []
    add = lambda *a, **k: found.append(k.get("sub"))       # noqa: E731
    A.chrome_position(_chrome(0.09), _chrome(0.15), {"tol": 0.10}, add, {})
    assert found == []                                     # small shifts are layout noise
    A.chrome_position(_chrome(0.09), _chrome(0.50), {"tol": 0.10}, add, {})
    assert found == ["along"]                              # left-aligned vs centred
