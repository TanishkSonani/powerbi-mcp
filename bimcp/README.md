# powerbi-local-mcp

Universal local MCP server for Power BI modeling. Connects to any MCP-compatible client — Claude Desktop, custom agents, or IDEs. **No cloud. No Fabric. No REST API.** Strictly local Power BI Desktop files and PBIP folders.

## Current Phase: 1 — Foundation & Resources

Phase 1 exposes the 4 Microsoft reference guides as native MCP resources. The LLM automatically receives DAX, TMDL, calendar, and UDF guidance as context.

| Resource URI | Content |
|---|---|
| `resource://dax_query_instructions_and_examples` | DAX query rules + 13 worked examples |
| `resource://dax_udf_instructions_and_examples` | UDF type system, param modes, examples |
| `resource://calendar_instructions_and_examples` | Calendar column groups, fiscal calendars |
| `resource://powerbi_project_instructions` | PBIP folder structure, TMDL format |

| Prompt | Purpose |
|---|---|
| `connect_desktop` | Connect to a running Power BI Desktop file |
| `connect_pbip` | Open a Power BI Project (PBIP) folder |

---

## Prerequisites

- Python 3.11+ (tested on 3.14)
- pip

## Installation

```bash
cd bimcp
pip install -e .
```

Or without installing (direct run):

```bash
pip install mcp pyyaml pydantic psutil python-dotenv
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
      "command": "C:\\Users\\tanis\\AppData\\Local\\Programs\\Python\\Python314\\python.exe",
      "args": [
        "C:\\Users\\tanis\\OneDrive\\Desktop\\PBIMCP\\bimcp\\server.py"
      ]
    }
  }
}
```

> **Tip:** Adjust the Python path if your installation differs. Run `where python` (Windows) to find yours.

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
│   ├── resources/
│   │   └── provider.py           # Scans md/ folder, serves as MCP resources
│   └── prompts/
│       └── connection_prompts.py # connect_desktop + connect_pbip prompts
└── tests/
    └── test_phase1.py            # Smoke tests for Phase 1
```

---

## Roadmap

| Phase | Status | Description |
|---|---|---|
| **1 — Foundation & Resources** | ✅ Complete | MCP server, resource guides, prompts |
| **2 — TMDL File Manipulation** | Planned | Read/write PBIP folders via TOM + TmdlSerializer |
| **3 — Live Desktop Integration** | Planned | Port discovery, DAX execution via local XMLA |
| **4 — Advanced Local Features** | Planned | RLS, translations, UDFs, calendar column groups |
