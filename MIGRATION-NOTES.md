# MIGRATION NOTES — phone-location-mcp (mcp-2x migration, chain B.1)

## PROBE-GATE PASS — 2026-09-24 (RC re-verification, scratch HERMES_HOME live gate)

`hermes mcp test phone-location` was re-run under BOTH scratch configs spawning the
cutover TARGET command line `/usr/bin/python3 /home/kimbo/projects/phone-location-mcp/server.py`
(Python 3.12.3). PASS judged by stdout (never the exit code):

- auto (no `protocol` key; initialize→-32601→discover path):
  `✓ Connected (1577ms)` + `✓ Tools discovered: 2`
- stateless (`protocol: stateless`):
  `✓ Connected (1679ms)` + `✓ Tools discovered: 2`

Call-shape evidence (R6: no live tools/call through the probe — `hermes mcp test` takes
only a server name): `$BIN server.py` driven over stdio with canned env
`PHONE_LOCATION_DATA_FILE=/nonexistent/nope.json` (T05 heredoc route) —
`server/discover` answered with `serverInfo {name: phone-location, version: 1.1.0}` and
`tools/call summary` returned the T02-recorded no-data string verbatim as
`content[0].text` (string passthrough, no structuredContent).

Gate hygiene: the 2026-09-16 gate left `~/.hermes/config.yaml` byte-unchanged at
md5 `00538b0109637aecbb56ac17453268be` (captured in that task); the 2026-09-24 RC
re-verification observed the then-current file at md5
`d79831aec6f3d9347a77f1b8a4b8dcd4` before and after both scratch probes. No
`~/.hermes/config.yaml.new`; zero leftover probe-spawned server processes
(`/usr/bin/python3 .../server.py` — all `mcp-venvs/.../bin/python3` matches are
pre-existing production gateway sessions, untouched pre-cutover).

Zero fixes needed: server.py and tests untouched by this gate. Cutover target line is
PROVEN per _chain.md § Cutover note — D.1 may flip config to `/usr/bin/python3` +
`protocol: stateless` (target stanza per handoff note).

---


## ERA REWRITE SUMMARY — stdlib server (T03–T09)

The server is now a pure-Python stdlib MCP loop (REFERENCE §1–§7 pattern): fastmcp 3.4.7
framework shed, `pyproject`/venv never existed for it to leave. Wire contract pinned by
characterization against the legacy server before the rewrite (golden/ + T02/T06): two
tools `get`/`summary`, string passthrough (sanctioned R3 DEVIATION from REFERENCE §5
dict-wrapping; both tools return str), no outputSchema, no structuredContent post-migration.
ServerInfo is `phone-location 1.1.0` (T07 — the card's "1.0.0" never existed in any tracked
file; legacy fastmcp self-reported 3.4.7). Era protocol `2026-07-28`, stateless-first
(auto route: initialize→-32601→server/discover). Zero-deps proven by the T09 stdlib-only
import scan + `/usr/bin/python3` import/runability proof (R5: no memory metric). `receiver.py`
out of era scope — plain-stdlib GPSLogger HTTP sidecar, not an MCP server, untouched.
Suite at HEAD: 31 passed.

## RC verification addendum — 2026-09-24

The release-candidate audit re-ran the full current suite (31 passed) and a fresh
isolated `git clone --no-local` (31 passed). It also exercised the real writable
`phone-location.json` through `get` and `summary`: coordinates were in range and
non-null, age was under 24 hours, and the summary agreed with the structured
result. This is a bounded local data-contract check, not cutover F2 fleet
acceptance; no live phone API/tool-use claim is made here.

The stdlib import scan and `/usr/bin/python3` import proof remain clean. The release
repairs are the JSON-RPC notification guard, frozen no-argument schema validation,
tool-level normalization of malformed location data, and the hermetic data-contract
and resource-cleanup regressions; README and this addendum describe the actual
result fields instead of the pre-migration maps-link wording. No release tag was
created.

## CUTOVER HAND-OFF STANZA (for D.1; final freeze in T12)

Config flip is D.1's job in its single restart window — this chain NEVER edits
`~/.hermes/config.yaml` and never deletes the mcp-venv (instant rollback).

```
server key:  phone-location
current (config.yaml:842-846):
  command: /mnt/HC_Volume_105667182/kimbo/mcp-venvs/phone-location-mcp/bin/python3
  args:    [/home/kimbo/projects/phone-location-mcp/server.py]
target after this migration (post-rewrite server is stdlib-only, runs anywhere ≥3.10):
  command: /usr/bin/python3            # Python 3.12.3 on this host — pinned, like PLAN F4 does for transitous
  args:    [/home/kimbo/projects/phone-location-mcp/server.py]
  protocol: stateless
```

T10's scratch-HERMES_HOME gate PROVED this exact line (auto + stateless: `✓ Connected` +
`✓ Tools discovered: 2`, stdout-judged). Version tag/push are owner-gated (R4): local
commits only, no tag in this chain, no pushes."

## CARD-CLAIM RECONCILIATION LEDGER (T12, 2026-09-16)

Card B.1 claims vs live-verified facts — reconciled at chain end:

| card claim | live fact | disposition |
|---|---|---|
| Card step 0: `git tag pre-migration/20260913` | Tag made and verified: `pre-migration/20260914` → `2d52620` (2026-09-14, not 2026-09-13) | SUPERSEDED — tag-date 20260914 |
| Card B.1: version 1.0.0→1.1.0 | No "1.0.0" string ever existed in tracked files; fastmcp self-reported 3.4.7. T07 introduced ServerInfo 1.1.0 | SUPERSEDED — version 1.0.0→1.1.0 correction recorded |
| Card B.1: VmHWM ≤ 25 MB success criterion | R5 RETIRED 2026-09-14 ~15:40 — no memory measurement anywhere | SUPERSEDED per R5 |
| Card B.1: R1 TimeoutError-pin | N/A — urllib-native, never httpx; `grep -rn httpx` zero hits; no shim | N/A with evidence |
| Card B.1: golden byte-identity | MET — golden/phone-location.tools.json tracked, byte-identity asserted in T05 freeze test | MET |
| Card B.1: hermes mcp test | MET — T10 scratch HERMES_HOME auto+stateless gate PASS (stdout-judged) | MET |
| Card B.1: pytest | MET — 31 passed (repo + fresh clone) | MET |
| Card B.1: reviews | MET — one review round per task, all approved | MET |

Golden/fixture provenance: golden/phone-location.tools.json (T00, tracked spec),
golden/phone-location.behavior.json (T02, canned-fixture characterization),
golden/phone-location.fixture.json (T02, mutable GPS JSON route). All tracked;
no test opens an ignored file (fresh-clone gate green).
