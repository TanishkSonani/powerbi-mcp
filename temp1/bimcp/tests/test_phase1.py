"""
Phase 1 smoke tests.

Starts the MCP server as a real subprocess (same Python interpreter),
connects via the official MCP client, and asserts the protocol
handshake + resource/prompt listings are correct.

Run with:
    cd bimcp
    pytest tests/test_phase1.py -v
"""

import sys
from pathlib import Path

import pytest
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

SERVER_PY = str(Path(__file__).parent.parent / "server.py")

# Expected resource URIs (from frontmatter uriTemplate fields)
EXPECTED_RESOURCE_URIS = {
    "resource://dax_query_instructions_and_examples",
    "resource://dax_udf_instructions_and_examples",
    "resource://calendar_instructions_and_examples",
    "resource://powerbi_project_instructions",
}

# Expected prompt names
EXPECTED_PROMPT_NAMES = {"connect_desktop", "connect_pbip"}


@pytest.fixture
def server_params():
    return StdioServerParameters(
        command=sys.executable,
        args=[SERVER_PY],
        env=None,
    )


@pytest.mark.asyncio
async def test_server_handshake(server_params):
    """Server initialises cleanly and responds to the MCP handshake."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            result = await session.initialize()
            assert result.serverInfo.name == "powerbi-local-mcp"


@pytest.mark.asyncio
async def test_resources_list_returns_four(server_params):
    """resources/list must return exactly 4 resources with correct URIs."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_resources()
            uris = {str(r.uri) for r in result.resources}
            assert len(result.resources) == 4, (
                f"Expected 4 resources, got {len(result.resources)}: {uris}"
            )
            assert uris == EXPECTED_RESOURCE_URIS, (
                f"URI mismatch.\n  Expected: {EXPECTED_RESOURCE_URIS}\n  Got:      {uris}"
            )


@pytest.mark.asyncio
async def test_resources_have_descriptions(server_params):
    """Each resource must carry a non-empty name and description."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_resources()
            for r in result.resources:
                assert r.name, f"Resource {r.uri} has no name"
                assert r.description, f"Resource {r.uri} has no description"


@pytest.mark.asyncio
async def test_read_dax_resource(server_params):
    """Reading the DAX guide resource returns non-empty markdown content."""
    target_uri = "resource://dax_query_instructions_and_examples"
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.read_resource(target_uri)  # type: ignore[arg-type]
            assert result.contents, "read_resource returned empty contents"
            body = result.contents[0].text  # type: ignore[union-attr]
            assert "DAX" in body, "DAX guide body does not contain 'DAX'"
            assert "EVALUATE" in body, "DAX guide body does not contain 'EVALUATE'"
            # Frontmatter must be stripped
            assert "---" not in body[:10], "Frontmatter was not stripped from resource body"


@pytest.mark.asyncio
async def test_read_pbip_resource(server_params):
    """Reading the PBIP instructions resource returns TMDL/PBIP content."""
    target_uri = "resource://powerbi_project_instructions"
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.read_resource(target_uri)  # type: ignore[arg-type]
            body = result.contents[0].text  # type: ignore[union-attr]
            assert "SemanticModel" in body
            assert "definition" in body


@pytest.mark.asyncio
async def test_prompts_list_returns_two(server_params):
    """prompts/list must return exactly 2 prompts with correct names."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_prompts()
            names = {p.name for p in result.prompts}
            assert len(result.prompts) == 2, (
                f"Expected 2 prompts, got {len(result.prompts)}: {names}"
            )
            assert names == EXPECTED_PROMPT_NAMES, (
                f"Prompt name mismatch.\n  Expected: {EXPECTED_PROMPT_NAMES}\n  Got: {names}"
            )


@pytest.mark.asyncio
async def test_prompt_connect_desktop_has_arguments(server_params):
    """connect_desktop prompt must declare a required 'file_name' argument."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_prompts()
            cd = next(p for p in result.prompts if p.name == "connect_desktop")
            assert cd.arguments, "connect_desktop has no arguments declared"
            arg_names = [a.name for a in cd.arguments]
            assert "file_name" in arg_names


@pytest.mark.asyncio
async def test_prompt_connect_pbip_has_arguments(server_params):
    """connect_pbip prompt must declare a required 'folder_path' argument."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_prompts()
            cp = next(p for p in result.prompts if p.name == "connect_pbip")
            assert cp.arguments, "connect_pbip has no arguments declared"
            arg_names = [a.name for a in cp.arguments]
            assert "folder_path" in arg_names


@pytest.mark.asyncio
async def test_get_prompt_connect_desktop(server_params):
    """get_prompt for connect_desktop must return messages embedding the file name."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.get_prompt(
                "connect_desktop", {"file_name": "SalesModel"}
            )
            assert result.messages, "get_prompt returned no messages"
            full_text = " ".join(
                m.content.text  # type: ignore[union-attr]
                for m in result.messages
            )
            assert "SalesModel" in full_text
            assert "powerbi_project_instructions" in full_text


@pytest.mark.asyncio
async def test_get_prompt_connect_pbip(server_params):
    """get_prompt for connect_pbip must return messages embedding the folder path."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.get_prompt(
                "connect_pbip", {"folder_path": "C:/Projects/MySalesModel"}
            )
            assert result.messages, "get_prompt returned no messages"
            full_text = " ".join(
                m.content.text  # type: ignore[union-attr]
                for m in result.messages
            )
            assert "MySalesModel" in full_text
            assert "TMDL" in full_text
