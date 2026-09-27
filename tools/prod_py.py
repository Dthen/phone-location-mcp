"""Portable production-interpreter resolution for this repo's capture tools.

WHY THIS EXISTS
    The pre-migration fastmcp venv and the migrated-era production interpreter
    are NOT the same interpreter. A tool that needs the former must be told
    where it is; it must never guess.

    There is deliberately NO `sys.executable` fallback. `sys.executable` is
    whatever happened to launch the process, so a fallback would silently
    substitute the wrong interpreter and make a gate vacuous — the tool would
    happily record provenance for, or gate against, an interpreter that was
    never configured. Raising is the honest outcome; a skip would be worse
    still, replacing a real proof with a no-op that passes.

RESOLUTION ORDER (first hit wins)
    1. `$PROD_PY_PHONE_LOCATION_OLD` — explicit, per-invocation override.
    2. `<repo-root>/.prod_py.old` — a pointer file holding the same path on
       one line. Gitignored and untracked, so it is machine-local and can
       never be published. Its existence is what lets the suite and the tools
       run on a developer machine without exporting anything.

    Note this is deliberately a *different* variable and pointer file from the
    `${PROD_PY}` / `.prod_py` pair the migrated-era gates use. Repointing a
    pre-flip capture tool at a different interpreter is a silent way to
    invalidate an artifact, so the two never share a setting.

    This repo's migrated-era gates do not need a resolver at all: they pin the
    portable system interpreter `/usr/bin/python3` directly, so there is no
    `.prod_py` consumer here and none is invented.

No host-specific path, volume name, disk id or hostname appears in this file;
the only paths written are placeholders and repo-relative locations.
"""

import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

ENV_VAR = "PROD_PY_PHONE_LOCATION_OLD"
POINTER = REPO / ".prod_py.old"


def prod_py() -> str:
    """Return the configured pre-migration (fastmcp) interpreter path.

    Raises RuntimeError when unconfigured — never falls back to
    ``sys.executable``, and never skips.
    """
    env = os.environ.get(ENV_VAR)
    if env and env.strip():
        return env.strip()
    if POINTER.is_file():
        # Unwrap to the actionable RuntimeError: an unreadable or non-UTF-8
        # pointer would otherwise surface as a bare OSError/UnicodeDecodeError
        # and hide the resolution instructions. The file is also allowed to
        # disappear between the is_file() check and the read (TOCTOU).
        try:
            pointer_value = POINTER.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            pointer_value = ""
        if pointer_value:
            return pointer_value
    raise RuntimeError(
        "Pre-migration interpreter not configured. Set $"
        f"{ENV_VAR}, or write the interpreter path to {POINTER.name} "
        "(gitignored, untracked, machine-local). This tool must not fall back "
        "to sys.executable: the suite interpreter is not the pre-migration "
        "fastmcp interpreter, so that fallback would substitute the wrong "
        "interpreter and make the resulting gate vacuous."
    )
