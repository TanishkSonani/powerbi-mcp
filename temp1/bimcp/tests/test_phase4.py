"""
Phase 4 tests — RLS Roles (Prompt 1).

All tests call tool functions directly (no MCP subprocess) using the
clean_context fixture + ContextManager.get().open_file_context(...).
"""

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "TestModel.SemanticModel"

EXPECTED_TOOL_COUNT = 43


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fixture_path(tmp_path) -> Path:
    dest = tmp_path / "TestModel.SemanticModel"
    shutil.copytree(FIXTURE_ROOT, dest)
    return dest


@pytest.fixture(autouse=True)
def clean_context():
    from src.context.manager import ContextManager
    ContextManager._instance = None
    yield
    try:
        ContextManager.get().close_context()
    except Exception:
        pass
    ContextManager._instance = None


def _open(fixture_path: Path):
    from src.context.manager import ContextManager
    definition = fixture_path / "definition"
    ContextManager.get().open_file_context(definition)


# ---------------------------------------------------------------------------
# Test 1 — tool count (MCP subprocess)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tools_list_returns_32():
    import json as _json
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    server_py = str(Path(__file__).parent.parent / "server.py")
    params = StdioServerParameters(command=sys.executable, args=[server_py], env=None)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            assert len(result.tools) == EXPECTED_TOOL_COUNT, (
                f"Expected {EXPECTED_TOOL_COUNT} tools, got {len(result.tools)}: "
                f"{[t.name for t in result.tools]}"
            )


# ---------------------------------------------------------------------------
# Test 2 — create and list role
# ---------------------------------------------------------------------------

def test_create_and_list_role(fixture_path):
    from src.tools.role_tools import create_role, list_roles
    _open(fixture_path)

    r = create_role("Analysts", "Read")
    assert r["status"] == "created"
    assert r["role"] == "Analysts"

    roles = list_roles()
    assert roles["count"] == 1
    assert roles["roles"][0]["name"] == "Analysts"
    assert roles["roles"][0]["model_permission"] == "Read"
    assert roles["roles"][0]["filter_count"] == 0


# ---------------------------------------------------------------------------
# Test 3 — add RLS filter
# ---------------------------------------------------------------------------

def test_add_rls_filter(fixture_path):
    from src.tools.role_tools import add_rls_filter, create_role, list_roles
    _open(fixture_path)
    create_role("Sales Team", "Read")

    r = add_rls_filter("Sales Team", "Sales", "[Region] = 'North'")
    assert r["status"] == "set"
    assert r["table"] == "Sales"

    roles = list_roles()
    assert roles["roles"][0]["filter_count"] == 1


# ---------------------------------------------------------------------------
# Test 4 — delete RLS filter
# ---------------------------------------------------------------------------

def test_delete_rls_filter(fixture_path):
    from src.tools.role_tools import add_rls_filter, create_role, delete_rls_filter, list_roles
    _open(fixture_path)
    create_role("Testers")
    add_rls_filter("Testers", "Sales", "[Amount] > 0")

    r = delete_rls_filter("Testers", "Sales")
    assert r["status"] == "deleted"

    roles = list_roles()
    assert roles["roles"][0]["filter_count"] == 0


# ---------------------------------------------------------------------------
# Test 5 — delete filter on non-existent table raises
# ---------------------------------------------------------------------------

def test_delete_nonexistent_filter_raises(fixture_path):
    from src.tools.role_tools import create_role, delete_rls_filter
    _open(fixture_path)
    create_role("Empty Role")

    with pytest.raises(ValueError, match="No filter for table"):
        delete_rls_filter("Empty Role", "NoSuchTable")


# ---------------------------------------------------------------------------
# Test 6 — update role permission
# ---------------------------------------------------------------------------

def test_update_role_permission(fixture_path):
    from src.tools.role_tools import create_role, list_roles, update_role
    _open(fixture_path)
    create_role("Admins", "Read")

    r = update_role("Admins", model_permission="ReadRefresh")
    assert r["status"] == "updated"

    roles = list_roles()
    assert roles["roles"][0]["model_permission"] == "ReadRefresh"


# ---------------------------------------------------------------------------
# Test 7 — rename role
# ---------------------------------------------------------------------------

