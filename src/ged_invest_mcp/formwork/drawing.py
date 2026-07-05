"""Deterministic SVG rendering of a closed-cell formwork layout draft.

Pure standard library: turns the dict returned by ``calculator.calculate_cell``
into a self-contained SVG string (no matplotlib, no binary data). The drawing is
reproducible and driven ENTIRELY by the ``layout_draft`` the calculator emits, so
it stays in sync with the BOM.

Two parts are produced:
  * PLAN  - only for closed cells whose outline is an axis-aligned rectangle.
  * ELEVATIONS - generic: for every distinct wall, the outer and inner face are
    drawn piece by piece with the vertical joints ("fugi") that coincide on both
    faces highlighted.
"""

from __future__ import annotations

from typing import Any

# piece-type colours (match the demo palette)
_TYPE_COLORS = {
    "outer_corner_0": "#4472c4",
    "filler_panel": "#ffd54a",
    "inner_corner": "#57b36b",
    "timber": "#eeeeee",
}
# fallback colour by panel width
_WIDTH_COLORS = {
    90: "#a9d18e", 75: "#ffd966", 70: "#f4b183",
    60: "#9dc3e6", 50: "#c9a0dc", 45: "#ffe08a", 30: "#f6c1c1", 25: "#d9d9d9",
}
_DEFAULT_COLOR = "#cccccc"


def _piece_color(piece: dict) -> str:
    t = piece.get("type")
    if t in _TYPE_COLORS:
        return _TYPE_COLORS[t]
    return _WIDTH_COLORS.get(int(piece.get("width_cm", 0)), _DEFAULT_COLOR)


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _rect(x: float, y: float, w: float, h: float, fill: str,
          stroke: str = "#222", sw: float = 0.7) -> str:
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
    )


def _text(x: float, y: float, s: str, size: float = 10.0,
          anchor: str = "middle", weight: str = "normal") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="sans-serif" '
        f'font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" '
        f'dominant-baseline="central">{_esc(s)}</text>'
    )


def _line(x1: float, y1: float, x2: float, y2: float,
          color: str = "#666", sw: float = 0.7, dash: str | None = None) -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
        f'stroke="{color}" stroke-width="{sw}"{d}/>'
    )


def _flat_widths(wall: dict) -> list[int]:
    return [
        int(s.replace("cm", ""))
        for s in wall.get("flat_panels_shared", [])
        if not s.startswith("timber")
    ]


def _dedupe_walls(walls: list[dict]) -> list[tuple[dict, int]]:
    """Group identical walls; returns [(wall, count), ...] preserving order."""
    out: list[list[Any]] = []
    for w in walls:
        key = (
            round(w.get("axis_length_cm", 0), 1),
            tuple((p["type"], p["width_cm"]) for p in w["outer_face"]["pieces"]),
        )
        for item in out:
            if item[2] == key:
                item[1] += 1
                break
        else:
            out.append([w, 1, key])
    return [(w, n) for w, n, _ in out]


