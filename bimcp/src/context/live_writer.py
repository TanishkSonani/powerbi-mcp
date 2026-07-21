"""
LiveWriter — safe, granular writes to a running Power BI Desktop model.

Why this exists
---------------
The previous live-write path (ps_adomd_bridge.push_measure) used a TMSL
`createOrReplace` scoped to the whole TABLE. TMSL replaces the entire object, and the
body it sent contained only {name, measures, partitions}: `columns` was omitted, so a
replace would DROP every column, and partitions were hardcoded to
`source.type = 'calculated'` with the M text as the expression, corrupting `type: 'm'`
Power Query partitions. That is destructive on any real table.

This module never does a whole-object replace. It manipulates the Tabular Object Model
(TOM) in place — `model.Tables["x"].Measures.Add(...)`, then `model.SaveChanges()` —
which is granular and preserves everything it does not touch. That is how Tabular
Editor operates against Desktop.

TOM lives in Microsoft.AnalysisServices.Tabular.dll, which ships with the Analysis
Services client libraries and is NOT part of Power BI Desktop. When it is missing every
operation refuses with one actionable message rather than falling back to the lossy
path — refusing is always better than silently damaging a model.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

_TOM_DLL = "Microsoft.AnalysisServices.Tabular.dll"
_CORE_DLL = "Microsoft.AnalysisServices.Core.dll"

_tom_paths_cache: tuple[Path, Path] | None = None

TOM_MISSING_MESSAGE = (
    "Live model edits need the Tabular Object Model (TOM), which is not installed. "
    f"'{_TOM_DLL}' was not found in the GAC or any Program Files location.\n"
    "Either:\n"
    "  • install the free Microsoft 'Analysis Services client libraries' (AMO/ADOMD) "
    "and reconnect — every live edit tool then works, or\n"
    "  • edit the saved model instead: open_pbip_folder(<path>) → make changes → save_model().\n"
    "Live READ tools (list_tables, list_measures, get_table, execute_dax, ...) work "
    "without TOM."
)


def _candidate_dirs():
    """Directories that may contain the AMO/TOM assemblies."""
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        import os
        base = os.environ.get(env)
        if base:
            yield Path(base) / "Microsoft.NET" / "ADOMD.NET"
            yield Path(base) / "Microsoft SQL Server" / "Management Studio"
            yield Path(base) / "Microsoft Analysis Services"
    import os
    windir = os.environ.get("WINDIR", r"C:\Windows")
    yield Path(windir) / "Microsoft.NET" / "assembly" / "GAC_MSIL" / "Microsoft.AnalysisServices.Tabular"


def find_tom() -> tuple[Path, Path] | None:
    """Locate (tabular_dll, core_dll). Cached once found."""
    global _tom_paths_cache
    if _tom_paths_cache and all(p.exists() for p in _tom_paths_cache):
        return _tom_paths_cache
    for d in _candidate_dirs():
        try:
            if not d.exists():
                continue
            for tab in d.rglob(_TOM_DLL):
                core = tab.parent / _CORE_DLL
                if core.exists():
                    _tom_paths_cache = (tab, core)
                    return _tom_paths_cache
        except OSError:
            continue
    return None


def is_tom_available() -> bool:
    return find_tom() is not None


# ---------------------------------------------------------------------------
# PowerShell TOM runner
# ---------------------------------------------------------------------------

def _run_tom(port: int, catalog: str, body: str, timeout: float = 90.0) -> dict:
    """
    Run a TOM script body against the live model.

    `body` receives $model (Microsoft.AnalysisServices.Tabular.Model) and must not call
    SaveChanges — this wrapper does it once, so a failing body leaves the model untouched.
    """
    paths = find_tom()
    if paths is None:
        return {"error": TOM_MISSING_MESSAGE}
    tab, core = paths

    script = f"""
