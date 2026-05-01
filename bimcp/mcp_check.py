import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def run():
    server_py = str(Path(__file__).parent / "server.py")
    params = StdioServerParameters(command=sys.executable, args=[server_py], env=None)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])
            try:
                result = await session.call_tool("discover_desktop", {})
                print("DISCOVER_RESULT:")
                print(result)
            except Exception as e:
                print("CALL_TOOL_ERROR:", e)


if __name__ == "__main__":
    asyncio.run(run())
