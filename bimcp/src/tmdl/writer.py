"""
TMDL writer — serialises Python dataclasses back to TMDL text.

Formatting conventions:
  - Hard tabs for indentation (never spaces)
  - Names containing spaces are wrapped in single quotes
  - lineageTag is always the last property in a sub-object block
  - Partition and annotation blocks are preserved verbatim
"""

from __future__ import annotations

from src.tmdl.models import Column, Measure, Relationship, Role, Table


def _quote(name: str) -> str:
    """Wrap name in single quotes if it contains spaces or special chars."""
    if " " in name or name.startswith("'"):
        return f"'{name}'"
    return name


# ---------------------------------------------------------------------------
# Column
# ---------------------------------------------------------------------------

def column_to_tmdl_lines(c: Column) -> list[str]:
    lines: list[str] = [f"\tcolumn {_quote(c.name)}"]
    lines.append(f"\t\tdataType: {c.data_type}")
    if c.format_string:
        lines.append(f"\t\tformatString: {c.format_string}")
    if c.description:
        lines.append(f"\t\tdescription: {c.description}")
    if c.sort_by_column:
        lines.append(f"\t\tsortByColumn: {c.sort_by_column}")
    if c.source_column:
        lines.append(f"\t\tsourceColumn: {c.source_column}")
    if c.is_hidden:
        lines.append("\t\tisHidden")
    if c.expression:
        lines.append("\t\ttype: calculated")
        lines.append("\t\texpression =")
        for expr_line in c.expression.split("\n"):
            lines.append(f"\t\t\t{expr_line}")
    lines.append(f"\t\tlineageTag: {c.lineage_tag}")
    return lines


# ---------------------------------------------------------------------------
# Measure
# ---------------------------------------------------------------------------

def measure_to_tmdl_lines(m: Measure) -> list[str]:
    is_multiline = "\n" in m.expression

    if is_multiline:
        lines: list[str] = [f"\tmeasure {_quote(m.name)} ="]
        for expr_line in m.expression.split("\n"):
            lines.append(f"\t\t\t{expr_line}")
    else:
        lines = [f"\tmeasure {_quote(m.name)} = {m.expression}"]

    if m.format_string:
        lines.append(f"\t\tformatString: {m.format_string}")
    if m.description:
        lines.append(f"\t\tdescription: {m.description}")
    if m.display_folder:
        lines.append(f"\t\tdisplayFolder: {m.display_folder}")
    if m.is_hidden:
        lines.append("\t\tisHidden")
    lines.append(f"\t\tlineageTag: {m.lineage_tag}")
    return lines


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------

def table_to_tmdl_text(table: Table) -> str:
    parts: list[str] = []

    # Header + table-level properties
    parts.append(f"table {_quote(table.name)}")
    parts.append(f"\tlineageTag: {table.lineage_tag}")
    if table.description:
        parts.append(f"\tdescription: {table.description}")
    if table.is_hidden:
        parts.append("\tisHidden")
    parts.append("")

    # Columns
    for col in table.columns:
        parts.extend(column_to_tmdl_lines(col))
        parts.append("")

    # Measures
    for meas in table.measures:
        parts.extend(measure_to_tmdl_lines(meas))
        parts.append("")

    # Preserved partition blocks (raw)
    if table._raw_partitions:
        parts.append(table._raw_partitions)
        parts.append("")

    # Preserved annotation lines (raw)
    if table._raw_annotations:
        for ann_line in table._raw_annotations.splitlines():
            if ann_line.strip():
                parts.append(ann_line)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------

def relationships_to_tmdl_text(relationships: list[Relationship]) -> str:
    blocks: list[str] = []

    for r in relationships:
        header = "relationship"
        if r.name:
            header = f"relationship {r.name}"
        lines = [header]
        lines.append(f"\tfromTable: {r.from_table}")
        lines.append(f"\tfromColumn: {r.from_column}")
        lines.append(f"\ttoTable: {r.to_table}")
        lines.append(f"\ttoColumn: {r.to_column}")
        if r.from_cardinality != "many":
            lines.append(f"\tfromCardinality: {r.from_cardinality}")
        if r.to_cardinality != "one":
            lines.append(f"\ttoCardinality: {r.to_cardinality}")
        if not r.is_active:
            lines.append("\tisActive = false")
        if r.cross_filter_behavior:
            lines.append(f"\tcrossFilterBehavior: {r.cross_filter_behavior}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

def role_to_tmdl_text(role: Role) -> str:
    parts: list[str] = [f"role {_quote(role.name)}"]
    parts.append(f"\tmodelPermission: {role.model_permission}")

    for f in role.filters:
        parts.append("")
        parts.append(f"\ttablePermission {_quote(f.table_name)}")
        if f.filter_expression is not None:
            parts.append("\t\tfilterExpression: ```")
            for expr_line in f.filter_expression.split("\n"):
                parts.append(f"\t\t\t{expr_line}")
            parts.append("\t\t\t```")

    return "\n".join(parts)
