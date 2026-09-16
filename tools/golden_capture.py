#!/usr/bin/env python3
"""Golden tools/list capture for phone-location-mcp (D4: capture BEFORE any edit).

Spawns the CURRENT (legacy, fastmcp 3.4.7) server exactly as the real
~/.hermes/config.yaml `phone-location` entry does:

    /mnt/HC_Volume_105667182/kimbo/mcp-venvs/phone-location-mcp/bin/python3 <repo>/server.py

sends `initialize` (the legacy server answers it), then
`notifications/initialized`, then `tools/list`, and writes the FULL `tools`
array verbatim (no key filtering) to <repo>/golden/phone-location.tools.json.

Paths derive from __file__ (repo root = parent of this tools/ dir) so the
script works from any checkout location. All logging goes to stderr; the only
stdout line is the final success line. No network: pure stdio subprocess.
"""

import json
import os
import select
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, "server.py")
GOLDEN_DIR = os.path.join(REPO, "golden")
OUT_PATH = os.path.join(GOLDEN_DIR, "phone-location.tools.json")

# The CURRENT production interpreter (Python 3.11.15 + fastmcp 3.4.7) — the
# command half of the config line quoted above. Capture runs against the
# pre-migration server only; post-rewrite tests use /usr/bin/python3.
INTERP = "/mnt/HC_Volume_105667182/kimbo/mcp-venvs/phone-location-mcp/bin/python3"

TIMEOUT_S = 20.0


def log(msg):
    print(msg, file=sys.stderr, flush=True)


class CaptureError(Exception):
    pass


def send(proc, obj):
    line = json.dumps(obj) + "\n"
    try:
        proc.stdin.write(line)
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as exc:
        raise CaptureError(f"server died before accepting {obj.get('method')!r}: {exc}")


def recv(proc, deadline, want_id):
    """Read newline-delimited JSON-RPC until a response with id == want_id."""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CaptureError(f"timeout waiting for response id={want_id}")
        ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 1.0))
        if not ready:
            if proc.poll() is not None:
                raise CaptureError(
                    f"server exited rc={proc.returncode} before responding to id={want_id}"
                )
            continue
        line = proc.stdout.readline()
        if not line:
            raise CaptureError(f"server closed stdout before responding to id={want_id}")
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            log(f"skip non-JSON server output: {line[:200]}")
            continue
        if msg.get("id") == want_id:
            return msg
        log(f"(other message, ignored while waiting for id={want_id}: "
            f"{msg.get('method', msg.get('id'))})")


def main():
    if not os.path.exists(INTERP):
        raise CaptureError(f"production interpreter missing: {INTERP}")
    if not os.path.exists(SERVER):
        raise CaptureError(f"server script missing: {SERVER}")

    proc = subprocess.Popen(
        [INTERP, SERVER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,  # server logs pass through to stderr only
        text=True,
        bufsize=1,
    )
    deadline = time.monotonic() + TIMEOUT_S
    tools = None
    try:
        send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "golden-capture", "version": "1.0.0"},
            },
        })
        resp = recv(proc, deadline, 1)
        if "error" in resp:
            raise CaptureError(f"initialize rejected: {resp['error']}")
        info = resp.get("result", {}).get("serverInfo", {})
        log(f"initialize ok: serverInfo={info}")

        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resp = recv(proc, deadline, 2)
        if "error" in resp:
            raise CaptureError(f"tools/list rejected: {resp['error']}")
        tools = resp.get("result", {}).get("tools")
        if not isinstance(tools, list) or not tools:
            raise CaptureError(f"tools/list returned no tools array: {resp}")
    finally:
        try:  # graceful shutdown: closing stdin lets the stdio server exit on EOF
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        try:
            rc = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = proc.wait()
        if rc != 0:
            raise CaptureError(f"server exited non-zero (rc={rc}) — capture cannot be trusted")
        if not tools:
            raise CaptureError("server exited without a tools/list result")

    os.makedirs(GOLDEN_DIR, exist_ok=True)
    tmp_path = OUT_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(tools, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp_path, OUT_PATH)

    rel = os.path.relpath(OUT_PATH, REPO)
    print(f"wrote {rel} ({len(tools)} tools)")


if __name__ == "__main__":
    try:
        main()
    except CaptureError as exc:
        log(f"FATAL: {exc}")
        sys.exit(1)
    except Exception as exc:  # any unexpected shape = hard fail, never a silent pass
        log(f"FATAL: unexpected {type(exc).__name__}: {exc}")
        sys.exit(1)
