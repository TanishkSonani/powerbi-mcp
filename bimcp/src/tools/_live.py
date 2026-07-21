"""
Shared helper for routing mutating tools to the live model.

Read tools need nothing special: PsLiveContext.model_state materialises the live model
into the same dataclasses the file model uses. Mutations are different — they must go
through live_writer (granular TOM edits), never through the file-mode code path.
"""

from __future__ import annotations

from src.context.manager import ContextManager


def live_target():
    """
    Return the active live context, or None when the context is file-based.

    Always drops the cached snapshot first: a write must never be planned against a
    stale picture of a model the user may have changed in Desktop since we last read it.
    """
    cm = ContextManager.get()
    if cm.context_type != "live":
        return None
    ctx = cm.get_active_context()
    refresh = getattr(ctx, "refresh", None)
    if callable(refresh):
        refresh()
    return ctx


def live_unsupported(what: str) -> dict:
    """Uniform refusal for object types with no safe live-edit path.

    Some TMDL constructs (UDFs, calendar column groups) have no stable TOM surface in
    Power BI Desktop's embedded engine. Refusing clearly beats attempting a partial
    write that could corrupt the model.
    """
    return {
        "error": (
            f"{what} cannot be modified in a LIVE model. Edit the saved model instead: "
            "open_pbip_folder(<path>) → make the change → save_model(). "
            "Reading them live is supported."
        )
    }
