#!/usr/bin/env python3
"""T02 characterization harness — freeze legacy phone-location MCP tool behavior.

Run with the CURRENT production interpreter ONLY (the one that has fastmcp;
the post-rewrite server must NOT be imported by this harness). This harness
captures against the PRE-migration fastmcp venv, which is NOT the interpreter
the post-migration server runs under, so it is configured separately from the
`${PROD_PY}` used by the migrated-era gates:

    export PROD_PY_PHONE_LOCATION_OLD=<venv-root>/phone-location-mcp/bin/python3
    <venv-root>/phone-location-mcp/bin/python3 \
        <repo-root>/tools/characterize_old_server.py

(or write that same path to the gitignored, untracked pointer file
`<repo-root>/.prod_py.old`; see tools/prod_py.py for the shared resolver).
The harness refuses to run unconfigured rather than silently falling back to
`sys.executable` — a fallback would record provenance for an interpreter that
was never used to capture, making the artifact a lie.

The paths this harness records into golden/phone-location.behavior.json are
PORTABLE by construction: repo-relative for in-repo artifacts, and a
`<venv-root>/...` placeholder for the interpreter. The artifact must be
identical no matter where the checkout lives or which venv root is used.

It imports server.py IN-PROCESS and records the exact text both tools return
for 6 cases — get x {data+geocode-hit, data+geocode-fail, no-data} and the same
three for summary — into ../golden/phone-location.behavior.json (next to this
file's repo root). Output artifact = the frozen behavioral contract that T06's
characterization tests replay against the migrated stdlib server.

Isolation guarantees (review checklist mirrors these):
* Zero network: urllib.request.urlopen is patched (canned geocode-success
  response AND a raising connection that pins the blanket
  `except Exception: return ""` swallow at server.py:56-57) before any case
  that can reach _reverse_geocode. no-data cases cannot reach it (data is None
  short-circuits), and we still assert during the run that urlopen was never
  called on those legs (call-count sentinels).
* Zero live state: server.DATA_FILE is repointed at golden/phone-location.fixture.json
  (data cases) or a path under /tmp that provably does not exist (no-data cases).
  The real repo-root phone-location.json is never read.
* lru_cache trap (server.py:42-43 — @lru_cache(maxsize=64) on raw lat/lon args):
  geocode-hit and geocode-fail share the fixture's coord pair in one process, so
  server._reverse_geocode.cache_clear() is called before EVERY case; a cached
  second-leg read would poison the recorded bytes and T06 would replay the trap.
* Time-dependent age text (`_age`, server.py:29-39): deterministic via a
  datetime patch (VERIFIED BINDING at task start — see PinnedFakeDatetime):
  server.py does `from datetime import datetime, timezone` at module level
  (server.py:6) and _age resolves `datetime.now(...)` through the MODULE GLOBAL
  name at call time, so rebinding server.datetime is observed by _age. We set
  server.datetime = PinnedFakeDatetime, a datetime subclass overriding only the
  now() classmethod (so datetime.fromisoformat, the isinstance checks in
  _age's subtraction and every other real-datetime use keep working) whose
  now(timezone.utc) returns FIXED_NOW. Fallback if a future refactor breaks the
  patch site: compute the expected freshness from the fixture received_at plus
  the stamped time.time() delta and assert the captured shape against the
  recorded literal instead (recorded case shape stays identical either way).

R3 groundwork record (verified, not assumed, by the mcp.call_tool probe below):
both tools return str; fastmcp 3.4.7's @mcp.tool() leaves them directly
callable AND wraps string returns as structuredContent={"result": <str>} on the
wire with outputSchema {"properties":{"result":{"type":"string"}},...,
"x-fastmcp-wrap-result": true}. That wrapper is a legacy-shape note only — the
migrated era server emits NO structuredContent (D3 / REFERENCE §4 trap) — so it
is recorded under legacy_notes and is NEVER an assertion target for T06.

REFERENCE §5 encoding (frozen from capture, never inferred here): `get` success
is json.dumps(indent=2), `get` error blob default separators, `summary` a plain
human string. This harness only records whatever bytes the legacy server makes.

Exit status: 0 = all 6 cases recorded; non-zero = any unexpected exception
(FAIL: printed), a missing/failed internal assertion, or an unconfigured
interpreter (resolved at import, see tools/prod_py.py).
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import server  # in-process under $PYO only — the legacy fastmcp server

from prod_py import prod_py

# The interpreter this capture is CONTRACTUALLY run under (pre-migration
# fastmcp venv). Resolved at import — deliberately raising rather than skipping
# when unconfigured, so a misconfigured capture can never masquerade as a real
# one. Provenance is recorded as a <venv-root>/... placeholder, never the
# machine's real interpreter path, so the artifact is checkout-independent.
INTERP = prod_py()
INTERP_PROVENANCE = f"<venv-root>/{Path(INTERP).parent.parent.name}/bin/{Path(INTERP).name}"

# ENFORCE the contract above rather than assuming it. This harness imports
# server.py IN-PROCESS, so the interpreter that actually captures the behavior
# is sys.executable — INTERP is only ever a label. Without this check the
# artifact could stamp _meta.interpreter_path from the CONFIGURED interpreter
# while _meta.interpreter records the version of a completely different one
# that really ran: provenance that silently lies, and the exact failure the
# resolver exists to prevent. Both paths are resolved() first because a venv's
# bin/python3 is a symlink to bin/python3.11.
if Path(INTERP).resolve() != Path(sys.executable).resolve():
    raise RuntimeError(
        "Interpreter mismatch: this capture must RUN under the configured "
        f"pre-migration interpreter ({INTERP_PROVENANCE}), but it is running "
        f"under Python {sys.version.split()[0]} at a different path. The "
        "_meta provenance this harness records would describe an interpreter "
        "that did not produce the capture. Re-run it with the configured "
        "interpreter, or point the configuration at the one you are using."
    )

FIXTURE = REPO / "golden" / "phone-location.fixture.json"
OUT = REPO / "golden" / "phone-location.behavior.json"

# Repo-relative spellings recorded in the artifact's _meta. Both are asserted
# back by tests/test_characterization.py: the fixture tail-matches the tracked
# golden/phone-location.fixture.json, so an absolute capture-time path would
# both leak a host path and fail that tail-match on any other checkout.
SERVER_PROVENANCE = "server.py"
FIXTURE_PROVENANCE = "golden/phone-location.fixture.json"

# Fixed "now" = fixture received_at + 3h -> freshness "at this location for 3 hours".
FIXED_NOW = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)

# Geocode response canned under a non-ASCII name so the ensure_ascii default in
# server.py's json.loads(resp.read()) round-trip is exercised (display_name is
# a plain str in the returned dict — json.dumps of the OUTER get() blob at
# indent=2 keeps ensure_ascii=True default, pinning the \uXXXX escaping).
GEOCODE_DISPLAY_NAME = "High Street, Edinburgh EH1 — Scotland"

# no-data path: a /tmp path with a unique suffix that is removed after (re)write.
NONEXISTENT = Path("/tmp") / f"phone-location-t02-missing-{os.getpid()}.json"


class PinnedFakeDatetime(datetime):
    """datetime subclass overriding ONLY now(). fromisoformat and all other
    classmethods keep real behavior; _age's subtraction/isinstance paths see a
    real tz-aware datetime. Binds server._age via the module global `datetime`
    (server.py:6 module-level from-import; _age looks the name up at call time —
    verified binding at task start with a direct probe: 3h offset pinned,
    _age -> (10800, 'at this location for 3 hours'))."""

    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW if tz is not None else FIXED_NOW.replace(tzinfo=None)


class FakeHTTPResponse:
    """Minimal urlopen() context-manager result returning canned bytes."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


