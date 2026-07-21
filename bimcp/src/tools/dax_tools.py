"""
Phase 3 — Live-only DAX execution and measure mutation tools.

These three tools require a LiveContext (connect_desktop first).
When called with a FileContext they return a clear error instead of crashing.
"""

from __future__ import annotations

import re

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
    Validate a DAX expression.

    Live context  → evaluates the expression in the engine (authoritative).
    File context  → static check only. A TMDL folder has no query engine, so the
                    expression cannot be evaluated; we verify balanced delimiters and
                    that every 'Table'[Column] reference resolves against the model.
    """
    cm = ContextManager.get()
    if cm.context_type == "live":
        ctx = cm.get_active_context()
        try:
            return ctx.validate_expression(table_name, expression)
        except ConnectionError as exc:
            return {"error": str(exc)}
    try:
        ctx = cm.get_active_context()
    except RuntimeError as exc:
        return {"error": str(exc)}
    return _static_validate(ctx, expression)


def _static_validate(ctx, expression: str) -> dict:
    """Syntax + reference check for DAX without an engine (file mode)."""
    problems: list[str] = []

    # Balanced delimiters, ignoring anything inside string literals.
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    in_str = False
    i = 0
    while i < len(expression):
        ch = expression[i]
        if in_str:
            if ch == '"':
                if i + 1 < len(expression) and expression[i + 1] == '"':
                    i += 1          # escaped quote inside a literal
                else:
                    in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "([{":
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack[-1] != pairs[ch]:
                problems.append(f"unbalanced '{ch}' at position {i}")
                break
            stack.pop()
        i += 1
    if in_str:
        problems.append("unterminated string literal")
    if stack:
        problems.append(f"unclosed '{stack[-1]}'")

    # Every 'Table'[Column] reference must resolve.
    try:
        tables = ctx.model_state.tables
        for tname, cname in re.findall(r"'([^']+)'\[([^\]]+)\]", expression):
            t = tables.get(tname)
            if t is None:
                problems.append(f"unknown table '{tname}'")
            elif not any(c.name.lower() == cname.lower() for c in t.columns) and \
                    not any(m.name.lower() == cname.lower() for m in t.measures):
                # DAX identifiers are case-insensitive, so compare case-insensitively.
                problems.append(f"'{tname}' has no column or measure '{cname}'")
    except Exception as exc:  # model unavailable — keep the syntax result
        problems.append(f"could not check references: {exc}")

    return {
        "valid": not problems,
        "checked": "static",
        "note": (
            "File mode has no query engine: this verifies syntax and that references "
            "resolve, but does NOT evaluate the expression. Connect to a live model "
            "(connect_desktop) for a real evaluation."
        ),
        "error": "; ".join(problems) if problems else None,
    }


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
