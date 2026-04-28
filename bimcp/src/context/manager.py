"""
Context manager — holds the single active context (FileContext or LiveContext).

FileContext wraps a TmdlModelState loaded from disk.
LiveContext connects to a running Power BI Desktop via XMLA HTTP.
All tool mutations operate through whichever context is active.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from src.context.live_context import LiveContext
from src.tmdl.models import DatabaseInfo, ModelInfo, TmdlModelState
from src.tmdl.parser import (
    parse_database_file,
    parse_model_file,
    parse_relationships_file,
    parse_table_file,
)
from src.tmdl.writer import relationships_to_tmdl_text, table_to_tmdl_text


class FileContext:
    """Holds the parsed TMDL model in memory and can write it back to disk."""

    def __init__(self, definition_path: Path, model_state: TmdlModelState) -> None:
        self.definition_path = definition_path
        self.model_state = model_state

    def save(self) -> list[str]:
        """Flush all in-memory changes to the TMDL definition/ folder."""
        written: list[str] = []
        tables_dir = self.definition_path / "tables"
        tables_dir.mkdir(exist_ok=True)

        # Determine which filenames are expected after the save
        expected_files: set[Path] = set()
        for name, table in self.model_state.tables.items():
            safe = _safe_filename(name)
            path = tables_dir / f"{safe}.tmdl"
            expected_files.add(path)
            path.write_text(table_to_tmdl_text(table), encoding="utf-8")
            written.append(str(path))

        # Delete stale .tmdl files (tables that were removed)
        for existing in tables_dir.glob("*.tmdl"):
            if existing not in expected_files:
                existing.unlink()
                written.append(f"[deleted] {existing}")

        # Write or clear relationships.tmdl
        rel_path = self.definition_path / "relationships.tmdl"
        if self.model_state.relationships:
            rel_path.write_text(
                relationships_to_tmdl_text(self.model_state.relationships),
                encoding="utf-8",
            )
            written.append(str(rel_path))
        elif rel_path.exists():
            rel_path.unlink()
            written.append(f"[deleted] {rel_path}")

        self.model_state._dirty = False
        return written


def _safe_filename(table_name: str) -> str:
    """Convert a table name to a safe filename (replace spaces with underscores)."""
    return table_name.replace(" ", "_").replace("/", "_").replace("\\", "_")


# ---------------------------------------------------------------------------
# Singleton ContextManager
# ---------------------------------------------------------------------------

class ContextManager:
    _instance: Optional[ContextManager] = None
    _active: Optional[Union[FileContext, LiveContext]] = None

    @classmethod
    def get(cls) -> ContextManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def open_file_context(self, definition_path: Path) -> FileContext:
        self.close_context()
        model_state = _load_tmdl_model(definition_path)
        self._active = FileContext(definition_path, model_state)
        return self._active

    def open_live_context(self, port: int, model_name: str) -> LiveContext:
        self.close_context()
        self._active = LiveContext(port, model_name)
        return self._active

    def get_active_context(self) -> Union[FileContext, LiveContext]:
        if self._active is None:
            raise RuntimeError(
                "No model is currently open. "
                "Use the 'open_pbip_folder' or 'connect_desktop' tool first."
            )
        return self._active

    @property
    def context_type(self) -> str | None:
        if self._active is None:
            return None
        if isinstance(self._active, LiveContext):
            return "live"
        return "file"

    def close_context(self) -> None:
        if self._active is not None:
            self._active.close()
        self._active = None


# ---------------------------------------------------------------------------
# Internal loader
# ---------------------------------------------------------------------------

def _load_tmdl_model(definition_path: Path) -> TmdlModelState:
    db_path = definition_path / "database.tmdl"
    database = (
        parse_database_file(db_path)
        if db_path.exists()
        else DatabaseInfo(name="Model")
    )

    model_path = definition_path / "model.tmdl"
    model_info = (
        parse_model_file(model_path) if model_path.exists() else ModelInfo()
    )

    tables: dict = {}
    tables_dir = definition_path / "tables"
    if tables_dir.exists():
        for tmdl_file in sorted(tables_dir.glob("*.tmdl")):
            table = parse_table_file(tmdl_file)
            if table.name:
                tables[table.name] = table

    rel_path = definition_path / "relationships.tmdl"
    relationships = (
        parse_relationships_file(rel_path) if rel_path.exists() else []
    )

    return TmdlModelState(
        definition_path=definition_path,
        database=database,
        model_info=model_info,
        tables=tables,
        relationships=relationships,
    )
