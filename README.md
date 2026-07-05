# Ged Invest MCP

An **MCP (Model Context Protocol)** server hosting a growing set of construction
tools for Ged Invest. The first tool domain is **wall formwork quantity takeoff**;
more tool domains can be added later as separate submodules.

## Idea / architecture

Separation of concerns:

```
Photo / drawing / PDF
        │
        ▼
[ LLM (ChatGPT / Claude) ]     ← reads the geometry, builds structured data
        │   { segments:[{length, height, corner}], system:"BAUTEKK" }
        ▼
[ Ged Invest MCP (Python) ]    ← deterministic calculators (tools)
        │   panel selection + corners + hardware + aggregation
        ▼
[ Result: BOM ]   → panels, corners, ties, props, m², timber
```

- The **LLM** is the "eyes": it reads an image and turns it into numbers.
- The **MCP** is the "calculator": repeatable, no guessing. The same geometry
  always yields the same result - unlike LLM arithmetic.

## Tool domains

| Domain | Status | Tools |
|---|---|---|
| `formwork` | ready | `list_formwork_systems`, `formwork_catalog`, `count_formwork`, `concrete_pressure_check` |
| _(future)_ | planned | additional construction tools register the same way |

### Formwork systems

| System | Max pressure | Catalog |
|---|---|---|
| **BAUTEKK** | 40 kN/m² | verified against **DTR BauTekk 04/2022** + project PDF (R01) |
| **BAUFRAME** | 60 kN/m² | default values — to be verified |
| **BAUSCHAL** | 80 kN/m² | default values — to be verified |

BAUTEKK data taken from the manufacturer DTR: panel widths
(10/20/25/30/45/50/60/75/90 cm, 70 cm as VT), **three heights (90/120/150 cm)**,
real article numbers for every size, outer/inner corners per height, and the
connector rule (**5/4/3 connectors per joint** for 150/120/90 cm plates). Tie and
prop counts remain design-dependent estimates.

> BAUFRAME/BAUSCHAL catalogs contain sensible starting values. Confirm panel
> sizes and article numbers against the official BAUKRANE catalog before
> production use (`src/ged_invest_mcp/formwork/catalog.py`).

## Install

Requires Python ≥ 3.10.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Run

The server supports two transports from a single entry point.

### stdio (local — Claude Desktop / Cursor)

```bash
python -m ged_invest_mcp.server        # stdio by default
ged-invest-mcp
```

In this mode you host **no network server** — the client (Claude/Cursor) launches
the Python process itself and talks to it over stdin/stdout. When the client
closes, the process ends. No ports, no hosting.

### HTTP (remote — ChatGPT / connectors)

```bash
python -m ged_invest_mcp.server --http                 # http://0.0.0.0:8000/mcp
python -m ged_invest_mcp.server --http --port 9000
python -m ged_invest_mcp.server --transport sse        # legacy SSE transport
```

ChatGPT cannot launch a local process, so it needs an MCP exposed over HTTP at a
URL. Expose a local server via a tunnel (e.g. `ngrok http 8000`) or deploy it,
then add `https://.../mcp` as a custom connector.

## `count_formwork` — two ways to pass geometry

**A) Segment list** (you know wall lengths and corners):

```json
{
  "system": "BAUTEKK",
  "segments": [
    {"length": 465, "height": 150, "corner_at_end": "outer", "label": "A"},
    {"length": 465, "height": 150, "corner_at_end": "outer", "label": "B"}
  ]
}
```

**B) Polygon** (outline coordinates — corners detected automatically):

```json
{
  "system": "BAUTEKK",
  "polygon": [[0,0],[840,0],[840,700],[0,700]],
  "height": 150
}
```

## Client configuration

### Claude Desktop / Cursor (`mcp.json`)

```json
{
  "mcpServers": {
    "ged-invest": {
      "command": "/ABSOLUTE/PATH/bud-ged-mcp/.venv/bin/python",
      "args": ["-m", "ged_invest_mcp.server"]
    }
  }
}
```

## Method (formwork, in short)

1. Wall outline → segments between corners (outer / inner / none for collinear).
2. Segment length → panel width selection (dynamic programming, min. timber).
3. Height → **panels are stacked** to reach the wall height; the top course may
   stand above the pour line (reported as `top_overshoot_cm`, panels are not cut).
4. Panels = columns × courses × **faces** (default 2 = wall centerlines; pass
   `faces=1` if the input already lists both faces separately).
5. Corners: one corner element per course.
6. Hardware: full family from the catalog (ties, nuts, cones, plugs, spacer
   tubes, connectors, pins, wedges, corner tensioners, props with heads/feet).
   Coefficients are **approximate** and should be calibrated against the DTR.
7. Horizontal leftover that does not fit panels → **timber infill** [lm].

### Auditable output

`count_formwork` returns, besides the BOM:
- `input_echo` — the geometry actually used (polygon-derived side lengths,
  corner types and winding), so you can verify the LLM read the drawing correctly;
- `assumptions` — faces, corner rule, spacings, panel heights, policies;
- per-segment `panels_horizontal_per_face` / `courses_vertical_per_face` layout;
- `reconciliation` — geometry area vs panel area and waste %;
- `units`, `catalog_version`, `warnings`, `disclaimer`.

This is an **engineering estimator** — the result supports quoting/ordering and
must be approved by the site manager (per the project notes).

### `concrete_pressure_check` — fresh concrete pressure (DIN 18218)

Computes the maximum characteristic lateral fresh concrete pressure per
**DIN 18218:2010-01** from the pouring (rise) rate `v`, consistency class
(`F1`–`F6`, `SCC`) and setting time `tE`, caps it by the hydrostatic pressure
`γc·H`, and compares it to the formwork system's limit:

- `characteristic_pressure_kn_m2`, `hydrostatic_pressure_kn_m2`, `design_pressure_kn_m2`, `governing`;
- `within_limit` and `max_pouring_rate_for_limit_m_per_h` (the fastest safe rate);
- pass `system` (e.g. `BAUTEKK`) to auto-fill the limit (40 kN/m²) and the DTR
  process cap (2 m/h).

Simplified 15 °C reference model (no temperature/admixture corrections). The real
pressure and safe pouring rate remain the **site manager's** responsibility.

## Adding a new tool domain

1. Create `src/ged_invest_mcp/<domain>/` with a `register(mcp)` function.
2. Import and call it in `server.py`.

## Tests

```bash
python -m pytest -q
```
