"""Tests for the deterministic formwork calculator."""

import pytest

from ged_invest_mcp.formwork import calculator
from ged_invest_mcp.formwork.calculator import WallSegment


def test_cover_width_exact():
    combo, timber = calculator.cover_width(450, (90, 75, 70, 60, 45, 25))
    assert timber == 0
    assert sum(k * v for k, v in combo.items()) == 450


def test_cover_width_leftover_is_timber():
    combo, timber = calculator.cover_width(193, (90, 75, 70, 60, 45, 25))
    assert timber == sum(k * v for k, v in combo.items()) * 0 + (193 - sum(k * v for k, v in combo.items()))
    assert 0 <= timber < 25


def test_cover_height_stacks_no_timber():
    # 280 with only 150 -> stack 2x150, overshoot 20 (never timber vertically)
    courses, overshoot = calculator.cover_height(280, (150,))
    assert courses == {150: 2}
    assert overshoot == 20


def test_cover_height_below_smallest_still_one_panel():
    # 100 with only 150 -> one panel, overshoot 50 (NOT zero panels)
    courses, overshoot = calculator.cover_height(100, (150,))
    assert courses == {150: 1}
    assert overshoot == 50


def test_short_wall_produces_panels_not_zero():
    seg = [WallSegment(length=300, height=100, corner_at_end="outer", label="A")]
    result = calculator.calculate(seg, system="BAUTEKK")
    assert result["summary"]["total_panels"] > 0


def test_simple_rectangle_segments():
    segments = [
        WallSegment(length=400, height=150, corner_at_end="outer", label="A"),
        WallSegment(length=300, height=150, corner_at_end="outer", label="B"),
        WallSegment(length=400, height=150, corner_at_end="outer", label="C"),
        WallSegment(length=300, height=150, corner_at_end="outer", label="D"),
    ]
    result = calculator.calculate(segments, system="BAUTEKK")
    assert result["system"] == "BAUTEKK"
    assert result["summary"]["total_length_m"] == 14.0
    assert result["summary"]["formwork_area_m2"] == 42.0
    outer = [c for c in result["bom"]["corners"] if c["kind"] == "outer"]
    assert outer and outer[0]["quantity"] == 4
    assert result["summary"]["total_panels"] > 0


def test_faces_one_halves_panels():
    seg = [WallSegment(length=360, height=150, corner_at_end="outer", label="A")]
    two = calculator.calculate(seg, system="BAUTEKK", faces=2)["summary"]["total_panels"]
    one = calculator.calculate(seg, system="BAUTEKK", faces=1)["summary"]["total_panels"]
    assert two == 2 * one


def test_invalid_faces_rejected():
    seg = [WallSegment(length=360, height=150, corner_at_end="outer")]
    with pytest.raises(ValueError):
        calculator.calculate(seg, system="BAUTEKK", faces=3)


def test_non_positive_length_rejected():
    seg = [WallSegment(length=0, height=150, corner_at_end="outer", label="A")]
    with pytest.raises(ValueError):
        calculator.calculate(seg, system="BAUTEKK")


def test_polygon_detects_corners_and_winding():
    points = [(0, 0), (840, 0), (840, 700), (0, 700)]
    segments, winding = calculator.polygon_to_segments(points, height=150)
    assert winding in ("CW", "CCW")
    assert len(segments) == 4
    assert all(s.corner_at_end == "outer" for s in segments)
    assert abs(segments[0].length - 840) < 1e-6


def test_polygon_l_shape_has_inner_corner():
    points = [(0, 0), (840, 0), (840, 290), (310, 290), (310, 700), (0, 700)]
    segments, _ = calculator.polygon_to_segments(points, height=150)
    kinds = [s.corner_at_end for s in segments]
    assert kinds.count("outer") == 5
    assert kinds.count("inner") == 1


def test_collinear_vertex_has_no_corner():
    # a redundant midpoint on a straight side must NOT create a phantom corner
    points = [(0, 0), (400, 0), (800, 0), (800, 400), (0, 400)]
    segments, _ = calculator.polygon_to_segments(points, height=150)
    none_corners = [s for s in segments if s.corner_at_end is None]
    assert len(none_corners) == 1


def test_hardware_families_present():
    seg = [WallSegment(length=400, height=150, corner_at_end="outer", label="A")]
    result = calculator.calculate(seg, system="BAUTEKK")
    items = {h["item"] for h in result["bom"]["hardware"]}
    assert any("Tie rod" in i for i in items)
    assert any("pin" in i.lower() for i in items)
    assert any("wedge" in i.lower() for i in items)
    assert any("prop" in i.lower() for i in items)


