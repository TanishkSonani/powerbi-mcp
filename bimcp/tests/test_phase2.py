"""
Phase 2 integration tests — TMDL file manipulation.

Each test runs the MCP server in a subprocess and talks to it via the official
MCP client (same pattern as test_phase1.py).  A fresh copy of the fixture is
created in a temp directory for each test so mutations never corrupt the fixture.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp import ClientSession

SERVER_PY = str(Path(__file__).parent.parent / "server.py")
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "TestModel.SemanticModel"

EXPECTED_TOOL_COUNT = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def fixture_path(tmp_path) -> Path:
    """Copy the fixture to a temp dir so each test gets a clean slate."""
    dest = tmp_path / "TestModel.SemanticModel"
    shutil.copytree(FIXTURE_ROOT, dest)
    return dest


@pytest.fixture
def server_params():
    return StdioServerParameters(command=sys.executable, args=[SERVER_PY], env=None)


async def _call(session: ClientSession, tool: str, **kwargs) -> dict:
    result = await session.call_tool(tool, arguments=kwargs)
    raw = result.content[0].text
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Test 1 — tools/list returns 20 tools
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tools_list_returns_20(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            assert len(result.tools) == EXPECTED_TOOL_COUNT, (
                f"Expected {EXPECTED_TOOL_COUNT} tools, got {len(result.tools)}: "
                f"{[t.name for t in result.tools]}"
            )


# ---------------------------------------------------------------------------
# Test 2 — open_pbip_folder returns correct model summary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_open_pbip_folder(server_params, fixture_path):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await _call(session, "open_pbip_folder", path=str(fixture_path))
            assert result["status"] == "connected"
            assert result["model"] == "TestModel"
            assert result["tables"] == 2
            assert result["total_measures"] == 1
            assert result["relationships"] == 1


# ---------------------------------------------------------------------------
# Test 3 — list_tables returns 2 tables: Sales and Product
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_tables(server_params, fixture_path):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await _call(session, "open_pbip_folder", path=str(fixture_path))
            result = await _call(session, "list_tables")
            names = {t["name"] for t in result["tables"]}
            assert result["count"] == 2
            assert "Sales" in names
            assert "Product" in names


# ---------------------------------------------------------------------------
# Test 4 — get_table returns columns and measures for Sales
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_table_with_measures(server_params, fixture_path):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await _call(session, "open_pbip_folder", path=str(fixture_path))
            result = await _call(session, "get_table", table_name="Sales")
            assert result["name"] == "Sales"
            col_names = {c["name"] for c in result["columns"]}
            assert "ProductKey" in col_names
            assert "Amount" in col_names
            meas_names = {m["name"] for m in result["measures"]}
            assert "Total Amount" in meas_names


# ---------------------------------------------------------------------------
# Test 5 — create_measure then get_measure returns it
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_and_get_measure(server_params, fixture_path):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await _call(session, "open_pbip_folder", path=str(fixture_path))
            created = await _call(session, "create_measure",
                                  table_name="Sales", name="Avg Amount",
                                  expression="AVERAGE(Sales[Amount])",
                                  format_string="$ #,0.00")
            assert created["status"] == "created"
            fetched = await _call(session, "get_measure",
                                  table_name="Sales", measure_name="Avg Amount")
            assert fetched["name"] == "Avg Amount"
            assert "AVERAGE" in fetched["expression"]
            assert fetched["format_string"] == "$ #,0.00"


# ---------------------------------------------------------------------------
# Test 6 — update_measure changes the DAX expression
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_measure(server_params, fixture_path):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await _call(session, "open_pbip_folder", path=str(fixture_path))
            await _call(session, "update_measure",
                        table_name="Sales", measure_name="Total Amount",
                        new_expression="SUMX(Sales, Sales[Amount] * 1.1)")
            fetched = await _call(session, "get_measure",
                                  table_name="Sales", measure_name="Total Amount")
            assert "SUMX" in fetched["expression"]


# ---------------------------------------------------------------------------
# Test 7 — delete_measure removes it
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_measure(server_params, fixture_path):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await _call(session, "open_pbip_folder", path=str(fixture_path))
            deleted = await _call(session, "delete_measure",
                                  table_name="Sales", measure_name="Total Amount")
            assert deleted["status"] == "deleted"
            listing = await _call(session, "list_measures")
            names = {m["name"] for m in listing["measures"]}
            assert "Total Amount" not in names


# ---------------------------------------------------------------------------
# Test 8 — save_model writes the new measure to disk
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_model_writes_disk(server_params, fixture_path):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await _call(session, "open_pbip_folder", path=str(fixture_path))
            await _call(session, "create_measure",
                        table_name="Sales", name="Disk Test Measure",
                        expression="1 + 1")
            saved = await _call(session, "save_model")
            assert saved["status"] == "saved"

    # After the server shuts down, inspect the file on disk directly
    sales_tmdl = fixture_path / "definition" / "tables" / "Sales.tmdl"
    content = sales_tmdl.read_text(encoding="utf-8")
    assert "Disk Test Measure" in content, (
        f"Expected 'Disk Test Measure' in {sales_tmdl}:\n{content}"
    )


# ---------------------------------------------------------------------------
# Test 9 — create and delete relationship
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_and_delete_relationship(server_params, fixture_path):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await _call(session, "open_pbip_folder", path=str(fixture_path))

            before = await _call(session, "list_relationships")
            original_count = before["count"]

            created = await _call(session, "create_relationship",
                                  from_table="Sales", from_column="Amount",
                                  to_table="Product", to_column="ProductKey")
            assert created["status"] == "created"

            after_create = await _call(session, "list_relationships")
            assert after_create["count"] == original_count + 1

            deleted = await _call(session, "delete_relationship",
                                  from_table="Sales", from_column="Amount",
                                  to_table="Product", to_column="ProductKey")
            assert deleted["status"] == "deleted"

            after_delete = await _call(session, "list_relationships")
            assert after_delete["count"] == original_count


# ---------------------------------------------------------------------------
# Test 10 — calling list_tables without open_pbip_folder returns error, not crash
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_context_error(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("list_tables", arguments={})
            text = result.content[0].text
            # Must return an error message, not raise / crash the server
            assert "Error" in text or "error" in text or "open" in text.lower()
