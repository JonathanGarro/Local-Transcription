"""enroll a voice sample: extract an embedding and add it to that person's profile.

usage:
    uv run enroll.py "data/priya_sample.wav" "Priya"
    uv run enroll.py "data/meeting.mp3" "Priya" --start 5:20 --end 6:10 --source plaud
"""

import argparse
from pathlib import Path

from dotenv import load_dotenv

# config
BASE_DIR = Path(__file__).parent
# values already set in the shell take precedence over .env
load_dotenv(BASE_DIR / ".env")
OUTPUT_DIR = BASE_DIR / "outputs" / "enroll"
RECOMMENDED_S = 20

from local_transcription import audio, embed, profiles  # noqa: E402


# helpers
def parse_time(text):
    # accepts seconds, mm:ss, or hh:mm:ss
    if text is None:
        return None
    seconds = 0.0
    for part in text.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


# main
def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("audio", type=Path)
    parser.add_argument("name")
    parser.add_argument("--start", help="clip start, such as 5:20")
    parser.add_argument("--end", help="clip end, such as 6:10")
    parser.add_argument("--source", default="other", choices=["plaud", "zoom", "other"])
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    wav = OUTPUT_DIR / f"{args.audio.stem}_16k.wav"
    audio.normalize(args.audio, wav)
    start, end = parse_time(args.start), parse_time(args.end)

    vector, seconds = embed.Embedder().clip(embed.load_audio(wav), start, end)
    if vector is None:
        raise SystemExit("clip is too short to embed, use at least a few seconds of speech")
    entry_id = profiles.add_entry(args.name, vector, method="enroll", source_file=args.audio.name,
                                  source_type=args.source, start=start, end=end, seconds=seconds)
    print(f"added entry {entry_id} to profile {args.name} ({seconds:.1f}s of audio)")
    if seconds < RECOMMENDED_S:
        print(f"note: clips of {RECOMMENDED_S}s or more give more reliable matches")
    print("the clip should contain only this person's voice")


if __name__ == "__main__":
    main()
