"""
Guard mixin for live (Desktop) contexts.

The TMDL model tools (measure/table/column/relationship/role/culture/udf/calendar
tools, plus get_model_info and save_model) all reach through the active context into
`ctx.model_state`, `ctx.definition_path` or `ctx.save()`. Those attributes exist only
on FileContext, which is loaded from a PBIP folder on disk.

When a live Desktop connection was active, every one of those ~90 call sites raised a
bare `AttributeError: 'PsLiveContext' object has no attribute 'model_state'` — opaque,
and it gave the caller no idea what to do instead.

Rather than patching each call site, live contexts inherit this mixin so any access to
a file-only attribute raises one clear, actionable error explaining both options.
"""

from __future__ import annotations

FILE_ONLY_MESSAGE = (
    "This tool edits the TMDL model files on disk, but the active context is a LIVE "
    "Power BI Desktop connection.\n"
    "Either:\n"
    "  • call open_pbip_folder(<path to the PBIP/.SemanticModel folder>) to edit the "
    "saved model on disk (then save_model to persist), or\n"
    "  • use a live-capable tool instead: execute_dax, validate_measure, "
    "push_measure_live, get_desktop_model_info, list_tables, list_measures."
)


class FileOnlyAttrsGuard:
    """Turns file-only attribute access on a live context into a clear error.

    NOTE: `model_state` is deliberately NOT guarded any more. PsLiveContext now
    materialises a real TmdlModelState from the live engine (live_model_reader), so
    read tools work against a live connection. Only genuinely disk-bound concepts
    (`definition_path`, `save`) remain guarded.
    """

    @property
    def definition_path(self):
        raise RuntimeError(FILE_ONLY_MESSAGE)

    def save(self, *_args, **_kwargs):
        raise RuntimeError(FILE_ONLY_MESSAGE)
