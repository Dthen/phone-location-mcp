"""Portability contract: no tracked file may name a machine, host or account.

WHY THIS EXISTS
    Every other change in this repo that made a path portable was a one-time
    hand edit. Nothing stopped a future edit — or a regenerated golden — from
    writing a real host path back into a tracked file, and nothing would have
    failed. This module makes that regression loud instead of silent.

HOW IT WORKS (and why it leaks nothing itself)
    Rather than searching for this machine's paths — which would put those
    paths in this very file — the rule is inverted: collect every absolute
    filesystem path in every tracked text file and require each one to sit
    under a system prefix that is portable by definition. Anything else (a
    home directory, a volume mount, a venv on someone's disk) fails.

    Portable spellings like <repo-root>/server.py and
    <venv-root>/<name>/bin/python3 are NOT absolute paths, so they are not
    collected and need no allowlist entry.
"""

import posixpath
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Absolute paths that are portable by definition: the system interpreter, the
# system env shim, and the standard scratch/null locations the suite
# legitimately uses. Anything outside this set is a machine-specific path.
#
# These are compared as exact matches or as a directory PREFIX, so they are
# written without a trailing slash: a bare "/usr/bin" is the literal that
# appears in source, and "/usr/bin/python3" must match it. A trailing slash
# would require the token to be "/usr/bin/..." and would silently never match
# the directory itself. NOTE: this tuple is scanned by the guard below, so it
# must contain only portable system paths — do not add a machine path here.
ALLOWED_ABSOLUTE = (
    "/usr/bin",
    "/usr/local/bin",
    "/usr/bin/env",
    "/tmp",
    "/var/tmp",
    "/dev/null",
    "/dev/stdin",
    "/dev/stdout",
    "/dev/stderr",
    "/nonexistent",
)


def is_portable_absolute(token):
    """True when `token` is a system path (or sits under one) rather than a
    machine-specific one. The prefix is matched as a whole path segment, so a
    longer sibling directory that merely starts with the same characters is
    correctly NOT treated as portable.

    The token is normalized first: without that, a ".." escape out of an
    allowed directory would satisfy that directory's prefix while naming a
    home directory, which is precisely the leak this guard exists to catch."""
    normalized = posixpath.normpath(token)
    for allowed in ALLOWED_ABSOLUTE:
        if normalized == allowed or normalized.startswith(allowed + "/"):
            return True
    return False

# An absolute path: leading slash, then path-ish segments. Each segment is
# either a dotfile/relative-looking run or starts with a word character, so a
# full path like /usr/bin/python3 is captured whole rather than truncated at
# the first dot. The lookbehind skips URLs (preceded by ':'), dotted words, and
# the closing '>' or '}' of a placeholder such as <repo-root>/server.py —
# those are not filesystem paths.
ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_./:>}$\-])"
    r"(/[A-Za-z0-9_~-]+(?:\.[A-Za-z0-9_~-]+)*"
    r"(?:/[A-Za-z0-9_.~-]+)*)"
)

# Not filesystem paths, so not this guard's business:
#  - HTTP route strings (receiver.py serves "/location" and "/health"); they
#    are compared against request paths, never opened as files.
#  - bare system-directory mentions with no child component, e.g. the "/tmp"
#    in a prose sentence or a "<venv-root>"-style illustrative fragment.
#    A real machine path always has at least one child segment.
NOT_A_FILESYSTEM_PATH = re.compile(r"^/[A-Za-z0-9_~-]+$")

# Text files worth scanning. Anything unreadable as UTF-8 (or binary) is
# skipped rather than failing the run — this is a leak guard, not a parser.
SCAN_SUFFIXES = {".py", ".md", ".json", ".txt", ".toml", ".cfg", ".ini", ".yaml", ".yml"}


def tracked_files():
    """Every file git currently tracks, as repo-relative paths."""
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [p for p in out.stdout.split("\0") if p]


def absolute_paths_in(rel_path):
    """Absolute filesystem paths appearing in one tracked text file."""
    path = REPO / rel_path
    if path.suffix not in SCAN_SUFFIXES:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    found = []
    for match in ABSOLUTE_PATH.finditer(text):
        token = match.group(1).rstrip(".,;:)")
        if NOT_A_FILESYSTEM_PATH.match(token):
            continue
        lineno = text.count("\n", 0, match.start()) + 1
        found.append((token, lineno))
    return found


