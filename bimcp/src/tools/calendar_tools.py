"""Calendar column group CRUD tools — file-context only."""

from src.context.manager import ContextManager, FileContext
from src.tools._live import live_target, live_unsupported
from src.tmdl.models import CalendarColumnGroup

_FILE_CTX_ERROR = {
    "error": "Calendar tools require an open PBIP folder. Use open_pbip_folder first."
}

_VALID_TIME_UNITS = {
    "Year", "Quarter", "Month", "Day", "Week",
    "DayOfWeek", "DayOfYear", "WeekOfYear", "MonthOfYear",
    "Hour", "Minute", "Second",
}


def _require_file_ctx():
    """Return FileContext or an error dict."""
    try:
        ctx = ContextManager.get().get_active_context()
    except RuntimeError as exc:
        return {"error": str(exc)}
    return ctx


def list_calendars() -> dict:
    """List all calendar column groups in the open model."""
    ctx = _require_file_ctx()
    if isinstance(ctx, dict):
        return ctx

    return {
        "calendar_groups": [
            {
                "name": g.name,
                "table_name": g.table_name,
                "column_name": g.column_name,
                "time_unit": g.time_unit,
                "is_default": g.is_default,
            }
            for g in ctx.model_state.calendar_groups
        ],
        "count": len(ctx.model_state.calendar_groups),
    }


def create_calendar(
    table_name: str,
    column_name: str,
    time_unit: str,
    name: str | None = None,
    is_default: bool = False,
) -> dict:
    """
    Create a calendar column group for date hierarchies.
    
    Args:
        table_name: The table containing the date column
        column_name: The date column name
        time_unit: Time unit for the group (Year, Quarter, Month, Day, Week, etc.)
        name: Optional custom name (defaults to "{column_name}_{time_unit}")
        is_default: Whether this is the default calendar group for the column
    
    Valid time_unit values:
        Year, Quarter, Month, Day, Week, DayOfWeek, DayOfYear,
        WeekOfYear, MonthOfYear, Hour, Minute, Second
    """
    if live_target() is not None:
        return live_unsupported("Calendar column groups")
    ctx = _require_file_ctx()
    if isinstance(ctx, dict):
        return ctx

    # Validate time_unit
    if time_unit not in _VALID_TIME_UNITS:
        raise ValueError(
            f"Invalid time_unit '{time_unit}'. "
            f"Must be one of: {', '.join(sorted(_VALID_TIME_UNITS))}"
        )

    # Validate table exists
    if table_name not in ctx.model_state.tables:
        available = list(ctx.model_state.tables.keys())
        raise ValueError(
            f"Table '{table_name}' not found. Available: {available}"
        )

    # Validate column exists in table
    table = ctx.model_state.tables[table_name]
    col_names = [c.name for c in table.columns]
    if column_name not in col_names:
        raise ValueError(
            f"Column '{column_name}' not found in table '{table_name}'. "
            f"Available: {col_names}"
        )

    # Generate name if not provided
    group_name = name or f"{column_name}_{time_unit}"

    # Check for duplicate
    for g in ctx.model_state.calendar_groups:
        if g.name == group_name:
            raise ValueError(f"Calendar group '{group_name}' already exists.")
        if (
            g.table_name == table_name
            and g.column_name == column_name
            and g.time_unit == time_unit
        ):
            raise ValueError(
                f"Calendar group for {table_name}[{column_name}] with "
                f"time_unit '{time_unit}' already exists."
            )

    ctx.model_state.calendar_groups.append(CalendarColumnGroup(
        name=group_name,
        table_name=table_name,
        column_name=column_name,
        time_unit=time_unit,
        is_default=is_default,
    ))
    ctx.model_state._dirty = True

    return {
        "status": "created",
        "calendar_group": group_name,
        "table": table_name,
        "column": column_name,
        "time_unit": time_unit,
        "reminder": "Call save_model to persist changes to disk.",
    }


def update_calendar_column_group(
    group_name: str,
    new_name: str | None = None,
    time_unit: str | None = None,
    is_default: bool | None = None,
) -> dict:
    """
    Update an existing calendar column group.
    
    Args:
        group_name: Current group name
        new_name: New name (optional)
        time_unit: New time unit (optional)
        is_default: New default status (optional)
    """
    if live_target() is not None:
        return live_unsupported("Calendar column groups")
    ctx = _require_file_ctx()
    if isinstance(ctx, dict):
        return ctx

    # Find the group
    group = None
    for g in ctx.model_state.calendar_groups:
        if g.name == group_name:
            group = g
            break

    if group is None:
        available = [g.name for g in ctx.model_state.calendar_groups]
        raise ValueError(
            f"Calendar group '{group_name}' not found. Available: {available}"
        )

    # Validate time_unit if provided
    if time_unit is not None and time_unit not in _VALID_TIME_UNITS:
        raise ValueError(
            f"Invalid time_unit '{time_unit}'. "
            f"Must be one of: {', '.join(sorted(_VALID_TIME_UNITS))}"
        )

    # Apply updates
    if time_unit is not None:
        group.time_unit = time_unit
    if is_default is not None:
        group.is_default = is_default

    final_name = group_name
    if new_name and new_name != group_name:
        # Check for duplicate name
        for g in ctx.model_state.calendar_groups:
            if g.name == new_name:
                raise ValueError(f"Calendar group '{new_name}' already exists.")
        group.name = new_name
        final_name = new_name

    ctx.model_state._dirty = True

    return {
        "status": "updated",
        "calendar_group": final_name,
        "reminder": "Call save_model to persist changes to disk.",
    }


def delete_calendar(group_name: str) -> dict:
    """Delete a calendar column group."""
    if live_target() is not None:
        return live_unsupported("Calendar column groups")
    ctx = _require_file_ctx()
    if isinstance(ctx, dict):
        return ctx

    # Find and remove the group
    found = False
    for i, g in enumerate(ctx.model_state.calendar_groups):
        if g.name == group_name:
            ctx.model_state.calendar_groups.pop(i)
            found = True
            break

    if not found:
        available = [g.name for g in ctx.model_state.calendar_groups]
        raise ValueError(
            f"Calendar group '{group_name}' not found. Available: {available}"
        )

    ctx.model_state._dirty = True

    return {
        "status": "deleted",
        "calendar_group": group_name,
        "reminder": "Call save_model to persist changes to disk.",
    }
