"""import meeting notes already in obsidian into the app's archive, without recordings.

usage:
    uv run import_obsidian.py                       # the MEETING_NOTES_DIR folder from .env
    uv run import_obsidian.py "/path/to/Meeting Notes"

safe to run again, notes already in the library are left alone.
"""

import argparse
from pathlib import Path

from dotenv import load_dotenv

# config
BASE_DIR = Path(__file__).parent
# values already set in the shell take precedence over .env
load_dotenv(BASE_DIR / ".env")

from local_transcription import obsidian  # noqa: E402


# main
def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path, nargs="?", help="default MEETING_NOTES_DIR in .env")
    args = parser.parse_args()

    imported, skipped, unknown = obsidian.import_notes(args.folder)
    print(f"imported {len(imported)} notes")
    if skipped:
        print(f"skipped {len(skipped)} with no date: {', '.join(skipped)}")
    if unknown:
        names = ", ".join(f"{name} ({count})" for name, count in sorted(unknown.items()))
        print(f"not in the library, so not linked: {names}")


if __name__ == "__main__":
    main()
