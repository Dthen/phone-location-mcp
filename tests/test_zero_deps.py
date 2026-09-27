"""phone-location-mcp T09: zero-deps proof — stdlib-only import scan.

R5: the VmHWM gate is RETIRED fleet-wide. Zero-deps is proven HERE by scan,
not by RAM: every import in every shipped file (``server.py``,
``receiver.py``, ``tools/*.py``) is checked against
``sys.stdlib_module_names`` (Python's own authoritative list — not a
hand-maintained allow-list that rots), with repo-local modules
(``server``/``receiver``, imported in-process by the characterization tool)
exempted. ``fastmcp`` never returns: it is not stdlib, so any reappearance
fails the scan, and an explicit tripwire asserts it with a readable message.

Second proof (runability): ``$BIN -c "import server"`` succeeds with the repo
root on sys.path — the TARGET production interpreter runs the server WITHOUT
the mcp-venv. The import must complete under a deadline with stdin at
/dev/null, which also proves importing ``server`` has no runtime side
effects (no stdin read, no network, no loop start — only defs + the
``__main__`` guard).
"""

import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "server.py"
RECEIVER = REPO / "receiver.py"
TOOLS_DIR = REPO / "tools"

# TARGET production interpreter (D.1 cutover line; grep: BIN_PY).
BIN_PY = "/usr/bin/python3"

# Repo-root modules shipped files may legitimately import (first-party —
# tools/characterize_old_server.py does `import server` in-process under the
# legacy $PYO interpreter; that is not a dependency).
#
# `prod_py` is this repo's own interpreter resolver (tools/prod_py.py), imported
# by the two pre-migration capture tools. It is first-party for the same reason
# as `server`/`receiver` — a repo-root module, not a distribution. This entry
# is NOT a hole in the gate: the scan still walks tools/prod_py.py itself, so
# the moment it imports anything outside sys.stdlib_module_names (fastmcp, or
# anything else) this test fails. Adding a first-party name widens the exempt
# set by exactly one repo-local module, never past the stdlib boundary.
FIRST_PARTY = {"server", "receiver", "prod_py"}

# Frameworks this migration shed. Non-stdlib already fails the generic scan;
# these give the "never returns" tripwire a named, self-explaining failure.
BANNED = {"fastmcp", "mcp"}

IMPORT_DEADLINE = 30.0  # seconds; a side-effecting import would hang or die


def shipped_files():
    """Every shipped .py file the scan must cover."""
    return [SERVER, RECEIVER] + sorted(TOOLS_DIR.glob("*.py"))


def collect_imports(path):
    """All import bindings in `path` — module-level AND nested (function-level
    imports are real runtime deps too), as (top_level_module, lineno) tuples.

    Relative imports (level > 0) are skipped: they stay inside the repo.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(
                (alias.name.split(".")[0], node.lineno) for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.append((node.module.split(".")[0], node.lineno))
    return found


def scan():
    """[(relpath, module, lineno)] for every import in every shipped file."""
    rows = []
    for path in shipped_files():
        rel = str(path.relative_to(REPO))
        rows.extend((rel, mod, lineno) for mod, lineno in collect_imports(path))
    return rows


# --------------------------------------------------------------------- scan


def test_scan_covers_every_shipped_file():
    """Guard against a vacuous pass: the file set and import set are non-empty
    and include the production files by name."""
    files = {rel for rel, _mod, _lineno in scan()}
    assert "server.py" in files, "scan missed server.py"
    assert "receiver.py" in files, "scan missed receiver.py"
    assert any(f.startswith("tools/") for f in files), "scan missed tools/*.py"


def test_every_shipped_import_is_stdlib_or_first_party():
    """The core zero-deps scan. Failure names the offending import (file:line
    + module), per the card's quality checklist."""
    if not hasattr(sys, "stdlib_module_names"):  # py3.10+ floor (REFERENCE §1/D8)
        raise AssertionError(
            f"pytest interpreter {sys.version.split()[0]} < 3.10: no "
            "sys.stdlib_module_names — run under $PYH (3.11)"
        )
    offenders = [
        (rel, mod, lineno)
        for rel, mod, lineno in scan()
        if mod not in sys.stdlib_module_names and mod not in FIRST_PARTY
    ]
    assert not offenders, "third-party imports found: " + "; ".join(
        f"{rel}:{lineno} imports {mod!r} (not in sys.stdlib_module_names)"
        for rel, mod, lineno in offenders
    )


def test_fastmcp_never_returns():
    """Explicit tripwire: the shed framework must not reappear in any shipped
    import, even transitively-named ones."""
    modules = {mod for _rel, mod, _lineno in scan()}
    leaked = sorted(modules & BANNED)
    assert not leaked, (
        f"banned module(s) {leaked} imported again — this server is "
        "stdlib-only by owner ruling (PLAN D5, _chain R5)"
    )


def test_receiver_is_already_clean():
    """receiver.py never imported fastmcp (live-verified pre-migration; the
    card checklist requires this status to stay asserted, not just prose)."""
    mods = {mod for mod, _lineno in collect_imports(RECEIVER)}
    assert mods, "receiver.py scan came up empty — did the file move?"
    assert not (mods & BANNED), f"receiver.py imports framework module(s) {sorted(mods & BANNED)}"
    non_std = sorted(m for m in mods if m not in sys.stdlib_module_names)
    assert not non_std, f"receiver.py has non-stdlib imports: {non_std}"


# ---------------------------------------------------------------- runability


def test_server_imports_on_target_interpreter_without_the_venv():
    """/usr/bin/python3 imports server with only the repo root on sys.path —
    proof it runs WITHOUT the mcp-venv, with no import-time side effects
    (deadline + stdin=/dev/null: a blocking or looping import fails here)."""
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "import server\n"
        "print(server.SERVER_INFO['name'], 'runs on', sys.version.split()[0])\n"
    )
    proc = subprocess.run(
        [BIN_PY, "-c", code],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=IMPORT_DEADLINE,
    )
    assert proc.returncode == 0, (
        f"{BIN_PY} failed to import server.py WITHOUT the mcp-venv:\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "phone-location runs on" in proc.stdout, (
        f"import succeeded but module identity wrong / side effects printed "
        f"to stdout: {proc.stdout!r}"
    )
    major, minor = (int(x) for x in proc.stdout.split("runs on")[1].split(".")[:2])
    assert (major, minor) >= (3, 10), (
        f"target interpreter {BIN_PY} is {major}.{minor} — below the REFERENCE "
        "§1 py3.10 floor"
    )
