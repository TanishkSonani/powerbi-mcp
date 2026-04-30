"""Table CRUD MCP tools."""

from src.context.manager import ContextManager
from src.tmdl.models import Table


def list_tables() -> dict:
    ctx = ContextManager.get().get_active_context()
    return {
        "tables": [
            {
                "name": name,
                "columns": len(t.columns),
                "measures": len(t.measures),
                "is_hidden": t.is_hidden,
                "description": t.description,
            }
            for name, t in ctx.model_state.tables.items()
        ],
        "count": len(ctx.model_state.tables),
    }


def get_table(table_name: str) -> dict:
    ctx = ContextManager.get().get_active_context()
    t = ctx.model_state.tables.get(table_name)
    if t is None:
        raise ValueError(
            f"Table '{table_name}' not found. "
            f"Available: {list(ctx.model_state.tables.keys())}"
        )
    return {
        "name": t.name,
        "description": t.description,
        "is_hidden": t.is_hidden,
        "lineage_tag": t.lineage_tag,
        "columns": [
            {
                "name": c.name,
                "data_type": c.data_type,
                "source_column": c.source_column,
                "format_string": c.format_string,
                "is_hidden": c.is_hidden,
            }
            for c in t.columns
        ],
        "measures": [
            {
                "name": m.name,
                "expression": m.expression,
                "format_string": m.format_string,
                "display_folder": m.display_folder,
            }
            for m in t.measures
        ],
    }


def create_table(name: str, description: str | None = None) -> dict:
    ctx = ContextManager.get().get_active_context()
    if name in ctx.model_state.tables:
        raise ValueError(f"Table '{name}' already exists.")
    ctx.model_state.tables[name] = Table(name=name, description=description)
    ctx.model_state._dirty = True
    return {
        "status": "created",
        "table": name,
        "reminder": "Call save_model to persist changes to disk.",
    }


def update_table(
    table_name: str,
    new_name: str | None = None,
    description: str | None = None,
) -> dict:
    ctx = ContextManager.get().get_active_context()
    t = ctx.model_state.tables.get(table_name)
    if t is None:
        raise ValueError(f"Table '{table_name}' not found.")

    if new_name and new_name != table_name:
        t.name = new_name
        del ctx.model_state.tables[table_name]
        ctx.model_state.tables[new_name] = t

    if description is not None:
        t.description = description

    ctx.model_state._dirty = True
    return {"status": "updated", "table": new_name or table_name}


def delete_table(table_name: str) -> dict:
    ctx = ContextManager.get().get_active_context()
    if table_name not in ctx.model_state.tables:
        raise ValueError(
            f"Table '{table_name}' not found. "
            f"Available: {list(ctx.model_state.tables.keys())}"
        )
    del ctx.model_state.tables[table_name]
    ctx.model_state._dirty = True
    return {
        "status": "deleted",
        "table": table_name,
        "reminder": "Call save_model to persist changes to disk.",
    }
