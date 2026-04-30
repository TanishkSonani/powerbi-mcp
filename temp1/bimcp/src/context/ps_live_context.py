"""
PsLiveContext — LiveContext backend for Power BI Desktop's TCP-based AS.

Delegates all operations to ps_adomd_bridge (PowerShell + ADOMD.NET).
Has the same public interface as LiveContext so ContextManager and all
tool functions work without modification.
"""

from __future__ import annotations

import json

from src.context import ps_adomd_bridge as _bridge


class PsLiveContext:
    """Live Desktop context using the PowerShell ADOMD.NET bridge."""

    def __init__(self, port: int, model_name: str, catalog: str) -> None:
        self.port = port
        self.model_name = model_name
        self.catalog = catalog
        self.connection_string = f"localhost:{port}"

    # ------------------------------------------------------------------
    # Public operations (same interface as LiveContext)
    # ------------------------------------------------------------------

    def execute_dax(self, dax_query: str, max_rows: int = 500) -> dict:
        return _bridge.execute_dax(self.port, self.catalog, dax_query, max_rows)

    def get_model_info(self) -> dict:
        info = _bridge.get_model_info(self.port, self.catalog)
        info["connection_string"] = self.connection_string
        return info

    def push_measure(
        self,
        table_name: str,
        name: str,
        expression: str,
        format_string: str | None = None,
    ) -> dict:
        _bridge.push_measure(
            self.port, self.catalog, table_name, name, expression, format_string
        )
        return {"status": "pushed", "table_name": table_name, "measure_name": name}

    def validate_expression(self, table_name: str, expression: str) -> dict:
        dax = f'EVALUATE ROW("Result", {expression})'
        try:
            result = self.execute_dax(dax, max_rows=1)
            first_val = result["rows"][0][0] if result["rows"] else None
            return {
                "valid": True,
                "result": str(first_val) if first_val is not None else None,
                "error": None,
            }
        except (RuntimeError, Exception) as exc:
            return {"valid": False, "result": None, "error": str(exc)}

    def close(self) -> None:
        pass  # stateless — each PS call opens and closes its own connection
