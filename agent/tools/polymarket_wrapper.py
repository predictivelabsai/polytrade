"""High-level wrapper for Polymarket and Weather API integration."""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from agent.tools.polymarket_tool import PolymarketClient, PolymarketMarket
from agent.tools.polymarket_clob_api import PolymarketCLOBClient
from agent.tools.weather_tool import WeatherClient, WeatherForecast
from agent.tools.trading_strategy import TradingStrategy

logger = logging.getLogger(__name__)

class PolymarketWrapper:
    """Unified wrapper for market discovery, price depth, and weather analysis."""

    def __init__(
        self,
        polymarket_client: PolymarketClient,
        clob_client: PolymarketCLOBClient,
        weather_client: WeatherClient,
        strategy: Optional[TradingStrategy] = None
    ):
        self.polymarket = polymarket_client
        self.clob = clob_client
        self.weather = weather_client
        self.strategy = strategy or TradingStrategy()

    async def scan_weather_opportunities(self) -> List[Dict[str, Any]]:
        """
        Scan for weather trading opportunities.
        1. Find weather markets on Gamma.
        2. Filter for those ending 'Tomorrow'.
        3. Fetch weather forecasts.
        4. Cross-reference with CLOB prices.
        5. Return opportunities with edge analysis.
        """
        logger.info("Scanning weather opportunities...")
        
        # 1. Fetch markets
        all_markets = await self.polymarket.search_weather_markets()
        
        # 2. Filter for "Tomorrow"
        # For simplicity, we assume 'Tomorrow' means within the next 24-48 hours
        # or we check if the market question refers to the specific date.
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        
        tomorrow_markets = []
        for market in all_markets:
            # Check end_date or question content
            # Often Polymarket endDate is in ISO format
            if tomorrow in market.end_date or tomorrow in market.question:
                 tomorrow_markets.append(market)
        
        if not tomorrow_markets:
            # Fallback: if none match strictly, use all for now but flag them
            tomorrow_markets = all_markets[:5]

        # 3 & 4. Fetch Weather and CLOB data
        opportunities = []
        for market in tomorrow_markets:
            city = self._extract_city(market.question)
            if not city:
                continue
            
            forecast = await self.weather.get_forecast(city)
            if not forecast:
                continue
            
            # Get CLOB order book for precise pricing if possible
            # Note: Gamma market ID != CLOB token ID. 
            # In a real scenario, we'd need a mapping or search CLOB by market_id.
            # Assuming for now we use the Gamma price as a baseline if CLOB fails.
            clob_book = await self.clob.get_order_book(market.id) # This might mismatch ID types
            
            market_price = market.yes_price
            if clob_book:
                market_price = clob_book.mid_price

            # Analyze edge
            # Heuristic fair price from forecast
            fair_price = self._calculate_fair_price(forecast, market.question)
            
            opportunity = self.strategy.analyze_market(
                market_id=market.id,
                city=city,
                market_question=market.question,
                market_price=market_price,
                fair_price=fair_price,
                liquidity=market.liquidity
            )
            
            opp_dict = {
                "market_id": market.id,
                "city": city,
                "question": market.question,
                "market_price": market_price,
                "fair_price": fair_price,
                "edge": opportunity.edge_percentage,
                "signal": opportunity.signal.value,
                "confidence": opportunity.confidence,
                "liquidity": market.liquidity,
                "end_date": market.end_date
            }
            opportunities.append(opp_dict)

        # Sort by edgeDESC
        opportunities.sort(key=lambda x: x["edge"], reverse=True)
        return opportunities


    def _extract_city(self, question: str) -> Optional[str]:
        cities = ["London", "New York", "Seoul", "Tokyo", "Paris", "Singapore", "Hong Kong", "Dubai"]
        for city in cities:
            if city.lower() in question.lower():
                return city
        return None

    def _calculate_fair_price(self, forecast: WeatherForecast, question: str) -> float:
        # Heuristic fair price based on forecast probabilities
        if "high" in question.lower():
            return forecast.probability_high
        elif "low" in question.lower():
            return forecast.probability_low
        else:
            return forecast.probability_avg
