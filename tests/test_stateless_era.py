"""phone-location-mcp T03: stateless 2026-07-28 era test suite.

Written RED against the legacy server on purpose (TDD discipline —
00-plan-context: "watch the test fail"). The suite is the spec for the
post-migration stdlib server (T04/T05); the repo pytest gate goes green
only at T06.

Spawn interpreter: BIN_PY = /usr/bin/python3 — the TARGET production line
(the cutover D.1 config flip; PLAN F4-style pin). Against the CURRENT
legacy server this spawn is import-death (fastmcp is absent there, so the
server never answers a byte); the substantive era violations it also pins
(`initialize` answered instead of -32601, no `server/discover` era result)
are live-verified legacy facts in _chain.md's "Legacy wire shape" row.

Every read from a spawned server goes through the fleet deadline helper
read_line_with_timeout (pytest-timeout is NOT installed in the pytest
interpreter, so the deadline lives IN the helper; a timeout reads as a
test failure, never a wedge). No test reads the real phone-location.json
and nothing touches the network: the tools/call legs use an unknown tool
or the no-data canned path via PHONE_LOCATION_DATA_FILE (which short-
circuits before _reverse_geocode, so zero urlopen calls by construction).
"""

import json
import os
import select
import subprocess
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server.py"
GOLDEN_PATH = (
    Path(__file__).resolve().parent.parent
    / "golden"
    / "phone-location.tools.json"
)

# TARGET production interpreter (post-rewrite spawn line, D.1 cutover).
# If D.1 re-pins, this is the one-line grep: BIN_PY.
BIN_PY = "/usr/bin/python3"

ERA_VERSION = "2026-07-28"
READ_TIMEOUT = 10.0  # seconds; generous — a dead/legacy server hits it fast


# ------------------------------------------------------------------ helpers


def read_line_with_timeout(f, sec):
    """Fleet deadline helper: one line from f within sec, else None.

    Built on select.select — a bare readline() is forbidden here because
    the legacy server under BIN_PY never answers a byte and would wedge
    the whole run.
    """
    ready, _, _ = select.select([f], [], [], sec)
    if not ready:
        return None
    return f.readline()


def _close_pipe(pipe):
    """Close one subprocess pipe without masking the test outcome."""
    if pipe is None:
        return
    try:
        pipe.close()
    except OSError:
        pass


def cleanup_process(proc):
    """Signal EOF, reap the child, and close every captured pipe."""
    try:
        _close_pipe(proc.stdin)
        if proc.poll() is None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    finally:
        try:
            _close_pipe(proc.stdout)
        finally:
            _close_pipe(proc.stderr)


