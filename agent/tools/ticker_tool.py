import os
import json
import httpx
from typing import Optional

class TickerTool:
    """Tool for loading detailed ticker information using Massive (Polygon) API."""

    def __init__(self):
        self.api_key = os.getenv("MASSIVE_API_KEY")
        self.base_url = "https://api.massive.com"
        self.client = httpx.Client(timeout=30.0)

    def get_ticker_details(self, ticker: str) -> str:
        """
        Get detailed information about a stock ticker (DES command).
        
        Args:
            ticker: The stock ticker symbol (e.g., AAPL)
        """
        if not self.api_key:
            return json.dumps({"error": "MASSIVE_API_KEY not configured"})

        ticker = ticker.upper()
        try:
            # 1. Get Ticker Details (Reference Data)
            response = self.client.get(
                f"{self.base_url}/v3/reference/tickers/{ticker}",
                params={"apiKey": self.api_key}
            )
            response.raise_for_status()
            details_data = response.json()
            results = details_data.get("results", {})

            # 2. Get Snapshot (Real-time Price/Quote Data)
            try:
                snap_response = self.client.get(
                    f"{self.base_url}/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}",
                    params={"apiKey": self.api_key}
                )
                if snap_response.status_code == 200:
                    snap_data = snap_response.json().get("ticker", {})
                    # Merge relevant price data into results
                    results["price_data"] = {
                        "last_price": snap_data.get("lastTrade", {}).get("p"),
                        "day": snap_data.get("day", {}),
                        "prevDay": snap_data.get("prevDay", {}),
                        "todaysChange": snap_data.get("todaysChange"),
                        "todaysChangePerc": snap_data.get("todaysChangePerc"),
                        "updated": snap_data.get("updated")
                    }
            except:
                pass 

            return json.dumps(results)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def get_ownership(self, ticker: str) -> str:
        """
        Get ownership summary for a security (OWN command).
        
        Args:
            ticker: The stock ticker symbol (e.g., AAPL)
        """
        if not self.api_key:
            return json.dumps({"error": "MASSIVE_API_KEY not configured"})

        ticker = ticker.upper()
        try:
            # Note: Massive/Polygon ownership data is often in reference data or specialized endpoints
            # We'll try the common reference endpoint for related data if OWN specifically isn't vX
            response = self.client.get(
                f"{self.base_url}/v3/reference/tickers/{ticker}",
                params={"apiKey": self.api_key}
            )
            response.raise_for_status()
            data = response.json().get("results", {})
            
            # Extract market cap, share class shares outstanding for basic OWN view
            ownership_info = {
                "ticker": ticker,
                "market_cap": data.get("market_cap"),
                "weighted_shares_outstanding": data.get("weighted_shares_outstanding"),
                "share_class_shares_outstanding": data.get("share_class_shares_outstanding"),
                "message": "Detailed institutional ownership requires v3/reference/ownership if enabled on Massive."
            }
            return json.dumps(ownership_info)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def close(self):
        self.client.close()