def test_audit_fields_present():
    points = [(0, 0), (400, 0), (400, 300), (0, 300)]
    segs, winding = calculator.polygon_to_segments(points, height=150)
    result = calculator.calculate(
        segs, system="BAUTEKK", geometry_source="polygon",
        polygon_points=points, polygon_winding=winding,
    )
    assert result["input_echo"]["geometry_source"] == "polygon"
    assert result["input_echo"]["polygon_winding"] == winding
    assert result["input_echo"]["segments_used"]
    assert "assumptions" in result and result["assumptions"]["faces"] == 2
    assert "reconciliation" in result
    assert result["units"]["length"] == "cm"
    assert result["disclaimer"]
    assert result["catalog_version"]


def test_nan_and_inf_rejected():
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            calculator.calculate([WallSegment(length=300, height=bad, label="A")], system="BAUTEKK")


def test_too_large_dimension_rejected():
    with pytest.raises(ValueError):
        calculator.calculate([WallSegment(length=1_000_000, height=150, label="A")], system="BAUTEKK")


def test_too_small_dimension_rejected():
    with pytest.raises(ValueError):
        calculator.calculate([WallSegment(length=0.4, height=150, label="A")], system="BAUTEKK")


def test_empty_segments_rejected():
    with pytest.raises(ValueError):
        calculator.calculate([], system="BAUTEKK")


def test_polygon_dedup_zero_length_side():
    # duplicate consecutive point must not crash; treated as one vertex
    points = [(0, 0), (0, 0), (400, 0), (400, 300), (0, 300)]
    segs, _ = calculator.polygon_to_segments(points, height=150)
    assert len(segs) == 4


def test_polygon_two_points_closed_rejected():
    with pytest.raises(ValueError):
        calculator.polygon_to_segments([(0, 0), (500, 0)], height=150, closed=True)


def test_polygon_degenerate_rejected():
    with pytest.raises(ValueError):
        calculator.polygon_to_segments([(0, 0), (400, 0), (800, 0)], height=150, closed=True)


def test_reconciliation_separates_overshoot_and_timber():
    # tall wall (overshoot) + odd length (timber), faces=1 for clarity
    segs = [WallSegment(length=193, height=280, corner_at_end="outer", label="A")]
    r = calculator.calculate(segs, system="BAUTEKK", faces=1)
    rec = r["reconciliation"]
    assert rec["vertical_overshoot_m2"] >= 0
    assert rec["horizontal_timber_m2"] >= 0
    # summary area equals geometry area (single rounding)
    assert r["summary"]["formwork_area_m2"] == rec["area_from_geometry_m2"]


def test_pressure_warning():
    segments = [WallSegment(length=300, height=150, corner_at_end="outer")]
    result = calculator.calculate(segments, system="BAUTEKK", pressure_kn_m2=60)
    assert any("ciśnien" in w.lower() for w in result["warnings"])


def test_unverified_catalog_warns():
    segments = [WallSegment(length=300, height=270, corner_at_end="outer")]
    result = calculator.calculate(segments, system="BAUFRAME")
    assert result["catalog_verified"] is False
    assert any("weryf" in w.lower() for w in result["warnings"])


# --- concrete pressure (DIN 18218) ---------------------------------------
from ged_invest_mcp.formwork import pressure


def test_pressure_din_reference_f3():
    # DIN 18218 F3, tE=5 (K1=1), gamma=25 (K2=1): 14*v + 18
    r = pressure.concrete_pressure(2.0, 3.0, "F3")
    assert r["characteristic_pressure_kn_m2"] == 46.0
    assert r["coefficients"]["K1"] == 1.0


def test_pressure_hydrostatic_cap():
    # Low rate on a short wall -> hydrostatic governs
    r = pressure.concrete_pressure(2.0, 0.5, "F3")
    assert r["hydrostatic_pressure_kn_m2"] == 12.5
    assert r["design_pressure_kn_m2"] == 12.5
    assert r["governing"] == "hydrostatyczne"


def test_pressure_limit_and_max_rate():
    r = pressure.concrete_pressure(3.0, 4.0, "F3", allowed_pressure_kn_m2=40)
    assert r["within_limit"] is False
    # (40 - 18) / 14 = 1.57
    assert r["max_pouring_rate_for_limit_m_per_h"] == 1.57
    assert any("przekracza" in w.lower() for w in r["warnings"])


