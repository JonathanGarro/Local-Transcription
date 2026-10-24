"""the hugging face token, needed once to download the speaker detection model.

pyannote's model is gated: the account that owns the token has to accept its terms on
hugging face first. once the model is in the cache the token isn't used again. the token
is kept in the macos keychain from settings, like the anthropic key, and HF_TOKEN in .env
works too.
"""

import os

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

# config
SERVICE = "Boswell"
ACCOUNT = "HF_TOKEN"
DIARIZATION_REPO = "pyannote/speaker-diarization-community-1"
TERMS_URL = f"https://hf.co/{DIARIZATION_REPO}"


# helpers
def _stored():
    try:
        return keyring.get_password(SERVICE, ACCOUNT)
    except KeyringError:
        return None


# main
def token():
    """the token in use, from the keychain, else .env, or None."""
    return _stored() or os.environ.get(ACCOUNT, "").strip() or None


def token_source():
    if _stored():
        return "keychain"
    return "env" if os.environ.get(ACCOUNT, "").strip() else None


def masked_token():
    key = token()
    return "*" * 24 + key[-3:] if key and len(key) > 3 else None


def save_token(value):
    value = value.strip()
    if not value.startswith("hf_") or " " in value:
        raise ValueError("that doesn't look like a Hugging Face token, which starts with hf_")
    keyring.set_password(SERVICE, ACCOUNT, value)


def remove_token():
    try:
        keyring.delete_password(SERVICE, ACCOUNT)
    except PasswordDeleteError:
        pass


def model_ready():
    """whether the speaker detection model is downloaded, so no token is needed."""
    from huggingface_hub import try_to_load_from_cache

    return isinstance(try_to_load_from_cache(DIARIZATION_REPO, "config.yaml"), str)


def check():
    """raises RuntimeError saying what's wrong when the token can't download the model."""
    from huggingface_hub import auth_check, whoami
    from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError

    if not token():
        raise RuntimeError("add a Hugging Face token first")
    if os.environ.get("HF_HUB_OFFLINE") == "1":
        raise RuntimeError("HF_HUB_OFFLINE in .env keeps the app offline, so the token can't be checked")
    try:
        # a bad token reads as no token, which the gated model turns away like unaccepted terms
        whoami(token=token())
    except HfHubHTTPError:
        raise RuntimeError("Hugging Face didn't accept the token") from None
    except OSError as err:
        raise RuntimeError(f"couldn't reach Hugging Face: {err}") from None
    try:
        auth_check(DIARIZATION_REPO, token=token())
    except GatedRepoError:
        raise RuntimeError("the token works, but its account hasn't accepted the model's terms yet") from None
    except RepositoryNotFoundError:
        raise RuntimeError("Hugging Face didn't accept the token") from None
    except HfHubHTTPError as err:
        raise RuntimeError(f"Hugging Face answered: {err}") from None
    except OSError as err:
        raise RuntimeError(f"couldn't reach Hugging Face: {err}") from None
