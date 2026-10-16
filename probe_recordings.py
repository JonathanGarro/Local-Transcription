"""run normalize, diarize, and transcribe on sample recordings and report timings.

usage:
    uv run probe_recordings.py                    # every audio file in data/
    uv run probe_recordings.py "data/file.mp3"    # specific files
    uv run probe_recordings.py --bench            # also time faster-whisper
"""

import argparse
import difflib
import json
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from local_transcription import asr, audio, diarize, merge

# config
BASE_DIR = Path(__file__).parent
# values already set in the shell take precedence over .env
load_dotenv(BASE_DIR / ".env")
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "outputs" / "probe"
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav"}


# helpers
def timed(fn, *args, **kwargs):
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, time.perf_counter() - start


def normalize_text(words):
    return [re.sub(r"[^\w']", "", w.lower()) for w in words["word"] if w.strip()]


def agreement(a, b):
    # rough word-level similarity between two transcripts, 1.0 means identical
    return difflib.SequenceMatcher(None, normalize_text(a), normalize_text(b), autojunk=False).ratio()


def fmt_time(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def write_preview(segments, path, title):
    lines = [f"# {title}", "", "speaker labels are raw diarization ids, not names yet", ""]
    for row in segments.itertuples():
        lines.append(f"**{row.speaker}** [{fmt_time(row.start)}] {row.text}")
        lines.append("")
    path.write_text("\n".join(lines))


def probe_file(src, bench):
    out_dir = OUTPUT_DIR / src.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    wav = out_dir / "audio_16k.wav"
    stats = {"file": src.name}

    print(f"\n{src.name}")
    print("  normalizing ...", flush=True)
    info = audio.normalize(src, wav)
    stats["minutes"] = round(info["duration"] / 60, 1)
    stats["source"] = f"{info['sample_rate']} Hz, {info['channels']} ch"
    stats["channels"] = info["channel_handling"]

    print("  diarizing ...", flush=True)
    (turns, exclusive, embeddings), elapsed = timed(diarize.diarize, wav)
    stats["diarize_s"] = round(elapsed, 1)
    stats["speakers"] = len(embeddings)
    turns.to_csv(out_dir / "turns.csv", index=False)
    exclusive.to_csv(out_dir / "turns_exclusive.csv", index=False)
    np.savez(out_dir / "speaker_embeddings.npz", **embeddings)
    regions = diarize.speech_regions(turns)
    stats["speech_min"] = round(sum(e - s for s, e in regions) / 60, 1)

    print("  transcribing with mlx-whisper ...", flush=True)
    (words, language), elapsed = timed(asr.transcribe_mlx, wav, regions=regions)
    stats["language"] = language
    stats["mlx_s"] = round(elapsed, 1)
    stats["mlx_x_realtime"] = round(info["duration"] / elapsed, 1)
    stats["words"] = len(words)
    words.to_csv(out_dir / "words_mlx.csv", index=False)

    if bench:
        print("  transcribing with faster-whisper (cpu) ...", flush=True)
        (fw_words, _), elapsed = timed(asr.transcribe_faster, wav, regions=regions)
        stats["faster_s"] = round(elapsed, 1)
        stats["faster_x_realtime"] = round(info["duration"] / elapsed, 1)
        stats["agreement"] = round(agreement(words, fw_words), 3)
        fw_words.to_csv(out_dir / "words_faster.csv", index=False)

    labeled = merge.smooth_islands(merge.assign_words(words, exclusive))
    segments = merge.words_to_segments(labeled)
    (out_dir / "segments.json").write_text(json.dumps(segments.to_dict("records"), indent=2))
    write_preview(segments, out_dir / "preview.md", src.stem)

    talk = exclusive.assign(seconds=exclusive["end"] - exclusive["start"])
    talk = talk.groupby("speaker").agg(turns=("seconds", "size"), seconds=("seconds", "sum"))
    talk["share"] = (talk["seconds"] / talk["seconds"].sum()).round(3)
    talk["minutes"] = (talk["seconds"] / 60).round(1)
    talk = talk.drop(columns="seconds").sort_values("share", ascending=False)
    print(talk.to_string())
    return stats


# main
def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--bench", action="store_true", help="also time faster-whisper")
    args = parser.parse_args()

    files = args.files or sorted(p for p in DATA_DIR.iterdir() if p.suffix.lower() in AUDIO_EXTENSIONS)
    rows = [probe_file(f, args.bench) for f in files]

    summary = pd.DataFrame(rows)
    summary.to_csv(OUTPUT_DIR / "summary.csv", index=False)
    print()
    with pd.option_context("display.width", 250, "display.max_columns", 20):
        print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
