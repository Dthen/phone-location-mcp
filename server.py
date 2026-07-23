#!/usr/bin/env python3
"""MCP server for phone GPS location — reads data stored by the GPSLogger receiver."""

import json
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DATA_FILE = Path(__file__).parent / "phone-location.json"
mcp = FastMCP("phone-location")


def _load():
    if not DATA_FILE.exists():
        return None
    try:
        return json.loads(DATA_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _plural(n, word):
    return f"{n} {word}{'s' if n != 1 else ''}"


def _age(data):
    received = datetime.fromisoformat(data["received_at"])
    seconds = (datetime.now(timezone.utc) - received).total_seconds()
    if seconds < 60:
        return round(seconds), f"at this location for {_plural(int(seconds), 'second')}"
    elif seconds < 3600:
        return round(seconds), f"at this location for {_plural(int(seconds / 60), 'minute')}"
    elif seconds < 86400:
        return round(seconds), f"at this location for {_plural(int(seconds / 3600), 'hour')}"
    else:
        return round(seconds), f"at this location for {_plural(int(seconds / 86400), 'day')}"


@lru_cache(maxsize=64)
def _reverse_geocode(lat, lon):
    """Look up a human-readable address for coordinates via Nominatim."""
    lat = round(lat, 4)
    lon = round(lon, 4)
    url = (
        f"https://nominatim.openstreetmap.org/reverse"
        f"?format=json&lat={lat}&lon={lon}&zoom=18&addressdetails=1"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "phone-location-mcp/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read())
            return result.get("display_name", "")
    except Exception:
        return ""


@mcp.tool()
def get() -> str:
    """Get the phone's current GPS location with full detail.

    Returns a JSON object with lat, lon, accuracy, speed, bearing, altitude,
    provider, age, freshness, and a reverse-geocoded address. Use for structured data."""

    data = _load()

    if data is None:
        return json.dumps({
            "error": "No location data available yet — the phone has not reported in.",
            "hint": "Ensure GPSLogger is running on the phone and has an active GPS fix."
        })

    age_s, freshness = _age(data)
    place = _reverse_geocode(data["lat"], data["lon"])

    return json.dumps({
        "lat": data["lat"],
        "lon": data["lon"],
        "accuracy_m": data["accuracy_m"],
        "speed_kmh": data["speed_kmh"],
        "bearing": data["bearing"],
        "altitude_m": data["altitude_m"],
        "provider": data["provider"],
        "gps_timestamp_utc": data["timestamp_utc"],
        "age_seconds": age_s,
        "freshness": freshness,
        "place": place,
    }, indent=2)


@mcp.tool()
def summary() -> str:
    """Get a quick human-readable summary of where the phone is right now.

    Returns one line: address, coordinates, accuracy, and how long the phone
    has been at that location. Use for simple queries."""

    data = _load()

    if data is None:
        return "No location data yet — the phone hasn't reported in. Make sure GPSLogger is running and has a GPS fix."

    age_s, freshness = _age(data)
    place = _reverse_geocode(data["lat"], data["lon"])

    location = f"{place} — {data['lat']:.5f}, {data['lon']:.5f}" if place else f"{data['lat']:.5f}, {data['lon']:.5f}"
    return (
        f"📱 Phone is at {location} (±{data['accuracy_m']:.0f}m accuracy) {freshness}"
    )


if __name__ == "__main__":
    mcp.run()
