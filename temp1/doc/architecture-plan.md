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

---

## 8. Phase 4 Kickoff Prompts — Advanced Local Features

> **Environment note:** Phase 4 continues without pythonnet. All features target the pure-Python TMDL parser/writer that was built in Phase 2. The existing `TmdlModelState`, `FileContext`, and `_load_tmdl_model` in `src/context/manager.py` are extended — not replaced — by each prompt. All 14 new tools are file-context only (they operate on PBIP folders on disk). The TMDL formats described below are based on the PBIP serialisation produced by Power BI Desktop as of compatibility level 1605; preserve unknown blocks as raw text to survive format evolution.
>
> Running total after Phase 4: **41 tools** (27 existing + 5 RLS + 3 translations + 3 UDFs + 3 calendars).

---

**Phase 4 — Prompt 1**: RLS Roles — data model + parser + writer + 5 tools (27 → 32 tools)

> "Extend the TMDL layer and add 5 RLS role tools. No changes to Phase 1–3 behaviour; only additive.
>
> **TMDL format for roles** — roles live in `definition/roles/` (one file per role, filename = safe role name + `.tmdl`). The format is:
> ```
> role 'Role Name'
> \tmodelPermission: ReadRefresh
>
> \ttablePermission Sales
> \t\tfilterExpression: ```
> \t\t\t[Region] = "North"
> \t\t\t```
>
> \ttablePermission Customer
> \t\tfilterExpression: [Country] = "US"
> ```
> `modelPermission` values: `Read`, `ReadRefresh`, `ReadExploreData`, `Admin`. `filterExpression` can be inline (same line after `:`) or a backtick-delimited multi-line block at indent 3. A `tablePermission` block with no `filterExpression` means the role can see all rows of that table.
>
> **`src/tmdl/models.py`** — add two new dataclasses at the bottom:
> ```python
> @dataclass
> class RlsFilter:
>     table_name: str
>     filter_expression: str | None = None   # None = no row filter (full access)
>
> @dataclass
> class Role:
>     name: str
>     model_permission: str = "Read"          # Read | ReadRefresh | ReadExploreData | Admin
>     filters: list[RlsFilter] = field(default_factory=list)
> ```
> Extend `TmdlModelState` — add field: `roles: dict[str, Role] = field(default_factory=dict)`.
>
> **`src/tmdl/parser.py`** — add `parse_role_file(path: Path) -> Role`. State machine (same tab-indent conventions as the table parser):
> - Line at indent 0 starting with `role ` → extract name via `_strip_quotes`.
> - Line at indent 1 starting with `modelPermission:` → store value.
> - Line at indent 1 starting with `tablePermission ` → begin a new `RlsFilter` for the named table; call `_collect_filter_expression(lines, i, n)` to read the optional expression (see below).
> - Inline `filterExpression:` (same line after `tablePermission` block header) and backtick-block multiline both need handling.
>
> Add helper `_collect_filter_expression(lines, i, n) -> tuple[str | None, int]`:
> - Advance past `tablePermission` header line; scan indent-2 lines.
> - If the next non-blank indent-2 line is `filterExpression: \`\`\`` → collect indent-3 lines until closing `\`\`\`` or until indent drops to ≤ 1; join with `\n` and return.
> - If the next non-blank indent-2 line is `filterExpression: <expr>` (inline) → return `<expr>`.
> - If no `filterExpression` line before indent drops to ≤ 1 → return `None`.
>
> Add `parse_roles_folder(roles_dir: Path) -> dict[str, Role]` — glob `roles_dir/*.tmdl`, call `parse_role_file` for each, build `{role.name: role}` dict. Return `{}` if folder absent.
>
> **`src/tmdl/writer.py`** — add `role_to_tmdl_text(role: Role) -> str`:
> - Header: `role {_quote(role.name)}`
> - `\tmodelPermission: {role.model_permission}`
> - For each filter: emit `\ttablePermission {_quote(f.table_name)}`; if `f.filter_expression` is not None emit the backtick block at indent 2/3:
>   ```
>   \t\tfilterExpression: ```
>   \t\t\t{expr line 1}
>   \t\t\t{expr line 2}
>   \t\t\t```
>   ```
>   (Use the backtick-block form even for single-line expressions — it is always valid and avoids quoting edge cases.)
>
> **`src/context/manager.py`** — update `_load_tmdl_model`:
> - Add `from src.tmdl.parser import parse_roles_folder` import.
> - After loading relationships, add: `roles_dir = definition_path / "roles"` → `roles = parse_roles_folder(roles_dir)`.
> - Include `roles=roles` in the `TmdlModelState(...)` constructor.
>
> Update `FileContext.save()`:
> - Add `from src.tmdl.writer import role_to_tmdl_text` import.
> - After writing tables/relationships, write roles: `roles_dir = self.definition_path / "roles"`. If `model_state.roles` is non-empty: `roles_dir.mkdir(exist_ok=True)`; for each role write `roles_dir / f"{_safe_filename(role.name)}.tmdl"` with `role_to_tmdl_text(role)`. Delete stale `.tmdl` files in `roles_dir` for removed roles (same pattern as tables). If `model_state.roles` is empty and `roles_dir.exists()`, leave it alone (don't delete — there may be pre-existing role files not yet loaded).
>
> **`src/tools/role_tools.py`** — implement 5 tools (all require `FileContext`; return `{'error': 'RLS tools require an open PBIP folder. Use open_pbip_folder first.'}` if context is `LiveContext` or absent):
>
> - `list_roles() -> dict` — return `{roles: [{name, model_permission, filter_count}], count}`.
> - `create_role(name: str, model_permission: str = 'Read') -> dict` — raise `ValueError` if name exists. Add `Role(name, model_permission)` to `model_state.roles`, set dirty. Return `{status: 'created', role: name, reminder: 'Call save_model to persist.'}`.
> - `update_role(role_name: str, new_name: str | None = None, model_permission: str | None = None) -> dict` — look up role, apply changes (rename key in dict if name changes), set dirty. Return `{status: 'updated', role: new_name or role_name}`.
> - `add_rls_filter(role_name: str, table_name: str, filter_expression: str | None = None) -> dict` — look up role; if a filter for `table_name` already exists, replace it; otherwise append. Set dirty. Return `{status: 'set', role: role_name, table: table_name}`.
> - `delete_rls_filter(role_name: str, table_name: str) -> dict` — remove the filter for `table_name` from the role's filters list. Raise `ValueError` if not found. Set dirty. Return `{status: 'deleted', role: role_name, table: table_name}`.
>
> **`server.py`** — import the 5 tools from `src.tools.role_tools`. Add all 5 to `_TOOL_REGISTRY`. Add 5 `Tool` objects to `_TOOLS` (total now **32**). Input schemas: `list_roles` (no params); `create_role` (required `name: string`, optional `model_permission: string` default `'Read'`); `update_role` (required `role_name: string`, optional `new_name: string`, optional `model_permission: string`); `add_rls_filter` (required `role_name: string`, `table_name: string`, optional `filter_expression: string`); `delete_rls_filter` (required `role_name: string`, `table_name: string`)."

---

**Phase 4 — Prompt 2**: Culture Translations — data model + parser + writer + 3 tools (32 → 35 tools)

> "Extend the TMDL layer with culture/translation support and add 3 translation tools.
>
> **TMDL format for cultures** — cultures live in `definition/cultures/` (one file per locale, e.g., `fr-FR.tmdl`). Power BI serialises translations using a flat list of `translatedLabel` entries keyed by object path. The format is:
> ```
> culture 'fr-FR'
>
> \ttranslatedLabel 'table.Sales'
> \t\ttranslatedCaption: Ventes
>
> \ttranslatedLabel 'table.Sales.column.Amount'
> \t\ttranslatedCaption: Montant
>
> \ttranslatedLabel 'table.Sales.measure.Total Amount'
> \t\ttranslatedCaption: 'Total des Montants'
>
> \ttranslatedLabel 'table.Sales.measure.Total Amount.description'
> \t\ttranslatedCaption: Description traduite
>
> \tannotation SomeKey = SomeValue
> ```
> Object path conventions:
> - Table: `table.{TableName}`
> - Column: `table.{TableName}.column.{ColumnName}`
> - Measure: `table.{TableName}.measure.{MeasureName}`
> Names with spaces are NOT quoted in the path string itself (the whole path is quoted). `translatedCaption` values that contain spaces are bare (Power BI quotes them only if necessary). Preserve unknown blocks (e.g., `annotation`, `linguisticMetadata`) as raw text so round-trips don't corrupt them.
>
> **`src/tmdl/models.py`** — add two new dataclasses:
> ```python
> @dataclass
> class Translation:
>     object_path: str          # e.g. "table.Sales.measure.Total Amount"
>     caption: str
>
> @dataclass
> class Culture:
>     locale: str               # e.g. "fr-FR"
>     translations: list[Translation] = field(default_factory=list)
>     _raw_extras: str = ""     # annotation blocks + anything not parsed
> ```
> Extend `TmdlModelState` — add: `cultures: dict[str, Culture] = field(default_factory=dict)`.
>
> **`src/tmdl/parser.py`** — add `parse_culture_file(path: Path) -> Culture` and `parse_cultures_folder(cultures_dir: Path) -> dict[str, Culture]`:
> - `parse_culture_file`: line-by-line state machine.
>   - Indent-0 line starting `culture ` → extract locale via `_strip_quotes`.
>   - Indent-1 `translatedLabel ` → extract path (strip quotes), then read indent-2 `translatedCaption:` value. Strip leading/trailing single quotes from the caption value if present.
>   - Any other indent-1 block (e.g., `annotation`, `linguisticMetadata`) → `_collect_raw_block(lines, i, n, stop_indent=1)` and append to `_raw_extras`.
> - `parse_cultures_folder`: glob `cultures_dir/*.tmdl`, parse each, return `{culture.locale: culture}`. Return `{}` if folder absent.
>
> **`src/tmdl/writer.py`** — add `culture_to_tmdl_text(culture: Culture) -> str`:
> - Header: `culture {_quote(culture.locale)}`
> - Blank line.
> - For each translation: emit `\ttranslatedLabel {_quote(t.object_path)}` then `\t\ttranslatedCaption: {t.caption}` then blank line.
> - If `culture._raw_extras` is non-empty, append it after the translations.
>
> **`src/context/manager.py`** — update `_load_tmdl_model`:
> - Add `from src.tmdl.parser import parse_cultures_folder` import.
> - After loading roles: `cultures_dir = definition_path / "cultures"` → `cultures = parse_cultures_folder(cultures_dir)`.
> - Include `cultures=cultures` in `TmdlModelState(...)`.
>
> Update `FileContext.save()`:
> - After writing roles, write cultures: `cultures_dir = self.definition_path / "cultures"`. If non-empty: `cultures_dir.mkdir(exist_ok=True)`; for each culture write `cultures_dir / f"{culture.locale}.tmdl"` with `culture_to_tmdl_text(culture)`. Delete stale `.tmdl` files for removed locales.
>
> **`src/tools/translation_tools.py`** — implement 3 tools (all require `FileContext`; return error dict if `LiveContext` or no context):
>
> - `list_cultures() -> dict` — return `{cultures: [{locale, translation_count}], count}`.
>
> - `add_translation(locale: str, object_type: str, table_name: str, object_name: str | None = None, caption: str) -> dict`:
>   - `object_type` must be one of `'table'`, `'column'`, `'measure'`. Raise `ValueError` for others.
>   - Build `object_path`:
>     - `'table'` → `f"table.{table_name}"`
>     - `'column'` → `f"table.{table_name}.column.{object_name}"`
>     - `'measure'` → `f"table.{table_name}.measure.{object_name}"`
>   - Get or create `Culture` for `locale` in `model_state.cultures`.
>   - If a `Translation` for `object_path` already exists in the culture, update its caption. Otherwise append a new one.
>   - Set dirty. Return `{status: 'set', locale, object_path, caption}`.
>
> - `bulk_add_translations(locale: str, translations: list[dict]) -> dict`:
>   - `translations` is a list of `{object_type, table_name, object_name (optional), caption}` dicts — same semantics as `add_translation`.
>   - Process each entry using the same path-building logic; accumulate errors.
>   - Set dirty once after all are processed. Return `{status: 'ok', locale, set_count: N, errors: [...]}`.
>
> **`server.py`** — import the 3 tools from `src.tools.translation_tools`. Add to `_TOOL_REGISTRY` and `_TOOLS`. Total now **35**. Input schemas: `list_cultures` (no params); `add_translation` (required `locale: string`, `object_type: string`, `table_name: string`, `caption: string`; optional `object_name: string`); `bulk_add_translations` (required `locale: string`, `translations: array of objects` with items having required `object_type`, `table_name`, `caption` and optional `object_name`)."

---

**Phase 4 — Prompt 3**: DAX UDF Tools — 3 tools leveraging existing measure infrastructure (35 → 38 tools)

> "Add 3 DAX UDF tools. UDFs in Power BI are stored as regular measures whose expression is a DAX function definition (the expression starts with a parameter list `(param1 [: Type]...) =>` or is a zero-parameter function body). There is no separate TMDL object type — UDFs live in table `.tmdl` files alongside ordinary measures. The UDF tools therefore reuse `src/tmdl/models.Measure`, the existing parser, and the existing writer without any TMDL-layer changes. The tools add UDF-specific validation and a discoverable surface.
>
> **UDF expression recognition** — an expression is a UDF if, when stripped of leading/trailing whitespace and after stripping any leading multi-line `VAR` / `RETURN` preamble, the trimmed text matches `^\s*\(` (starts with an open parenthesis) or is a zero-parameter body following `() =>`. The simplest reliable heuristic: `expression.lstrip().startswith('(')` OR `re.search(r'\)\s*=>', expression)` is truthy.
>
> **`src/tools/udf_tools.py`** — implement 3 tools (require `FileContext` — return error dict if `LiveContext` or no context; reference `resource://dax_udf_instructions_and_examples` in error messages to guide the user):
>
> - `list_udfs() -> dict`:
>   - Iterate `ctx.model_state.tables.items()`. For each table, scan measures; collect those whose expression matches the UDF heuristic above.
>   - Return `{udfs: [{table, name, parameter_preview, display_folder}], count}`.
>   - `parameter_preview`: the substring from the first `(` to the first `)` inclusive, truncated to 80 chars (e.g., `"(radius : Scalar Numeric)"`).
>
> - `create_udf(table_name: str, name: str, expression: str, description: str | None = None, display_folder: str | None = None) -> dict`:
>   - Validate that `expression` matches the UDF heuristic. If not, return `{error: "Expression does not look like a DAX UDF definition. Expected '(param1 [: Type]...) => body' syntax. See resource://dax_udf_instructions_and_examples."}`.
>   - Look up `table_name` in `model_state.tables`; raise `ValueError` if absent.
>   - Raise `ValueError` if a measure with `name` already exists in that table (use `create_measure` semantics — no silent overwrite).
>   - Append `Measure(name=name, expression=expression, description=description, display_folder=display_folder)` to `t.measures`. Set dirty.
>   - Return `{status: 'created', table: table_name, udf: name, reminder: 'Call save_model to persist.'}`.
>
> - `update_udf(table_name: str, udf_name: str, new_expression: str | None = None, new_name: str | None = None, description: str | None = None, display_folder: str | None = None) -> dict`:
>   - Look up the measure in the table (raise `ValueError` if not found).
>   - Validate the current measure IS a UDF (expression matches heuristic). If not, return `{error: "Measure '{udf_name}' does not appear to be a UDF. Use update_measure for regular measures."}`.
>   - If `new_expression` provided, validate it is also a UDF expression (same check). On failure, return the same guidance error.
>   - Apply changes (delegate to the same field-patching logic as `update_measure`). Set dirty.
>   - Return `{status: 'updated', table: table_name, udf: new_name or udf_name}`.
>
> **`server.py`** — import the 3 tools from `src.tools.udf_tools`. Add to `_TOOL_REGISTRY` and `_TOOLS`. Total now **38**. Input schemas: `list_udfs` (no params); `create_udf` (required `table_name: string`, `name: string`, `expression: string`; optional `description: string`, `display_folder: string`); `update_udf` (required `table_name: string`, `udf_name: string`; optional `new_expression: string`, `new_name: string`, `description: string`, `display_folder: string`)."

---

**Phase 4 — Prompt 4**: Calendar Column Groups — data model + parser + writer + 3 tools + full test suite (38 → 41 tools)

> "Add calendar column group support and 3 tools, then write the full Phase 4 test suite.
>
> **TMDL format for calendar column groups** — calendars are defined per table, appended to the table's `.tmdl` file after partitions/annotations. Power BI serialises each calendar as a `calendarColumnGroup` block at indent 1 within the `table` block. A `columnGroup` sub-block describes each time unit. The `TimeRelated` group (for non-standard time columns) uses `timeUnit: Unknown` with `associatedColumn` entries and no `primaryColumn`.
>
> ```
> \tcalendarColumnGroup 'Gregorian Calendar'
>
> \t\tcolumnGroup Year
> \t\t\ttimeUnit: Year
> \t\t\tprimaryColumn: Year
>
> \t\tcolumnGroup Quarter
> \t\t\ttimeUnit: Quarter
> \t\t\tprimaryColumn: 'Year Quarter Number'
>
> \t\t\tassociatedColumn 'Year Quarter'
>
> \t\tcolumnGroup Month
> \t\t\ttimeUnit: Month
> \t\t\tprimaryColumn: 'Month Number'
>
> \t\t\tassociatedColumn Month
> \t\t\t\ttimeUnit: MonthOfYear
>
> \t\tcolumnGroup Date
> \t\t\ttimeUnit: Date
> \t\t\tprimaryColumn: Date
>
> \t\tcolumnGroup TimeRelated
> \t\t\ttimeUnit: Unknown
>
> \t\t\tassociatedColumn RelativeMonth
> \t\t\tassociatedColumn Season
> ```
> Rules: `primaryColumn` names containing spaces use single-quote wrapping. `associatedColumn` lines at indent 3 name a column; an optional `timeUnit:` at indent 4 sets the partial unit (e.g., `MonthOfYear`) for that associated column. The `TimeRelated` group has no `primaryColumn`; all its columns are `associatedColumn` entries.
>
> **`src/tmdl/models.py`** — add new dataclasses:
> ```python
> @dataclass
> class AssociatedColumn:
>     name: str
>     time_unit: str | None = None    # e.g. "MonthOfYear"; None for TimeRelated columns
>
> @dataclass
> class ColumnGroup:
>     time_unit: str                  # "Year", "Month", "Date", "Unknown", etc.
>     primary_column: str | None = None   # None for TimeRelated groups
>     associated_columns: list[AssociatedColumn] = field(default_factory=list)
>
> @dataclass
> class CalendarColumnGroup:
>     name: str
>     column_groups: list[ColumnGroup] = field(default_factory=list)
> ```
> Extend `Table` — add field: `calendar_column_groups: list[CalendarColumnGroup] = field(default_factory=list)`.
>
> **`src/tmdl/parser.py`** — extend `parse_table_file` to recognise `calendarColumnGroup` blocks at indent 1:
> - Pattern: `s.startswith('calendarColumnGroup ')` → extract name via `_strip_quotes`, then collect the body with a new `_parse_calendar_column_group(lines, i, n)` helper.
> - `_parse_calendar_column_group(lines, i, n) -> tuple[CalendarColumnGroup, int]`:
>   - `i` starts at the `calendarColumnGroup` header line; advance past it.
>   - Scan indent-2 lines; each `columnGroup {name}` starts a new `ColumnGroup` block.
>   - Inside a `columnGroup` block (indent-3 lines): `timeUnit:` sets the unit; `primaryColumn:` sets primary (strip quotes); `associatedColumn {name}` (at indent 3) starts an `AssociatedColumn`; if the next line is indent-4 `timeUnit:`, set the associated column's `time_unit`.
>   - Stop when indent drops to ≤ 1 (end of `calendarColumnGroup`).
>   - Return `(CalendarColumnGroup(name=name, column_groups=[...]), i)`.
> - In `parse_table_file`, append the parsed `CalendarColumnGroup` to `calendar_column_groups` list.
>
> **`src/tmdl/writer.py`** — extend `table_to_tmdl_text` to emit calendar column groups after annotations:
> - For each `cal` in `table.calendar_column_groups`, call `_calendar_to_tmdl_lines(cal)` and extend `parts`.
>
> Add `_calendar_to_tmdl_lines(cal: CalendarColumnGroup) -> list[str]`:
> ```
> \tcalendarColumnGroup {_quote(cal.name)}
> (blank)
> \t\tcolumnGroup {group.time_unit}     ← use time_unit as the block name
> \t\t\ttimeUnit: {group.time_unit}
> \t\t\tprimaryColumn: {_quote(group.primary_column)}  ← omit if None (TimeRelated)
> \t\t\t(blank)
> \t\t\tassociatedColumn {_quote(ac.name)}
> \t\t\t\ttimeUnit: {ac.time_unit}     ← omit line if None
> (blank between column groups)
> ```
>
> **`src/tools/calendar_tools.py`** — implement 3 tools (require `FileContext`; see Phase 4 Prompts 1–2 for the standard error-return pattern; reference `resource://calendar_instructions_and_examples` in error messages where helpful):
>
> - `list_calendars() -> dict`:
>   - Iterate tables; collect all `CalendarColumnGroup` objects.
>   - Return `{calendars: [{calendar_name, table_name, column_group_count, time_units: [list of timeUnit strings]}], count}`.
>
> - `create_calendar(table_name: str, calendar_name: str, column_groups: list[dict]) -> dict`:
>   - Look up `table_name`; raise `ValueError` if absent.
>   - Validate `calendar_name` is unique across **all** tables in the model (the calendar guide states calendar names must be model-unique). Raise `ValueError` with a clear message if a duplicate exists.
>   - `column_groups` is a list of dicts with keys `time_unit` (required), `primary_column` (required unless `time_unit == 'Unknown'`), `associated_columns` (optional list of `{name, time_unit (optional)}`).
>   - Validate all `time_unit` values against the allowed list from `calendar_instructions_and_examples.md`: `Unknown`, `Year`, `Quarter`, `QuarterOfYear`, `Month`, `MonthOfYear`, `MonthOfQuarter`, `Week`, `WeekOfYear`, `WeekOfQuarter`, `WeekOfMonth`, `Date`, `DayOfYear`, `DayOfQuarter`, `DayOfMonth`, `DayOfWeek`. Return `{error: 'Invalid timeUnit: {val}. ...'}` on first failure.
>   - Validate no duplicate `time_unit` within the same calendar. The `Unknown` unit may only appear once.
>   - Build `CalendarColumnGroup(name=calendar_name, column_groups=[...])` and append to `table.calendar_column_groups`. Set dirty.
>   - Return `{status: 'created', table: table_name, calendar: calendar_name, reminder: 'Call save_model to persist.'}`.
>
> - `update_calendar_column_group(table_name: str, calendar_name: str, column_groups: list[dict]) -> dict`:
>   - Look up table and calendar (raise `ValueError` if not found). Apply the same validations as `create_calendar` (uniqueness check excludes the calendar being updated). Replace `cal.column_groups` with the new list. Set dirty.
>   - Return `{status: 'updated', table: table_name, calendar: calendar_name}`.
>
> **`server.py`** — import the 3 tools from `src.tools.calendar_tools`. Add to `_TOOL_REGISTRY` and `_TOOLS`. Total now **41**. Input schemas: `list_calendars` (no params); `create_calendar` (required `table_name: string`, `calendar_name: string`, `column_groups: array`; items have required `time_unit: string`, optional `primary_column: string`, optional `associated_columns: array` of `{name: string, time_unit: string (optional)}`); `update_calendar_column_group` (same required fields as `create_calendar`).
>
> **`tests/test_phase2.py`** and **`tests/test_phase3.py`** — update `EXPECTED_TOOL_COUNT = 27` → `41` in phase3, and `20` stays in phase2 (phase2 test counts phase2 tools only).
> Actually: update **`tests/test_phase3.py`** line `EXPECTED_TOOL_COUNT = 27` → `41`. The phase2 test file's count of 20 tests only phase2 tools and doesn't change.
>
> **`tests/test_phase4.py`** — test suite using the same TMDL fixture (`tests/fixtures/TestModel.SemanticModel`) copied to `tmp_path` via `shutil.copytree`. All tests call tool functions directly (no MCP subprocess) — same pattern as the mocked tests in `test_phase3.py` that use `clean_context` fixture plus `ContextManager.get().open_file_context(...)`. Add `clean_context` fixture (reset `ContextManager._instance = None` before each test).
>
> Write 12 tests:
>
> 1. `test_tools_list_returns_41` — start MCP subprocess, assert `len(result.tools) == 41`.
> 2. `test_create_and_list_role` — `open_pbip_folder`, `create_role('Analysts', 'Read')`, `list_roles()` → count=1, name='Analysts'.
> 3. `test_add_rls_filter` — after `create_role`, `add_rls_filter('Analysts', 'Sales', "[Region] = 'North'")` → status='set'; `list_roles()` → filter_count=1.
> 4. `test_delete_rls_filter` — add then delete RLS filter → filter_count back to 0.
> 5. `test_update_role_permission` — `create_role('Admins', 'Read')`, `update_role('Admins', model_permission='ReadRefresh')`, `list_roles()` → role has `model_permission='ReadRefresh'`.
> 6. `test_role_roundtrip_save` — create role with filter, `save_model()`, re-open folder, `list_roles()` → role and filter survive the roundtrip.
> 7. `test_add_and_list_translation` — `add_translation('fr-FR', 'table', 'Sales', caption='Ventes')` → status='set'; `list_cultures()` → count=1, locale='fr-FR'.
> 8. `test_translation_roundtrip_save` — add translations, save, reopen, `list_cultures()` → translations survive.
> 9. `test_bulk_add_translations` — `bulk_add_translations('de-DE', [...3 entries...])` → set_count=3, errors=[].
> 10. `test_create_and_list_udf` — `create_udf('Sales', 'DoubleIt', '(x : Scalar Numeric) => x * 2')` → status='created'; `list_udfs()` → count=1, name='DoubleIt'.
> 11. `test_udf_invalid_expression_rejected` — `create_udf('Sales', 'Bad', 'SUM(Sales[Amount])')` → result contains 'error' key.
> 12. `test_create_and_list_calendar` — add column `Date` (type `dateTime`) and `Year` (type `int64`) to Sales table first; then `create_calendar('Sales', 'My Calendar', [{'time_unit': 'Year', 'primary_column': 'Year'}, {'time_unit': 'Date', 'primary_column': 'Date'}])` → status='created'; `list_calendars()` → count=1, calendar_name='My Calendar'."

---

*Phase 4 prompts appended 2026-04-30*