def test_pressure_flowable_min_and_k1():
    # SCC: K1 = tE/5; at tE=10 -> K1=2
    r = pressure.concrete_pressure(1.0, 5.0, "SCC", setting_time_h=10.0)
    assert r["coefficients"]["K1"] == 2.0


def test_pressure_invalid_class():
    with pytest.raises(ValueError):
        pressure.concrete_pressure(2.0, 3.0, "F9")


def test_pressure_svb_alias():
    assert pressure.normalize_class("svb") == "SCC"


# --- closed-cell (wall thickness aware) ----------------------------------
def test_cell_face_lengths_thickness():
    poly = [(0, 0), (1000, 0), (1000, 800), (0, 800)]
    walls, winding = calculator.polygon_to_cell(poly, height=150, thickness=25, system="BAUTEKK")
    a = walls[0]  # long wall
    assert round(a.outer_length, 1) == 1025.0   # axis + thickness
    assert round(a.inner_length, 1) == 945.0    # axis - thickness - 2*15 inner legs


def test_cell_corners_outer_and_inner():
    poly = [(0, 0), (1000, 0), (1000, 800), (0, 800)]
    walls, winding = calculator.polygon_to_cell(poly, height=150, thickness=25)
    res = calculator.calculate_cell(walls, system="BAUTEKK", wall_thickness_cm=25)
    assert res["summary"]["outer_corners"] == 4
    assert res["summary"]["inner_corners"] == 4
    kinds = {c["kind"]: c["quantity"] for c in res["bom"]["corners"]}
    assert kinds == {"outer": 4, "inner": 4}


def test_cell_uses_only_catalog_widths_no_sliver():
    poly = [(0, 0), (1000, 0), (1000, 800), (0, 800)]
    walls, _ = calculator.polygon_to_cell(poly, height=150, thickness=25)
    res = calculator.calculate_cell(walls, system="BAUTEKK", wall_thickness_cm=25)
    widths = {int(p["size"].split("x")[0]) for p in res["bom"]["panels"]}
    assert widths <= set(calculator.get_system("BAUTEKK").panel_widths)


def test_cell_rejects_bad_thickness():
    poly = [(0, 0), (1000, 0), (1000, 800), (0, 800)]
    with pytest.raises(ValueError):
        calculator.polygon_to_cell(poly, height=150, thickness=0)


def test_cell_stock_restriction_excludes_missing_widths():
    poly = [(0, 0), (1000, 0), (1000, 800), (0, 800)]
    walls, _ = calculator.polygon_to_cell(poly, height=150, thickness=25)
    stock = (90, 75, 70, 60, 45, 25)  # invoice: no 50, no 30
    res = calculator.calculate_cell(walls, system="BAUTEKK", wall_thickness_cm=25,
                                    available_widths=stock)
    used = {int(p["size"].split("x")[0]) for p in res["bom"]["panels"]}
    assert used <= set(stock)
    assert 50 not in used and 30 not in used


def test_available_widths_unknown_rejected():
    poly = [(0, 0), (1000, 0), (1000, 800), (0, 800)]
    walls, _ = calculator.polygon_to_cell(poly, height=150, thickness=25)
    with pytest.raises(ValueError):
        calculator.calculate_cell(walls, system="BAUTEKK", wall_thickness_cm=25,
                                  available_widths=(999,))


# --- closed-cell aligned-joint corner template ---------------------------
def _cell_10x8(**kw):
    poly = [(0, 0), (1000, 0), (1000, 800), (0, 800)]
    walls, winding = calculator.polygon_to_cell(poly, height=150, thickness=25)
    return calculator.calculate_cell(
        walls, system="BAUTEKK", wall_thickness_cm=25,
        available_widths=(90, 75, 70, 60, 50, 45, 25),
        polygon_points=poly, polygon_winding=winding, **kw,
    )


def test_cell_default_template_leg15_filler40():
    # default inner leg = catalog 15x15 (on the invoice) -> filler H40, a dedicated
    # element (25+15=40). Joints still align by construction.
    res = _cell_10x8()
    ct = res["corner_template"]
    assert ct["inner_corner_cm"] == "15x15"
    assert ct["outer_filler_width_cm"] == 40
    assert ct["filler_is_standard_panel"] is False
    assert ct["joints_align"] is True


