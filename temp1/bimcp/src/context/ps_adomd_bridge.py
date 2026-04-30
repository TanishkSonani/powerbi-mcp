"""
PowerShell/ADOMD.NET bridge for Power BI Desktop's embedded Analysis Services.

Power BI Desktop's msmdsrv.exe speaks TCP-based XMLA (not HTTP).  The only
pure-Python way to reach it without pythonnet is to shell out to PowerShell,
which can load Microsoft.PowerBI.AdomdClient.dll directly from the Desktop
installation and return JSON results via stdout.

All public functions raise RuntimeError on failure and return plain dicts/lists
on success — the same contract as LiveContext's HTTP equivalents.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# DLL location
# ---------------------------------------------------------------------------

_PBI_BIN = Path("C:/Program Files/Microsoft Power BI Desktop/bin")
_ADOMD_DLL = _PBI_BIN / "Microsoft.PowerBI.AdomdClient.dll"

_PS_HEADER = f"""
$ErrorActionPreference = 'Continue'
Add-Type -Path '{_ADOMD_DLL}' -ErrorAction SilentlyContinue
"""


def is_available() -> bool:
    return _ADOMD_DLL.exists()


# ---------------------------------------------------------------------------
# Internal runner
# ---------------------------------------------------------------------------

def _run_ps(script: str, timeout: float = 30.0) -> object:
    """Execute a PowerShell script and parse its JSON stdout."""
    full = _PS_HEADER + script
    result = subprocess.run(
        ["powershell.exe", "-NonInteractive", "-NoProfile", "-Command", full],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    stdout = result.stdout.strip()
    if not stdout or stdout == "null":
        stderr = result.stderr.strip()
        raise RuntimeError(stderr or "PowerShell returned no output")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"PowerShell output is not valid JSON: {stdout[:300]}") from exc


# ---------------------------------------------------------------------------
# Public bridge functions
# ---------------------------------------------------------------------------

def probe_port(port: int, timeout: float = 5.0) -> dict | None:
    """
    Try to connect to localhost:{port} via ADOMD.NET.
    Returns {model_name, catalog, port} or None if unreachable.
    """
    script = f"""
try {{
    $conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:{port}")
    $conn.Open()
    $ds = $conn.GetSchemaDataSet("DBSCHEMA_CATALOGS", $null)
    $cat = if ($ds.Tables[0].Rows.Count -gt 0) {{ $ds.Tables[0].Rows[0]['CATALOG_NAME'] }} else {{ "model@{port}" }}
    $ds2 = $conn.GetSchemaDataSet("TMSCHEMA_MODEL", $null)
    $mName = if ($ds2.Tables[0].Rows.Count -gt 0) {{ $ds2.Tables[0].Rows[0]['Name'] }} else {{ $cat }}
    $conn.Close()
    @{{ model_name = $mName; catalog = $cat; port = {port} }} | ConvertTo-Json -Compress
}} catch {{
    Write-Output "null"
}}"""
    try:
        data = _run_ps(script, timeout=timeout)
        if data is None:
            return None
        return {
            "model_name": data["model_name"],
            "catalog": data["catalog"],
            "port": port,
            "connection_string": f"localhost:{port}",
        }
    except Exception:
        return None


def get_model_info(port: int, catalog: str) -> dict:
    """Return {model_name, table_count, measure_count, tables}."""
    script = f"""
$conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:{port};Initial Catalog={catalog}")
$conn.Open()

$dsMod = $conn.GetSchemaDataSet("TMSCHEMA_MODEL", $null)
$modelName = if ($dsMod.Tables[0].Rows.Count -gt 0) {{ $dsMod.Tables[0].Rows[0]['Name'] }} else {{ "Model" }}

$dsTbl = $conn.GetSchemaDataSet("TMSCHEMA_TABLES", $null)
$tables = @()
foreach ($r in $dsTbl.Tables[0].Rows) {{
    if (-not $r['IsHidden']) {{ $tables += $r['Name'] }}
}}

