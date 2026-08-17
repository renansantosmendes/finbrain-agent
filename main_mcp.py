"""Same deep agent as main.py, but consuming the tools from the deployed MCP
server instead of importing them locally from skills/.
"""
import asyncio
import sys
sys.stdout.reconfigure(encoding="utf-8")

if sys.platform == "win32":
    # psycopg's async driver (used for conversation persistence) can't run
    # under Windows' default ProactorEventLoop.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv
load_dotenv()

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langfuse.langchain import CallbackHandler
from langfuse import Langfuse

import persistence
from logging_config import logger

langfuse = Langfuse()
langfuse_handler = CallbackHandler()

finbrain_system_prompt = langfuse.get_prompt("FINBRAIN_SYSTEM_PROMPT").compile()

persistence.bootstrap_schema()


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
    logger.info("main: loaded %d MCP tools", len(tools))

    thread_id = "financial-agent-thread"

    async with AsyncPostgresSaver.from_conn_string(persistence.get_checkpointer_conn_string()) as checkpointer:
        await checkpointer.setup()

        agent = create_deep_agent(
            model="openai:gpt-5-nano",
            tools=tools,
            skills=["skills"],
            backend=backend,
            system_prompt=finbrain_system_prompt,
            checkpointer=checkpointer,
        )

        user_message = "Estime qual seria o comportamento da ação da Microsoft (MSFT) no próximo mês, com base nos dados históricos de preços."
        inputs = {"messages": [{"role": "user", "content": user_message}]}

        config = {
            "configurable": {"thread_id": thread_id},
            "callbacks": [langfuse_handler],
            "metadata": {
                "langfuse_session_id": thread_id,
                "langfuse_tags": ["skills-demo", "financial-agent"],
            },
        }

        logger.info("main: sending message thread_id=%s", thread_id)
        await persistence.log_message(thread_id, "user", user_message)

        async for chunk in agent.astream(inputs, config):
            print(chunk)

        final_state = await agent.aget_state(config)
        final_message = final_state.values["messages"][-1]
        await persistence.log_message(thread_id, "assistant", final_message.text)
        logger.info("main: run complete thread_id=%s", thread_id)


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else MCP_URL
    asyncio.run(main(url))
