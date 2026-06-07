"""
aria/tools/weather_tool.py
───────────────────────────
Fetches current weather data using the Open-Meteo API (free, no auth required).

Flow:
  1. Geocode city name → lat/lon using Open-Meteo geocoding API
  2. Fetch current weather using lat/lon

This tool is intentionally improvable by ARIA's Improvement Engine.
"""

from __future__ import annotations

import httpx

from aria.tools.base import BaseTool, TestCase, ToolResult


# WMO Weather Interpretation Codes → human-readable descriptions
_WMO_CODES: dict[int, str] = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


class WeatherTool(BaseTool):
    """
    Retrieves current weather conditions for a given city.

    Input:
        city (str): City name (e.g., "Dhaka", "London", "New York")
        units (str, optional): "celsius" (default) or "fahrenheit"

    Output:
        A dict with temperature, weather condition, wind speed, humidity, and city info.
    """

    name = "weather_tool"

    _GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
    _WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

    def run(self, input: dict) -> ToolResult:
        city = str(input.get("city", "")).strip()
        units = str(input.get("units", "celsius")).lower()

        if not city:
            return ToolResult(success=False, output=None, error="No city name provided.")

        if units not in ("celsius", "fahrenheit"):
            units = "celsius"

        try:
            # Step 1: Geocode
            geo = self._geocode(city)
            if not geo:
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"Could not find location: '{city}'",
                )

            lat, lon, display_name, country = (
                geo["latitude"], geo["longitude"],
                geo["name"], geo.get("country", "")
            )

            # Step 2: Fetch weather
            weather = self._fetch_weather(lat, lon, units)
            temp_unit = "°C" if units == "celsius" else "°F"

            return ToolResult(
                success=True,
                output={
                    "city": display_name,
                    "country": country,
                    "latitude": lat,
                    "longitude": lon,
                    "temperature": f"{weather['temperature']}{temp_unit}",
                    "condition": weather["condition"],
                    "wind_speed_kmh": weather["wind_speed"],
                    "humidity_percent": weather["humidity"],
                    "units": units,
                },
            )

        except httpx.TimeoutException:
            return ToolResult(success=False, output=None, error="Weather API request timed out.")
        except Exception as exc:
            return ToolResult(success=False, output=None, error=str(exc))

    def _geocode(self, city: str) -> dict | None:
        with httpx.Client(timeout=8.0) as client:
            resp = client.get(
                self._GEOCODE_URL,
                params={"name": city, "count": 1, "language": "en", "format": "json"},
            )
            resp.raise_for_status()
            data = resp.json()

        results = data.get("results", [])
        return results[0] if results else None

    def _fetch_weather(self, lat: float, lon: float, units: str) -> dict:
        temp_unit = "celsius" if units == "celsius" else "fahrenheit"
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
            "temperature_unit": temp_unit,
            "wind_speed_unit": "kmh",
            "timezone": "auto",
        }
        with httpx.Client(timeout=8.0) as client:
            resp = client.get(self._WEATHER_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        current = data["current"]
        wmo_code = int(current.get("weather_code", 0))

        return {
            "temperature": current.get("temperature_2m", "N/A"),
            "humidity": current.get("relative_humidity_2m", "N/A"),
            "wind_speed": current.get("wind_speed_10m", "N/A"),
            "condition": _WMO_CODES.get(wmo_code, f"Unknown ({wmo_code})"),
        }

    def test_cases(self) -> list[TestCase]:
        return [
            TestCase(
                name="empty_city",
                input={"city": ""},
                expected_success=False,
                description="Empty city name must fail.",
            ),
            TestCase(
                name="nonexistent_city",
                input={"city": "xyzabc123notacity"},
                expected_success=False,
                description="Unknown city must fail gracefully.",
            ),
            TestCase(
                name="known_city",
                input={"city": "London"},
                expected_success=True,
                description="London should return weather data.",
            ),
            TestCase(
                name="fahrenheit_units",
                input={"city": "New York", "units": "fahrenheit"},
                expected_success=True,
                description="Fahrenheit units should work.",
            ),
        ]
