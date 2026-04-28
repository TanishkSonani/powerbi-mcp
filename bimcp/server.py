"""
powerbi-local-mcp — Phase 2: TMDL File Manipulation
Universal local MCP server for Power BI modeling.
Connectable from any MCP-compatible client (Claude Desktop, custom agents, IDEs).
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

# Ensure src/ is importable when running as `python server.py`
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    GetPromptResult,
    Prompt,
    Resource,
    TextContent,
    Tool,
)
from pydantic import AnyUrl

from src.resources.provider import ResourceProvider
from src.prompts.connection_prompts import get_prompt as _get_prompt_impl
from src.prompts.connection_prompts import list_prompts as _list_prompts_impl

# Phase 2 tools
from src.tools.model_tools import get_model_info, open_pbip_folder, save_model
from src.tools.table_tools import (
    create_table, delete_table, get_table, list_tables, update_table,
)
from src.tools.measure_tools import (
    create_measure, delete_measure, get_measure, list_measures, update_measure,
)
from src.tools.column_tools import (
    create_column, delete_column, list_columns, update_column,
)
from src.tools.relationship_tools import (
    create_relationship, delete_relationship, list_relationships,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("powerbi-local-mcp")

# ---------------------------------------------------------------------------
# Server + providers
# ---------------------------------------------------------------------------

app = Server("powerbi-local-mcp")
_resources = ResourceProvider()

# ---------------------------------------------------------------------------
# Tool registry — maps tool name → callable (sync functions)
# ---------------------------------------------------------------------------

_TOOL_REGISTRY: dict[str, callable] = {
    # Model
    "open_pbip_folder":    open_pbip_folder,
    "get_model_info":      get_model_info,
    "save_model":          save_model,
    # Tables
    "list_tables":         list_tables,
    "get_table":           get_table,
    "create_table":        create_table,
    "update_table":        update_table,
    "delete_table":        delete_table,
    # Measures
    "list_measures":       list_measures,
    "get_measure":         get_measure,
    "create_measure":      create_measure,
    "update_measure":      update_measure,
    "delete_measure":      delete_measure,
    # Columns
    "list_columns":        list_columns,
    "create_column":       create_column,
    "update_column":       update_column,
    "delete_column":       delete_column,
    # Relationships
    "list_relationships":  list_relationships,
    "create_relationship": create_relationship,
    "delete_relationship": delete_relationship,
}

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

_STR = {"type": "string"}

_TOOLS: list[Tool] = [
    # ---- Model ----
    Tool(
        name="open_pbip_folder",
        description="Open a PBIP folder and load its TMDL model into memory. Accepts the PBIP root, .SemanticModel folder, or definition/ folder.",
        inputSchema={"type": "object", "properties": {"path": {**_STR, "description": "Path to the PBIP root, .SemanticModel, or definition/ folder"}}, "required": ["path"]},
    ),
    Tool(
        name="get_model_info",
        description="Return metadata about the currently open model (name, compatibility level, table/measure counts, dirty flag).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="save_model",
        description="Flush all in-memory changes to disk as TMDL files. Returns the list of written file paths.",
        inputSchema={"type": "object", "properties": {}},
    ),
    # ---- Tables ----
    Tool(
        name="list_tables",
        description="List all tables in the open model with column and measure counts.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_table",
        description="Return full detail for one table — all columns and measures.",
        inputSchema={"type": "object", "properties": {"table_name": {**_STR, "description": "Exact table name"}}, "required": ["table_name"]},
    ),
    Tool(
        name="create_table",
        description="Add a new (empty) table to the model.",
        inputSchema={"type": "object", "properties": {"name": {**_STR, "description": "Table name"}, "description": {**_STR, "description": "Optional description"}}, "required": ["name"]},
    ),
    Tool(
        name="update_table",
        description="Rename a table or change its description.",
        inputSchema={"type": "object", "properties": {"table_name": {**_STR, "description": "Current table name"}, "new_name": {**_STR, "description": "New name (optional)"}, "description": {**_STR, "description": "New description (optional)"}}, "required": ["table_name"]},
    ),
    Tool(
        name="delete_table",
        description="Remove a table from the model.",
        inputSchema={"type": "object", "properties": {"table_name": {**_STR, "description": "Table to delete"}}, "required": ["table_name"]},
    ),
    # ---- Measures ----
    Tool(
        name="list_measures",
        description="List all measures across all tables (with expression preview).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_measure",
        description="Return full detail (expression, format, description) for one measure.",
        inputSchema={"type": "object", "properties": {"table_name": {**_STR, "description": "Host table"}, "measure_name": {**_STR, "description": "Measure name"}}, "required": ["table_name", "measure_name"]},
    ),
    Tool(
        name="create_measure",
        description="Add a new DAX measure to a table.",
        inputSchema={"type": "object", "properties": {"table_name": {**_STR}, "name": {**_STR}, "expression": {**_STR, "description": "DAX expression"}, "format_string": {**_STR, "description": "Optional format string"}, "description": {**_STR}, "display_folder": {**_STR}}, "required": ["table_name", "name", "expression"]},
    ),
    Tool(
        name="update_measure",
        description="Modify a measure's DAX expression, name, format string, or description.",
        inputSchema={"type": "object", "properties": {"table_name": {**_STR}, "measure_name": {**_STR}, "new_expression": {**_STR}, "new_name": {**_STR}, "new_format_string": {**_STR}, "description": {**_STR}, "display_folder": {**_STR}}, "required": ["table_name", "measure_name"]},
    ),
    Tool(
        name="delete_measure",
        description="Remove a measure from a table.",
        inputSchema={"type": "object", "properties": {"table_name": {**_STR}, "measure_name": {**_STR}}, "required": ["table_name", "measure_name"]},
    ),
    # ---- Columns ----
    Tool(
        name="list_columns",
        description="List all columns in a specific table.",
        inputSchema={"type": "object", "properties": {"table_name": {**_STR}}, "required": ["table_name"]},
    ),
    Tool(
        name="create_column",
        description="Add a column to a table (data column or calculated column).",
        inputSchema={"type": "object", "properties": {"table_name": {**_STR}, "name": {**_STR}, "data_type": {**_STR, "description": "e.g. string, int64, decimal, dateTime, boolean"}, "source_column": {**_STR}, "expression": {**_STR, "description": "DAX for calculated columns"}, "format_string": {**_STR}, "description": {**_STR}}, "required": ["table_name", "name"]},
    ),
    Tool(
        name="update_column",
        description="Modify a column's name, data type, description, or format string.",
        inputSchema={"type": "object", "properties": {"table_name": {**_STR}, "column_name": {**_STR}, "new_name": {**_STR}, "description": {**_STR}, "format_string": {**_STR}, "data_type": {**_STR}}, "required": ["table_name", "column_name"]},
    ),
    Tool(
        name="delete_column",
        description="Remove a column from a table.",
        inputSchema={"type": "object", "properties": {"table_name": {**_STR}, "column_name": {**_STR}}, "required": ["table_name", "column_name"]},
    ),
    # ---- Relationships ----
    Tool(
        name="list_relationships",
        description="List all relationships in the model.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="create_relationship",
        description="Add a relationship between two tables.",
        inputSchema={"type": "object", "properties": {"from_table": {**_STR}, "from_column": {**_STR}, "to_table": {**_STR}, "to_column": {**_STR}, "from_cardinality": {**_STR, "description": "many (default) or one"}, "to_cardinality": {**_STR, "description": "one (default) or many"}}, "required": ["from_table", "from_column", "to_table", "to_column"]},
    ),
    Tool(
        name="delete_relationship",
        description="Remove a relationship by specifying both ends.",
        inputSchema={"type": "object", "properties": {"from_table": {**_STR}, "from_column": {**_STR}, "to_table": {**_STR}, "to_column": {**_STR}}, "required": ["from_table", "from_column", "to_table", "to_column"]},
    ),
]

# ---------------------------------------------------------------------------
# MCP — Resources
# ---------------------------------------------------------------------------

@app.list_resources()
async def list_resources() -> list[Resource]:
    return _resources.list_resources()


@app.read_resource()
async def read_resource(uri: AnyUrl) -> str:
    return _resources.read_resource(str(uri))


# ---------------------------------------------------------------------------
# MCP — Prompts
# ---------------------------------------------------------------------------

@app.list_prompts()
async def list_prompts() -> list[Prompt]:
    return _list_prompts_impl()


@app.get_prompt()
async def get_prompt(name: str, arguments: dict[str, str] | None = None) -> GetPromptResult:
    return _get_prompt_impl(name, arguments or {})


# ---------------------------------------------------------------------------
# MCP — Tools
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[Tool]:
    return _TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    args = arguments or {}
    fn = _TOOL_REGISTRY.get(name)
    if fn is None:
        return [TextContent(type="text", text=f"Unknown tool: {name!r}")]
    try:
        result = fn(**args)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    except (ValueError, RuntimeError) as exc:
        return [TextContent(type="text", text=f"Error: {exc}")]
    except Exception as exc:
        logger.exception("Unexpected error in tool %r", name)
        return [TextContent(type="text", text=f"Unexpected error: {exc}")]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _run():
    logger.info("powerbi-local-mcp starting (Phase 2 — TMDL File Manipulation)")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