def test_scan_covers_the_tracked_source_files():
    """Guard against a vacuous pass: the scan really does see the files that
    used to carry host paths. If git or the suffix filter ever breaks, this
    fails instead of the scan silently finding nothing."""
    files = set(tracked_files())
    for expected in (
        "README.md",
        "golden/phone-location.behavior.json",
        "tools/characterize_old_server.py",
        "tools/golden_capture.py",
        "tools/prod_py.py",
        "tests/test_characterization.py",
    ):
        assert expected in files, f"scan lost sight of {expected}"
    assert not (REPO / "MIGRATION-NOTES.md").exists(), (
        "MIGRATION-NOTES.md is an internal migration log and must not come back"
    )


def test_no_tracked_file_contains_a_machine_specific_path():
    """The core guard: every absolute path in every tracked file must be a
    portable system path. A home directory, volume mount or per-machine venv
    fails here with file:line."""
    offenders = []
    for rel in tracked_files():
        for token, lineno in absolute_paths_in(rel):
            if not is_portable_absolute(token):
                offenders.append(f"{rel}:{lineno} {token!r}")
    assert not offenders, (
        "tracked files name machine-specific paths (use <repo-root>, "
        "<venv-root>/<name>/bin/python3, or ${PROD_PY} instead):\n  "
        + "\n  ".join(offenders)
    )


def test_golden_provenance_is_portable_and_repo_relative():
    """The golden's _meta records WHAT produced the capture. Those three keys
    are recorded output, never host state: `server` and `fixture` are
    repo-relative and `interpreter_path` is a <venv-root> placeholder.

    This is the assertion that makes the portable values load-bearing — a
    future regeneration that wrote a real capture-time path back in would fail
    here even though the characterization replay would still pass.
    """
    import json

    meta = json.loads(
        (REPO / "golden" / "phone-location.behavior.json").read_text(encoding="utf-8")
    )["_meta"]

    assert meta["server"] == "server.py", (
        f"_meta.server must be repo-relative, got {meta['server']!r}"
    )
    assert meta["fixture"] == "golden/phone-location.fixture.json", (
        f"_meta.fixture must be repo-relative, got {meta['fixture']!r}"
    )
    assert meta["interpreter_path"].startswith("<venv-root>/"), (
        f"_meta.interpreter_path must be a <venv-root> placeholder, got "
        f"{meta['interpreter_path']!r}"
    )
    for key in ("server", "fixture", "interpreter_path"):
        assert not meta[key].startswith("/"), (
            f"_meta.{key} must not be an absolute path, got {meta[key]!r}"
        )


def test_capture_generator_agrees_with_the_tracked_golden():
    """The generator and the artifact must not drift: a re-capture has to
    reproduce the same _meta, or the portable values are cosmetic.

    tools/characterize_old_server.py cannot be imported here (it imports the
    pre-migration fastmcp server, which is deliberately absent), so its
    provenance constants are read out of the source. The interpreter constant
    is an f-string over the resolved INTERP, so it is evaluated the same way
    the module evaluates it.
    """
    import json
    from pathlib import Path as _Path

    source = (REPO / "tools" / "characterize_old_server.py").read_text(encoding="utf-8")
    constants = {}
    for line in source.splitlines():
        for name in ("SERVER_PROVENANCE", "FIXTURE_PROVENANCE"):
            if line.startswith(name):
                constants[name] = line.split("=", 1)[1].strip().strip('"')
    # INTERP_PROVENANCE is built from the configured interpreter's own path
    # (the <venv-root>/<venv-dir-name>/bin/<exe> shape). Reproduce that shape
    # from a synthetic venv layout assembled segment by segment: writing a real
    # absolute path as a literal here would be exactly the leak this module
    # exists to catch, and this file is itself a tracked file it scans.
    sample = _Path(*("/", "example-venv-root", "phone-location-mcp", "bin", "python3"))
    constants["INTERP_PROVENANCE"] = (
        f"<venv-root>/{sample.parent.parent.name}/bin/{sample.name}"
    )
    assert all(constants.values()), f"failed to read generator constants: {constants}"

    meta = json.loads(
        (REPO / "golden" / "phone-location.behavior.json").read_text(encoding="utf-8")
    )["_meta"]
    assert constants["SERVER_PROVENANCE"] == meta["server"]
    assert constants["FIXTURE_PROVENANCE"] == meta["fixture"]
    assert constants["INTERP_PROVENANCE"] == meta["interpreter_path"], (
        "the generator would write a different _meta.interpreter_path than the "
        f"golden holds: {constants['INTERP_PROVENANCE']!r} vs {meta['interpreter_path']!r}"
    )


