"""confirm speaker names for a processed recording and add them to voice profiles.

usage:
    uv run confirm.py "outputs/<recording>"                          # use the name column in review.csv
    uv run confirm.py "outputs/<recording>" SPEAKER_01=Alex SPEAKER_00=Priya
    uv run confirm.py "outputs/<recording>" SPEAKER_01=Alex --no-enroll
"""

import argparse
from pathlib import Path

import pandas as pd

from local_transcription import pipeline

# config
BASE_DIR = Path(__file__).parent


# helpers
def parse_pairs(pairs):
    mapping = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"expected SPEAKER_XX=Name, got {pair!r}")
        speaker, name = pair.split("=", 1)
        mapping[speaker.strip()] = name.strip()
    return mapping


def mapping_from_review(out_dir):
    path = out_dir / "review.csv"
    if not path.exists():
        raise SystemExit(f"no review.csv in {out_dir}, pass SPEAKER_XX=Name pairs instead")
    review = pd.read_csv(path, dtype={"name": str}).fillna({"name": ""})
    review = review[review["name"].str.strip() != ""]
    return dict(zip(review["speaker"], review["name"].str.strip()))


# main
def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("pairs", nargs="*", help="SPEAKER_XX=Name")
    parser.add_argument("--no-enroll", action="store_true",
                        help="rename in the transcript only, do not touch profiles")
    args = parser.parse_args()

    mapping = parse_pairs(args.pairs) if args.pairs else mapping_from_review(args.output_dir)
    if not mapping:
        raise SystemExit("no names to confirm")

    speakers, added = pipeline.apply_names(args.output_dir, mapping, enroll=not args.no_enroll)
    print(speakers[["speaker", "name", "status", "minutes"]].fillna({"name": ""}).to_string(index=False))
    for name, entry_id in added:
        print(f"added entry {entry_id} to profile {name}")
    print(f"updated {(args.output_dir / 'transcript.md')}")


if __name__ == "__main__":
    main()