# --- urlopen instrumentation -------------------------------------------------
_real_urlopen = urllib.request.urlopen
_urlopen_calls = []  # zero-arg sentinels recorded per case; cleared each case


def _ok_urlopen(req, timeout=None):
    _urlopen_calls.append("ok")
    return FakeHTTPResponse(
        json.dumps({"display_name": GEOCODE_DISPLAY_NAME}).encode("utf-8")
    )


def _fail_urlopen(req, timeout=None):
    _urlopen_calls.append("fail")
    raise ConnectionError("characterization harness: canned connection failure")


def _trap_urlopen(req, timeout=None):
    _urlopen_calls.append("trap")
    raise AssertionError("urlopen reached outside a patched case — network isolation violated")


# --- case runner ---------------------------------------------------------------
def run_case(tool, kind):
    """kind in {geocode_hit, geocode_fail, no_data}. Returns (text, note)."""
    server._reverse_geocode.cache_clear()  # lru_cache trap — before EVERY case
    _urlopen_calls.clear()  # per-case call-count sentinel baseline

    if kind == "no_data":
        NONEXISTENT.unlink(missing_ok=True)  # ensure it does not exist
        server.DATA_FILE = NONEXISTENT
        urllib.request.urlopen = _trap_urlopen  # must not be reached; count asserted
    elif kind == "geocode_hit":
        server.DATA_FILE = FIXTURE
        urllib.request.urlopen = _ok_urlopen
    elif kind == "geocode_fail":
        server.DATA_FILE = FIXTURE
        urllib.request.urlopen = _fail_urlopen  # pins except Exception -> "" (server.py:56-57)
    else:
        raise AssertionError(f"unknown case kind: {kind}")

    fn = getattr(server, tool)
    text = fn()  # @mcp.tool() returns the original function — VERIFIED callable in-process
    if not isinstance(text, str):
        raise AssertionError(f"{tool} returned {type(text).__name__}, not str")

    if kind == "no_data" and _urlopen_calls:
        raise AssertionError(f"no-data case hit urlopen ({_urlopen_calls}) — short-circuit broken")
    return text


