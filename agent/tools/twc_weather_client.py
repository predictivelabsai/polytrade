"""Client for The Weather Company (TWC) API."""
import httpx
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class TwcWeatherClient:
    """Client for fetching weather data from The Weather Company API."""

    FORECAST_URL = "https://api.weather.com/v3/wx/forecast/daily/5day"
    HISTORY_URL = "https://api.weather.com/v3/wx/conditions/historical/dailysummary/30day"

    def __init__(self, api_key: str):
        """Initialize TWC client.
        
        Args:
            api_key: TWC API key
        """
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def get_day_weather(self, lat: float, lon: float, date_str: str) -> Optional[Dict[str, Any]]:
        """Fetch weather for a specific day using TWC.
        
        Args:
            lat: Latitude
            lon: Longitude
            date_str: Date string (YYYY-MM-DD)
            
        Returns:
            Dict containing 'tempmax' (Fahrenheit) or None if not found.
        """
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        today = datetime.now().date()
        
        if target_date >= today:
            return await self._get_forecast(lat, lon, target_date)
        else:
            return await self._get_history(lat, lon, target_date)

    async def _get_forecast(self, lat: float, lon: float, target_date: datetime.date) -> Optional[Dict[str, Any]]:
        """Fetch forecast from 5-day endpoint."""
        url = f"{self.FORECAST_URL}?geocode={lat},{lon}&format=json&units=e&language=en-US&apiKey={self.api_key}"
        try:
            resp = await self.client.get(url)
            if resp.status_code != 200:
                logger.error(f"TWC Forecast Error: {resp.status_code} {resp.text}")
                return None
            
            data = resp.json()
            valid_times = data.get("validTimeLocal", [])
            max_temps = data.get("calendarDayTemperatureMax", [])
            
            # Find matching date
            # validTimeLocal formats like "2026-01-30T07:00:00-05:00"
            for i, time_str in enumerate(valid_times):
                try:
                    dt = datetime.fromisoformat(time_str[:10]).date()
                    if dt == target_date:
                        temp = max_temps[i]
                        return {"tempmax": temp, "source": "TWC-Forecast"}
                except Exception as e:
                    continue
            
            return None # Date not in forecast range
            
        except Exception as e:
            logger.error(f"TWC Client Forecast Exception: {e}")
            return None

    async def _get_history(self, lat: float, lon: float, target_date: datetime.date) -> Optional[Dict[str, Any]]:
        """Fetch history from 30-day endpoint."""
        # Note: 30day endpoint returns past 30 days
        diff = (datetime.now().date() - target_date).days
        if diff > 30:
            return None # Out of range for this endpoint
            
        url = f"{self.HISTORY_URL}?geocode={lat},{lon}&format=json&units=e&language=en-US&apiKey={self.api_key}"
        try:
            resp = await self.client.get(url)
            if resp.status_code != 200:
                logger.error(f"TWC History Error: {resp.status_code} {resp.text}")
                return None
            
            data = resp.json()
            valid_times = data.get("validTimeLocal", [])
            max_temps = data.get("temperatureMax", [])
            
            for i, time_str in enumerate(valid_times):
                try:
                    dt = datetime.fromisoformat(time_str[:10]).date()
                    if dt == target_date:
                        temp = max_temps[i]
                        return {"tempmax": temp, "source": "TWC-History"}
                except:
                    continue
            
            return None
            
        except Exception as e:
            logger.error(f"TWC Client History Exception: {e}")
            return None
