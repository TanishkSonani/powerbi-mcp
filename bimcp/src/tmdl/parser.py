"""
TMDL parser — pure Python, no external dependencies.

Parses .tmdl files into the Python dataclasses defined in models.py.
Handles:
  - database.tmdl  → DatabaseInfo
  - model.tmdl     → ModelInfo
  - tables/*.tmdl  → Table  (with Column, Measure, raw partition/annotation blocks)
  - relationships.tmdl → list[Relationship]

Indentation rules (TMDL uses hard tabs):
  indent 0  → top-level object declaration (table, database, model, relationship)
  indent 1  → table-level sub-objects (column, measure, partition, annotation)
              and table-level scalar properties (lineageTag, isHidden, description)
  indent 2  → sub-object properties (dataType, lineageTag, formatString, ...)
  indent 3+ → expression content for multi-line DAX or M code
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from src.tmdl.models import (
    CalendarColumnGroup, Column, Culture, DatabaseInfo, Measure, ModelInfo,
    Relationship, Role, RlsFilter, Table, Translation, UDF,
)

_MEASURE_RE = re.compile(r"^measure\s+('(?:[^'\\]|\\.)*'|\S+)\s*=\s*(.*)$")
_COLUMN_RE  = re.compile(r"^column\s+('(?:[^'\\]|\\.)*'|\S+)$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip("\t"))


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if s.startswith("'") and s.endswith("'"):
        return s[1:-1]
    return s


# ---------------------------------------------------------------------------
# database.tmdl
# ---------------------------------------------------------------------------

def parse_database_file(path: Path) -> DatabaseInfo:
    name = "Model"
    compat = 1605
    lineage = str(uuid4())

    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.rstrip()
        if line.startswith("database "):
            name = line[9:].strip()
        s = line.lstrip("\t")
        if s.startswith("compatibilityLevel:"):
            try:
                compat = int(s[len("compatibilityLevel:"):].strip())
            except ValueError:
                pass
        elif s.startswith("lineageTag:"):
            lineage = s[len("lineageTag:"):].strip()

    return DatabaseInfo(name=name, compatibility_level=compat, lineage_tag=lineage)


# ---------------------------------------------------------------------------
# model.tmdl
# ---------------------------------------------------------------------------

def parse_model_file(path: Path) -> ModelInfo:
    lineage = str(uuid4())
    culture = "en-US"

    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        s = raw.lstrip("\t").rstrip()
        if s.startswith("lineageTag:"):
            lineage = s[len("lineageTag:"):].strip()
        elif s.startswith("culture:"):
            culture = s[len("culture:"):].strip()

    return ModelInfo(lineage_tag=lineage, culture=culture)


# ---------------------------------------------------------------------------
# tables/*.tmdl
# ---------------------------------------------------------------------------

def parse_table_file(path: Path) -> Table:
    lines = path.read_text(encoding="utf-8-sig").splitlines()

    table_name = path.stem.replace("_", " ")  # fallback; overridden by header
    lineage_tag = str(uuid4())
    description: str | None = None
    is_hidden = False
    columns: list[Column] = []
    measures: list[Measure] = []
    raw_partition_parts: list[str] = []
    raw_annotation_parts: list[str] = []

    i = 0
    n = len(lines)

    # --- table header ---
    if n > 0 and lines[0].startswith("table "):
        table_name = _strip_quotes(lines[0][6:])
        i = 1

    # --- table body ---
    while i < n:
        line = lines[i]
        ind = _indent(line)
        s = line.lstrip("\t").rstrip()

        if not s or s.startswith("//"):
            i += 1
            continue

        if ind == 1:
            if s.startswith("lineageTag:"):
                lineage_tag = s[len("lineageTag:"):].strip()
                i += 1
            elif s == "isHidden":
                is_hidden = True
                i += 1
            elif s.startswith("description:"):
                description = s[len("description:"):].strip()
                i += 1
            elif _COLUMN_RE.match(s):
                col, i = _parse_column(lines, i, n)
                columns.append(col)
            elif s.startswith("measure "):
                meas, i = _parse_measure(lines, i, n)
                measures.append(meas)
            elif s.startswith("partition ") or s == "partition":
                raw, i = _collect_raw_block(lines, i, n, stop_indent=1)
                raw_partition_parts.append(raw)
            elif s.startswith("annotation "):
                raw_annotation_parts.append(line)
                i += 1
            else:
                i += 1
        else:
            i += 1

    return Table(
        name=table_name,
        lineage_tag=lineage_tag,
        description=description,
        is_hidden=is_hidden,
        columns=columns,
        measures=measures,
        _raw_partitions="\n".join(raw_partition_parts),
        _raw_annotations="\n".join(raw_annotation_parts),
    )


def _parse_column(lines: list[str], i: int, n: int) -> tuple[Column, int]:
    s = lines[i].lstrip("\t").rstrip()
    m = _COLUMN_RE.match(s)
    col_name = _strip_quotes(m.group(1)) if m else s[7:]

    data_type = "string"
    source_col: str | None = None
    expression: str | None = None
    fmt: str | None = None
    desc: str | None = None
    lineage = str(uuid4())
    hidden = False
    sort_by: str | None = None

    i += 1
    expr_lines: list[str] = []
    in_expression = False

    while i < n:
        line = lines[i]
        ind = _indent(line)
        s2 = line.lstrip("\t").rstrip()

        if not s2:
            i += 1
            continue
        if ind <= 1:          # end of column block
            break

        if in_expression:
            if ind >= 3:
                expr_lines.append(s2)
                i += 1
                continue
            else:
                in_expression = False
                expression = "\n".join(expr_lines)

        if s2.startswith("dataType:"):
            data_type = s2[len("dataType:"):].strip()
        elif s2.startswith("lineageTag:"):
            lineage = s2[len("lineageTag:"):].strip()
        elif s2.startswith("sourceColumn:"):
            source_col = s2[len("sourceColumn:"):].strip()
        elif s2.startswith("formatString:"):
            fmt = s2[len("formatString:"):].strip()
        elif s2.startswith("description:"):
            desc = s2[len("description:"):].strip()
        elif s2.startswith("sortByColumn:"):
            sort_by = s2[len("sortByColumn:"):].strip()
        elif s2 == "isHidden":
            hidden = True
        elif s2 == "expression" or s2.startswith("expression ="):
            in_expression = True
        i += 1

    if in_expression and expr_lines:
        expression = "\n".join(expr_lines)

    return Column(
        name=col_name, data_type=data_type, source_column=source_col,
        expression=expression, format_string=fmt, description=desc,
        lineage_tag=lineage, is_hidden=hidden, sort_by_column=sort_by,
    ), i


def _parse_measure(lines: list[str], i: int, n: int) -> tuple[Measure, int]:
    raw_header = lines[i].lstrip("\t").rstrip()
    m = _MEASURE_RE.match(raw_header)
    if not m:
        return Measure(name="Unknown", expression=""), i + 1

    meas_name = _strip_quotes(m.group(1))
    inline_expr = (m.group(2) or "").strip()

    fmt: str | None = None
    desc: str | None = None
    folder: str | None = None
    lineage = str(uuid4())
    hidden = False
    expr_lines: list[str] = []

    i += 1

    # --- multi-line expression: lines at indent 3 follow immediately ---
    if not inline_expr:
        while i < n:
            line = lines[i]
            ind = _indent(line)
            s = line.lstrip("\t").rstrip()
            if not s:
                i += 1
                continue
            if ind <= 2:
                break          # end of expression, now at properties
            expr_lines.append(s)
            i += 1

    # --- measure properties at indent 2 ---
    while i < n:
        line = lines[i]
        ind = _indent(line)
        s = line.lstrip("\t").rstrip()
        if not s:
            i += 1
            continue
        if ind <= 1:
            break

        if s.startswith("formatString:"):
            fmt = s[len("formatString:"):].strip()
        elif s.startswith("lineageTag:"):
            lineage = s[len("lineageTag:"):].strip()
        elif s.startswith("description:"):
            desc = s[len("description:"):].strip()
        elif s.startswith("displayFolder:"):
            folder = s[len("displayFolder:"):].strip()
        elif s == "isHidden":
            hidden = True
        i += 1

    expression = "\n".join(expr_lines) if expr_lines else inline_expr

    return Measure(
        name=meas_name, expression=expression, format_string=fmt,
        description=desc, display_folder=folder, lineage_tag=lineage,
        is_hidden=hidden,
    ), i


def _collect_raw_block(lines: list[str], i: int, n: int, stop_indent: int) -> tuple[str, int]:
    """Collect lines from i until the next line at indent ≤ stop_indent."""
    collected = [lines[i]]
    i += 1
    while i < n:
        line = lines[i]
        s = line.lstrip("\t").rstrip()
        ind = _indent(line)
        if s and ind <= stop_indent:
            break
        collected.append(line)
        i += 1
    return "\n".join(collected), i


# ---------------------------------------------------------------------------
# relationships.tmdl
# ---------------------------------------------------------------------------

def parse_relationships_file(path: Path) -> list[Relationship]:
    if not path.exists():
        return []

    lines = path.read_text(encoding="utf-8-sig").splitlines()
    results: list[Relationship] = []
    current: dict | None = None

    def _flush():
        if current and current.get("fromTable"):
            results.append(Relationship(
                from_table=current.get("fromTable", ""),
                from_column=current.get("fromColumn", ""),
                to_table=current.get("toTable", ""),
                to_column=current.get("toColumn", ""),
                from_cardinality=current.get("fromCardinality", "many"),
                to_cardinality=current.get("toCardinality", "one"),
                is_active=current.get("isActive", True),
                cross_filter_behavior=current.get("crossFilterBehavior"),
                name=current.get("name"),
            ))

    for raw in lines:
        line = raw.rstrip()
        ind = _indent(line)
        s = line.lstrip("\t")

        if not s or s.startswith("//"):
            continue

        if ind == 0:
            if s == "relationship" or s.startswith("relationship "):
                _flush()
                parts = s.split(None, 1)
                current = {"name": parts[1] if len(parts) > 1 else None, "isActive": True}
            else:
                _flush()
                current = None
        elif ind == 1 and current is not None:
            if ":" in s:
                key, _, val = s.partition(":")
                key, val = key.strip(), val.strip()
                mapping = {
                    "fromTable": "fromTable", "fromColumn": "fromColumn",
                    "toTable": "toTable", "toColumn": "toColumn",
                    "fromCardinality": "fromCardinality", "toCardinality": "toCardinality",
                    "crossFilterBehavior": "crossFilterBehavior",
                }
                if key in mapping:
                    current[mapping[key]] = val
            elif s.strip() == "isActive = false":
                current["isActive"] = False

    _flush()
    return results


# ---------------------------------------------------------------------------
# roles/*.tmdl
# ---------------------------------------------------------------------------

def _collect_filter_expression(lines: list[str], i: int, n: int) -> tuple[str | None, int]:
    """
    Parse the optional filterExpression inside a tablePermission block.
    Called with i pointing at the line AFTER the 'tablePermission' header.
    Returns (expression_or_None, next_i).
    """
    expr_lines: list[str] = []
    in_backtick_block = False

    while i < n:
        line = lines[i]
        ind = _indent(line)
        s = line.lstrip("\t").rstrip()

        if not s:
            i += 1
            continue

        # End of tablePermission block — back at indent ≤ 1
        if ind <= 1:
            break

        # Indent 2: look for filterExpression keyword
        if ind == 2 and s.startswith("filterExpression:"):
            after = s[len("filterExpression:"):].strip()
            if after == "```":
                # Multi-line backtick block: collect indent-3 lines until closing ```
                in_backtick_block = True
                i += 1
                while i < n:
                    bl = lines[i]
                    bs = bl.lstrip("\t").rstrip()
                    if bs == "```":
                        i += 1
                        break
                    if _indent(bl) <= 1 and bs:
                        break
                    if bs:
                        expr_lines.append(bs)
                    i += 1
                return "\n".join(expr_lines) if expr_lines else None, i
            elif after:
                # Inline expression
                i += 1
                return after, i
            else:
                i += 1
                continue
        i += 1

    return None, i


def parse_role_file(path: Path) -> Role:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    n = len(lines)

    role_name = path.stem.replace("_", " ")
    model_permission = "Read"
    filters: list[RlsFilter] = []

    i = 0
    if n > 0 and lines[0].startswith("role "):
        role_name = _strip_quotes(lines[0][5:].strip())
        i = 1

    while i < n:
        line = lines[i]
        ind = _indent(line)
        s = line.lstrip("\t").rstrip()

        if not s or s.startswith("//"):
            i += 1
            continue

        if ind == 1:
            if s.startswith("modelPermission:"):
                model_permission = s[len("modelPermission:"):].strip()
                i += 1
            elif s.startswith("tablePermission "):
                table_name = _strip_quotes(s[len("tablePermission "):].strip())
                i += 1
                expr, i = _collect_filter_expression(lines, i, n)
                filters.append(RlsFilter(table_name=table_name, filter_expression=expr))
            else:
                i += 1
        else:
            i += 1

    return Role(name=role_name, model_permission=model_permission, filters=filters)


def parse_roles_folder(roles_dir: Path) -> dict[str, Role]:
    if not roles_dir.exists():
        return {}
    roles: dict[str, Role] = {}
    for tmdl_file in sorted(roles_dir.glob("*.tmdl")):
        role = parse_role_file(tmdl_file)
        if role.name:
            roles[role.name] = role
    return roles


# ---------------------------------------------------------------------------
# cultures/*.tmdl — Translations
# ---------------------------------------------------------------------------

def parse_culture_file(path: Path) -> Culture:
    """Parse a culture TMDL file (e.g., cultures/fr-FR.tmdl)."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    n = len(lines)

    # Culture name from filename (e.g., fr-FR.tmdl -> fr-FR)
    culture_name = path.stem
    translations: list[Translation] = []

    i = 0
    # Check for culture header
    if n > 0 and lines[0].startswith("culture "):
        culture_name = _strip_quotes(lines[0][8:].strip())
        i = 1

    current_object_type: str | None = None
    current_object_name: str | None = None
    current_table: str | None = None

    while i < n:
        line = lines[i]
        ind = _indent(line)
        s = line.lstrip("\t").rstrip()

        if not s or s.startswith("//"):
            i += 1
            continue

        # Indent 1: linguisticMetadata or table/measure/column translation blocks
        if ind == 1:
            if s.startswith("linguisticMetadata"):
                # Skip linguistic metadata blocks
                i += 1
                while i < n and _indent(lines[i]) > 1:
                    i += 1
                continue
            elif s.startswith("table "):
                current_object_type = "Table"
                current_object_name = _strip_quotes(s[6:].strip())
                current_table = current_object_name
                i += 1
            elif s.startswith("measure "):
                current_object_type = "Measure"
                current_object_name = _strip_quotes(s[8:].strip())
                i += 1
            elif s.startswith("column "):
                current_object_type = "Column"
                current_object_name = _strip_quotes(s[7:].strip())
                i += 1
            else:
                i += 1
        elif ind == 2 and current_object_type and current_object_name:
            # Translation properties: caption, description, displayFolder
            if s.startswith("caption:"):
                value = s[8:].strip()
                translations.append(Translation(
                    object_type=current_object_type,
                    object_name=current_object_name,
                    property_name="Caption",
                    translated_value=value,
                    table_name=current_table if current_object_type != "Table" else None,
                ))
            elif s.startswith("description:"):
                value = s[12:].strip()
                translations.append(Translation(
                    object_type=current_object_type,
                    object_name=current_object_name,
                    property_name="Description",
                    translated_value=value,
                    table_name=current_table if current_object_type != "Table" else None,
                ))
            elif s.startswith("displayFolder:"):
                value = s[14:].strip()
                translations.append(Translation(
                    object_type=current_object_type,
                    object_name=current_object_name,
                    property_name="DisplayFolder",
                    translated_value=value,
                    table_name=current_table if current_object_type != "Table" else None,
                ))
            i += 1
        else:
            i += 1

    return Culture(name=culture_name, translations=translations)