$ErrorActionPreference = 'Stop'
try {{
    Add-Type -Path '{core}' -ErrorAction SilentlyContinue
    Add-Type -Path '{tab}'  -ErrorAction SilentlyContinue
    $srv = New-Object Microsoft.AnalysisServices.Tabular.Server
    $srv.Connect("localhost:{port}")
    $db = $srv.Databases.FindByName('{catalog}')
    if ($null -eq $db) {{ $db = $srv.Databases['{catalog}'] }}
    if ($null -eq $db) {{ throw "Database '{catalog}' not found on localhost:{port}" }}
    $model = $db.Model

{body}

    $model.SaveChanges() | Out-Null
    $srv.Disconnect()
    @{{ status = 'ok' }} | ConvertTo-Json -Compress
}} catch {{
    try {{ if ($srv) {{ $srv.Disconnect() }} }} catch {{}}
    @{{ status = 'error'; message = $_.Exception.Message }} | ConvertTo-Json -Compress
}}"""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NonInteractive", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
        )
        out = (result.stdout or "").strip()
        if not out:
            return {"error": (result.stderr or "TOM script produced no output").strip()[:500]}
        data = json.loads(out)
    except json.JSONDecodeError:
        return {"error": f"TOM script returned non-JSON: {out[:300]}"}
    except Exception as exc:
        return {"error": f"TOM script failed: {exc}"}

    if data.get("status") == "error":
        return {"error": data.get("message", "unknown TOM error")}
    return data


def _ps(value: str | None) -> str:
    """Escape a value for a single-quoted PowerShell literal."""
    return "" if value is None else str(value).replace("'", "''")


# ---------------------------------------------------------------------------
# Measures
# ---------------------------------------------------------------------------

def upsert_measure(port, catalog, table_name, name, expression,
                   format_string=None, description=None, display_folder=None) -> dict:
    """Create or update a measure in place — columns/partitions are untouched."""
    optional = ""
    if format_string is not None:
        optional += f"\n    $m.FormatString = '{_ps(format_string)}'"
    if description is not None:
        optional += f"\n    $m.Description = '{_ps(description)}'"
    if display_folder is not None:
        optional += f"\n    $m.DisplayFolder = '{_ps(display_folder)}'"

    body = f"""
    $t = $model.Tables.Find('{_ps(table_name)}')
    if ($null -eq $t) {{ throw "Table '{_ps(table_name)}' not found" }}
    $m = $t.Measures.Find('{_ps(name)}')
    if ($null -eq $m) {{
        $m = New-Object Microsoft.AnalysisServices.Tabular.Measure
        $m.Name = '{_ps(name)}'
        $t.Measures.Add($m) | Out-Null
    }}
    $m.Expression = '{_ps(expression)}'{optional}
"""
    r = _run_tom(port, catalog, body)
    return r if "error" in r else {
        "status": "ok", "applied": "live", "table": table_name, "measure": name,
    }


def delete_measure(port, catalog, table_name, name) -> dict:
    body = f"""
    $t = $model.Tables.Find('{_ps(table_name)}')
    if ($null -eq $t) {{ throw "Table '{_ps(table_name)}' not found" }}
    $m = $t.Measures.Find('{_ps(name)}')
    if ($null -eq $m) {{ throw "Measure '{_ps(name)}' not found in '{_ps(table_name)}'" }}
    $t.Measures.Remove($m) | Out-Null
"""
    r = _run_tom(port, catalog, body)
    return r if "error" in r else {
        "status": "deleted", "applied": "live", "table": table_name, "measure": name,
    }


# ---------------------------------------------------------------------------
# Columns (calculated columns only — data columns come from the partition query)
# ---------------------------------------------------------------------------

def upsert_calculated_column(port, catalog, table_name, name, expression,
                             data_type=None, format_string=None, description=None) -> dict:
    optional = ""
    if format_string is not None:
        optional += f"\n    $c.FormatString = '{_ps(format_string)}'"
    if description is not None:
        optional += f"\n    $c.Description = '{_ps(description)}'"
    if data_type:
        optional += (
            f"\n    $c.DataType = [Microsoft.AnalysisServices.Tabular.DataType]::"
            f"{_ps(str(data_type).capitalize())}"
        )
    body = f"""
    $t = $model.Tables.Find('{_ps(table_name)}')
    if ($null -eq $t) {{ throw "Table '{_ps(table_name)}' not found" }}
    $c = $t.Columns.Find('{_ps(name)}')
    if ($null -eq $c) {{
        $c = New-Object Microsoft.AnalysisServices.Tabular.CalculatedColumn
        $c.Name = '{_ps(name)}'
        $t.Columns.Add($c) | Out-Null
    }}
    if ($c -isnot [Microsoft.AnalysisServices.Tabular.CalculatedColumn]) {{
        throw "Column '{_ps(name)}' exists but is a data column; only calculated columns can be edited live"
    }}
    $c.Expression = '{_ps(expression)}'{optional}
