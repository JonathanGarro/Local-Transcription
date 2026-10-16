"""project names from the obsidian project index, one note per project.

PROJECT_INDEX_DIR in .env points at the folder. only the "project status" line of each
note's frontmatter is read, the rest of the note is never opened by the app.
"""

import os
from pathlib import Path

from local_transcription import library

# config
STATUS_KEY = "project status"
ACTIVE_STATUS = "In-Progress"


# helpers
def index_dir():
    folder = os.environ.get("PROJECT_INDEX_DIR")
    return Path(folder).expanduser() if folder else None


def _status(path):
    """the project status from the frontmatter, or None if it has none."""
    with path.open(encoding="utf-8", errors="replace") as f:
        if f.readline().strip() != "---":
            return None
        for line in f:
            if line.strip() == "---":
                return None
            key, _, value = line.partition(":")
            if key.strip().lower() == STATUS_KEY:
                return value.strip().strip("\"'") or None
    return None


# main
def index_projects():
    """[{name, status}] for every note in the project index. empty if it is not set up."""
    folder = index_dir()
    if not folder or not folder.is_dir():
        return []
    return [{"name": path.stem, "status": _status(path)} for path in folder.glob("*.md")
            if not path.name.startswith(".")]


def all_projects():
    """index projects plus any typed into the app, in-progress first, then a to z."""
    found = {p["name"].lower(): p for p in index_projects()}
    for name in library.lookup_names("projects"):
        found.setdefault(name.lower(), {"name": name, "status": None})
    return sorted(found.values(), key=lambda p: (p["status"] != ACTIVE_STATUS, p["name"].lower()))

