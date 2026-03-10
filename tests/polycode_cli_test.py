import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add project root to sys.path
root_path = Path(__file__).parent.parent
if str(root_path) not in sys.path:
    sys.path.append(str(root_path))

from agent.agent import Agent
from agent.types import AgentConfig, AnswerChunkEvent, DoneEvent, LogEvent

load_dotenv()

async def test_cli_query_repro():
    """Reproduce the CLI query behavior to catch errors."""
    config = AgentConfig(
        model=os.getenv("MODEL", "grok-3"),
        model_provider=os.getenv("MODEL_PROVIDER", "xai"),
        max_iterations=10
    )
    
    agent = Agent.create(config)
    query = "What is Amazon's debt-to-equity ratio based on recent financials?"
    
    print(f"Testing query: {query}")
    print(f"Model: {config.model} | Provider: {config.model_provider}")
    
    found_answer = False
    try:
        async for event in agent.run(query):
            if isinstance(event, LogEvent):
                if event.level == "thought":
                    print(f"Thought: {event.message[:100]}...")
                elif event.level == "tool":
                    print(f"Tool Plan: {event.message}")
            elif isinstance(event, AnswerChunkEvent):
                # Just print a dot to show progress
                print(".", end="", flush=True)
            elif isinstance(event, DoneEvent):
                print(f"\n\nPolyCode Final Answer:\n{event.answer}")
                found_answer = True
                break
    except Exception as e:
        print(f"\nCaught unexpected exception: {e}")
        import traceback
        traceback.print_exc()
        raise e

    if not found_answer:
        print("\nWarning: No final answer received (check iterations or tool calls)")

if __name__ == "__main__":
    asyncio.run(test_cli_query_repro())