"""
    r = _run_tom(port, catalog, body)
    return r if "error" in r else {
        "status": "ok", "applied": "live", "table": table_name, "column": name,
    }


def delete_column(port, catalog, table_name, name) -> dict:
    body = f"""
    $t = $model.Tables.Find('{_ps(table_name)}')
    if ($null -eq $t) {{ throw "Table '{_ps(table_name)}' not found" }}
    $c = $t.Columns.Find('{_ps(name)}')
    if ($null -eq $c) {{ throw "Column '{_ps(name)}' not found in '{_ps(table_name)}'" }}
    $t.Columns.Remove($c) | Out-Null
"""
    r = _run_tom(port, catalog, body)
    return r if "error" in r else {
        "status": "deleted", "applied": "live", "table": table_name, "column": name,
    }


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

def delete_table(port, catalog, table_name) -> dict:
    body = f"""
    $t = $model.Tables.Find('{_ps(table_name)}')
    if ($null -eq $t) {{ throw "Table '{_ps(table_name)}' not found" }}
    $model.Tables.Remove($t) | Out-Null
"""
    r = _run_tom(port, catalog, body)
    return r if "error" in r else {"status": "deleted", "applied": "live", "table": table_name}


def update_table(port, catalog, table_name, new_name=None, description=None, is_hidden=None) -> dict:
    parts = ""
    if description is not None:
        parts += f"\n    $t.Description = '{_ps(description)}'"
    if is_hidden is not None:
        parts += f"\n    $t.IsHidden = ${str(bool(is_hidden)).lower()}"
    if new_name:
        parts += f"\n    $t.Name = '{_ps(new_name)}'"
    if not parts:
        return {"status": "ok", "applied": "live", "table": table_name, "note": "nothing to change"}
    body = f"""
    $t = $model.Tables.Find('{_ps(table_name)}')
    if ($null -eq $t) {{ throw "Table '{_ps(table_name)}' not found" }}{parts}
"""
    r = _run_tom(port, catalog, body)
    return r if "error" in r else {
        "status": "updated", "applied": "live", "table": new_name or table_name,
    }


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------

def create_relationship(port, catalog, from_table, from_column, to_table, to_column,
                        from_cardinality="many", to_cardinality="one") -> dict:
    fc = "Many" if str(from_cardinality).lower().startswith("m") else "One"
    tc = "Many" if str(to_cardinality).lower().startswith("m") else "One"
    body = f"""
    $ft = $model.Tables.Find('{_ps(from_table)}'); if ($null -eq $ft) {{ throw "Table '{_ps(from_table)}' not found" }}
    $tt = $model.Tables.Find('{_ps(to_table)}');   if ($null -eq $tt) {{ throw "Table '{_ps(to_table)}' not found" }}
    $fc = $ft.Columns.Find('{_ps(from_column)}');  if ($null -eq $fc) {{ throw "Column '{_ps(from_column)}' not found in '{_ps(from_table)}'" }}
    $tcol = $tt.Columns.Find('{_ps(to_column)}');  if ($null -eq $tcol) {{ throw "Column '{_ps(to_column)}' not found in '{_ps(to_table)}'" }}
    $r = New-Object Microsoft.AnalysisServices.Tabular.SingleColumnRelationship
    $r.FromColumn = $fc
    $r.ToColumn = $tcol
    $r.FromCardinality = [Microsoft.AnalysisServices.Tabular.RelationshipEndCardinality]::{fc}
    $r.ToCardinality = [Microsoft.AnalysisServices.Tabular.RelationshipEndCardinality]::{tc}
    $model.Relationships.Add($r) | Out-Null
"""
    r = _run_tom(port, catalog, body)
    return r if "error" in r else {
        "status": "created", "applied": "live",
        "relationship": f"{from_table}[{from_column}] -> {to_table}[{to_column}]",
    }


def delete_relationship(port, catalog, from_table, from_column, to_table, to_column) -> dict:
    body = f"""
    $target = $null
    foreach ($r in $model.Relationships) {{
        if ($r.FromTable.Name -eq '{_ps(from_table)}' -and $r.FromColumn.Name -eq '{_ps(from_column)}' -and
            $r.ToTable.Name -eq '{_ps(to_table)}'   -and $r.ToColumn.Name -eq '{_ps(to_column)}') {{ $target = $r }}
    }}
    if ($null -eq $target) {{ throw "Relationship not found" }}
    $model.Relationships.Remove($target) | Out-Null
