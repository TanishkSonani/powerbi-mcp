"""
PsLiveContext — LiveContext backend for Power BI Desktop's TCP-based AS.

Delegates all operations to ps_adomd_bridge (PowerShell + ADOMD.NET).
Has the same public interface as LiveContext so ContextManager and all
tool functions work without modification.
"""

from __future__ import annotations

import json

from src.context import ps_adomd_bridge as _bridge
from src.context.live_guard import FileOnlyAttrsGuard


class PsLiveContext(FileOnlyAttrsGuard):
    """Live Desktop context using the PowerShell ADOMD.NET bridge."""

    def __init__(self, port: int, model_name: str, catalog: str) -> None:
        self.port = port
        self.model_name = model_name
        self.catalog = catalog
        self.connection_string = f"localhost:{port}"
        self._model_state = None  # lazily materialised snapshot

    # ------------------------------------------------------------------
    # Live model snapshot — lets every model tool read a live connection
    # ------------------------------------------------------------------

    @property
    def model_state(self):
        """TmdlModelState built from the live engine (cached; see refresh())."""
        if self._model_state is None:
            from src.context.live_model_reader import build_live_model_state
            self._model_state = build_live_model_state(self.port, self.catalog)
        return self._model_state

    def refresh(self) -> None:
        """Drop the cached snapshot so the next read re-pulls from the engine.

        Called before every live write so we never act on a stale picture of a model
        the user may have edited in Desktop meanwhile.
        """
        self._model_state = None

    # ------------------------------------------------------------------
    # Public operations (same interface as LiveContext)
    # ------------------------------------------------------------------

    def execute_dax(self, dax_query: str, max_rows: int = 500) -> dict:
        return _bridge.execute_dax(self.port, self.catalog, dax_query, max_rows)

    def get_model_info(self) -> dict:
        info = _bridge.get_model_info(self.port, self.catalog)
        info["connection_string"] = self.connection_string
        return info

    def list_tables(self) -> list[dict]:
        """Read-only table listing straight from the live model."""
        return _bridge.list_tables(self.port, self.catalog)

    def list_measures(self) -> list[dict]:
        """Read-only measure listing straight from the live model."""
        return _bridge.list_measures(self.port, self.catalog)

    def push_measure(
        self,
        table_name: str,
        name: str,
        expression: str,
        format_string: str | None = None,
    ) -> dict:
        """Create/replace a measure in the live model.

        Routed through live_writer (granular TOM edit). The previous implementation
        used a whole-table TMSL createOrReplace that dropped the table's columns and
        rewrote `type: 'm'` partitions as calculated ones — destructive on any real
        table. That path is no longer used.
        """
        from src.context import live_writer

        result = live_writer.upsert_measure(
            self.port, self.catalog, table_name, name, expression,
            format_string=format_string,
        )
        if "error" in result:
            raise RuntimeError(result["error"])
        return {"status": "pushed", "table_name": table_name, "measure_name": name}

    def validate_expression(self, table_name: str, expression: str) -> dict:
        """Evaluate a DAX expression in the live engine.

        A result with no columns means the query never really ran (the engine rejected
        it). Treating that as success previously reported broken DAX as valid.
        """
        dax = f'EVALUATE ROW("Result", {expression})'
        try:
            result = self.execute_dax(dax, max_rows=1)
        except Exception as exc:
            return {"valid": False, "result": None, "error": str(exc)}

        if not result.get("columns"):
            return {
                "valid": False,
                "result": None,
                "error": "Expression did not evaluate (engine returned no result set).",
            }
        rows = result.get("rows") or []
        first_val = rows[0][0] if rows and rows[0] else None
        return {
            "valid": True,
            "result": str(first_val) if first_val is not None else None,
            "error": None,
        }

    def close(self) -> None:
        pass  # stateless — each PS call opens and closes its own connection
