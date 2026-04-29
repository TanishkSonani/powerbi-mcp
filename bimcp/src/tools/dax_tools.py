"""
Phase 3 — Live-only DAX execution and measure mutation tools.

These three tools require a LiveContext (connect_desktop first).
When called with a FileContext they return a clear error instead of crashing.
"""

from __future__ import annotations

from src.context.live_context import LiveContext
from src.context.manager import ContextManager


def _require_live() -> LiveContext | dict:
    """Return the active LiveContext, or an error dict if not live."""
    cm = ContextManager.get()
    if cm.context_type != "live":
        return {
            "error": (
                "DAX execution requires a live Desktop connection. "
                "Use connect_desktop first."
            )
        }
    return cm.get_active_context()  # type: ignore[return-value]


def execute_dax(dax_query: str, max_rows: int = 500) -> dict:
    """
    Execute a DAX query against the live Desktop model.
    Returns columns, rows, row count, truncation flag, and a markdown table.
    """
    ctx = _require_live()
    if isinstance(ctx, dict):
        return ctx
    try:
        result = ctx.execute_dax(dax_query, max_rows)
        return {"status": "ok", **result}
    except ConnectionError as exc:
        return {"error": str(exc)}
    except ValueError as exc:
        return {"error": f"DAX error: {exc}"}


def validate_measure(table_name: str, expression: str) -> dict:
    """
    Validate a DAX expression by evaluating it in the live model.
    Returns {valid: bool, result, error}.
    """
    ctx = _require_live()
    if isinstance(ctx, dict):
        return ctx
    try:
        return ctx.validate_expression(table_name, expression)
    except ConnectionError as exc:
        return {"error": str(exc)}


def push_measure_live(
    table_name: str,
    name: str,
    expression: str,
    format_string: str | None = None,
) -> dict:
    """
    Create or replace a measure in the live Desktop model without touching disk.
    Changes are visible immediately in Power BI Desktop.
    """
    ctx = _require_live()
    if isinstance(ctx, dict):
        return ctx
    try:
        return ctx.push_measure(table_name, name, expression, format_string)
    except (ValueError, ConnectionError) as exc:
        return {"error": str(exc)}
