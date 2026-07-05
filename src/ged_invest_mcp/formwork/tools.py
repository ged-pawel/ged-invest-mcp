"""Formwork MCP tools, registered on a shared FastMCP instance."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from . import calculator, catalog, drawing, pressure


class SegmentInput(BaseModel):
    """A single straight wall segment."""

    length: float = Field(..., gt=0, description="Wall segment length in [cm]")
    height: float = Field(..., gt=0, description="Wall height in [cm]")
    corner_at_end: Literal["outer", "inner"] | None = Field(
        None,
        description="Corner type at the end of the segment: 'outer' (convex), 'inner' (concave), or None",
    )
    label: str | None = Field(None, description="Optional segment name, e.g. 'A'")


def register(mcp: FastMCP) -> None:
    """Register all formwork tools on the given MCP instance."""

    @mcp.tool()
    def list_formwork_systems() -> dict[str, Any]:
        """List available wall formwork systems with their parameters.

        Call this FIRST to learn the available systems, panel sizes and concrete
        pressure limits before computing a takeoff.
        """
        return {
            "systems": [
                {
                    "name": s.name,
                    "max_pressure_kn_m2": s.max_pressure_kn_m2,
                    "panel_widths_cm": list(s.panel_widths),
                    "panel_heights_cm": list(s.panel_heights),
                    "catalog_verified": s.verified,
                    "notes": s.notes,
                }
                for s in catalog.SYSTEMS.values()
            ]
        }

    @mcp.tool()
    def formwork_catalog(system: str) -> dict[str, Any]:
        """Return the full panel/corner catalog and parameters of a system.

        Args:
            system: system name ("BAUTEKK", "BAUFRAME", "BAUSCHAL").
        """
        s = catalog.get_system(system)
        return {
            "system": s.name,
            "max_pressure_kn_m2": s.max_pressure_kn_m2,
            "catalog_verified": s.verified,
            "panels": [
                {"size": p.name, "width": p.width, "height": p.height,
                 "article_no": p.article_no, "area_m2": round(p.area_m2, 3)}
                for p in s.panels()
            ],
            "corners": [
                {"kind": s.outer_corner.kind, "description": s.outer_corner.description,
                 "article_no": s.outer_corner.article_no},
                {"kind": s.inner_corner.kind, "description": s.inner_corner.description,
                 "article_no": s.inner_corner.article_no},
            ],
            "tie_spacing_cm": {"horizontal": s.tie_spacing_h, "vertical": s.tie_spacing_v},
            "max_prop_spacing_cm": s.max_prop_spacing,
            "notes": s.notes,
        }

    @mcp.tool()
    def count_formwork(
        system: Annotated[str, Field(description="System: BAUTEKK, BAUFRAME or BAUSCHAL")],
        segments: Annotated[
            list[SegmentInput] | None,
            Field(description="List of wall segments (length+height+corner). Use this OR 'polygon'."),
        ] = None,
        polygon: Annotated[
            list[list[float]] | None,
            Field(description="Alternative: wall outline as a list of points [[x,y],...] in [cm]. Corners and lengths are detected automatically."),
        ] = None,
        height: Annotated[
            float | None,
            Field(description="Wall height [cm] - required when 'polygon' is given."),
        ] = None,
        wall_thickness: Annotated[
            float | None,
            Field(gt=0, description="Wall thickness [cm]. When given with 'polygon', enables CLOSED-CELL mode: aligned joints on both faces + corner template (outer '0' + 2 filler panels, inner leg x leg corner)."),
        ] = None,
        inner_corner_leg: Annotated[
            float | None,
            Field(gt=0, description="Inner corner element leg [cm] (closed-cell). If omitted it is auto-chosen so filler = leg + thickness is a stock panel (e.g. thickness 25 -> leg 20, filler 45). E.g. set 20 to buy a 20x20 inner corner and use 45 panels."),
        ] = None,
        pressure_kn_m2: Annotated[
            float | None,
            Field(description="Optional fresh concrete pressure [kN/m2] to validate system choice."),
        ] = None,
        faces: Annotated[
            int,
            Field(ge=1, le=2, description="Formwork faces per segment: 2 if input is wall centerlines (default), 1 if you already list each face separately."),
        ] = 2,
        available_panel_widths: Annotated[
            list[int] | None,
            Field(description="Restrict panel selection to widths actually IN STOCK [cm], e.g. from the invoice (e.g. [90,75,70,60,45,25] - note: no 50). Widths not in the catalog are ignored."),
        ] = None,
        render_svg: Annotated[
            bool,
            Field(description="If true (closed-cell mode only), also return a deterministic SVG drawing under 'layout_svg' (plan for rectangles + per-wall elevations with aligned joints)."),
        ] = False,
        available_stock: Annotated[
            dict[str, int] | None,
            Field(description="HARD quantity limits on hand. Keys: panel WIDTHS as strings (e.g. {\"90\":58,\"75\":26,\"70\":24,\"60\":26,\"45\":20,\"25\":10}) plus reserved keys 'outer_corner', 'inner_corner' and 'filler' (dedicated filler only). Adds a 'stock_check' with shortfalls and substitution suggestions."),
        ] = None,
    ) -> dict[str, Any]:
        """Compute the full, auditable bill of materials (BOM) for wall formwork.

        Provide geometry in ONE of two ways:
          1) `segments` - when you know each wall length and corner type,
          2) `polygon` + `height` - when you have an outline (coordinates) from a
             drawing; corner types (outer/inner) and side lengths are detected
             automatically.

        IMPORTANT: by default each segment is counted with TWO faces (assuming the
        input describes wall centerlines). If your geometry already lists both
        faces separately, pass faces=1 to avoid doubling.

        Returns a deterministic result including: an input echo (the geometry
        actually used), assumptions, a summary, the BOM (panels, corners,
        hardware), a per-segment panel layout, and an area reconciliation.
        """
        if segments and polygon:
            raise ValueError("Provide either 'segments' or 'polygon', not both.")

        widths = tuple(available_panel_widths) if available_panel_widths else None

        if polygon:
            if height is None:
                raise ValueError("'height' [cm] is required when 'polygon' is given.")
            points = [(float(p[0]), float(p[1])) for p in polygon]
            if wall_thickness is not None:
                walls, winding = calculator.polygon_to_cell(
                    points, height=height, thickness=wall_thickness, system=system,
                )
                cell = calculator.calculate_cell(
                    walls, system=system, pressure_kn_m2=pressure_kn_m2,
                    wall_thickness_cm=wall_thickness,
                    inner_corner_leg_cm=inner_corner_leg,
                    polygon_points=points, polygon_winding=winding,
                    available_widths=widths,
                )
                if render_svg:
                    cell["layout_svg"] = drawing.render_layout_svg(cell)
                if available_stock:
                    cell["stock_check"] = calculator.reconcile_stock(cell, available_stock)
                return cell
            wall, winding = calculator.polygon_to_segments(points, height=height, closed=True)
            res = calculator.calculate(
                wall, system=system, pressure_kn_m2=pressure_kn_m2, faces=faces,
                geometry_source="polygon", polygon_points=points, polygon_winding=winding,
                available_widths=widths,
            )
            if available_stock:
                res["stock_check"] = calculator.reconcile_stock(res, available_stock)
            return res
        if segments:
            wall = [
                calculator.WallSegment(
                    length=s.length,
                    height=s.height,
                    corner_at_end=s.corner_at_end,
                    label=s.label,
                )
                for s in segments
            ]
            res = calculator.calculate(
                wall, system=system, pressure_kn_m2=pressure_kn_m2, faces=faces,
                geometry_source="segments", available_widths=widths,
            )
            if available_stock:
                res["stock_check"] = calculator.reconcile_stock(res, available_stock)
            return res
        raise ValueError("You must provide 'segments' or 'polygon'.")

    @mcp.tool()
    def draw_formwork_layout(
        system: Annotated[str, Field(description="System: BAUTEKK, BAUFRAME or BAUSCHAL")],
        polygon: Annotated[
            list[list[float]],
            Field(description="Closed wall outline as points [[x,y],...] in [cm] (axis/centerline)."),
        ],
        height: Annotated[float, Field(gt=0, description="Wall height [cm].")],
        wall_thickness: Annotated[float, Field(gt=0, description="Wall thickness [cm].")],
        inner_corner_leg: Annotated[
            float | None,
            Field(gt=0, description="Inner corner leg [cm]; defaults to the catalog value (filler = leg + thickness)."),
        ] = None,
        available_panel_widths: Annotated[
            list[int] | None,
            Field(description="Restrict panels to in-stock widths [cm]."),
        ] = None,
        scale: Annotated[
            float,
            Field(gt=0, le=2, description="SVG scale in pixels per centimetre (default 0.32)."),
        ] = 0.32,
    ) -> dict[str, Any]:
        """Render a deterministic SVG drawing of a closed-cell formwork layout.

        Returns the plan (for rectangular outlines) plus per-wall elevations of the
        outer and inner faces with the aligned vertical joints highlighted. The
        drawing is generated from the same deterministic layout the BOM uses, so it
        always matches `count_formwork`.
        """
        points = [(float(p[0]), float(p[1])) for p in polygon]
        widths = tuple(available_panel_widths) if available_panel_widths else None
        walls, winding = calculator.polygon_to_cell(
            points, height=height, thickness=wall_thickness, system=system,
        )
        cell = calculator.calculate_cell(
            walls, system=system, wall_thickness_cm=wall_thickness,
            inner_corner_leg_cm=inner_corner_leg,
            polygon_points=points, polygon_winding=winding,
            available_widths=widths,
        )
        return {
            "system": cell["system"],
            "corner_template": cell["corner_template"],
            "summary": cell["summary"],
            "svg_mime_type": "image/svg+xml",
            "layout_svg": drawing.render_layout_svg(cell, scale=scale),
        }

    @mcp.tool()
    def concrete_pressure_check(
        pouring_rate_m_per_h: Annotated[
            float, Field(gt=0, description="Mean vertical rise (pouring) rate v [m/h].")
        ],
        wall_height_m: Annotated[
            float, Field(gt=0, description="Casting height H [m] (for the hydrostatic cap).")
        ],
        consistency_class: Annotated[
            str,
            Field(description="Concrete consistency class: F1, F2, F3, F4, F5, F6 or SCC (SVB alias). Default F3."),
        ] = "F3",
        setting_time_h: Annotated[
            float,
            Field(gt=0, description="Final setting time tE [h], valid 5-20 (clamped). Default 5."),
        ] = 5.0,
        concrete_unit_weight_kn_m3: Annotated[
            float, Field(gt=0, description="Specific concrete weight gamma_c [kN/m3]. Default 25.")
        ] = 25.0,
        system: Annotated[
            str | None,
            Field(description="Optional formwork system name to take its pressure limit from (BAUTEKK, BAUFRAME, BAUSCHAL)."),
        ] = None,
        allowed_pressure_kn_m2: Annotated[
            float | None,
            Field(description="Explicit allowed pressure [kN/m2]. Overrides the system limit if both are given."),
        ] = None,
        max_pouring_rate_m_per_h: Annotated[
            float | None,
            Field(description="Optional process cap on pouring rate [m/h] (DTR BAUTEKK: 2 m/h)."),
        ] = None,
    ) -> dict[str, Any]:
        """Check fresh concrete pressure on the formwork per DIN 18218:2010.

        Computes the maximum characteristic lateral pressure from the pouring rate,
        consistency class and setting time, caps it by the hydrostatic pressure, and
        compares it to the formwork system's limit. Also reports the maximum pouring
        rate that would keep the pressure within the limit.

        This is a SIMPLIFIED advisory estimate at the 15 C reference (no temperature/
        admixture corrections). The final determination of the real fresh concrete
        pressure and safe pouring rate is the site manager's responsibility.
        """
        limit = allowed_pressure_kn_m2
        rate_cap = max_pouring_rate_m_per_h
        if system is not None:
            s = catalog.get_system(system)
            if limit is None:
                limit = s.max_pressure_kn_m2
            if rate_cap is None and s.name == "BAUTEKK":
                rate_cap = 2.0  # DTR BauTekk process limit
        return pressure.concrete_pressure(
            pouring_rate_m_per_h=pouring_rate_m_per_h,
            wall_height_m=wall_height_m,
            consistency_class=consistency_class,
            setting_time_h=setting_time_h,
            concrete_unit_weight_kn_m3=concrete_unit_weight_kn_m3,
            allowed_pressure_kn_m2=limit,
            max_pouring_rate_m_per_h=rate_cap,
        )
