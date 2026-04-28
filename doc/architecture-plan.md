# Comprehensive Architecture Plan: Universal Local Power BI MCP Server

---

## 1. High-Level Architecture

### Core Insight: One TOM API, Two Connection Modes

The most important architectural decision is **not** to build a custom TMDL parser. The `Microsoft.AnalysisServices.Tabular` library exposes `TmdlSerializer.DeserializeModelFromFolder()` and `TmdlSerializer.SerializeModelToFolder()` — the same official serializer Microsoft uses internally. This means your tool surface is identical whether targeting a PBIP folder on disk or a live Desktop instance. Only the connection string changes.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    MCP CLIENTS (any)                                │
│         Claude Desktop │ Custom Agents │ IDEs │ curl                │
└───────────────────────────┬─────────────────────────────────────────┘
                            │ stdio (JSON-RPC 2.0)
┌───────────────────────────▼─────────────────────────────────────────┐
│                    MCP SERVER CORE  (Python)                        │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────────┐ │
│  │  Tool Router    │  │ Resource Provider│  │  Prompt Provider   │ │
│  │  (dispatch)     │  │  (serves .md)    │  │  (slash commands)  │ │
│  └────────┬────────┘  └──────────────────┘  └────────────────────┘ │
│           │                                                         │
│  ┌────────▼────────────────────────────────────────────┐           │
│  │              Context Manager                         │           │
│  │  ┌──────────────────────┐  ┌───────────────────────┐│           │
│  │  │  File Context        │  │  Live Context          ││           │
│  │  │  (PBIP folder open)  │  │  (Desktop connected)   ││           │
│  │  │  conn: tmdl://path   │  │  conn: localhost:<port> ││          │
│  │  └──────────────────────┘  └───────────────────────┘│           │
│  └──────────────────────┬──────────────────────────────┘           │
│                         │                                           │
│  ┌──────────────────────▼──────────────────────────────┐           │
│  │              TOM Bridge  (pythonnet)                 │           │
│  │                                                      │           │
│  │  TmdlSerializer.DeserializeModelFromFolder()         │           │
│  │  TmdlSerializer.SerializeModelToFolder()             │           │
│  │  Server.Connect("localhost:<port>")                  │           │
│  │  AdomdCommand.ExecuteReader() → DAX results          │           │
│  └──────────────────────────────────────────────────────┘           │
└─────────────────────────────────────────────────────────────────────┘
                            │
         ┌──────────────────┴────────────────────┐
         │                                        │
┌────────▼────────┐                    ┌──────────▼──────────┐
│  PBIP Folder    │                    │  Power BI Desktop   │
│  on Disk        │                    │  (running)          │
│                 │                    │                     │
│  .SemanticModel/│                    │  Embedded AS Engine │
│  definition/    │                    │  msmdsrv.exe        │
│  ├── model.tmdl │                    │  port: dynamic      │
│  ├── tables/    │                    │                     │
│  └── rels.tmdl  │                    │  localhost:<port>   │
└─────────────────┘                    └─────────────────────┘
```

### Routing Logic

The `ContextManager` holds one active context at a time. Every tool call goes through it:

```
Tool called
    │
    ▼
Is a context active?
    │ No → return error: "No model open. Use open_pbip_folder or connect_desktop first."
    │
    ▼
What type is the active context?
    │
    ├── FileContext → Load TMDL from folder into in-memory TOM model
    │                 → Apply mutation via TOM API
    │                 → Serialize back to disk via TmdlSerializer
    │
    └── LiveContext → Mutation: Apply via TOM Server.Connect("localhost:<port>")
                      DAX query: Route to AdomdClient (NOT TOM — different library)
                      Never writes to disk; Desktop owns the files
```

The routing is not by tool name — every tool works in both modes. The context type determines execution path. This is the key design principle: **tools are mode-agnostic, the context is mode-aware**.

One exception: `execute_dax` is **Live-only**. In FileContext, it returns a clear error: `"DAX execution requires a live Desktop connection. Use connect_desktop first."` This is honest and consistent.

### Local XMLA Port Discovery

Power BI Desktop starts a private `msmdsrv.exe` (Analysis Services) subprocess per model. The port is written to disk in a predictable location. Discovery uses a **layered fallback strategy**:

```python
WORKSPACE_ROOT = Path(os.environ["LOCALAPPDATA"]) \
    / "Microsoft" / "Power BI Desktop" \
    / "AnalysisServicesWorkspaces"

# Each open model gets a workspace subfolder:
# AnalysisServicesWorkspaces/
# └── AnalysisServicesWorkspace_{GUID}/
#     └── Data/
#         └── msmdsrv.port.txt        ← PRIMARY: contains the port integer

# FALLBACK 1: Parse Desktop's FlightRecorder logs
PBI_LOG = Path(os.environ["APPDATA"]) \
    / "Microsoft" / "Power BI Desktop" / "Traces"

# FALLBACK 2: netstat — find ports owned by msmdsrv.exe PID
# subprocess.run(["netstat", "-ano"], capture_output=True)
# cross-ref with tasklist to find msmdsrv.exe PIDs

# FALLBACK 3: Scan localhost:2383-65535 range with 50ms timeout
# (last resort, slow — flag this as degraded mode)
```

For multi-model scenarios (multiple Desktop windows open), `discover_desktop` enumerates **all** workspace folders, reads each `msmdsrv.port.txt`, and attempts to read the model name from the AS instance. The user selects which to connect to.

The `AnalysisServices.AppSettings.json` (`"isProcessWithUI": true`) confirms Microsoft uses this same embedded AS pattern — it's stable and documented, not a hack.

---

## 2. Recommended Tech Stack & Rationale

### Language: Python 3.11+

**Not TypeScript.** Here is the specific reasoning for each constraint:

| Requirement | Python Verdict | TypeScript Verdict |
|---|---|---|
| TOM library access | `pythonnet` → direct .NET interop, same API as C# | `edge-js` or `ffi-napi` → fragile, limited, breaks on .NET version bumps |
| TMDL serialization | `TmdlSerializer` via pythonnet = official, zero parser to write | No .NET = must write full TMDL parser from scratch |
| Port file / filesystem ops | `pathlib`, `psutil` — excellent | `fs`, `child_process` — adequate but no advantage |
| Non-blocking DAX | `asyncio` + `run_in_executor` → clean thread offloading | `worker_threads` — possible but TOM DLL can't cross thread easily |
| MCP SDK maturity | `mcp` (Anthropic's Python SDK) — first-class support | `@modelcontextprotocol/sdk` — also first class |
| Windows DLL loading | pythonnet handles this natively | No native .NET interop |

**The decisive factor**: Using pythonnet + TOM means you never write a TMDL parser. That alone saves 40+ hours and eliminates an entire class of correctness bugs (whitespace sensitivity, escape sequences, multi-line DAX expressions with embedded quotes).

### Specific Libraries

```
# Core MCP
mcp>=1.0.0                    # Anthropic's Python MCP SDK

