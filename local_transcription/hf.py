"""the models boswell downloads from hugging face, and the token the speaker model needs.

two models: whisper, which transcribes, and pyannote's speaker detection, whose embedding
model also matches voices to profiles. both download once, from setup or settings, with
progress, or the first time they're used. pyannote's model is gated: the account that owns
the token has to accept its terms on hugging face first. once it's in the cache the token
isn't used again. the token is kept in the macos keychain from settings, like the anthropic
key, and HF_TOKEN in .env works too.
"""

import os
import threading
import time
from pathlib import Path

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

# config
SERVICE = "Boswell"
ACCOUNT = "HF_TOKEN"
DIARIZATION_REPO = "pyannote/speaker-diarization-community-1"
TERMS_URL = f"https://hf.co/{DIARIZATION_REPO}"
WHISPER_REPO = "mlx-community/whisper-large-v3-turbo"
# each model, with a file that's only there once it has finished downloading
MODELS = [
    {"repo": WHISPER_REPO, "label": "Transcription", "detail": "Whisper large-v3 turbo", "size": "1.6 GB",
     "check": "weights.safetensors",
     "gated": False},
    {"repo": DIARIZATION_REPO, "label": "Speaker detection", "detail": "pyannote community-1", "size": "34 MB",
     "check": "embedding/pytorch_model.bin", "gated": True},
]
# how often download progress is measured
POLL_S = 0.5

_download = {"running": False, "repo": None, "done": 0, "total": 0, "error": None}


# helpers
def _stored():
    try:
        return keyring.get_password(SERVICE, ACCOUNT)
    except KeyringError:
        return None


def _cached(model):
    from huggingface_hub import try_to_load_from_cache

    return isinstance(try_to_load_from_cache(model["repo"], model["check"]), str)


def _blob_bytes(repo):
    """bytes in a model's cache folder so far, counting files still downloading."""
    from huggingface_hub import constants

    blobs = Path(constants.HF_HUB_CACHE) / f"models--{repo.replace('/', '--')}" / "blobs"
    return sum(p.stat().st_size for p in blobs.glob("*") if p.is_file()) if blobs.is_dir() else 0


def _fetch(model):
    """download one model, measuring progress against the size hugging face gives for it."""
    from huggingface_hub import HfApi, snapshot_download

    use_token = token() if model["gated"] else None
    info = HfApi().model_info(model["repo"], files_metadata=True, token=use_token)
    total = sum(f.size or 0 for f in info.siblings)
    start = _blob_bytes(model["repo"])
    _download.update(repo=model["repo"], done=0, total=total)
    finished = threading.Event()

    def measure():
        while not finished.wait(POLL_S):
            _download["done"] = min(total, max(0, _blob_bytes(model["repo"]) - start))

    threading.Thread(target=measure, daemon=True).start()
    try:
        snapshot_download(model["repo"], token=use_token)
    finally:
        finished.set()
    _download["done"] = total


def _download_missing():
    from huggingface_hub.errors import GatedRepoError, HfHubHTTPError

    try:
        for model in MODELS:
            if not _cached(model):
                _fetch(model)
    except GatedRepoError:
        _download["error"] = "the speaker detection model needs a token whose account has accepted its terms"
    except HfHubHTTPError as err:
        _download["error"] = f"Hugging Face answered: {err}"
    except OSError as err:
        _download["error"] = (f"couldn't download the models: {err}" if os.environ.get("HF_HUB_OFFLINE") != "1"
                              else "HF_HUB_OFFLINE in .env stops downloads. Take it out to download the models")
    except Exception as err:
        _download["error"] = f"couldn't download the models: {err}"
    finally:
        _download.update(running=False, repo=None)


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
    return _cached(MODELS[1])


def models_state():
    """each model and whether it's downloaded, and how a download in progress is going."""
    return {"models": [{**{k: m[k] for k in ("repo", "label", "detail", "size", "gated")}, "ready": _cached(m)} for m in MODELS],
            "download": {k: _download[k] for k in ("running", "repo", "done", "total", "error")}}


def download():
    """start downloading whichever models aren't in the cache yet, in the background."""
    if _download["running"]:
        return
    _download.update(running=True, repo=None, done=0, total=0, error=None)
    threading.Thread(target=_download_missing, daemon=True).start()


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
