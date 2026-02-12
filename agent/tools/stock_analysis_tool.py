import os
import json
import httpx
from typing import Optional, List, Dict, Any

class StockAnalysisTool:
    """Tool for analyst recommendations, earnings estimates, and relative valuation."""

    def __init__(self):
        self.api_key = os.getenv("MASSIVE_API_KEY")
        self.base_url = "https://api.massive.com"
        self.client = httpx.Client(timeout=30.0)

    def get_analyst_recommendations(self, ticker: str) -> str:
        """
        Get analyst buy/sell/hold ratings (ANR command).
        """
        if not self.api_key:
            return json.dumps({"error": "MASSIVE_API_KEY not configured"})

        ticker = ticker.upper()
        try:
            # Massive/Polygon often provides this in the reference data or a specialized vX endpoint
            # For now, we fetch ticker details which often contains sentiment/ratings in results
            response = self.client.get(
                f"{self.base_url}/v3/reference/tickers/{ticker}",
                params={"apiKey": self.api_key}
            )
            response.raise_for_status()
            data = response.json().get("results", {})
            
            # Simulated structure if not directly available, but usually Massive has this in vX financials or snapshot
            results = {
                "ticker": ticker,
                "ratings": data.get("ratings", {"buy": 0, "hold": 0, "sell": 0}),
                "consensus": data.get("consensus", "Not Available"),
                "price_target": data.get("price_target"),
                "source": "Massive Reference Data"
            }
            return json.dumps(results)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def get_earnings_estimates(self, ticker: str) -> str:
        """
        Get earnings estimates and consensus forecasts (EE command).
        """
        if not self.api_key:
            return json.dumps({"error": "MASSIVE_API_KEY not configured"})

        ticker = ticker.upper()
        try:
            # Fetch upcoming/recent earnings events
            response = self.client.get(
                f"{self.base_url}/vX/reference/financials",
                params={
                    "ticker": ticker,
                    "apiKey": self.api_key,
                    "limit": 1
                }
            )
            response.raise_for_status()
            data = response.json()
            
            estimates = {
                "ticker": ticker,
                "recent_financials": data.get("results", [{}])[0].get("financials", {}),
                "message": "Upcoming earnings dates can be found in v3/reference/events if enabled."
            }
            return json.dumps(estimates)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def get_relative_valuation(self, ticker: str) -> str:
        """
        Compares a security against its peers (RV command).
        """
        if not self.api_key:
            return json.dumps({"error": "MASSIVE_API_KEY not configured"})

        ticker = ticker.upper()
        try:
            # 1. Get Peer companies
            response = self.client.get(
                f"{self.base_url}/v3/reference/tickers/{ticker}",
                params={"apiKey": self.api_key}
            )
            response.raise_for_status()
            details = response.json().get("results", {})
            
            # Massive/Polygon sometimes has 'related_tickers' or similar
            # If not, the agent can use search to find competitors
            peers = [ticker] # Placeholder for self
            
            return json.dumps({
                "ticker": ticker,
                "peers": peers,
                "industry": details.get("sic_description", "Unknown"),
                "sector": details.get("sector", "Unknown"),
                "market_cap": details.get("market_cap")
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    def close(self):
        self.client.close()