def test_rename_role(fixture_path):
    from src.tools.role_tools import create_role, list_roles, update_role
    _open(fixture_path)
    create_role("OldName")

    update_role("OldName", new_name="NewName")

    roles = list_roles()
    assert roles["roles"][0]["name"] == "NewName"
    assert roles["count"] == 1


# ---------------------------------------------------------------------------
# Test 8 — role roundtrip through save/reload
# ---------------------------------------------------------------------------

def test_role_roundtrip_save(fixture_path):
    from src.context.manager import ContextManager
    from src.tools.model_tools import save_model
    from src.tools.role_tools import add_rls_filter, create_role, list_roles
    _open(fixture_path)
    create_role("RegionFilter", "Read")
    add_rls_filter("RegionFilter", "Sales", "[Region] = 'South'")
    save_model()

    # Reload
    ContextManager._instance = None
    definition = fixture_path / "definition"
    ContextManager.get().open_file_context(definition)

    roles = list_roles()
    assert roles["count"] == 1
    role = roles["roles"][0]
    assert role["name"] == "RegionFilter"
    assert role["model_permission"] == "Read"
    assert role["filter_count"] == 1


# ---------------------------------------------------------------------------
# Test 9 — role without filter (full table access) roundtrips cleanly
# ---------------------------------------------------------------------------

def test_role_no_filter_roundtrip(fixture_path):
    from src.context.manager import ContextManager
    from src.tools.model_tools import save_model
    from src.tools.role_tools import add_rls_filter, create_role, list_roles
    _open(fixture_path)
    create_role("ReadAll", "ReadRefresh")
    # Add a tablePermission with NO filter expression (full table access)
    add_rls_filter("ReadAll", "Product", None)
    save_model()

    ContextManager._instance = None
    ContextManager.get().open_file_context(fixture_path / "definition")

    roles = list_roles()
    assert roles["roles"][0]["filter_count"] == 1  # one tablePermission, no expr


# ---------------------------------------------------------------------------
# Test 10 — file-context guard
# ---------------------------------------------------------------------------

def test_role_tools_require_file_context():
    from src.tools.role_tools import create_role, list_roles
    # No context open — should return error dict, not raise
    result = list_roles()
    assert "error" in result

    result2 = create_role("Ghost")
    assert "error" in result2


# ===========================================================================
# Phase 4 Prompt 2 — Cultures/Translations Tests
# ===========================================================================

# ---------------------------------------------------------------------------
# Test 11 — list cultures (empty initially)
# ---------------------------------------------------------------------------

def test_list_cultures_empty(fixture_path):
    from src.tools.culture_tools import list_cultures
    _open(fixture_path)

    result = list_cultures()
    assert result["count"] == 0
    assert result["cultures"] == []


# ---------------------------------------------------------------------------
# Test 12 — add translation
# ---------------------------------------------------------------------------

def test_add_translation(fixture_path):
    from src.tools.culture_tools import add_translation, list_cultures
    _open(fixture_path)

    r = add_translation(
        culture_name="fr-FR",
        object_type="Table",
        object_name="Sales",
        property_name="Caption",
        translated_value="Ventes",
    )
    assert r["status"] == "created"
    assert r["culture"] == "fr-FR"

    cultures = list_cultures()
    assert cultures["count"] == 1
    assert cultures["cultures"][0]["name"] == "fr-FR"
    assert cultures["cultures"][0]["translation_count"] == 1


# ---------------------------------------------------------------------------
# Test 13 — bulk add translations
# ---------------------------------------------------------------------------

def test_bulk_add_translations(fixture_path):
    from src.tools.culture_tools import bulk_add_translations, list_cultures
    _open(fixture_path)

    translations = [
        {"object_type": "Table", "object_name": "Sales", "property_name": "Caption", "translated_value": "Ventas"},
        {"object_type": "Table", "object_name": "Product", "property_name": "Caption", "translated_value": "Producto"},
        {"object_type": "Measure", "object_name": "Total Amount", "property_name": "Caption", "translated_value": "Cantidad Total", "table_name": "Sales"},
    ]

    r = bulk_add_translations("es-ES", translations)
    assert r["status"] == "completed"
    assert r["created"] == 3
    assert r["culture"] == "es-ES"

    cultures = list_cultures()
    assert cultures["count"] == 1
    assert cultures["cultures"][0]["translation_count"] == 3


# ---------------------------------------------------------------------------
# Test 14 — culture roundtrip save/reload
# ---------------------------------------------------------------------------

