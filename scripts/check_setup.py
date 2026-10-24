"""confirm ffmpeg, the hugging face token, and model downloads all work.

usage:
    uv run scripts/check_setup.py            # download models if needed and test them
    uv run scripts/check_setup.py --bench    # also download and test faster-whisper
    uv run scripts/check_setup.py --offline  # verify everything loads from cache only
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# config
# the project folder, one up from scripts/
BASE_DIR = Path(__file__).resolve().parent.parent
# values already set in the shell take precedence over .env
load_dotenv(BASE_DIR / ".env")
sys.path.insert(0, str(BASE_DIR))

from local_transcription import hf, paths  # noqa: E402

OUTPUT_DIR = paths.OUTPUT_DIR / "check"
MLX_WHISPER_REPO = "mlx-community/whisper-large-v3-turbo"
FASTER_WHISPER_MODEL = "large-v3-turbo"
DIARIZATION_REPO = "pyannote/speaker-diarization-community-1"
SAMPLE_RATE = 16000
TEST_LINES = [
    ("Samantha", "Good morning everyone. Let's start with the budget review for the third quarter."),
    ("Daniel", "Thanks. The numbers look fine, but travel costs went up about twelve percent."),
]


# helpers
def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def normalize_audio(src, dst):
    # 16 khz mono 16-bit wav, same settings the pipeline will use
    result = run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                  "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(dst)])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return dst


def make_test_audio():
    # two synthetic voices so diarization has something to separate
    clips = []
    for i, (voice, text) in enumerate(TEST_LINES):
        aiff = OUTPUT_DIR / f"line_{i}.aiff"
        result = run(["say", "-v", voice, "-o", str(aiff), text])
        if result.returncode != 0:
            result = run(["say", "-o", str(aiff), text])
        if result.returncode != 0:
            raise RuntimeError(f"macos say failed: {result.stderr.strip()}")
        clips.append(aiff)

    list_file = OUTPUT_DIR / "concat.txt"
    list_file.write_text("".join(f"file '{c.name}'\n" for c in clips))
    joined = OUTPUT_DIR / "test_joined.aiff"
    result = run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                  "-i", str(list_file), "-c", "copy", str(joined)])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return normalize_audio(joined, OUTPUT_DIR / "test_16k.wav")


def check_ffmpeg():
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg not found on PATH, install with: brew install ffmpeg")
    version = run(["ffmpeg", "-version"]).stdout.splitlines()[0]
    wav = make_test_audio()
    return f"{version.split(' Copyright')[0]}, wrote {wav}"


def check_token(offline):
    token = hf.token()
    if token:
        if offline:
            return "token set (not needed in offline mode)"
        from huggingface_hub import whoami
        user = whoami(token=token)["name"]
        return f"token valid for user {user}"
    if offline:
        return "token not set (fine in offline mode)"
    raise RuntimeError("no Hugging Face token, add one in the app's setup or Settings, or HF_TOKEN in .env")


def check_mlx_whisper(wav, offline):
    import mlx_whisper
    from huggingface_hub import snapshot_download

    snapshot_download(MLX_WHISPER_REPO, local_files_only=offline)
    start = time.perf_counter()
    result = mlx_whisper.transcribe(str(wav), path_or_hf_repo=MLX_WHISPER_REPO,
                                    word_timestamps=True, language="en")
    elapsed = time.perf_counter() - start
    words = sum(len(s.get("words", [])) for s in result["segments"])
    text = result["text"].strip()
    return f"{elapsed:.1f}s, {words} words, text: {text[:70]}"


def check_faster_whisper(wav, offline):
    from faster_whisper import WhisperModel

    # ctranslate2 has no metal backend, so cpu int8 is the best option on a mac
    model = WhisperModel(FASTER_WHISPER_MODEL, device="cpu", compute_type="int8",
                         local_files_only=offline)
    start = time.perf_counter()
    segments, _ = model.transcribe(str(wav), word_timestamps=True, language="en")
    segments = list(segments)
    elapsed = time.perf_counter() - start
    words = sum(len(s.words or []) for s in segments)
    text = " ".join(s.text.strip() for s in segments)
    return f"{elapsed:.1f}s, {words} words, text: {text[:70]}"


def check_diarization(wav):
    import soundfile as sf
    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(DIARIZATION_REPO, token=hf.token())
    if pipeline is None:
        raise RuntimeError(f"could not load {DIARIZATION_REPO}, accept its terms at "
                           f"https://hf.co/{DIARIZATION_REPO} and check the token")
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    pipeline.to(torch.device(device))

    # pass the waveform in memory so decoding does not depend on torchcodec
    samples, rate = sf.read(str(wav), dtype="float32")
    audio = {"waveform": torch.from_numpy(samples).unsqueeze(0), "sample_rate": rate}
    start = time.perf_counter()
    output = pipeline(audio)
    elapsed = time.perf_counter() - start

    speakers = output.speaker_diarization.labels()
    turns = len(list(output.speaker_diarization.itertracks()))
    emb_shape = getattr(output, "speaker_embeddings", None)
    emb_shape = None if emb_shape is None else tuple(emb_shape.shape)
    return f"{elapsed:.1f}s on {device}, {len(speakers)} speakers, {turns} turns, embeddings {emb_shape}"


# main
def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bench", action="store_true", help="also check faster-whisper")
    parser.add_argument("--offline", action="store_true", help="load models from cache only")
    args = parser.parse_args()
    paths.migrate()

    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    import pandas as pd

    rows = []

    def step(name, fn, *fn_args):
        print(f"checking {name} ...", flush=True)
        try:
            detail = fn(*fn_args)
            rows.append({"check": name, "status": "ok", "detail": detail})
        except Exception as exc:
            rows.append({"check": name, "status": "FAIL", "detail": str(exc).splitlines()[0][:200]})

    step("ffmpeg", check_ffmpeg)
    step("hf token", check_token, args.offline)
    wav = OUTPUT_DIR / "test_16k.wav"
    if wav.exists():
        step("mlx-whisper", check_mlx_whisper, wav, args.offline)
        if args.bench:
            step("faster-whisper", check_faster_whisper, wav, args.offline)
        step("pyannote diarization", check_diarization, wav)

    results = pd.DataFrame(rows)
    print()
    with pd.option_context("display.max_colwidth", 120, "display.width", 200):
        print(results.to_string(index=False))
    failed = (results["status"] != "ok").any()
    print()
    if failed:
        print("some checks failed, see details above")
    elif not args.offline:
        print("all checks passed, models are cached. run again with --offline to confirm, "
              "then uncomment HF_HUB_OFFLINE=1 in .env")
    else:
        print("all checks passed from cache, offline mode is working")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