def _draw_elevation(wall: dict, count: int, thickness: float,
                    filler_w: int, ox: float, oy: float, scale: float) -> tuple[str, float, float]:
    """Draw both faces of one wall. Returns (svg, width_px, height_px)."""
    parts: list[str] = []
    h_cm = float(wall.get("height_cm", 150))
    ph = h_cm * scale                      # strip height in px
    gap = 12.0                             # gap between the two faces
    inner_offset = thickness * scale       # inner face inset so flat runs align
    label_w = 46.0
    title = (
        f'Wall {wall.get("label", "?")}  L(axis)={wall.get("axis_length_cm")} cm  '
        f'x{count}  -  outer / inner face (aligned joints)'
    )
    parts.append(_text(ox + label_w, oy, title, size=11, anchor="start", weight="bold"))
    top = oy + 10

    def strip(y: float, pieces: list[dict], x_start: float) -> float:
        x = ox + label_w + x_start
        for p in pieces:
            w = float(p["width_cm"])
            if w <= 0:               # the '0' outer corner has no width to draw
                continue
            wpx = w * scale
            parts.append(_rect(x, y, wpx, ph, _piece_color(p)))
            if w >= 40:
                parts.append(_text(x + wpx / 2, y + ph / 2, f'{int(w)}', size=8))
            x += wpx
        return x

    outer_end = strip(top, wall["outer_face"]["pieces"], 0.0)
    strip(top + ph + gap, wall["inner_face"]["pieces"], inner_offset)
    parts.append(_text(ox + label_w - 4, top + ph / 2, "OUT", size=9, anchor="end", weight="bold"))
    parts.append(_text(ox + label_w - 4, top + ph + gap + ph / 2, "IN", size=9, anchor="end", weight="bold"))

    # aligned vertical joints (red dashed) at the shared flat-run boundaries
    x_j = ox + label_w + filler_w * scale
    y0, y1 = top, top + 2 * ph + gap
    parts.append(_line(x_j, y0, x_j, y1, color="#d33", sw=0.8, dash="4,3"))
    for w in _flat_widths(wall):
        x_j += w * scale
        parts.append(_line(x_j, y0, x_j, y1, color="#d33", sw=0.8, dash="4,3"))

    total_h = 10 + 2 * ph + gap + 24
    total_w = (outer_end - ox)
    return "\n".join(parts), total_w, total_h


def _rectangle_plan(result: dict, ox: float, oy: float, scale: float
                    ) -> tuple[str, float, float] | None:
    """Draw a plan only when the outline is an axis-aligned rectangle."""
    echo = result.get("input_echo", {})
    pts = echo.get("polygon_points_cm")
    if not pts or len(pts) != 4:
        return None
    xs = sorted({round(p[0], 3) for p in pts})
    ys = sorted({round(p[1], 3) for p in pts})
    if len(xs) != 2 or len(ys) != 2:
        return None                       # not an axis-aligned rectangle
    ax_l = xs[1] - xs[0]
    ax_s = ys[1] - ys[0]
    t = float(result["assumptions"]["wall_thickness_cm"])
    ct = result["corner_template"]
    filler_w = int(ct["outer_filler_width_cm"])
    leg = int(ct["inner_corner_cm"].split("x")[0])
    h = t / 2.0

    walls = result["layout_draft"]
    long_flat = _flat_widths(walls[0])
    short_flat = _flat_widths(walls[1]) if len(walls) > 1 else long_flat

    # model -> svg px (flip Y so it reads like the plan)
    outer_w = (ax_l + t) * scale
    outer_h = (ax_s + t) * scale

    def X(mx: float) -> float:
        return ox + (mx + h) * scale

    def Y(my: float) -> float:
        return oy + outer_h - (my + h) * scale

    parts: list[str] = [_text(ox, oy - 10, "PLAN (closed cell, aligned joints)", size=12, anchor="start", weight="bold")]
    parts.append(_rect(X(-h), Y(ax_s + h), outer_w, outer_h, "#efefef", stroke="#222", sw=1.5))
    parts.append(_rect(X(h), Y(ax_s - h), (ax_l - t) * scale, (ax_s - t) * scale, "#ffffff", stroke="#222", sw=1.5))
    # axis (dashed blue)
    parts.append(
        f'<rect x="{X(0):.1f}" y="{Y(ax_s):.1f}" width="{ax_l*scale:.1f}" '
        f'height="{ax_s*scale:.1f}" fill="none" stroke="#1f77b4" '
        f'stroke-width="0.8" stroke-dasharray="5,3"/>'
    )

    # division ticks along each wall (across the thickness band)
    def ticks_h(y_out: float, y_in: float) -> None:
        x = -h
        seq = [45] + long_flat + [45]
        pos = [x]
        for w in seq:
            x += w
            pos.append(x)
        for px in pos:
            parts.append(_line(X(px), Y(y_out), X(px), Y(y_in), color="#666", sw=0.7))

    def ticks_v(x_out: float, x_in: float) -> None:
        y = -h
        seq = [45] + short_flat + [45]
        pos = [y]
        for w in seq:
            y += w
            pos.append(y)
        for py in pos:
            parts.append(_line(X(x_out), Y(py), X(x_in), Y(py), color="#666", sw=0.7))

    ticks_h(-h, -h + t)              # bottom long wall
    ticks_h(ax_s - h + t, ax_s + h)  # top long wall  (approx band)
    ticks_v(-h, -h + t)              # left short wall
    ticks_v(ax_l - h + t, ax_l + h)  # right short wall

    # corner markers: filler (yellow), inner corner (green), NZ 0 (blue dot)
    def corner(cx: float, cy: float, sx: int, sy: int) -> None:
        parts.append(_rect(min(X(cx), X(cx + sx * filler_w)), min(Y(cy), Y(cy + sy * t)),
                           abs(filler_w * scale), abs(t * scale), _TYPE_COLORS["filler_panel"]))
        parts.append(_rect(min(X(cx), X(cx + sx * t)), min(Y(cy), Y(cy + sy * filler_w)),
                           abs(t * scale), abs(filler_w * scale), _TYPE_COLORS["filler_panel"]))
        parts.append(_rect(min(X(cx + sx * t), X(cx + sx * (t + leg))),
                           min(Y(cy + sy * t), Y(cy + sy * (t + leg))),
                           abs(leg * scale), abs(leg * scale), _TYPE_COLORS["inner_corner"]))
        parts.append(_rect(min(X(cx), X(cx + sx * 8)), min(Y(cy), Y(cy + sy * 8)),
                           abs(8 * scale), abs(8 * scale), _TYPE_COLORS["outer_corner_0"], sw=0.9))

    corner(-h, -h, +1, +1)
    corner(ax_l + h, -h, -1, +1)
    corner(ax_l + h, ax_s + h, -1, -1)
    corner(-h, ax_s + h, +1, -1)

    parts.append(_text(X(ax_l / 2), Y(-h) + 20, f'axis {ax_l/100:.2f} m', size=10))
    parts.append(_text(X(-h) - 22, Y(ax_s / 2), f'axis {ax_s/100:.2f} m', size=10))

    return "\n".join(parts), outer_w + 60, outer_h + 40