def parse_cultures_folder(cultures_dir: Path) -> dict[str, Culture]:
    """Parse all culture files in the cultures/ folder."""
    if not cultures_dir.exists():
        return {}
    cultures: dict[str, Culture] = {}
    for tmdl_file in sorted(cultures_dir.glob("*.tmdl")):
        culture = parse_culture_file(tmdl_file)
        if culture.name:
            cultures[culture.name] = culture
    return cultures


# ---------------------------------------------------------------------------
# expressions.tmdl — User-defined functions (UDFs)
# ---------------------------------------------------------------------------

def parse_expressions_file(path: Path) -> dict[str, UDF]:
    """Parse expressions.tmdl for user-defined functions."""
    if not path.exists():
        return {}

    lines = path.read_text(encoding="utf-8-sig").splitlines()
    n = len(lines)
    udfs: dict[str, UDF] = {}

    i = 0
    while i < n:
        line = lines[i]
        ind = _indent(line)
        s = line.lstrip("\t").rstrip()

        if not s or s.startswith("//"):
            i += 1
            continue

        # Look for expression headers at indent 0
        if ind == 0 and s.startswith("expression "):
            udf_name = _strip_quotes(s[11:].strip().rstrip("=").strip())
            expression_lines: list[str] = []
            return_type = "variant"
            description: str | None = None
            parameters: list[dict] = []
            lineage_tag = str(uuid4())

            i += 1
            # Collect expression body and properties
            while i < n:
                inner_line = lines[i]
                inner_ind = _indent(inner_line)
                inner_s = inner_line.lstrip("\t").rstrip()

                if not inner_s:
                    i += 1
                    continue
                if inner_ind == 0:
                    break  # Next top-level item

                if inner_ind == 1:
                    if inner_s.startswith("returnType:"):
                        return_type = inner_s[11:].strip()
                    elif inner_s.startswith("lineageTag:"):
                        lineage_tag = inner_s[11:].strip()
                    elif inner_s.startswith("description:"):
                        description = inner_s[12:].strip()
                    elif inner_s.startswith("parameter "):
                        # Parse parameter definition
                        param_name = _strip_quotes(inner_s[10:].strip())
                        param_type = "variant"
                        param_desc: str | None = None
                        i += 1
                        while i < n and _indent(lines[i]) >= 2:
                            param_line = lines[i].lstrip("\t").rstrip()
                            if param_line.startswith("type:"):
                                param_type = param_line[5:].strip()
                            elif param_line.startswith("description:"):
                                param_desc = param_line[12:].strip()
                            i += 1
                        parameters.append({
                            "name": param_name,
                            "type": param_type,
                            "description": param_desc,
                        })
                        continue
                    else:
                        i += 1
                        continue
                elif inner_ind >= 2:
                    # Expression body lines
                    expression_lines.append(inner_s)
                i += 1

            udfs[udf_name] = UDF(
                name=udf_name,
                expression="\n".join(expression_lines),
                return_type=return_type,
                description=description,
                parameters=parameters,
                lineage_tag=lineage_tag,
            )
        else:
            i += 1

    return udfs


