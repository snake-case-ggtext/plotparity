"""Draw-call intercept probe for matplotlib (research name: Probe 2C).

Wraps the live Agg renderer during ``fig.draw(proxy)`` and records every draw call (draw_path / draw_text /
draw_image decoded; the other routes recorded by name) plus open_group nesting and paint order, on the SAME
renderer and in the SAME draw that produces the PNG. Engine-generic: it knows nothing about any extension.
Adapters turn the raw events into components.

Ownership: during the recording draw, the `draw` of every top-level artist (each child of an Axes, and each
non-Axes child of the Figure) is wrapped, so each event carries `owner`, an index into `observations["owners"]`
(the artist's class). Events drawn outside every top-level artist have `owner: None`. The core uses this to
decide which events the port is responsible for (DESIGN.md §5, "Ownership").

Ported from experiment/probes.py (RecordingRendererProxy); additions: draw_image/draw_tex recording, owner
attribution, effective alpha (graphics-context overrides), `record()` returning raw events and the RGBA buffer.
"""

from __future__ import annotations

import math
import re
import time
from pathlib import Path
from typing import Any

import matplotlib.colors as mcolors
import numpy as np
from matplotlib.font_manager import fontManager
from matplotlib.path import Path as MplPath

# ---------------------------------------------------------------------------
# Color & geometry helpers
# ---------------------------------------------------------------------------

def rgba_to_hex(rgba: Any) -> tuple[str, float]:
    """Convert Matplotlib RGBA / color string to (#RRGGBB, alpha) or ('none', 0.0)."""
    if rgba is None:
        return "none", 0.0
    if isinstance(rgba, str):
        s = rgba.strip().lower()
        if s in ("none", "", "na", "transparent"):
            return "none", 0.0
        if s.startswith("rgb("):
            nums = [float(x) for x in re.findall(r"[\d.]+", s)]
            if len(nums) >= 3:
                return "#{:02x}{:02x}{:02x}".format(
                    int(round(nums[0])), int(round(nums[1])), int(round(nums[2]))
                ), (nums[3] if len(nums) >= 4 else 1.0)
        try:
            r, g, b, a = mcolors.to_rgba(rgba)
            return mcolors.to_hex((r, g, b)), round(float(a), 4)
        except Exception:
            return s, 1.0
    if isinstance(rgba, (tuple, list, np.ndarray)):
        arr = [float(x) for x in rgba]
        if len(arr) == 3:
            r, g, b = arr
            return mcolors.to_hex((r, g, b)), 1.0
        if len(arr) >= 4:
            r, g, b, a = arr[:4]
            if a == 0.0:
                return "none", 0.0
            return mcolors.to_hex((r, g, b)), round(a, 4)
    return "none", 0.0


def hex_to_rgb01(h: str) -> tuple[float, float, float] | None:
    if not h or h == "none":
        return None
    try:
        r, g, b = mcolors.to_rgb(h)
        return (float(r), float(g), float(b))
    except Exception:
        return None


def bbox_bl_to_norm_tl(x0: float, y0: float, x1: float, y1: float, fig_w: float, fig_h: float) -> list[float]:
    return [
        round(x0 / fig_w, 5),
        round(1.0 - y1 / fig_h, 5),
        round(x1 / fig_w, 5),
        round(1.0 - y0 / fig_h, 5),
    ]


def point_bl_to_norm_tl(x: float, y: float, fig_w: float, fig_h: float) -> tuple[float, float]:
    return round(x / fig_w, 5), round(1.0 - y / fig_h, 5)


def point_in_bbox(px: float, py: float, bbox: list[float], pad: float = 0.01) -> bool:
    return (bbox[0] - pad) <= px <= (bbox[2] + pad) and (bbox[1] - pad) <= py <= (bbox[3] + pad)


def _norm_angle(deg: float) -> float:
    """Normalize angle to (-180, 180]."""
    a = ((deg + 180.0) % 360.0) - 180.0
    if abs(a + 180.0) < 1e-6:
        return 180.0
    return round(a, 3)


