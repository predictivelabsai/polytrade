"""Smoke test for the PolyCode agent."""
import asyncio
import os
import sys
from pathlib import Path

# Add root directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from agent.agent import Agent
from agent.types import AgentConfig, AgentEvent, LogEvent, DoneEvent


# Load environment variables
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)


async def test_agent_initialization():
    """Test that the agent can be initialized."""
    model = os.getenv("MODEL", "grok-3")
    provider = os.getenv("MODEL_PROVIDER", "xai")
    config = AgentConfig(model=model, model_provider=provider)
    agent = Agent.create(config)
    assert agent is not None
    print(f"✓ Agent initialization successful ({model} via {provider})")


async def test_agent_run_loop_imports():
    """Test that the agent run loop doesn't have immediate import/name errors."""
    model = os.getenv("MODEL", "grok-3")
    provider = os.getenv("MODEL_PROVIDER", "xai")
    config = AgentConfig(model=model, model_provider=provider)
    agent = Agent.create(config)
    
    # We don't necessarily need a valid API key to check for NameErrors
    # but we'll try to catch events
    found_log_event = False
    
    try:
        async for event in agent.run("Test query", []):
            if isinstance(event, LogEvent):
                found_log_event = True
                print(f"✓ Received LogEvent: {event.message[:50]}")
            if isinstance(event, DoneEvent):
                print(f"✓ Received DoneEvent")
    except NameError as e:
        print(f"✗ NameError detected: {str(e)}")
        raise e
    except Exception as e:
        # We expect some exceptions if no API key is provided, but not NameError
        print(f"ℹ Received expected exception or finished: {type(e).__name__}: {str(e)}")

    print("✓ Agent run loop imports check completed")


if __name__ == "__main__":
    asyncio.run(test_agent_initialization())
    asyncio.run(test_agent_run_loop_imports())
