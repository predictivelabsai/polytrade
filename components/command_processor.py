import os
import sys
import json
import asyncio
import re
from typing import Optional, Dict, Any, List, Tuple
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax

class CommandProcessor:
    """Processes OpenBB-style commands directly for speed (bash-style) or yields to agent."""

    def __init__(self, agent_instance):
        self.agent = agent_instance
        self.console = Console()
        self.current_ticker: Optional[str] = None
        self.history: List[str] = []
        from utils.portfolio_manager import PortfolioManager
        from agent.tools.polymarket_tool import get_polymarket_client
        self.portfolio = PortfolioManager()
        self._pm_client_cache = None

    async def process_command(self, user_input: str) -> Tuple[bool, Optional[str]]:
        """
        Processes a command directly if possible.
        Returns (is_handled, agent_query).
        """
        raw_parts = user_input.strip().split()
        if not raw_parts:
            return True, None

        cmd = raw_parts[0].lower()
        args = raw_parts[1:]

        # Global basic commands
        if cmd in ["help", "h", "?"]:
            self._show_help()
            return True, None
        elif cmd == "cls":
            os.system('cls' if os.name == 'nt' else 'clear')
            return True, None
        elif cmd in ["exit", "q"]:
            self.console.print("[yellow]Exiting FinCode...[/yellow]")
            sys.exit(0)
        elif cmd == "reset" or cmd == "r" or user_input == "..":
            self.current_ticker = None
            self.console.print("[green]Context reset.[/green]")
            return True, None

        # Data commands - Direct Execution!
        if cmd == "load":
            if not args:
                self.console.print("[red]Error: Specify ticker (e.g., load AAPL)[/red]")
                return True, None
            ticker = args[0].upper()
            self.current_ticker = ticker
            self.console.print(f"Loading [bold cyan]{ticker}[/bold cyan] details...")
            
            result = await self._exec_tool("get_ticker_details", ticker=ticker)
            self._display_data(f"{ticker} Profile", result)
            return True, None

        elif cmd == "news":
            ticker = args[0].upper() if args else self.current_ticker
            if not ticker:
                self.console.print("[red]Error: No ticker loaded/specified.[/red]")
                return True, None
            self.console.print(f"Fetching news for [bold cyan]{ticker}[/bold cyan]...")
            
            result = await self._exec_tool("get_news", query=ticker)
            self._display_data(f"{ticker} News", result)
            return True, None

        elif cmd == "financials":
            ticker = args[0].upper() if args else self.current_ticker
            if not ticker:
                self.console.print("[red]Error: No ticker loaded/specified.[/red]")
                return True, None
            
            # Default to income statement for quick view
            self.console.print(f"Fetching financials for [bold cyan]{ticker}[/bold cyan]...")
            result = await self._exec_tool("get_financials", ticker=ticker, statement_type="income")
            self._display_data(f"{ticker} Financials", result)
            return True, None

        elif cmd == "quote":
             ticker = args[0].upper() if args else self.current_ticker
             if not ticker:
                 self.console.print("[red]Error: No ticker loaded/specified.[/red]")
                 return True, None
             self.console.print(f"Fetching quote for [bold cyan]{ticker}[/bold cyan]...")
             result = await self._exec_tool("get_ticker_details", ticker=ticker)
             self._display_data(f"{ticker} Quote", result)
             return True, None

        elif user_input.strip().lower().startswith("poly:"):
            full_input = user_input.strip()
            if full_input.lower().startswith("poly: "):
                # Extract original case for args
                effective_cmd = "poly:" + full_input[6:].strip().split()[0].lower() if len(full_input) > 6 else "poly:"
                effective_args = full_input[6:].strip().split()[1:] if len(full_input) > 6 else []
            else:
                effective_cmd = cmd
                effective_args = args


            # Handle poly:portfolio
            if effective_cmd == "poly:portfolio":
                 mode = "real"
                 if effective_args and effective_args[0].lower() == "paper":
                     mode = "paper"
                 
                 if mode == "real":
                     if not self._pm_client_cache:
                         from agent.tools.polymarket_tool import get_polymarket_client
                         self._pm_client_cache = await get_polymarket_client()
                     self.pm_client = self._pm_client_cache
    
                     self.console.print("[bold yellow]Fetching On-Chain Portfolio...[/bold yellow]")
                     data = await self.pm_client.get_portfolio()
                     
                     if "error" in data:
                         self.console.print(f"[bold red]Error:[/bold red] {data['error']}")
                     else:
                         await self._display_real_portfolio(data)
                 else:
                     await self._display_portfolio() # Paper portfolio display
                 
                 return True, None

            elif any(x in effective_cmd or (effective_args and x in effective_args[0]) for x in ["weather", "weathter", "wether"]):
                 # Check if it was actually poly:realportfolio (redundant safety but ok)
                 if effective_cmd == "poly:portfolio":
                     pass # Handled above
                 else:
                    # If they typed 'poly: weather' then args[0] might be the city
                    # If they typed 'poly:weather London' then effective_cmd is 'poly:weather' and args[0] is 'London'
                    # If they typed 'poly: weather London' then effective_cmd is 'poly:weather' and args is ['London']
                    
                    # Re-parse city correctly
                    if effective_cmd == "poly:weather" or effective_cmd == "poly:weathter" or effective_cmd == "poly:wether":
                        city = " ".join(effective_args)
                    else:
                        # Case like 'poly: weather London' where effective_cmd was parsed as 'poly:weather' already
                        city = " ".join(effective_args)

                    query = "temperature"
                    if city:
                        self.console.print(f"[bold cyan]Searching Polymarket Weather for:[/bold cyan] [yellow]{city}[/yellow]")
                        result = await self._exec_tool("search_weather_markets", query=query, city=city)
                    else:
                        self.console.print(f"[bold cyan]Scanning Polymarket Weather Opportunities...[/bold cyan]")
                        result = await self._exec_tool("search_weather_markets", query=query)
                    
                    self._display_weather_markets(result, city or "All Cities")
                    return True, None

            elif effective_cmd == "poly:predict":
                if not effective_args:
                    self.console.print("[red]Error: Usage: poly:predict (city) (numdays) or poly:predict earnings (ticker) (days) (lookback)[/red]")
                    self.console.print("Example: poly:predict London 2")
                    self.console.print("Example: poly:predict earnings MSFT 2 2y")
                    return True, None
                
                # Check for earnings (positional or key:value)
                first_arg = effective_args[0].lower()
                if first_arg in ["earnings", "market:earnings"]:
                    # Positional check: poly:predict earnings MSFT 2 2y OR poly:predict earnings 7
                    if len(effective_args) >= 2 and ":" not in effective_args[1]:
                        second_arg = effective_args[1]
                        if second_arg.isdigit():
                            # Batch mode: poly:predict earnings (days) (lookback)
                            days = int(second_arg)
                            lookback = effective_args[2] if len(effective_args) > 2 else "2y"
                            await self._handle_poly_predict_earnings(None, days, lookback)
                        else:
                            # Ticker mode: poly:predict earnings (ticker) (days) (lookback)
                            ticker = second_arg.upper()
                            days = 2 # Default
                            lookback = "2y" # Default
                            
                            if len(effective_args) > 2:
                                arg2 = effective_args[2]
                                if arg2.isdigit():
                                    days = int(arg2)
                                    if len(effective_args) > 3:
                                        lookback = effective_args[3]
                                else:
                                    # Case like: poly:predict earnings EXXON 2y
                                    lookback = arg2
                            
                            await self._handle_poly_predict_earnings(ticker, days, lookback)
                        return True, None
                    
                    # Key:Value style: poly:predict market:earnings ticker:MSFT days:2
                    arg_dict = {}
                    for a in effective_args:
                        if ":" in a:
                            k, v = a.split(":", 1)
                            arg_dict[k.lower()] = v.upper() if k.lower() == "ticker" else v
                    
                    ticker = arg_dict.get("ticker", self.current_ticker)
                    days = int(arg_dict.get("days", "2"))
                    lookback = arg_dict.get("lookback", "2y")
                    
                    await self._handle_poly_predict_earnings(ticker, days, lookback)
                    return True, None

                # Default to weather prediction
                city = effective_args[0].title()
                try:
                    numdays = int(effective_args[1]) if len(effective_args) > 1 else 2
                except ValueError:
                    numdays = 2
                
                # Determine date range for prediction (tomorrow onwards)
                from datetime import datetime, timedelta
                today = datetime.now()
                target_date_obj = today + timedelta(days=numdays)
                target_date = target_date_obj.strftime("%Y-%m-%d")
                self.console.print(f"[bold cyan]Running Prediction for {city} (Next {numdays} days)...[/bold cyan]")
                
                # Run prediction using same engine
                await self._run_backtest_handler(city, target_date, numdays, is_prediction=True)
                return True, None

            elif effective_cmd == "poly:backtest" or effective_cmd == "poly:backtest2":
                if not effective_args:
                    self.console.print("[red]Error: Usage: poly:backtest (city) (numdays)[/red]")
                    return True, None
                
                # Parse arguments: Handle multi-word cities and numdays
                from datetime import datetime
                numdays = 7  # Default
                args_to_parse = effective_args.copy()
                
                if args_to_parse and args_to_parse[-1].isdigit():
                    numdays = int(args_to_parse.pop())
                
                if not args_to_parse:
                    self.console.print("[red]Error: City name is required.[/red]")
                    return True, None
                    
                city_raw = " ".join(args_to_parse).replace('"', '').replace("'", "")
                city = city_raw.title() if city_raw.upper() not in ["NYC", "LA", "DC", "SF"] else city_raw.upper()
                
                today = datetime.now().strftime("%Y-%m-%d")
                self.console.print(f"[bold cyan]Running Cross-Sectional Backtest for {city} ({numdays} days)...[/bold cyan]")
                await self._run_backtest_handler(city, today, numdays, v2_mode=True)
                return True, None

            elif effective_cmd == "poly:buy":
                # Check for mode
                mode = "paper" # Default
                args_to_use = effective_args
                if effective_args and effective_args[0].lower() in ["paper", "real"]:
                    mode = effective_args[0].lower()
                    args_to_use = effective_args[1:]

                if len(args_to_use) < 2:
                    self.console.print(f"[red]Error: Usage: poly:buy (paper/real) (amount) (market_id)[/red]")
                    return True, None
                
                try:
                    amount = float(args_to_use[0])
                    market_id = args_to_use[1]
                except ValueError:
                    self.console.print("[red]Error: Amount must be a number.[/red]")
                    return True, None

                if mode == "real":
                    self.console.print(f"[bold green]Executing REAL Buy: ${amount:.2f} on {market_id}...[/bold green]")
                    
                    if not self._pm_client_cache:
                        from agent.tools.polymarket_tool import get_polymarket_client
                        self._pm_client_cache = await get_polymarket_client()
                    
                    # Check if this is a Gamma ID (short) or Token ID (long)
                    token_id = market_id
                    if len(market_id) < 20: 
                        # Likely a Gamma ID, resolve it
                        self.console.print(f"[dim]Resolving Gamma ID {market_id} to CLOB Token ID...[/dim]")
                        market = await self._pm_client_cache.get_market_by_id(market_id)
                        if not market or not market.clob_token_ids:
                            self.console.print(f"[bold red]Error:[/bold red] Could not resolve market ID {market_id} to tokens.")
                            return True, None
                        token_id = market.clob_token_ids[0] # Use YES token
                        self.console.print(f"[dim]Resolved to YES Token: {token_id[:10]}...[/dim]")

                    result = await self._exec_tool("place_real_order", amount=amount, token_id=token_id)
                    self._display_data("Real Trade Execution", result)
                else:
                    # Paper buy
                    self.console.print(f"Fetching current price for [yellow]{market_id}[/yellow]...")
                    if not self._pm_client_cache:
                        from agent.tools.polymarket_tool import get_polymarket_client
                        self._pm_client_cache = await get_polymarket_client()
                    
                    market = await self._pm_client_cache.get_market_by_id(market_id)
                    
                    if not market:
                        self.console.print(f"[red]Error: Could not find market {market_id}[/red]")
                        return True, None
                    
                    # Market is a PolymarketMarket object
                    price = market.yes_price
                    if price <= 0:
                        self.console.print("[red]Error: Market has no valid price.[/red]")
                        return True, None
                    
                    trade = self.portfolio.add_trade(market_id, market.question, amount, price)
                    self.console.print(Panel(
                        f"Market: {market.question}\nEntry Price: [bold]${price:.3f}[/bold]\nAmount: [green]${amount:.2f}[/green]\nShares: [cyan]{trade['shares']:.2f}[/cyan]",
                        title="[bold green]Paper Trade Executed[/bold green]",
                        border_style="green"
                    ))
                return True, None

            elif effective_cmd == "poly:sell":
                # Check for mode
                mode = "paper" # Default
                args_to_use = effective_args
                if effective_args and effective_args[0].lower() in ["paper", "real"]:
                    mode = effective_args[0].lower()
                    args_to_use = effective_args[1:]

                if len(args_to_use) < 1:
                    self.console.print(f"[red]Error: Usage: poly:sell (paper/real) (id/amount) (market_id)[/red]")
                    return True, None

                if mode == "real":
                    if len(args_to_use) < 2:
                        self.console.print("[red]Error: Usage: poly:sell real <amount> <market_id>[/red]")
                        return True, None
                    
                    try:
                        amount = float(args_to_use[0])
                        market_id = args_to_use[1]
                    except ValueError:
                        self.console.print("[red]Error: Amount must be a number.[/red]")
                        return True, None
                    
                    self.console.print(f"[bold red]Executing REAL Sell: {amount} shares on {market_id}...[/bold red]")
                    
                    if not self._pm_client_cache:
                        from agent.tools.polymarket_tool import get_polymarket_client
                        self._pm_client_cache = await get_polymarket_client()
                    
                    # Check if this is a Gamma ID (short) or Token ID (long)
                    token_id = market_id
                    if len(market_id) < 20: 
                        # Likely a Gamma ID, resolve it
                        self.console.print(f"[dim]Resolving Gamma ID {market_id} to CLOB Token ID...[/dim]")
                        market = await self._pm_client_cache.get_market_by_id(market_id)
                        if not market or not market.clob_token_ids:
                            self.console.print(f"[bold red]Error:[/bold red] Could not resolve market ID {market_id} to tokens.")
                            return True, None
                        token_id = market.clob_token_ids[0] # Use YES token
                        self.console.print(f"[dim]Resolved to YES Token: {token_id[:10]}...[/dim]")

                    result = await self._exec_tool("place_real_order", amount=amount, token_id=token_id, side="SELL")
                    self._display_data("Real Trade Execution (SELL)", result)
                else:
                    # Paper sell
                    trade_id = args_to_use[0]
                    
                    # Check if trade exists and is open
                    trades = self.portfolio.get_trades()
                    target_trade = None
                    for t in trades:
                        if t["id"] == trade_id or t["id"].endswith(trade_id):
                            if t["status"] == "OPEN":
                                target_trade = t
                                break
                            else:
                                self.console.print(f"[yellow]Trade {trade_id} is already SOLD.[/yellow]")
                                return True, None
                    
                    if not target_trade:
                        self.console.print(f"[red]Error: Open trade with ID {trade_id} not found.[/red]")
                        return True, None
                    
                    self.console.print(f"Closing trade {trade_id} at current market price...")
                    if not self._pm_client_cache:
                        from agent.tools.polymarket_tool import get_polymarket_client
                        self._pm_client_cache = await get_polymarket_client()
                    
                    market = await self._pm_client_cache.get_market_by_id(target_trade["market_id"])
                    if not market:
                        self.console.print("[red]Error: Could not fetch current market price to close trade.[/red]")
                        return True, None
                    
                    exit_price = market.yes_price
                    closed_trade = self.portfolio.close_trade_by_id(trade_id, exit_price)
                    
                    if closed_trade:
                        pnl = closed_trade["payout"] - closed_trade["amount"]
                        pnl_perc = (pnl / closed_trade["amount"] * 100) if closed_trade["amount"] > 0 else 0
                        pnl_color = "green" if pnl >= 0 else "red"
                        
                        self.console.print(Panel(
                            f"Market: {closed_trade['question']}\n"
                            f"Exit Price: [bold]${exit_price:.3f}[/bold]\n"
                            f"Payout: [green]${closed_trade['payout']:.2f}[/green]\n"
                            f"PnL: [{pnl_color}]${pnl:+.2f} ({pnl_perc:+.2f}%)[/{pnl_color}]",
                            title="[bold yellow]Paper Trade SOLD[/bold yellow]",
                            border_style="yellow"
                        ))
                return True, None
            
            else:
                self.console.print(f"[red]Unknown Polymarket command: {effective_cmd}[/red]")
                self.console.print("Available: poly:weather, poly:backtest, poly:buy")
                return True, None


        # If it doesn't match a direct shortcut, it might be a complex command for the agent
        # or just a natural language question.
        return False, user_input

    async def _exec_tool(self, tool_name: str, **kwargs) -> Any:
        """Call a tool function directly from the agent's tool_map."""
        if tool_name not in self.agent.tool_map:
            return {"error": f"Tool {tool_name} not available"}
        
        try:
            tool = self.agent.tool_map[tool_name]
            # StructuredTool.func is the raw method
            import inspect
            if inspect.iscoroutinefunction(tool.func):
                return await tool.func(**kwargs)
            else:
                return tool.func(**kwargs)
        except Exception as e:
            return {"error": str(e)}

    def _display_data(self, title: str, data: Any):
        """Standardized data display for direct commands."""
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except:
                pass
        
        if isinstance(data, dict) and "error" in data:
            self.console.print(f"[red]Error:[/red] {data['error']}")
            return

        # Special handling for Ticker Profile / Quote
        if isinstance(data, dict) and "market_cap" in data and "description" in data:
            ticker = data.get("ticker", "N/A")
            name = data.get("name", "N/A")
            market_cap = f"${data['market_cap']:,}" if isinstance(data.get("market_cap"), (int, float)) else "N/A"
            
            # Extract price data if available
            price = data.get("price_data", {})
            day = price.get("day", {})
            prev = price.get("prevDay", {})
            
            # Key Stats Table
            stats = Table(show_header=False, box=None, padding=(0, 2))
            
            # Format price row if available
            if day.get("c"):
                current_price = f"${day['c']:.2f}"
                change = price.get("todaysChange", 0)
                change_perc = price.get("todaysChangePerc", 0)
                color = "green" if change >= 0 else "red"
                sign = "+" if change >= 0 else ""
                stats.add_row("[bold cyan]Price:[/bold cyan]", f"[bold {color}]{current_price} ({sign}{change:.2f}, {sign}{change_perc:.2f}%)[/bold {color}]")

            stats.add_row("[bold cyan]Previous Close:[/bold cyan]", f"${prev['c']:.2f}" if prev.get("c") else "N/A")
            stats.add_row("[bold cyan]Open:[/bold cyan]", f"${day['o']:.2f}" if day.get("o") else "N/A")
            
            if day.get("l") and day.get("h"):
                stats.add_row("[bold cyan]Day Range:[/bold cyan]", f"${day['l']:.2f} - ${day['h']:.2f}")

            stats.add_row("[bold cyan]Market Cap:[/bold cyan]", market_cap)
            
            if day.get("v"):
                stats.add_row("[bold cyan]Volume:[/bold cyan]", f"{day['v']:,}")

            stats.add_row("[bold cyan]Exchange:[/bold cyan]", data.get("primary_exchange", "N/A"))
            stats.add_row("[bold cyan]Website:[/bold cyan]", data.get("homepage_url", "N/A"))
            
            # Shared outstanding
            shares = f"{data.get('share_class_shares_outstanding', 0):,}" if data.get("share_class_shares_outstanding") else "N/A"
            stats.add_row("[bold cyan]Shares Out:[/bold cyan]", shares)
            
            updated_ts = price.get("updated")
            if updated_ts:
                # Convert nanoseconds to string
                from datetime import datetime
                try:
                    ts = datetime.fromtimestamp(updated_ts / 1e9).strftime('%Y-%m-%d %H:%M:%S')
                    stats.add_row("[bold cyan]Last Updated:[/bold cyan]", ts)
                except:
                    pass

            self.console.print(f"\n[bold green]{name} ({ticker})[/bold green]")
            self.console.print(Panel(stats, title="[bold]Market Data & Key Stats[/bold]", border_style="cyan"))
            
            self.console.print(Panel(
                data.get("description", "No description available."),
                title="[bold]Business Description[/bold]",
                border_style="blue",
                padding=(1, 2)
            ))
            self.console.print(f"Source: Massive")
            return

        # Special handling for News results to quote sources and provider
        if isinstance(data, dict) and "results" in data and "provider" in data:
            self.console.print(f"\n[bold green]Provider: {data['provider']}[/bold green]")
            for item in data["results"]:
                title_text = item.get("title", "No Title")
                summary = item.get("summary", item.get("content", ""))
                source = item.get("source", item.get("url", "N/A"))
                timestamp = item.get("timestamp", "N/A")
                
                self.console.print(Panel(
                    f"{summary}\n\n[bold italic]Source:[/bold italic] {source}\n[bold italic]Time:[/bold italic] {timestamp}",
                    title=f"[bold]{title_text}[/bold]",
                    border_style="blue"
                ))
            return

        # Special handling for Financials (Tabular)
        if isinstance(data, dict) and any(k in data for k in ["revenues", "net_income_loss", "assets", "liabilities"]):
            meta = data.pop("_metadata", {})
            end_date = meta.get("end_date", "N/A")
            period = f"{meta.get('fiscal_year', '')} {meta.get('fiscal_period', '')}".strip()
            
            table = Table(
                title=f"[bold green]{title}[/bold green] (Cut-off: {end_date} | {period})",
                show_header=True,
                header_style="bold cyan"
            )
            table.add_column("Line Item", style="dim")
            table.add_column("Value", justify="right")
            
            for key, val in data.items():
                if isinstance(val, dict) and "value" in val:
                    display_val = f"{val['value']:,}" if isinstance(val['value'], (int, float)) else str(val['value'])
                    unit = val.get("unit", "")
                    table.add_row(key.replace("_", " ").title(), f"{display_val} {unit}")
            
            self.console.print(table)
            self.console.print(f"Source: Massive")
            return

        pretty_json = json.dumps(data, indent=2)
        panel = Panel(
            Syntax(pretty_json, "json", theme="monokai", word_wrap=True),
            title=f"[bold green]{title}[/bold green]",
            border_style="cyan"
        )
        self.console.print(panel)

    def _display_weather_markets(self, markets: Any, city: str):
        """Display weather markets in a formatted table."""
        from datetime import datetime
        
        if not markets or not isinstance(markets, list):
            self.console.print(f"[bold red]No weather markets found for {city}.[/bold red]")
            return
        
        # Filter markets to only show those with complete data (no N/As)
        complete_markets = [
            m for m in markets 
            if m.get("yes_book") and m.get("yes_book").get("best_bid") is not None
            and m.get("forecast_at_resolution")
        ]
        
        if not complete_markets:
            self.console.print(f"[bold yellow]Found {len(markets)} markets but none have complete CLOB and forecast data.[/bold yellow]")
            self.console.print(f"Markets without forecasts cannot be analyzed for edge opportunities.")
            return
        
        # Create Table for console output
        table = Table(
            title=f"Weather Markets: {city} ({len(complete_markets)} with complete data)", 
            show_header=True, 
            header_style="bold magenta",
            expand=True
        )
        table.add_column("Question", style="dim", no_wrap=False, max_width=45)
        table.add_column("Liq", justify="right", width=7)
        table.add_column("YES % (VWAP)", justify="right", style="bright_green", width=12)
        table.add_column("NO % (VWAP)", justify="right", style="bright_red", width=11)
        table.add_column("Resolves (UTC)", justify="center", style="cyan", width=14)
        table.add_column("Forecast @ (UTC)", justify="center", style="magenta", width=14)
        table.add_column("Temp Forecast", justify="center", style="yellow", width=15)

        for m in complete_markets:
            yes_book = m.get("yes_book") or {}
            no_book = m.get("no_book") or {}
            forecast = m.get("forecast_at_resolution")
            
            # Get volume-weighted prices (now stored in best_bid/best_ask)
            yes_vwap = yes_book.get('best_bid', 0)  # VWAP stored here now
            no_vwap = no_book.get('best_bid', 0)    # VWAP stored here now
            
            # Calculate fair value
            yes_fair = yes_vwap if yes_vwap > 0 else 0.5
            no_fair = no_vwap if no_vwap > 0 else 0.5
            
            # Format resolution time with AM/PM
            resolution_time = "N/A"
            if m.get("end_date"):
                try:
                    dt = datetime.fromisoformat(m["end_date"].replace('Z', '+00:00'))
                    resolution_time = dt.strftime("%b %d %I:%M%p")
                except:
                    resolution_time = m["end_date"][:16]
            
            # Format forecast time with AM/PM
            forecast_time = "N/A"
            if forecast and forecast.get("time"):
                try:
                    dt = datetime.fromisoformat(forecast["time"].replace('Z', '+00:00'))
                    forecast_time = dt.strftime("%b %d %I:%M%p")
                except:
                    forecast_time = forecast["time"][:16]
            
            # Format forecast temperature
            temp_c = forecast['temperature_c']
            temp_f = forecast['temperature_f']
            forecast_str = f"{temp_c}°C/{temp_f}°F"
            
            table.add_row(
                m["question"],
                f"${m['liquidity']/1000:.1f}k",
                f"{yes_fair*100:.0f}%",
                f"{no_fair*100:.0f}%",
                resolution_time,
                forecast_time,
                forecast_str
            )
        
        self.console.print(table)
        self.console.print(f"\nPrices shown are volume-weighted fair values from order book depth.")

    async def _display_real_portfolio(self, data: Dict[str, Any]):
        """Display real on-chain portfolio/positions."""
        bal = data.get("balance", 0.0)
        positions = data.get("positions", [])
        
        self.console.print(f"\n[bold green]Real Wallet Balance:[/bold green] ${bal:.2f} USDC")
        
        if not positions:
            self.console.print("[yellow]No active positions found in this account.[/yellow]")
            return

        table = Table(title="Real On-Chain Positions", header_style="bold green")
        table.add_column("Market", ratio=4)
        table.add_column("Outcome", justify="center")
        table.add_column("ID", style="dim")
        table.add_column("Size", justify="right")
        table.add_column("Entry", justify="right")
        table.add_column("Curr", justify="right")
        table.add_column("PnL", justify="right")

        total_value = 0
        total_pnl = 0

        for p in positions:
            shares = p["size"]
            entry = p["entry_price"]
            curr = p["current_price"]
            pnl = p["pnl"]
            pnl_perc = p["pnl_percent"]
            val = p["current_value"]
            
            pnl_color = "green" if pnl >= 0 else "red"
            total_value += val
            total_pnl += pnl

            # Pick the best ID to show (Market ID preferred for selling)
            display_id = p.get("market_id") or p.get("asset", "")[:8]

            table.add_row(
                p["market"],
                f"[bold cyan]{p['outcome']}[/bold cyan]",
                f"[dim]{display_id}[/dim]",
                f"{shares:.2f}",
                f"${entry:.3f}",
                f"${curr:.3f}",
                f"[{pnl_color}]${pnl:+.2f} ({pnl_perc:+.1f}%)[/{pnl_color}]"
            )

        self.console.print(table)
        
        summary = Table.grid(padding=(0, 2))
        summary.add_column(justify="right", style="bold")
        summary.add_column(justify="left")
        summary.add_row("Total Positions Value:", f"${total_value:.2f}")
        pnl_color = "green" if total_pnl >= 0 else "red"
        summary.add_row("Net On-Chain Profit:", f"[{pnl_color}]${total_pnl:+.2f}[/{pnl_color}]")
        summary.add_row("Total Account Value:", f"[bold cyan]${(bal + total_value):.2f}[/bold cyan]")
        
        self.console.print(Panel(summary, title="[bold]Real Portfolio Summary[/bold]", border_style="green"))

    async def _run_backtest_handler(self, city: str, date: str, lookback_days: int = 7, is_prediction: bool = False, v2_mode: bool = False):
        """Async handler for running backtest to avoid blocking the CLI loop."""
        try:
            from utils.backtest_engine import BacktestEngine
            from agent.tools.polymarket_tool import PolymarketClient
            from agent.tools.visual_crossing_client import VisualCrossingClient
            
            pm_client = PolymarketClient()
            vc_client = VisualCrossingClient()
            
            # Initialize Tomorrow.io client if key is available
            tomorrow_key = os.getenv("TOMORROWIO_API_KEY")
            tm_client = None
            if tomorrow_key:
                from agent.tools.weather_tool import WeatherClient
                tm_client = WeatherClient(api_key=tomorrow_key)

            engine = BacktestEngine(pm_client, vc_client, tomorrow_client=tm_client)
            
            if is_prediction:
                print(f"Fetching forecast data for {city}...")
            
            result = await engine.run_backtest(city, date, lookback_days, is_prediction=is_prediction, v2_mode=v2_mode)
            
            if not result.get("success"):
                self.console.print(f"\n[red]Backtest Failed: {result.get('error')}[/red]")
                await pm_client.close()
                await vc_client.close()
                if tm_client: await tm_client.close()
                return

            # Display Trade Details Table
            if result.get("trades"):
                title = f"Market Prediction '{result['city']}'" if is_prediction else f"Market Backtest '{result['city']}'"
                self.console.print(f"\n[bold green]{title}[/bold green]")
                
                trade_table = Table(show_header=True, header_style="bold cyan")
                trade_table.add_column("Date", style="dim", width=7)
                trade_table.add_column("Target", justify="center")
                trade_table.add_column("VC-Fcst", justify="center", style="magenta", no_wrap=True)
                if is_prediction:
                    trade_table.add_column("TM-Fcst", justify="center", style="dim magenta", no_wrap=True)
                trade_table.add_column("Actual", justify="center", style="blue", no_wrap=True)
                if is_prediction or v2_mode:
                    trade_table.add_column("ID", style="cyan", width=8)
                if is_prediction or v2_mode:
                    trade_table.add_column("Side", justify="center", width=6)
                trade_table.add_column("Our%", justify="right", width=5)
                trade_table.add_column("Mkt%", justify="right", width=5)
                trade_table.add_column("Price", justify="right", width=7)
                if not v2_mode or is_prediction:
                    trade_table.add_column("Ends In", justify="right", style="dim")
                trade_table.add_column("Result", justify="center")

                for t in result["trades"]:
                    res_color = "green" if "WIN" in t["result"] else "red" if "LOSS" in t["result"] else "yellow"
                    # Compact Date: Jan 28
                    from datetime import datetime
                    try:
                        date_display = datetime.strptime(t["date"], "%Y-%m-%d").strftime("%b %d")
                    except:
                        date_display = t["date"]

                    def fmt_temp(f_val):
                        if f_val == "N/A" or f_val is None: return "N/A"
                        try:
                            f = float(f_val)
                            c = (f - 32) * 5/9
                            return f"{c:.1f}°C ({f:.1f}°F)"
                        except: return str(f_val)

                    row_data = [
                        date_display,
                        t.get("target_display", f"{t['bucket']} ({t['target_f']}°F)"),
                        fmt_temp(t.get('forecast')),
                    ]
                    if is_prediction:
                        row_data.append(fmt_temp(t.get('forecast_secondary')))
                    
                    row_data.append(fmt_temp(t.get('actual')))
                    
                    if is_prediction or v2_mode:
                        row_data.append(str(t.get("market_id", "N/A")))
                    
                    if is_prediction or v2_mode:
                        side = t.get("Side", "NONE")
                        side_color = "bright_green" if side == "YES" else "bright_red" if side == "NO" else "dim"
                        row_data.append(f"[{side_color}]{side}[/{side_color}]")
                        
                    row_data.extend([
                        t["prob"],
                        t.get("market_prob", "N/A"),
                        f"${t['price']:.3f}"
                    ])
                    
                    if not v2_mode or is_prediction:
                        row_data.append(t.get("countdown", "N/A"))
                        
                    row_data.append(f"[{res_color}]{t['result']}[/{res_color}]")
                    
                    trade_table.add_row(*row_data)
                self.console.print(trade_table)
            else:
                markets_found = result.get("markets_found", 0)
                if markets_found > 0:
                     self.console.print(f"\n[bold yellow]Found {markets_found} relevant markets, but no trades met the strategy criteria (positive edge).[/bold yellow]")
                     self.console.print(f"[dim](This suggests the market prices were less attractive than the calculated fair values.)[/dim]")
                else:
                    self.console.print(f"\n[bold yellow]No active trades found for {result['city']} in the specified period.[/bold yellow]")
                    self.console.print(f"[dim](This usually means no 'Highest Temperature' markets match the dates on Polymarket.)[/dim]")

            # Display Summary Stats
            self.console.print("\n[bold]Portfolio Performance Summary:[/bold]")
            stats = Table(show_header=True, header_style="bold magenta")
            stats.add_column("Metric")
            stats.add_column("Value", justify="right")
            
            stats.add_row("Initial Bankroll", "$1000.00")
            stats.add_row("Completed Investments", f"${result['resolved_invested']:.2f}")
            stats.add_row("Max Drawdown", f"{result.get('max_drawdown', 0.0):.1f}%")
            stats.add_row("Total Payouts", f"${result['resolved_payout']:.2f}")
            
            pnl_color = "green" if result['resolved_roi'] >= 0 else "red"
            stats.add_row("Net Profit (Resolved)", f"[{pnl_color}]${(result['resolved_payout'] - result['resolved_invested']):.2f}[/{pnl_color}]")
            stats.add_row("Resolved ROI", f"[{pnl_color}]{result['resolved_roi']:.2f}%[/{pnl_color}]")
            
            if result.get("pending_invested", 0) > 0:
                stats.add_section()
                stats.add_row("Capital in Active Markets", f"[yellow]${result['pending_invested']:.2f}[/yellow]")
            
            self.console.print(stats)
            self.console.print(f"\nDetailed report saved to: {result['csv_path']}")

            await pm_client.close()
            await vc_client.close()
            if tm_client: await tm_client.close()
            
        except Exception as e:
            self.console.print(f"\n[bold red]System Error during backtest:[/bold red] {e}")
            import traceback
            traceback.print_exc()

    async def _display_portfolio(self):
        """Display the current paper trading portfolio performance."""
        trades = self.portfolio.get_trades()
        if not trades:
            self.console.print("[yellow]Your portfolio is empty. Use poly:buy to start trading![/yellow]")
            return

        table = Table(title="Paper Trading Portfolio", header_style="bold magenta")
        table.add_column("ID", style="dim", width=10)
        table.add_column("Market", ratio=4)
        table.add_column("Entry", justify="right")
        table.add_column("Curr", justify="right")
        table.add_column("PnL", justify="right")
        table.add_column("Status", justify="center")

        total_invested = 0
        total_value = 0

        for t in trades:
            market_id = t["market_id"]
            entry_price = t["entry_price"]
            shares = t["shares"]
            invested = t["amount"]
            
            # For OPEN trades, get current price
            current_price = entry_price
            status_display = t["status"]
            
            if t["status"] == "OPEN":
                if not self._pm_client_cache:
                    from agent.tools.polymarket_tool import get_polymarket_client
                    self._pm_client_cache = await get_polymarket_client()
                
                market = await self._pm_client_cache.get_market_by_id(market_id)
                if market:
                    current_price = market.yes_price
                status_display = "[yellow]OPEN[/yellow]"
            else:
                current_price = t.get("exit_price") or (1.0 if t["payout"] > 0 else 0.0)
                status_display = "[green]SOLD[/green]" if t["payout"] > 0 else "[red]SOLD[/red]"

            value = shares * current_price
            pnl = value - invested
            pnl_perc = (pnl / invested * 100) if invested > 0 else 0
            pnl_color = "green" if pnl >= 0 else "red"
            
            total_invested += invested
            total_value += value

            table.add_row(
                t["id"][-6:], # Only show last 6 chars of ID
                t["question"],
                f"${entry_price:.3f}",
                f"${current_price:.3f}",
                f"[{pnl_color}]${pnl:+.2f} ({pnl_perc:+.1f}%)[/{pnl_color}]",
                status_display
            )

        self.console.print(table)
        
        # Summary footer
        total_pnl = total_value - total_invested
        total_pnl_perc = (total_pnl / total_invested * 100) if total_invested > 0 else 0
        pnl_color = "green" if total_pnl >= 0 else "red"
        
        summary = Table.grid(padding=(0, 2))
        summary.add_column(justify="right", style="bold")
        summary.add_column(justify="left")
        summary.add_row("Total Invested:", f"${total_invested:.2f}")
        summary.add_row("Portfolio Value:", f"${total_value:.2f}")
        summary.add_row("Net Profit/Loss:", f"[{pnl_color}]${total_pnl:+.2f} ({total_pnl_perc:+.2f}%)[/{pnl_color}]")
        
        self.console.print(Panel(summary, title="[bold]Portfolio Summary[/bold]", border_style="cyan"))

    async def _handle_poly_predict_earnings(self, ticker_symbol: Optional[str], days: int, lookback: str):
        """Handle the poly:predict market:earnings command (Single Ticker or Batch Days)."""
        from datetime import datetime, timedelta
        import yfinance as yf
        import re
        import asyncio

        if ticker_symbol:
            # Single Ticker Mode
            await self._run_single_earnings_prediction(ticker_symbol, days, lookback)
        else:
            # Batch Mode: Find all earnings in upcoming 'days'
            self.console.print(f"[bold cyan]Scanning for upcoming earnings events in the next {days} days...[/bold cyan]")
            
            if not self._pm_client_cache:
                from agent.tools.polymarket_tool import get_polymarket_client
                self._pm_client_cache = await get_polymarket_client()

            with self.console.status("[bold green]Searching Polymarket for Earnings Markets..."):
                markets = await self._pm_client_cache.gamma_search("beat earnings", status="active")
            
            if not markets:
                self.console.print("[yellow]No active earnings markets found on Polymarket.[/yellow]")
                return

            # Filter for markets resolving within 'days'
            events_to_analyze = []
            now = datetime.now()
            target_limit = now + timedelta(days=days)
            
            # Improved ticker extraction strategy:
            # 1. Look for text in parentheses like (AAPL)
            parens_pattern = re.compile(r'\(([A-Z]{1,5})\)')
            # 2. Look for any uppercase words of length 1-5
            ticker_pattern = re.compile(r'\b([A-Z]{1,5})\b')
            
            common_words = {
                "BEAT", "EPS", "Q1", "Q2", "Q3", "Q4", "FY", "US", "QUARTER", "THE", "WILL", 
                "AND", "FOR", "WITH", "THAT", "THIS", "FROM", "THEY", "BEATS", "MISS", 
                "TOTAL", "ALL", "NEW", "CORP", "INC", "CO", "LTD", "PLC", "TECH", "OIL",
                "GAS", "GOLD", "BANK", "VISA", "APPLE", "EXXON", "WALT", "DISNEY", "EBAY",
                "FORD", "INTEL", "META" # Filter out names that are often written in caps but have different tickers
            }
            
            seen_tickers = set()
            for m in markets:
                if not m.end_date: continue
                try:
                    expiry = datetime.fromisoformat(m.end_date.replace("Z", "+00:00"))
                    
                    if now.replace(tzinfo=expiry.tzinfo) <= expiry <= (now + timedelta(days=days)).replace(tzinfo=expiry.tzinfo):
                        q_up = m.question.upper()
                        
                        # Strategy 1: Parens first (Highest confidence)
                        ticker = None
                        parens_matches = parens_pattern.findall(m.question) # Use original case case for parens? 
                        # Actually use original case for parens to be safe but usually they are caps
                        if not parens_matches:
                            parens_matches = parens_pattern.findall(q_up)
                            
                        if parens_matches:
                            for pm in parens_matches:
                                if pm not in common_words:
                                    ticker = pm
                                    break
                        
                        # Strategy 2: First uppercase word not in common_words
                        if not ticker:
                            potential_tickers = ticker_pattern.findall(q_up)
                            for pt in potential_tickers:
                                if pt in common_words: continue
                                ticker = pt
                                break
                        
                        if ticker:
                            if ticker in seen_tickers: continue
                            seen_tickers.add(ticker)
                            events_to_analyze.append({
                                "ticker": ticker,
                                "market": m,
                                "expiry": expiry
                            })
                except:
                    continue

            if not events_to_analyze:
                self.console.print(f"[yellow]No earnings markets found resolving in the next {days} days.[/yellow]")
                return

            self.console.print(f"[green]Found {len(events_to_analyze)} upcoming earnings events. Analyzing...[/green]")
            
            # Use a summary table for batch results
            summary_table = Table(title=f"Upcoming Earnings Analysis (Next {days} Days)", header_style="bold magenta")
            summary_table.add_column("Ticker", style="bold cyan")
            summary_table.add_column("Date", justify="center")
            summary_table.add_column("Hist. Beat", justify="right")
            summary_table.add_column("Poly YES%", justify="right")
            summary_table.add_column("Edge", justify="right")
            summary_table.add_column("Question", ratio=1, overflow="ellipsis")

            for event in sorted(events_to_analyze, key=lambda x: x['expiry']):
                ticker = event['ticker']
                m = event['market']
                
                # Simplified analysis for batch
                try:
                    # Use asyncio.to_thread for yf calls to avoid blocking, though yfinance is mostly IO
                    yft = yf.Ticker(ticker)
                    
                    # Secondary check if yfinance fails to find ticker (maybe it's a name?)
                    dates = yft.earnings_dates
                    if (dates is None or dates.empty) and len(ticker) > 5:
                        # Try searching by name? (Too slow for batch, skip for now)
                        pass

                    if dates is not None and not dates.empty:
                        # Parsing lookback for batch
                        years = 2
                        if "y" in lookback: years = int(lookback.replace("y", ""))
                        
                        # Timezone awareness
                        now_tz = now.replace(tzinfo=dates.index.tz)
                        start_date = now_tz - timedelta(days=365 * years)
                        
                        hist = dates[dates.index < now_tz]
                        valid_hist = hist.dropna(subset=['Reported EPS', 'EPS Estimate'])
                        recent_hist = valid_hist[valid_hist.index > start_date]
                        
                        if not recent_hist.empty:
                            beats = recent_hist[recent_hist['Reported EPS'] > recent_hist['EPS Estimate']]
                            beat_rate = len(beats) / len(recent_hist)
                            
                            # Check if this market is ALREADY RESOLVED even if yfinance doesn't show it
                            m_prob = m.yes_price
                            if m.closed and m.resolution:
                                m_prob = 1.0 if m.resolution.upper() == "YES" else 0.0
                            
                            edge = beat_rate - m_prob
                            edge_color = "bright_green" if edge > 0.05 else "bright_red" if edge < -0.05 else "white"
                            
                            res_marker = "✓ " if m.closed else ""
                            summary_table.add_row(
                                ticker,
                                event['expiry'].strftime("%b %d"),
                                f"{beat_rate:.1%}",
                                f"{m_prob:.1%}",
                                f"[{edge_color}]{res_marker}{edge:+.1%}[/{edge_color}]",
                                m.question
                            )
                        else:
                            summary_table.add_row(ticker, event['expiry'].strftime("%b %d"), "N/A", f"{m.yes_price:.1%}", "---", m.question)
                    else:
                        summary_table.add_row(ticker, event['expiry'].strftime("%b %d"), "N/A", f"{m.yes_price:.1%}", "---", m.question)
                except:
                    summary_table.add_row(ticker, event['expiry'].strftime("%b %d"), "ERR", f"{m.yes_price:.1%}", "---", m.question)

            self.console.print(summary_table)
            self.console.print("\n[dim]Tip: ✓ = Already Resolved. Use 'poly:predict earnings <ticker>' for a deep dive.[/dim]")

    async def _run_single_earnings_prediction(self, ticker_symbol: str, days: int, lookback: str):
        """Original detailed logic for a single ticker prediction."""
        from datetime import datetime, timedelta
        import yfinance as yf
        import re
        import asyncio

        yftictker = yf.Ticker(ticker_symbol)

        self.console.print(f"[bold cyan]Analyzing Earnings Predictability for {ticker_symbol}...[/bold cyan]")
        
        # 1. Fetch Polymarket YES price
        if not self._pm_client_cache:
            from agent.tools.polymarket_tool import get_polymarket_client
            self._pm_client_cache = await get_polymarket_client()
        
        with self.console.status("[bold green]Searching Polymarket Market..."):
            async def get_all_relevant_markets(query):
                active = await self._pm_client_cache.gamma_search(query, status="active")
                closed = await self._pm_client_cache.gamma_search(query, status="closed")
                return active + closed

            def calculate_score(m):
                q_up = m.question.upper()
                t_up = ticker_symbol.upper()
                
                # Check for ticker OR company name (if available)
                has_name = False
                try: 
                    name = yftictker.info.get('longName', '').upper()
                    if name and name.split(' ')[0] in q_up: has_name = True
                except: pass
                
                text_match = (t_up in q_up or has_name) and ("BEAT" in q_up or "EARNINGS" in q_up)
                if not text_match: return -1
                
                score = 0
                if hasattr(m, "end_date") and m.end_date:
                    try:
                        expiry = datetime.fromisoformat(m.end_date.replace("Z", "+00:00"))
                        now_tz = datetime.now(expiry.tzinfo)
                        
                        if now_tz < expiry <= now_tz + timedelta(days=days):
                            score += 100
                        elif now_tz - timedelta(days=7) <= expiry <= now_tz: # Relaxed to 7 days
                            score += 50
                        elif expiry > now_tz + timedelta(days=days):
                            score += 10
                    except: pass
                
                if not getattr(m, 'closed', False):
                    score += 5
                return score

            async def find_best_in_markets(m_list):
                if not m_list: return None
                sorted_m = sorted(m_list, key=calculate_score, reverse=True)
                if sorted_m and calculate_score(sorted_m[0]) >= 10:
                    best = sorted_m[0]
                    if best.closed:
                        try:
                            full_m = await self._pm_client_cache.get_market_by_id(best.id)
                            if full_m: return full_m
                        except: pass
                    return best
                return None

            # Try Ticker + context
            markets = await get_all_relevant_markets(f"{ticker_symbol} beat earnings")
            best_market = await find_best_in_markets(markets)
            
            if not best_market:
                # Try Ticker only
                markets = await get_all_relevant_markets(ticker_symbol)
                best_market = await find_best_in_markets(markets)

            if not best_market:
                # Try Company Name fallback
                try:
                    name_search = yftictker.info.get('longName', '').split(' ')[0]
                    if name_search:
                        markets = await get_all_relevant_markets(f"{name_search} beat earnings")
                        best_market = await find_best_in_markets(markets)
                except: pass
        
        if not best_market:
            self.console.print(f"[yellow]Warning: No active earnings market found for {ticker_symbol} on Polymarket.[/yellow]")
            market_prob = 0.5 # Neutral if not found
            question = "N/A (No Market Found)"
        else:
            market_prob = best_market.yes_price
            question = best_market.question

        # 2. Fetch yfinance historical EPS data
        with self.console.status(f"[bold green]Fetching Historical EPS Data (last {lookback})..."):
            try:
                dates = yftictker.earnings_dates
                
                if dates is None or dates.empty:
                    self.console.print(f"[red]Error: Could not find earnings history for {ticker_symbol} on yfinance.[/red]")
                    return

                # Parse lookback
                years = 2
                if "y" in lookback:
                    years = int(lookback.replace("y", ""))
                
                # Use current date with timezone from data
                now = datetime.now(dates.index.tz)
                start_date = now - timedelta(days=365 * years)
                
                # Split into historical and upcoming
                # We also include dates in the VERY recent past (last 3 days) that have NaN Reported EPS
                # as "Just Reported" instead of filtering them out.
                historical = dates[dates.index < now]
                valid_historical = historical.dropna(subset=['Reported EPS', 'EPS Estimate'])
                
                # Find "Just Reported" (in past 3 days but no numbers yet)
                just_reported = historical[
                    (historical.index >= now - timedelta(days=3)) & 
                    (historical['Reported EPS'].isna()) & 
                    (historical['EPS Estimate'].notna())
                ]
                
                upcoming = dates[dates.index >= now].sort_index().head(1)
                
                if valid_historical.empty and upcoming.empty and just_reported.empty:
                    self.console.print(f"[red]Error: No earnings dates found in the last {lookback} for {ticker_symbol}.[/red]")
                    return
                
                last_n_years = valid_historical[valid_historical.index > start_date]
                
                if last_n_years.empty and upcoming.empty and just_reported.empty:
                    self.console.print(f"[red]Error: No earnings dates found in the last {lookback} for {ticker_symbol}.[/red]")
                    return
                
                # Check for EPS Estimate and Reported EPS
                if 'EPS Estimate' not in last_n_years.columns or 'Reported EPS' not in last_n_years.columns:
                    self.console.print(f"[red]Error: Missing EPS Estimate/Reported columns for {ticker_symbol}.[/red]")
                    return
                
                data = last_n_years
                beats = data[data['Reported EPS'] > data['EPS Estimate']]
                beat_rate = len(beats) / len(data) if not data.empty else 0
                
                # Calculate Avg Surprise
                if not data.empty:
                    if 'Surprise(%)' in data.columns:
                        import pandas as pd
                        avg_surprise = pd.to_numeric(data['Surprise(%)'], errors='coerce').mean()
                    else:
                        avg_surprise = ((data['Reported EPS'] - data['EPS Estimate']) / data['EPS Estimate'].abs() * 100).mean()
                else:
                    avg_surprise = 0

                # Upcoming estimation
                upcoming_est = "N/A"
                upcoming_date = "N/A"
                if not upcoming.empty:
                    upcoming_est = f"{upcoming.iloc[0]['EPS Estimate']:.2f}"
                    upcoming_date = upcoming.index[0].strftime("%Y-%m-%d")

            except Exception as e:
                self.console.print(f"[red]Error analyzing historical data: {str(e)}[/red]")
                return

        # 3. Fetch Polygon (Massive) Financials
        with self.console.status(f"[bold green]Fetching Massive Financials for {ticker_symbol}..."):
            from agent.tools.financials_tool import FinancialsTool
            fin_tool = FinancialsTool()
            # Set provider to massive explicitly for this check
            fin_tool.provider = "massive"
            
            poly_data = {}
            try:
                # Get quarterly income statement
                fin_json = fin_tool.get_financials(ticker=ticker_symbol, statement_type="income", period="quarterly")
                poly_data = json.loads(fin_json)
                fin_tool.close()
            except Exception as e:
                self.console.print(f"[dim yellow]Warning: Massive data unavailable: {e}[/dim yellow]")

        # 4. AI Research & Sentiment
        ai_prob = "N/A"
        ai_reasoning = "N/A"
        with self.console.status(f"[bold green]AI Researching latest news for {ticker_symbol}..."):
            try:
                from agent.tools.web_tool import WebSearchTool
                web_tool = WebSearchTool()
                search_query = f"{ticker_symbol} stock earnings outlook news guidance beat miss"
                search_results = web_tool.search(search_query)
                
                # Prepare prompt for LLM
                prompt = f"""
                Analyze the following data for {ticker_symbol} and estimate the probability of them beating their upcoming earnings EPS estimate.
                
                HISTORICAL PERFORMANCE:
                - Beat Rate (Last {lookback}): {beat_rate:.1%}
                - Average Surprise Magnitude: {avg_surprise:+.1f}%
                
                UPCOMING EVENT:
                - Estimate: {upcoming_est}
                - Expected Date: {upcoming_date}
                
                RECENT FINANCIALS (Massive API):
                - Revenue: {poly_data.get('revenues', {}).get('value', 'N/A')}
                - Gross Profit: {poly_data.get('gross_profit', {}).get('value', 'N/A')}
                - Net Income: {poly_data.get('net_income_loss', {}).get('value', 'N/A')}
                
                POLYMARKET SENTIMENT:
                - Market YES Price (Winning Probability): {market_prob:.1%}
                
                LATEST NEWS/RESEARCH RESULTS:
                {search_results}
                
                Task: 
                1. Give a probability (0% to 100%) that they will beat the earnings EPS estimate.
                2. Provide a concise reasoning summary (max 3-4 sentences). 
                3. MUST include a "Bull Case" and "Bear Case" summary in your reasoning.
                
                Return exactly in this format:
                PROBABILITY: XX%
                REASONING: [Your reasoning with Bull/Bear breakdown]
                """
                
                # Use self.agent.llm if available, otherwise fallback
                llm = getattr(self.agent, 'llm', None)
                if llm:
                    response = await asyncio.to_thread(llm.invoke, prompt)
                    response_text = response.content if hasattr(response, "content") else str(response)
                    
                    # Parse probability
                    prob_match = re.search(r"PROBABILITY:\s*(\d+)%", response_text, re.IGNORECASE)
                    if prob_match:
                        ai_prob = f"{prob_match.group(1)}%"
                    
                    # Parse reasoning
                    reasoning_match = re.search(r"REASONING:\s*(.*)", response_text, re.DOTALL | re.IGNORECASE)
                    if reasoning_match:
                        ai_reasoning = reasoning_match.group(1).strip()
                else:
                    ai_reasoning = "AI Research unavailable (LLM not configured)."
                
                web_tool.close()
            except Exception as e:
                ai_reasoning = f"AI Research failed: {e}"

        # 5. Display Results
        
        # A. Comparison Table
        market_label = "Current Forecast (YES)"
        if best_market and getattr(best_market, 'closed', False):
            res = getattr(best_market, 'resolution', 'Unknown')
            market_label = f"Resolution ({res})"
            if res:
                if res.upper() == 'YES': market_prob = 1.0
                elif res.upper() == 'NO': market_prob = 0.0
            
        table = Table(title=f"Earnings Prediction Analysis: {ticker_symbol}", box=None)
        table.add_column("Source", style="bold cyan")
        table.add_column("Metric", style="dim")
        table.add_column("Value", justify="right")
        
        table.add_row("Polymarket", market_label, f"[bold]{market_prob:.1%}[/bold]")
        table.add_row("yfinance", "Upcoming Estimate", f"[bold yellow]{upcoming_est}[/bold yellow] ({upcoming_date})")
        table.add_row("yfinance", f"Hist. Beat Rate ({lookback})", f"[bold green]{beat_rate:.1%}[/bold green]" if beat_rate > market_prob else f"[bold]{beat_rate:.1%}[/bold]")
        table.add_row("yfinance", "Avg Surprise Magnitude", f"{avg_surprise:+.1f}%")
        
        # Add Polygon Data if available
        if poly_data and "error" not in poly_data:
            rev_data = poly_data.get("revenues", {})
            gp_data = poly_data.get("gross_profit", {})
            ni_data = poly_data.get("net_income_loss", {})
            
            def fmt_val(d):
                v = d.get("value")
                if v is None: return "N/A"
                if abs(v) >= 1e9: return f"${v/1e9:.1f}B"
                if abs(v) >= 1e6: return f"${v/1e6:.1f}M"
                return f"${v:,.0f}"

            table.add_row("Massive", f"Revenue ({poly_data.get('_metadata',{}).get('fiscal_period','Q?')})", f"[bold cyan]{fmt_val(rev_data)}[/bold cyan]")
            table.add_row("Massive", "Gross Profit", fmt_val(gp_data))
            table.add_row("Massive", "Net Income", fmt_val(ni_data))

        table.add_row("AI Agent", "Search-Based Beat Prob.", f"[bold magenta]{ai_prob}[/bold magenta]")
        table.add_row("yfinance", "Sample Size (Quarters)", str(len(data)))
        
        edge = beat_rate - market_prob
        edge_color = "green" if edge > 0.05 else "red" if edge < -0.05 else "white"
        table.add_row("ANALYSIS", "[italic]Historical Edge[/italic]", f"[bold {edge_color}]{edge:+.1%}[/bold {edge_color}]")
        
        self.console.print(table)
        
        # B. Detailed Earnings History
        history_title = f"Historical EPS Track Record (Last {len(data)} Quarters)"
        if not upcoming.empty:
            history_title += f" + Upcoming {upcoming_date}"
            
        history_table = Table(title=history_title, header_style="bold magenta")
        history_table.add_column("Date", style="dim")
        history_table.add_column("Estimate", justify="right")
        history_table.add_column("Reported", justify="right")
        history_table.add_column("Surprise %", justify="right")
        history_table.add_column("Result", justify="center")

        # 1. Include Just Reported (Pending Data) first
        if not just_reported.empty:
            for date, row in just_reported.sort_index(ascending=False).iterrows():
                # If we have a resolved Polymarket market, use its result
                res_display = "[bold yellow]JUST REPORTED[/bold yellow]"
                if best_market and getattr(best_market, 'closed', False):
                    # Check if the date matches the market end_date (approx)
                    try:
                        m_date = datetime.fromisoformat(best_market.end_date.replace("Z", "+00:00"))
                        if abs((date - m_date).days) <= 5: # Within 5 days
                            res_val = getattr(best_market, 'resolution', 'Unknown')
                            res_display = f"[bold green]BEAT[/bold green] (Poly)" if res_val == 'YES' else f"[bold red]MISS[/bold red] (Poly)" if res_val == 'NO' else res_display
                    except:
                        pass
                
                history_table.add_row(
                    date.strftime("%Y-%m-%d"),
                    f"{row['EPS Estimate']:.2f}",
                    "N/A",
                    "N/A",
                    res_display
                )

        # 2. Include Upcoming next
        if not upcoming.empty:
            row = upcoming.iloc[0]
            history_table.add_row(
                upcoming.index[0].strftime("%Y-%m-%d"),
                f"{row['EPS Estimate']:.2f}",
                "N/A",
                "N/A",
                "[bold yellow]PENDING[/bold yellow]"
            )

        # Show latest reports first
        sorted_data = data.sort_index(ascending=False)
        for date, row in sorted_data.iterrows():
            est = row['EPS Estimate']
            rep = row['Reported EPS']
            surp = row.get('Surprise(%)') or ((rep - est) / abs(est) * 100 if est != 0 else 0)
            
            # Convert surp to float for comparison if it's a series or weird type
            try:
                surp_val = float(surp)
            except:
                surp_val = 0
                
            res = "[bold green]BEAT[/bold green]" if rep > est else "[bold red]MISS[/bold red]"
            surp_style = "green" if surp_val > 0 else "red"
            
            history_table.add_row(
                date.strftime("%Y-%m-%d"),
                f"{est:.2f}",
                f"{rep:.2f}",
                f"[{surp_style}]{surp_val:+.1f}%[/{surp_style}]",
                res
            )
        
        self.console.print(history_table)
        
        if ai_reasoning != "N/A":
            self.console.print(Panel(ai_reasoning, title="[bold magenta]AI Research Reasoning[/bold magenta]", border_style="magenta"))

        self.console.print(Panel(f"[dim]{question}[/dim]", title="Polymarket Reference"))

    def _show_help(self):
        table = Table(title="FinCode Global Commands (BASH-STYLE)", show_header=True, header_style="bold cyan")
        table.add_column("Command", style="bold yellow")
        table.add_column("Description")
        table.add_column("Speed", style="italic green")

        table.add_row("load (ticker)", "Direct profile lookup (Massive)", "Instant")
        table.add_row("news (ticker)", "Direct news lookup (xAI/Grok)", "Instant")
        table.add_row("financials (ticker)", "Direct financials lookup (Massive)", "Instant")
        table.add_row("quote (ticker)", "Real-time quote data", "Instant")
        table.add_row("poly:backtest (city) (numdays)", "Cross-Sectional YES/NO Backtest", "5-10s")
        table.add_row("poly:predict (city) (numdays)", "Multi-day Highest-Prob Prediction", "5-10s")
        table.add_row("poly:predict earnings (ticker) (days) (lookback)", "Compare YF Beat Rate vs Polymarket Price", "3-5s")
        table.add_row("poly:weather (city)", "Scan for weather opportunities or search by city", "Instant")
        table.add_row("poly:buy (paper/real) (amt) (id)", "Buy YES shares (Default: Paper)", "~2s")
        table.add_row("poly:sell (paper/real) (id/amt) (id)", "Sell shares (Default: Paper)", "~2s")
        table.add_row("poly:portfolio (paper/real)", "View positions (Default: Real)", "Instant")
        table.add_row("reset, r, ..", "Reset context/ticker", "-")
        table.add_row("help, h, ?", "Displays this menu", "-")
        table.add_row("cls", "Clear screen", "-")
        table.add_row("exit, q", "Quit application", "-")
        
        self.console.print(table)
        self.console.print("\n[bold cyan]Examples:[/bold cyan]")
        self.console.print("  [yellow]poly:weather London[/yellow] - Search for London weather markets")
        self.console.print("  [yellow]poly:weather \"temperature New York\"[/yellow] - Detailed keyword search")
        self.console.print("\n[italic]Note: Any other input is handled by the AI Research Agent (LangGraph).[/italic]")