class EraServer:
    """Spawned server over stdio + line-oriented JSON-RPC with deadlines.

    `self.silent` mirrors what the last rpc() saw: True when the server
    answered nothing within the deadline (import-death / legacy silence).
    """

    def __init__(self, env=None):
        spawn_env = dict(os.environ)
        # Canned data-file seam: point at a path that never exists so the
        # tools take the deterministic no-data branch (zero network).
        spawn_env["PHONE_LOCATION_DATA_FILE"] = "/tmp/phone-location-t03-missing.json"
        if env:
            spawn_env.update(env)
        self.proc = subprocess.Popen(
            [BIN_PY, str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=spawn_env,
            text=True,
        )
        self.silent = False
        self._next_id = 0

    def send_line(self, line):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def send(self, msg):
        self.send_line(json.dumps(msg))

    def next_id(self):
        self._next_id += 1
        return self._next_id

    def read(self, sec=READ_TIMEOUT):
        line = read_line_with_timeout(self.proc.stdout, sec)
        self.silent = line is None or not line.strip()
        return None if self.silent else json.loads(line)

    def rpc(self, method, params=None, msg_id=None):
        """One request with an id -> exactly one response (or None on timeout)."""
        mid = self.next_id() if msg_id is None else msg_id
        msg = {"jsonrpc": "2.0", "id": mid, "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)
        return self.read()

    def alive(self):
        return self.proc.poll() is None

    def close_stdin(self):
        if self.proc.stdin is not None:
            self.proc.stdin.close()

    def cleanup(self):
        cleanup_process(self.proc)


def _spawn(env=None):
    """Yield-style fixture helper (no pytest fixture imports needed)."""
    return EraServer(env=env)


# ------------------------------------------------------------------- 1. + 2.


def test_discover_era_shape():
    """server/discover answers the pinned era shape (REFERENCE §2)."""
    srv = _spawn()
    try:
        resp = srv.rpc("server/discover", {"clientInfo": {"name": "t03"}})
        assert resp is not None, "server/discover answered nothing (import-death or silence)"
        result = resp["result"]
        assert result["supportedVersions"] == [ERA_VERSION]
        assert result["capabilities"] == {"tools": {}}
        assert result["resultType"] == "complete"
        assert result["ttlMs"] == 0
        assert result["cacheScope"] == "private"
        # T07: the spec-recommended serverInfo stamp (REFERENCE §2 verified
        # wire shape). Version 1.1.0 is introduced here — the repo never had
        # a version string pre-migration (fastmcp self-reported 3.4.7).
        assert result["_meta"]["io.modelcontextprotocol/serverInfo"] == {
            "name": "phone-location", "version": "1.1.0"}
    finally:
        srv.cleanup()


def test_discover_paramless():
    """The request may arrive with no params at all; must answer either way (§2)."""
    srv = _spawn()
    try:
        resp = srv.rpc("server/discover")  # no params key at all
        assert resp is not None, "paramless server/discover answered nothing"
        assert resp["result"]["supportedVersions"] == [ERA_VERSION]
    finally:
        srv.cleanup()


# ---------------------------------------------------------------------- 3.


def test_initialize_rejected_32601_same_pipe_discover():
    """initialize -> -32601 (never hang/close), then discover on the SAME pipes
    still works and the server stays alive (REFERENCE §3 hard rules)."""
    srv = _spawn()
    try:
        resp = srv.rpc("initialize", {"protocolVersion": ERA_VERSION,
                                      "capabilities": {},
                                      "clientInfo": {"name": "t03"}})
        assert resp is not None, "initialize answered nothing (legacy silence under BIN_PY)"
        assert "error" in resp and resp["error"]["code"] == -32601, (
            "initialize must be rejected with -32601, never answered")
        # same-pipe follow-up: the era entry point still works afterwards
        disc = srv.rpc("server/discover")
        assert disc is not None, "server died after rejecting initialize"
        assert disc["result"]["supportedVersions"] == [ERA_VERSION]
        assert srv.alive(), "server must not exit on a rejected method"
    finally:
        srv.cleanup()


# ------------------------------------------------------------------ 4. + 5.


def test_tools_list_era_triple():
    """tools/list result carries the era triple: resultType complete /
    ttlMs 0 / cacheScope private, and the 2-tool surface (REFERENCE §4)."""
    srv = _spawn()
    try:
        resp = srv.rpc("tools/list")
        assert resp is not None, "tools/list answered nothing"
        result = resp["result"]
        assert result["resultType"] == "complete"
        assert result["ttlMs"] == 0
        assert result["cacheScope"] == "private"
        assert len(result["tools"]) == 2
    finally:
        srv.cleanup()


def test_tools_list_has_no_output_schema():
    """THE TRAP (D3, REFERENCE §4): the migrated listing declares NO
    outputSchema on any tool (legacy fastmcp goldens HAVE one — the era
    server must not, or validate_tool_result raises on every call)."""
    srv = _spawn()
    try:
        resp = srv.rpc("tools/list")
        assert resp is not None, "tools/list answered nothing"
        tools = resp["result"]["tools"]
        assert all("outputSchema" not in t for t in tools), (
            "migrated listing must not carry outputSchema")
    finally:
        srv.cleanup()


def test_tools_list_golden_byte_identity():
    """UNCONDITIONAL golden freeze test (00-plan-context recipe 3):
    name+description+inputSchema byte-identical per tool against the
    committed golden (absolute __file__-derived path, no skipif);
    outputSchema stripped from the golden before diffing (old fastmcp
    goldens HAVE it — the new server must NOT), and the golden's
    inputSchema is the VERIFIED paramless shape for both tools."""
    golden = json.loads(GOLDEN_PATH.read_text())  # absolute __file__-derived path
    # strip outputSchema (and any other legacy-only keys) before diffing:
    # the era contract is name + description + inputSchema only.
    expected = {
        t["name"]: (t["name"], t["description"], t["inputSchema"])
        for t in golden
    }
    assert set(expected) == {"get", "summary"}
    for _, _, input_schema in expected.values():
        assert input_schema == {
            "additionalProperties": False,
            "properties": {},
            "type": "object",
        }

    srv = _spawn()
    try:
        resp = srv.rpc("tools/list")
        assert resp is not None, "tools/list answered nothing"
        listed = {
            t["name"]: (t["name"], t["description"], t["inputSchema"])
            for t in resp["result"]["tools"]
        }
        assert listed == expected, "tools surface drifted from the frozen golden"
        assert all("outputSchema" not in t for t in resp["result"]["tools"])
    finally:
        srv.cleanup()


# ----------------------------------------------------------------- 6. and 7.


def test_tools_call_era_triple_and_text_passthrough():
    """tools/call result carries the triple; the string return passes
    through VERBATIM in content[0].text (R3 sanctioned string-passthrough
    DEVIATION; no structuredContent, no outputSchema). No-data canned
    path via env seam -> deterministic, zero network."""
    srv = _spawn()
    try:
        resp = srv.rpc("tools/call", {"name": "summary", "arguments": {}})
        assert resp is not None, "tools/call answered nothing"
        assert "result" in resp, f"expected a result, got {resp}"
        result = resp["result"]
        assert result["resultType"] == "complete"
        assert result["ttlMs"] == 0
        assert result["cacheScope"] == "private"
        assert "structuredContent" not in result
        assert result.get("isError") is not True
        texts = [c for c in result["content"] if c.get("type") == "text"]
        assert texts, "expected text content"
        assert "No location data" in texts[0]["text"], (
            "canned no-data text missing -> data seam not honored")
    finally:
        srv.cleanup()


def test_tools_call_unknown_tool_is_error_result():
    """An unknown tool name is a RESULT with isError:true carrying the text
    error (REFERENCE §5) — never a JSON-RPC error, never a crash."""
    srv = _spawn()
    try:
        resp = srv.rpc("tools/call", {"name": "no-such-tool", "arguments": {}})
        assert resp is not None, "tools/call answered nothing"
        assert "result" in resp, "tool-level errors are results, not JSON-RPC errors"
        result = resp["result"]
        assert result["resultType"] == "complete"
        assert result["ttlMs"] == 0
        assert result["cacheScope"] == "private"
        assert result["isError"] is True
        joined = "".join(c.get("text", "") for c in result["content"])
        assert "Unknown tool" in joined
    finally:
        srv.cleanup()


# -------------------------------------------------------------- 8., 9., 10..


def test_ping_answers_empty_object():
    """ping (a request with id) -> result {} (REFERENCE §6 copy-the-{} form)."""
    srv = _spawn()
    try:
        resp = srv.rpc("ping")
        assert resp is not None, "ping answered nothing"
        assert resp["result"] == {}
        assert srv.alive()
    finally:
        srv.cleanup()


def test_unknown_method_32601():
    """Any other method with an id (e.g. resources/list) -> -32601 (§3, §6)."""
    srv = _spawn()
    try:
        resp = srv.rpc("resources/list")
        assert resp is not None, "resources/list answered nothing"
        assert resp["error"]["code"] == -32601
        assert srv.alive()
    finally:
        srv.cleanup()


def test_notifications_initialized_swallowed():
    """A notification (no id) is consumed silently — NEVER answered, even
    for a legacy-era method name (§1 rule: a spurious response corrupts
    client correlation). Then a real request on the same pipe still works."""
    srv = _spawn()
    try:
        srv.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        # proof of silence: the next response must carry the NEXT id (2),
        # so an id-less phantom answer would break correlation.
        resp = srv.rpc("ping", msg_id=2)
        assert resp is not None, "server answered nothing to the follow-up request"
        assert resp["id"] == 2, (
            "id mismatch: the server emitted a phantom response to the notification")
        assert resp["result"] == {}
    finally:
        srv.cleanup()


def test_id_less_known_requests_are_swallowed():
    """Known methods are still notifications when they have no id.

    The framing rule applies to every JSON-RPC notification, not only to the
    ``notifications/*`` namespace. A response for any of these messages would
    carry id=null and corrupt the next request's correlation.
    """
    srv = _spawn()
    try:
        for method, params in (
            ("server/discover", {}),
            ("tools/list", {}),
            ("tools/call", {"name": "summary", "arguments": {}}),
            ("ping", {}),
        ):
            srv.send({"jsonrpc": "2.0", "method": method, "params": params})
        resp = srv.rpc("ping", msg_id=9)
        assert resp is not None, "server answered nothing after id-less known requests"
        assert resp["id"] == 9, (
            f"id-less known request produced a phantom response: got {resp!r}"
        )
        assert srv.alive()
    finally:
        srv.cleanup()


def test_explicit_null_id_is_answered_for_known_methods():
    """A JSON-RPC request that explicitly includes id:null still receives
    a response; the absent-id notification guard must not collapse null."""
    srv = _spawn()
    try:
        for mid, method, params in (
            (None, "server/discover", None),
            (None, "tools/list", None),
            (None, "tools/call", {"name": "summary", "arguments": {}}),
            (None, "ping", None),
        ):
            msg = {"jsonrpc": "2.0", "id": mid, "method": method}
            if params is not None:
                msg["params"] = params
            srv.send(msg)
            resp = srv.read()
            assert resp is not None, f"{method}: explicit null id was not answered"
            assert resp["id"] is None
            assert "result" in resp
        assert srv.alive()
    finally:
        srv.cleanup()


# -------------------------------------------------------------------- 12..


def test_eof_clean_exit_zero():
    """Close stdin -> loop ends -> interpreter exits rc 0 within 5 s (§7)."""
    srv = _spawn()
    try:
        srv.close_stdin()
        rc = srv.proc.wait(timeout=5)
        assert rc == 0, f"EOF exit must be clean rc=0, got {rc}"
    finally:
        srv.cleanup()


# ============================================================ T08 regressions
# REFERENCE §7 test-suite recipe: five canonical regressions + the non-JSON
# garbage-lines test = six. Each was an A.1 code-quality killer (guards in
# §1/§3/§5; the §1 reconfigure guard ships since T04 — server.py:11 — and
# test_binary_garbage_line_does_not_kill_the_server pins it, making E.1's
# retro-fit a NO-OP here). Cite REFERENCE, not other repos' commits.


def test_garbage_lines_do_not_kill_the_server():
    """Non-JSON lines are skipped, never fatal (§1 except-continue, §7)."""
    srv = _spawn()
    try:
        garbage = ["not json at all", "{{{{", "{bad: json,]", "[]]]",
                   "hello world 42"]
        for g in garbage:
            srv.send_line(g)
        resp = srv.rpc("server/discover")
        assert resp is not None, (
            f"server answered nothing after garbage stream {garbage!r}")
        assert resp["result"]["supportedVersions"] == [ERA_VERSION]
        assert srv.alive(), "server must survive non-JSON garbage lines"
    finally:
        srv.cleanup()


def test_non_dict_json_lines_do_not_kill_the_server():
    """Valid JSON that isn't an object (5, null, [1,2]) is skipped like
    garbage (§1 isinstance guard) — no response, no crash."""
    srv = _spawn()
    try:
        non_dict = ["5", "null", "[1,2]"]
        for raw in non_dict:
            srv.send_line(raw)
        resp = srv.rpc("tools/list")
        assert resp is not None, (
            f"server answered nothing after non-dict JSON lines {non_dict!r}")
        assert resp["id"] == 1, (
            "a phantom response to a non-dict line broke correlation "
            f"(got id {resp.get('id')!r} for request id 1)")
        assert "result" in resp, f"expected a result, got {resp}"
        assert srv.alive(), "server must survive non-dict JSON lines"
    finally:
        srv.cleanup()


def test_non_string_method_routes_as_unknown_method():
    """A non-string method (null, 42) must route as unknown-method -32601
    rather than crash on .startswith (§1 rule; A.1 code-quality killer)."""
    srv = _spawn()
    try:
        for bad_method in (None, 42):
            srv.send({"jsonrpc": "2.0", "id": 7, "method": bad_method})
            line = srv.read()
            assert line is not None, (
                f"method={bad_method!r} produced silence (crash-loop "
                "or unanswered request)")
            assert "error" in line, (
                f"non-string method={bad_method!r}: expected an error "
                f"response, got {line}")
            assert line["error"]["code"] == -32601, (
                f"non-string method={bad_method!r} must route as "
                f"unknown-method -32601, got {line}")
            assert line["id"] == 7, (
                f"non-string method={bad_method!r}: response id must echo "
                f"request id 7, got {line}")
        assert srv.alive(), "server must survive non-string method values"
    finally:
        srv.cleanup()


def test_id_less_unknown_request_answered_by_nothing():
    """An unknown method with NO id is a notification: it must be answered
    by NOTHING — a spurious "id": null error corrupts client correlation
    (§1 rule, §3 rid-is-None guard). Proof by correlation: the next real
    request's response carries its own id."""
    srv = _spawn()
    try:
        srv.send({"jsonrpc": "2.0", "method": "no/such/notification"})
        resp = srv.rpc("ping", msg_id=2)
        assert resp is not None, "server answered nothing to the follow-up request"
        assert resp["id"] == 2, (
            "id mismatch: the server emitted a phantom response to the "
            f"id-less unknown request (got id {resp.get('id')!r}, expected 2)")
        assert resp["result"] == {}
        assert srv.alive()
    finally:
        srv.cleanup()


def test_tools_call_missing_params_gets_minus_32602():
    """tools/call with params absent / non-dict, or with a non-string /
    missing name, must get JSON-RPC -32602 (REFERENCE §5 check-upfront) —
    never -32603, never a crash. Owner test for the -32602 mandate T05
    imposes; the 12 era tests do not cover it."""
    srv = _spawn()
    try:
        srv.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})   # no params key
        cases = [
            ("absent params", None),
            ("params non-dict (list)", [1, 2]),
            ("params non-dict (str)", "nope"),
            ("params without name", {"arguments": {}}),
            ("params name non-string", {"name": 42}),
        ]
        mid = 1
        for label, params in cases:
            if params is not None:
                mid += 1
                srv.send({"jsonrpc": "2.0", "id": mid, "method": "tools/call",
                          "params": params})
            line = srv.read()
            assert line is not None, f"{label}: tools/call answered nothing"
            assert "error" in line, (
                f"{label}: expected a JSON-RPC error, got {line}")
            assert line["error"]["code"] == -32602, (
                f"{label}: must be -32602 (check-upfront, REFERENCE §5), "
                f"never -32603, got {line}")
            assert line["id"] == mid, (
                f"{label}: response id must echo request id {mid}, got {line}")
        assert srv.alive(), "server must survive missing/non-dict params"
    finally:
        srv.cleanup()