def test_cell_explicit_leg20_filler45_is_stock():
    # leg 20 -> filler 45, which is a real stock panel on the invoice
    res = _cell_10x8(inner_corner_leg_cm=20)
    ct = res["corner_template"]
    assert ct["inner_corner_cm"] == "20x20"
    assert ct["outer_filler_width_cm"] == 45
    assert ct["filler_is_standard_panel"] is True
    assert ct["joints_align"] is True


def test_cell_requires_thickness():
    poly = [(0, 0), (1000, 0), (1000, 800), (0, 800)]
    walls, _ = calculator.polygon_to_cell(poly, height=150, thickness=25)
    with pytest.raises(ValueError):
        calculator.calculate_cell(walls, system="BAUTEKK", wall_thickness_cm=None)


def test_cell_flat_run_identical_on_both_faces():
    res = _cell_10x8(inner_corner_leg_cm=20)
    for wall in res["layout_draft"]:
        outer_flat = [p["width_cm"] for p in wall["outer_face"]["pieces"] if p["type"] == "panel"]
        inner_flat = [p["width_cm"] for p in wall["inner_face"]["pieces"] if p["type"] == "panel"]
        assert outer_flat == inner_flat        # shared run => joints coincide
        assert wall["joints_aligned"] is True


def test_cell_corner_bom_counts():
    res = _cell_10x8(inner_corner_leg_cm=20)
    assert res["summary"]["outer_corners"] == 4
    assert res["summary"]["inner_corners"] == 4
    assert res["summary"]["corner_filler_panels"] == 8   # 2 per corner
    inner = [c for c in res["bom"]["corners"] if c["kind"] == "inner"][0]
    assert "20x20" in inner["description"]
    assert inner["note"] and "zamówienia" in inner["note"]
    # fillers are tracked as a separate BOM line, not mixed into wall panels
    fillers = res["bom"]["corner_fillers"]
    assert sum(f["quantity"] for f in fillers) == 8
    assert all(f["width_cm"] == 45 for f in fillers)
    assert all(f["is_standard_panel"] for f in fillers)


def test_cell_default_filler_is_dedicated_element():
    # leg 15 -> filler H40, not a stock panel -> billed as dedicated element + warning
    res = _cell_10x8(inner_corner_leg_cm=15)
    assert res["corner_template"]["joints_align"] is True
    fillers = res["bom"]["corner_fillers"]
    assert all(f["is_standard_panel"] is False for f in fillers)
    assert all(f["width_cm"] == 40 for f in fillers)
    assert any("osobny" in w.lower() or "dedykowany" in w.lower() for w in res["warnings"])


# --- deterministic SVG rendering (moved into the MCP) --------------------
def test_render_layout_svg_is_wellformed():
    from ged_invest_mcp.formwork import drawing
    res = _cell_10x8(inner_corner_leg_cm=20)
    svg = drawing.render_layout_svg(res)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg
    assert "PLAN" in svg          # rectangle -> plan present
    assert "fugi zgrane" in svg
    assert 'fill="#a9d18e"' in svg  # panel colours on plan + elevations
    # plan width labels outside the thin wall bands
    plan_part = svg.split("Ściana")[0]
    assert plan_part.count('font-size="7"') >= 4


def test_render_layout_svg_reflects_widths_and_corners():
    from ged_invest_mcp.formwork import drawing
    res = _cell_10x8(inner_corner_leg_cm=20)
    svg = drawing.render_layout_svg(res)
    # the elevation labels the real flat widths used on the long wall
    for w in ("90", "75", "50", "45"):
        assert f'>{w}<' in svg
    # deduped: the two long walls collapse to one block marked x2
    assert "x2" in svg


def test_render_layout_svg_requires_layout_draft():
    from ged_invest_mcp.formwork import drawing
    with pytest.raises(ValueError):
        drawing.render_layout_svg({"summary": {}})


def test_render_svg_scales_with_scale_param():
    from ged_invest_mcp.formwork import drawing
    res = _cell_10x8(inner_corner_leg_cm=20)
    small = drawing.render_layout_svg(res, scale=0.2)
    big = drawing.render_layout_svg(res, scale=0.5)
    import re
    ws = float(re.search(r'width="(\d+)"', small).group(1))
    wb = float(re.search(r'width="(\d+)"', big).group(1))
    assert wb > ws


