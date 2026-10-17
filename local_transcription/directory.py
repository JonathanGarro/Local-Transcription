"""people's teams and organizations from the obsidian people folder, one note per person.

PEOPLE_DIR in .env points at the folder. obsidian is the source of truth: a person's teams
and organizations are read from their note whenever a note or the people page needs them,
and saving them in the app writes them back into that note's frontmatter, leaving every
other line alone. the team and organization pickers list the notes in the Teams and
Organizations folders next to it.
"""

import json
import os
import re
from pathlib import Path

import yaml

# config
KEYS = ("teams", "organizations")
LOOKUP_FOLDERS = {"teams": "Teams", "organizations": "Organizations"}
# the layout of the vault's Person template, for someone saved before they have a note
NEW_NOTE = "---\nnotes:\nteams:\norganizations:\n---\n# {name}\n## Context\n\n## Key facts\n"


# helpers
def people_dir():
    folder = os.environ.get("PEOPLE_DIR")
    return Path(folder).expanduser() if folder else None


def _frontmatter(text):
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


def _names(value):
    """'[[Northwind Foundation]]', a list of them, or nothing, as plain names."""
    values = value if isinstance(value, list) else [value]
    names = (re.sub(r"^\[\[(.*?)(\|.*)?\]\]$", r"\1", str(v).strip()) for v in values if v)
    return list(dict.fromkeys(n for n in names if n))


def _index():
    """{lowercase name: {"teams", "organizations"}} for every note in the people folder."""
    folder = people_dir()
    if not folder or not folder.is_dir():
        return {}
    found = {}
    for path in folder.glob("*.md"):
        meta = _frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        found[path.stem.lower()] = {key: _names(meta.get(key)) for key in KEYS}
    return found


def _set_list(text, key, values):
    """the note with one list property replaced, written the way the people notes store it."""
    block = [f"{key}:"] + [f"  - {json.dumps(f'[[{v}]]', ensure_ascii=False)}" for v in values]
    if not text.startswith("---\n") or text.find("\n---", 3) < 0:
        return "---\n" + "\n".join(block) + "\n---\n" + text
    closing = text.find("\n---", 3)
    lines = text[4:closing].split("\n") if closing > 3 else []
    start = next((i for i, line in enumerate(lines) if re.match(rf"^{re.escape(key)}\s*:", line)), None)
    if start is None:
        lines += block
    else:
        # the key's own line plus its indented or '- ' list items
        end = start + 1
        while end < len(lines) and re.match(r"^(\s+|-)", lines[end]):
            end += 1
        lines[start:end] = block
    return "---\n" + "\n".join(lines) + text[closing:]


# main
def details(name, index=None):
    """{"teams", "organizations"} from a person's note, empty lists if they have none."""
    index = _index() if index is None else index
    return index.get(name.lower(), {key: [] for key in KEYS})


def all_details():
    """the whole people folder at once, for pages that list everyone. pass to details()."""
    return _index()


def save(name, teams=None, organizations=None):
    """write teams and organizations into a person's note. None leaves that one as it is."""
    folder = people_dir()
    if not folder or not folder.is_dir():
        raise ValueError("set PEOPLE_DIR in .env to your obsidian people folder")
    if "/" in name:
        raise ValueError(f"{name} can't be a note name")
    path = next((p for p in folder.glob("*.md") if p.stem.lower() == name.lower()), folder / f"{name}.md")
    text = path.read_text(encoding="utf-8") if path.exists() else NEW_NOTE.format(name=name)
    for key, values in (("teams", teams), ("organizations", organizations)):
        if values is not None:
            text = _set_list(text, key, list(dict.fromkeys(v.strip() for v in values if v.strip())))
    path.write_text(text, encoding="utf-8")


def lookup_names(kind):
    """every team or organization with a note in the vault or on someone's note, sorted."""
    if kind not in KEYS:
        raise ValueError(f"no lookup {kind}")
    folder = people_dir()
    found = {}
    lookup_dir = folder.parent / LOOKUP_FOLDERS[kind] if folder else None
    if lookup_dir and lookup_dir.is_dir():
        found.update({p.stem.lower(): p.stem for p in lookup_dir.glob("*.md")})
    for person in _index().values():
        for name in person[kind]:
            found.setdefault(name.lower(), name)
    return sorted(found.values(), key=str.lower)
