"""project names from an obsidian project index, one note per project. optional: without one,
the project picker offers the projects typed into the app.

the folder is chosen in settings, or PROJECT_INDEX_DIR in .env. only the status and "things
project" lines of each note's frontmatter are read, the rest of the note is never opened by
the app. projects with an active status are listed first.
"""

import re

from local_transcription import library, settings

# config
# the property a project's status is read from, the first of these a note has
STATUS_KEYS = ("project status", "status")
# statuses listed first, in any case
ACTIVE_STATUSES = {"in-progress", "in progress", "active", "current"}
# a charter's link to its things 3 project, such as things:///show?id=5rg4Dyv6CNpxh1jf3QbqET
THINGS_KEY = "things project"
THINGS_ID = re.compile(r"[?&]id=([A-Za-z0-9]+)")


# helpers
def index_dir():
    return settings.folder("project_index_dir")[0]


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
    return next((value for key in STATUS_KEYS if (value := _field(path, key))), None)


def _active(project):
    return (project["status"] or "").strip().lower() in ACTIVE_STATUSES


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
    """index projects plus any typed into the app, active ones first, then a to z."""
    found = {p["name"].lower(): p for p in index_projects()}
    for name in library.lookup_names("projects"):
        found.setdefault(name.lower(), {"name": name, "status": None})
    return sorted(({**p, "active": _active(p)} for p in found.values()), key=lambda p: (not p["active"], p["name"].lower()))

