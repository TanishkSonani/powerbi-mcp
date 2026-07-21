"""
Build a TmdlModelState snapshot from a LIVE Power BI Desktop model.

Every model tool reads `ctx.model_state`, which historically existed only on
FileContext (parsed from TMDL on disk). By materialising the same dataclasses from the
live engine's TMSCHEMA DMVs, all the list/get tools work against a live connection with
no changes to the tools themselves.

Read-only: this uses DMV queries through the existing ADOMD bridge. Writes go through
live_writer (TOM), never through here.
"""

from __future__ import annotations

from pathlib import Path

from src.context import ps_adomd_bridge as _bridge
from src.tmdl.models import (
    Column,
    Culture,
    DatabaseInfo,
    Measure,
    ModelInfo,
    Relationship,
    RlsFilter,
    Role,
    Table,
    TmdlModelState,
    Translation,
)

# Power BI auto-generates hidden date tables per date column. They are real objects but
# are noise in listings (and must never be edited), so they are filtered out.
_SYSTEM_TABLE_PREFIXES = ("LocalDateTable_", "DateTableTemplate_")

# TMSCHEMA DataType enum -> the lowercase names used by the TMDL dataclasses.
_DATATYPE_MAP = {
    2: "string", 6: "int64", 8: "double", 9: "datetime",
    10: "decimal", 11: "boolean", 17: "binary", 19: "variant",
}


def _is_system_table(name: str) -> bool:
    return any(name.startswith(p) for p in _SYSTEM_TABLE_PREFIXES)


def _snapshot(port: int, catalog: str) -> dict:
    """One PowerShell round-trip returning every DMV rowset we need."""
    script = f"""
$conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:{port};Initial Catalog={catalog}")
$conn.Open()

function Read-Schema($name) {{
    $rows = @()
    try {{
        $ds = $conn.GetSchemaDataSet($name, $null)
        foreach ($r in $ds.Tables[0].Rows) {{
            $h = @{{}}
            foreach ($c in $ds.Tables[0].Columns) {{
                $v = $r[$c.ColumnName]
                $h[$c.ColumnName] = if ($v -is [System.DBNull]) {{ $null }} else {{ "$v" }}
            }}
            $rows += $h
        }}
    }} catch {{ }}
    return ,@($rows)
}}

$out = @{{
    model         = Read-Schema 'TMSCHEMA_MODEL'
    tables        = Read-Schema 'TMSCHEMA_TABLES'
    columns       = Read-Schema 'TMSCHEMA_COLUMNS'
    measures      = Read-Schema 'TMSCHEMA_MEASURES'
    relationships = Read-Schema 'TMSCHEMA_RELATIONSHIPS'
    roles         = Read-Schema 'TMSCHEMA_ROLES'
    permissions   = Read-Schema 'TMSCHEMA_TABLE_PERMISSIONS'
    cultures      = Read-Schema 'TMSCHEMA_CULTURES'
    translations  = Read-Schema 'TMSCHEMA_OBJECT_TRANSLATIONS'
}}
$conn.Close()
$out | ConvertTo-Json -Compress -Depth 6"""
    data = _bridge._run_ps(script, timeout=90.0)
    return data if isinstance(data, dict) else {}


def _rows(data: dict, key: str) -> list[dict]:
    """Normalise a rowset: PowerShell collapses a 1-element array into a bare object."""
    v = data.get(key)
    if v is None:
        return []
    if isinstance(v, dict):
        return [v]
    return [r for r in v if isinstance(r, dict)]


