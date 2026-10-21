"""transcribe a recording and label speakers using enrolled voice profiles.

usage:
    uv run scripts/transcribe.py "data/2026-10-14 - Manager Check-In.mp3"
    uv run scripts/transcribe.py "data/call.mp3" --source zoom --num-speakers 5
    uv run scripts/transcribe.py "data/call.mp3" --split off     # treat a stereo file as one mix
"""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

# config
# the project folder, one up from scripts/
BASE_DIR = Path(__file__).resolve().parent.parent
# values already set in the shell take precedence over .env
load_dotenv(BASE_DIR / ".env")
CHANNELS = {"left": 0, "right": 1}

from local_transcription import diarize, match, pipeline  # noqa: E402


# main
def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("audio", type=Path)
    parser.add_argument("--threshold", type=float, default=match.THRESHOLD,
                        help=f"minimum cosine similarity to assign a name (default {match.THRESHOLD})")
    parser.add_argument("--num-speakers", type=int, help="exact speaker count, if known")
    parser.add_argument("--min-speakers", type=int, default=diarize.MIN_SPEAKERS)
    parser.add_argument("--max-speakers", type=int, default=diarize.MAX_SPEAKERS)
    parser.add_argument("--title", help="override the title taken from the file name")
    parser.add_argument("--source", default="other", choices=["plaud", "zoom", "other"],
                        help="recording source, stored with any profile entries this run adds")
    parser.add_argument("--language", help="force a language code such as en")
    parser.add_argument("--split", default="auto", choices=["auto", "on", "off"],
                        help="split mode for stereo files with your mic on one channel (default auto)")
    parser.add_argument("--mic-channel", default=os.environ.get("MIC_CHANNEL", "left"),
                        choices=sorted(CHANNELS), help="which channel is your mic in split mode")
    parser.add_argument("--owner", default=os.environ.get("MIC_OWNER"),
                        help="name for the mic channel in split mode (default MIC_OWNER in .env)")
    args = parser.parse_args()

    out_dir, speakers, review = pipeline.run(
        args.audio, threshold=args.threshold, num_speakers=args.num_speakers,
        min_speakers=args.min_speakers, max_speakers=args.max_speakers, title=args.title,
        source_type=args.source, language=args.language, split=args.split,
        mic_channel=CHANNELS[args.mic_channel], owner=args.owner)

    print()
    table = speakers[["speaker", "name", "status", "score", "best_name", "best_score", "minutes"]]
    print(table.fillna({"name": "", "best_name": ""}).to_string(index=False, na_rep=""))
    print()
    print(f"transcript: {(out_dir / 'transcript.md').relative_to(BASE_DIR)}")
    if review:
        print(f"review:     {review.relative_to(BASE_DIR)}")
        print("fill in the name column, then run:")
        print(f'    uv run scripts/confirm.py "{out_dir.relative_to(BASE_DIR)}"')


if __name__ == "__main__":
    main()
