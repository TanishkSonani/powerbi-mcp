# powerbi-local-mcp

Universal local MCP server for Power BI modeling. Connects to any MCP-compatible client — Claude Desktop, custom agents, or IDEs. **No cloud. No Fabric. No REST API.** Strictly local Power BI Desktop files and PBIP folders.

## Current Phase: 4 — Complete (43 Tools)

All phases implemented: Foundation & Resources, TMDL File Manipulation, Live Desktop Integration, and Advanced Features (RLS, Translations, UDFs, Calendar Groups).

### Resources (4)

| Resource URI | Content |
|---|---|
| `resource://dax_query_instructions_and_examples` | DAX query rules + 13 worked examples |
| `resource://dax_udf_instructions_and_examples` | UDF type system, param modes, examples |
| `resource://calendar_instructions_and_examples` | Calendar column groups, fiscal calendars |
| `resource://powerbi_project_instructions` | PBIP folder structure, TMDL format |

### Prompts (2)

| Prompt | Purpose |
|---|---|
| `connect_desktop` | Connect to a running Power BI Desktop file |
| `connect_pbip` | Open a Power BI Project (PBIP) folder |

### Tools (43)

| Category | Tools |
|---|---|
| **Model** | `open_pbip_folder`, `get_model_info`, `save_model` |
| **Tables** | `list_tables`, `get_table`, `create_table`, `update_table`, `delete_table` |
| **Measures** | `list_measures`, `get_measure`, `create_measure`, `update_measure`, `delete_measure` |
| **Columns** | `list_columns`, `create_column`, `update_column`, `delete_column` |
| **Relationships** | `list_relationships`, `create_relationship`, `delete_relationship` |
| **Desktop** | `discover_desktop`, `connect_desktop`, `disconnect`, `get_desktop_model_info` |
| **DAX** | `execute_dax`, `validate_measure`, `push_measure_live` |
| **RLS Roles** | `list_roles`, `create_role`, `update_role`, `add_rls_filter`, `delete_rls_filter` |
| **Cultures** | `list_cultures`, `add_translation`, `bulk_add_translations` |
| **UDFs** | `list_udfs`, `create_udf`, `update_udf`, `delete_udf` |
| **Calendars** | `list_calendars`, `create_calendar`, `update_calendar_column_group`, `delete_calendar` |

> 📘 **Using this as a Power BI analyst rather than a developer?** See
> **[README_FOR_ANALYSTS.md](README_FOR_ANALYSTS.md)** — plain-English guide with example prompts.

### Context support

Every tool declares its supported context in its MCP description, so agents pick correctly:
`[file+live]`, `[file]`, `[live]`, `[any]`.

| Capability | file (PBIP folder) | live (Desktop) |
|---|:--:|:--:|
| All read/inspect tools (tables, columns, measures, relationships, roles, cultures, UDFs, calendars, model info) | ✅ | ✅ |
| Measures / columns / relationships / roles / RLS / translations — create, update, delete | ✅ | ✅ ¹ |
| `create_table`, UDFs, calendar groups | ✅ | ❌ ² |
| `execute_dax` | ❌ ³ | ✅ |
| `validate_measure` | ✅ static | ✅ evaluated |
| `save_model` | ✅ | no-op ⁴ |

¹ Live writes use granular TOM edits and require Microsoft's free **Analysis Services client
libraries** (AMO/ADOMD). Without them every write refuses with an actionable message — it never
falls back to an unsafe path. Live *reads* need nothing extra.
² No stable live-edit surface; edit the saved model instead.
³ A folder of TMDL text has no query engine — this is a hard limit, not a gap.
⁴ Live edits apply immediately, so there is nothing to flush.

### Live editing safety

Live writes are **granular** (`Measures.Add(...)` → `SaveChanges()`). An earlier implementation
used a whole-table TMSL `createOrReplace` whose payload omitted `columns` and rewrote Power Query
(`type: 'm'`) partitions as calculated ones — destroying any real table. That path is disabled and
replaced; no operation replaces a whole object.

---

## Prerequisites

- Python 3.11+ (tested on 3.14)
- pip
- Power BI Desktop (for live connection features)

## Installation

```bash
cd bimcp
pip install -e .
```

Or without installing (direct run):

```bash
pip install mcp pyyaml pydantic psutil python-dotenv requests
python server.py
```

## Running

```bash
python server.py
```

The server speaks MCP over stdio and is ready for client connection.

---

## Claude Desktop Configuration

Add this entry to your `claude_desktop_config.json`:

**Location:**
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "powerbi-local-mcp": {
      "command": "python",
      "args": ["path/to/bimcp/server.py"]
    }
  }
}
```

> **Tip:** Use absolute paths. Run `where python` (Windows) or `which python` (macOS/Linux) to find your Python path.

---

## Project Structure

```
bimcp/
├── server.py                     # MCP server entry point (stdio transport)
├── pyproject.toml                # Dependencies and project config
├── resources/
│   └── md/                       # Microsoft reference guides (MCP Resources)
│       ├── dax_query_instructions_and_examples.md
│       ├── dax_udf_instructions_and_examples.md
│       ├── calendar_instructions_and_examples.md
│       └── powerbi_project_instructions.md
├── src/
│   ├── context/                  # Context management (File/Live)
│   │   ├── manager.py            # FileContext + ContextManager singleton
│   │   ├── live_context.py       # XMLA HTTP client for Desktop
│   │   ├── ps_live_context.py    # PowerShell ADOMD bridge
│   │   └── ps_adomd_bridge.py    # PowerShell script runner
│   ├── discovery/
│   │   └── port_finder.py        # Desktop port discovery
│   ├── tmdl/                     # TMDL parser/writer
│   │   ├── models.py             # Dataclasses for all TMDL objects
│   │   ├── parser.py             # Pure Python TMDL parser
│   │   ├── writer.py             # TMDL serializer
│   │   └── path_resolver.py      # PBIP folder resolution
│   ├── tools/                    # MCP tool implementations
│   │   ├── model_tools.py        # open_pbip_folder, save_model, get_model_info
│   │   ├── table_tools.py        # Table CRUD
│   │   ├── measure_tools.py      # Measure CRUD
│   │   ├── column_tools.py       # Column CRUD
│   │   ├── relationship_tools.py # Relationship CRUD
│   │   ├── desktop_tools.py      # discover, connect, disconnect
│   │   ├── dax_tools.py          # execute_dax, validate, push_measure_live
│   │   ├── role_tools.py         # RLS role management
│   │   ├── culture_tools.py      # Translation management
│   │   ├── udf_tools.py          # UDF management
│   │   └── calendar_tools.py     # Calendar column groups
│   ├── resources/
│   │   └── provider.py           # Scans md/ folder, serves as MCP resources
│   └── prompts/
│       └── connection_prompts.py # connect_desktop + connect_pbip prompts
└── tests/
    ├── test_phase1.py            # Resource/prompt tests
    ├── test_phase2.py            # TMDL file manipulation tests
    ├── test_phase3.py            # Live Desktop tests
    └── test_phase4.py            # RLS, cultures, UDFs, calendars tests
```

---

## Roadmap

| Phase | Status | Description |
|---|---|---|
| **1 — Foundation & Resources** | ✅ Complete | MCP server, resource guides, prompts |
| **2 — TMDL File Manipulation** | Planned | Read/write PBIP folders via TOM + TmdlSerializer |
| **3 — Live Desktop Integration** | Planned | Port discovery, DAX execution via local XMLA |
| **4 — Advanced Local Features** | Planned | RLS, translations, UDFs, calendar column groups |
