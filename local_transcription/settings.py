"""choices made on the settings page, kept in data/settings.json so they are never committed.

also the claude models the app offers and their list prices, which turn the token counts of
each request into an estimated cost.
"""

import json
import os
from pathlib import Path

from local_transcription import paths, templates

# config
SETTINGS_PATH = paths.DATA_DIR / "settings.json"
ACCENTS = ["blue", "red", "green", "purple", "orange"]
# where found to-dos go, one app at a time. "none" turns the to-do features off
TODO_APPS = ["things", "todoist", "reminders", "none"]
THINGS_APP = Path("/Applications/Things3.app")
# which channel of a zoom recording split into two is your mic
MIC_CHANNELS = ["left", "right"]
# what writes notes: an mlx model on this mac, or claude
SUMMARY_BACKENDS = ["local", "claude"]
# what setup asks for, which resetting it forgets
SETUP_KEYS = ["owner", "summary_backend", "mic_channel", "meeting_notes_dir", "people_dir", "project_index_dir",
              "todo_app", "note_template", "things_areas"]
# obsidian folders chosen on the settings page, which win over the same names in .env
FOLDERS = {"meeting_notes_dir": "MEETING_NOTES_DIR", "people_dir": "PEOPLE_DIR", "project_index_dir": "PROJECT_INDEX_DIR"}
DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"
# dollars per million tokens. writing to the cache costs a quarter more than plain input
MODELS = {
    "claude-opus-5-5": {"label": "Claude Opus 5.5", "input": 4.00, "output": 20.00, "cache_read": 0.20},
    "claude-sonnet-5-5": {"label": "Claude Sonnet 5.5", "input": 2.00, "output": 10.00, "cache_read": 0.20},
    "claude-haiku-5-5": {"label": "Claude Haiku 5.5", "input": 0.10, "output": 0.50, "cache_read": 0.01},
    "claude-fable-5-1": {"label": "Claude Fable 5.1", "input": 10.00, "output": 50.00, "cache_read": 0.25},
}
# models a declined request can fall back from, which may answer instead
OTHER_PRICES = {
    "claude-opus-5": {"input": 5.00, "output": 25.00, "cache_read": 0.50},
    "claude-opus-4-8": {"input": 5.00, "output": 25.00, "cache_read": 0.50},
}
CACHE_WRITE_FACTOR = 1.25
# models whose declined requests the api can retry on another model
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"}


# main
def load():
    try:
        saved = json.loads(SETTINGS_PATH.read_text())
    except (OSError, ValueError):
        saved = {}
    # before there was a choice, to-dos went to things, so that stays the default where it's installed
    default_app = "things" if THINGS_APP.exists() else "none"
    return {"accent": saved.get("accent") if saved.get("accent") in ACCENTS else "blue",
            "claude_model": saved.get("claude_model") if saved.get("claude_model") in MODELS else None,
            "todo_app": saved.get("todo_app") if saved.get("todo_app") in TODO_APPS else default_app,
            **{key: saved.get(key) or None for key in FOLDERS},
            # a template of your own that was deleted falls back to the standard one
            "note_template": saved.get("note_template") if saved.get("note_template") in templates.names()
            else templates.DEFAULT,
            # who the app is for, and the rest asked for when it's first opened. each falls back to .env
            "owner": str(saved.get("owner") or "").strip() or None,
            "mic_channel": saved.get("mic_channel") if saved.get("mic_channel") in MIC_CHANNELS else None,
            "summary_backend": saved.get("summary_backend") if saved.get("summary_backend") in SUMMARY_BACKENDS
            else None,
            "setup_done": bool(saved.get("setup_done")),
            "setup_reset": bool(saved.get("setup_reset")),
            # things areas to read projects from. an empty list is every area, None falls back to .env
            "things_areas": saved.get("things_areas") if isinstance(saved.get("things_areas"), list) else None}


