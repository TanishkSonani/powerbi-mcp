"""
TMDL in-memory model — pure Python dataclasses.
Represents tables, columns, measures, relationships, and the full model state.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from uuid import uuid4


def _new_guid() -> str:
    return str(uuid4())


@dataclass
class Column:
    name: str
    data_type: str = "string"
    source_column: Optional[str] = None
    expression: Optional[str] = None          # calculated column DAX
    format_string: Optional[str] = None
    description: Optional[str] = None
    lineage_tag: str = field(default_factory=_new_guid)
    is_hidden: bool = False
    sort_by_column: Optional[str] = None


@dataclass
class Measure:
    name: str
    expression: str
    format_string: Optional[str] = None
    description: Optional[str] = None
    display_folder: Optional[str] = None
    lineage_tag: str = field(default_factory=_new_guid)
    is_hidden: bool = False


@dataclass
class Table:
    name: str
    lineage_tag: str = field(default_factory=_new_guid)
    description: Optional[str] = None
    is_hidden: bool = False
    columns: list[Column] = field(default_factory=list)
    measures: list[Measure] = field(default_factory=list)
    # Raw text blocks preserved verbatim during read/write cycles
    _raw_partitions: str = ""
    _raw_annotations: str = ""


@dataclass
class Relationship:
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    from_cardinality: str = "many"
    to_cardinality: str = "one"
    is_active: bool = True
    cross_filter_behavior: Optional[str] = None
    name: Optional[str] = None               # optional TMDL relationship ID


@dataclass
class DatabaseInfo:
    name: str
    compatibility_level: int = 1605
    lineage_tag: str = field(default_factory=_new_guid)


@dataclass
class ModelInfo:
    lineage_tag: str = field(default_factory=_new_guid)
    culture: str = "en-US"


@dataclass
class RlsFilter:
    table_name: str
    filter_expression: str | None = None   # None = no row filter (full access)


@dataclass
class Role:
    name: str
    model_permission: str = "Read"         # Read | ReadRefresh | ReadExploreData | Admin
    filters: list[RlsFilter] = field(default_factory=list)


@dataclass
class TmdlModelState:
    """Complete in-memory snapshot of a TMDL definition/ folder."""
    definition_path: Path
    database: DatabaseInfo = field(default_factory=lambda: DatabaseInfo(name="Model"))
    model_info: ModelInfo = field(default_factory=ModelInfo)
    tables: dict[str, Table] = field(default_factory=dict)   # table_name → Table
    relationships: list[Relationship] = field(default_factory=list)
    roles: dict[str, Role] = field(default_factory=dict)     # role_name → Role
    _dirty: bool = False
