"""the anthropic api key, kept in the macos keychain, and the keychain for the other tokens.

the native app's settings save it here, and the server, the web version and the scripts all
read it from here. a key still in .env works until it is moved, and the keychain wins when
both are set. the key is never logged or sent anywhere but to the anthropic api.
"""

import os
import re

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from local_transcription import paths

# config
SERVICE = "Wemmick"
# where values were kept while the app was called heron, and boswell before that, newest
# first. copied over when first read
OLD_SERVICES = ("Heron", "Boswell")
ACCOUNT = "ANTHROPIC_API_KEY"
# the library's .env, which the app reads, and the project folder's, which scripts read too
ENV_FILES = (paths.ENV_FILE, paths.PROJECT_DIR / ".env")
KEY_SHAPE = re.compile(r"^sk-ant-[A-Za-z0-9_-]{20,}$")


# helpers
def get(account):
    """a value from the keychain, or None. one saved under an old name is copied over, and
    left there for the old app, which may still be open."""
    try:
        value = keyring.get_password(SERVICE, account)
        for old in OLD_SERVICES:
            if value is not None:
                break
            value = keyring.get_password(old, account)
            if value is not None:
                keyring.set_password(SERVICE, account, value)
        return value
    except KeyringError:
        return None


def put(account, value):
    keyring.set_password(SERVICE, account, value)


def forget(account):
    """delete a value, under the old names too, so it can't come back from there."""
    for service in (SERVICE, *OLD_SERVICES):
        try:
            keyring.delete_password(service, account)
        except PasswordDeleteError:
            pass


def _stored():
    return get(ACCOUNT)


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
    put(ACCOUNT, key)
    os.environ[ACCOUNT] = key


def remove():
    """forget the keychain's key. a key still in .env is not touched."""
    forget(ACCOUNT)
    os.environ.pop(ACCOUNT, None)


def move_from_env():
    """copy the key from .env into the keychain, then take its line out of each .env so there
    is one place for it. the rest of each file is left as it was."""
    key = os.environ.get(ACCOUNT, "").strip()
    if not key:
        raise ValueError("there is no key in .env to move")
    save(key)
    for env_file in ENV_FILES:
        if env_file.exists():
            lines = env_file.read_text().splitlines(keepends=True)
            kept = [line for line in lines if not re.match(rf"^\s*(export\s+)?{ACCOUNT}\s*=", line)]
            if kept != lines:
                env_file.write_text("".join(kept))
