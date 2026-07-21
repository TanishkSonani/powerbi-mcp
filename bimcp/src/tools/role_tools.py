"""RLS role CRUD tools — file-context only."""

from src.context import live_writer
from src.context.manager import ContextManager, FileContext
from src.tmdl.models import Role, RlsFilter
from src.tools._live import live_target


def _require_file_ctx():
    """Return the active context, or an error dict when nothing is open.

    Previously this refused any live connection outright. Reads now work in both
    contexts (PsLiveContext materialises the same model_state), and the mutating
    tools branch to live_writer before reaching here.
    """
    try:
        return ContextManager.get().get_active_context()
    except RuntimeError as exc:
        return {"error": str(exc)}


def list_roles() -> dict:
    ctx = _require_file_ctx()
    if isinstance(ctx, dict):
        return ctx
    return {
        "roles": [
            {
                "name": r.name,
                "model_permission": r.model_permission,
                "filter_count": len(r.filters),
            }
            for r in ctx.model_state.roles.values()
        ],
        "count": len(ctx.model_state.roles),
    }


def create_role(name: str, model_permission: str = "Read") -> dict:
    lc = live_target()
    if lc is not None:
        return live_writer.upsert_role(lc.port, lc.catalog, name, model_permission)
    ctx = _require_file_ctx()
    if isinstance(ctx, dict):
        return ctx
    if name in ctx.model_state.roles:
        raise ValueError(f"Role '{name}' already exists.")
    ctx.model_state.roles[name] = Role(name=name, model_permission=model_permission)
    ctx.model_state._dirty = True
    return {
        "status": "created",
        "role": name,
        "reminder": "Call save_model to persist changes to disk.",
    }


def update_role(
    role_name: str,
    new_name: str | None = None,
    model_permission: str | None = None,
) -> dict:
    lc = live_target()
    if lc is not None:
        if new_name and new_name != role_name:
            return {"error": "Renaming a role in a live model isn't supported; create the new role and delete the old one."}
        return live_writer.upsert_role(
            lc.port, lc.catalog, role_name, model_permission or "Read",
        )
    ctx = _require_file_ctx()
    if isinstance(ctx, dict):
        return ctx
    role = _require_role(ctx, role_name)
    if model_permission is not None:
        role.model_permission = model_permission
    if new_name and new_name != role_name:
        if new_name in ctx.model_state.roles:
            raise ValueError(f"Role '{new_name}' already exists.")
        del ctx.model_state.roles[role_name]
        role.name = new_name
        ctx.model_state.roles[new_name] = role
    ctx.model_state._dirty = True
    return {
        "status": "updated",
        "role": new_name or role_name,
    }


def add_rls_filter(
    role_name: str,
    table_name: str,
    filter_expression: str | None = None,
) -> dict:
    lc = live_target()
    if lc is not None:
        return live_writer.upsert_rls_filter(
            lc.port, lc.catalog, role_name, table_name, filter_expression,
        )
    ctx = _require_file_ctx()
    if isinstance(ctx, dict):
        return ctx
    role = _require_role(ctx, role_name)
    # Replace existing filter for same table, or append
    role.filters = [f for f in role.filters if f.table_name != table_name]
    role.filters.append(RlsFilter(table_name=table_name, filter_expression=filter_expression))
    ctx.model_state._dirty = True
    return {
        "status": "set",
        "role": role_name,
        "table": table_name,
        "reminder": "Call save_model to persist changes to disk.",
    }


def delete_rls_filter(role_name: str, table_name: str) -> dict:
    lc = live_target()
    if lc is not None:
        return live_writer.delete_rls_filter(lc.port, lc.catalog, role_name, table_name)
    ctx = _require_file_ctx()
    if isinstance(ctx, dict):
        return ctx
    role = _require_role(ctx, role_name)
    before = len(role.filters)
    role.filters = [f for f in role.filters if f.table_name != table_name]
    if len(role.filters) == before:
        raise ValueError(
            f"No filter for table '{table_name}' found in role '{role_name}'."
        )
    ctx.model_state._dirty = True
    return {"status": "deleted", "role": role_name, "table": table_name}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_role(ctx: FileContext, role_name: str) -> Role:
    role = ctx.model_state.roles.get(role_name)
    if role is None:
        available = list(ctx.model_state.roles.keys())
        raise ValueError(
            f"Role '{role_name}' not found. Available: {available}"
        )
    return role
