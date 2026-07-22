"""Same deep agent as main.py, but consuming the tools from the deployed MCP
server instead of importing them locally from skills/.
"""
import asyncio
import sys
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langchain_mcp_adapters.client import MultiServerMCPClient

MCP_URL = "https://finbrain-mcp.vercel.app/mcp"

backend = FilesystemBackend(
    root_dir=".",
    virtual_mode=False,
)


async def main(url: str) -> None:
    client = MultiServerMCPClient({
        "finbrain": {
            "transport": "streamable_http",
            "url": url,
        },
    })
    tools = await client.get_tools()

    agent = create_deep_agent(
        model="openai:gpt-5-nano",
        tools=tools,
        skills=["skills"],
        backend=backend,
    )

    inputs = {"messages": [{"role": "user", "content": "PETR4 está cara ou barata pelos fundamentos?"}]}
    config = {"configurable": {"thread_id": "investidor_01"}}

    async for chunk in agent.astream(inputs, config):
        print(chunk)


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else MCP_URL
    asyncio.run(main(url))
