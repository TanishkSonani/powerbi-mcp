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
    """Model summary. Works in both contexts (live models have no definition_path)."""
    cm = ContextManager.get()
    ctx = cm.get_active_context()
    m = ctx.model_state
    is_live = cm.context_type == "live"
    info = {
        "name": m.database.name,
        "compatibility_level": m.database.compatibility_level,
        "culture": m.model_info.culture,
        "tables": len(m.tables),
        "total_measures": sum(len(t.measures) for t in m.tables.values()),
        "total_columns": sum(len(t.columns) for t in m.tables.values()),
        "relationships": len(m.relationships),
        "source": "live" if is_live else "file",
    }
    if is_live:
        info["connection_string"] = getattr(ctx, "connection_string", None)
        # Live edits apply immediately, so there is never a pending-save state.
        info["has_unsaved_changes"] = False
    else:
        info["definition_path"] = str(ctx.definition_path)
        info["has_unsaved_changes"] = m._dirty
    return info


def save_model() -> dict:
    """Persist file-mode edits. In live mode there is nothing to flush."""
    cm = ContextManager.get()
    ctx = cm.get_active_context()
    if cm.context_type == "live":
        return {
            "status": "no-op",
            "detail": (
                "Connected to a live model — edits are applied immediately, so there "
                "is nothing to save. save_model applies to file mode "
                "(open_pbip_folder), where changes are held in memory until flushed."
            ),
        }
    written = ctx.save()
    return {
        "status": "saved",
        "files_written": len(written),
        "files": written,
    }