def test_tools_call_non_object_arguments_get_minus_32602():
    """Each frozen no-argument tool rejects non-object arguments at the
    protocol boundary instead of executing its handler."""
    srv = _spawn()
    try:
        mid = 0
        for tool in ("get", "summary"):
            for arguments in ([], "bad", 42, None):
                mid += 1
                srv.send({
                    "jsonrpc": "2.0",
                    "id": mid,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments},
                })
                resp = srv.read()
                assert resp is not None
                assert resp.get("error", {}).get("code") == -32602, resp
                assert resp["id"] == mid
        assert srv.alive()
    finally:
        srv.cleanup()


def test_tools_call_unknown_properties_are_tool_errors():
    """additionalProperties=false means extra keys are a normal tool-level
    error result, never a successful execution or a JSON-RPC error."""
    srv = _spawn()
    try:
        mid = 0
        for tool in ("get", "summary"):
            mid += 1
            srv.send({
                "jsonrpc": "2.0",
                "id": mid,
                "method": "tools/call",
                "params": {"name": tool, "arguments": {"unexpected": True}},
            })
            resp = srv.read()
            assert resp is not None
            assert "result" in resp, resp
            assert resp["id"] == mid
            assert resp["result"]["isError"] is True
            text = resp["result"]["content"][0]["text"]
            assert "unexpected" in text
        assert srv.alive()
    finally:
        srv.cleanup()