def main():
    server.datetime = PinnedFakeDatetime  # deterministic _age (see module docstring)

    fixture_data = json.loads(FIXTURE.read_text())
    age_at_fixed = round((FIXED_NOW - datetime.fromisoformat(fixture_data["received_at"])).total_seconds())

    cases = {}
    order = [
        ("get", "geocode_hit"),
        ("get", "geocode_fail"),
        ("get", "no_data"),
        ("summary", "geocode_hit"),
        ("summary", "geocode_fail"),
        ("summary", "no_data"),
    ]
    for tool, kind in order:
        text = run_case(tool, kind)
        # hyphen case keys — the card's canned verification asserts d['cases']['get/no-data']
        cases[f"{tool}/{kind.replace('_', '-')}"] = {"text": text, "call_count": len(_urlopen_calls)}

    NONEXISTENT.unlink(missing_ok=True)
    urllib.request.urlopen = _real_urlopen  # restore before the async probe
    server.DATA_FILE = REPO / "phone-location.json"  # restore module state (process ends anyway)

    # --- legacy wire probe: re-pin structuredContent + outputSchema presence ---
    # in-process via the fastmcp call path (no wire, no network; DATA_FILE set to
    # fixture so get() runs the data leg; geocode leg patched again to canned ok).
    import asyncio

    server.DATA_FILE = FIXTURE
    server._reverse_geocode.cache_clear()
    urllib.request.urlopen = _ok_urlopen
    res = asyncio.run(server.mcp.call_tool("summary", {}))
    structured_summary = res.structured_content
    tools = asyncio.run(server.mcp.list_tools())
    by_name = {t.name: t for t in tools}
    out_schema = {n: (getattr(t, "outputSchema", None) or getattr(t, "output_schema", None))
                  for n, t in by_name.items()}
    urllib.request.urlopen = _real_urlopen
    server.DATA_FILE = REPO / "phone-location.json"

    legacy_notes = {
        "structuredContent_wrapper": (
            "fastmcp 3.4.7 wraps str tool returns as structuredContent={'result': <str>} "
            "(re-pinned in-process against the tag interpreter via mcp.call_tool). "
            "Recorded ONLY as a legacy wire-shape note — the migrated era server emits NO "
            "structuredContent and NO outputSchema (D3 / REFERENCE §4 trap); NEVER an "
            "assertion target."
        ),
        "structured_content_probe": {
            "summary": structured_summary if isinstance(structured_summary, dict) else None,
        },
        "output_schema_probe": {k: v for k, v in out_schema.items()},
        "string_returns_verified": all(
            c["text"] and isinstance(c["text"], str) for c in cases.values()
        ),
        "reference": (
            "_chain.md 'Legacy wire shape' + 'R3' rows; probe 2026-09-14 VERIFIED "
            "tools/call result keys _meta,content,isError,structuredContent with "
            "structuredContent={'result':<str>}"
        ),
    }

    artifact = {
        "_meta": {
            "generated_by": "tools/characterize_old_server.py (T02)",
            "server": SERVER_PROVENANCE,
            "tag": "pre-migration/20260914",
            "interpreter": sys.version.split()[0],
            "interpreter_path": INTERP_PROVENANCE,
            "fixture": FIXTURE_PROVENANCE,
            "pinned_now_utc": FIXED_NOW.isoformat(),
            "fixture_received_at": fixture_data["received_at"],
            "age_seconds_at_pinned_now": age_at_fixed,
            "now_offset_note": (
                f"freshness text is deterministic under the pinned clock: "
                f"pinned now ({FIXED_NOW.isoformat()}) - fixture received_at "
                f"({fixture_data['received_at']}) = {age_at_fixed}s = "
                f"'at this location for 3 hours'"
            ),
            "isolation": (
                "urlopen patched per case (ok/fail/trap with call-count assert on no-data); "
                "_reverse_geocode.cache_clear() before every case; DATA_FILE -> fixture or "
                "nonexistent /tmp path; live phone-location.json never read; datetime patched "
                "via PinnedFakeDatetime subclass"
            ),
        },
        "legacy_notes": legacy_notes,
        "cases": cases,
    }

    OUT.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUT} ({len(cases)} cases)")
    for k, v in cases.items():
        print(f"  {k}: {len(v['text'])} chars, urlopen calls: {v['call_count']}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
