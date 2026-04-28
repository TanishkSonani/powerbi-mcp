"""Measure CRUD MCP tools."""

from src.context.manager import ContextManager
from src.tmdl.models import Measure

_EXPR_PREVIEW_LEN = 80


def list_measures() -> dict:
    ctx = ContextManager.get().get_active_context()
    measures = []
    for table_name, t in ctx.model_state.tables.items():
        for m in t.measures:
            expr = m.expression
            preview = (expr[:_EXPR_PREVIEW_LEN] + "…") if len(expr) > _EXPR_PREVIEW_LEN else expr
            measures.append({
                "table": table_name,
                "name": m.name,
                "expression_preview": preview,
                "format_string": m.format_string,
                "display_folder": m.display_folder,
                "is_hidden": m.is_hidden,
            })
    return {"measures": measures, "count": len(measures)}


def get_measure(table_name: str, measure_name: str) -> dict:
    ctx = ContextManager.get().get_active_context()
    t = _require_table(ctx, table_name)
    m = _find_measure(t, measure_name)
    return {
        "table": table_name,
        "name": m.name,
        "expression": m.expression,
        "format_string": m.format_string,
        "description": m.description,
        "display_folder": m.display_folder,
        "is_hidden": m.is_hidden,
        "lineage_tag": m.lineage_tag,
    }


def create_measure(
    table_name: str,
    name: str,
    expression: str,
    format_string: str | None = None,
    description: str | None = None,
    display_folder: str | None = None,
) -> dict:
    ctx = ContextManager.get().get_active_context()
    t = _require_table(ctx, table_name)
    if any(m.name == name for m in t.measures):
        raise ValueError(f"Measure '{name}' already exists in table '{table_name}'.")
    t.measures.append(Measure(
        name=name, expression=expression, format_string=format_string,
        description=description, display_folder=display_folder,
    ))
    ctx.model_state._dirty = True
    return {
        "status": "created",
        "table": table_name,
        "measure": name,
        "reminder": "Call save_model to persist changes to disk.",
    }


def update_measure(
    table_name: str,
    measure_name: str,
    new_expression: str | None = None,
    new_name: str | None = None,
    new_format_string: str | None = None,
    description: str | None = None,
    display_folder: str | None = None,
) -> dict:
    ctx = ContextManager.get().get_active_context()
    t = _require_table(ctx, table_name)
    m = _find_measure(t, measure_name)
    if new_expression is not None:
        m.expression = new_expression
    if new_name and new_name != measure_name:
        m.name = new_name
    if new_format_string is not None:
        m.format_string = new_format_string
    if description is not None:
        m.description = description
    if display_folder is not None:
        m.display_folder = display_folder
    ctx.model_state._dirty = True
    return {
        "status": "updated",
        "table": table_name,
        "measure": new_name or measure_name,
    }


def delete_measure(table_name: str, measure_name: str) -> dict:
    ctx = ContextManager.get().get_active_context()
    t = _require_table(ctx, table_name)
    before = len(t.measures)
    t.measures = [m for m in t.measures if m.name != measure_name]
    if len(t.measures) == before:
        raise ValueError(f"Measure '{measure_name}' not found in table '{table_name}'.")
    ctx.model_state._dirty = True
    return {"status": "deleted", "table": table_name, "measure": measure_name}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_table(ctx, table_name: str):
    t = ctx.model_state.tables.get(table_name)
    if t is None:
        raise ValueError(
            f"Table '{table_name}' not found. "
            f"Available: {list(ctx.model_state.tables.keys())}"
        )
    return t


def _find_measure(table, measure_name: str):
    m = next((m for m in table.measures if m.name == measure_name), None)
    if m is None:
        names = [m.name for m in table.measures]
        raise ValueError(
            f"Measure '{measure_name}' not found in table '{table.name}'. "
            f"Available: {names}"
        )
    return m
