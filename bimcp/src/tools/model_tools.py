"""Model-level MCP tools: open_pbip_folder, get_model_info, save_model."""

from src.context.manager import ContextManager
from src.tmdl.path_resolver import resolve_tmdl_definition_path


def open_pbip_folder(path: str) -> dict:
    def_path = resolve_tmdl_definition_path(path)
    ctx = ContextManager.get().open_file_context(def_path)
    m = ctx.model_state
    total_measures = sum(len(t.measures) for t in m.tables.values())
    total_columns = sum(len(t.columns) for t in m.tables.values())
    return {
        "status": "connected",
        "model": m.database.name,
        "definition_path": str(def_path),
        "compatibility_level": m.database.compatibility_level,
        "culture": m.model_info.culture,
        "tables": len(m.tables),
        "total_columns": total_columns,
        "total_measures": total_measures,
        "relationships": len(m.relationships),
    }


def get_model_info() -> dict:
    ctx = ContextManager.get().get_active_context()
    m = ctx.model_state
    return {
        "name": m.database.name,
        "compatibility_level": m.database.compatibility_level,
        "culture": m.model_info.culture,
        "definition_path": str(ctx.definition_path),
        "tables": len(m.tables),
        "total_measures": sum(len(t.measures) for t in m.tables.values()),
        "total_columns": sum(len(t.columns) for t in m.tables.values()),
        "relationships": len(m.relationships),
        "has_unsaved_changes": m._dirty,
    }


def save_model() -> dict:
    ctx = ContextManager.get().get_active_context()
    written = ctx.save()
    return {
        "status": "saved",
        "files_written": len(written),
        "files": written,
    }
