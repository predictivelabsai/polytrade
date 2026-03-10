"""Visual Crossing API client for fetching historical weather data."""
import os
import httpx
import json
import logging
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class VisualCrossingClient:
    """Client for Visual Crossing Timeline API."""

    BASE_URL = "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Visual Crossing client.
        
        Args:
            api_key: Visual Crossing API key
        """
        self.api_key = api_key or os.getenv("VISUAL_CROSSING_API_KEY")
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def get_historical_weather_range(
        self,
        city: str,
        end_date: str,
        days: int = 7
    ) -> List[Dict[str, Any]]:
        """Fetch historical weather data for a range of dates.
        
        Args:
            city: City name
            end_date: The resolution date (YYYY-MM-DD)
            days: Number of days prior to end_date to fetch
            
        Returns:
            List of daily weather data
        """
        if not self.api_key:
            logger.error("Visual Crossing API key not provided")
            return {"days": [], "forecast_time": None}

        # Calculate range
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=days)
        start_date = start_dt.strftime("%Y-%m-%d")

        # API URL format: /timeline/{city}/{startDate}/{endDate}?key={apiKey}&unitGroup=us
        url = f"{self.BASE_URL}/{city}/{start_date}/{end_date}"
        params = {
            "key": self.api_key,
            "unitGroup": "us",
            "include": "days,hours",
            "contentType": "json"
        }

        import asyncio
        max_retries = 10
        if os.getenv("POLYCODE_DEBUG", "false").lower() == "true":
            print(f"DEBUG: Weather API Request: {url} (params={ {k:v for k,v in params.items() if k != 'key'} })")
        for attempt in range(max_retries):
            response = await self.client.get(url, params=params)
            
            if response.status_code == 429:
                wait_time = (2 ** attempt) * 10 # 10s, 20s, 40s, 80s...
                logger.warning(f"Rate limited (429). Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
                continue
                
            response.raise_for_status()
            data = response.json()
            
            # The API doesn't always provide a single "generation time", 
            # so we use the retrieval time as the forecast reference.
            retrieval_time = datetime.now().strftime("%m-%d %H:%M")
            
            return {
                "days": data.get("days", []),
                "forecast_time": retrieval_time
            }
        
        logger.error("Exhausted all retries for Visual Crossing API")
        return {"days": [], "forecast_time": None}

    async def get_day_weather(self, city: str, date: str) -> Optional[Dict[str, Any]]:
        """Fetch weather for a specific single day.
        
        Args:
            city: City name
            date: Date (YYYY-MM-DD)
            
        Returns:
            Weather data for that day
        """
        res = await self.get_historical_weather_range(city, date, days=0)
        days = res.get("days", [])
        if not days:
            return None
            
        day_data = days[0]
        day_data["forecast_time"] = res.get("forecast_time")
        return day_data
