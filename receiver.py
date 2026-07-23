#!/usr/bin/env python3
"""GPSLogger HTTP receiver — runs in background, accepts POSTs from phone, stores to JSON."""

import json
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PORT = 8765
DATA_FILE = Path(__file__).parent / "phone-location.json"

_location_cache = None


def _safe_float(val, default=-1.0):
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _parse_params(path, body):
    from urllib.parse import urlparse, parse_qs

    data = {}
    parsed = urlparse(path)
    qs = parse_qs(parsed.query)
    for key in qs:
        data[key] = qs[key][0]

    if body and not data:
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            for pair in body.replace("&", "\n").split("\n"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    data[k] = v
    return data


def _process_location(data):
    global _location_cache

    entry = {
        "lat": _safe_float(data.get("lat"), 0.0),
        "lon": _safe_float(data.get("lon"), 0.0),
        "accuracy_m": _safe_float(data.get("acc") or data.get("accuracy")),
        "speed_kmh": _safe_float(data.get("spd") or data.get("speed")),
        "bearing": _safe_float(data.get("dir") or data.get("bearing") or data.get("bear")),
        "altitude_m": _safe_float(data.get("alt") or data.get("altitude")),
        "provider": data.get("prov") or data.get("provider", "unknown"),
        "timestamp_utc": data.get("time", datetime.now(timezone.utc).isoformat()),
        "received_at": datetime.now(timezone.utc).isoformat(),
    }

    _location_cache = entry
    DATA_FILE.write_text(json.dumps(entry, indent=2))
    return entry


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def _handle(self):
        global _location_cache

        if self.path.startswith("/location"):
            body = b""
            if self.command == "POST":
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")

            data = _parse_params(self.path, body)
            if data:
                try:
                    _process_location(data)
                except Exception as e:
                    print(f"[phone-location] parse error: {e}")

            resp = _location_cache or {"error": "no location yet"}
            self.send_response(200)
        elif self.path == "/health":
            resp = {"status": "ok", "has_location": _location_cache is not None}
            self.send_response(200)
        else:
            resp = {}
            self.send_response(404)

        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(resp).encode())

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    if DATA_FILE.exists():
        try:
            _location_cache = json.loads(DATA_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    print(f"[phone-location] Listening on :{PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
