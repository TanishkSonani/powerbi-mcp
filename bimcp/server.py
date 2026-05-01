"""
powerbi-local-mcp — Phase 3: Live Desktop Integration
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

# Phase 3 tools
from src.tools.desktop_tools import (
    connect_desktop, disconnect, discover_desktop, get_desktop_model_info,
)
from src.tools.dax_tools import execute_dax, push_measure_live, validate_measure

# Phase 4 tools
from src.tools.role_tools import (
    add_rls_filter, create_role, delete_rls_filter, list_roles, update_role,
)
from src.tools.culture_tools import (
    add_translation, bulk_add_translations, list_cultures,
)
from src.tools.udf_tools import (
    create_udf, delete_udf, list_udfs, update_udf,
)
from src.tools.calendar_tools import (
    create_calendar, delete_calendar, list_calendars, update_calendar_column_group,
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
    # Desktop (Phase 3) — 4 tools
    "discover_desktop":       discover_desktop,
    "connect_desktop":        connect_desktop,
    "disconnect":             disconnect,
    "get_desktop_model_info": get_desktop_model_info,
    # DAX / live mutation (Phase 3) — 3 tools
    "execute_dax":            execute_dax,
    "validate_measure":       validate_measure,
    "push_measure_live":      push_measure_live,
    # RLS roles (Phase 4) — 5 tools
    "list_roles":             list_roles,
    "create_role":            create_role,
    "update_role":            update_role,
    "add_rls_filter":         add_rls_filter,
    "delete_rls_filter":      delete_rls_filter,
    # Cultures/Translations (Phase 4) — 3 tools
    "list_cultures":          list_cultures,
    "add_translation":        add_translation,
    "bulk_add_translations":  bulk_add_translations,
    # UDFs (Phase 4) — 4 tools
    "list_udfs":              list_udfs,
    "create_udf":             create_udf,
    "update_udf":             update_udf,
    "delete_udf":             delete_udf,
    # Calendar column groups (Phase 4) — 4 tools
    "list_calendars":         list_calendars,
    "create_calendar":        create_calendar,
    "update_calendar_column_group": update_calendar_column_group,
    "delete_calendar":        delete_calendar,
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
    # ---- Desktop (Phase 3) ----
    Tool(
        name="discover_desktop",
        description="Scan for running Power BI Desktop instances and return their model names and ports.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="connect_desktop",
        description="Connect to a running Power BI Desktop model via local XMLA. Specify model_name or port to disambiguate when multiple instances are open.",
        inputSchema={"type": "object", "properties": {"model_name": {**_STR, "description": "Model name (case-insensitive)"}, "port": {"type": "integer", "description": "XMLA port number"}}},
    ),
    Tool(
        name="disconnect",
        description="Release the current context (file or live Desktop). Safe to call when no context is active.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_desktop_model_info",
        description="Return live metadata from the connected Desktop model (tables, measure count, port). Requires connect_desktop first.",
        inputSchema={"type": "object", "properties": {}},
    ),
    # ---- DAX / live mutation (Phase 3) ----
    Tool(
        name="execute_dax",
        description="Execute a DAX query against the live Desktop model and return results as a markdown table. Requires connect_desktop first.",
        inputSchema={"type": "object", "properties": {"dax_query": {**_STR, "description": "DAX query to execute (e.g. EVALUATE ...)"}, "max_rows": {"type": "integer", "description": "Maximum rows to return (default 500)"}}, "required": ["dax_query"]},
    ),
    Tool(
        name="validate_measure",
        description="Validate a DAX expression against the live model. Returns {valid, result, error}. Requires connect_desktop first.",
        inputSchema={"type": "object", "properties": {"table_name": {**_STR, "description": "Table context for the expression"}, "expression": {**_STR, "description": "DAX expression to validate"}}, "required": ["table_name", "expression"]},
    ),
    Tool(
        name="push_measure_live",
        description="Create or replace a measure in the live Desktop model without writing to disk. Changes are immediately visible in Power BI. Requires connect_desktop first.",
        inputSchema={"type": "object", "properties": {"table_name": {**_STR}, "name": {**_STR, "description": "Measure name"}, "expression": {**_STR, "description": "DAX expression"}, "format_string": {**_STR, "description": "Optional format string"}}, "required": ["table_name", "name", "expression"]},
    ),
    # ---- RLS Roles (Phase 4) ----
    Tool(
        name="list_roles",
        description="List all RLS roles in the open model with their permission level and filter count.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="create_role",
        description="Create a new RLS role. modelPermission values: Read, ReadRefresh, ReadExploreData, Admin.",
        inputSchema={"type": "object", "properties": {"name": {**_STR, "description": "Role name"}, "model_permission": {**_STR, "description": "Read (default), ReadRefresh, ReadExploreData, or Admin"}}, "required": ["name"]},
    ),
    Tool(
        name="update_role",
        description="Rename a role or change its model permission level.",
        inputSchema={"type": "object", "properties": {"role_name": {**_STR, "description": "Current role name"}, "new_name": {**_STR, "description": "New name (optional)"}, "model_permission": {**_STR, "description": "New permission level (optional)"}}, "required": ["role_name"]},
    ),
    Tool(
        name="add_rls_filter",
        description="Add or replace a row-level security DAX filter on a table for a role. Omit filter_expression to grant full table access within the role.",
        inputSchema={"type": "object", "properties": {"role_name": {**_STR}, "table_name": {**_STR}, "filter_expression": {**_STR, "description": "DAX filter expression (optional — omit for full access)"}}, "required": ["role_name", "table_name"]},
    ),
    Tool(
        name="delete_rls_filter",
        description="Remove the row-level security filter for a specific table from a role.",
        inputSchema={"type": "object", "properties": {"role_name": {**_STR}, "table_name": {**_STR}}, "required": ["role_name", "table_name"]},
    ),
    # ---- Cultures/Translations (Phase 4) ----
    Tool(
        name="list_cultures",
        description="List all cultures (languages) in the open model with their translation counts.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="add_translation",
        description="Add or update a translation for a table, measure, or column. Use property_name: Caption, Description, or DisplayFolder.",
        inputSchema={"type": "object", "properties": {
            "culture_name": {**_STR, "description": "Culture code (e.g., 'fr-FR', 'de-DE')"},
            "object_type": {**_STR, "description": "'Table', 'Measure', or 'Column'"},
            "object_name": {**_STR, "description": "Name of the object to translate"},
            "property_name": {**_STR, "description": "'Caption', 'Description', or 'DisplayFolder'"},
            "translated_value": {**_STR, "description": "The translated text"},
            "table_name": {**_STR, "description": "Parent table (required for Measure/Column)"},
        }, "required": ["culture_name", "object_type", "object_name", "property_name", "translated_value"]},
    ),
    Tool(
        name="bulk_add_translations",
        description="Add multiple translations at once for a culture. Each translation needs object_type, object_name, property_name, translated_value, and optionally table_name.",
        inputSchema={"type": "object", "properties": {
            "culture_name": {**_STR, "description": "Culture code (e.g., 'fr-FR')"},
            "translations": {"type": "array", "description": "Array of translation objects", "items": {"type": "object"}},
        }, "required": ["culture_name", "translations"]},
    ),
    # ---- UDFs (Phase 4) ----
    Tool(
        name="list_udfs",
        description="List all user-defined functions (UDFs) in the open model.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="create_udf",
        description="Create a new user-defined function. Return types: variant, string, int64, double, datetime, boolean.",
        inputSchema={"type": "object", "properties": {
            "name": {**_STR, "description": "Function name"},
            "expression": {**_STR, "description": "DAX expression body"},
            "return_type": {**_STR, "description": "Return type (default: variant)"},
            "description": {**_STR, "description": "Optional description"},
            "parameters": {"type": "array", "description": "Optional parameter list [{name, type, description}]", "items": {"type": "object"}},
        }, "required": ["name", "expression"]},
    ),
    Tool(
        name="update_udf",
        description="Update an existing user-defined function.",
        inputSchema={"type": "object", "properties": {
            "udf_name": {**_STR, "description": "Current UDF name"},
            "new_name": {**_STR, "description": "New name (optional)"},
            "new_expression": {**_STR, "description": "New DAX expression (optional)"},
            "return_type": {**_STR, "description": "New return type (optional)"},
            "description": {**_STR, "description": "New description (optional)"},
            "parameters": {"type": "array", "description": "New parameters (optional, replaces existing)", "items": {"type": "object"}},
        }, "required": ["udf_name"]},
    ),
    Tool(
        name="delete_udf",
        description="Delete a user-defined function.",
        inputSchema={"type": "object", "properties": {"udf_name": {**_STR, "description": "UDF name to delete"}}, "required": ["udf_name"]},
    ),
    # ---- Calendar Column Groups (Phase 4) ----
    Tool(
        name="list_calendars",
        description="List all calendar column groups in the open model for date hierarchies.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="create_calendar",
        description="Create a calendar column group for date hierarchies. Valid time_unit: Year, Quarter, Month, Day, Week, DayOfWeek, DayOfYear, WeekOfYear, MonthOfYear, Hour, Minute, Second.",
        inputSchema={"type": "object", "properties": {
            "table_name": {**_STR, "description": "Table containing the date column"},
            "column_name": {**_STR, "description": "Date column name"},
            "time_unit": {**_STR, "description": "Time unit for the group"},
            "name": {**_STR, "description": "Optional custom name (defaults to column_timeunit)"},
            "is_default": {"type": "boolean", "description": "Whether this is the default group for the column"},
        }, "required": ["table_name", "column_name", "time_unit"]},
    ),
    Tool(
        name="update_calendar_column_group",
        description="Update an existing calendar column group.",
        inputSchema={"type": "object", "properties": {
            "group_name": {**_STR, "description": "Current group name"},
            "new_name": {**_STR, "description": "New name (optional)"},
            "time_unit": {**_STR, "description": "New time unit (optional)"},
            "is_default": {"type": "boolean", "description": "New default status (optional)"},
        }, "required": ["group_name"]},
    ),
    Tool(
        name="delete_calendar",
        description="Delete a calendar column group.",
        inputSchema={"type": "object", "properties": {"group_name": {**_STR, "description": "Group name to delete"}}, "required": ["group_name"]},
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
    logger.info("powerbi-local-mcp starting (Phase 4 — Complete, 43 tools)")
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
