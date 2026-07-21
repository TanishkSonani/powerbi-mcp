# powerbi-local-mcp — User Guide

## Overview

A local MCP server for Power BI modeling that supports two modes:
- File context: operate on a PBIP folder (`.SemanticModel/definition`) using TMDL files.
- Live context: discover and connect to a running Power BI Desktop instance via local XMLA.

This guide explains how to set up, run, and integrate the MCP server with clients (Claude Desktop, Claude Code, Codex Desktop, terminal/TUI tools), lists the available tools and commands, and gives best-practice tips.

## Prerequisites

- Windows (Power BI Desktop is Windows-only for local XMLA embedding).
- Python 3.11+ (the project was built and tested on Python 3.11+; 3.14 shown working here).
- Power BI Desktop installed and open for Live features.
- Optional but recommended: Visual C++ redistributable for pythonnet, and .NET runtime compatible with the installed `pythonnet`.
- If you plan to use Live (XMLA/DAX execution), ensure Power BI Desktop can expose the local Analysis Services workspace (default behavior).

## Quick setup (local development)

1. Open a PowerShell terminal in the repository root (`bimcp`):

```powershell
cd "c:\Users\tanis\OneDrive\Desktop\PBIMCP\temp1\bimcp"
```

2. Create a Python virtual environment and install dependencies:

```powershell
C:/path/to/python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

3. Run the full test suite (recommended to verify environment):

```powershell
.\.venv\Scripts\python.exe -m pytest -rA
```

## Starting the MCP server

- Run the server directly (stdio transport):

```powershell
.\.venv\Scripts\python.exe server.py
```

Note: `server.py` uses the stdio MCP transport. Many clients (including test harnesses and some desktop clients) start the server as a child process and communicate over stdio.

## Integrating clients

General approach: Clients that implement the Model Context Protocol (MCP) can connect via stdio or other supported transports. The common pattern for desktop GUI clients is to provide a command that starts the server process (the server uses stdio).

- Claude Desktop / Claude Code:
  - Create a new local tool/process configuration that launches the Python interpreter with `server.py` as the argument. Example:

    - Command: `C:/path/to/python.exe`
    - Arguments: `C:\path\to\bimcp\server.py`
    - Working directory: `C:\path\to\bimcp`

  - Start the tool from the client — it will run the server as a child process and communicate with it over stdio. The client should then enumerate prompts/resources and call tools.

- Codex Desktop / Other MCP-capable GUI clients:
  - Same pattern: configure a local command that launches `server.py` with the system Python.

- Terminal / TUI / Scripts:
  - Use the `mcp` Python client libraries or the `mcp` CLI to start a stdio client or call tools programmatically. Example small script (already included as `mcp_check.py`) shows how to start the server as a subprocess and call tools using `mcp.client.stdio`.

## Available MCP tools (summary)

The server registers a set of synchronous tools. Key categories and examples:

- Model management
  - `open_pbip_folder`: open a PBIP model folder (.SemanticModel/definition)
  - `get_model_info`: read top-level metadata
  - `save_model`: write in-memory changes back to TMDL files

- Tables / Columns / Measures / Relationships
  - `list_tables`, `get_table`, `create_table`, `update_table`, `delete_table`
  - `list_columns`, `create_column`, `update_column`, `delete_column`
  - `list_measures`, `get_measure`, `create_measure`, `update_measure`, `delete_measure`
  - `list_relationships`, `create_relationship`, `delete_relationship`

- Desktop (Live) integration
  - `discover_desktop`: enumerate running Power BI Desktop workspaces and XMLA ports
  - `connect_desktop`: connect to a chosen running Desktop instance
  - `disconnect`: close the live connection
  - `get_desktop_model_info`: read live metadata

- DAX / Live operations
  - `execute_dax`: run a DAX query (EVALUATE ...) against the live model
  - `validate_measure`: validate/evaluate a measure expression in the live model
  - `push_measure_live`: create/replace a measure in the live model (no disk write)

- Phase 4 (RLS, Cultures, UDFs, Calendars)
  - Roles: `list_roles`, `create_role`, `update_role`, `add_rls_filter`, `delete_rls_filter`
  - Translations/cultures: `list_cultures`, `add_translation`, `bulk_add_translations`
  - UDFs: `list_udfs`, `create_udf`, `update_udf`, `delete_udf`
  - Calendar groups: `list_calendars`, `create_calendar`, `update_calendar_column_group`, `delete_calendar`

Full tool schemas are defined in `server.py` and are exposed to clients via `list_tools`.

## Example: programmatic client usage (Python)

A minimal async example (see `mcp_check.py`):

```python
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

# Start server.py as a child process and obtain a ClientSession
params = StdioServerParameters(command=sys.executable, args=[server_py])
async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        # call a tool
        result = await session.call_tool("discover_desktop", {})
```

## Live vs File context (guidelines)

- File context (PBIP folder): safe for on-disk edits. Use `open_pbip_folder` then the CRUD tools (`create_measure`, `update_column`, etc.), then `save_model` to write changes.
- Live context (Power BI Desktop): use for executing DAX (`execute_dax`, `validate_measure`) and for making ephemeral changes visible in Desktop (use `push_measure_live`). Live changes do not write back to PBIP files.
- Tools are mode-agnostic; the Context Manager decides the execution path. Some tools are Live-only (e.g., `execute_dax`).

## Best practices and tips

- Keep PBIP folders under version control; prefer making changes in File context and saving often.
- Use `validate_measure` before committing complex DAX to disk.
- For long-running DAX queries, prefer `execute_dax` with reasonable `max_rows` and rely on server-side timeouts; the server offloads blocking DAX calls to worker threads.
- When multiple Power BI Desktop windows are open, use `discover_desktop` to enumerate instances and choose by model name/port.
- If you need to make temporary analysis changes, use `push_measure_live` to avoid touching files.

## Troubleshooting

- Missing `pythonnet` / CLR errors: ensure a compatible `pythonnet` and .NET runtime are installed. On Windows use the matching architecture (64-bit Python + 64-bit .NET).
- No Desktop instances found: verify Power BI Desktop is running and that `AnalysisServicesWorkspaces` contains `msmdsrv.port.txt` files. Also try the fallback netstat-based discovery; ensure firewall is not blocking localhost.
- DAX execution errors: use `validate_measure` to inspect expression-level errors before pushing to the model.

## Important files

- Architecture plan: [doc/architecture-plan.md](doc/architecture-plan.md#L1)
- Current progress notes: [doc/progress.txt](doc/progress.txt#L1)
- Server entrypoint: [server.py](server.py#L1)
- Helper script that starts the server and calls tools: [mcp_check.py](mcp_check.py#L1)

## Next steps & recommendations

- If you want, I can:
  - Run the server continuously in a background terminal and keep it live for interactive testing.
  - Add a `README-quickstart.md` with copy-paste commands tailored to users on your team.
  - Expand the guide with screenshots or example flows for Claude Desktop configuration if you provide the exact UI/version you use.

---

Generated by an automated check of the repository. If you'd like adjustments or more client-specific integration instructions, tell me which client and version to document and I'll update the guide.
