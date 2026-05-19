from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx


GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}


WEATHER_CODES = {
    0: "Clear",
    1: "Mostly clear",
    2: "Partly cloudy",
    3: "Cloudy",
    45: "Fog",
    48: "Fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Rain showers",
    82: "Heavy showers",
    95: "Thunderstorms",
}


def weather_label(code: int | None) -> str:
    if code is None:
        return "Weather unavailable"
    return WEATHER_CODES.get(code, "Weather")


def search_locations(query: str) -> list[dict[str, Any]]:
    query = query.strip()
    if len(query) < 2:
        return []
    with httpx.Client(timeout=4.0) as client:
        response = client.get(
            GEOCODE_URL,
            params={"name": query, "count": 5, "language": "en", "format": "json"},
        )
        response.raise_for_status()
        results = response.json().get("results", [])
    locations = []
    for item in results:
        admin = item.get("admin1") or item.get("country") or ""
        label = ", ".join(part for part in [item.get("name"), admin] if part)
        locations.append(
            {
                "name": item.get("name", label),
                "label": label,
                "latitude": item["latitude"],
                "longitude": item["longitude"],
                "timezone": item.get("timezone", "auto"),
            }
        )
    return locations


def get_forecast(latitude: float, longitude: float, label: str) -> dict[str, Any]:
    key = f"{latitude:.4f},{longitude:.4f}"
    cached = _cache.get(key)
    if cached and datetime.utcnow() - cached[0] < timedelta(minutes=15):
        return cached[1]
    with httpx.Client(timeout=4.0) as client:
        response = client.get(
            FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "forecast_days": 6,
                "past_days": 3,
                "timezone": "auto",
            },
        )
        response.raise_for_status()
        data = response.json()
    current = data.get("current", {})
    daily = data.get("daily", {})
    code = current.get("weather_code")
    forecast = {
        "ok": True,
        "label": label,
        "temperature": round(current["temperature_2m"]) if current.get("temperature_2m") is not None else None,
        "feels_like": round(current["apparent_temperature"]) if current.get("apparent_temperature") is not None else None,
        "condition": weather_label(code),
        "wind": round(current["wind_speed_10m"]) if current.get("wind_speed_10m") is not None else None,
        "high": round(daily["temperature_2m_max"][0]) if daily.get("temperature_2m_max") else None,
        "low": round(daily["temperature_2m_min"][0]) if daily.get("temperature_2m_min") else None,
        "rain": daily["precipitation_probability_max"][0] if daily.get("precipitation_probability_max") else None,
        "daily": {},
    }
    for idx, day in enumerate(daily.get("time", [])):
        forecast["daily"][day] = {
            "high": round(daily["temperature_2m_max"][idx])
            if daily.get("temperature_2m_max") and daily["temperature_2m_max"][idx] is not None
            else None,
            "low": round(daily["temperature_2m_min"][idx])
            if daily.get("temperature_2m_min") and daily["temperature_2m_min"][idx] is not None
            else None,
            "rain": daily["precipitation_probability_max"][idx]
            if daily.get("precipitation_probability_max")
            else None,
        }
    _cache[key] = (datetime.utcnow(), forecast)
    return forecast
