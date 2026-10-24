"""apple intelligence, the model built into macos 26, for finding to-dos on this mac.

python can't reach it, so BoswellAI, a small swift helper built with the mac app, runs each
prompt and answers in json. the app tells the server where the helper is in BOSWELL_AI, and
a server started by hand looks for it in mac/.build. the model only takes about 4,000
tokens at a time, so callers send long text in parts, and a part that's still too long
comes back marked so it can be split.
"""

import json
import os
import subprocess
import time
from pathlib import Path

# config
HELPER_NAME = "BoswellAI"
BUILD_DIR = Path(__file__).resolve().parent.parent / "mac" / ".build"
# whether the model is ready changes rarely, as when apple intelligence is turned on
CHECK_TTL_S = 300
# one prompt can take a while on an older mac
TIMEOUT_S = 600

_check = {"at": 0, "result": None}


# helpers
def helper():
    """the helper's path, or None when it hasn't been built."""
    named = os.environ.get("BOSWELL_AI")
    if named and Path(named).exists():
        return Path(named)
    built = [p for p in (BUILD_DIR / "release" / HELPER_NAME, BUILD_DIR / "debug" / HELPER_NAME) if p.exists()]
    return max(built, key=lambda p: p.stat().st_mtime) if built else None


def _run(args, payload=None, timeout=TIMEOUT_S):
    path = helper()
    if not path:
        raise RuntimeError("the Apple Intelligence helper isn't built. Run uv run make_app.py")
    result = subprocess.run([str(path), *args], input=json.dumps(payload) if payload is not None else None,
                            capture_output=True, text=True, timeout=timeout)
    try:
        answer = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError((result.stderr or "the Apple Intelligence helper gave no answer").strip()[-300:]) from None
    if answer.get("error"):
        raise RuntimeError(answer["error"])
    return answer


# main
def availability():
    """whether the model can be used, as a dict of available and the reason it can't."""
    if _check["result"] is None or time.time() - _check["at"] > CHECK_TTL_S:
        try:
            _check["result"] = _run(["check"], timeout=30)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as err:
            _check["result"] = {"available": False, "reason": str(err)}
        _check["at"] = time.time()
    return _check["result"]


def todos(instructions, prompts):
    """run each prompt for to-dos with the same instructions. returns one entry per prompt:
    a list of to-do dicts, or None for a prompt too long for the model."""
    answer = _run(["todos"], {"instructions": instructions, "prompts": prompts})
    return [None if r.get("too_long") else r.get("todos", []) for r in answer["results"]]
