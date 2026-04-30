"""
Ad-hoc live test script — run directly to test against real Desktop.
Usage:  python tests/live_test.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.context import ps_adomd_bridge as bridge
from src.discovery.port_finder import find_desktop_instances
from src.tools.desktop_tools import connect_desktop, discover_desktop, get_desktop_model_info
from src.tools.dax_tools import execute_dax, push_measure_live, validate_measure

# ---------------------------------------------------------------------------
PORT = 62640
CATALOG = "efba0fae-6679-4433-974c-f6ec284c5222"
# ---------------------------------------------------------------------------

print("\n=== 1. DISCOVER DESKTOP ===")
disc = discover_desktop()
print(json.dumps(disc, indent=2))

print("\n=== 2. CONNECT ===")
conn = connect_desktop()
print(json.dumps(conn, indent=2))

print("\n=== 3. MODEL INFO ===")
info = get_desktop_model_info()
print(json.dumps(info, indent=2))

print("\n=== 4. PUSH CALCULATED TABLE ===")
expr = 'DATATABLE("Key", INTEGER, "Value", DOUBLE, {{1,100},{2,200},{3,300}})'
tmsl = {
    "createOrReplace": {
        "object": {"database": CATALOG, "table": "TestTable"},
        "table": {
            "name": "TestTable",
            "partitions": [{
                "name": "TestTable-Partition",
                "mode": "import",
                "source": {"type": "calculated", "expression": expr},
            }],
        },
    }
}
try:
    r = bridge.push_tmsl(PORT, CATALOG, json.dumps(tmsl))
    print("push_tmsl result:", r)
except Exception as e:
    print("push_tmsl error:", e)

print("\n=== 4b. REFRESH TABLE ===")
refresh_tmsl = json.dumps({
    "refresh": {
        "type": "full",
        "objects": [{"database": CATALOG, "table": "TestTable"}],
    }
})
try:
    rr = bridge.push_tmsl(PORT, CATALOG, refresh_tmsl)
    print("refresh result:", rr)
except Exception as e:
    print("refresh error:", e)

print("\n=== 5. MODEL INFO AFTER TABLE PUSH ===")
info2 = get_desktop_model_info()
print(json.dumps(info2, indent=2))

print("\n=== 6. EXECUTE DAX ===")
try:
    dax_result = execute_dax("EVALUATE SUMMARIZECOLUMNS(TestTable[Key], TestTable[Value])", max_rows=10)
    print(json.dumps({k: v for k, v in dax_result.items() if k != "markdown_table"}, indent=2))
    print("\nMarkdown table:")
    print(dax_result.get("markdown_table", ""))
except Exception as e:
    print("execute_dax error:", e)

print("\n=== 7. PUSH MEASURE ===")
try:
    m = push_measure_live(
        table_name="TestTable",
        name="Total Value",
        expression="SUM(TestTable[Value])",
        format_string="#,0.00",
    )
    print("push_measure_live:", json.dumps(m, indent=2))
except Exception as e:
    print("push_measure_live error:", e)

print("\n=== 8. VALIDATE MEASURE ===")
try:
    v = validate_measure("TestTable", "SUM(TestTable[Value])")
    print("validate_measure:", json.dumps(v, indent=2))
except Exception as e:
    print("validate_measure error:", e)

print("\n=== 9. EXECUTE DAX — MEASURE ===")
try:
    dax2 = execute_dax('EVALUATE ROW("Total Value", [Total Value])', max_rows=5)
    print(dax2.get("markdown_table", ""))
except Exception as e:
    print("execute_dax measure error:", e)

print("\nDone.")
