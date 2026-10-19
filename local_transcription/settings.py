"""choices made on the settings page, kept in data/settings.json so they are never committed.

also the claude models the app offers and their list prices, which turn the token counts of
each request into an estimated cost.
"""

import json
import os
from pathlib import Path

# config
SETTINGS_PATH = Path(__file__).parent.parent / "data" / "settings.json"
ACCENTS = ["blue", "red", "green", "purple", "orange"]
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
    return {"accent": saved.get("accent") if saved.get("accent") in ACCENTS else "blue",
            "claude_model": saved.get("claude_model") if saved.get("claude_model") in MODELS else None}


def save(**changes):
    current = load()
    if changes.get("accent") is not None and changes["accent"] not in ACCENTS:
        raise ValueError(f"accent must be one of {', '.join(ACCENTS)}")
    if changes.get("claude_model") is not None and changes["claude_model"] not in MODELS:
        raise ValueError(f"model must be one of {', '.join(MODELS)}")
    current.update({k: v for k, v in changes.items() if v is not None})
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(current, indent=2))
    return current


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