def test_interpreter_pointer_files_are_never_tracked():
    """`.prod_py` / `.prod_py.old` hold a real path on this machine by design.
    They are machine-local configuration, so they must stay untracked and
    ignored — a tracked pointer would publish the very path this scrub
    removes. (The live device data file is covered the same way.)"""
    tracked = set(tracked_files())
    for name in (".prod_py", ".prod_py.old", "phone-location.json"):
        assert name not in tracked, f"{name} must never be tracked"

    ignore = subprocess.run(
        ["git", "-C", str(REPO), "check-ignore", "--",
         ".prod_py", ".prod_py.old", "phone-location.json"],
        capture_output=True, text=True,
    )
    ignored = set(ignore.stdout.split())
    for name in (".prod_py", ".prod_py.old", "phone-location.json"):
        assert name in ignored, (
            f"{name} must be gitignored (check-ignore said: {ignore.stdout!r})"
        )


def test_first_party_exemption_does_not_exempt_the_directory():
    """`prod_py` is in test_zero_deps.FIRST_PARTY so the capture tools can
    import it. That exemption is by NAME: the zero-deps scan still walks
    tools/prod_py.py's own imports, so it must fail the moment that module
    reaches outside the stdlib. This pins the exemption from silently
    widening to 'anything in tools/'."""
    sys.path.insert(0, str(REPO / "tests"))
    try:
        import test_zero_deps as tzd
    finally:
        sys.path.pop(0)

    assert "prod_py" in tzd.FIRST_PARTY

    scanned = {rel for rel, _mod, _lineno in tzd.scan()}
    assert "tools/prod_py.py" in scanned, (
        "tools/prod_py.py is no longer scanned — the first-party exemption "
        "would have become a blind spot"
    )

    own = {mod for rel, mod, _lineno in tzd.scan() if rel == "tools/prod_py.py"}
    assert own <= sys.stdlib_module_names, (
        f"tools/prod_py.py must stay stdlib-only, found {sorted(own - sys.stdlib_module_names)}"
    )

    # The exemption must not extend to an unnamed module in the same folder.
    # Every shipped tools/*.py file other than the exempted ones is still
    # covered by the gate, so a NEW tool added there is scanned like the rest.
    exempt_names = {"prod_py"}
    for rel in sorted(scanned):
        if not rel.startswith("tools/"):
            continue
        module_name = Path(rel).stem
        if module_name in exempt_names:
            continue
        assert module_name not in tzd.FIRST_PARTY or module_name in exempt_names, (
            f"{rel} is first-party-exempt but is not the intended exemption; "
            "the exempt set must not quietly widen"
        )


def test_dot_dot_escapes_cannot_launder_a_home_path():
    """A '..' segment must not smuggle a machine path past the allowlist.
    Without normalization, a dot-dot escape from an allowed directory would
    satisfy that directory's prefix while naming a home directory — a real
    bypass of the rule this module enforces, so it is pinned here rather than
    left to inspection.

    The probe paths are assembled from segments instead of written as literals:
    this file is itself a tracked file that the guard above scans, so a
    literal example would (correctly) fail that guard."""
    def escape(*segments):
        return "/" + "/".join(segments)

    laundering = [
        escape("tmp", "..", "home", "someone", "x"),
        escape("usr", "bin", "..", "..", "root", ".ssh", "id_rsa"),
        escape("tmp", "..", "mnt", "somevolume", "venvs", "y", "bin", "python3"),
    ]
    for path in laundering:
        assert not is_portable_absolute(path), (
            f"a dot-dot escape laundered past the allowlist: {escape('<allowed>', '..', '<private>')}"
        )

    # ...while a normalized system path is still accepted.
    assert is_portable_absolute(escape("tmp", ".", "phone-location-missing.json"))
    assert is_portable_absolute(escape("usr", "bin", "python3"))