def _effective_face_alpha(gc, rgb_face) -> float:
    """Alpha Agg actually paints a face with: a forced gc alpha (artist `alpha=`) or an RGB (3-tuple) face takes
    the gc alpha, otherwise the face colour's own alpha (matplotlib `_backend_agg_wrapper` convert_face)."""
    if rgb_face is None:
        return 0.0
    face = [float(v) for v in rgb_face] if not isinstance(rgb_face, str) else list(mcolors.to_rgba(rgb_face))
    if gc.get_forced_alpha() or len(face) == 3:
        return round(float(gc.get_alpha()), 4)
    return round(face[3], 4)


class RecordingRendererProxy:
    """Transparent proxy around a Matplotlib renderer (e.g., RendererAgg).

    Intercepts `open_group`, `close_group`, `draw_path`, and `draw_text`
    without modifying the extension's code at all. Delegates every call
    (including `get_text_width_height_descent` and `points_to_pixels`)
    to the underlying renderer so rendering output is 100% identical.
    """

    def __init__(self, wrapped, fig_w: float, fig_h: float, dpi: float):
        self._wrapped = wrapped
        self._fig_w = fig_w
        self._fig_h = fig_h
        self._dpi = dpi
        self._group_stack: list[str] = []
        self._group_ids: list[int] = []      # instance id of each open group (names alone are ambiguous)
        self._group_counter = 0
        self._paint_index = 0
        self.events: list[dict[str, Any]] = []
        self.groups_seen: list[str] = []
        self.owner_stack: list[int] = []     # indices into record()'s owner list; [0] = the top-level artist
        self.role_stack: list[dict] = []     # theme-element role of the artist being drawn (innermost wins)

    def _role(self) -> dict:
        return dict(self.role_stack[-1]) if self.role_stack else {}

    def _owner(self) -> int | None:
        return self.owner_stack[0] if self.owner_stack else None

    def __getattr__(self, name: str):
        return getattr(self._wrapped, name)

    def open_group(self, s: str, gid: str | None = None):
        self._group_stack.append(s)
        self._group_counter += 1
        self._group_ids.append(self._group_counter)
        self.groups_seen.append(s)
        return self._wrapped.open_group(s, gid=gid)

    def close_group(self, s: str):
        if self._group_stack:
            self._group_stack.pop()
            self._group_ids.pop()
        return self._wrapped.close_group(s)

    def draw_path(self, gc, path, transform, rgbFace=None):
        self._paint_index += 1
        tpath = path.transformed(transform)
        verts = tpath.vertices
        codes = tpath.codes
        if len(verts) > 0 and np.all(np.isfinite(verts)):
            x0, y0 = float(verts[:, 0].min()), float(verts[:, 1].min())
            x1, y1 = float(verts[:, 0].max()), float(verts[:, 1].max())
            bb_tl = bbox_bl_to_norm_tl(x0, y0, x1, y1, self._fig_w, self._fig_h)
            w_pt = (x1 - x0) * 72.0 / self._dpi
            h_pt = (y1 - y0) * 72.0 / self._dpi
            codes_list = [int(c) for c in codes] if codes is not None else []
            has_curve = (int(MplPath.CURVE4) in codes_list) or (int(MplPath.CURVE3) in codes_list)
            fc_hex, _ = rgba_to_hex(rgbFace)
            fc_alpha = _effective_face_alpha(gc, rgbFace) if fc_hex != "none" else 0.0
            if fc_alpha == 0.0:
                fc_hex = "none"
            ec_hex, ec_alpha = rgba_to_hex(gc.get_rgb())   # gc rgb already carries a forced alpha
            lw_pt = round(float(gc.get_linewidth()), 3)
            if lw_pt == 0.0:
                ec_hex, ec_alpha = "none", 0.0
            clip_rect = gc.get_clip_rectangle()
            clip_tl = (
                bbox_bl_to_norm_tl(
                    clip_rect.x0, clip_rect.y0, clip_rect.x1, clip_rect.y1,
                    self._fig_w, self._fig_h,
                )
                if clip_rect is not None
                else None
            )
            # Compute local (unrotated) dimensions from raw path when transform is affine
            self.events.append({
                "kind": "path",
                "paint_index": self._paint_index,
                "groups": list(self._group_stack), "group_ids": list(self._group_ids), "owner": self._owner(), **self._role(),
                "bbox_norm_tl": bb_tl,
                "width_px": round(x1 - x0, 2),
                "height_px": round(y1 - y0, 2),
                "width_pt": round(w_pt, 3),
                "height_pt": round(h_pt, 3),
                "vertex_count": int(len(verts)),
                "has_curve4": int(MplPath.CURVE4) in codes_list,
                "is_rounded": has_curve,
                "is_closed": int(MplPath.CLOSEPOLY) in codes_list,
                "facecolor_hex": fc_hex,
                "face_alpha": fc_alpha,
                "edgecolor_hex": ec_hex,
                "edge_alpha": ec_alpha,
                "linewidth_pt": lw_pt,
                "clip_bbox_norm_tl": clip_tl,
                "vertices_norm_tl": [
                    [round(float(vx) / self._fig_w, 5), round(1.0 - float(vy) / self._fig_h, 5)]
                    for vx, vy in verts
                ],
            })
        return self._wrapped.draw_path(gc, path, transform, rgbFace)

    def draw_text(self, gc, x, y, s, prop, angle, ismath=False, mtext=None):
        # NOTE: In Matplotlib RendererBase, draw_text(gc, x, y, ...) receives y in
        # TOP-DOWN display pixels (y=0 at top), unlike draw_path which uses BOTTOM-UP!
        self._paint_index += 1
        x_f = float(x)
        y_td = float(y)
        y_bl = self._fig_h - y_td
        xn = round(x_f / self._fig_w, 5)
        yn = round(y_td / self._fig_h, 5)
        w_px, h_px, d_px = self._wrapped.get_text_width_height_descent(s, prop, ismath=ismath)
        rad = math.radians(float(angle))
        ca, sa = math.cos(rad), math.sin(rad)
        # 4 corners of text box in bottom-up display coords relative to baseline-left (x_f, y_bl)
        local_corners = [
            (0.0, -d_px),
            (w_px, -d_px),
            (w_px, h_px - d_px),
            (0.0, h_px - d_px),
        ]
        rot_x = [x_f + lx * ca - ly * sa for lx, ly in local_corners]
        rot_y = [y_bl + lx * sa + ly * ca for lx, ly in local_corners]
        bb_tl = bbox_bl_to_norm_tl(min(rot_x), min(rot_y), max(rot_x), max(rot_y), self._fig_w, self._fig_h)

        # The colour is kept even when fully transparent (alpha is compared separately; "none" would hide it).
        rgba = [float(v) for v in gc.get_rgb()]
        col_hex = mcolors.to_hex(rgba[:3])
        alpha = round(rgba[3] if len(rgba) >= 4 else 1.0, 4)   # gc rgb already carries a forced alpha
        resolved_font = fontManager.findfont(prop)
        clip_rect = gc.get_clip_rectangle()
        clip_tl = (
            bbox_bl_to_norm_tl(
                clip_rect.x0, clip_rect.y0, clip_rect.x1, clip_rect.y1,
                self._fig_w, self._fig_h,
            )
            if clip_rect is not None
            else None
        )
        # Check if clipped out
        clipped = False
        if clip_tl is not None:
            clipped = (
                bb_tl[0] < clip_tl[0] - 0.002 or bb_tl[2] > clip_tl[2] + 0.002
                or bb_tl[1] < clip_tl[1] - 0.002 or bb_tl[3] > clip_tl[3] + 0.002
            )

        self.events.append({
            "kind": "text",
            "paint_index": self._paint_index,
            "groups": list(self._group_stack), "group_ids": list(self._group_ids), "owner": self._owner(), **self._role(),
            "text": str(s),
            "x_norm": xn,
            "y_norm": yn,
            "x_pt": round(x_f * 72.0 / self._dpi, 3),
            "y_pt_tl": round(y_td * 72.0 / self._dpi, 3),
            "advance_width_pt": round(float(w_px) * 72.0 / self._dpi, 3),
            "height_pt": round(float(h_px) * 72.0 / self._dpi, 3),
            "descent_pt": round(float(d_px) * 72.0 / self._dpi, 3),
            "bbox_norm_tl": bb_tl,
            # the rotated text rectangle (baseline-left, baseline-right, top-right, top-left) in norm_tl coords
            "quad_norm_tl": [list(point_bl_to_norm_tl(x, y, self._fig_w, self._fig_h)) for x, y in zip(rot_x, rot_y)],
            "color_hex": col_hex,
            "alpha": alpha,
            "fontsize_pt": round(float(prop.get_size_in_points()), 3),
            "fontweight": _normalize_weight(prop.get_weight()),
            "fontstyle": _normalize_style(prop.get_style()),
            "requested_family": list(prop.get_family()),
            "resolved_font_file": Path(resolved_font).name,
            "angle_deg": _norm_angle(float(angle)),
            "clip_bbox_norm_tl": clip_tl,
            "partially_clipped": bool(clipped),
        })
        return self._wrapped.draw_text(gc, x, y, s, prop, angle, ismath=ismath, mtext=mtext)


