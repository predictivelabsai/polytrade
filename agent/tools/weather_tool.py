import httpx
import os
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime
import logging
import statistics

logger = logging.getLogger(__name__)


@dataclass
class WeatherForecast:
    """Represents weather forecast data."""
    city: str
    latitude: float
    longitude: float
    high_temp: float  # Fahrenheit
    low_temp: float   # Fahrenheit
    avg_temp: float   # Fahrenheit
    condition: str
    timestamp: str
    probability_high: float
    probability_low: float
    probability_avg: float
    high_temp_c: Optional[float] = None
    low_temp_c: Optional[float] = None
    avg_temp_c: Optional[float] = None
    hourly_data: Optional[List[Dict[str, Any]]] = None


class WeatherClient:
    """Client for Tomorrow.io weather API."""

    BASE_URL = "https://api.tomorrow.io/v4"
    FORECAST_ENDPOINT = "/weather/forecast"

    def __init__(self, api_key: str):
        """Initialize weather client.
        
        Args:
            api_key: Tomorrow.io API key
        """
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30.0)
        self.city_coordinates = {
            "London": {"lat": 51.5074, "lon": -0.1278},
            "New York": {"lat": 40.7128, "lon": -74.0060},
            "Seoul": {"lat": 37.5665, "lon": 126.9780},
            "Tokyo": {"lat": 35.6762, "lon": 139.6503},
            "Paris": {"lat": 48.8566, "lon": 2.3522},
            "Singapore": {"lat": 1.3521, "lon": 103.8198},
            "Hong Kong": {"lat": 22.3193, "lon": 114.1694},
            "Dubai": {"lat": 25.2048, "lon": 55.2708},
        }

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def get_day_weather(self, city: str, date_str: str) -> Optional[Dict[str, Any]]:
        """Fetch weather for a specific single day (Forecast/Today).
        
        Args:
            city: City name
            date_str: Date (YYYY-MM-DD)
            
        Returns:
            Weather data matching the Visual Crossing format
        """
        try:
            target_dt = datetime.strptime(date_str, "%Y-%m-%d").date()
            
            # 1. Fetch forecast with both daily and hourly timelines
            # This is a bit redundant if called many times, but safer for now.
            # We use 1d and 1h to ensure we have the best data.
            forecast_data = await self.get_full_forecast_data(city)
            if not forecast_data:
                return None
            
            # 2. Check Daily timeline for the absolute Max/Min for the day
            # Polymarket resolves on calendar day highs. Daily timeline is most accurate.
            daily_timeline = forecast_data.get("timelines", {}).get("daily", [])
            for day in daily_timeline:
                day_time = day.get("time", "")
                if not day_time: continue
                day_dt = datetime.fromisoformat(day_time.replace('Z', '+00:00')).date()
                
                if day_dt == target_dt:
                    val = day.get("values", {})
                    # For current-day predictions, Tomorrow.io daily high 
                    # is the actual recorded high so far OR the forecast high.
                    return {
                        "tempmax": val.get("temperatureMax"),
                        "tempmin": val.get("temperatureMin"),
                        "temp": val.get("temperatureApparentAvg") or val.get("temperatureAvg"),
                        "forecast_time": datetime.now().strftime("%m-%d %H:%M")
                    }
            
            # 3. Fallback to Hourly calculation if daily is missing for that day
            hourly_timeline = forecast_data.get("timelines", {}).get("hourly", [])
            day_hours = []
            for entry in hourly_timeline:
                entry_dt = datetime.fromisoformat(entry["time"].replace('Z', '+00:00')).date()
                if entry_dt == target_dt:
                    day_hours.append(entry["values"].get("temperature", 0))
            
            if day_hours:
                return {
                    "tempmax": max(day_hours),
                    "tempmin": min(day_hours),
                    "temp": sum(day_hours) / len(day_hours),
                    "forecast_time": datetime.now().strftime("%m-%d %H:%M")
                }

            return None
        except Exception as e:
            logger.error(f"Tomorrow.io get_day_weather error for {city} on {date_str}: {e}")
            return None

    async def get_full_forecast_data(self, city: str) -> Optional[Dict[str, Any]]:
        """Internal helper to get raw response from Tomorrow.io with both timelines."""
        try:
            city_normalized = city.title()
            
            # Use coordinates if we have them (more precise), otherwise use city string
            location = f"{city}"
            if city_normalized in self.city_coordinates:
                 coords = self.city_coordinates[city_normalized]
                 location = f"{coords['lat']},{coords['lon']}"
            
            params = {
                "location": location,
                "apikey": self.api_key,
                "units": "imperial",
                "timelines": "1d,1h"
            }
            
            if os.getenv("POLYCODE_DEBUG", "false").lower() == "true":
                print(f"DEBUG: [Tomorrow.io] Real API Request for {city} (location={location})")

            res = await self.client.get(f"{self.BASE_URL}{self.FORECAST_ENDPOINT}", params=params)
            res.raise_for_status()
            return res.json()
        except Exception as e:
            if os.getenv("POLYCODE_DEBUG", "false").lower() == "true":
                print(f"DEBUG: [Tomorrow.io] Error: {e}")
            return None

    async def get_forecast(
        self,
        city: str,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        days: int = 7,
    ) -> Optional[WeatherForecast]:
        """Fetch weather forecast for a city.
        
        Args:
            city: City name
            latitude: Optional latitude override
            longitude: Optional longitude override
            days: Number of days to forecast
            
        Returns:
            WeatherForecast object or None if error
        """
        try:
            # Get coordinates (normalize city name to title case for lookup)
            if latitude is None or longitude is None:
                city_normalized = city.title()  # Convert to title case (e.g., "london" -> "London")
                if city_normalized not in self.city_coordinates:
                    logger.error(f"City {city} not found in coordinates")
                    return None
                coords = self.city_coordinates[city_normalized]
                latitude = coords["lat"]
                longitude = coords["lon"]

            params = {
                "location": f"{latitude},{longitude}",
                "apikey": self.api_key,
                "units": "imperial",
            }

            response = await self.client.get(
                f"{self.BASE_URL}{self.FORECAST_ENDPOINT}",
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            
            # Parse hourly forecast
            timeline = data.get("timelines", {}).get("hourly", [])
            if not timeline:
                # Fallback to daily if hourly is missing
                timeline = data.get("timelines", {}).get("daily", [])
            
            if not timeline:
                 return None

            first_point = timeline[0]
            values = first_point.get("values", {})
            
            # Calculate range from timeline
            temps_f = [float(p.get("values", {}).get("temperature", 65)) for p in timeline[:24]] # Next 24 hours
            high_temp_f = max(temps_f) if temps_f else float(values.get("temperature", 65))
            low_temp_f = min(temps_f) if temps_f else float(values.get("temperature", 65))
            avg_temp_f = statistics.mean(temps_f) if temps_f else float(values.get("temperature", 65))
            
            # Celsius conversions
            def to_celsius(f): return round((f - 32) * 5/9, 2)
            
            forecast = WeatherForecast(
                city=city,
                latitude=latitude,
                longitude=longitude,
                high_temp=high_temp_f,
                low_temp=low_temp_f,
                avg_temp=avg_temp_f,
                high_temp_c=to_celsius(high_temp_f),
                low_temp_c=to_celsius(low_temp_f),
                avg_temp_c=to_celsius(avg_temp_f),
                condition=self._map_weather_code(values.get("weatherCode", 0)),
                timestamp=first_point.get("time", datetime.now().isoformat()),
                probability_high=1.0,
                probability_low=1.0,
                probability_avg=1.0,
                hourly_data=timeline,
            )
            
            return forecast
        except Exception as e:
            logger.error(f"Error fetching forecast for {city}: {e}")
            return None

    async def get_forecasts_for_cities(
        self,
        cities: List[str],
    ) -> Dict[str, Optional[WeatherForecast]]:
        """Fetch forecasts for multiple cities.
        
        Args:
            cities: List of city names
            
        Returns:
            Dictionary mapping city names to WeatherForecast objects
        """
        forecasts = {}
        for city in cities:
            forecast = await self.get_forecast(city)
            forecasts[city] = forecast
        return forecasts

    def calculate_probability(
        self,
        actual_temp: float,
        target_temp: float,
        deviation: float = 3.5,
    ) -> float:
        """Calculate probability that temperature will be within range.
        
        Args:
            actual_temp: Actual/forecasted temperature
            target_temp: Target temperature threshold
            deviation: Temperature deviation in Fahrenheit
            
        Returns:
            Probability as float between 0 and 1
        """
        # Simple probability calculation based on deviation
        # If actual is within ±deviation of target, probability is high
        diff = abs(actual_temp - target_temp)
        
        if diff <= deviation:
            # Linear interpolation from 1.0 to 0.5
            probability = 1.0 - (diff / (2 * deviation)) * 0.5
        else:
            # Exponential decay beyond deviation
            probability = 0.5 * (0.5 ** ((diff - deviation) / deviation))
        
        return max(0.0, min(1.0, probability))

    def _parse_forecast(
        self,
        data: Dict[str, Any],
        city: str,
        latitude: float,
        longitude: float,
    ) -> Optional[WeatherForecast]:
        """Parse raw forecast data.
        
        Args:
            data: Raw forecast data from API
            city: City name
            latitude: Latitude
            longitude: Longitude
            
        Returns:
            WeatherForecast object
        """
        try:
            timelines = data.get("timelines", {})
            daily = timelines.get("daily", [])
            
            if not daily:
                logger.warning(f"No daily forecast data for {city}")
                return None

            # Get first day forecast
            first_day = daily[0]
            values = first_day.get("values", {})
            
            high_temp = float(values.get("temperatureMax", 70))
            low_temp = float(values.get("temperatureMin", 50))
            avg_temp = float(values.get("temperature", (high_temp + low_temp) / 2))
            
            # Map weather code to condition
            weather_code = values.get("weatherCode", 1000)
            condition = self._map_weather_code(weather_code)
            
            # Calculate probabilities with ±3.5°F deviation
            prob_high = self.calculate_probability(high_temp, high_temp, 3.5)
            prob_low = self.calculate_probability(low_temp, low_temp, 3.5)
            prob_avg = self.calculate_probability(avg_temp, avg_temp, 3.5)
            
            return WeatherForecast(
                city=city,
                latitude=latitude,
                longitude=longitude,
                high_temp=high_temp,
                low_temp=low_temp,
                avg_temp=avg_temp,
                condition=condition,
                timestamp=first_day.get("time", ""),
                probability_high=prob_high,
                probability_low=prob_low,
                probability_avg=prob_avg,
            )
        except Exception as e:
            logger.error(f"Error parsing forecast for {city}: {e}")
            return None

    def _map_weather_code(self, code: int) -> str:
        """Map Tomorrow.io weather code to human-readable condition.
        
        Args:
            code: Weather code from API
            
        Returns:
            Weather condition string
        """
        weather_map = {
            0: "Unknown",
            1000: "Clear",
            1100: "Mostly Clear",
            1101: "Partly Cloudy",
            1102: "Mostly Cloudy",
            1001: "Cloudy",
            2000: "Fog",
            2100: "Light Fog",
            4000: "Drizzle",
            4001: "Rain",
            4200: "Light Rain",
            4201: "Rain",
            5000: "Snow",
            5001: "Flurries",
            5100: "Light Snow",
            5101: "Heavy Snow",
            6000: "Freezing Drizzle",
            6001: "Freezing Rain",
            6200: "Light Freezing Rain",
            6201: "Heavy Freezing Rain",
            7000: "Ice Pellets",
            7101: "Heavy Ice Pellets",
            7102: "Light Ice Pellets",
            8000: "Thunderstorm",
        }
        return weather_map.get(code, "Unknown")


# Singleton instance
_client: Optional[WeatherClient] = None


async def get_weather_client(api_key: str) -> WeatherClient:
    """Get or create weather client.
    
    Args:
        api_key: Tomorrow.io API key
        
    Returns:
        WeatherClient instance
    """
    global _client
    if _client is None:
        _client = WeatherClient(api_key=api_key)
    return _client
