"""Candidate render worker. Runs INSIDE the candidate interpreter:

    PYTHONPATH=<port parent>:<plotparity/src> python -m plotparity.engines.python.worker job.json

For each case: reset matplotlib/plotnine state, BUILD the plot from the case's python block, DRAW it
(one recorded draw gives both the PNG and the draw-call observations). Writes <out>/<case>/{candidate.png, observations.json} or error.json.

Error attribution (DESIGN §4):
  ModuleNotFoundError for a non-port module / import check failure  -> environment_error
  NameError for a name in candidate.api                              -> missing_api      (port)
  any exception while DRAWING (drawing runs port code)               -> render_error     (port)
  exception while BUILDING with a traceback frame in the port        -> render_error     (port)
  other exception while BUILDING                                     -> case_error       (infrastructure)
"""

from __future__ import annotations

import ast
import json
import sys
import time
import traceback
import warnings
from pathlib import Path


def configure_mpl(dpi: int, width_px: int, height_px: int):
    import matplotlib as mpl
    from plotnine.options import set_option
    from plotnine.themes.theme import DEFAULT_RCPARAMS

    mpl.use("Agg")
    mpl.rcdefaults()
    DEFAULT_RCPARAMS["font.monospace"] = ["DejaVu Sans Mono"]
    DEFAULT_RCPARAMS["font.sans-serif"] = ["DejaVu Sans"]
    DEFAULT_RCPARAMS["font.serif"] = ["DejaVu Serif"]
    mpl.rcParams["font.sans-serif"] = ["DejaVu Sans"]
    mpl.rcParams["text.hinting"] = "auto"
    mpl.rcParams["text.antialiased"] = True
    set_option("dpi", dpi)
    set_option("figure_size", (width_px / dpi, height_px / dpi))


