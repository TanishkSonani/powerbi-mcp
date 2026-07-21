# Power BI MCP — Guide for Analysts

This connects your AI assistant (Claude Desktop) directly to your Power BI models, so you can
inspect and change them **by asking in plain English** instead of clicking through Power BI Desktop.

Everything runs **locally**. No cloud, no Fabric, no data leaves your machine.

---

## What you can actually do with it

| You want to… | Just ask |
|---|---|
| Understand a model you inherited | *"List every table and measure, and show me how they're related."* |
| Check a DAX measure is correct | *"Does `DIVIDE([Late Deliveries], [Total Deliveries])` work? What does it return?"* |
| Find where a column is used | *"Which measures reference `sales_qty`?"* |
| Sanity-check the numbers | *"Run a DAX query for total sales by plant."* |
| Add or fix a measure | *"Add a measure `Return Rate %` = returns ÷ sales, formatted as a percentage."* |
| Document the model | *"Write a summary of every measure grouped by the table it uses."* |
| Set up row-level security | *"Create a role `Plant Managers` that only sees `PLANT-001`."* |
| Translate for another region | *"Add French captions for all measures."* |
| Audit before shipping | *"Are any relationships inactive? Any measures referencing missing columns?"* |

---

## Two ways to connect (this matters)

### 🔴 Live — connected to Power BI Desktop
> *"Connect to my open Power BI Desktop model."*

Talks to the model **currently open** in Power BI Desktop.

- ✅ **Run real DAX queries and see actual numbers**
- ✅ Validate a measure by genuinely evaluating it
- ✅ Changes appear immediately — no save, no reopen
- ❗ Power BI Desktop must be open with your file loaded

**Best for:** exploring data, verifying DAX, checking numbers are right.

### 📁 File — a saved PBIP folder
> *"Open the PBIP folder at C:\Reports\Sales.SemanticModel"*

Reads the saved model files on disk (a PBIP / `.SemanticModel` folder).

- ✅ Full editing of everything — tables, columns, measures, relationships, RLS, translations
- ✅ Works with Power BI Desktop **closed**
- ✅ Changes are held in memory until you say *"save the model"* — nothing is written until then
- ❗ Cannot run DAX (a folder of text files has no engine to run queries)

**Best for:** bulk edits, refactoring, version-controlled changes.

> **Rule of thumb:** *Live* to check numbers. *File* to make changes.

---

## Full capability list

Legend — **file+live** works either way · **file** saved folder only · **live** Desktop only

### Getting connected
| Ask for | Tool | Where |
|---|---|---|
| Find open Power BI Desktop windows | `discover_desktop` | live |
| Connect to the open model | `connect_desktop` | live |
| Open a saved PBIP folder | `open_pbip_folder` | file |
| Model summary (tables/measures/relationships) | `get_model_info` | file+live |
| Finish up | `disconnect` | any |

### Exploring the model
| Ask for | Tool | Where |
|---|---|---|
| All tables | `list_tables` | file+live |
| One table in full detail | `get_table` | file+live |
| Columns in a table | `list_columns` | file+live |
| All measures | `list_measures` | file+live |
| One measure's DAX | `get_measure` | file+live |
| Relationships (incl. inactive) | `list_relationships` | file+live |
| Security roles | `list_roles` | file+live |
| Languages/translations | `list_cultures` | file+live |
| Custom functions | `list_udfs` | file+live |
| Date/calendar groups | `list_calendars` | file+live |

### Querying and checking DAX
| Ask for | Tool | Where |
|---|---|---|
| Run a DAX query, see results | `execute_dax` | **live only** |
| Check a DAX expression | `validate_measure` | file+live* |

\* **Live** actually evaluates it and returns the number. **File** does a static check — balanced
brackets and that every `'Table'[Column]` exists. It will catch a typo'd column name, but it
cannot tell you the *value*.

### Changing the model
| Ask for | Tool | Where |
|---|---|---|
| Add / edit / remove a measure | `create_measure`, `update_measure`, `delete_measure` | file+live |
| Add / edit / remove a column | `create_column`, `update_column`, `delete_column` | file+live† |
| Rename or remove a table | `update_table`, `delete_table` | file+live |
| Create a table | `create_table` | file only |
| Add / remove relationships | `create_relationship`, `delete_relationship` | file+live |
| Security roles & RLS filters | `create_role`, `update_role`, `add_rls_filter`, `delete_rls_filter` | file+live |
| Translations | `add_translation`, `bulk_add_translations` | file+live |
| Custom functions (UDFs) | `create_udf`, `update_udf`, `delete_udf` | file only |
| Calendar groups | `create_calendar`, `update_calendar_column_group`, `delete_calendar` | file only |
| **Save your changes to disk** | `save_model` | file |

† Live models can only add/edit **calculated** columns (ones defined by a DAX formula). Regular
data columns come from the table's Power Query, which has to be changed in Power BI Desktop.

---

## Important things to know

**Nothing is written until you say so (file mode).** Edits are held in memory. Ask to
*"save the model"* to write them out. Close without saving and the changes are simply discarded.

**Live edits are immediate.** There is no undo. Work on a copy if you're experimenting.

**Live editing needs one extra install.** Reading a live model works out of the box. *Changing*
one needs Microsoft's free **Analysis Services client libraries** (AMO/ADOMD). If they're missing
you'll get a message telling you exactly that — nothing breaks, and nothing is half-changed. Until
then, use file mode to make edits.

**This tool does not touch visuals.** It works on the *data model* — tables, columns, measures,
relationships, security. Pages, charts, and layout aren't reachable through this connection and
have to be done in Power BI Desktop.

**Always verify numbers in live mode.** File mode can confirm a measure *refers to real columns*,
but only a live connection can tell you it returns the *right answer*.

---

## Setup

1. Install Python 3.11+
2. In this folder: `pip install -e .`
3. Add to Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "powerbi-local-mcp": {
      "command": "python",
      "args": ["-u", "C:\\path\\to\\bimcp\\server.py"]
    }
  }
}
```

4. Restart Claude Desktop.

**Optional — to enable live editing:** install Microsoft's *Analysis Services client libraries*
(AMO/ADOMD), then restart Claude Desktop.

---

## Troubleshooting

| Symptom | What it means |
|---|---|
| "No Power BI Desktop instance detected" | Desktop isn't open, or the model is still loading. The message includes a diagnostic explaining which. |
| "Live model edits need the Tabular Object Model" | Reading works; editing needs the AMO/ADOMD install above. Use file mode meanwhile. |
| "This tool edits the TMDL model files on disk…" | You asked for a file-mode action while connected live. Open the PBIP folder, or use the live equivalent it suggests. |
| A DAX query returns an error | Good — errors are now reported with the engine's exact message rather than looking like an empty result. |

---

## A good first session

```
1. "Connect to my open Power BI Desktop model."
2. "Give me a summary of the model."
3. "List all measures with their DAX."
4. "Are any relationships inactive?"
5. "Run a DAX query showing total sales by plant."
6. "Check whether DIVIDE([Late Deliveries Count], [Total Deliveries Count]) works."
```