# --- hard stock limits ----------------------------------------------------
# invoice FVS-25/05/2401 + bought 20x20 corners + 10x 50x150 panels
_INVOICE_STOCK = {
    "90": 58, "75": 26, "70": 24, "60": 26, "50": 10, "45": 20, "25": 10,
    "outer_corner": 4, "inner_corner": 10,
}


def test_cover_width_55_uses_50_plus_timber_when_in_stock():
    cols, timber = calculator.cover_width(55, (90, 75, 70, 60, 50, 45, 25))
    assert cols == {50: 1} and timber == 5


def test_stock_check_detects_90_shortfall():
    res = _cell_10x8(inner_corner_leg_cm=20)
    chk = calculator.reconcile_stock(res, _INVOICE_STOCK)
    p90 = [i for i in chk["items"] if i["key"] == "90"][0]
    assert p90["needed"] == 64 and p90["available"] == 58
    assert p90["shortfall"] == 6
    assert chk["fits_in_stock"] is False


def test_stock_check_filler_shares_45_stock():
    # leg 20 -> filler 45 is a standard panel: 4 wall panels + 8 fillers = 12 of 45
    res = _cell_10x8(inner_corner_leg_cm=20)
    chk = calculator.reconcile_stock(res, _INVOICE_STOCK)
    p45 = [i for i in chk["items"] if i["key"] == "45"][0]
    assert p45["needed"] == 12
    assert p45["available"] == 20 and p45["shortfall"] == 0


def test_stock_check_corners_counted():
    res = _cell_10x8(inner_corner_leg_cm=20)
    chk = calculator.reconcile_stock(res, _INVOICE_STOCK)
    inner = [i for i in chk["items"] if i["key"] == "inner_corner"][0]
    outer = [i for i in chk["items"] if i["key"] == "outer_corner"][0]
    assert inner["needed"] == 4 and inner["available"] == 10
    assert outer["needed"] == 4 and outer["available"] == 4 and outer["shortfall"] == 0


def test_stock_check_suggests_substitution_for_shortfall():
    res = _cell_10x8(inner_corner_leg_cm=20)
    chk = calculator.reconcile_stock(res, _INVOICE_STOCK)
    sug = chk["substitution_suggestions"]
    assert len(sug) == 1
    s = sug[0]
    assert s["missing_length_cm"] == 540       # 6 x 90
    covered = sum(int(k.replace("cm", "")) * n for k, n in s["cover_with"].items())
    assert covered + s["uncovered_cm"] == 540


def test_stock_check_fits_when_enough():
    res = _cell_10x8(inner_corner_leg_cm=20)
    generous = dict(_INVOICE_STOCK, **{"90": 100})
    chk = calculator.reconcile_stock(res, generous)
    assert chk["fits_in_stock"] is True
    assert chk["substitution_suggestions"] == []


def _cell_825x1065(**kw):
    poly = [(0, 0), (825, 0), (825, 1065), (0, 1065)]
    walls, winding = calculator.polygon_to_cell(poly, height=150, thickness=25)
    return calculator.calculate_cell(
        walls, system="BAUTEKK", wall_thickness_cm=25, inner_corner_leg_cm=20,
        available_widths=(90, 75, 70, 60, 50, 45, 25),
        polygon_points=poly, polygon_winding=winding, **kw,
    )


def test_stock_replan_fits_invoice_for_825x1065():
    res = _cell_825x1065(stock=_INVOICE_STOCK)
    assert res["assumptions"]["stock_replanned"] is True
    p90 = next(p for p in res["bom"]["panels"] if p["size"].startswith("90"))
    assert p90["quantity"] <= 58
    chk = calculator.reconcile_stock(res, _INVOICE_STOCK)
    assert chk["fits_in_stock"] is True


def test_stock_replan_fits_invoice_for_10x8():
    poly = [(0, 0), (1000, 0), (1000, 800), (0, 800)]
    walls, winding = calculator.polygon_to_cell(poly, height=150, thickness=25)
    res = calculator.calculate_cell(
        walls, system="BAUTEKK", wall_thickness_cm=25, inner_corner_leg_cm=20,
        available_widths=(90, 75, 70, 60, 50, 45, 25),
        polygon_points=poly, polygon_winding=winding, stock=_INVOICE_STOCK,
    )
    assert res["assumptions"]["stock_replanned"] is True
    p90 = next(p for p in res["bom"]["panels"] if p["size"].startswith("90"))
    assert p90["quantity"] <= 58
    assert calculator.reconcile_stock(res, _INVOICE_STOCK)["fits_in_stock"] is True