def _normalize_weight(w: Any) -> str:
    if isinstance(w, (int, float)):
        return "bold" if w >= 600 else "normal"
    s = str(w).lower()
    return "bold" if s in ("bold", "semibold", "demibold", "heavy", "black", "700", "800", "900") else "normal"


def _normalize_style(st: Any) -> str:
    s = str(st).lower()
    return "italic" if s in ("italic", "oblique") else "normal"




def _draw_image(self, gc, x, y, im, transform=None):
    """Record raster images (e.g. gridtext/<img>). x, y = bottom-left in display px (bottom-up)."""
    self._paint_index += 1
    h_px, w_px = int(im.shape[0]), int(im.shape[1])
    bb = bbox_bl_to_norm_tl(float(x), float(y), float(x) + w_px, float(y) + h_px, self._fig_w, self._fig_h)
    self.events.append({
        "kind": "image",
        "paint_index": self._paint_index,
        "groups": list(self._group_stack), "group_ids": list(self._group_ids), "owner": self._owner(), **self._role(),
        "bbox_norm_tl": bb,
        "w_px": w_px,
        "h_px": h_px,
        "mean_alpha": round(float(np.asarray(im)[..., 3].mean()) / 255.0, 4) if np.asarray(im).ndim == 3 and np.asarray(im).shape[-1] == 4 else 1.0,
    })
    if transform is None:
        return self._wrapped.draw_image(gc, x, y, im)
    return self._wrapped.draw_image(gc, x, y, im, transform)