def test_culture_roundtrip(fixture_path):
    from src.context.manager import ContextManager
    from src.tools.culture_tools import add_translation, list_cultures
    from src.tools.model_tools import save_model
    _open(fixture_path)

    add_translation("de-DE", "Table", "Sales", "Caption", "Verkauf")
    save_model()

    # Reload
    ContextManager._instance = None
    ContextManager.get().open_file_context(fixture_path / "definition")

    cultures = list_cultures()
    assert cultures["count"] == 1
    assert cultures["cultures"][0]["name"] == "de-DE"


# ---------------------------------------------------------------------------
# Test 15 — culture tools require file context
# ---------------------------------------------------------------------------

def test_culture_tools_require_file_context():
    from src.tools.culture_tools import list_cultures
    result = list_cultures()
    assert "error" in result


# ===========================================================================
# Phase 4 Prompt 3 — UDF Tests
# ===========================================================================

# ---------------------------------------------------------------------------
# Test 16 — list UDFs (empty initially)
# ---------------------------------------------------------------------------

def test_list_udfs_empty(fixture_path):
    from src.tools.udf_tools import list_udfs
    _open(fixture_path)

    result = list_udfs()
    assert result["count"] == 0
    assert result["udfs"] == []


# ---------------------------------------------------------------------------
# Test 17 — create UDF
# ---------------------------------------------------------------------------

def test_create_udf(fixture_path):
    from src.tools.udf_tools import create_udf, list_udfs
    _open(fixture_path)

    r = create_udf(
        name="DoubleValue",
        expression="[Amount] * 2",
        return_type="double",
        description="Doubles the amount",
    )
    assert r["status"] == "created"
    assert r["udf"] == "DoubleValue"

    udfs = list_udfs()
    assert udfs["count"] == 1
    assert udfs["udfs"][0]["name"] == "DoubleValue"
    assert udfs["udfs"][0]["return_type"] == "double"


# ---------------------------------------------------------------------------
# Test 18 — create UDF with parameters
# ---------------------------------------------------------------------------

def test_create_udf_with_params(fixture_path):
    from src.tools.udf_tools import create_udf, list_udfs
    _open(fixture_path)

    params = [
        {"name": "Value", "type": "double", "description": "Input value"},
        {"name": "Factor", "type": "int64", "description": "Multiplier"},
    ]
    r = create_udf("Multiply", "Value * Factor", parameters=params)
    assert r["status"] == "created"
    assert r["parameter_count"] == 2

    udfs = list_udfs()
    assert udfs["udfs"][0]["parameter_count"] == 2


# ---------------------------------------------------------------------------
# Test 19 — update UDF
# ---------------------------------------------------------------------------

def test_update_udf(fixture_path):
    from src.tools.udf_tools import create_udf, list_udfs, update_udf
    _open(fixture_path)

    create_udf("OldFunc", "1 + 1", return_type="int64")
    update_udf("OldFunc", new_name="NewFunc", new_expression="2 + 2")

    udfs = list_udfs()
    assert udfs["count"] == 1
    assert udfs["udfs"][0]["name"] == "NewFunc"


# ---------------------------------------------------------------------------
# Test 20 — delete UDF
# ---------------------------------------------------------------------------

def test_delete_udf(fixture_path):
    from src.tools.udf_tools import create_udf, delete_udf, list_udfs
    _open(fixture_path)

    create_udf("ToDelete", "0")
    delete_udf("ToDelete")

    udfs = list_udfs()
    assert udfs["count"] == 0


# ---------------------------------------------------------------------------
# Test 21 — UDF roundtrip save/reload
# ---------------------------------------------------------------------------

def test_udf_roundtrip(fixture_path):
    from src.context.manager import ContextManager
    from src.tools.model_tools import save_model
    from src.tools.udf_tools import create_udf, list_udfs
    _open(fixture_path)

    create_udf("Persisted", "SUM(Sales[Amount])", return_type="double")
    save_model()

    ContextManager._instance = None
    ContextManager.get().open_file_context(fixture_path / "definition")

    udfs = list_udfs()
    assert udfs["count"] == 1
    assert udfs["udfs"][0]["name"] == "Persisted"


# ---------------------------------------------------------------------------
# Test 22 — UDF tools require file context
# ---------------------------------------------------------------------------