"""
    r = _run_tom(port, catalog, body)
    return r if "error" in r else {"status": "deleted", "applied": "live"}


# ---------------------------------------------------------------------------
# Roles / RLS
# ---------------------------------------------------------------------------

def upsert_role(port, catalog, name, model_permission="Read") -> dict:
    perm = _ps(str(model_permission))
    body = f"""
    $r = $model.Roles.Find('{_ps(name)}')
    if ($null -eq $r) {{
        $r = New-Object Microsoft.AnalysisServices.Tabular.ModelRole
        $r.Name = '{_ps(name)}'
        $model.Roles.Add($r) | Out-Null
    }}
    $r.ModelPermission = [Microsoft.AnalysisServices.Tabular.ModelPermission]::{perm}
"""
    r = _run_tom(port, catalog, body)
    return r if "error" in r else {"status": "ok", "applied": "live", "role": name}


def upsert_rls_filter(port, catalog, role_name, table_name, filter_expression=None) -> dict:
    expr = f"'{_ps(filter_expression)}'" if filter_expression else "''"
    body = f"""
    $r = $model.Roles.Find('{_ps(role_name)}'); if ($null -eq $r) {{ throw "Role '{_ps(role_name)}' not found" }}
    $t = $model.Tables.Find('{_ps(table_name)}'); if ($null -eq $t) {{ throw "Table '{_ps(table_name)}' not found" }}
    $tp = $r.TablePermissions.Find('{_ps(table_name)}')
    if ($null -eq $tp) {{
        $tp = New-Object Microsoft.AnalysisServices.Tabular.TablePermission
        $tp.Table = $t
        $r.TablePermissions.Add($tp) | Out-Null
    }}
    $tp.FilterExpression = {expr}
"""
    r = _run_tom(port, catalog, body)
    return r if "error" in r else {
        "status": "ok", "applied": "live", "role": role_name, "table": table_name,
    }


def delete_rls_filter(port, catalog, role_name, table_name) -> dict:
    body = f"""
    $r = $model.Roles.Find('{_ps(role_name)}'); if ($null -eq $r) {{ throw "Role '{_ps(role_name)}' not found" }}
    $tp = $r.TablePermissions.Find('{_ps(table_name)}')
    if ($null -eq $tp) {{ throw "No RLS filter on '{_ps(table_name)}' for role '{_ps(role_name)}'" }}
    $r.TablePermissions.Remove($tp) | Out-Null
"""
    r = _run_tom(port, catalog, body)
    return r if "error" in r else {
        "status": "deleted", "applied": "live", "role": role_name, "table": table_name,
    }


# ---------------------------------------------------------------------------
# Translations
# ---------------------------------------------------------------------------

def upsert_translation(port, catalog, culture_name, object_type, object_name,
                       property_name, translated_value, table_name=None) -> dict:
    """Set a translated caption/description/display-folder for an object."""
    prop = _ps(str(property_name).capitalize())
    otype = str(object_type).lower()
    if otype == "table":
        locate = f"$obj = $model.Tables.Find('{_ps(object_name)}')"
    elif otype in ("measure", "column"):
        coll = "Measures" if otype == "measure" else "Columns"
        locate = (
            f"$t = $model.Tables.Find('{_ps(table_name)}'); "
            f"if ($null -eq $t) {{ throw \"Table '{_ps(table_name)}' not found\" }}; "
            f"$obj = $t.{coll}.Find('{_ps(object_name)}')"
        )
    else:
        return {"error": f"Unsupported object_type '{object_type}' (use Table, Measure or Column)."}

    body = f"""
    $c = $model.Cultures.Find('{_ps(culture_name)}')
    if ($null -eq $c) {{
        $c = New-Object Microsoft.AnalysisServices.Tabular.Culture
        $c.Name = '{_ps(culture_name)}'
        $model.Cultures.Add($c) | Out-Null
    }}
    {locate}
    if ($null -eq $obj) {{ throw "Object '{_ps(object_name)}' not found" }}
    $c.ObjectTranslations.SetTranslation($obj,
        [Microsoft.AnalysisServices.Tabular.TranslatedProperty]::{prop},
        '{_ps(translated_value)}')
"""
    r = _run_tom(port, catalog, body)
    return r if "error" in r else {
        "status": "ok", "applied": "live", "culture": culture_name, "object": object_name,
    }