RecordingRendererProxy.draw_image = _draw_image


def _route_recorder(route: str):
    """Record routes that Agg implements in C++ (they never call draw_path), without decoding them.

    Collections (geom_point markers, fill_between, …) go through draw_path_collection / draw_markers;
    a stroked single-path collection silently switches to draw_markers (found by the ggridges pilot).
    Recording them lets adapters fail closed on drawing they don't understand.
    """
    def method(self, gc, *args, **kwargs):
        self._paint_index += 1
        clip = gc.get_clip_rectangle()
        self.events.append({
            "kind": route,
            "paint_index": self._paint_index,
            "groups": list(self._group_stack), "group_ids": list(self._group_ids), "owner": self._owner(), **self._role(),
            "clip_bbox_norm_tl": (bbox_bl_to_norm_tl(clip.x0, clip.y0, clip.x1, clip.y1, self._fig_w, self._fig_h)
                                  if clip is not None else None),
        })
        return getattr(self._wrapped, route)(gc, *args, **kwargs)
    method.__name__ = route
    return method


_MAX_COLLECTION_OFFSETS = 500


def _draw_path_collection(self, gc, master_transform, paths, all_transforms, offsets, offset_trans,
                          facecolors, edgecolors, linewidths, linestyles, antialiaseds, urls, offset_position,
                          *args, **kwargs):
    """Record draw_path_collection with lightweight decoded offsets/styles (up to 500 items),
    so draw-time custom artists (e.g. dotplots) are observable in one pass without re-rendering."""
    self._paint_index += 1
    clip = gc.get_clip_rectangle()
    ev: dict[str, Any] = {
        "kind": "draw_path_collection",
        "paint_index": self._paint_index,
        "groups": list(self._group_stack), "group_ids": list(self._group_ids), "owner": self._owner(), **self._role(),
        "clip_bbox_norm_tl": (bbox_bl_to_norm_tl(clip.x0, clip.y0, clip.x1, clip.y1, self._fig_w, self._fig_h)
                              if clip is not None else None),
    }
    try:
        offs = np.asarray(offsets, dtype=float)
        n = len(offs) if offs.ndim == 2 else 0
        ev["n_offsets"] = int(n)
        if 0 < n <= _MAX_COLLECTION_OFFSETS:
            disp = offset_trans.transform(offs) if offset_trans is not None else offs
            ev["offsets_norm_tl"] = [[round(float(x) / self._fig_w, 5), round(1.0 - float(y) / self._fig_h, 5)]
                                     for x, y in disp]
            if facecolors is not None and len(facecolors):
                ev["facecolors_hex"] = [rgba_to_hex(c)[0] for c in facecolors[:_MAX_COLLECTION_OFFSETS]]
            if edgecolors is not None and len(edgecolors):
                ev["edgecolors_hex"] = [rgba_to_hex(c)[0] for c in edgecolors[:_MAX_COLLECTION_OFFSETS]]
            if linewidths is not None and len(linewidths):
                ev["linewidths_pt"] = [round(float(w), 3) for w in linewidths[:_MAX_COLLECTION_OFFSETS]]
            if paths:
                # Marker bounding size in pt from first path + transform (composing all_transforms[0] and master_transform)
                p0 = paths[0]
                if all_transforms is not None and len(all_transforms):
                    from matplotlib.transforms import Affine2D
                    t0 = Affine2D(all_transforms[0])
                    if master_transform is not None:
                        t0 = t0 + master_transform
                    p0 = p0.transformed(t0)
                elif master_transform is not None:
                    p0 = p0.transformed(master_transform)
                v = p0.vertices
                if len(v) and np.all(np.isfinite(v)):
                    ev["marker_size_pt"] = [round(float(np.ptp(v[:, 0])) * 72.0 / self._dpi, 3),
                                            round(float(np.ptp(v[:, 1])) * 72.0 / self._dpi, 3)]
    except Exception:
        pass
    self.events.append(ev)
    return self._wrapped.draw_path_collection(
        gc, master_transform, paths, all_transforms, offsets, offset_trans,
        facecolors, edgecolors, linewidths, linestyles, antialiaseds, urls, offset_position,
        *args, **kwargs,
    )