# .NET Interop (the linchpin)
pythonnet>=3.0.3              # CLR bridge — loads TOM DLL into Python process

# Microsoft libraries loaded at runtime via pythonnet:
# Microsoft.AnalysisServices.Tabular.dll   → TOM (model CRUD + TMDL serialization)
# Microsoft.AnalysisServices.AdomdClient.dll → DAX query execution
# These ship with SQL Server Management Objects (SMO) or the ADOMD.NET NuGet package
# Simplest install: pip install sqlserver-analysis-services  (community wrapper)
# or: download NuGet Microsoft.AnalysisServices.Tabular and unzip DLLs

# Async runtime
asyncio (stdlib)              # Non-blocking stdio transport
concurrent.futures (stdlib)   # ThreadPoolExecutor for TOM calls

# Data
pydantic>=2.0                 # Tool input validation and schema generation
pandas (optional)             # DAX result formatting to markdown tables

# Filesystem
psutil>=5.9                   # Process inspection for port discovery fallback
pathlib (stdlib)              # PBIP folder traversal

# Config
pyyaml>=6.0                   # Parse .md frontmatter for resource metadata
python-dotenv>=1.0            # Optional env config
```

### Handling Long-Running DAX Queries Without Blocking stdio

The MCP stdio transport is a single-threaded message loop. A blocking DAX call would freeze it. The solution is thread offloading with cancellation support:

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tom-worker")

async def execute_dax_async(connection_string: str, dax: str, timeout_sec: int = 120):
    loop = asyncio.get_event_loop()
    # TOM/ADOMD calls run in a worker thread; stdio loop stays free
    result = await asyncio.wait_for(
        loop.run_in_executor(_executor, _blocking_dax_call, connection_string, dax),
        timeout=timeout_sec
    )
    return result

def _blocking_dax_call(connection_string: str, dax: str):
    # This runs in a thread — safe to block here
    conn = AdomdConnection(connection_string)
    conn.Open()
    cmd = AdomdCommand(dax, conn)
    reader = cmd.ExecuteReader()
    return _reader_to_dict(reader)
```

`asyncio.wait_for` provides clean timeout semantics. If the client disconnects, the Future is cancelled and the thread is signalled. The executor pool prevents thread explosion on rapid sequential calls.

---

## 3. Phased Implementation Plan

### Phase 1: Foundation & Resources

**Goal**: A running MCP server that any MCP client can connect to, exposing the `.md` files as proper MCP resources and answering `tools/list`.

**MCP Tools built:**
- *(none — Phase 1 has no tools, only resources and prompts)*

**MCP Resources built:**
- `resource://dax_query_instructions_and_examples`
- `resource://dax_udf_instructions_and_examples`
- `resource://calendar_instructions_and_examples`
- `resource://powerbi_project_instructions`

