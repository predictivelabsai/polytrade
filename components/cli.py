"""Simplified CLI interface for PolyCode."""
import asyncio
from typing import Optional, List, Dict
from rich.console import Console
from rich.markdown import Markdown

from agent.agent import Agent
from agent.types import (
    AgentConfig, AgentEvent, ToolStartEvent, ToolEndEvent,
    AnswerChunkEvent, DoneEvent, LogEvent
)
from components.command_processor import CommandProcessor


import os

class PolyCodeCLI:
    """CLI application for PolyCode."""

    def __init__(self, model: Optional[str] = None, provider: Optional[str] = None):
        self.model = model or os.getenv("MODEL", "grok-3")
        self.provider = provider or os.getenv("MODEL_PROVIDER", "xai")
        self.agent: Optional[Agent] = None
        self.chat_history: List[Dict[str, str]] = []
        self.console = Console()
        self.cmd_processor: Optional[CommandProcessor] = None

    async def initialize(self):
        """Initialize the agent."""
        config = AgentConfig(model=self.model, model_provider=self.provider)
        self.agent = Agent.create(config)
        self.cmd_processor = CommandProcessor(self.agent)

    async def process_query(self, query: str):
        """Process a user query and stream results to console."""
        if not self.agent:
            self.console.print("[red]Agent not initialized[/red]")
            return

        self.chat_history.append({"role": "user", "content": query})
        
        async for event in self.agent.run(query, self.chat_history):
            if isinstance(event, LogEvent):
                if event.level == "thought":
                    self.console.print(f"\n[italic blue]> Thought: {event.message.strip()}[/italic blue]")
                elif event.level == "tool":
                    self.console.print(f"🔧 [bold yellow]Action:[/bold yellow] {event.message}")
                else:
                    self.console.print(f"ℹ️ {event.message}")
            
            elif isinstance(event, ToolStartEvent):
                self.console.print(f"🔧 Using [bold cyan]{event.tool}[/bold cyan]...")
            elif isinstance(event, ToolEndEvent):
                self.console.print(f"✓ [bold green]{event.tool}[/bold green] completed")
            elif isinstance(event, AnswerChunkEvent):
                # We could stream chunky answers, but for now we wait for Done
                pass
            elif isinstance(event, DoneEvent):
                self.console.print("\n[bold cyan]PolyCode:[/bold cyan]")
                self.console.print(Markdown(event.answer))
                self.chat_history.append({"role": "assistant", "content": event.answer})

    async def run(self):
        """Run the CLI interactive loop."""
        await self.initialize()

        self.console.print("\n[bold cyan]PolyCode CLI[/bold cyan] - Polymarket Research Agent")
        self.console.print(f"[yellow]Model:[/yellow] {self.model}")
        self.console.print(f"[yellow]Provider:[/yellow] {self.provider}")
        self.console.print("Type 'help' for commands or ask a question.\n")

        while True:
            try:
                # Update prompt based on current ticker
                ticker_display = f"({self.cmd_processor.current_ticker}) " if self.cmd_processor.current_ticker else ""
                prompt = f"[bold green]{ticker_display}You:[/bold green] "
                
                user_input = self.console.input(prompt).strip()

                if not user_input:
                    continue

                # Process command first
                is_handled, agent_query = await self.cmd_processor.process_command(user_input)
                
                if is_handled:
                    continue
                
                if agent_query:
                    self.console.print("\n[yellow]Researching...[/yellow]")
                    await self.process_query(agent_query)
                    self.console.print("")

            except KeyboardInterrupt:
                self.console.print("\n\n[yellow]Goodbye![/yellow]")
                break
            except Exception as e:
                self.console.print(f"[red]Error: {str(e)}[/red]")
                import traceback
                traceback.print_exc()