RecordingRendererProxy.draw_path_collection = _draw_path_collection


for _route in ("draw_markers", "draw_gouraud_triangles", "draw_quad_mesh", "draw_tex"):
    setattr(RecordingRendererProxy, _route, _route_recorder(_route))


def is_port_module(module: str, package: str) -> bool:
    """True if a class defined in `module` belongs to the port package `package`."""
    return bool(package) and (module == package or module.startswith(package + "."))


def top_level_artists(fig) -> list[tuple[Any, str]]:
    """(artist, parent) for every top-level artist: each child of each Axes, and each non-Axes Figure child."""
    from matplotlib.axes import Axes
    out = [(a, "figure") for a in fig.get_children() if not isinstance(a, Axes)]
    for ax in fig.axes:
        out += [(a, "axes") for a in ax.get_children()]
    return out


# ── theme-element roles: which plotnine artist draws which ggplot2 theme element ──
_TARGET_ROLES = {
    "plot_title": "plot.title", "plot_subtitle": "plot.subtitle", "plot_caption": "plot.caption", "plot_tag": "plot.tag",
    "axis_title_x": "axis.title.x.bottom", "axis_title_y": "axis.title.y.left",
    "legend_title": "legend.title", "legend_text_legend": "legend.text", "legend_text_colorbar": "legend.text",
    "strip_text_x": "strip.text.x.top", "strip_text_y": "strip.text.y.right",
    # backgrounds drawn inside a strip text's draw(): tagged so they're never mistaken for an in-panel box
    "strip_background_x": "strip.background.x", "strip_background_y": "strip.background.y",
}
_GUIDE_ROLES = {"legend_title": "legend.title", "legend_text_legend": "legend.text", "legend_text_colorbar": "legend.text"}


def _plot_of(fig):
    """The ggplot object behind a plotnine figure (via its layout engine, possibly wrapped by a package)."""
    eng, seen = fig.get_layout_engine(), 0
    while eng is not None and seen < 5:
        if hasattr(eng, "plot") and hasattr(eng.plot, "theme"):
            return eng.plot
        eng = next((v for v in vars(eng).values() if hasattr(v, "plot") or "LayoutEngine" in type(v).__name__), None)
        seen += 1
    return None