**MCP Prompts built** (mirroring Microsoft's CHANGELOG-revealed slash commands):
- `/connect_pbip` — "Open semantic model from PBIP folder '[path]'"
- `/connect_desktop` — "Connect to '[File Name]' in Power BI Desktop"

**Architecture of the resource provider:**

The `.md` files have YAML frontmatter (`name`, `description`, `uriTemplate`). The resource provider strips frontmatter, uses the `uriTemplate` value as the MCP URI, and serves the markdown body as the resource content. The LLM receives these as context — exactly how Microsoft uses them.

```python
# resources/provider.py
import re
from pathlib import Path
from mcp.types import Resource, TextContent

RESOURCE_DIR = Path(__file__).parent.parent / "resources" / "md"

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return {}, text
    import yaml
    meta = yaml.safe_load(match.group(1))
    body = text[match.end():]
    return meta, body

def list_resources() -> list[Resource]:
    resources = []
    for md_file in RESOURCE_DIR.glob("*.md"):
        meta, _ = _parse_frontmatter(md_file.read_text(encoding="utf-8"))
        resources.append(Resource(
            uri=meta.get("uriTemplate", f"resource://{md_file.stem}"),
            name=meta.get("name", md_file.stem),
            description=meta.get("description", ""),
            mimeType="text/markdown"
        ))
    return resources
```

**Done criteria**: `mcp dev server.py` connects cleanly. Claude Desktop config entry works. `resources/list` returns 4 resources. `resources/read resource://dax_query_instructions_and_examples` returns the full DAX guide body. No tools yet — that's intentional.

---

### Phase 2: TMDL File Manipulation (Desktop Closed)

**Goal**: Full CRUD on PBIP folder structure without Desktop running, using TOM + TmdlSerializer.

**MCP Tools built:**

| Tool | Description | Input | Output |
|---|---|---|---|
| `open_pbip_folder` | Load a PBIP `.SemanticModel/definition` folder into TOM | `path: str` | model summary |
| `list_tables` | List all tables with column/measure counts | — | table list |
| `get_table` | Full table definition (columns, measures, partitions) | `table_name: str` | table schema |
| `create_table` | Add a new calculated or import table | table schema | confirmation |
| `update_table` | Rename or change table properties | patch dict | confirmation |
| `delete_table` | Remove table and its TMDL file | `table_name: str` | confirmation |
| `list_measures` | All measures across all tables | — | measure list |
| `get_measure` | Get measure DAX + format string | `table: str, measure: str` | measure detail |
| `create_measure` | Add measure to a table | table + measure schema | confirmation |
| `update_measure` | Modify measure DAX or format string | patch dict | confirmation |
| `delete_measure` | Remove a measure | `table: str, measure: str` | confirmation |
| `list_columns` | All columns in a table | `table_name: str` | column list |
| `create_column` | Add calculated or data column | column schema | confirmation |
| `update_column` | Modify column properties | patch dict | confirmation |
| `delete_column` | Remove column | `table: str, column: str` | confirmation |
| `list_relationships` | All model relationships | — | relationship list |
| `create_relationship` | Add a relationship | relationship schema | confirmation |
| `delete_relationship` | Remove relationship by index or ID | relationship ID | confirmation |
| `save_model` | Serialize in-memory TOM model back to TMDL folder on disk | — | files written list |
| `get_model_info` | Top-level model properties | — | model summary |

**Core TOM bridge pattern:**

```python
# tom/bridge.py
import clr
from pathlib import Path

def _load_tom_dlls():
    dll_dir = Path(__file__).parent / "dlls"
    clr.AddReference(str(dll_dir / "Microsoft.AnalysisServices.Tabular"))
    clr.AddReference(str(dll_dir / "Microsoft.AnalysisServices.Core"))
    from Microsoft.AnalysisServices.Tabular import Server, TmdlSerializer
    return Server, TmdlSerializer

class FileContext:
    def __init__(self, definition_path: str):
        Server, TmdlSerializer = _load_tom_dlls()
        self._server = Server()
        # Load TMDL folder into in-memory TOM model (no network connection)
        self._database = TmdlSerializer.DeserializeModelFromFolder(definition_path)
        self._definition_path = definition_path
        self._dirty = False

    def save(self):
        Server, TmdlSerializer = _load_tom_dlls()
        TmdlSerializer.SerializeModelToFolder(self._database.Model, self._definition_path)
        self._dirty = False

    @property
    def model(self):
        return self._database.Model
```

**PBIP path resolution** — the `open_pbip_folder` tool validates the required structure from `powerbi_project_instructions.md`:

```python
def resolve_tmdl_definition_path(user_path: str) -> Path:
    p = Path(user_path)
    # Accept any of these as input:
    # 1. The definition/ folder directly
    # 2. The .SemanticModel/ folder
    # 3. The root PBIP folder
    if (p / "model.tmdl").exists():
        return p  # already at definition/
    if (p / "definition" / "model.tmdl").exists():
        return p / "definition"
    # Search for .SemanticModel subfolder
    candidates = list(p.glob("*.SemanticModel/definition"))
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError(f"Cannot locate TMDL definition folder in: {user_path}")
```

**Done criteria**: The LLM can open a `.pbip` folder, list all tables and measures, add a new measure, and save — with the `.tmdl` file on disk correctly updated. Validated against a real PBIP project.

---

### Phase 3: Live Desktop Integration

**Goal**: Discover running Desktop instances, connect via local XMLA, execute DAX, and apply model changes live.

**MCP Tools built:**

| Tool | Description |
|---|---|
| `discover_desktop` | Scan workspace folders, return list of {model_name, port, workspace_path} |
| `connect_desktop` | Establish LiveContext to a specific Desktop model by name or port |
| `disconnect` | Release current context (file or live) |
| `execute_dax` | Run DAX query on live Desktop, return results as markdown table |
| `get_desktop_model_info` | Read live model metadata via TOM |
| `push_measure_live` | Create/update measure in live model (no file write) |
| `validate_measure` | Evaluate a DAX expression in live model, return result or error |

**Port discovery implementation:**

```python
# discovery/port_finder.py
import os
from pathlib import Path
import subprocess, re

def find_desktop_instances() -> list[dict]:
    workspace_root = (
        Path(os.environ["LOCALAPPDATA"])
        / "Microsoft" / "Power BI Desktop"
        / "AnalysisServicesWorkspaces"
    )
    instances = []
    if not workspace_root.exists():
        return instances

    for ws_dir in workspace_root.iterdir():
        if not ws_dir.is_dir():
            continue
        port_file = ws_dir / "Data" / "msmdsrv.port.txt"
        if not port_file.exists():
            continue
        try:
            port = int(port_file.read_text().strip())
            model_name = _probe_model_name(port)  # quick TOM connect to read DB name
            instances.append({
                "model_name": model_name,
                "port": port,
                "workspace": str(ws_dir),
                "connection_string": f"Data Source=localhost:{port}",
            })
        except Exception:
            continue  # stale workspace from crashed session
    return instances

def _probe_model_name(port: int) -> str:
    # Quick TOM connect — read the database name
    server = Server()
    server.Connect(f"Data Source=localhost:{port}")
    name = server.Databases[0].Name if server.Databases.Count > 0 else f"model@{port}"
    server.Disconnect()
    return name
```

**DAX execution returning markdown table:**

```python
async def execute_dax(self, dax_query: str, max_rows: int = 500) -> str:
    loop = asyncio.get_event_loop()
    rows, columns = await asyncio.wait_for(
        loop.run_in_executor(_executor, self._run_dax, dax_query, max_rows),
        timeout=120,
    )
    # Format as markdown table for LLM readability
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join(["---"] * len(columns)) + " |"
    data_rows = ["| " + " | ".join(str(v) for v in row) + " |" for row in rows]
    return "\n".join([header, separator] + data_rows)
```

**Done criteria**: `discover_desktop` finds a running Desktop model. `connect_desktop "Sales Model"` establishes connection. `execute_dax "EVALUATE ROW(\"test\", 1)"` returns a result table. `push_measure_live` adds a measure visible immediately in Desktop without a save/reload cycle.

---

### Phase 4: Advanced Local Features

**Goal**: RLS, translations, UDFs, and calendar column groups — all backed by the `.md` resource files as LLM context.

**MCP Tools built:**

| Tool | Resource Guide Used |
|---|---|
| `list_roles` | — |
| `create_role` | — |
| `update_role` | — |
| `add_rls_filter` | — |
| `delete_rls_filter` | — |
| `list_cultures` | — |
| `add_translation` | — |
| `bulk_add_translations` | — |
| `list_udfs` | `dax_udf_instructions_and_examples` |
| `create_udf` | `dax_udf_instructions_and_examples` |
| `update_udf` | `dax_udf_instructions_and_examples` |
| `list_calendars` | `calendar_instructions_and_examples` |
| `create_calendar` | `calendar_instructions_and_examples` |
| `update_calendar_column_group` | `calendar_instructions_and_examples` |

The pattern for UDFs and calendars: before calling the tool, the LLM should `resources/read` the corresponding guide. The MCP Prompt definitions for `/connect_pbip` and `/connect_desktop` should automatically include relevant resources in context via prompt arguments — this mirrors what Microsoft does with their resource URIs in the CHANGELOG-era architecture.

**Done criteria**: Can add a French translation for all measures in one call. Can create an RLS role with a DAX filter. Can define a DAX UDF following the guide syntax. Can add a Gregorian calendar column group matching the `.md` example exactly.

---

## 4. Effort & Time Estimation

Assumptions: 1 senior developer, 10-12 hours/week (evenings + weekends). Estimates are deliberately conservative — TMDL and pythonnet have genuine sharp edges.

| Phase | Component | Hours | Notes |
|---|---|---|---|
| **Phase 1** | MCP server scaffold + stdio setup | 4h | `mcp` SDK is well-documented |
| | Frontmatter parser + resource provider | 3h | Regex + yaml, straightforward |
| | Prompt definitions + config system | 2h | |
| | Claude Desktop wiring + smoke test | 2h | `claude_desktop_config.json` entry |
| | **Phase 1 Total** | **11h** | **~1 week** |
| **Phase 2** | pythonnet environment setup + DLL loading | 6h | Version-pinning pythonnet to .NET runtime is the hard part |
| | FileContext: TmdlSerializer load/save | 5h | |
| | PBIP path resolver + validation | 3h | Per `powerbi_project_instructions.md` |
| | Table CRUD tools (6 tools) | 8h | 1.5h each incl. input schema |
| | Measure + Column CRUD (8 tools) | 10h | Similar pattern, faster after first |
| | Relationship tools (3 tools) | 4h | |
| | `save_model` + dirty-tracking | 3h | |
| | Integration tests (real .pbip folder) | 10h | Finding edge cases in real models |
| | **Phase 2 Total** | **49h** | **~4-5 weeks** |
| **Phase 3** | Port discovery (primary + fallbacks) | 6h | Stale workspace handling is finicky |
| | LiveContext: TOM Server.Connect | 5h | Auth on local = Windows/no-auth |
| | `execute_dax` async wrapper | 6h | Cancellation, timeout, ADOMD setup |
| | Result formatter (markdown table) | 3h | |
| | `discover_desktop` + `connect_desktop` | 4h | Multi-instance enumeration |
| | Live model mutation tools | 6h | Shares logic with Phase 2 |
| | Desktop deadlock handling | 5h | CHANGELOG warns: "Deadlock in table_operations GET" — real issue |
| | Integration tests (live Desktop) | 12h | Must test with real Desktop open, multiple models |
| | **Phase 3 Total** | **47h** | **~4-5 weeks** |
| **Phase 4** | RLS roles + filters (5 tools) | 10h | |
| | Translation cultures (3 tools) | 8h | Bulk ops are the complex part |
| | DAX UDFs (3 tools) | 8h | |
| | Calendar column groups (3 tools) | 8h | TimeUnit enum validation per the guide |
| | End-to-end integration tests | 8h | |
| | **Phase 4 Total** | **42h** | **~4 weeks** |
| | | | |
| **TOTAL** | | **~149 hours** | **~14-15 calendar weeks** |

**Risk items that could add time:**
- `pythonnet` + .NET 8 runtime compatibility: **+8h** if DLL resolution breaks (likely on first try)
- TMDL format changes between Desktop versions: **+4h** per regression
- Desktop XMLA port instability (port file stale after crash): **+4h** for robust cleanup detection
- Multi-architecture DLL packaging (x64 vs ARM64 Surface devices): **+6h** if needed

---

## 5. Immediate Next Steps — Phase 1 Kickoff

Here are the **exact 3 prompts** to give Claude Code right now to generate Phase 1:

---

**Prompt 1** — Project scaffold and dependency declaration:

> "Generate the complete project scaffold for my Python MCP server. Create: `pyproject.toml` with dependencies (mcp, pythonnet, pydantic, pyyaml, psutil), `server.py` as the MCP server entry point using stdio transport with the server name `powerbi-local-mcp`, and a `README.md` with the Claude Desktop `claude_desktop_config.json` entry. The server should start cleanly with no tools registered yet — just the MCP handshake working."

---

**Prompt 2** — Resource provider with the `.md` files:

> "Generate `src/resources/provider.py`. It must: (1) scan the `resources/md/` folder for `.md` files, (2) parse YAML frontmatter to extract `name`, `description`, and `uriTemplate` fields, (3) implement `list_resources()` returning a list of `mcp.types.Resource` objects using `uriTemplate` as the URI, and (4) implement `read_resource(uri: str)` returning the markdown body (frontmatter stripped) as `mcp.types.TextContent`. Also copy the 4 `.md` files from `extension/server/Resources/` into `resources/md/`. Wire the provider into `server.py` so `@server.list_resources` and `@server.read_resource` are registered."

---

**Prompt 3** — MCP Prompts and smoke test:

> "Generate `src/prompts/connection_prompts.py` with 3 MCP prompt definitions mirroring Microsoft's `/ConnectToPowerBIDesktop`, `/ConnectToFabric` (omit — local only), and `/ConnectToPowerBIProject` patterns from the CHANGELOG. Prompts: `connect_desktop` (arg: `file_name: str`) and `connect_pbip` (arg: `folder_path: str`). Each prompt should embed the `powerbi_project_instructions` resource URI in its messages so the LLM gets file-structure context automatically. Then generate `tests/test_phase1.py` that starts the server in a subprocess, connects via MCP test client, asserts `resources/list` returns exactly 4 resources with correct URIs, and asserts `prompts/list` returns 2 prompts."

---

---

## 6. Phase 2 Kickoff Prompts — TMDL File Manipulation

> **Environment note (discovered during Phase 1 execution):** pythonnet has no Python 3.14 wheel (max available: 2.5.2) and Power BI Desktop is not installed on this machine. Phase 2 therefore uses a **pure Python TMDL parser/writer** instead of the TOM bridge. The code architecture (FileContext, ContextManager, tool surface) is identical — only the underlying engine changes. The TOM bridge can be swapped in later by replacing `src/tmdl/parser.py` and `src/tmdl/writer.py` with pythonnet calls once the environment supports it.

Here are the **exact 4 prompts** to execute Phase 2:

---

**Phase 2 — Prompt 1**: TMDL Foundation Layer (models, parser, writer, path resolver, context manager)

> "Create the TMDL foundation layer for Phase 2 in `bimcp/src/tmdl/` and `bimcp/src/context/`. No pythonnet — pure Python string parsing only.
>
> **`src/tmdl/models.py`**: Python dataclasses — `Column` (name, data_type, source_column, expression, format_string, description, lineage_tag, is_hidden, sort_by_column), `Measure` (name, expression, format_string, description, display_folder, lineage_tag, is_hidden), `Table` (name, lineage_tag, description, is_hidden, columns: list, measures: list, _raw_partitions: str, _raw_annotations: str), `Relationship` (from_table, from_column, to_table, to_column, from_cardinality='many', to_cardinality='one', is_active=True, name=None), `DatabaseInfo` (name, compatibility_level=1605, lineage_tag), `ModelInfo` (lineage_tag, culture='en-US'), `TmdlModelState` (definition_path, database, model_info, tables: dict[str, Table], relationships: list, _dirty: bool).
>
> **`src/tmdl/parser.py`**: Functions — `parse_table_file(path) -> Table` (line-by-line state machine: extract table header name, then scan indent-1 lines for `column`, `measure`, `partition`, `annotation` keywords and `lineageTag:`/`isHidden`/`description:` properties; for measures detect inline `= expr` vs empty `=` then collect indent-3 lines as multi-line expression; collect partition+annotation blocks as raw strings), `parse_relationships_file(path) -> list[Relationship]` (split on blank lines to find `relationship` blocks, extract key: value props), `parse_database_file(path) -> DatabaseInfo`, `parse_model_file(path) -> ModelInfo`. All use `utf-8-sig` encoding.
>
> **`src/tmdl/writer.py`**: Functions — `table_to_tmdl_text(table: Table) -> str` (emit header, lineageTag, columns, measures, raw partitions, raw annotations using tab indentation), `column_to_tmdl_lines(c: Column) -> list[str]`, `measure_to_tmdl_lines(m: Measure) -> list[str]` (inline for single-line, 3-tab indent for multi-line DAX), `relationships_to_tmdl_text(rels: list[Relationship]) -> str`. Quote names that contain spaces with single quotes.
>
> **`src/tmdl/path_resolver.py`**: `resolve_tmdl_definition_path(user_path: str) -> Path` — accept: (1) definition/ folder (has model.tmdl), (2) .SemanticModel/ folder (has definition/model.tmdl), (3) PBIP root (glob `*.SemanticModel/definition`). Raise `ValueError` with a helpful message if none found.
>
> **`src/context/manager.py`**: `FileContext` class (holds definition_path + TmdlModelState, `save()` writes all tables via `table_to_tmdl_text`, writes relationships.tmdl, deletes stale .tmdl files for removed tables, returns list of written paths). `ContextManager` singleton (`open_file_context(path)`, `get_active_context()` raises RuntimeError if none, `close_context()`). Helper `_load_tmdl_model(definition_path)` reads all files."

---

**Phase 2 — Prompt 2**: Model + Table tools (8 tools) — wire into `server.py`

> "Create `src/tools/model_tools.py` and `src/tools/table_tools.py`. Each function uses `ContextManager.get().get_active_context()` first and raises `RuntimeError` if no context is active.
>
> **Model tools** (`model_tools.py`): `open_pbip_folder(path: str) -> dict` (calls path_resolver then context_manager.open_file_context, returns model name, table count, measure count, compatibility level, definition_path); `get_model_info() -> dict` (returns all model metadata + dirty flag); `save_model() -> dict` (calls ctx.save(), returns files_written list).
>
> **Table tools** (`table_tools.py`): `list_tables() -> dict` (returns list with name, column count, measure count, is_hidden per table); `get_table(table_name: str) -> dict` (full table detail: all columns and measures as dicts); `create_table(name: str, description: str = None) -> dict` (add new Table to model_state.tables, set dirty); `update_table(table_name: str, new_name: str = None, description: str = None) -> dict` (rename key in tables dict if name changed, set dirty); `delete_table(table_name: str) -> dict` (remove from dict, set dirty).
>
> **Wire into `server.py`**: Add `@app.list_tools()` handler returning all 8 Tool objects with proper `inputSchema` JSON dicts. Add `@app.call_tool()` dispatcher: `import json` + a `_TOOL_REGISTRY` dict mapping tool name → callable, call via `result = registry[name](**arguments)` and return `[types.TextContent(type='text', text=json.dumps(result, indent=2))]`. Wrap in try/except to return error messages cleanly. Import `CallToolResult` and `TextContent` from `mcp.types`."

---

**Phase 2 — Prompt 3**: Measure + Column + Relationship tools (12 tools) — wire into `server.py`

> "Create `src/tools/measure_tools.py`, `src/tools/column_tools.py`, `src/tools/relationship_tools.py`. All follow the same pattern: get active context, mutate `model_state`, set `_dirty = True`.
>
> **Measure tools**: `list_measures() -> dict` (all measures across all tables, truncate expression at 80 chars); `get_measure(table_name, measure_name) -> dict` (full measure detail); `create_measure(table_name, name, expression, format_string=None, description=None, display_folder=None) -> dict`; `update_measure(table_name, measure_name, new_expression=None, new_name=None, new_format_string=None, description=None) -> dict`; `delete_measure(table_name, measure_name) -> dict`.
>
> **Column tools**: `list_columns(table_name) -> dict`; `create_column(table_name, name, data_type='string', source_column=None, expression=None, format_string=None, description=None) -> dict`; `update_column(table_name, column_name, new_name=None, description=None, format_string=None, data_type=None) -> dict`; `delete_column(table_name, column_name) -> dict`.
>
> **Relationship tools**: `list_relationships() -> dict`; `create_relationship(from_table, from_column, to_table, to_column, from_cardinality='many', to_cardinality='one') -> dict` (check for duplicates before adding); `delete_relationship(from_table, from_column, to_table, to_column) -> dict`.
>
> **Update `server.py`**: Extend `_TOOL_REGISTRY` with all 12 new tools. Extend `@app.list_tools()` to return all 20 Tool objects total with correct `inputSchema` for each (required vs optional parameters)."

---

**Phase 2 — Prompt 4**: Integration test suite + TMDL fixture

> "Create a minimal but valid TMDL fixture at `tests/fixtures/TestModel.SemanticModel/definition/` with: `database.tmdl` (database TestModel, compatibilityLevel 1605), `model.tmdl` (model Model, culture en-US), `tables/Sales.tmdl` (2 columns: ProductKey/string and Amount/decimal, 1 measure: 'Total Amount' = SUM(Sales[Amount]) with formatString, 1 partition block as raw text, 1 annotation line), `tables/Product.tmdl` (1 column: ProductKey/string, 1 partition block), `relationships.tmdl` (1 relationship from Sales[ProductKey] to Product[ProductKey]).
>
> Create `tests/test_phase2.py` using the same MCP subprocess+client pattern as `test_phase1.py`. Test suite (use a COPY of the fixture via `tmp_path` or `shutil.copytree` to avoid corrupting the fixture):
> 1. `test_tools_list_returns_20` — `tools/list` returns exactly 20 tools.
> 2. `test_open_pbip_folder` — open_pbip_folder returns model name 'TestModel', 2 tables, 1 measure total.
> 3. `test_list_tables` — returns 2 tables: Sales and Product.
> 4. `test_get_table_with_measures` — get_table('Sales') returns columns and measures.
> 5. `test_create_and_get_measure` — create_measure on Sales then get_measure returns it.
> 6. `test_update_measure` — update_measure changes the DAX expression.
> 7. `test_delete_measure` — delete_measure removes it.
> 8. `test_save_model_writes_disk` — after create_measure + save_model, the Sales.tmdl file on disk contains the new measure name.
> 9. `test_create_and_delete_relationship` — create_relationship then delete_relationship, assert count goes +1 then back to original.
> 10. `test_no_context_error` — calling list_tables before open_pbip_folder returns an error message (not a crash)."

---

---

## 7. Phase 3 Kickoff Prompts — Live Desktop Integration

> **Environment note:** pythonnet remains unavailable for Python 3.14. Phase 3 therefore uses a **pure Python XMLA HTTP client** (built on the `requests` library) rather than the TOM/ADOMD.NET bridge described in the original architecture. The Analysis Services engine embedded in Power BI Desktop (`msmdsrv.exe`) exposes a standard SOAP-based XMLA endpoint at `http://localhost:{port}/xmla`. Every capability the original plan required of TOM/ADOMD is achievable through XMLA:
> - **Metadata discovery** → XMLA `Discover` (DBSCHEMA_CATALOGS, TMSCHEMA_TABLES, TMSCHEMA_MEASURES)
> - **DAX query execution** → XMLA `Execute` with a DAX `<Statement>`
> - **Live measure push** → XMLA `Execute` with a TMSL `createOrReplace` JSON command
> - **Expression validation** → XMLA `Execute` a minimal `EVALUATE ROW(...)` and inspect for fault
>
> Port discovery remains unchanged: read `msmdsrv.port.txt` from the AnalysisServicesWorkspaces folder (primary), fall back to `netstat` cross-referenced against `tasklist` (secondary). Both are pure Python OS calls. Since Power BI Desktop is not installed on this machine, all Phase 3 tests use fixture XML responses and `unittest.mock.patch` for the HTTP layer; live integration tests are guarded by a `PBI_LIVE_PORT` environment variable skip marker.

Here are the **exact 4 prompts** to execute Phase 3:

---

**Phase 3 — Prompt 1**: Port Discovery + XMLA LiveContext Infrastructure

> "Create the Live Desktop integration infrastructure for Phase 3. No pythonnet — use pure Python `requests` for all XMLA communication.
>
> **Add `requests` to `pyproject.toml`** under `[project] dependencies`.
>
> **`src/discovery/__init__.py`**: empty.
>
> **`src/discovery/port_finder.py`**:
> - `find_desktop_port_files() -> list[dict]` — glob `%LOCALAPPDATA%/Microsoft/Power BI Desktop/AnalysisServicesWorkspaces/*/Data/msmdsrv.port.txt`. For each file, read and parse the port integer; skip on `FileNotFoundError` or `ValueError`. Return `[{port: int, workspace_path: str}]`.
> - `find_desktop_ports_via_netstat() -> list[int]` — run `tasklist /FO CSV /NH /FI "IMAGENAME eq msmdsrv.exe"` to get PIDs, then run `netstat -ano` and filter for TCP LISTENING lines whose PID matches; parse port numbers. Return `list[int]`. Return `[]` on any subprocess error.
> - `probe_xmla_instance(port: int, timeout: float = 2.0) -> dict | None` — POST a minimal XMLA Discover SOAP envelope (`<RequestType>DISCOVER_XML_METADATA</RequestType>` with empty `<RestrictionList/>`) to `http://localhost:{port}/xmla`. On HTTP 200, parse the XML response with `xml.etree.ElementTree` to find the first `<DATABASE_ID>` or `<CATALOG_NAME>` element; use its text as `model_name` (fallback: `f"model@{port}"`). Return `{model_name: str, port: int, connection_string: str}`. On `requests.exceptions.ConnectionError`, `requests.exceptions.Timeout`, or any XML parse error, return `None`.
> - `find_desktop_instances() -> list[dict]` — combine results from `find_desktop_port_files()` and (if that returns empty) `find_desktop_ports_via_netstat()`. Deduplicate ports. For each unique port, call `probe_xmla_instance(port)` and collect non-None results. Return the list sorted by port ascending.
>
> **`src/context/live_context.py`**: `LiveContext` class:
> - `__init__(self, port: int, model_name: str)`: store `self.port`, `self.model_name`, `self.connection_string = f"http://localhost:{port}/xmla"`, `self._catalog = model_name`.
> - `_discover(self, request_type: str, restrictions: dict = None, timeout: float = 10.0) -> str`: build a SOAP Discover envelope (`xmlns="urn:schemas-microsoft-com:xml-analysis"`), POST to `self.connection_string` with `Content-Type: text/xml; charset=utf-8`. Return `response.text`. Raise `ConnectionError` on `requests` exception.
> - `_execute(self, statement: str, timeout: float = 120.0, is_tmsl: bool = False) -> str`: build SOAP Execute envelope with `<Command><Statement>{statement}</Statement></Command>` and `<PropertyList><Catalog>{self._catalog}</Catalog><Format>Tabular</Format></PropertyList>`. POST and return `response.text`. Raise `ConnectionError` on network error.
> - `_parse_xmla_rowset(xml_text: str) -> dict`: parse the XMLA Execute response XML. Find `<xsd:element>` nodes in the schema to extract column names and types, then find `<row>` elements in the data section; extract child text values per column. Return `{columns: list[str], rows: list[list], row_count: int}`. Raise `ValueError` if response contains a SOAP Fault (extract faultstring text and include it in the message).
> - `_format_markdown_table(columns: list[str], rows: list[list]) -> str`: emit `| col1 | col2 |`, then `| --- | --- |`, then one pipe-row per data row.
> - `execute_dax(self, dax_query: str, max_rows: int = 500) -> dict`: call `_execute(dax_query)` then `_parse_xmla_rowset`. Truncate to `max_rows`. Return `{columns, rows, row_count, truncated: bool, markdown_table: str}`.
> - `get_model_info(self) -> dict`: call `_discover('TMSCHEMA_TABLES')`, parse table names from the XML; call `_discover('TMSCHEMA_MEASURES')`, count measures. Return `{model_name, port, connection_string, table_count, measure_count, tables: list[str]}`.
> - `push_measure(self, table_name: str, name: str, expression: str, format_string: str | None = None) -> dict`: build a TMSL `createOrReplace` JSON object `{"createOrReplace": {"object": {"database": self._catalog, "table": table_name, "measure": name}, "definition": {"name": name, "expression": expression, "formatString": format_string or ""}}}`. Call `_execute(json.dumps(tmsl))`. Check response for Fault; on success return `{status: 'pushed', table_name, measure_name: name}`.
> - `validate_expression(self, table_name: str, expression: str) -> dict`: call `execute_dax(f'EVALUATE ROW(\"Result\", {expression})')`. If it succeeds return `{valid: True, result: str(rows[0][0]) if rows else None, error: None}`. If `_parse_xmla_rowset` raises `ValueError`, return `{valid: False, result: None, error: str(exc)}`.
> - `close(self)`: no-op (stateless HTTP).
>
> **Update `src/context/manager.py`**:
> - Add `from src.context.live_context import LiveContext`.
> - Add `open_live_context(port: int, model_name: str)` method — creates `LiveContext(port, model_name)` and sets it as the active context, closing any existing context first.
> - Add `context_type` property returning `'file'` if active context is `FileContext`, `'live'` if `LiveContext`, `None` if no context."

---

**Phase 3 — Prompt 2**: Discovery + Connection + Model Info Tools (4 new tools → 24 total)

> "Create `src/tools/desktop_tools.py` with 4 tools for discovering and connecting to a live Power BI Desktop instance. All tools import `ContextManager` from `src.context.manager` and `find_desktop_instances` from `src.discovery.port_finder`.
>
> **`discover_desktop() -> dict`** (no arguments): call `find_desktop_instances()`. Return `{instances: [{model_name, port, connection_string}], count: int}`. Never raises — return empty list if Desktop not running or folder absent.
>
> **`connect_desktop(model_name: str = None, port: int = None) -> dict`**: call `find_desktop_instances()`. Selection logic (in order): if `port` given, find instance with matching port; if `model_name` given, find by case-insensitive name match; if neither given and exactly 1 instance found, use it; otherwise return `{error: 'Multiple Desktop instances found. Specify model_name or port. Available: [{model_name}@{port}, ...]'}`. On match found, call `ContextManager.get().open_live_context(instance['port'], instance['model_name'])`. Return `{status: 'connected', model_name, port, connection_string}`. If no instances found, return `{error: 'No Power BI Desktop instance detected. Open a model in Desktop first.'}`.
>
> **`disconnect() -> dict`**: call `ContextManager.get().close_context()`. Return `{status: 'disconnected'}`. Does not error if no context was active.
>
> **`get_desktop_model_info() -> dict`**: get active context via `ContextManager.get().get_active_context()`. If it is a `FileContext`, return `{error: 'Not connected to a live Desktop instance. Use connect_desktop first.'}`. If `LiveContext`, call `ctx.get_model_info()` and return its result.
>
> **Update `server.py`**:
> - Import the 4 new tools from `src.tools.desktop_tools`.
> - Add all 4 to `_TOOL_REGISTRY`.
> - Update `@app.list_tools()` to return **24 Tool objects** total.
> - Input schemas: `discover_desktop` (no params), `connect_desktop` (properties: `model_name` string optional, `port` integer optional; no required fields), `disconnect` (no params), `get_desktop_model_info` (no params).
> - Update `EXPECTED_TOOL_COUNT` comment in server.py to 24."

---

**Phase 3 — Prompt 3**: DAX Execution + Live Mutation Tools (3 new tools → 27 total)

> "Create `src/tools/dax_tools.py` with 3 tools for DAX execution and live model mutation. These tools are **Live-only**: if a FileContext is active they return a clear error message instead of crashing.
>
> **`execute_dax(dax_query: str, max_rows: int = 500) -> dict`**: get active context. If `FileContext` (or no context), return `{error: 'DAX execution requires a live Desktop connection. Use connect_desktop first.'}`. If `LiveContext`, call `ctx.execute_dax(dax_query, max_rows)`. Return `{status: 'ok', columns: [...], row_count: N, truncated: bool, markdown_table: '...'}`. Catch `ConnectionError` and return `{error: str(exc)}`. Catch `ValueError` (XMLA fault = bad DAX) and return `{error: 'DAX error: ' + str(exc)}`.
>
> **`validate_measure(table_name: str, expression: str) -> dict`**: get active context. If not `LiveContext`, return error. Call `ctx.validate_expression(table_name, expression)`. Return `{valid: bool, result: str | None, error: str | None}`.
>
> **`push_measure_live(table_name: str, name: str, expression: str, format_string: str = None) -> dict`**: get active context. If not `LiveContext`, return error. Call `ctx.push_measure(table_name, name, expression, format_string)`. Return result from `push_measure`. Catch `ValueError` or `ConnectionError` and return `{error: str(exc)}`.
>
> **Update `server.py`**:
> - Import the 3 new tools from `src.tools.dax_tools`.
> - Add all 3 to `_TOOL_REGISTRY`.
> - Update `@app.list_tools()` to return **27 Tool objects** total.
> - Input schemas: `execute_dax` (required `dax_query: string`, optional `max_rows: integer`), `validate_measure` (required `table_name: string`, `expression: string`), `push_measure_live` (required `table_name: string`, `name: string`, `expression: string`; optional `format_string: string`).
> - Update `EXPECTED_TOOL_COUNT` comment to 27."

---

**Phase 3 — Prompt 4**: Test Suite + fixture XML responses + update Phase 2 tool count

> "Create the Phase 3 test infrastructure and update the Phase 2 tool-count constant.
>
> **Update `tests/test_phase2.py`**: change `EXPECTED_TOOL_COUNT = 20` to `EXPECTED_TOOL_COUNT = 27` so the tools-list test passes after Phase 3 tools are registered. No other changes to that file.
>
> **Fixture XMLA responses** — create `tests/fixtures/xmla/` with 3 files:
>
> `discover_response.xml` — a minimal valid XMLA Discover response containing one database row with `<DATABASE_ID>TestDesktopModel</DATABASE_ID>` and `<CATALOG_NAME>TestDesktopModel</CATALOG_NAME>` wrapped in the standard XMLA SOAP envelope and rowset namespace.
>
> `execute_rowset_response.xml` — a minimal XMLA Execute response in the standard `urn:schemas-microsoft-com:xml-analysis:rowset` format. Schema section: 2 columns named `[Sales].[ProductKey]` (type `xsd:string`) and `[Sales].[Amount]` (type `xsd:double`). Data section: 3 `<row>` elements with values: (`K1`, `100.0`), (`K2`, `200.0`), (`K3`, `300.0`).
>
> `execute_fault_response.xml` — a SOAP Fault response wrapping a standard XMLA error: `<faultcode>XMLAnalysisError.0x80040E14</faultcode>`, `<faultstring>The expression refers to multiple columns.</faultstring>`.
>
> **`tests/test_phase3.py`** — 10 unit tests (always run) + 5 integration tests (skip when `PBI_LIVE_PORT` env var absent):
>
> *Unit tests (use `unittest.mock.patch` throughout; start the MCP server subprocess for tools/list only):*
>
> 1. `test_tools_list_returns_27` — same subprocess+MCP-client pattern as Phase 2; assert exactly 27 tools.
> 2. `test_discover_desktop_no_workspace_folder` — patch `find_desktop_instances` to return `[]`; call `discover_desktop` via MCP; assert `result['count'] == 0` and `result['instances'] == []`. No error key in result.
> 3. `test_discover_desktop_finds_one_instance` — patch `find_desktop_instances` to return one instance dict; call `discover_desktop`; assert `count == 1` and `instances[0]['model_name']` is present.
> 4. `test_connect_desktop_no_instances_error` — patch `find_desktop_instances` to return `[]`; call `connect_desktop` with no args; assert result contains `'error'` key.
> 5. `test_connect_desktop_multiple_no_selector_error` — patch `find_desktop_instances` to return 2 instances; call `connect_desktop` with no args; assert result contains `'error'` key mentioning both instances or asking for clarification.
> 6. `test_execute_dax_without_live_context` — open a FileContext via `open_pbip_folder` (use `fixture_path`), then call `execute_dax`; assert result contains `'error'` key with 'connect_desktop' in the message.
> 7. `test_validate_measure_without_live_context` — same setup; call `validate_measure`; assert error.
> 8. `test_push_measure_live_without_live_context` — same setup; call `push_measure_live`; assert error.
> 9. `test_xmla_rowset_parsing` — directly instantiate `LiveContext(port=12345, model_name='Test')`, call `ctx._parse_xmla_rowset(xml_text)` with the content of `execute_rowset_response.xml`, assert `columns` has 2 entries and `row_count == 3`.
> 10. `test_xmla_fault_raises_value_error` — call `ctx._parse_xmla_rowset(xml_text)` with `execute_fault_response.xml` content; assert it raises `ValueError` with the fault string in the message.
>
> *Integration tests (decorated `@pytest.mark.skipif(not os.environ.get('PBI_LIVE_PORT'), reason='No live Desktop')`):*
>
> 11. `test_live_discover_desktop` — call `discover_desktop` via MCP; assert `count >= 1`.
> 12. `test_live_connect_and_model_info` — call `connect_desktop(port=int(os.environ['PBI_LIVE_PORT']))` then `get_desktop_model_info`; assert result has `model_name` and `table_count >= 0`.
> 13. `test_live_execute_dax_row` — call `execute_dax` with `EVALUATE ROW(\"TestVal\", 42)`; assert `status == 'ok'` and `row_count == 1`.
> 14. `test_live_validate_expression_valid` — call `validate_measure(table_name='Sales', expression='1 + 1')`; assert `valid == True`.
> 15. `test_live_push_measure_and_verify` — call `push_measure_live(table_name='Sales', name='Phase3 Test', expression='1+1')`; assert `status == 'pushed'`; call `execute_dax('EVALUATE {[Phase3 Test]}')` to confirm the measure exists in the live model."

---

*Generated by Claude Code — Architecture plan for `powerbi-local-mcp` — 2026-04-29*
