"""import meeting notes already in obsidian, from before this app.

each note in MEETING_NOTES_DIR becomes an archived meeting with no recording. the note stays
in the vault and is read from there, so obsidian keeps the only copy. the people and project
in its frontmatter are linked to the library, so the people page counts those meetings too.
notes the app sent itself are skipped, and running it again only adds notes new since.
"""

import re
from pathlib import Path

import yaml

from local_transcription import library, pipeline

# config
# '2025-08-01 - Quarterly Budget Check-In'
NAME_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})[\s-]*(.*)$")


# helpers
def unlink(value):
    """'[[Sam Okafor]]' or '[[Sam Okafor|Sam]]' to 'Sam Okafor'."""
    return re.sub(r"^\[\[(.*?)(\|.*)?\]\]$", r"\1", str(value).strip())


def frontmatter(text):
    """the note's frontmatter as a dict, empty if it has none or it does not parse."""
    if not text.startswith("---"):
        return {}
    closing = text.find("\n---", 3)
    if closing < 0:
        return {}
    try:
        meta = yaml.safe_load(text[3:closing])
    except yaml.YAMLError:
        return {}
    return meta if isinstance(meta, dict) else {}


def _first(value):
    # obsidian may store a single property as a list
    return value[0] if isinstance(value, list) and value else value


# main
def import_notes(folder=None):
    """add every note not yet in the library. returns (imported meeting ids, skipped file
    names, {name: count} for people the library does not know)."""
    folder = Path(folder) if folder else pipeline.notes_dir()
    if not folder or not folder.is_dir():
        raise ValueError("set MEETING_NOTES_DIR in .env to your obsidian meeting notes folder")
    meetings = library.meetings()
    known_ids = set(meetings.get("meeting_id", []))
    known_paths = set(meetings["sent_path"].dropna()) if "sent_path" in meetings else set()
    imported, skipped, unknown = [], [], {}
    for path in sorted(folder.glob("*.md")):
        if path.stem in known_ids or str(path) in known_paths:
            continue
        meta = frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        found = NAME_PATTERN.match(path.stem)
        date = str(meta.get("date") or (found.group(1) if found else ""))
        if not date:
            skipped.append(path.name)
            continue
        title = found.group(2).strip() if found and found.group(2).strip() else path.stem
        person_ids = []
        for name in filter(None, (unlink(n) for n in meta.get("people") or [] if n)):
            person_id = library.find_person(name)
            if person_id:
                person_ids.append(person_id)
            else:
                unknown[name] = unknown.get(name, 0) + 1
        library.save_imported_meeting(path.stem, title, date, path, person_ids)
        project = _first(meta.get("project"))
        if project:
            library.set_meeting_project(path.stem, unlink(project))
        imported.append(path.stem)
    return imported, skipped, unknown
