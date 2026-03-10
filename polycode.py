#!/usr/bin/env python3
"""Main entry point for PolyCode CLI."""
import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
from components.cli import PolyCodeCLI


async def main():
    """Main entry point."""
    # Load environment variables
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    # Get model and provider from environment or use defaults
    model = os.getenv("MODEL", "grok-3")
    provider = os.getenv("MODEL_PROVIDER", "xai")

    # Create and run app
    cli = PolyCodeCLI(model=model, provider=provider)
    
    import sys
    if len(sys.argv) > 1:
        # One-off command execution
        await cli.initialize()
        user_input = " ".join(sys.argv[1:])
        is_handled, agent_query = await cli.cmd_processor.process_command(user_input)
        if not is_handled and agent_query:
            await cli.process_query(agent_query)
    else:
        # Interactive mode
        await cli.run()


if __name__ == "__main__":
    asyncio.run(main())
