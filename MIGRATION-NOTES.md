# MIGRATION NOTES — phone-location-mcp (mcp-2x migration, chain B.1)

## PROBE-GATE PASS — 2026-09-16 (T10, scratch HERMES_HOME live gate)

`hermes mcp test phone-location` run under BOTH scratch configs spawning the cutover
TARGET command line `/usr/bin/python3 /home/kimbo/projects/phone-location-mcp/server.py`
(Python 3.12.3, post-rewrite stdlib-only server at HEAD dd99f68). PASS judged by stdout
(recipe 1 — never the exit code):

- auto (no `protocol` key; initialize→-32601→discover path):
  `✓ Connected (1537ms)` + `✓ Tools discovered: 2`
- stateless (`protocol: stateless`):
  `✓ Connected (1985ms)` + `✓ Tools discovered: 2`

Call-shape evidence (R6: no live tools/call through the probe — `hermes mcp test` takes
only a server name): `$BIN server.py` driven over stdio with canned env
`PHONE_LOCATION_DATA_FILE=/nonexistent/nope.json` (T05 heredoc route) —
`server/discover` answered with `serverInfo {name: phone-location, version: 1.1.0}` and
`tools/call summary` returned the T02-recorded no-data string verbatim as
`content[0].text` (string passthrough, no structuredContent).

Gate hygiene: scratch configs lived under the task workspace and were deleted after the
run; `~/.hermes/config.yaml` byte-unchanged (md5 `d59414e484ecc246835fd1ce5baf141d` before
and after, captured IN TASK); no `~/.hermes/config.yaml.new`; zero leftover probe-spawned
server processes (`/usr/bin/python3 .../server.py` — the two pre-existing mcp-venv `$PYO`
servers are the live production gateway session, untouched pre-cutover).

Zero fixes needed: server.py and tests untouched by this gate. Cutover target line is
PROVEN per _chain.md § Cutover note — D.1 may flip config to `/usr/bin/python3` +
`protocol: stateless` (target stanza per handoff note).
