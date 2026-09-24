"""Behavioral release checks for the phone-location MCP server."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import server


FIXTURE = REPO / "golden" / "phone-location.fixture.json"


def test_successful_location_contract_is_sane_and_summary_matches_get(tmp_path, monkeypatch):
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    source["received_at"] = datetime.now(timezone.utc).isoformat()
    location_file = tmp_path / "location.json"
    location_file.write_text(json.dumps(source), encoding="utf-8")

    monkeypatch.setattr(server, "DATA_FILE", location_file)
    monkeypatch.setattr(server, "_reverse_geocode", lambda lat, lon: "Canned release-test place")

    location = json.loads(server.get())
    required = {
        "lat", "lon", "accuracy_m", "speed_kmh", "bearing", "altitude_m",
        "provider", "gps_timestamp_utc", "age_seconds", "freshness", "place",
    }
    assert set(location) == required
    assert -90 <= location["lat"] <= 90
    assert -180 <= location["lon"] <= 180
    assert not (location["lat"] == 0 and location["lon"] == 0)
    assert 0 <= location["age_seconds"] < 86400
    assert location["place"] == "Canned release-test place"

    summary = server.summary()
    assert summary.startswith("📱 Phone is at Canned release-test place — ")
    assert f"{source['lat']:.5f}, {source['lon']:.5f}" in summary
    assert location["freshness"] in summary
