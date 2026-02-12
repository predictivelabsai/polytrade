import os
import json
import httpx
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

class StockGraphTool:
    """Tool for price graphs (GP) and intraday analysis (GIP)."""

    def __init__(self):
        self.api_key = os.getenv("MASSIVE_API_KEY")
        self.base_url = "https://api.massive.com"
        self.client = httpx.Client(timeout=30.0)

    def get_price_graph(
        self, 
        ticker: str, 
        timespan: str = "day", 
        multiplier: int = 1,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None
    ) -> str:
        """
        Get a standard price graph data with volume (GP command).
        
        Args:
            ticker: Stock ticker symbol (e.g., AAPL)
            timespan: The size of the time window (minute, hour, day, week, month, quarter, year)
            multiplier: The size of the timespan multiplier
            from_date: Start date (YYYY-MM-DD), defaults to 30 days ago
            to_date: End date (YYYY-MM-DD), defaults to today
        """
        if not self.api_key:
            return json.dumps({"error": "MASSIVE_API_KEY not configured"})

        ticker = ticker.upper()
        if not to_date:
            to_date = datetime.now().strftime("%Y-%m-%d")
        if not from_date:
            from_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        try:
            url = f"{self.base_url}/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from_date}/{to_date}"
            response = self.client.get(url, params={"apiKey": self.api_key})
            response.raise_for_status()
            return json.dumps(response.json())
        except Exception as e:
            return json.dumps({"error": str(e)})

    def get_intraday_graph(self, ticker: str) -> str:
        """
        Intraday price graph for analyzing trends within a single day (GIP command).
        Fetches 1-minute aggregates for the last trading day.
        """
        # Calculate last trading day (approximate)
        last_day = datetime.now().strftime("%Y-%m-%d")
        # We'll use the GP method with 1-minute resolution
        return self.get_price_graph(ticker, timespan="minute", multiplier=1, from_date=last_day, to_date=last_day)

    def close(self):
        self.client.close()