def build_live_model_state(port: int, catalog: str) -> TmdlModelState:
    """Materialise the live model as a TmdlModelState (same shape as the file model)."""
    data = _snapshot(port, catalog)

    # ── tables (id -> Table) ───────────────────────────────────────────────
    tables: dict[str, Table] = {}
    id_to_table: dict[str, str] = {}
    for r in _rows(data, "tables"):
        name = r.get("Name") or ""
        if not name or _is_system_table(name):
            continue
        id_to_table[str(r.get("ID"))] = name
        tables[name] = Table(
            name=name,
            description=r.get("Description"),
            is_hidden=str(r.get("IsHidden", "")).lower() in ("true", "1"),
        )

    # ── columns ────────────────────────────────────────────────────────────
    col_id_to_ref: dict[str, tuple[str, str]] = {}
    for r in _rows(data, "columns"):
        tname = id_to_table.get(str(r.get("TableID")))
        cname = r.get("ExplicitName") or r.get("Name") or ""
        if not tname or not cname:
            continue
        col_id_to_ref[str(r.get("ID"))] = (tname, cname)
        dt = r.get("ExplicitDataType") or r.get("DataType")
        try:
            dtype = _DATATYPE_MAP.get(int(dt), "string") if dt is not None else "string"
        except (TypeError, ValueError):
            dtype = "string"
        tables[tname].columns.append(Column(
            name=cname,
            data_type=dtype,
            source_column=r.get("SourceColumn"),
            expression=r.get("Expression"),
            format_string=r.get("FormatString"),
            description=r.get("Description"),
            is_hidden=str(r.get("IsHidden", "")).lower() in ("true", "1"),
        ))

    # ── measures ───────────────────────────────────────────────────────────
    for r in _rows(data, "measures"):
        tname = id_to_table.get(str(r.get("TableID")))
        if not tname:
            continue
        tables[tname].measures.append(Measure(
            name=r.get("Name") or "",
            expression=r.get("Expression") or "",
            format_string=r.get("FormatString"),
            description=r.get("Description"),
            display_folder=r.get("DisplayFolder"),
            is_hidden=str(r.get("IsHidden", "")).lower() in ("true", "1"),
        ))

    # ── relationships ──────────────────────────────────────────────────────
    relationships: list[Relationship] = []
    for r in _rows(data, "relationships"):
        f = col_id_to_ref.get(str(r.get("FromColumnID")))
        t = col_id_to_ref.get(str(r.get("ToColumnID")))
        if not f or not t:
            continue
        relationships.append(Relationship(
            from_table=f[0], from_column=f[1],
            to_table=t[0], to_column=t[1],
            is_active=str(r.get("IsActive", "true")).lower() in ("true", "1"),
            name=r.get("Name"),
        ))

    # ── roles + RLS filters ────────────────────────────────────────────────
    roles: dict[str, Role] = {}
    role_id_to_name: dict[str, str] = {}
    _PERM = {"1": "Read", "2": "ReadRefresh", "3": "ReadExploreData", "4": "Admin"}
    for r in _rows(data, "roles"):
        name = r.get("Name") or ""
        if not name:
            continue
        role_id_to_name[str(r.get("ID"))] = name
        roles[name] = Role(
            name=name,
            model_permission=_PERM.get(str(r.get("ModelPermission")), "Read"),
        )
    for r in _rows(data, "permissions"):
        rname = role_id_to_name.get(str(r.get("RoleID")))
        tname = id_to_table.get(str(r.get("TableID")))
        if not rname or not tname:
            continue
        roles[rname].filters.append(
            RlsFilter(table_name=tname, filter_expression=r.get("FilterExpression") or None)
        )

    # ── cultures + translations ────────────────────────────────────────────
    cultures: dict[str, Culture] = {}
    culture_id_to_name: dict[str, str] = {}
    for r in _rows(data, "cultures"):
        name = r.get("Name") or ""
        if not name:
            continue
        culture_id_to_name[str(r.get("ID"))] = name
        cultures[name] = Culture(name=name)
    _PROP = {"1": "Caption", "2": "Description", "3": "DisplayFolder"}
    for r in _rows(data, "translations"):
        cname = culture_id_to_name.get(str(r.get("CultureID")))
        if not cname:
            continue
        cultures[cname].translations.append(Translation(
            object_type=str(r.get("ObjectType") or ""),
            object_name=str(r.get("ObjectName") or ""),
            property_name=_PROP.get(str(r.get("Property")), str(r.get("Property") or "")),
            translated_value=str(r.get("Value") or ""),
        ))

    model_rows = _rows(data, "model")
    model_name = (model_rows[0].get("Name") if model_rows else None) or "Model"

    return TmdlModelState(
        # Live models have no on-disk definition folder; a sentinel keeps the dataclass
        # satisfied while `definition_path` stays guarded on the live context.
        definition_path=Path(f"<live:localhost:{port}>"),
        database=DatabaseInfo(name=model_name),
        model_info=ModelInfo(),
        tables=tables,
        relationships=relationships,
        roles=roles,
        cultures=cultures,
        udfs={},
        calendar_groups=[],
    )
