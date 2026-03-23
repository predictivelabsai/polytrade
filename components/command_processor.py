import os
import sys
import json
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
            self.console.print("[yellow]Exiting PolyTrade...[/yellow]")
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

        # Bloomberg-style commands
        elif cmd == "des":
            ticker = args[0].upper() if args else self.current_ticker
            if not ticker:
                self.console.print("[red]Error: Specify ticker (e.g., des AAPL)[/red]")
                return True, None
            self.current_ticker = ticker
            self.console.print(f"Fetching [bold cyan]Description (DES)[/bold cyan] for {ticker}...")
            result = await self._exec_tool("get_ticker_details", ticker=ticker)
            self._display_data(f"{ticker} Profile (DES)", result)
            return True, None

        elif cmd == "fa":
            ticker = args[0].upper() if args else self.current_ticker
            if not ticker:
                self.console.print("[red]Error: No ticker loaded/specified.[/red]")
                return True, None
            self.console.print(f"Fetching [bold cyan]Financial Analysis (FA)[/bold cyan] for {ticker}...")
            result = await self._exec_tool("get_financials", ticker=ticker, statement_type="all")
            self._display_data(f"{ticker} Financials (FA)", result)
            return True, None

        elif cmd == "anr":
            ticker = args[0].upper() if args else self.current_ticker
            if not ticker:
                self.console.print("[red]Error: No ticker loaded/specified.[/red]")
                return True, None
            self.console.print(f"Fetching [bold cyan]Analyst Recommendations (ANR)[/bold cyan] for {ticker}...")
            result = await self._exec_tool("get_analyst_recommendations", ticker=ticker)
            self._display_data(f"{ticker} Analyst Ratings (ANR)", result)
            return True, None

        elif cmd == "ee":
            ticker = args[0].upper() if args else self.current_ticker
            if not ticker:
                self.console.print("[red]Error: No ticker loaded/specified.[/red]")
                return True, None
            self.console.print(f"Fetching [bold cyan]Earnings Estimates (EE)[/bold cyan] for {ticker}...")
            result = await self._exec_tool("get_earnings_estimates", ticker=ticker)
            self._display_data(f"{ticker} Earnings Estimates (EE)", result)
            return True, None

        elif cmd == "rv":
            ticker = args[0].upper() if args else self.current_ticker
            if not ticker:
                self.console.print("[red]Error: No ticker loaded/specified.[/red]")
                return True, None
            self.console.print(f"Fetching [bold cyan]Relative Valuation (RV)[/bold cyan] for {ticker}...")
            result = await self._exec_tool("get_relative_valuation", ticker=ticker)
            self._display_data(f"{ticker} Relative Valuation (RV)", result)
            return True, None

        elif cmd == "own":
            ticker = args[0].upper() if args else self.current_ticker
            if not ticker:
                self.console.print("[red]Error: No ticker loaded/specified.[/red]")
                return True, None
            self.console.print(f"Fetching [bold cyan]Ownership (OWN)[/bold cyan] for {ticker}...")
            result = await self._exec_tool("get_ownership", ticker=ticker)
            self._display_data(f"{ticker} Ownership (OWN)", result)
            return True, None

        elif cmd == "gp":
            ticker = args[0].upper() if args else self.current_ticker
            if not ticker:
                self.console.print("[red]Error: No ticker loaded/specified.[/red]")
                return True, None
            self.console.print(f"Fetching [bold cyan]Price Graph (GP)[/bold cyan] for {ticker}...")
            result = await self._exec_tool("get_price_graph", ticker=ticker)
            self._display_data(f"{ticker} Price Graph (GP)", result)
            return True, None

        elif cmd == "gip":
            ticker = args[0].upper() if args else self.current_ticker
            if not ticker:
                self.console.print("[red]Error: No ticker loaded/specified.[/red]")
                return True, None
            self.console.print(f"Fetching [bold cyan]Intraday Graph (GIP)[/bold cyan] for {ticker}...")
            result = await self._exec_tool("get_intraday_graph", ticker=ticker)
            self._display_data(f"{ticker} Intraday Graph (GIP)", result)
            return True, None

        elif cmd == "scan":
            self.console.print(f"[bold cyan]Scanning Polymarket Weather Opportunities...[/bold cyan]")
            result = await self._exec_tool("scan_weather_opportunities")
            if isinstance(result, list):
                self._display_data("Weather Opportunities", result)
            elif isinstance(result, dict) and "error" in result:
                self.console.print(f"[red]{result['error']}[/red]")
            else:
                self._display_data("Weather Opportunities", result)
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

            # Handle poly:backtest
            if effective_cmd == "poly:backtest":
                if not effective_args:
                    self.console.print("[red]Error: Usage: poly:backtest <city> <numdays>[/red]")
                    self.console.print("Example: poly:backtest Seoul 1")
                    return True, None
                
                # Parse arguments: Handle multi-word cities, numdays, and optional DATE
                # Format: poly:backtest <city> [numdays] [date]
                
                from datetime import datetime
                numdays = 7  # Default
                target_date = datetime.now().strftime("%Y-%m-%d")
                
                # Copy args to consume
                args_to_parse = effective_args.copy()
                
                # 1. Check for Date at the end (YYYY-MM-DD)
                if args_to_parse and len(args_to_parse[-1]) == 10 and args_to_parse[-1].count('-') == 2:
                    target_date = args_to_parse.pop()
                    
                # 2. Check for NumDays at the end (after date removed)
                if args_to_parse and args_to_parse[-1].isdigit():
                    numdays = int(args_to_parse.pop())
                
                # 3. Remaining is City
                if not args_to_parse:
                     self.console.print("[red]Error: City name is required.[/red]")
                     return True, None
                     
                raw_city = " ".join(args_to_parse).replace('"', '').replace("'", "")
                
                # Handle standard casing unless it's an acronym
                if raw_city.upper() in ["NYC", "LA", "DC", "SF", "NYC."]:
                    city = raw_city.upper()
                else:
                    city = raw_city.title()
                
                self.console.print(f"[bold cyan]Running Cross-Sectional Backtest for {city} on {target_date} for {numdays} days...[/bold cyan]")
                
                # Run backtest
                await self._run_backtest_handler(city, target_date, numdays)
                return True, None

            # Handle poly:weather (with fuzzy matching for typos like 'weathter')
            elif effective_cmd == "poly:portfolio":
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
                    self.console.print("[red]Error: Usage: poly:predict <city> <numdays>[/red]")
                    self.console.print("Example: poly:predict London 2")
                    return True, None
                
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

            elif effective_cmd == "poly:backtestv2" or effective_cmd == "poly:backtest2":
                if not effective_args:
                    self.console.print("[red]Error: Usage: poly:backtestv2 <city> <numdays>[/red]")
                    return True, None
                
                city = effective_args[0].title()
                try:
                    numdays = int(effective_args[1]) if len(effective_args) > 1 else 7
                except ValueError:
                    numdays = 7
                
                from datetime import datetime
                today = datetime.now().strftime("%Y-%m-%d")
                self.console.print(f"[bold cyan]Running Cross-Sectional Backtest V2 for {city} ({numdays} days)...[/bold cyan]")
                await self._run_backtest_handler(city, today, numdays, v2_mode=True)
                return True, None

            elif effective_cmd == "poly:buy":
                if len(effective_args) < 2:
                    self.console.print("[red]Error: Usage: poly:buy <amount> <market_id>[/red]")
                    return True, None
                
                amount = float(effective_args[0])
                market_id = effective_args[1]
                
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
                return True, None

            elif effective_cmd == "poly:sell":
                if len(effective_args) < 2:
                    self.console.print("[red]Error: Usage: poly:sell <amount> <market_id>[/red]")
                    return True, None
                
                amount = float(effective_args[0])
                market_id = effective_args[1]
                
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
                return True, None

            elif effective_cmd == "poly:simbuy":
                if len(effective_args) < 2:
                    self.console.print("[red]Error: Usage: poly:simbuy <amount> <market_id>[/red]")
                    return True, None
                amount = float(effective_args[0])
                market_id = effective_args[1]
                self.console.print(f"[bold cyan]Simulating Buy: {amount} on {market_id}[/bold cyan]")
                result = await self._exec_tool("simulate_polymarket_trade", amount=amount, market_id=market_id)
                self._display_data("Trade Simulation", result)
                return True, None

            elif effective_cmd == "poly:paperbuy":
                if len(effective_args) < 2:
                    self.console.print("[red]Error: Usage: poly:paperbuy <amount> <market_id>[/red]")
                    return True, None
                
                try:
                    amount = float(effective_args[0])
                    market_id = effective_args[1]
                except ValueError:
                    self.console.print("[red]Error: Amount must be a number.[/red]")
                    return True, None
                
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

            elif effective_cmd == "poly:papersell":
                if len(effective_args) < 1:
                    self.console.print("[red]Error: Usage: poly:papersell <transaction_id>[/red]")
                    return True, None
                
                trade_id = effective_args[0]
                
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

            elif effective_cmd == "poly:paperportfolio":
                await self._display_portfolio()
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
            import inspect
            # Prefer the async coroutine if available (avoids asyncio.run inside event loop)
            if hasattr(tool, 'coroutine') and tool.coroutine is not None:
                return await tool.coroutine(**kwargs)
            elif inspect.iscoroutinefunction(tool.func):
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
            self.console.print(f"Source: Massive (Polygon)")
            return

        pretty_json = json.dumps(data, indent=2)
        
        # Check if it's a specialty report (EE, ANR, RV, OWN)
        if isinstance(data, dict):
            # ANR Handling
            if "ratings" in data and "consensus" in data:
                ratings = data.get("ratings", {})
                grid = Table.grid(padding=(0, 2))
                grid.add_column(justify="right", style="bold")
                grid.add_column(justify="left")
                grid.add_row("Consensus:", f"[bold green]{data.get('consensus')}[/bold green]")
                grid.add_row("Price Target:", f"${data.get('price_target')}" if data.get("price_target") else "N/A")
                grid.add_row("Buy/Hold/Sell:", f"{ratings.get('buy', 0)} / {ratings.get('hold', 0)} / {ratings.get('sell', 0)}")
                self.console.print(Panel(grid, title=f"[bold]Analyst Recommendations (ANR) - {data.get('ticker')}[/bold]", border_style="cyan"))
                return

            # OWN Handling
            if "market_cap" in data and "ticker" in data and "message" in data:
                 grid = Table.grid(padding=(0, 2))
                 grid.add_column(justify="right", style="bold")
                 grid.add_column(justify="left")
                 mcap = f"${data['market_cap']:,}" if isinstance(data.get("market_cap"), (int, float)) else "N/A"
                 grid.add_row("Market Cap:", mcap)
                 grid.add_row("Shares Outstanding:", f"{data.get('share_class_shares_outstanding', 0):,}")
                 grid.add_row("Note:", f"[dim]{data.get('message')}[/dim]")
                 self.console.print(Panel(grid, title=f"[bold]Ownership Summary (OWN) - {data.get('ticker')}[/bold]", border_style="cyan"))
                 return

            # RV Handling
            if "peers" in data and "industry" in data:
                peers = ", ".join(data.get("peers", []))
                grid = Table.grid(padding=(0, 2))
                grid.add_column(justify="right", style="bold")
                grid.add_column(justify="left")
                grid.add_row("Industry:", data.get("industry"))
                grid.add_row("Sector:", data.get("sector"))
                grid.add_row("Peers:", peers)
                self.console.print(Panel(grid, title=f"[bold]Relative Valuation (RV) Peers - {data.get('ticker')}[/bold]", border_style="cyan"))
                return

            # GP / GIP (Aggregates) Handling
            if "results" in data and isinstance(data["results"], list) and len(data["results"]) > 0:
                # If there are result items, it's likely a graph response
                results = data["results"]
                table = Table(title=f"Price Aggregates - {data.get('ticker')}", show_header=True, header_style="bold magenta")
                table.add_column("Date/Time", style="dim")
                table.add_column("Open", justify="right")
                table.add_column("High", justify="right")
                table.add_column("Low", justify="right")
                table.add_column("Close", justify="right")
                table.add_column("Volume", justify="right")

                from datetime import datetime
                for res in results[:20]: # Show only first 20 for brevity
                    ts = datetime.fromtimestamp(res["t"] / 1000).strftime('%Y-%m-%d %H:%M')
                    table.add_row(
                        ts,
                        f"${res['o']:.2f}",
                        f"${res['h']:.2f}",
                        f"${res['l']:.2f}",
                        f"${res['c']:.2f}",
                        f"{res['v']:,}"
                    )
                self.console.print(table)
                if len(results) > 20:
                    self.console.print(f"[dim]Showing 20 of {len(results)} data points...[/dim]")
                return

            # FA (All Financials) Handling
            if all(k in data for k in ["income", "balance", "cash_flow"]):
                self.console.print(f"\n[bold green]Aggregated Financial Analysis (FA) for {title}[/bold green]")
                for st_name, st_data in data.items():
                    if "error" in st_data:
                        self.console.print(f"[red]Error fetching {st_name}: {st_data['error']}[/red]")
                    else:
                        self._display_data(f"{st_name.title()} Statement", st_data)
                return

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
        
        # Show all markets that have order book data (forecast is optional)
        display_markets = [
            m for m in markets
            if m.get("yes_book") and m.get("yes_book").get("token_id")
        ]

        if not display_markets:
            self.console.print(f"[bold yellow]Found {len(markets)} markets but none have order book data.[/bold yellow]")
            return

        # Create Table for console output
        table = Table(
            title=f"Weather Markets: {city} ({len(display_markets)} markets)",
            show_header=True,
            header_style="bold magenta",
            expand=True
        )
        table.add_column("Question", style="dim", no_wrap=False, max_width=40)
        table.add_column("Liq", justify="right", width=7)
        table.add_column("YES %", justify="right", style="bright_green", width=6)
        table.add_column("NO %", justify="right", style="bright_red", width=6)
        table.add_column("Resolves", justify="center", style="cyan", width=12)
        table.add_column("Temp Forecast", justify="center", style="yellow", width=14)
        table.add_column("Token ID (YES)", style="dim", no_wrap=True, width=20)

        for m in display_markets:
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
            
            # Format forecast temperature
            if forecast:
                temp_c = forecast['temperature_c']
                temp_f = forecast['temperature_f']
                forecast_str = f"{temp_c}°C/{temp_f}°F"
            else:
                forecast_str = "N/A"
            
            # Token ID for simbuy/buy
            token_id = yes_book.get("token_id", "")
            token_short = f"...{token_id[-12:]}" if token_id else "N/A"

            table.add_row(
                m["question"],
                f"${m['liquidity']/1000:.1f}k",
                f"{yes_fair*100:.0f}%",
                f"{no_fair*100:.0f}%",
                resolution_time,
                forecast_str,
                token_short,
            )
        
        self.console.print(table)
        # Print first 3 full token IDs for easy copy-paste
        self.console.print(f"\n[bold cyan]Token IDs for trading (use with poly:simbuy / poly:buy):[/bold cyan]")
        for m in display_markets[:3]:
            tid = (m.get("yes_book") or {}).get("token_id", "")
            q = m["question"][:50]
            if tid:
                self.console.print(f"  [yellow]{q}[/yellow]")
                self.console.print(f"  [dim]{tid}[/dim]")
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
                
                trade_table = Table(show_header=True, header_style="bold cyan", expand=True)
                trade_table.add_column("Date", style="dim", width=7)
                trade_table.add_column("Target", justify="center", ratio=3)
                trade_table.add_column("VC-Fcst", justify="center", style="magenta", width=7)
                if is_prediction:
                    trade_table.add_column("TM-Fcst", justify="center", style="dim magenta", width=7)
                trade_table.add_column("Actual", justify="center", style="blue")
                if is_prediction or v2_mode:
                    trade_table.add_column("ID", style="cyan", width=8)
                if v2_mode:
                    trade_table.add_column("Side", justify="center", width=6)
                trade_table.add_column("Our%", justify="right", width=5)
                trade_table.add_column("Mkt%", justify="right", width=5)
                trade_table.add_column("Price", justify="right", width=7)
                if not v2_mode:
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

                    row_data = [
                        date_display,
                        t.get("target_display", f"{t['bucket']} ({t['target_f']}°F)"),
                        f"{t['forecast']}°F",
                    ]
                    if is_prediction:
                        row_data.append(f"{t.get('forecast_secondary', 'N/A')}°F")
                    
                    row_data.append(t.get("actual", "N/A"))
                    
                    if is_prediction or v2_mode:
                        row_data.append(str(t.get("market_id", "N/A")))
                    
                    if v2_mode:
                        side = t.get("Side", "NONE")
                        side_color = "bright_green" if side == "YES" else "bright_red" if side == "NO" else "dim"
                        row_data.append(f"[{side_color}]{side}[/{side_color}]")
                        
                    row_data.extend([
                        t["prob"],
                        t.get("market_prob", "N/A"),
                        f"${t['price']:.3f}"
                    ])
                    
                    if not v2_mode:
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
            self.console.print("[yellow]Your portfolio is empty. Use poly:paperbuy to start trading![/yellow]")
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

    def _show_help(self):
        table = Table(title="PolyTrade Global Commands (BASH-STYLE)", show_header=True, header_style="bold cyan")
        table.add_column("Command", style="bold yellow")
        table.add_column("Description")
        table.add_column("Speed", style="italic green")

        table.add_row("load <ticker>", "Direct profile lookup (Massive)", "Instant")
        table.add_row("news [ticker]", "Direct news lookup (xAI/Grok)", "Instant")
        table.add_row("financials [ticker]", "Direct financials lookup (Massive/Polygon)", "Instant")
        table.add_row("quote [ticker]", "Real-time quote data", "Instant")
        table.add_row("poly:backtest <city> <numdays>", "Multi-day Highest-Prob Backtest", "5-10s")
        table.add_row("poly:backtestv2 <city> <numdays>", "Cross-Sectional YES/NO Backtest", "5-10s")
        table.add_row("poly:predict <city> <numdays>", "Multi-day Highest-Prob Prediction", "5-10s")
        table.add_row("poly:weather [city]", "Scan for weather opportunities or search by city", "Instant")
        table.add_row("poly:paperbuy <amt> <id>", "Simulate a trade in your paper portfolio", "Instant")
        table.add_row("poly:papersell <id>", "Sell an open paper trade by ID", "Instant")
        table.add_row("poly:paperportfolio", "View your paper trading performance", "Instant")
        table.add_row("poly:buy <amt> <id>", "REAL Order Execution (Max $1000.00)", "~2s")
        table.add_row("poly:sell <amt> <id>", "REAL Order Selling (Shares)", "~2s")
        table.add_row("poly:portfolio", "View Real On-Chain USDC + Positions", "Instant")
        table.add_row("poly:simbuy <amt> <id>", "Simulate price/slippage without trading", "Instant")
        table.add_row("reset, r, ..", "Reset context/ticker", "-")
        table.add_row("help, h, ?", "Displays this menu", "-")
        table.add_row("cls", "Clear screen", "-")
        table.add_row("exit, q", "Quit application", "-")
        
        self.console.print(table)
        self.console.print("\n[bold cyan]Examples:[/bold cyan]")
        self.console.print("  [yellow]poly:weather London[/yellow] - Search for London weather markets")
        self.console.print("  [yellow]poly:weather \"temperature New York\"[/yellow] - Detailed keyword search")
        self.console.print("\n[italic]Note: Any other input is handled by the AI Research Agent (LangGraph).[/italic]")
