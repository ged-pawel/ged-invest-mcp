"""Deterministic wall formwork takeoff.

Method (a repeatable engineering estimator):

1. GEOMETRY
   A wall is described either as a list of segments (length + height) or as a
   polygon (vertex coordinates). From a polygon we derive each side length and
   the type of each corner (convex = outer, concave = inner).

2. PANEL SELECTION PER SEGMENT
   - horizontal: cover length L with a combination of available panel widths
     (dynamic programming: first minimize leftover for timber infill, then the
     number of panels). Horizontal leftover -> vertical timber board (client side).
   - vertical: STACK panel heights until they reach the wall height. Panels are
     not cut; if the top course stands above the pour line that is reported as
     "overshoot", not as timber.
   - panels per face = (# columns) x (# courses); a wall has TWO faces (see
     `faces`), so panels are multiplied accordingly.

3. CORNERS
   Each corner is a vertical element (outer/inner), one piece per course.

4. HARDWARE
   Derived from catalog `HardwareRule`s (ties, pins, wedges, cones, corner
   tensioners, props with heads/feet). Coefficients are approximate.

The output is fully auditable: it echoes the geometry actually used, the panel
layout per segment, the assumptions, and an area reconciliation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache

from .catalog import CATALOG_VERSION, FormworkSystem, get_system
from .messages_pl import DISCLAIMER, UNITS

# Sanity bounds on a single wall dimension [cm]: below MIN it is measurement
# noise, above MAX it is almost certainly a unit slip (e.g. mm entered as cm)
# and would also blow up the DP arrays. Both are rejected with a clear error.
MIN_DIMENSION_CM = 1.0
MAX_DIMENSION_CM = 10_000.0  # 100 m

# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------
@dataclass
class WallSegment:
    """A single straight wall segment."""

    length: float  # [cm]
    height: float  # [cm]
    corner_at_end: str | None = None  # "outer" | "inner" | None
    label: str | None = None


# ---------------------------------------------------------------------------
# Dynamic programming
# ---------------------------------------------------------------------------
@lru_cache(maxsize=8192)
def _min_pieces(target: int, sizes: tuple[int, ...]) -> tuple[tuple[int, int], ...] | None:
    """Minimum number of pieces summing EXACTLY to `target` (coin problem).

    Returns an immutable tuple of (size, count) pairs, or None if impossible.
    An immutable, cache-safe return value avoids the shared-mutable-dict hazard.
    """
    if target == 0:
        return ()
    inf = math.inf
    dp: list[float] = [inf] * (target + 1)
    choice: list[int] = [-1] * (target + 1)
    dp[0] = 0
    for i in range(1, target + 1):
        for s in sizes:
            if s <= i and dp[i - s] + 1 < dp[i]:
                dp[i] = dp[i - s] + 1
                choice[i] = s
    if dp[target] == inf:
        return None
    combo: dict[int, int] = {}
    i = target
    while i > 0:
        s = choice[i]
        combo[s] = combo.get(s, 0) + 1
        i -= s
    return tuple(sorted(combo.items()))


def cover_width(target_cm: int, widths: tuple[int, ...]) -> tuple[dict[int, int], int]:
    """Cover a length with panel widths; leftover becomes timber infill [cm].

    Minimizes timber leftover first, then the number of panels.
    """
    target = int(round(target_cm))
    if target <= 0:
        return {}, 0
    smallest = min(widths)
    for t in range(0, smallest):  # leftover can only be < smallest panel
        remaining = target - t
        if remaining < 0:
            break
        combo = _min_pieces(remaining, widths)
        if combo is not None:
            return dict(combo), t
    # length smaller than the smallest panel: all timber
    return {}, target


def cover_height(target_cm: int, heights: tuple[int, ...]) -> tuple[dict[int, int], int]:
    """Cover a height by STACKING panels to reach at least the wall height.

    Panels are not cut. Returns (courses, overshoot_cm) where overshoot is how
    far the top course stands above the wall top (no timber on the vertical axis).
    """
    target = int(round(target_cm))
    if target <= 0:
        return {}, 0
    max_h = max(heights)
    # smallest sum >= target that is exactly composable by the heights
    for s in range(target, target + max_h + 1):
        combo = _min_pieces(s, heights)
        if combo is not None:
            return dict(combo), s - target
    # fallback: stack the tallest panel
    n = math.ceil(target / max_h)
    return {max_h: n}, n * max_h - target


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass
class SegmentResult:
    label: str
    length: float
    height: float
    columns: dict[int, int]
    courses: dict[int, int]
    timber_h_cm: float
    overshoot_v_cm: float
    panels: dict[str, int]
    panels_per_face: int
    faces: int
    area_m2: float
    corner: str | None
    horizontal_layout: list[str] = field(default_factory=list)
    vertical_layout: list[str] = field(default_factory=list)


_DIM_PL = {
    "length": "długość",
    "height": "wysokość",
    "flat run (axis minus corners)": "bieg płyt (oś minus narożniki)",
}


def _validate_dimension(value: float, name: str, label: str) -> None:
    """Reject non-finite, non-positive, too-small or too-large dimensions.

    Note: `NaN <= 0` and `inf <= 0` are both False, so a plain positivity check
    would let them through and crash later in int(round(...)); check finiteness
    explicitly here.
    """
    dim = _DIM_PL.get(name, name)
    if not math.isfinite(value):
        raise ValueError(f"Odcinek „{label}”: nieprawidłowa {dim} ({value}).")
    if value < MIN_DIMENSION_CM:
        raise ValueError(
            f"Odcinek „{label}”: {dim} {value} cm jest poniżej minimum {MIN_DIMENSION_CM} cm."
        )
    if value > MAX_DIMENSION_CM:
        raise ValueError(
            f"Odcinek „{label}”: {dim} {value} cm przekracza maksimum {MAX_DIMENSION_CM} cm "
            f"(prawdopodobnie błąd jednostki — wymiary podawaj w cm)."
        )


def _resolve_widths(
    system: FormworkSystem, available_widths: tuple[int, ...] | None
) -> tuple[tuple[int, ...], list[str]]:
    """Restrict panel widths to what is actually in stock, if provided.

    Returns (widths, warnings). Widths not present in the catalog are dropped
    (with a warning) so panel-article lookups stay valid.
    """
    if not available_widths:
        return system.panel_widths, []
    warnings: list[str] = []
    known = set(system.panel_widths)
    kept = tuple(sorted({int(w) for w in available_widths if int(w) in known}, reverse=True))
    unknown = sorted({int(w) for w in available_widths} - known)
    if unknown:
        warnings.append(
            f"Pominięto szerokości spoza katalogu {system.name}: {unknown} cm."
        )
    if not kept:
        raise ValueError(
            f"Żadna z podanych szerokości {sorted(set(available_widths))} nie występuje "
            f"w katalogu {system.name} {list(system.panel_widths)}."
        )
    return kept, warnings


def _calculate_segment(
    seg: WallSegment, system: FormworkSystem, faces: int,
    widths: tuple[int, ...] | None = None,
) -> SegmentResult:
    label = seg.label or "?"
    _validate_dimension(seg.length, "length", label)
    _validate_dimension(seg.height, "height", label)

    columns, timber_h = cover_width(int(round(seg.length)), widths or system.panel_widths)
    courses, overshoot_v = cover_height(int(round(seg.height)), system.panel_heights)

    num_columns = sum(columns.values())
    num_courses = sum(courses.values())

    panels: dict[str, int] = {}
    for w, nw in columns.items():
        for h, nh in courses.items():
            key = f"{w}x{h}"
            panels[key] = panels.get(key, 0) + nw * nh * faces

    panels_per_face = num_columns * num_courses
    area = faces * (seg.length / 100.0) * (seg.height / 100.0)

    # ordered, human-readable layouts (per single face)
    h_layout = [f"{w} cm" for w in sorted(columns, reverse=True) for _ in range(columns[w])]
    if timber_h > 0:
        h_layout.append(f"deska {timber_h} cm")
    v_layout = [f"{h} cm" for h in sorted(courses, reverse=True) for _ in range(courses[h])]
    if overshoot_v > 0:
        v_layout.append(f"(nadwyżka górna {overshoot_v} cm)")

    return SegmentResult(
        label=seg.label or "?",
        length=seg.length,
        height=seg.height,
        columns=columns,
        courses=courses,
        timber_h_cm=float(timber_h),
        overshoot_v_cm=float(overshoot_v),
        panels=panels,
        panels_per_face=panels_per_face,
        faces=faces,
        area_m2=round(area, 3),
        corner=seg.corner_at_end,
        horizontal_layout=h_layout,
        vertical_layout=v_layout,
    )


# ---------------------------------------------------------------------------
# Polygon -> segments + corner types
# ---------------------------------------------------------------------------
def polygon_to_segments(
    points: list[tuple[float, float]],
    height: float,
    closed: bool = True,
) -> tuple[list[WallSegment], str]:
    """Convert a polygon (list of (x, y) in cm) into wall segments.

    Returns (segments, winding) where winding is "CCW" or "CW". Convex vertices
    are tagged "outer", concave "inner", collinear vertices get no corner.
    """
    for p in points:
        if not (math.isfinite(p[0]) and math.isfinite(p[1])):
            raise ValueError(f"Wielokąt ma nieprawidłową współrzędną: {p}.")

    points = _dedup_points(points, closed)
    n = len(points)
    min_points = 3 if closed else 2
    if n < min_points:
        raise ValueError(
            f"{'Zamknięty wielokąt' if closed else 'Polilinia'} wymaga co najmniej "
            f"{min_points} różnych punktów (po usunięciu duplikatów: {n})."
        )

    signed_area2 = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        signed_area2 += x1 * y2 - x2 * y1
    if closed and abs(signed_area2) < 1e-6:
        raise ValueError(
            "Wielokąt jest zdegenerowany (zerowy obszar / punkty współliniowe); "
            "nie można określić wnętrza i zewnętrza dla narożników."
        )
    ccw = signed_area2 > 0
    winding = "CCW" if ccw else "CW"

    num_sides = n if closed else n - 1
    segments: list[WallSegment] = []
    for i in range(num_sides):
        p0 = points[i]
        p1 = points[(i + 1) % n]
        length = math.hypot(p1[0] - p0[0], p1[1] - p0[1])

        corner: str | None = None
        if closed or i < num_sides - 1:
            p2 = points[(i + 2) % n]
            corner = _corner_type(p0, p1, p2, ccw)

        segments.append(
            WallSegment(
                length=length,
                height=height,
                corner_at_end=corner,
                label=chr(ord("A") + i) if i < 26 else f"S{i + 1}",
            )
        )
    return segments, winding


def _dedup_points(points: list[tuple[float, float]], closed: bool) -> list[tuple[float, float]]:
    """Remove consecutive duplicate points (and, for closed rings, a trailing
    point equal to the first). Prevents zero-length sides from crashing."""
    out: list[tuple[float, float]] = []
    for p in points:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > 1e-9:
            out.append((p[0], p[1]))
    if closed and len(out) >= 2 and math.hypot(out[-1][0] - out[0][0], out[-1][1] - out[0][1]) <= 1e-9:
        out.pop()
    return out


def _corner_type(p0, p1, p2, ccw: bool) -> str | None:
    """Return 'outer' (convex), 'inner' (concave) or None (collinear).

    Uses the sine of the turn angle (cross product normalized by edge lengths)
    so the collinear threshold is scale-independent.
    """
    v1x, v1y = p1[0] - p0[0], p1[1] - p0[1]
    v2x, v2y = p2[0] - p1[0], p2[1] - p1[1]
    len1 = math.hypot(v1x, v1y)
    len2 = math.hypot(v2x, v2y)
    if len1 < 1e-9 or len2 < 1e-9:
        return None
    sin_angle = (v1x * v2y - v1y * v2x) / (len1 * len2)
    if abs(sin_angle) < 1e-6:  # ~0.00006 deg - effectively collinear
        return None
    convex = (sin_angle > 0) if ccw else (sin_angle < 0)
    return "outer" if convex else "inner"


# ---------------------------------------------------------------------------
# Main calculation
# ---------------------------------------------------------------------------
def calculate(
    segments: list[WallSegment],
    system: str = "BAUTEKK",
    pressure_kn_m2: float | None = None,
    faces: int = 2,
    geometry_source: str = "segments",
    polygon_points: list[tuple[float, float]] | None = None,
    polygon_winding: str | None = None,
    available_widths: tuple[int, ...] | None = None,
) -> dict:
    """Compute the full, auditable bill of materials (BOM) for a wall."""
    sys = get_system(system)
    if faces not in (1, 2):
        raise ValueError("`faces` musi być 1 lub 2.")
    if not segments:
        raise ValueError("Nie podano odcinków ścian.")

    widths, warnings = _resolve_widths(sys, available_widths)
    if not sys.verified:
        warnings.append(
            f"Katalog {sys.name} używa wartości DOMYŚLNYCH — zweryfikuj je w "
            f"oficjalnym katalogu BAUKRANE ({sys.notes})"
        )
    if pressure_kn_m2 is not None and pressure_kn_m2 > sys.max_pressure_kn_m2:
        warnings.append(
            f"Podane ciśnienie betonu {pressure_kn_m2} kN/m2 przekracza limit "
            f"systemu {sys.name} ({sys.max_pressure_kn_m2} kN/m2). Rozważ mocniejszy system."
        )
    if faces == 2:
        warnings.append(
            "Przyjęto DWIE strony szalunku na odcinek (wejście = osie ścian). "
            "Jeśli podajesz już obie strony osobno, ustaw faces=1, aby uniknąć podwojenia."
        )

    results = [_calculate_segment(seg, sys, faces, widths) for seg in segments]

    # aggregate panels
    panel_totals: dict[str, int] = {}
    for r in results:
        for k, v in r.panels.items():
            panel_totals[k] = panel_totals.get(k, 0) + v

    # corners: one piece per course, billed per course height (correct SKU)
    outer_by_height: dict[int, int] = {}
    inner_by_height: dict[int, int] = {}
    for r in results:
        if r.corner not in ("outer", "inner"):
            continue
        target = outer_by_height if r.corner == "outer" else inner_by_height
        for h, nh in r.courses.items():
            target[h] = target.get(h, 0) + nh
    outer_corners = sum(outer_by_height.values())
    inner_corners = sum(inner_by_height.values())

    total_length = sum(r.length for r in results)
    # single-rounded so summary area == reconciliation geometry area
    total_area = round(sum(faces * (r.length / 100.0) * (r.height / 100.0) for r in results), 3)
    timber_lm = round(sum(r.timber_h_cm for r in results) / 100.0, 2)

    # ties: intervals horizontally (avoids double-count at shared corners),
    # rows vertically by spacing (calibrated against the PDF - no extra fencepost
    # row, which was overcounting). Ties pass through, counted once (not x faces).
    total_ties = 0
    for r in results:
        tie_cols = max(math.ceil(r.length / sys.tie_spacing_h), 1)
        tie_rows = max(math.ceil(r.height / sys.tie_spacing_v), 1)
        total_ties += tie_cols * tie_rows

    # vertical panel-to-panel joints (per face) and DTR-based connector count.
    # DTR BauTekk: a joint uses 5 connectors for 150cm plates, 4 for 120, 3 for 90.
    vertical_joints = 0
    connectors = 0
    default_cpj = 3
    for r in results:
        cols = sum(r.columns.values())
        joints_per_course = max(cols - 1, 0)
        for h, nh in r.courses.items():
            vertical_joints += joints_per_course * nh * r.faces
            cpj = sys.connectors_per_joint.get(h, default_cpj)
            connectors += joints_per_course * nh * r.faces * cpj

    total_panels = sum(panel_totals.values())
    total_corners = outer_corners + inner_corners
    props = math.ceil(total_length / sys.max_prop_spacing) if total_length else 0

    bases = {
        "tie": total_ties,
        "panel": total_panels,
        "vertical_joint": vertical_joints,
        "corner": total_corners,
        "prop": props,
        "area_m2": total_area,
    }

    hardware = _assemble_hardware(sys, bases, connectors)

    bom_panels = _bom_panels(sys, panel_totals)
    bom_corners = _bom_corners(sys, outer_by_height, inner_by_height)

    # reconciliation: keep the two effects SEPARATE so the numbers are honest.
    # - vertical overshoot: panels stand above the pour line (panel area surplus)
    # - horizontal timber: panels under-cover the length, filled by client timber
    area_from_geometry = total_area
    area_from_panels = round(_panel_area(panel_totals), 3)
    overshoot_area = round(
        sum(faces * (sum(w * n for w, n in r.columns.items()) / 100.0) * (r.overshoot_v_cm / 100.0)
            for r in results), 3
    )
    timber_area = round(
        sum(faces * (r.timber_h_cm / 100.0) * (sum(h * n for h, n in r.courses.items()) / 100.0)
            for r in results), 3
    )
    overshoot_pct = round(overshoot_area / area_from_geometry * 100, 1) if area_from_geometry else 0.0
    timber_pct = round(timber_area / area_from_geometry * 100, 1) if area_from_geometry else 0.0

    return {
        "system": sys.name,
        "catalog_version": CATALOG_VERSION,
        "catalog_verified": sys.verified,
        "max_pressure_kn_m2": sys.max_pressure_kn_m2,
        "units": UNITS,
        "assumptions": {
            "faces": faces,
            "corner_rule": "wypukły=zewn., wklęsły=wewn.; jeden element narożnikowy na warstwę",
            "tie_spacing_cm": {"horizontal": sys.tie_spacing_h, "vertical": sys.tie_spacing_v},
            "max_prop_spacing_cm": sys.max_prop_spacing,
            "panel_heights_considered_cm": list(sys.panel_heights),
            "width_policy": "minimalizuj resztkę deski, potem liczbę płyt",
            "height_policy": "układaj płyty w pionie do wysokości ściany; nadwyżkę górną nie tnij",
            "hardware_coeffs": "przybliżone; skalibruj wg DTR",
        },
        "input_echo": {
            "geometry_source": geometry_source,
            "polygon_points_cm": [list(p) for p in polygon_points] if polygon_points else None,
            "polygon_winding": polygon_winding,
            "segments_used": [
                {"label": r.label, "length_cm": round(r.length, 1),
                 "height_cm": round(r.height, 1), "corner_at_end": r.corner}
                for r in results
            ],
        },
        "summary": {
            "segment_count": len(results),
            "total_length_m": round(total_length / 100.0, 2),
            "formwork_area_m2": total_area,
            "total_panels": total_panels,
            "total_corners": total_corners,
            "timber_infill_lm": timber_lm,
        },
        "bom": {
            "panels": bom_panels,
            "corners": bom_corners,
            "hardware": hardware,
        },
        "segments": [
            {
                "label": r.label,
                "length_cm": round(r.length, 1),
                "height_cm": round(r.height, 1),
                "faces": r.faces,
                "panels_horizontal_per_face": r.horizontal_layout,
                "courses_vertical_per_face": r.vertical_layout,
                "panels_total": sum(r.panels.values()),
                "panels_breakdown": r.panels,
                "timber_infill_h_cm": r.timber_h_cm,
                "top_overshoot_cm": r.overshoot_v_cm,
                "area_m2": r.area_m2,
                "corner_at_end": r.corner,
            }
            for r in results
        ],
        "reconciliation": {
            "area_from_geometry_m2": area_from_geometry,
            "area_from_panels_m2": area_from_panels,
            "vertical_overshoot_m2": overshoot_area,
            "vertical_overshoot_pct": overshoot_pct,
            "horizontal_timber_m2": timber_area,
            "horizontal_timber_pct": timber_pct,
            "note": (
                "area_from_panels = geometria + nadwyżka_pionowa (płyty ponad "
                "linią zalewu) − deski_poziome (wypełnienie po stronie klienta)."
            ),
        },
        "warnings": warnings,
        "disclaimer": DISCLAIMER,
        "method": (
            "Estymator: poziome pokrycie DP (deski), pionowy układ płyt "
            f"(nadwyżka bez cięcia), {faces} strona/strony, narożniki na warstwę, "
            f"wiązania w siatce, podpory co max {sys.max_prop_spacing / 100:.2f} m, "
            "akcesoria wg współczynników katalogowych."
        ),
    }


def _assemble_hardware(sys: FormworkSystem, bases: dict, connectors: int) -> list[dict]:
    """Build the hardware BOM: DTR connectors first, then coefficient rules."""
    hardware: list[dict] = []
    if connectors > 0:
        hardware.append({
            "item": "Złącze BAUTEKK" if sys.name == "BAUTEKK" else f"Złącze {sys.name}",
            "article_no": "7270B00001" if sys.name == "BAUTEKK" else None,
            "quantity": connectors,
            "basis": "spoina_pionowa (DTR: 5/4/3 na spoinę wg wysokości płyty)",
            "approximate": False,
        })
    for rule in sys.hardware:
        if rule.basis not in bases:
            raise ValueError(
                f"Catalog error: hardware '{rule.key}' uses unknown basis "
                f"'{rule.basis}'. Valid bases: {', '.join(bases)}."
            )
        qty = math.ceil(bases[rule.basis] * rule.coeff)
        if qty <= 0:
            continue
        hardware.append({
            "item": rule.name,
            "article_no": rule.article_no,
            "quantity": qty,
            "basis": rule.basis,
            "coeff": rule.coeff,
            "approximate": rule.approximate,
        })
    return hardware


def _bom_panels(sys: FormworkSystem, panel_totals: dict[str, int]) -> list[dict]:
    return [
        {"size": k, "article_no": sys.panel_article_numbers.get(_parse_size(k)), "quantity": v}
        for k, v in sorted(panel_totals.items(), key=lambda kv: -kv[1])
    ]


def _bom_corners(sys: FormworkSystem, outer_by_height: dict[int, int],
                 inner_by_height: dict[int, int]) -> list[dict]:
    bom: list[dict] = []
    for h, qty in sorted(outer_by_height.items(), reverse=True):
        bom.append({
            "kind": "outer", "height_cm": h,
            "description": sys.outer_corner.description.replace("{h}", str(h)),
            "article_no": sys.outer_corner.article_for(h), "quantity": qty,
        })
    for h, qty in sorted(inner_by_height.items(), reverse=True):
        bom.append({
            "kind": "inner", "height_cm": h,
            "description": sys.inner_corner.description.replace("{h}", str(h)),
            "article_no": sys.inner_corner.article_for(h), "quantity": qty,
        })
    return bom


# ---------------------------------------------------------------------------
# Closed cell (wall thickness aware): outer face longer, inner face shorter,
# every corner needs BOTH an outer element (on the convex face) and an inner
# element (on the concave face).
# ---------------------------------------------------------------------------
@dataclass
class CellWall:
    """One wall of a closed cell, with per-face panel-run lengths [cm]."""

    label: str
    axis_length: float
    outer_length: float   # outer-face panel run (after corner legs)
    inner_length: float   # inner-face panel run (after corner legs)
    height: float
    end_is_corner: bool   # whether the end vertex is a real (non-collinear) corner
    start_is_corner: bool = True   # whether the start vertex is a real corner
    start_kind: str | None = None  # "outer" (convex) | "inner" (concave) | None
    end_kind: str | None = None


def polygon_to_cell(
    points: list[tuple[float, float]],
    height: float,
    thickness: float,
    system: str = "BAUTEKK",
) -> tuple[list[CellWall], str]:
    """Convert a closed centerline polygon + wall thickness into per-face walls.

    For each 90deg-ish corner the outer face gains t/2 and the inner face loses
    t/2 (and vice-versa at a concave corner). The inner corner element occupies
    `leg_cm` on each inner face; the outer "0" corner occupies nothing.
    """
    if not math.isfinite(thickness) or thickness <= 0:
        raise ValueError("Grubość ściany musi być dodatnią liczbą [cm].")
    sys = get_system(system)

    for p in points:
        if not (math.isfinite(p[0]) and math.isfinite(p[1])):
            raise ValueError(f"Wielokąt ma nieprawidłową współrzędną: {p}.")
    pts = _dedup_points(points, closed=True)
    n = len(pts)
    if n < 3:
        raise ValueError(
            f"Komórka zamknięta wymaga co najmniej 3 różnych punktów (jest {n})."
        )

    signed_area2 = sum(
        pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
        for i in range(n)
    )
    if abs(signed_area2) < 1e-6:
        raise ValueError(
            "Wielokąt jest zdegenerowany (zerowy obszar / punkty współliniowe)."
        )
    ccw = signed_area2 > 0
    winding = "CCW" if ccw else "CW"

    # corner kind at each vertex (turn between incoming and outgoing edge)
    kinds = [_corner_type(pts[(i - 1) % n], pts[i], pts[(i + 1) % n], ccw) for i in range(n)]

    t = thickness
    outer_leg = sys.outer_corner.leg_cm
    inner_leg = sys.inner_corner.leg_cm

    def offset(kind: str | None) -> float:
        # outer face length change at a vertex: +t/2 convex, -t/2 concave, 0 collinear
        return t / 2 if kind == "outer" else (-t / 2 if kind == "inner" else 0.0)

    def leg_on_outer(kind: str | None) -> float:
        return outer_leg if kind == "outer" else (inner_leg if kind == "inner" else 0.0)

    def leg_on_inner(kind: str | None) -> float:
        return inner_leg if kind == "outer" else (outer_leg if kind == "inner" else 0.0)

    walls: list[CellWall] = []
    for i in range(n):
        p0, p1 = pts[i], pts[(i + 1) % n]
        axis_len = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        ks, ke = kinds[i], kinds[(i + 1) % n]
        outer_len = axis_len + offset(ks) + offset(ke)
        inner_len = axis_len - offset(ks) - offset(ke)
        outer_panel = outer_len - leg_on_outer(ks) - leg_on_outer(ke)
        inner_panel = inner_len - leg_on_inner(ks) - leg_on_inner(ke)
        walls.append(CellWall(
            label=chr(ord("A") + i) if i < 26 else f"S{i + 1}",
            axis_length=axis_len,
            outer_length=max(outer_panel, 0.0),
            inner_length=max(inner_panel, 0.0),
            height=height,
            end_is_corner=ke is not None,
            start_is_corner=ks is not None,
            start_kind=ks,
            end_kind=ke,
        ))
    return walls, winding


def calculate_cell(
    walls: list[CellWall],
    system: str = "BAUTEKK",
    pressure_kn_m2: float | None = None,
    wall_thickness_cm: float | None = None,
    inner_corner_leg_cm: float | None = None,
    polygon_points: list[tuple[float, float]] | None = None,
    polygon_winding: str | None = None,
    available_widths: tuple[int, ...] | None = None,
) -> dict:
    """Full BOM + layout DRAFT for a CLOSED cell with ALIGNED joints.

    The flat panel run is IDENTICAL on both faces so the vertical joints line up
    (ties/connectors pass through a shared joint). The corner absorbs the
    outer/inner length difference:
        outer face at a convex corner = "0" corner + a filler panel of width
            (inner_leg + thickness);
        inner face at a convex corner = the inner corner element (leg x leg).
    For this to align, the filler width must be a real stock panel, i.e.
        filler = inner_leg + thickness   (e.g. 20 + 25 = 45).
    """
    sys = get_system(system)
    if not walls:
        raise ValueError("Nie podano ścian.")
    if wall_thickness_cm is None or not math.isfinite(wall_thickness_cm) or wall_thickness_cm <= 0:
        raise ValueError("Tryb komórki zamkniętej wymaga dodatniej grubości ściany (wall_thickness_cm).")
    t = float(wall_thickness_cm)

    widths, warnings = _resolve_widths(sys, available_widths)
    # inner corner leg defaults to the catalog value (BAUTEKK: 15x15, on the
    # invoice). The corner filler width is fixed by the alignment rule
    # filler = inner_leg + thickness (e.g. 15 + 25 = 40 -> H40), so the flat
    # panel joints coincide on both faces regardless of the exact leg.
    inner_leg = float(inner_corner_leg_cm) if inner_corner_leg_cm else float(sys.inner_corner.leg_cm)
    if inner_leg <= 0:
        raise ValueError("Noga narożnika wewn. musi być dodatnia (wyrównanie fug w komórce zamkniętej).")
    filler_w = int(round(inner_leg + t))
    joints_align = True  # guaranteed by construction (filler = leg + thickness)
    filler_is_panel = filler_w in set(widths)
    filler_item = (f"Płyta {filler_w}x{{h}} (wypełniacz narożnikowy)" if filler_is_panel
                   else f"H{filler_w} wypełniacz narożnikowy (płyta {filler_w})")
    if not filler_is_panel:
        warnings.append(
            f"Wypełniacz narożnikowy H{filler_w} (= noga wewn. {int(inner_leg)} + grubość "
            f"{int(t)}) to OSOBNY element narożnikowy do zamówienia, nie standardowa płyta ścienna."
        )
    if not sys.verified:
        warnings.append(
            f"Katalog {sys.name} używa wartości DOMYŚLNYCH — zweryfikuj je w "
            f"oficjalnym katalogu BAUKRANE ({sys.notes})"
        )
    if pressure_kn_m2 is not None and pressure_kn_m2 > sys.max_pressure_kn_m2:
        warnings.append(
            f"Podane ciśnienie betonu {pressure_kn_m2} kN/m2 przekracza limit "
            f"systemu {sys.name} ({sys.max_pressure_kn_m2} kN/m2). Rozważ mocniejszy system."
        )
    warnings.append(
        f"Komórka zamknięta (fugi zgrane): ten sam bieg płyt na obu stronach; "
        f"każdy narożnik = zewn. „0” + 2×{filler_w} wypełniacz + wewn. {int(inner_leg)}×{int(inner_leg)}."
    )

    panel_totals: dict[str, int] = {}
    filler_by_height: dict[int, int] = {}
    outer_by_height: dict[int, int] = {}
    inner_by_height: dict[int, int] = {}
    total_axis_length = 0.0
    total_ties = 0
    vertical_joints = 0
    connectors = 0
    default_cpj = 3
    area_geom = 0.0
    timber_area = 0.0
    overshoot_area = 0.0
    timber_lm_total = 0.0
    filler_total = 0
    seg_out: list[dict] = []

    def reduction(is_corner: bool) -> float:
        return (inner_leg + t / 2) if is_corner else 0.0

    for w in walls:
        _validate_dimension(w.height, "height", w.label)
        courses, overshoot = cover_height(int(round(w.height)), sys.panel_heights)
        course_height_sum = sum(h * nh for h, nh in courses.items())

        flat_run = w.axis_length - reduction(w.start_is_corner) - reduction(w.end_is_corner)
        _validate_dimension(flat_run, "flat run (axis minus corners)", w.label)
        cols, timber = cover_width(int(round(flat_run)), widths)
        flat_seq = [cw for cw in sorted(cols, reverse=True) for _ in range(cols[cw])]
        flat_cols = sum(cols.values())
        flat_width_sum = sum(cw * ncw for cw, ncw in cols.items())

        ends = [(w.start_is_corner, w.start_kind), (w.end_is_corner, w.end_kind)]
        convex_ends = sum(1 for c, k in ends if c and k == "outer")
        concave_ends = sum(1 for c, k in ends if c and k == "inner")
        corner_ends = sum(1 for c, _ in ends if c)

        # flat panels: shared run counted on BOTH faces
        for cw, ncw in cols.items():
            for h, nh in courses.items():
                panel_totals[f"{cw}x{h}"] = panel_totals.get(f"{cw}x{h}", 0) + ncw * nh * 2
        # corner filler (H{filler_w}): one per corner end, tracked separately from
        # regular wall panels (it is a dedicated corner element unless it happens
        # to equal a stock panel width).
        for h, nh in courses.items():
            filler_by_height[h] = filler_by_height.get(h, 0) + corner_ends * nh
            filler_total += corner_ends * nh

        # corner elements: 1 outer '0' + 1 inner leg element per corner vertex/course
        if w.end_is_corner:
            for h, nh in courses.items():
                outer_by_height[h] = outer_by_height.get(h, 0) + nh
                inner_by_height[h] = inner_by_height.get(h, 0) + nh

        outer_face_len = flat_run + convex_ends * filler_w + concave_ends * inner_leg
        inner_face_len = flat_run + convex_ends * inner_leg + concave_ends * filler_w
        area_geom += (outer_face_len + inner_face_len) / 100.0 * (w.height / 100.0)
        # both faces share the flat run -> timber & overshoot count x2
        timber_area += 2 * (timber / 100.0) * (course_height_sum / 100.0)
        timber_lm_total += 2 * (timber / 100.0)
        panels_width_both = 2 * flat_width_sum + corner_ends * filler_w
        overshoot_area += (panels_width_both / 100.0) * (overshoot / 100.0)

        # joints per face = (flat panels + corner pieces) - 1; shared flat -> aligned
        joints_per_face = max(flat_cols + corner_ends - 1, 0)
        for h, nh in courses.items():
            vertical_joints += 2 * joints_per_face * nh
            connectors += 2 * joints_per_face * nh * sys.connectors_per_joint.get(h, default_cpj)

        # ties + props per WALL (shared by both faces), on the axis
        total_ties += max(math.ceil(w.axis_length / sys.tie_spacing_h), 1) * \
            max(math.ceil(w.height / sys.tie_spacing_v), 1)
        total_axis_length += w.axis_length

        # ---- layout draft (one course; repeats per course) ----
        def face_pieces(is_outer: bool) -> list[dict]:
            pieces: list[dict] = []

            def end_piece(is_corner, kind):
                if not is_corner:
                    return None
                # convex: outer=filler, inner=corner leg ; concave: swapped
                outer_gets_filler = (kind == "outer")
                if is_outer == outer_gets_filler:
                    return {"type": "filler_panel", "width_cm": filler_w}
                return {"type": "inner_corner", "width_cm": int(inner_leg)}

            sp = end_piece(w.start_is_corner, w.start_kind)
            if w.start_is_corner:
                pieces.append({"type": "outer_corner_0", "width_cm": 0} if
                              (is_outer == (w.start_kind == "outer")) else
                              {"type": "inner_corner", "width_cm": int(inner_leg)})
            if sp and sp["type"] == "filler_panel":
                pieces.append(sp)
            for cw in flat_seq:
                pieces.append({"type": "panel", "width_cm": cw})
            if timber > 0:
                pieces.append({"type": "timber", "width_cm": int(timber)})
            ep = end_piece(w.end_is_corner, w.end_kind)
            if ep and ep["type"] == "filler_panel":
                pieces.append(ep)
            if w.end_is_corner:
                pieces.append({"type": "outer_corner_0", "width_cm": 0} if
                              (is_outer == (w.end_kind == "outer")) else
                              {"type": "inner_corner", "width_cm": int(inner_leg)})
            return pieces

        v_layout = [f"{h} cm" for h in sorted(courses, reverse=True) for _ in range(courses[h])]
        if overshoot > 0:
            v_layout.append(f"(nadwyżka górna {overshoot} cm)")
        seg_out.append({
            "label": w.label,
            "axis_length_cm": round(w.axis_length, 1),
            "height_cm": round(w.height, 1),
            "flat_run_cm": round(flat_run, 1),
            "flat_panels_shared": [f"{cw} cm" for cw in flat_seq] + ([f"deska {int(timber)} cm"] if timber else []),
            "outer_face": {"length_cm": round(outer_face_len, 1), "pieces": face_pieces(True)},
            "inner_face": {"length_cm": round(inner_face_len, 1), "pieces": face_pieces(False)},
            "courses_vertical": v_layout,
            "joints_aligned": joints_align,
        })

    total_panels = sum(panel_totals.values())
    outer_corners = sum(outer_by_height.values())
    inner_corners = sum(inner_by_height.values())
    total_corners = outer_corners + inner_corners
    props = math.ceil(total_axis_length / sys.max_prop_spacing) if total_axis_length else 0
    area_geom = round(area_geom, 3)

    bases = {
        "tie": total_ties,
        "panel": total_panels,
        "vertical_joint": vertical_joints,
        "corner": total_corners,
        "prop": props,
        "area_m2": area_geom,
    }
    hardware = _assemble_hardware(sys, bases, connectors)
    overshoot_pct = round(overshoot_area / area_geom * 100, 1) if area_geom else 0.0
    timber_pct = round(timber_area / area_geom * 100, 1) if area_geom else 0.0

    # corner BOM reflects the chosen inner leg (may differ from the catalog SKU)
    bom_corners = []
    catalog_leg = float(sys.inner_corner.leg_cm)
    for h, qty in sorted(outer_by_height.items(), reverse=True):
        bom_corners.append({
            "kind": "outer", "height_cm": h,
            "description": sys.outer_corner.description.replace("{h}", str(h)),
            "article_no": sys.outer_corner.article_for(h), "quantity": qty,
        })
    for h, qty in sorted(inner_by_height.items(), reverse=True):
        matches_catalog = abs(inner_leg - catalog_leg) < 1e-6
        bom_corners.append({
            "kind": "inner", "height_cm": h,
            "description": f"{sys.name} narożnik wewn. {int(inner_leg)}x{int(inner_leg)}x{h}",
            "article_no": sys.inner_corner.article_for(h) if matches_catalog else None,
            "quantity": qty,
            "note": None if matches_catalog else f"noga spoza katalogu {int(inner_leg)} cm — do zamówienia osobno",
        })

    bom_fillers = []
    filler_area = 0.0
    for h, qty in sorted(filler_by_height.items(), reverse=True):
        filler_area += (filler_w / 100.0) * (h / 100.0) * qty
        bom_fillers.append({
            "height_cm": h,
            "description": (f"{sys.name} płyta {filler_w}x{h} (jako wypełniacz narożnikowy)"
                            if filler_is_panel
                            else f"H{filler_w} wypełniacz narożnikowy {filler_w}x{h} (element dedykowany)"),
            "width_cm": filler_w,
            "is_standard_panel": filler_is_panel,
            "quantity": qty,
            "note": None if filler_is_panel else "dedykowany wypełniacz narożnikowy — zamówić osobno",
        })

    return {
        "system": sys.name,
        "catalog_version": CATALOG_VERSION,
        "catalog_verified": sys.verified,
        "max_pressure_kn_m2": sys.max_pressure_kn_m2,
        "units": UNITS,
        "corner_template": {
            "outer_corner": "0 (zero) + wypełniacz z każdej strony",
            "outer_filler_width_cm": filler_w,
            "filler_is_standard_panel": filler_is_panel,
            "inner_corner_cm": f"{int(inner_leg)}x{int(inner_leg)}",
            "alignment_rule": "wypełniacz = noga_wewn. + grubość → ten sam bieg płyt na obu stronach (fugi zgrane)",
            "joints_align": joints_align,
        },
        "assumptions": {
            "mode": "closed_cell_aligned",
            "wall_thickness_cm": t,
            "inner_corner_leg_cm": inner_leg,
            "corner_rule": "każdy narożnik = 1 zewn. „0” + 2 wypełniacze + 1 element narożnika wewn., na warstwę",
            "joint_rule": "ten sam bieg płyt na obu stronach — pionowe fugi się pokrywają",
            "panel_widths_used_cm": list(widths),
            "tie_spacing_cm": {"horizontal": sys.tie_spacing_h, "vertical": sys.tie_spacing_v},
            "max_prop_spacing_cm": sys.max_prop_spacing,
            "panel_heights_considered_cm": list(sys.panel_heights),
            "width_policy": "minimalizuj resztkę deski, potem liczbę płyt",
            "height_policy": "układaj płyty w pionie do wysokości ściany; nadwyżkę górną nie tnij",
            "hardware_coeffs": "przybliżone; skalibruj wg DTR",
        },
        "input_echo": {
            "geometry_source": "closed_cell",
            "polygon_points_cm": [list(p) for p in polygon_points] if polygon_points else None,
            "polygon_winding": polygon_winding,
            "walls_used": [
                {"label": w.label, "axis_length_cm": round(w.axis_length, 1),
                 "start_corner": w.start_kind, "end_corner": w.end_kind}
                for w in walls
            ],
        },
        "summary": {
            "wall_count": len(walls),
            "total_axis_length_m": round(total_axis_length / 100.0, 2),
            "formwork_area_m2": area_geom,
            "total_panels": total_panels,
            "corner_filler_panels": filler_total,
            "total_corners": total_corners,
            "outer_corners": outer_corners,
            "inner_corners": inner_corners,
            "timber_infill_lm": round(timber_lm_total, 2),
        },
        "bom": {
            "panels": _bom_panels(sys, panel_totals),
            "corner_fillers": bom_fillers,
            "corners": bom_corners,
            "hardware": hardware,
        },
        "layout_draft": seg_out,
        "reconciliation": {
            "area_from_geometry_m2": area_geom,
            "area_from_panels_m2": round(_panel_area(panel_totals) + filler_area, 3),
            "vertical_overshoot_m2": round(overshoot_area, 3),
            "vertical_overshoot_pct": overshoot_pct,
            "horizontal_timber_m2": round(timber_area, 3),
            "horizontal_timber_pct": timber_pct,
            "note": "Powierzchnia płyt + wypełniaczy; narożnik „0” nie ma szerokości.",
        },
        "warnings": warnings,
        "disclaimer": DISCLAIMER,
        "method": (
            "Planer komórki zamkniętej: wspólny bieg płyt na ścianę (fugi zgrane), "
            f"narożnik = 0 + 2×{filler_w} wypełniacz + wewn. {int(inner_leg)}×{int(inner_leg)}, "
            "wiązania/podpory na osi ściany, złącza wg DTR na spoinę."
        ),
    }


def _parse_size(k: str) -> tuple[int, int]:
    w, h = k.split("x")
    return int(w), int(h)


def _panel_area(panel_totals: dict[str, int]) -> float:
    total = 0.0
    for k, qty in panel_totals.items():
        w, h = _parse_size(k)
        total += (w / 100.0) * (h / 100.0) * qty
    return total


# --- hard stock limits ----------------------------------------------------
# Reserved (non-width) stock keys.
_CORNER_KEYS = {"outer_corner", "inner_corner", "filler"}


def _greedy_cover(missing_cm: int, surplus: dict[int, int]) -> tuple[dict[int, int], int]:
    """Cover ``missing_cm`` using surplus panels, maximizing covered length.

    Bounded subset-sum (respecting per-width surplus counts) that gets as close to
    ``missing_cm`` as possible without exceeding it. Consumes (mutates) the
    ``surplus`` counts and returns the widths used and the leftover length still
    missing (-> order more / timber). A non-binding suggestion, not a re-plan.
    """
    # reachable_sum -> {width: count} using whole surplus panels, sum <= missing
    reachable: dict[int, dict[int, int]] = {0: {}}
    for w, cnt in surplus.items():
        if cnt <= 0:
            continue
        updated = dict(reachable)
        for base, used in reachable.items():
            for k in range(1, cnt + 1):
                s = base + k * w
                if s > missing_cm:
                    break
                if s not in updated:
                    nu = dict(used)
                    nu[w] = nu.get(w, 0) + k
                    updated[s] = nu
        reachable = updated
    best = max(reachable)
    used = reachable[best]
    for w, n in used.items():
        surplus[w] -= n
    return used, missing_cm - best


def reconcile_stock(result: dict, stock: dict[str, int]) -> dict:
    """Compare a closed-cell (or segment) BOM against a hard stock inventory.

    ``stock`` maps item -> quantity on hand. Keys are panel WIDTHS as strings
    (e.g. "90", "45"), plus the reserved keys ``outer_corner``, ``inner_corner``
    and ``filler`` (the last only used when the corner filler is a dedicated
    element rather than a standard panel).

    Returns a per-item reconciliation (needed / available / shortfall / surplus),
    a ``fits_in_stock`` flag, and a greedy substitution SUGGESTION for any panel
    width that is short (cover the missing length from surplus widths). The
    suggestion does not guarantee aligned joints - re-planning may be required.
    """
    stock = {str(k): int(v) for k, v in stock.items()}

    demand: dict[str, int] = {}
    for p in result.get("bom", {}).get("panels", []):
        w = p["size"].split("x")[0]
        demand[w] = demand.get(w, 0) + p["quantity"]

    ct = result.get("corner_template")
    summary = result.get("summary", {})
    if ct:
        fw = str(ct["outer_filler_width_cm"])
        filler_qty = summary.get("corner_filler_panels", 0)
        if ct.get("filler_is_standard_panel"):
            demand[fw] = demand.get(fw, 0) + filler_qty      # shares the width stock
        elif filler_qty:
            demand["filler"] = filler_qty
        demand["outer_corner"] = summary.get("outer_corners", 0)
        demand["inner_corner"] = summary.get("inner_corners", 0)

    lines: list[dict] = []
    surplus_widths: dict[int, int] = {}
    fits = True
    for item in sorted(demand, key=lambda k: (k in _CORNER_KEYS, -int(k) if k.isdigit() else 0)):
        need = demand[item]
        avail = stock.get(item, 0)
        short = max(need - avail, 0)
        sur = max(avail - need, 0)
        if short > 0:
            fits = False
        lines.append({
            "item": item if item in _CORNER_KEYS else f"płyta {item} cm",
            "key": item,
            "needed": need,
            "available": avail,
            "shortfall": short,
            "surplus": sur,
        })
        if item.isdigit() and sur > 0:
            surplus_widths[int(item)] = sur

    suggestions: list[dict] = []
    for line in lines:
        if line["key"].isdigit() and line["shortfall"] > 0:
            width = int(line["key"])
            missing_cm = line["shortfall"] * width
            used, remaining = _greedy_cover(missing_cm, surplus_widths)
            suggestions.append({
                "short_item": f"płyta {width} cm",
                "short_qty": line["shortfall"],
                "missing_length_cm": missing_cm,
                "cover_with": {f"{w} cm": n for w, n in sorted(used.items(), reverse=True)},
                "uncovered_cm": remaining,
                "note": ("pokryto z nadwyżki magazynowej (sprawdź wyrównanie fug)"
                         if remaining == 0
                         else f"brakuje jeszcze {remaining} cm — domów lub użyj deski"),
            })

    return {
        "fits_in_stock": fits,
        "items": lines,
        "substitution_suggestions": suggestions,
        "note": ("Twarda kontrola stanu magazynowego. Zamienniki to zachłanne "
                 "propozycje niewiążące; zachowanie pionowych fug może wymagać nowego planu."),
    }
