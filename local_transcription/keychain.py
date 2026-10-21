"""the anthropic api key, kept in the macos keychain.

the native app's settings save it here, and the server, the web version and the scripts all
read it from here. a key still in .env works until it is moved, and the keychain wins when
both are set. the key is never logged or sent anywhere but to the anthropic api.
"""

import os
import re
from pathlib import Path

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

# config
SERVICE = "Boswell"
ACCOUNT = "ANTHROPIC_API_KEY"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
KEY_SHAPE = re.compile(r"^sk-ant-[A-Za-z0-9_-]{20,}$")


# helpers
def _stored():
    try:
        return keyring.get_password(SERVICE, ACCOUNT)
    except KeyringError:
        return None


# main
def load():
    """put the keychain's key in the environment, where the anthropic client looks.
    returns where the key in use came from: "keychain", "env", or None."""
    key = _stored()
    if key:
        os.environ[ACCOUNT] = key
        return "keychain"
    return "env" if os.environ.get(ACCOUNT, "").strip() else None


def source():
    """where the key in use came from, without changing anything."""
    stored = _stored()
    if stored and os.environ.get(ACCOUNT) == stored:
        return "keychain"
    return "env" if os.environ.get(ACCOUNT, "").strip() else None


def save(key):
    """keep a new key in the keychain and use it from now on."""
    key = key.strip()
    if not KEY_SHAPE.match(key):
        raise ValueError("that doesn't look like an Anthropic API key, which starts with sk-ant-")
    keyring.set_password(SERVICE, ACCOUNT, key)
    os.environ[ACCOUNT] = key


def remove():
    """forget the keychain's key. a key still in .env is not touched."""
    try:
        keyring.delete_password(SERVICE, ACCOUNT)
    except PasswordDeleteError:
        pass
    os.environ.pop(ACCOUNT, None)


def move_from_env():
    """copy the key from .env into the keychain, then take its line out of .env so there is
    one place for it. the rest of .env is left as it was."""
    key = os.environ.get(ACCOUNT, "").strip()
    if not key:
        raise ValueError("there is no key in .env to move")
    save(key)
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text().splitlines(keepends=True)
        kept = [line for line in lines if not re.match(rf"^\s*(export\s+)?{ACCOUNT}\s*=", line)]
        if kept != lines:
            ENV_FILE.write_text("".join(kept))
