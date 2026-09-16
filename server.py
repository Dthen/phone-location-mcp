#!/usr/bin/env python3
"""MCP server for phone GPS location — reads data stored by the GPSLogger receiver."""

import json, sys
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

if hasattr(sys.stdin, "reconfigure"):            # binary/undecodable bytes must not kill the loop
    sys.stdin.reconfigure(errors="replace")      # invalid UTF-8 → U+FFFD → lands in the json.loads except

# ── Era constants (REFERENCE §1 pinned table + constants block) ───────────
ERA_VERSION = "2026-07-28"
SERVER_INFO = {"name": "phone-location", "version": "1.1.0"}  # bump rationale finalized T07
ERA_RESULT_FIELDS = {"resultType": "complete", "ttlMs": 0, "cacheScope": "private"}
RESULT_META = {"io.modelcontextprotocol/serverInfo": SERVER_INFO}     # spec-RECOMMENDED stamp, optional

def era_result(payload):
    """A result carrying the era-strict fields D3 mandates on every response."""
    out = dict(payload)
    out.update(ERA_RESULT_FIELDS)
    out["_meta"] = RESULT_META
    return out

# ── Tools layer (T04: placeholder wiring only; T05 attaches the real surface) ──

DATA_FILE = Path(__file__).parent / "phone-location.json"

TOOLS = []  # T05 replaces: [{"name","description","inputSchema"}] byte-frozen from golden/phone-location.tools.json


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


def handle_call(name, args):
    # T05 replaces this placeholder with the real get/summary dispatch.
    return {"error": f"Unknown tool: {name}"}

# ── MCP JSON-RPC loop (REFERENCE §1 skeleton verbatim; stateless-only D2) ──

def send(resp):
    sys.stdout.write(json.dumps(resp) + "\n")   # exactly one object per line
    sys.stdout.flush()                          # a buffered reply = a client timeout

def main():
    for line in sys.stdin:                      # EOF on stdin ends the loop (see §7)
        line = line.strip()
        if not line: continue
        try: req = json.loads(line)
        except Exception: continue              # garbage lines: skip, NEVER die (§7)
        if not isinstance(req, dict): continue  # valid JSON, not an object ("5", null, [1,2]): skip, NEVER die (§7)
        rid = req.get("id")                     # str or int; absent ⇒ notification
        method = req.get("method")              # null/42/etc must not crash .startswith below
        if not isinstance(method, str): method = ""   # route as unknown-method
        if method == "server/discover":
            send({"jsonrpc":"2.0","id":rid,"result":era_result({
                "supportedVersions":[ERA_VERSION],
                "capabilities":{"tools":{}}})})
        elif method == "tools/list":
            send({"jsonrpc":"2.0","id":rid,"result":era_result({"tools":TOOLS})})
        elif method == "tools/call":
            params = req.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                send({"jsonrpc":"2.0","id":rid,"error":{"code":-32602,
                    "message":"missing required param: params (with string 'name')"}})
                continue
            try:
                result = handle_call(params["name"], params.get("arguments", {}))
                is_err = isinstance(result, dict) and ("error" in result or "transport_error" in result)
                payload: dict = {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
                if is_err:
                    payload["isError"] = True
                send({"jsonrpc":"2.0","id":rid,"result":era_result(payload)})
            except Exception as e:                       # dispatch-level only (shouldn't happen)
                send({"jsonrpc":"2.0","id":rid,"error":{"code":-32603,"message":str(e)}})
        elif method.startswith("notifications/"): pass    # §6
        elif method == "ping":
            send({"jsonrpc":"2.0","id":rid,"result":{}})
        else:
            # includes legacy `initialize` (D2) and every other unknown method, per JSON-RPC
            if rid is None and "id" not in req: continue   # no-id = notification: never respond
            send({"jsonrpc":"2.0","id":rid,"error":{"code":-32601,"message":f"Method not found: {method}"}})

if __name__ == "__main__":
    main()
