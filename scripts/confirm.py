"""confirm speaker names for a processed recording and add them to voice profiles.

usage:
    uv run scripts/confirm.py "<meeting>"                          # use the name column in review.csv
    uv run scripts/confirm.py "<meeting>" SPEAKER_01=Alex SPEAKER_00=Priya
    uv run scripts/confirm.py "<meeting>" SPEAKER_01=Alex --no-enroll
    uv run scripts/confirm.py "<meeting>" --skip SPEAKER_00      # leave a speaker unnamed
"""

import argparse
from pathlib import Path

import pandas as pd

from local_transcription import paths, pipeline

# config
# the project folder, one up from scripts/
BASE_DIR = Path(__file__).resolve().parent.parent


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
    parser.add_argument("output_dir", type=Path, help="the meeting's folder, or just its name")
    parser.add_argument("pairs", nargs="*", help="SPEAKER_XX=Name")
    parser.add_argument("--no-enroll", action="store_true",
                        help="rename in the transcript only, do not touch profiles")
    parser.add_argument("--skip", nargs="+", default=[], metavar="SPEAKER",
                        help="leave these speakers unnamed so the meeting no longer needs review")
    args = parser.parse_args()
    paths.migrate()
    args.output_dir = paths.meeting_folder(args.output_dir)

    if args.pairs:
        mapping = parse_pairs(args.pairs)
    else:
        mapping = {} if args.skip else mapping_from_review(args.output_dir)
    if not mapping and not args.skip:
        raise SystemExit("no names to confirm")

    speakers, added = pipeline.apply_names(args.output_dir, mapping, enroll=not args.no_enroll,
                                           skip=args.skip)
    print(speakers[["speaker", "name", "status", "minutes"]].fillna({"name": ""}).to_string(index=False))
    for name, entry_id in added:
        print(f"added entry {entry_id} to profile {name}")
    print(f"updated {(args.output_dir / 'transcript.md')}")


if __name__ == "__main__":
    main()
