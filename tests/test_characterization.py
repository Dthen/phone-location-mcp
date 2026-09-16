"""phone-location-mcp T06: characterization tests vs the migrated server.

Replays golden/phone-location.behavior.json — the 6 cases T02 froze from the
legacy fastmcp server under $PYO — against the post-migration stdlib server,
byte-exact. IN-PROCESS route (card mandate): the migrated server is stdlib, so
this pytest interpreter imports it directly and drives it exactly the way the
fixture was captured: DATA_FILE repointed via monkeypatch, urllib.request.urlopen
replayed (canned JSON for the hit leg, a raise for the fail leg, a trap for the
no-data legs), and the clock pinned with the same fixed-clock convention T02
recorded (PinnedFakeDatetime over server.datetime, FIXED_NOW stamped in the
golden _meta).

Isolation mirrors the capture harness (tools/characterize_old_server.py):
* Zero network — urlopen is never live: hit/fail legs replay canned fakes;
  no-data legs arm a trap and assert the call count stayed 0.
* Zero live state — server.DATA_FILE is monkeypatched per case to the golden
  fixture or a provably-nonexistent /tmp path; the mutable repo-root
  phone-location.json is never read (sanity-pinned by golden _meta).
* lru_cache trap — server._reverse_geocode.cache_clear() at the start of every
  case: geocode-hit and geocode-fail share the fixture's coord pair in one
  process, so a cached first-leg address would poison the fail case (the same
  trap T02 neutralizes).
* Failure diffs are BYTE diffs — the assert message reports the first differing
  byte offset with both byte values (and their codepoints in multi-byte
  sequences) plus context around it.
"""

import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import server  # the migrated stdlib server, in-process

GOLDEN_PATH = REPO / "golden" / "phone-location.behavior.json"
FIXTURE_PATH = REPO / "golden" / "phone-location.fixture.json"

BEHAVIOR = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
CASES = BEHAVIOR["cases"]
META = BEHAVIOR["_meta"]

# T02's fixed-clock convention, read back from the golden _meta so the test
# cannot drift from the artifact it asserts against.
FIXED_NOW = datetime.fromisoformat(META["pinned_now_utc"])

# Canned geocode display name exercised by both data cases (recorded in
# legacy_notes.structured_content_probe.summary.result).
GEOCODE_DISPLAY_NAME = "High Street, Edinburgh EH1 — Scotland"


class PinnedFakeDatetime(datetime):
    """datetime subclass overriding ONLY now() — identical to T02's harness
    patch site: server.py does a module-level `from datetime import datetime`
    and _age resolves the name at call time, so rebinding server.datetime is
    observed. fromisoformat and everything else keep real behavior."""

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


def _first_diff_bytes(expected: bytes, actual: bytes) -> str:
    """Byte-level diff report: first differing offset (or truncation point),
    both byte values, codepoints of the enclosing multi-byte sequences, context."""

    def decode_at(data: bytes, lo: int, hi: int) -> str:
        frag = data[lo:hi]
        try:
            text = frag.decode("utf-8", errors="backslashreplace")
        except Exception:  # pragma: no cover - backslashreplace never raises
            text = repr(frag)
        return "".join(f"U+{ord(ch):04X}({ch})" for ch in text)

    limit = min(len(expected), len(actual))
    for i in range(limit):
        if expected[i] != actual[i]:
            lo = max(0, i - 12)
            hi = min(len(expected), i + 13)
            hi_a = min(len(actual), i + 13)
            return (
                f"first diff at byte offset {i}: expected 0x{expected[i]:02X}, "
                f"got 0x{actual[i]:02X}\n"
                f"  expected bytes [{lo}:{hi}]: {decode_at(expected, lo, hi)}\n"
                f"  actual   bytes [{lo}:{hi_a}]: {decode_at(actual, lo, hi_a)}\n"
                f"  expected: {expected[lo:hi]!r}\n"
                f"  actual  : {actual[lo:hi_a]!r}"
            )
    if len(expected) != len(actual):
        which = "actual is shorter (truncated)" if len(actual) < len(expected) else "actual is longer"
        return (
            f"common prefix identical through byte {limit - 1}; lengths differ "
            f"(expected {len(expected)} bytes, got {len(actual)}; {which})\n"
            f"  expected tail: {expected[limit:limit + 24]!r}\n"
            f"  actual   tail: {actual[limit:limit + 24]!r}"
        )
    return "byte-identical"  # only reached if an assert fires on equal bytes


def _call_tool(tool):
    """Invoke the migrated tool function in-process (matches the capture route:
    T02 called the raw function that @mcp.tool() left directly callable)."""
    return getattr(server, tool)()