def test_get_unknown_json_shape_is_normalized_to_tool_error(tmp_path):
    """An invalid JSON document is malformed data, not the documented
    not-yet-reported no-data success text."""
    location_file = tmp_path / "invalid.json"
    location_file.write_text("not json", encoding="utf-8")
    srv = _spawn({"PHONE_LOCATION_DATA_FILE": str(location_file)})
    try:
        resp = srv.rpc("tools/call", {"name": "get", "arguments": {}})
        assert resp is not None
        assert "result" in resp, resp
        assert resp["result"]["isError"] is True
        text = resp["result"]["content"][0]["text"]
        assert "malformed" in text.lower()
        assert "no location data" not in text.lower()
    finally:
        srv.cleanup()


def test_malformed_location_data_is_normalized_to_tool_error(tmp_path):
    """Malformed JSON data and expected location/timestamp failures stay in
    the tool layer: result + isError, no -32603 and no exception text."""
    cases = {
        "empty object": {},
        "json list": [55.0, -3.0],
        "missing received_at": {
            "lat": 55.0, "lon": -3.0, "accuracy_m": 5,
            "speed_kmh": 0, "bearing": 0, "altitude_m": 0,
            "provider": "test", "timestamp_utc": "2026-09-24T12:00:00+00:00",
        },
        "invalid received_at": {
            "lat": 55.0, "lon": -3.0, "accuracy_m": 5,
            "speed_kmh": 0, "bearing": 0, "altitude_m": 0,
            "provider": "test", "timestamp_utc": "2026-09-24T12:00:00+00:00",
            "received_at": "not-a-timestamp",
        },
        "missing coordinate": {
            "lon": -3.0, "accuracy_m": 5, "speed_kmh": 0,
            "bearing": 0, "altitude_m": 0,
            "provider": "test", "timestamp_utc": "2026-09-24T12:00:00+00:00",
            "received_at": "2026-09-24T12:00:00+00:00",
        },
        "timezone-free received_at": {
            "lat": 55.0, "lon": -3.0, "accuracy_m": 5, "speed_kmh": 0,
            "bearing": 0, "altitude_m": 0,
            "provider": "test", "timestamp_utc": "2026-09-24T12:00:00+00:00",
            "received_at": "2026-09-24T12:00:00",
        },
        "non-numeric accuracy": {
            "lat": 55.0, "lon": -3.0, "accuracy_m": "unknown",
            "speed_kmh": 0, "bearing": 0, "altitude_m": 0,
            "provider": "test", "timestamp_utc": "2026-09-24T12:00:00+00:00",
            "received_at": "2026-09-24T12:00:00+00:00",
        },
    }
    mid = 0
    for label, data in cases.items():
        location_file = tmp_path / f"{label.replace(' ', '_')}.json"
        location_file.write_text(json.dumps(data), encoding="utf-8")
        for tool in ("get", "summary"):
            mid += 1
            clean = _spawn({"PHONE_LOCATION_DATA_FILE": str(location_file)})
            try:
                clean.send({
                    "jsonrpc": "2.0", "id": mid, "method": "tools/call",
                    "params": {"name": tool, "arguments": {}},
                })
                resp = clean.read()
                assert resp is not None, f"{label}/{tool}: no response"
                assert "result" in resp, f"{label}/{tool}: {resp}"
                assert resp["id"] == mid
                assert resp["result"]["isError"] is True
                text = resp["result"]["content"][0]["text"]
                assert "location data" in text.lower()
                assert "Traceback" not in text
                assert "KeyError" not in text
                assert "not-a-timestamp" not in text
            finally:
                clean.cleanup()


