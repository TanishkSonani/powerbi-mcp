"""Column CRUD MCP tools."""

from src.context import live_writer
from src.context.manager import ContextManager
from src.tools._live import live_target
from src.tmdl.models import Column


def list_columns(table_name: str) -> dict:
    ctx = ContextManager.get().get_active_context()
    t = _require_table(ctx, table_name)
    return {
        "table": table_name,
        "columns": [
            {
                "name": c.name,
                "data_type": c.data_type,
                "source_column": c.source_column,
                "format_string": c.format_string,
                "is_hidden": c.is_hidden,
                "description": c.description,
            }
            for c in t.columns
        ],
        "count": len(t.columns),
    }


def create_column(
    table_name: str,
    name: str,
    data_type: str = "string",
    source_column: str | None = None,
    expression: str | None = None,
    format_string: str | None = None,
    description: str | None = None,
) -> dict:
    lc = live_target()
    if lc is not None:
        if not expression:
            return {
                "error": (
                    "Live models can only add CALCULATED columns (they need a DAX "
                    "'expression'). A data column comes from the table's Power Query "
                    "partition — change the query in Power BI Desktop, or edit the "
                    "saved model with open_pbip_folder + save_model."
                )
            }
        return live_writer.upsert_calculated_column(
            lc.port, lc.catalog, table_name, name, expression,
            data_type=data_type, format_string=format_string, description=description,
        )
    ctx = ContextManager.get().get_active_context()
    t = _require_table(ctx, table_name)
    if any(c.name == name for c in t.columns):
        raise ValueError(f"Column '{name}' already exists in table '{table_name}'.")
    t.columns.append(Column(
        name=name, data_type=data_type, source_column=source_column,
        expression=expression, format_string=format_string, description=description,
    ))
    ctx.model_state._dirty = True
    return {
        "status": "created",
        "table": table_name,
        "column": name,
        "reminder": "Call save_model to persist changes to disk.",
    }


def update_column(
    table_name: str,
    column_name: str,
    new_name: str | None = None,
    description: str | None = None,
    format_string: str | None = None,
    data_type: str | None = None,
) -> dict:
    lc = live_target()
    if lc is not None:
        # A live column's shape is owned by its table's Power Query partition; TOM has
        # no safe in-place rename/retype for a data column. Refuse rather than mutate
        # the in-memory snapshot and report a success that never reaches the engine.
        table = lc.model_state.tables.get(table_name)
        col = next((c for c in table.columns if c.name == column_name), None) if table else None
        if col is None:
            return {"error": f"Column '{column_name}' not found in table '{table_name}'."}
        if col.expression:
            return live_writer.upsert_calculated_column(
                lc.port, lc.catalog, table_name, new_name or column_name, col.expression,
                data_type=data_type, format_string=format_string, description=description,
            )
        return {
            "error": (
                f"'{table_name}'[{column_name}] is a DATA column: its name and type come "
                "from the table's Power Query partition, which can't be changed live. "
                "Edit the query in Power BI Desktop, or change the saved model with "
                "open_pbip_folder + save_model. (Calculated columns CAN be updated live.)"
            )
        }
    ctx = ContextManager.get().get_active_context()
    t = _require_table(ctx, table_name)
    c = next((c for c in t.columns if c.name == column_name), None)
    if c is None:
        raise ValueError(f"Column '{column_name}' not found in table '{table_name}'.")
    if new_name:
        c.name = new_name
    if description is not None:
        c.description = description
    if format_string is not None:
        c.format_string = format_string
    if data_type is not None:
        c.data_type = data_type
    ctx.model_state._dirty = True
    return {"status": "updated", "table": table_name, "column": new_name or column_name}


def delete_column(table_name: str, column_name: str) -> dict:
    lc = live_target()
    if lc is not None:
        return live_writer.delete_column(lc.port, lc.catalog, table_name, column_name)
    ctx = ContextManager.get().get_active_context()
    t = _require_table(ctx, table_name)
    before = len(t.columns)
    t.columns = [c for c in t.columns if c.name != column_name]
    if len(t.columns) == before:
        raise ValueError(f"Column '{column_name}' not found in table '{table_name}'.")
    ctx.model_state._dirty = True
    return {"status": "deleted", "table": table_name, "column": column_name}


def _require_table(ctx, table_name: str):
    t = ctx.model_state.tables.get(table_name)
    if t is None:
        raise ValueError(
            f"Table '{table_name}' not found. "
            f"Available: {list(ctx.model_state.tables.keys())}"
        )
    return t
