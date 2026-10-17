"""reprocess meetings imported from obsidian using their plaud recordings, so their voices
can be confirmed and added to profiles. needs the plaud cli, logged in.

usage:
    uv run import_plaud.py "2026-10-13 - Customer Dashboard Review with Maya"
    uv run import_plaud.py --next 5      # the newest imported meetings not yet processed
    uv run import_plaud.py --hours 4     # keep going through the newest until 4 hours have passed

each meeting then needs review in the app, where names from its note are suggested first.
"""

import argparse
import time
from pathlib import Path

from dotenv import load_dotenv

# config
BASE_DIR = Path(__file__).parent
# values already set in the shell take precedence over .env
load_dotenv(BASE_DIR / ".env")

from local_transcription import library, pipeline, plaud  # noqa: E402


# helpers
def waiting(count=None):
    """the newest imported meetings with a plaud link and no recording processed yet, all of
    them when count is None."""
    meetings = library.meetings().query("source_type == 'obsidian'")
    picked = []
    for row in meetings.itertuples():
        if count is not None and len(picked) >= count:
            break
        if not Path(row.sent_path).exists() or (pipeline.OUTPUT_DIR / row.meeting_id / "run.json").exists():
            continue
        if plaud.plaud_link(row.sent_path):
            picked.append(row.meeting_id)
    return picked


# main
def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("meetings", nargs="*", help="meeting ids, the note names without .md")
    parser.add_argument("--next", type=int, help="process this many of the newest waiting meetings")
    parser.add_argument("--hours", type=float,
                        help="work through the newest waiting meetings, starting no new one after this long")
    args = parser.parse_args()

    meeting_ids = args.meetings + (waiting(args.next) if args.next else waiting() if args.hours else [])
    if not meeting_ids:
        parser.error("name some meetings or use --next or --hours")
    batch_started = time.time()
    for i, meeting_id in enumerate(meeting_ids):
        if args.hours and time.time() - batch_started > args.hours * 3600:
            print(f"\n{args.hours:g} hours are up after {i} meetings, run again for the next batch")
            break
        print(f"\n{meeting_id}")
        started = time.time()
        try:
            _, suggested = plaud.reprocess(meeting_id, progress=lambda m: print(f"  {m.strip()}"))
        except Exception as err:
            print(f"  failed: {err}")
            continue
        names = ", ".join(f"{s} = {v['name']} ({v['share']:.0%})" for s, v in sorted(suggested.items()))
        print(f"  done in {(time.time() - started) / 60:.1f} min, the note suggests: {names or 'no names'}")


if __name__ == "__main__":
    main()