def test_binary_garbage_line_does_not_kill_the_server():
    """REFERENCE §7 binary-garbage skeleton, copied verbatim (G→D→G→L
    stream, id-correlation asserts, rc=0 on EOF). Bytes-mode Popen on the
    $BIN production interpreter; the two readline()s are wrapped in the
    fleet deadline helper read_line_with_timeout — a timeout guard, not a
    skeleton deviation (crtsh T05:79 precedent). Pins the §1 reconfigure
    guard (server.py:11) present since T04."""
    p = subprocess.Popen([BIN_PY, str(SERVER)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        discover_line_json = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "server/discover"})
        tools_list_line_json = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        p.stdin.write(b"\xff\xfe\x00garbage\n")                      # G — pre-stream invalid UTF-8
        p.stdin.write(discover_line_json.encode() + b"\n"); p.stdin.flush()   # D
        raw1 = read_line_with_timeout(p.stdout, READ_TIMEOUT)
        assert raw1 is not None, "pre-stream invalid UTF-8 killed the server (no response to id 1)"
        resp = json.loads(raw1)
        assert resp["id"] == 1 and resp["result"]["supportedVersions"] == ["2026-07-28"]
        p.stdin.write(b"\x00\xff\n")                                 # G — mid-stream garbage
        p.stdin.write(tools_list_line_json.encode() + b"\n"); p.stdin.flush()  # L
        raw2 = read_line_with_timeout(p.stdout, READ_TIMEOUT)
        assert raw2 is not None, "mid-stream invalid UTF-8 killed the server (no response to id 2)"
        resp2 = json.loads(raw2)                                     # id==2 proves no phantom response to G
        assert resp2["id"] == 2 and "result" in resp2
        assert p.poll() is None
        p.stdin.close(); assert p.wait(timeout=5) == 0               # clean EOF exit
    finally:
        cleanup_process(p)
