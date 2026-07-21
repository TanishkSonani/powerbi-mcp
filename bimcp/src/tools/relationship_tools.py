"""Relationship CRUD MCP tools."""

from src.context import live_writer
from src.context.manager import ContextManager
from src.tmdl.models import Relationship
from src.tools._live import live_target


def list_relationships() -> dict:
    ctx = ContextManager.get().get_active_context()
    return {
        "relationships": [
            {
                "from": f"{r.from_table}[{r.from_column}]",
                "to": f"{r.to_table}[{r.to_column}]",
                "from_cardinality": r.from_cardinality,
                "to_cardinality": r.to_cardinality,
                "is_active": r.is_active,
            }
            for r in ctx.model_state.relationships
        ],
        "count": len(ctx.model_state.relationships),
    }


def create_relationship(
    from_table: str,
    from_column: str,
    to_table: str,
    to_column: str,
    from_cardinality: str = "many",
    to_cardinality: str = "one",
) -> dict:
    lc = live_target()
    if lc is not None:
        return live_writer.create_relationship(
            lc.port, lc.catalog, from_table, from_column, to_table, to_column,
            from_cardinality=from_cardinality, to_cardinality=to_cardinality,
        )
    ctx = ContextManager.get().get_active_context()
    # Duplicate check
    for r in ctx.model_state.relationships:
        if (r.from_table == from_table and r.from_column == from_column
                and r.to_table == to_table and r.to_column == to_column):
            raise ValueError(
                f"Relationship {from_table}[{from_column}] → "
                f"{to_table}[{to_column}] already exists."
            )
    ctx.model_state.relationships.append(Relationship(
        from_table=from_table, from_column=from_column,
        to_table=to_table, to_column=to_column,
        from_cardinality=from_cardinality, to_cardinality=to_cardinality,
    ))
    ctx.model_state._dirty = True
    return {
        "status": "created",
        "from": f"{from_table}[{from_column}]",
        "to": f"{to_table}[{to_column}]",
        "reminder": "Call save_model to persist changes to disk.",
    }


def delete_relationship(
    from_table: str,
    from_column: str,
    to_table: str,
    to_column: str,
) -> dict:
    lc = live_target()
    if lc is not None:
        return live_writer.delete_relationship(
            lc.port, lc.catalog, from_table, from_column, to_table, to_column,
        )
    ctx = ContextManager.get().get_active_context()
    before = len(ctx.model_state.relationships)
    ctx.model_state.relationships = [
        r for r in ctx.model_state.relationships
        if not (r.from_table == from_table and r.from_column == from_column
                and r.to_table == to_table and r.to_column == to_column)
    ]
    if len(ctx.model_state.relationships) == before:
        raise ValueError(
            f"Relationship {from_table}[{from_column}] → "
            f"{to_table}[{to_column}] not found."
        )
    ctx.model_state._dirty = True
    return {
        "status": "deleted",
        "from": f"{from_table}[{from_column}]",
        "to": f"{to_table}[{to_column}]",
    }
