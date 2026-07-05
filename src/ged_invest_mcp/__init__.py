"""Ged Invest MCP.

An MCP server exposing a growing set of construction tools for Ged Invest.

Design principle: the LLM (ChatGPT / Claude) acts as the "eyes" - it reads
drawings, photos or PDFs and turns them into structured data. The MCP tools act
as deterministic calculators, so the same input always yields the same result.

The first tool domain is `formwork` (wall formwork quantity takeoff). Additional
tool domains can be added as separate submodules that register their tools on the
shared MCP instance (see `server.py`).
"""

__version__ = "0.1.0"