def role_artists(fig) -> list[tuple[Any, str, int]]:
    """[(artist, ggplot2 theme role, index)] for every themed artist of a plotnine figure.

    Uses the artists plotnine itself applies each theme element to (theme.targets, each guide's own theme, the
    axes' tick labels), so a package that implements theme elements by styling plotnine's artists is covered
    without knowing the package. Relies on plotnine 0.15 internals (pinned by tests/test_roles.py).
    """
    plot = _plot_of(fig)
    out: list[tuple[Any, str, int]] = []
    if plot is None:
        return out
    targets = plot.theme.targets
    for attr, role in _TARGET_ROLES.items():
        v = getattr(targets, attr, None)
        for i, a in enumerate(v if isinstance(v, list) else ([v] if v is not None else [])):
            out.append((a, role, i))
    for _scale, guide in getattr(getattr(plot, "guides", None), "_lookup", {}).values():
        gt = getattr(getattr(guide, "theme", None), "targets", None)
        for attr, role in _GUIDE_ROLES.items():
            v = getattr(gt, attr, None) if gt is not None else None
            for i, a in enumerate(v if isinstance(v, list) else ([v] if v is not None else [])):
                out.append((a, role, i))
    for ax in fig.axes:
        if hasattr(ax, "xaxis"):
            for axis, role in ((ax.xaxis, "axis.text.x.bottom"), (ax.yaxis, "axis.text.y.left")):
                for i, t in enumerate(axis.get_major_ticks()):
                    out.append((t.label1, role, i))
    return out


def record(fig) -> tuple[dict[str, Any], np.ndarray]:
    """Draw `fig` ONCE through the recording proxy on its Agg renderer.

    Returns (observations, RGBA buffer of that same draw). The caller saves the PNG from the buffer, so the
    image and the observations come from one draw. A preceding `fig.canvas.draw()` must have finalized layout.
    """
    t0 = time.perf_counter()
    base = fig.canvas.get_renderer()
    fig_w, fig_h = fig.canvas.get_width_height()
    proxy = RecordingRendererProxy(base, fig_w, fig_h, float(fig.dpi))
    owners: list[dict[str, Any]] = []
    wrapped = []
    for artist, parent in top_level_artists(fig):
        k = len(owners)
        cls = type(artist)
        owners.append({"class": f"{cls.__module__}.{cls.__qualname__}", "module": cls.__module__, "parent": parent})
        orig = artist.draw

        def draw(renderer, *a, _orig=orig, _k=k, **kw):
            proxy.owner_stack.append(_k)
            try:
                return _orig(renderer, *a, **kw)
            finally:
                proxy.owner_stack.pop()

        wrapped.append((artist, "draw" in vars(artist), orig))
        artist.draw = draw
    roles = []
    try:
        role_list = role_artists(fig)
    except Exception as exc:                # never let role mapping break a render; roles just stay unknown
        role_list, roles = [], [{"error": f"{type(exc).__name__}: {exc}"}]
    for artist, role, idx in role_list:
        rk = len(roles)
        roles.append({"role": role, "role_index": idx, "class": type(artist).__name__})
        orig = artist.draw

        def rdraw(renderer, *a, _orig=orig, _tag={"role": role, "role_index": idx, "role_artist": rk}, **kw):
            proxy.role_stack.append(_tag)
            try:
                return _orig(renderer, *a, **kw)
            finally:
                proxy.role_stack.pop()

        wrapped.append((artist, "draw" in vars(artist), orig))
        artist.draw = rdraw
    try:
        base.clear()                       # what FigureCanvasAgg.draw does before drawing
        fig.draw(proxy)
    finally:
        for artist, had, orig in reversed(wrapped):     # role wrappers sit on top of owner wrappers
            if had:
                artist.draw = orig
            else:
                del artist.draw
    rgba = np.asarray(base.buffer_rgba()).copy()
    panels = []
    for ax in fig.axes:
        bb = ax.bbox
        panels.append(bbox_bl_to_norm_tl(bb.x0, bb.y0, bb.x1, bb.y1, fig_w, fig_h))
    return {
        "figure_px": [int(fig_w), int(fig_h)],
        "dpi": float(fig.dpi),
        "panels_norm_tl": panels,
        "owners": owners,
        "roles": roles,
        "events": proxy.events,
        "groups_seen": sorted(set(proxy.groups_seen)),
        "extraction_ms": round((time.perf_counter() - t0) * 1000, 2),
    }, rgba
