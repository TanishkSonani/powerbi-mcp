"""Table CRUD MCP tools."""

from src.context import live_writer
from src.context.manager import ContextManager
from src.tmdl.models import Table
from src.tools._live import live_target


def list_tables() -> dict:
    # Works in both contexts: FileContext parses TMDL from disk, PsLiveContext
    # materialises the same TmdlModelState from the live engine.
    ctx = ContextManager.get().get_active_context()
    return {
        "tables": [
            {
                "name": name,
                "columns": len(t.columns),
                "measures": len(t.measures),
                "is_hidden": t.is_hidden,
                "description": t.description,
            }
            for name, t in ctx.model_state.tables.items()
        ],
        "count": len(ctx.model_state.tables),
    }


def get_table(table_name: str) -> dict:
    ctx = ContextManager.get().get_active_context()
    t = ctx.model_state.tables.get(table_name)
    if t is None:
        raise ValueError(
            f"Table '{table_name}' not found. "
            f"Available: {list(ctx.model_state.tables.keys())}"
        )
    return {
        "name": t.name,
        "description": t.description,
        "is_hidden": t.is_hidden,
        "lineage_tag": t.lineage_tag,
        "columns": [
            {
                "name": c.name,
                "data_type": c.data_type,
                "source_column": c.source_column,
                "format_string": c.format_string,
                "is_hidden": c.is_hidden,
            }
            for c in t.columns
        ],
        "measures": [
            {
                "name": m.name,
                "expression": m.expression,
                "format_string": m.format_string,
                "display_folder": m.display_folder,
            }
            for m in t.measures
        ],
    }


def create_table(name: str, description: str | None = None) -> dict:
    lc = live_target()
    if lc is not None:
        # A live table needs a data source (partition). Creating an empty shell in a
        # running model produces an unusable table, so require the file path instead.
        return {
            "error": (
                "Creating a table in a LIVE model isn't supported: a table needs a "
                "partition (Power Query or calculated) to hold data, which must be "
                "authored in Power BI Desktop. Add the table there, or build it in the "
                "saved model with open_pbip_folder + save_model."
            )
        }
    ctx = ContextManager.get().get_active_context()
    if name in ctx.model_state.tables:
        raise ValueError(f"Table '{name}' already exists.")
    ctx.model_state.tables[name] = Table(name=name, description=description)
    ctx.model_state._dirty = True
    return {
        "status": "created",
        "table": name,
        "reminder": "Call save_model to persist changes to disk.",
    }


def update_table(
    table_name: str,
    new_name: str | None = None,
    description: str | None = None,
) -> dict:
    lc = live_target()
    if lc is not None:
        return live_writer.update_table(
            lc.port, lc.catalog, table_name,
            new_name=new_name, description=description,
        )
    ctx = ContextManager.get().get_active_context()
    t = ctx.model_state.tables.get(table_name)
    if t is None:
        raise ValueError(f"Table '{table_name}' not found.")

    if new_name and new_name != table_name:
        t.name = new_name
        del ctx.model_state.tables[table_name]
        ctx.model_state.tables[new_name] = t

    if description is not None:
        t.description = description

    ctx.model_state._dirty = True
    return {"status": "updated", "table": new_name or table_name}


def delete_table(table_name: str) -> dict:
    lc = live_target()
    if lc is not None:
        return live_writer.delete_table(lc.port, lc.catalog, table_name)
    ctx = ContextManager.get().get_active_context()
    if table_name not in ctx.model_state.tables:
        raise ValueError(
            f"Table '{table_name}' not found. "
            f"Available: {list(ctx.model_state.tables.keys())}"
        )
    del ctx.model_state.tables[table_name]
    ctx.model_state._dirty = True
    return {
        "status": "deleted",
        "table": table_name,
        "reminder": "Call save_model to persist changes to disk.",
    }
