"""Column CRUD MCP tools."""

from src.context.manager import ContextManager
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