def render_layout_svg(result: dict, scale: float = 0.32) -> str:
    """Render a closed-cell layout draft as a standalone SVG string.

    Args:
        result: the dict returned by ``calculator.calculate_cell``.
        scale: pixels per centimetre.
    """
    if "layout_draft" not in result:
        raise ValueError("result has no 'layout_draft' (closed-cell calculation required).")

    thickness = float(result.get("assumptions", {}).get("wall_thickness_cm", 0))
    filler_w = int(result.get("corner_template", {}).get("outer_filler_width_cm", 0))

    margin = 24.0
    y = margin
    body: list[str] = []
    max_w = 0.0

    plan = _rectangle_plan(result, margin, y + 16, scale)
    if plan:
        svg_plan, pw, ph = plan
        body.append(svg_plan)
        max_w = max(max_w, pw)
        y += ph + 48

    for wall, count in _dedupe_walls(result["layout_draft"]):
        svg_el, ew, eh = _draw_elevation(wall, count, thickness, filler_w, margin, y, scale)
        body.append(svg_el)
        max_w = max(max_w, ew)
        y += eh + 16

    width = max_w + 2 * margin
    height = y + margin
    header = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
        f'height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}">'
    )
    bg = f'<rect x="0" y="0" width="{width:.0f}" height="{height:.0f}" fill="white"/>'
    title = _text(margin, margin - 6,
                  f'{result.get("system", "")} formwork draft - '
                  f'{result.get("summary", {}).get("total_panels", "?")} panels, '
                  f'{result.get("summary", {}).get("total_corners", "?")} corners',
                  size=13, anchor="start", weight="bold")
    return "\n".join([header, bg, title, *body, "</svg>"])