def save(**changes):
    current = load()
    if changes.get("accent") is not None and changes["accent"] not in ACCENTS:
        raise ValueError(f"accent must be one of {', '.join(ACCENTS)}")
    if changes.get("claude_model") is not None and changes["claude_model"] not in MODELS:
        raise ValueError(f"model must be one of {', '.join(MODELS)}")
    if changes.get("todo_app") is not None and changes["todo_app"] not in TODO_APPS:
        raise ValueError(f"to-do app must be one of {', '.join(TODO_APPS)}")
    if changes.get("mic_channel") and changes["mic_channel"] not in MIC_CHANNELS:
        raise ValueError(f"the mic channel must be one of {', '.join(MIC_CHANNELS)}")
    if changes.get("summary_backend") and changes["summary_backend"] not in SUMMARY_BACKENDS:
        raise ValueError(f"notes are written by one of {', '.join(SUMMARY_BACKENDS)}")
    if changes.get("things_areas") is not None:
        changes["things_areas"] = [str(a).strip() for a in changes["things_areas"] if str(a).strip()]
    if changes.get("owner") is not None:
        changes["owner"] = changes["owner"].strip()
    if changes.get("note_template") is not None and changes["note_template"] not in templates.names():
        raise ValueError(f"there's no note template named {changes['note_template']}")
    for key in FOLDERS:
        # "" clears a folder, which falls back to .env
        if changes.get(key):
            path = Path(changes[key]).expanduser()
            if not path.is_dir():
                raise ValueError(f"there's no folder at {path}")
            changes[key] = str(path)
    current.update({k: v for k, v in changes.items() if v is not None})
    if changes.get("setup_done"):
        current.pop("setup_reset", None)
    # unset values aren't kept, so load gives them their defaults
    current = {k: v for k, v in current.items() if v not in ("", None)}
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(current, indent=2))
    return current


def folder(key):
    """an obsidian folder, from the settings page, else .env, as (path, "settings" or "env"),
    or (None, None) when neither names one."""
    chosen = load()[key] or os.environ.get(FOLDERS[key], "").strip()
    if not chosen:
        return None, None
    return Path(chosen).expanduser(), "settings" if load()[key] else "env"


def owner():
    """your name, from settings, else MIC_OWNER in .env, or None before it's set."""
    return load()["owner"] or os.environ.get("MIC_OWNER", "").strip() or None


def mic_channel():
    """"left" or "right", from settings, else MIC_CHANNEL in .env, else left."""
    found = load()["mic_channel"] or os.environ.get("MIC_CHANNEL", "").strip()
    return found if found in MIC_CHANNELS else "left"


def summary_backend():
    """what writes notes, from settings, else SUMMARY_BACKEND in .env, else the local model."""
    found = load()["summary_backend"] or os.environ.get("SUMMARY_BACKEND", "").strip()
    return found if found in SUMMARY_BACKENDS else "local"


def setup_needed():
    """whether to show the setup that's shown when the app is first opened: not yet finished,
    and no name in .env, so someone already set up through .env doesn't see it, unless setup
    was reset."""
    saved = load()
    return not saved["setup_done"] and (saved["setup_reset"] or not os.environ.get("MIC_OWNER", "").strip())


def reset_setup():
    """forget what setup asked for, so it shows again when the app opens, even with a name in
    .env. the library, the keys and tokens in the keychain, and .env itself are left alone."""
    try:
        saved = json.loads(SETTINGS_PATH.read_text())
    except (OSError, ValueError):
        saved = {}
    for key in SETUP_KEYS:
        saved.pop(key, None)
    saved.update(setup_done=False, setup_reset=True)
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(saved, indent=2))


def claude_model():
    """the model picked on the settings page, else CLAUDE_MODEL from .env, else the default."""
    return load()["claude_model"] or os.environ.get("CLAUDE_MODEL") or DEFAULT_CLAUDE_MODEL


def cost(model, input_tokens=0, output_tokens=0, cache_write_tokens=0, cache_read_tokens=0):
    """estimated dollars for one request at list prices, None for a model not priced here."""
    price = MODELS.get(model) or OTHER_PRICES.get(model)
    if not price:
        return None
    return ((input_tokens or 0) * price["input"] + (output_tokens or 0) * price["output"]
            + (cache_write_tokens or 0) * price["input"] * CACHE_WRITE_FACTOR
            + (cache_read_tokens or 0) * price["cache_read"]) / 1e6


def masked_key():
    """the api key as asterisks with its last three characters, or None when it is not set."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return "*" * 24 + key[-3:] if len(key) > 3 else None
