# phone-location-mcp

MCP server that lets an AI agent know where your phone is. An Android phone running GPSLogger pushes location data to a receiver, which the MCP server reads and exposes as tools.

```
Phone (GPSLogger) ──HTTP──▶ receiver.py ──JSON──▶ phone-location.json
                                                     │
AI Agent ──stdio──▶ server.py (MCP) ────────────────┘
```

## Setup

Requirements: Python ≥ 3.10 with nothing but the standard library — `server.py` and
`receiver.py` have zero third-party dependencies (`/usr/bin/python3 server.py` just runs).
(The pre-2026 server needed `pip install fastmcp`; the stdlib-era rewrite dropped the
framework — see `MIGRATION-NOTES.md`.)

**Receiver** — run `receiver.py` on a machine your phone can reach; unchanged and plain
stdlib, since it is a GPSLogger HTTP sidecar, not an MCP server. Open port 8765/tcp
(Tailscale-only recommended).

**MCP server** — add to your agent's MCP config (stdio, MCP protocol era `2026-07-28`,
stateless):
```yaml
mcp_servers:
  phone-location:
    command: /usr/bin/python3
    args:
      - /home/kimbo/projects/phone-location-mcp/server.py
    protocol: stateless
    enabled: true
```

**Phone** — install [GPSLogger](https://f-droid.org/en/packages/com.mendhak.gpslogger/) from F-Droid, enable "Log to custom URL", set URL to:
```
http://<host>:8765/location?lat=%lat&lon=%lon&acc=%acc&spd=%spd&dir=%dir&alt=%alt&prov=%prov&time=%time
```
Method: GET.

## MCP tools

`get` — full JSON containing coordinates, accuracy, speed, bearing, altitude,
GPS timestamp, age/freshness, and a reverse-geocoded place.

`summary` — one human-readable line with reverse-geocoded place, coordinates,
accuracy, and freshness.

## License

BSD Zero Clause.
