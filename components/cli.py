"""Simplified CLI interface for PolyTrade."""
import json
import time
from typing import Optional, List, Dict
from rich.console import Console
from rich.markdown import Markdown

from agent.agent import Agent
from agent.types import (
    AgentConfig, ToolStartEvent, ToolEndEvent,
    ToolErrorEvent, AnswerChunkEvent, DoneEvent, LogEvent
)
from components.command_processor import CommandProcessor


import os

class PolyCodeCLI:
    """CLI application for PolyTrade."""

    def __init__(self, model: Optional[str] = None, provider: Optional[str] = None):
        self.model = model or os.getenv("MODEL")
        self.provider = provider or os.getenv("MODEL_PROVIDER")
        self.agent: Optional[Agent] = None
        self.chat_history: List[Dict[str, str]] = []
        self.console = Console()
        self.cmd_processor: Optional[CommandProcessor] = None
        self.user_id: Optional[str] = None
        self.user_email: Optional[str] = None
        self.display_name: Optional[str] = None

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

        # Create DB run
        run_id = None
        try:
            from db.repository import create_run
            run_id = await create_run(query, self.model, self.provider)
        except Exception:
            pass

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

                # Save trades to DB (same as chat UI + API)
                try:
                    from db.repository import upsert_trade, save_backtest_trades
                    result = {}
                    if isinstance(event.result, str) and event.result.startswith("{"):
                        try:
                            result = json.loads(event.result)
                        except Exception:
                            pass
                    elif isinstance(event.result, dict):
                        result = event.result

                    if event.tool == "simulate_polymarket_trade" and result:
                        price = float(result.get("vwap", result.get("entry_price", 0)))
                        amount = float(result.get("amount_executed", result.get("amount", 0)))
                        shares = float(result.get("shares_bought", result.get("shares", 0)))
                        if price > 0 and amount > 0:
                            await upsert_trade({
                                "trade_id": f"P-{result.get('market_id', 'unk')[:20]}-{int(time.time())}",
                                "run_id": run_id,
                                "market_id": str(result.get("market_id", "")),
                                "market_question": result.get("market_question", ""),
                                "trade_side": "BUY",
                                "amount": amount,
                                "entry_price": price,
                                "shares": shares,
                                "status": "OPEN",
                                "trade_type": "paper",
                            })

                    elif event.tool == "place_real_order" and result.get("status") == "success":
                        await upsert_trade({
                            "trade_id": f"R-{int(time.time())}",
                            "run_id": run_id,
                            "market_id": str(result.get("token_id", "")),
                            "trade_side": result.get("side", "BUY"),
                            "amount": float(result.get("amount", 0)),
                            "entry_price": 0,
                            "shares": 0,
                            "status": "OPEN",
                            "trade_type": "real",
                        })

                    elif event.tool == "run_backtest" and result.get("trades"):
                        city = result.get("city", "")
                        await save_backtest_trades(run_id, result, city)

                except Exception:
                    pass  # DB optional

            elif isinstance(event, ToolErrorEvent):
                self.console.print(f"✗ [bold red]{event.tool}[/bold red] — {event.error[:120]}")

            elif isinstance(event, AnswerChunkEvent):
                pass

            elif isinstance(event, DoneEvent):
                self.console.print("\n[bold cyan]PolyTrade:[/bold cyan]")
                self.console.print(Markdown(event.answer))
                self.chat_history.append({"role": "assistant", "content": event.answer})

                # Persist run + PnL snapshot
                try:
                    from db.repository import finish_run, save_pnl_snapshot
                    if run_id:
                        await finish_run(run_id, event.iterations, event.tool_calls)
                        await save_pnl_snapshot(run_id=run_id)
                except Exception:
                    pass

    async def do_login(self):
        """Run the interactive login prompt."""
        from utils.cli_auth import async_cli_login
        uid, email, display = await async_cli_login(self.console)
        if uid:
            self.user_id = uid
            self.user_email = email
            self.display_name = display

    async def run(self):
        """Run the CLI interactive loop."""
        await self.initialize()

        self.console.print("\n[bold cyan]PolyTrade CLI[/bold cyan] - Financial Research Agent")
        self.console.print(f"[yellow]Model:[/yellow] {self.model}")
        self.console.print(f"[yellow]Provider:[/yellow] {self.provider}")

        # Login prompt on startup
        await self.do_login()

        if self.display_name:
            self.console.print(f"[bold cyan]Welcome, {self.display_name}![/bold cyan]")
        self.console.print("Type 'help' for commands or ask a question.\n")

        while True:
            try:
                # Update prompt based on current ticker + user
                ticker_display = f"({self.cmd_processor.current_ticker}) " if self.cmd_processor.current_ticker else ""
                user_label = self.display_name or "You"
                prompt = f"[bold green]{ticker_display}{user_label}:[/bold green] "

                user_input = self.console.input(prompt).strip()

                if not user_input:
                    continue

                # Handle login/logout commands
                if user_input.lower() == "login":
                    await self.do_login()
                    continue
                if user_input.lower() == "logout":
                    self.user_id = None
                    self.user_email = None
                    self.display_name = None
                    self.console.print("[yellow]Logged out.[/yellow]\n")
                    continue
                if user_input.lower() == "whoami":
                    if self.user_id:
                        self.console.print(f"  [cyan]{self.display_name}[/cyan] ({self.user_email})")
                    else:
                        self.console.print("  [dim]Not logged in. Type 'login' to sign in.[/dim]")
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
