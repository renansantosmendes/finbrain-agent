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
from langfuse.langchain import CallbackHandler
from langfuse import Langfuse

langfuse = Langfuse()
langfuse_handler = CallbackHandler()

finbrain_system_prompt = langfuse.get_prompt("FINBRAIN_SYSTEM_PROMPT").compile()


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
        system_prompt=finbrain_system_prompt,
    )

    inputs = {"messages": [{"role": "user", "content": "Estime qual seria o comportamento da ação da Microsoft (MSFT) no próximo mês, com base nos dados históricos de preços."}]}
    
    config = {
        "configurable": {"thread_id": "financial-agent-thread"},
        "callbacks": [langfuse_handler],
        "metadata": {
            "langfuse_session_id": "financial-agent-thread",
            "langfuse_tags": ["skills-demo", "financial-agent"],
        },
    }
    

    async for chunk in agent.astream(inputs, config):
        print(chunk)


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else MCP_URL
    asyncio.run(main(url))
