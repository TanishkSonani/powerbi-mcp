"""
ConnectionPrompts — MCP prompt definitions for Power BI model connections.

Mirrors Microsoft's slash commands revealed in the CHANGELOG:
    /ConnectToPowerBIDesktop  →  connect_desktop
    /ConnectToPowerBIProject  →  connect_pbip

Each prompt embeds the powerbi_project_instructions resource URI in its
messages so the LLM automatically receives PBIP/TMDL file-structure context
when the prompt is invoked.
"""

from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    TextContent,
)

_PBIP_RESOURCE_URI = "resource://powerbi_project_instructions"

_PROMPTS: dict[str, Prompt] = {
    "connect_desktop": Prompt(
        name="connect_desktop",
        description=(
            "Connect to a Power BI Desktop file that is currently open. "
            "Use this when Power BI Desktop is running and you want the AI agent "
            "to interact with the live semantic model (DAX execution, live edits)."
        ),
        arguments=[
            PromptArgument(
                name="file_name",
                description="The name of the .pbix file open in Power BI Desktop (without extension is fine).",
                required=True,
            )
        ],
    ),
    "connect_pbip": Prompt(
        name="connect_pbip",
        description=(
            "Open a Power BI Project (PBIP) folder from disk. "
            "Use this when Power BI Desktop is closed and you want the AI agent "
            "to read or modify TMDL files directly (file-based editing)."
        ),
        arguments=[
            PromptArgument(
                name="folder_path",
                description=(
                    "Absolute path to the PBIP root folder, the .SemanticModel folder, "
                    "or the definition/ TMDL folder."
                ),
                required=True,
            )
        ],
    ),
}


def list_prompts() -> list[Prompt]:
    return list(_PROMPTS.values())


def get_prompt(name: str, arguments: dict[str, str]) -> GetPromptResult:
    if name not in _PROMPTS:
        available = ", ".join(_PROMPTS.keys())
        raise ValueError(f"Prompt {name!r} not found. Available: {available}")

    if name == "connect_desktop":
        file_name = arguments.get("file_name", "<file name>")
        return GetPromptResult(
            description=_PROMPTS[name].description,
            messages=[
                # System context: PBIP/TMDL structure reference
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            f"Use the resource at {_PBIP_RESOURCE_URI} for reference on "
                            "Power BI Project file structure and TMDL semantics."
                        ),
                    ),
                ),
                # User instruction
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            f"Connect to '{file_name}' in Power BI Desktop. "
                            "Once connected, you can execute DAX queries, inspect the live model, "
                            "and apply modeling changes directly to the running instance. "
                            "Use the powerbi-local-mcp tools to interact with the model."
                        ),
                    ),
                ),
            ],
        )

    if name == "connect_pbip":
        folder_path = arguments.get("folder_path", "<folder path>")
        return GetPromptResult(
            description=_PROMPTS[name].description,
            messages=[
                # System context: PBIP/TMDL structure reference
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            f"Use the resource at {_PBIP_RESOURCE_URI} for reference on "
                            "Power BI Project file structure and TMDL semantics. "
                            "When Power BI Desktop is closed, edit TMDL files directly on disk; "
                            "do not create or edit TMDL files manually when the MCP server is available."
                        ),
                    ),
                ),
                # User instruction
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            f"Open semantic model from PBIP folder '{folder_path}'. "
                            "The server will locate the definition/ TMDL folder automatically. "
                            "You can then list tables, measures, and columns, and make changes "
                            "that will be saved back to disk via TmdlSerializer."
                        ),
                    ),
                ),
            ],
        )

    raise ValueError(f"No handler defined for prompt: {name!r}")