def environment(package: str | None = None) -> dict:
    import hashlib

    import matplotlib
    import plotnine

    ttf = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    fonts = {n: hashlib.sha256((ttf / n).read_bytes()).hexdigest()[:16]
             for n in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf")}
    return {"python": sys.version.split()[0], "executable": sys.executable, "plotnine": plotnine.__version__,
            "matplotlib": matplotlib.__version__, "ttf_dir": str(ttf), "font_hashes": fonts,
            "plotnine_file": plotnine.__file__}


def _namespace(port):
    import numpy as np
    import pandas as pd
    import plotnine
    from plotnine.themes.elements import margin

    ns = {k: getattr(plotnine, k) for k in getattr(plotnine, "__all__", dir(plotnine)) if not k.startswith("_")}
    ns.update(pd=pd, np=np, margin=margin)
    ns.update({k: getattr(port, k) for k in dir(port) if not k.startswith("_")})
    return ns


def _in_port(tb_text: str, port_dir: str, package: str, msg: str) -> bool:
    return port_dir in tb_text or package in msg or package.replace("_", "-") in msg


def _err(kind: str, exc: BaseException, stage: str) -> dict:
    return {"kind": kind, "stage": stage, "type": type(exc).__name__, "message": str(exc),
            "traceback": traceback.format_exc()[-4000:]}


def classify(exc: BaseException, stage: str, port_dir: str, package: str, api: set[str]) -> dict:
    tb = traceback.format_exc()
    if isinstance(exc, ModuleNotFoundError) and exc.name and not exc.name.startswith(package):
        return _err("environment_error", exc, stage)
    if stage == "build" and isinstance(exc, NameError) and getattr(exc, "name", None) in api:
        return _err("missing_api", exc, stage)
    if stage == "draw" or _in_port(tb, port_dir, package, str(exc)):
        return _err("render_error", exc, stage)
    return _err("case_error", exc, stage)


def build_plot(code: str, data: dict | None, port, fname: str):
    import pandas as pd

    ns = _namespace(port)
    ns["df"] = pd.DataFrame(data) if data else None
    tree = ast.parse(code, filename=fname, mode="exec")
    if not tree.body or not isinstance(tree.body[-1], ast.Expr):
        raise SyntaxError(f"{fname}: the last statement of the python block must be the plot expression")
    last = ast.Expression(tree.body.pop().value)
    exec(compile(tree, fname, "exec"), ns)
    return eval(compile(last, fname, "eval"), ns)


def draw_plot(p, png: Path, dpi: int, width_px: int, height_px: int, package: str = "", adapter: str | None = None):
    """Layout draw, then ONE recorded draw whose Agg buffer is saved as the PNG (image == observations).
    Then a diagnostic draw with the port's top-level artists hidden: `port_ink_px` counts panel pixels the
    port's artists changed (used when the adapter finds no components: ink → extractor blind, none → fail)."""
    import matplotlib.image as mimage
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    from plotparity.engines.python.probe import record

    fig = p.draw()
    if not hasattr(fig.canvas, "get_renderer"):
        FigureCanvasAgg(fig)
    fig.set_dpi(dpi)
    fig.set_size_inches(width_px / dpi, height_px / dpi)
    fig.canvas.draw()                      # lets plotnine's layout engine finalize positions
    obs, rgba = record(fig)
    mimage.imsave(png, rgba, format="png", dpi=dpi)
    hook = _adapter_hook(adapter)
    if hook is not None:                   # adapter-specific Python observations (e.g. plotnine's built layer data)
        obs.update(hook(fig, p))
    obs["port_ink_px"] = _port_ink(fig, package, rgba, obs)
    plt.close(fig)
    return obs


def _port_ink(fig, package: str, rgba, obs: dict) -> int | None:
    """Canvas pixels that differ when the port's top-level artists are hidden (layout frozen so hiding a chrome
    artist cannot reflow axes and shift unrelated pixels)."""
    import numpy as np

    from plotparity.engines.python.probe import is_port_module, top_level_artists

    mine = [a for a, _ in top_level_artists(fig) if is_port_module(type(a).__module__, package) and a.get_visible()]
    if not mine:
        return 0
    base = fig.canvas.get_renderer()
    eng = fig.get_layout_engine()
    for a in mine:
        a.set_visible(False)
    try:
        if eng is not None:
            fig.set_layout_engine("none")
        base.clear()
        fig.draw(base)
        other = np.asarray(base.buffer_rgba())[..., :3].astype(int)
    finally:
        if eng is not None:
            fig.set_layout_engine(eng)
        for a in mine:
            a.set_visible(True)
    return int((np.abs(rgba[..., :3].astype(int) - other).max(axis=-1) > 0).sum())


def _adapter_hook(name: str | None):
    """`observe(fig, plot) -> dict` from plotparity/adapters/<name>/observe_py.py, if the adapter has one. It runs in
    the package's interpreter, so it may only import what that environment has (plotnine, pandas, numpy)."""
    if not name:
        return None
    import importlib
    try:
        mod = importlib.import_module(f"plotparity.adapters.{name}.observe_py")
    except ModuleNotFoundError as exc:
        if exc.name == f"plotparity.adapters.{name}.observe_py":
            return None
        raise
    return mod.observe


def main(job_path: str) -> int:
    job = json.loads(Path(job_path).read_text())
    out = Path(job["out_dir"])
    package, port_dir, api = job["package"], job["port_dir"], set(job.get("api", []))
    r = job["render"]
    env = environment()
    try:
        port = __import__(package)
        pfile = str(Path(port.__file__).resolve())
        if not pfile.startswith(str(Path(port_dir).resolve())):
            raise ImportError(f"{package} imported from {pfile}, not from the candidate source {port_dir} "
                              "(an installed copy is shadowing it)")
        env["port_file"] = pfile
        port_error = None
    except BaseException as exc:  # noqa: BLE001
        port, port_error = None, exc
    (out / "worker_env.json").write_text(json.dumps(env, indent=2))

    for case in job["cases"]:
        cdir = out / case["id"]
        cdir.mkdir(parents=True, exist_ok=True)
        if port_error is not None:
            kind = "environment_error" if isinstance(port_error, ImportError) and "shadowing" in str(port_error) \
                else ("environment_error" if isinstance(port_error, ModuleNotFoundError)
                      and not str(getattr(port_error, "name", "")).startswith(package) else "render_error")
            try:
                raise port_error
            except BaseException as exc:  # noqa: BLE001
                (cdir / "error.json").write_text(json.dumps(_err(kind, exc, "import"), indent=2))
            continue
        import matplotlib.pyplot as plt
        import plotnine

        plt.close("all")
        configure_mpl(r["dpi"], r["width_px"], r["height_px"])
        plotnine.theme_set(plotnine.theme_gray())
        t0 = time.perf_counter()
        stage = "build"
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                p = build_plot(case["code"], case.get("data"), port, case["file"])
                stage = "draw"
                obs = draw_plot(p, cdir / "candidate.png", r["dpi"], r["width_px"], r["height_px"], package,
                                job.get("adapter"))
                obs["warnings"] = sorted({f"{w.category.__name__}: {w.message}" for w in caught})[:20]
                obs["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                (cdir / "observations.json").write_text(json.dumps(obs))
            except BaseException as exc:  # noqa: BLE001
                (cdir / "error.json").write_text(json.dumps(classify(exc, stage, port_dir, package, api), indent=2))
                plt.close("all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