# ---------------------------------------------------------------------------
# Calendar column groups (from column annotations)
# ---------------------------------------------------------------------------

def extract_calendar_groups(tables: dict[str, Table]) -> list[CalendarColumnGroup]:
    """Extract calendar column groups from table column annotations."""
    groups: list[CalendarColumnGroup] = []

    for table_name, table in tables.items():
        for col in table.columns:
            # Check raw annotations for calendar-related settings
            if table._raw_annotations:
                for ann_line in table._raw_annotations.splitlines():
                    if "CalendarColumnGroup" in ann_line or "TimeUnit" in ann_line:
                        # Parse annotation for calendar group
                        # Format: annotation CalendarColumnGroup = {"TimeUnit": "Year", ...}
                        if col.name in ann_line:
                            time_unit = "Year"  # Default
                            if "Month" in ann_line:
                                time_unit = "Month"
                            elif "Quarter" in ann_line:
                                time_unit = "Quarter"
                            elif "Day" in ann_line:
                                time_unit = "Day"
                            elif "Week" in ann_line:
                                time_unit = "Week"

                            groups.append(CalendarColumnGroup(
                                name=f"{col.name}_{time_unit}",
                                column_name=col.name,
                                table_name=table_name,
                                time_unit=time_unit,
                                is_default=False,
                            ))

    return groups
