"""write an obsidian meeting note (agenda, discussion, transcript, action items) for a
processed recording.

usage:
    uv run scripts/summarize.py "outputs/<recording>"                       # local model, nothing leaves the mac
    uv run scripts/summarize.py "outputs/<recording>" --backend claude      # sends transcript text to the claude api
    uv run scripts/summarize.py "outputs/<recording>" --project "Customer Dashboard"
"""

import argparse
from pathlib import Path

from dotenv import load_dotenv

# config
# the project folder, one up from scripts/
BASE_DIR = Path(__file__).resolve().parent.parent
# values already set in the shell take precedence over .env
load_dotenv(BASE_DIR / ".env")

from local_transcription import notes, pipeline, templates  # noqa: E402


# main
def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--backend", choices=notes.BACKENDS, default=notes.default_backend(),
                        help="local runs on this mac, claude sends the transcript text to anthropic")
    parser.add_argument("--project", help="file the meeting under this project (created if new)")
    parser.add_argument("--owner", help="whose action items to list (default MIC_OWNER in .env)")
    parser.add_argument("--template", choices=templates.names(),
                        help="the note template to write it in (default the one chosen in settings)")
    args = parser.parse_args()

    path = pipeline.write_note(args.output_dir, backend=args.backend, project=args.project, owner=args.owner,
                               template=args.template)
    print(f"note: {path.relative_to(BASE_DIR)}")


if __name__ == "__main__":
    main()