$dsMeas = $conn.GetSchemaDataSet("TMSCHEMA_MEASURES", $null)
$measCount = $dsMeas.Tables[0].Rows.Count

$conn.Close()
@{{
    model_name   = $modelName
    catalog      = "{catalog}"
    port         = {port}
    table_count  = $tables.Count
    measure_count = $measCount
    tables       = $tables
}} | ConvertTo-Json -Compress"""
    return _run_ps(script)


def execute_dax(port: int, catalog: str, dax_query: str, max_rows: int = 500) -> dict:
    """Execute a DAX query and return {columns, rows, row_count, truncated, markdown_table}."""
    # Escape single quotes in the DAX for embedding in PS string
    safe_dax = dax_query.replace("'", "''")
    script = f"""
$conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:{port};Initial Catalog={catalog}")
$conn.Open()
$cmd  = $conn.CreateCommand()
$cmd.CommandText = '{safe_dax}'
$reader = $cmd.ExecuteReader()

$cols = @()
for ($i = 0; $i -lt $reader.FieldCount; $i++) {{
    $raw = $reader.GetName($i)
    $clean = $raw -replace '^\\[?[^\\]\\[]+\\]\\.\\[?', '' -replace '^[^\\[]*\\[', '' -replace '\\]$', ''
    $cols += $clean
}}

$rows = [System.Collections.Generic.List[object]]::new()
$count = 0
while ($reader.Read() -and $count -lt {max_rows + 1}) {{
    $row = @()
    for ($i = 0; $i -lt $reader.FieldCount; $i++) {{
        $v = $reader.GetValue($i)
        $row += if ($v -eq [System.DBNull]::Value) {{ $null }} else {{ "$v" }}
    }}
    $rows.Add($row)
    $count++
}}
$reader.Close(); $conn.Close()

$truncated = $rows.Count -gt {max_rows}
if ($truncated) {{ $rows = $rows | Select-Object -First {max_rows} }}

@{{ columns = $cols; rows = $rows; row_count = $rows.Count; truncated = $truncated }} | ConvertTo-Json -Depth 5 -Compress"""
    data = _run_ps(script)

    columns = data.get("columns") or []
    rows_raw = data.get("rows") or []
    # Normalise: single-row / single-col results may not be arrays in PS JSON
    if columns and not isinstance(columns, list):
        columns = [columns]
    rows: list[list] = []
    for r in rows_raw:
        if isinstance(r, list):
            rows.append(r)
        else:
            rows.append([r])

    # Build markdown table
    if columns:
        header = "| " + " | ".join(columns) + " |"
        sep = "| " + " | ".join(["---"] * len(columns)) + " |"
        data_rows = [
            "| " + " | ".join("" if v is None else str(v) for v in row) + " |"
            for row in rows
        ]
        md = "\n".join([header, sep] + data_rows)
    else:
        md = "_no columns_"

    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": data.get("truncated", False),
        "markdown_table": md,
    }


def push_tmsl(port: int, catalog: str, tmsl_json: str) -> dict:
    """
    Execute a TMSL command via ADOMD ExecuteNonQuery.
    Returns {status: 'ok'} or raises RuntimeError.
    """
    safe = tmsl_json.replace("'", "''")
    script = f"""
$conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:{port};Initial Catalog={catalog}")
$conn.Open()
$cmd = $conn.CreateCommand()
$cmd.CommandText = '{safe}'
try {{
    $cmd.ExecuteNonQuery() | Out-Null
    $conn.Close()
    @{{ status = 'ok' }} | ConvertTo-Json -Compress
}} catch {{
    $conn.Close()
    @{{ status = 'error'; message = $_.Exception.Message }} | ConvertTo-Json -Compress
}}"""
    result = _run_ps(script)
    if result.get("status") == "error":
        raise RuntimeError(result.get("message", "TMSL command failed"))
    return result


def push_measure(
    port: int,
    catalog: str,
    table_name: str,
    name: str,
    expression: str,
    format_string: str | None = None,
) -> dict:
    """
    Add or replace a measure in a table via TMSL createOrReplace.

    Power BI Desktop's embedded AS only accepts a full table body inside
    createOrReplace (individual `measure` objects inside `create` are rejected).
    This function reads the existing measures and partitions, merges the new
    measure in, and posts a single createOrReplace for the whole table.
    """
    safe_table = table_name.replace("'", "''")
    safe_name = name.replace("'", "''")
    safe_expr = expression.replace("'", "''")
    safe_fmt = format_string.replace("'", "''") if format_string else None
    fmt_part = f"; formatString = '{safe_fmt}'" if safe_fmt else ""

    script = f"""
$conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:{port};Initial Catalog={catalog}")
$conn.Open()

# Resolve table ID (schemas use TableID, not TableName)
$dsTbl = $conn.GetSchemaDataSet("TMSCHEMA_TABLES", $null)
$tableId = $null
foreach ($r in $dsTbl.Tables[0].Rows) {{
    if ($r['Name'] -eq '{safe_table}') {{ $tableId = $r['ID'] }}
}}

# Read existing measures for this table
$dsMeas = $conn.GetSchemaDataSet("TMSCHEMA_MEASURES", $null)
$measList = [System.Collections.Generic.List[hashtable]]::new()
foreach ($r in $dsMeas.Tables[0].Rows) {{
    if ($r['TableID'] -eq $tableId) {{
        $m = @{{ name = $r['Name']; expression = $r['Expression'] }}
        if ($r['FormatString']) {{ $m['formatString'] = $r['FormatString'] }}
        $measList.Add($m)
    }}
}}
# Remove old entry with same name (we'll replace it)
$measList = $measList | Where-Object {{ $_['name'] -ne '{safe_name}' }}
# Add the new/updated measure
$newMeas = @{{ name = '{safe_name}'; expression = '{safe_expr}'{fmt_part} }}
$measList += $newMeas

# Read existing partitions for this table (TableID not TableName; expression in QueryDefinition)
$dsPart = $conn.GetSchemaDataSet("TMSCHEMA_PARTITIONS", $null)
$partList = [System.Collections.Generic.List[hashtable]]::new()
foreach ($r in $dsPart.Tables[0].Rows) {{
    if ($r['TableID'] -eq $tableId) {{
        $p = @{{
            name = $r['Name']
            mode = 'import'
            source = @{{
                type = 'calculated'
                expression = $r['QueryDefinition']
            }}
        }}
        $partList.Add($p)
    }}
}}

$tableDef = @{{
    name       = '{safe_table}'
    measures   = @($measList)
    partitions = @($partList)
}}
$tmsl = @{{
    createOrReplace = @{{
        object = @{{ database = '{catalog}'; table = '{safe_table}' }}
        table  = $tableDef
    }}
}}
$cmd = $conn.CreateCommand()
$cmd.CommandText = ($tmsl | ConvertTo-Json -Depth 10 -Compress)
try {{
    $cmd.ExecuteNonQuery() | Out-Null
    # Refresh the table so calculated partition data is available after createOrReplace
    $refresh = @{{
        refresh = @{{
            type    = 'full'
            objects = @(@{{ database = '{catalog}'; table = '{safe_table}' }})
        }}
    }}
    $cmd.CommandText = ($refresh | ConvertTo-Json -Depth 5 -Compress)
    $cmd.ExecuteNonQuery() | Out-Null
    $conn.Close()
    @{{ status = 'ok' }} | ConvertTo-Json -Compress
}} catch {{
    $conn.Close()
    @{{ status = 'error'; message = $_.Exception.Message }} | ConvertTo-Json -Compress
}}"""
    result = _run_ps(script, timeout=60.0)
    if result.get("status") == "error":
        raise RuntimeError(result.get("message", "TMSL push_measure failed"))
    return result