def _assert_case(case_key, tool, kind, tmp_path, monkeypatch):
    """Replay one frozen case byte-exact. case_key is the golden's literal key."""
    expected_text = CASES[case_key]["text"]
    expected_calls = CASES[case_key]["call_count"]

    # lru_cache trap neutralizer — before EVERY case (shared fixture coords).
    server._reverse_geocode.cache_clear()

    # Pin the golden's fixture pointer instead of trusting ambient state.
    # Relocatable by design: _meta.fixture is the ABSOLUTE path T02 recorded at
    # capture time, which cannot equal this checkout's path after a clone to a
    # different location. Pin the repo-relative tail (golden/<fixture name>)
    # identity on both sides instead of the capture-time absolute path.
    meta_fixture = Path(META["fixture"])
    local_tail = FIXTURE_PATH.relative_to(REPO)
    meta_tail = meta_fixture.relative_to(meta_fixture.parents[1]) if len(meta_fixture.parents) > 1 else meta_fixture
    assert meta_tail == local_tail, (
        f"golden _meta fixture {META['fixture']!r} does not tail-match the "
        f"tracked {local_tail} under this checkout — replay would assert "
        f"against the wrong input")

    calls = []

    def ok_urlopen(req, timeout=None):
        calls.append("ok")
        return FakeHTTPResponse(
            json.dumps({"display_name": GEOCODE_DISPLAY_NAME}).encode("utf-8"))

    def fail_urlopen(req, timeout=None):
        calls.append("fail")
        raise ConnectionError("characterization replay: canned connection failure")

    def trap_urlopen(req, timeout=None):
        calls.append("trap")
        raise AssertionError(f"urlopen reached in {case_key} — network isolation violated")

    if kind == "geocode-hit":
        monkeypatch.setattr(server, "DATA_FILE", FIXTURE_PATH)
        monkeypatch.setattr(urllib.request, "urlopen", ok_urlopen)
    elif kind == "geocode-fail":
        monkeypatch.setattr(server, "DATA_FILE", FIXTURE_PATH)
        monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)
    elif kind == "no-data":
        missing = tmp_path / "phone-location-characterization-missing.json"
        assert not missing.exists()
        monkeypatch.setattr(server, "DATA_FILE", missing)
        monkeypatch.setattr(urllib.request, "urlopen", trap_urlopen)
    else:  # pragma: no cover - guard against a typo'd parametrization
        raise AssertionError(f"unknown case kind: {kind}")

    # Same fixed-clock convention T02 recorded (verified binding: _age reads the
    # module global `datetime` at call time).
    monkeypatch.setattr(server, "datetime", PinnedFakeDatetime)

    actual_text = _call_tool(tool)
    assert isinstance(actual_text, str), (
        f"{case_key}: migrated {tool}() returned {type(actual_text).__name__}, "
        f"not str — legacy contract is a string return")

    # Byte-exact comparison (the frozen text is the contract).
    expected_bytes = expected_text.encode("utf-8")
    actual_bytes = actual_text.encode("utf-8")
    assert expected_bytes == actual_bytes, (
        f"{case_key}: tool text drifted from the frozen legacy bytes\n"
        + _first_diff_bytes(expected_bytes, actual_bytes))

    # urlopen call-count leg of the frozen contract (0 on no-data short-circuit).
    assert len(calls) == expected_calls, (
        f"{case_key}: expected urlopen called {expected_calls} time(s), "
        f"got {len(calls)} ({calls})")

    server._reverse_geocode.cache_clear()  # leave no cache state for the next case


# ------------------------------------------------------------------ 6 cases
# Test names mirror the golden case keys: get/geocode-hit, get/geocode-fail,
# get/no-data, summary/geocode-hit, summary/geocode-fail, summary/no-data.


def test_get_geocode_hit(tmp_path, monkeypatch):
    _assert_case("get/geocode-hit", "get", "geocode-hit", tmp_path, monkeypatch)


def test_get_geocode_fail(tmp_path, monkeypatch):
    _assert_case("get/geocode-fail", "get", "geocode-fail", tmp_path, monkeypatch)


def test_get_no_data(tmp_path, monkeypatch):
    _assert_case("get/no-data", "get", "no-data", tmp_path, monkeypatch)


def test_summary_geocode_hit(tmp_path, monkeypatch):
    _assert_case("summary/geocode-hit", "summary", "geocode-hit", tmp_path, monkeypatch)


def test_summary_geocode_fail(tmp_path, monkeypatch):
    _assert_case("summary/geocode-fail", "summary", "geocode-fail", tmp_path, monkeypatch)


def test_summary_no_data(tmp_path, monkeypatch):
    _assert_case("summary/no-data", "summary", "no-data", tmp_path, monkeypatch)


# ------------------------------------------------------------- completeness
# Collection-time guard (not a test — the card pins exactly 6 characterization
# cases): if the golden artifact ever grows or renames a case, importing this
# module fails loudly instead of silently skipping it.

_REPLAYED = {
    "get/geocode-hit", "get/geocode-fail", "get/no-data",
    "summary/geocode-hit", "summary/geocode-fail", "summary/no-data",
}
assert set(CASES) == _REPLAYED, (
    f"golden cases {sorted(CASES)} do not match the cases replayed here "
    f"{sorted(_REPLAYED)}")