def test_udf_tools_require_file_context():
    from src.tools.udf_tools import list_udfs
    result = list_udfs()
    assert "error" in result


# ===========================================================================
# Phase 4 Prompt 4 — Calendar Column Group Tests
# ===========================================================================

# ---------------------------------------------------------------------------
# Test 23 — list calendars (empty initially)
# ---------------------------------------------------------------------------

def test_list_calendars_empty(fixture_path):
    from src.tools.calendar_tools import list_calendars
    _open(fixture_path)

    result = list_calendars()
    # May not be 0 if fixture has calendar annotations
    assert "calendar_groups" in result
    assert "count" in result


# ---------------------------------------------------------------------------
# Test 24 — create calendar group
# ---------------------------------------------------------------------------

def test_create_calendar(fixture_path):
    from src.tools.calendar_tools import create_calendar, list_calendars
    _open(fixture_path)

    # First need to add a date column to Sales table
    from src.context.manager import ContextManager
    ctx = ContextManager.get().get_active_context()
    from src.tmdl.models import Column
    ctx.model_state.tables["Sales"].columns.append(
        Column(name="OrderDate", data_type="dateTime")
    )

    r = create_calendar(
        table_name="Sales",
        column_name="OrderDate",
        time_unit="Year",
    )
    assert r["status"] == "created"
    assert r["calendar_group"] == "OrderDate_Year"

    calendars = list_calendars()
    assert calendars["count"] >= 1


# ---------------------------------------------------------------------------
# Test 25 — update calendar group
# ---------------------------------------------------------------------------

def test_update_calendar_column_group(fixture_path):
    from src.tools.calendar_tools import create_calendar, list_calendars, update_calendar_column_group
    _open(fixture_path)

    # Add date column
    from src.context.manager import ContextManager
    from src.tmdl.models import Column
    ctx = ContextManager.get().get_active_context()
    ctx.model_state.tables["Sales"].columns.append(
        Column(name="ShipDate", data_type="dateTime")
    )

    create_calendar("Sales", "ShipDate", "Month")
    update_calendar_column_group("ShipDate_Month", new_name="ShipMonth", time_unit="Quarter")

    calendars = list_calendars()
    names = [g["name"] for g in calendars["calendar_groups"]]
    assert "ShipMonth" in names


# ---------------------------------------------------------------------------
# Test 26 — delete calendar group
# ---------------------------------------------------------------------------

def test_delete_calendar(fixture_path):
    from src.tools.calendar_tools import create_calendar, delete_calendar, list_calendars
    _open(fixture_path)

    from src.context.manager import ContextManager
    from src.tmdl.models import Column
    ctx = ContextManager.get().get_active_context()
    ctx.model_state.tables["Sales"].columns.append(
        Column(name="DueDate", data_type="dateTime")
    )

    create_calendar("Sales", "DueDate", "Day")
    initial_count = list_calendars()["count"]

    delete_calendar("DueDate_Day")

    final = list_calendars()
    assert final["count"] == initial_count - 1


# ---------------------------------------------------------------------------
# Test 27 — calendar tools require file context
# ---------------------------------------------------------------------------

def test_calendar_tools_require_file_context():
    from src.tools.calendar_tools import list_calendars
    result = list_calendars()
    assert "error" in result


# ---------------------------------------------------------------------------
# Test 28 — invalid time_unit raises
# ---------------------------------------------------------------------------

def test_create_calendar_invalid_time_unit(fixture_path):
    from src.tools.calendar_tools import create_calendar
    _open(fixture_path)

    from src.context.manager import ContextManager
    from src.tmdl.models import Column
    ctx = ContextManager.get().get_active_context()
    ctx.model_state.tables["Sales"].columns.append(
        Column(name="TestDate", data_type="dateTime")
    )

    with pytest.raises(ValueError, match="Invalid time_unit"):
        create_calendar("Sales", "TestDate", "InvalidUnit")


# ---------------------------------------------------------------------------
# Test 29 — create UDF with invalid return type raises
# ---------------------------------------------------------------------------

def test_create_udf_invalid_return_type(fixture_path):
    from src.tools.udf_tools import create_udf
    _open(fixture_path)

    with pytest.raises(ValueError, match="Invalid return_type"):
        create_udf("BadFunc", "1", return_type="invalid_type")
