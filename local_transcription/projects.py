"""project names from the obsidian project index, one note per project.

PROJECT_INDEX_DIR in .env points at the folder. only the "project status" and "things
project" lines of each note's frontmatter are read, the rest of the note is never opened by
the app.
"""

import os
import re
from pathlib import Path

from local_transcription import library

# config
STATUS_KEY = "project status"
ACTIVE_STATUS = "In-Progress"
# a charter's link to its things 3 project, such as things:///show?id=5rg4Dyv6CNpxh1jf3QbqET
THINGS_KEY = "things project"
THINGS_ID = re.compile(r"[?&]id=([A-Za-z0-9]+)")


# helpers
def index_dir():
    folder = os.environ.get("PROJECT_INDEX_DIR")
    return Path(folder).expanduser() if folder else None


def _field(path, wanted):
    """one frontmatter value, or None if the note has none."""
    with path.open(encoding="utf-8", errors="replace") as f:
        if f.readline().strip() != "---":
            return None
        for line in f:
            if line.strip() == "---":
                return None
            key, _, value = line.partition(":")
            if key.strip().lower() == wanted:
                return value.strip().strip("\"'") or None
    return None


def _status(path):
    return _field(path, STATUS_KEY)


# main
def index_projects():
    """[{name, status}] for every note in the project index. empty if it is not set up."""
    folder = index_dir()
    if not folder or not folder.is_dir():
        return []
    return [{"name": path.stem, "status": _status(path)} for path in folder.glob("*.md")
            if not path.name.startswith(".")]


def things_project_id(name):
    """the things 3 project id a project's charter links to, or None when the charter is
    missing or has no link."""
    folder = index_dir()
    if not name or not folder or not folder.is_dir():
        return None
    charter = next((p for p in folder.glob("*.md") if p.stem.lower() == name.lower()), None)
    link = _field(charter, THINGS_KEY) if charter else None
    found = THINGS_ID.search(link or "")
    return found.group(1) if found else None


def all_projects():
    """index projects plus any typed into the app, in-progress first, then a to z."""
    found = {p["name"].lower(): p for p in index_projects()}
    for name in library.lookup_names("projects"):
        found.setdefault(name.lower(), {"name": name, "status": None})
    return sorted(found.values(), key=lambda p: (p["status"] != ACTIVE_STATUS, p["name"].lower()))

