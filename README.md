# phone-location-mcp

MCP server that lets an AI agent know where your phone is. An Android phone running GPSLogger pushes location data to a receiver, which the MCP server reads and exposes as tools.

```
Phone (GPSLogger) ──HTTP──▶ receiver.py ──JSON──▶ phone-location.json
                                                     │
AI Agent ──stdio──▶ server.py (MCP) ────────────────┘
```

## Setup

Install dependencies: `pip install fastmcp`

**Receiver** — run `receiver.py` on a machine your phone can reach. Open port 8765/tcp (Tailscale-only recommended).

**MCP server** — add to your agent's MCP config:
```yaml
mcp_servers:
  phone-location:
    command: python3
    args:
      - ./server.py
    enabled: true
```

**Phone** — install [GPSLogger](https://f-droid.org/en/packages/com.mendhak.gpslogger/) from F-Droid, enable "Log to custom URL", set URL to:
```
http://<host>:8765/location?lat=%lat&lon=%lon&acc=%acc&spd=%spd&dir=%dir&alt=%alt&prov=%prov&time=%time
```
Method: GET.

## MCP tools

`get` — full JSON: coordinates, accuracy, speed, bearing, altitude, freshness, maps link.

`summary` — one line: "Phone was at 55.47, -4.59 (±14m) 2 minutes ago" + maps link.

## License

BSD Zero Clause.
