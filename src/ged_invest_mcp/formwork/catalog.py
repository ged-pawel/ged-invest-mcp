"""BAUKRANE formwork system catalogs.

IMPORTANT:
- BAUTEKK data comes from the source project PDF (Ged-Invest / drawing R01),
  including real article numbers.
- BAUFRAME and BAUSCHAL are reasonable defaults (flagged `verified=False`) and
  must be confirmed against the official BAUKRANE catalog / DTR before
  production use.
- Hardware coefficients (see `HardwareRule.coeff`) are TRANSPARENT APPROXIMATIONS
  meant to be calibrated against a real DTR. They ensure the whole hardware
  family is present in the BOM instead of being silently omitted.

All dimensions are in centimeters [cm]; pressure in kN/m2.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Bump when catalog data or coefficients change, so results are traceable.
CATALOG_VERSION = "2026-07-05"


@dataclass(frozen=True)
class Panel:
    """A single formwork panel size."""

    width: int
    height: int
    article_no: str | None = None

    @property
    def name(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def area_m2(self) -> float:
        return (self.width / 100.0) * (self.height / 100.0)


@dataclass(frozen=True)
class Corner:
    """A corner element (outer or inner).

    `articles_by_height` maps a course height [cm] to the corner article number,
    so a corner is billed with the right SKU per vertical course.
    """

    kind: str  # "outer" | "inner"
    height: int
    article_no: str | None = None
    description: str = ""
    articles_by_height: dict[int, str] = field(default_factory=dict)
    # width [cm] the corner element occupies on each adjoining wall face.
    # Outer "0" corners consume 0; the BAUTEKK inner corner is 15x15 -> 15.
    leg_cm: float = 0.0

    @property
    def name(self) -> str:
        return f"{self.kind}-corner {self.height}"

    def article_for(self, height: int) -> str | None:
        return self.articles_by_height.get(height, self.article_no)


# Recognized bases a hardware quantity can be derived from.
HARDWARE_BASES = (
    "tie",             # per tie rod
    "panel",           # per formwork panel
    "vertical_joint",  # per vertical panel-to-panel joint (per face)
    "corner",          # per corner element
    "prop",            # per alignment prop
    "area_m2",         # per square meter of formwork
)


@dataclass(frozen=True)
class HardwareRule:
    """A hardware item plus how its quantity is derived.

    quantity = ceil(base_value * coeff), where base_value is selected by `basis`.
    `coeff` values are approximations to be calibrated against the DTR.
    """

    key: str
    name: str
    basis: str
    coeff: float
    article_no: str | None = None
    approximate: bool = True


@dataclass(frozen=True)
class FormworkSystem:
    """Definition of a single formwork system."""

    name: str
    max_pressure_kn_m2: float
    # available panel widths [cm], largest to smallest
    panel_widths: tuple[int, ...]
    # available panel heights [cm] (possible vertical courses)
    panel_heights: tuple[int, ...]
    outer_corner: Corner
    inner_corner: Corner
    # tie grid spacing [cm] on the wall face: horizontal and vertical
    tie_spacing_h: int
    tie_spacing_v: int
    # maximum spacing of alignment props [cm]
    max_prop_spacing: int
    # connectors per panel-to-panel joint, keyed by course height [cm]
    # (DTR BauTekk: 5 for 150cm, 4 for 120cm, 3 for 90cm plates)
    connectors_per_joint: dict[int, int] = field(default_factory=dict)
    # hardware rules (ties, pins, wedges, cones, corner tensioners, props...)
    hardware: tuple[HardwareRule, ...] = ()
    # panel article numbers keyed by (width, height); missing => not available
    panel_article_numbers: dict[tuple[int, int], str] = field(default_factory=dict)
    verified: bool = False
    notes: str = ""

    def panels(self) -> list[Panel]:
        """Full list of panels (cartesian product of widths x heights)."""
        out: list[Panel] = []
        for h in self.panel_heights:
            for w in self.panel_widths:
                out.append(Panel(width=w, height=h, article_no=self.panel_article_numbers.get((w, h))))
        return out


# ---------------------------------------------------------------------------
# Hardware sets
# ---------------------------------------------------------------------------
# BAUTEKK hardware with real article numbers from PDF R01. Coefficients are
# approximate ratios (per panel / joint / tie / corner / prop) to be calibrated.
# Hardware article numbers verified against DTR BauTekk (04/2022).
# Connectors are handled separately (height-dependent count per joint), so they
# are NOT in this coefficient list.
_BAUTEKK_HARDWARE = (
    HardwareRule("tie_rod", "Tie rod DW-15 100cm", "tie", 1.0, "7270015100"),
    HardwareRule("tie_nut", "Nut D100 (plate nut)", "tie", 2.0, "7000000100"),
    HardwareRule("tie_cone", "Plastic cone PVC 22", "tie", 2.0, "7003000022"),
    HardwareRule("tie_plug", "Plastic plug D22", "tie", 2.0, "7000000022"),
    HardwareRule("pin", "BAUTEKK pin", "panel", 1.0, "7270B00003"),
    HardwareRule("tensioning_pin", "BAUTEKK tensioning pin D15 (timber inserts)", "panel", 0.5, "7270B00004"),
    HardwareRule("wedge", "BAUTEKK wedge", "panel", 2.0, "7271080130"),
    HardwareRule("corner_tensioner", "BAUTEKK corner tensioner (VT / bracing)", "corner", 0.6, "72700B0021"),
    HardwareRule("prop", "Alignment prop 0.9-1.3", "prop", 1.0, "7271090130"),
    HardwareRule("prop_head", "Prop head BAUTEKK", "prop", 1.0, "72720B0000"),
    HardwareRule("prop_foot", "Prop foot BAUTEKK", "prop", 1.0, "7271008300"),
)

# Generic hardware for unverified systems (no article numbers). Connectors are
# computed separately from `connectors_per_joint`, so they are not listed here.
_GENERIC_HARDWARE = (
    HardwareRule("tie_rod", "Tie rod DW-15", "tie", 1.0, None),
    HardwareRule("tie_nut", "Nut", "tie", 2.0, None),
    HardwareRule("tie_cone", "Plastic cone", "tie", 2.0, None),
    HardwareRule("pin", "Pin", "panel", 2.0, None),
    HardwareRule("wedge", "Wedge", "panel", 1.0, None),
    HardwareRule("corner_tensioner", "Corner tensioner", "corner", 2.0, None),
    HardwareRule("prop", "Alignment prop", "prop", 1.0, None),
    HardwareRule("prop_head", "Prop head", "prop", 1.0, None),
    HardwareRule("prop_foot", "Prop foot", "prop", 1.0, None),
)


# ---------------------------------------------------------------------------
# BAUTEKK - data from the PDF (Ged-Invest, drawing R01, Bautekk system)
# ---------------------------------------------------------------------------
# Panel article numbers for all three heights (DTR BauTekk 04/2022).
# 70cm exists only as a multi-hole VT panel (7221S70xxx).
_BAUTEKK_PANELS = {
    # height 150
    (90, 150): "7210S90150", (75, 150): "7210S75150", (70, 150): "7221S70150",
    (60, 150): "7210S60150", (50, 150): "7210S50150", (45, 150): "7210S45150",
    (30, 150): "7210S30150", (25, 150): "7210S25150", (20, 150): "7210S20150",
    (10, 150): "7210S10150",
    # height 120
    (90, 120): "7210S90120", (75, 120): "7210S75120", (70, 120): "7221S70120",
    (60, 120): "7210S60120", (50, 120): "7210S50120", (45, 120): "7210S45120",
    (30, 120): "7210S30120", (25, 120): "7210S25120", (20, 120): "7210S20120",
    (10, 120): "7210S10120",
    # height 90
    (90, 90): "72100S9090", (75, 90): "72100S7590", (70, 90): "72210S7090",
    (60, 90): "72100S6090", (50, 90): "72100S5090", (45, 90): "72100S4590",
    (30, 90): "72100S3090", (25, 90): "72100S2590", (20, 90): "72100S2090",
    (10, 90): "72100S1090",
}

BAUTEKK = FormworkSystem(
    name="BAUTEKK",
    max_pressure_kn_m2=40.0,
    panel_widths=(90, 75, 70, 60, 50, 45, 30, 25, 20, 10),
    panel_heights=(150, 120, 90),
    outer_corner=Corner(
        kind="outer", height=150, article_no="72130B0150",
        description="BAUTEKK outer corner 0x{h}",
        articles_by_height={150: "72130B0150", 120: "72130B0120", 90: "721300B090", 60: "721300B060"},
    ),
    inner_corner=Corner(
        kind="inner", height=150, article_no="7210B15150",
        description="BAUTEKK inner corner 15x15x{h}",
        articles_by_height={150: "7210B15150", 120: "7210B15120", 90: "72100B1590", 60: "72100B1560"},
        leg_cm=15.0,
    ),
    tie_spacing_h=90,
    tie_spacing_v=75,
    max_prop_spacing=250,
    connectors_per_joint={150: 5, 120: 4, 90: 3, 60: 2},
    hardware=_BAUTEKK_HARDWARE,
    panel_article_numbers=_BAUTEKK_PANELS,
    verified=True,
    notes="Panel sizes, heights (90/120/150), article numbers, corners and the "
          "connector rule (5/4/3 per joint) are from DTR BauTekk 04/2022. Tie and "
          "prop counts remain design-dependent estimates. Timber infill is on the client's side.",
)

# ---------------------------------------------------------------------------
# BAUFRAME / BAUFRAME ALU - defaults, TO BE VERIFIED
# ---------------------------------------------------------------------------
BAUFRAME = FormworkSystem(
    name="BAUFRAME",
    max_pressure_kn_m2=60.0,
    panel_widths=(240, 90, 75, 60, 45, 30),
    panel_heights=(270, 135),
    outer_corner=Corner(kind="outer", height=270, description="BAUFRAME outer corner (to be verified)"),
    inner_corner=Corner(kind="inner", height=270, description="BAUFRAME inner corner (to be verified)", leg_cm=15.0),
    tie_spacing_h=120,
    tie_spacing_v=135,
    max_prop_spacing=250,
    connectors_per_joint={270: 8, 135: 4},
    hardware=_GENERIC_HARDWARE,
    verified=False,
    notes="Default values. Verify panel sizes, article numbers and hardware coefficients against the BAUKRANE catalog.",
)

# ---------------------------------------------------------------------------
# BAUSCHAL - heavy system, defaults, TO BE VERIFIED
# ---------------------------------------------------------------------------
BAUSCHAL = FormworkSystem(
    name="BAUSCHAL",
    max_pressure_kn_m2=80.0,
    panel_widths=(240, 120, 90, 75, 60, 45, 30),
    panel_heights=(330, 165),
    outer_corner=Corner(kind="outer", height=330, description="BAUSCHAL outer corner (to be verified)"),
    inner_corner=Corner(kind="inner", height=330, description="BAUSCHAL inner corner (to be verified)", leg_cm=15.0),
    tie_spacing_h=120,
    tie_spacing_v=165,
    max_prop_spacing=250,
    connectors_per_joint={330: 10, 165: 5},
    hardware=_GENERIC_HARDWARE,
    verified=False,
    notes="Default values. Verify panel sizes, article numbers and hardware coefficients against the BAUKRANE catalog.",
)


SYSTEMS: dict[str, FormworkSystem] = {s.name: s for s in (BAUTEKK, BAUFRAME, BAUSCHAL)}


def get_system(name: str) -> FormworkSystem:
    """Return a system by name (case-insensitive)."""
    key = name.strip().upper()
    if key in ("BAUFRAME ALU", "BAUFRAME_ALU"):
        key = "BAUFRAME"
    if key not in SYSTEMS:
        available = ", ".join(SYSTEMS)
        raise ValueError(f"Unknown system '{name}'. Available: {available}")
    return SYSTEMS[key]
