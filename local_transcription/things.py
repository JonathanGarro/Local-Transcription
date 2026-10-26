"""to-dos into things 3, through its applescript dictionary.

projects are read from the areas chosen in settings, else those named in THINGS_AREAS in
.env, else every area, and to-dos are made in a project or in the inbox. applescript leaves things in the background, where its url scheme would bring
it to the front. the first use asks for permission to control things.
"""

import os
import subprocess
import time

from local_transcription import settings

# config
# projects are read again after this long, so new ones show up
PROJECTS_TTL_S = 60
# where projects outside any area are grouped
NO_AREA = "No Area"

# argv: the areas to read, or none for every open project
PROJECTS_SCRIPT = """
on run argv
  set out to ""
  tell application "Things3"
    repeat with p in projects
      set areaName to ""
      try
        set areaName to name of area of p
      end try
      if status of p is open and ((count of argv) is 0 or argv contains areaName) then set out to out & areaName & tab & (name of p) & tab & (id of p) & linefeed
    end repeat
  end tell
  return out
end run
"""

AREAS_SCRIPT = """
set out to ""
tell application "Things3"
  repeat with a in areas
    set out to out & (name of a) & linefeed
  end repeat
end tell
return out
"""

# argv: title, notes, project id or "", when ("", "today", "tomorrow", or YYYY-MM-DD), deadline (YYYY-MM-DD or "")
ADD_SCRIPT = """
on isoDate(s)
  set d to current date
  set day of d to 1
  set year of d to (text 1 thru 4 of s) as integer
  set month of d to (text 6 thru 7 of s) as integer
  set day of d to (text 9 thru 10 of s) as integer
  set time of d to 0
  return d
end isoDate

on run argv
  set {theTitle, theNotes, projectId, theWhen, theDeadline} to argv
  tell application "Things3"
    set t to make new to do with properties {name:theTitle, notes:theNotes}
    if projectId is not "" then set project of t to project id projectId
    if theWhen is "today" then
      schedule t for (current date)
    else if theWhen is "tomorrow" then
      schedule t for ((current date) + 1 * days)
    else if theWhen is not "" then
      schedule t for my isoDate(theWhen)
    end if
    if theDeadline is not "" then set due date of t to my isoDate(theDeadline)
    return id of t
  end tell
end run
"""

_cache = {"at": 0, "projects": None}


# helpers
def _osascript(script, *args):
    result = subprocess.run(["osascript", "-", *args], input=script, capture_output=True, text=True)
    if result.returncode != 0:
        message = result.stderr.strip()
        if "-1743" in message:
            message = "Wemmick isn't allowed to control Things. Allow it in System Settings, Privacy & Security, Automation"
        raise RuntimeError(message or "things did not answer")
    return result.stdout


def chosen_areas():
    """the areas projects are read from, from settings, else THINGS_AREAS in .env. an empty
    list is every area."""
    chosen = settings.load()["things_areas"]
    if chosen is not None:
        return chosen
    return [a.strip() for a in os.environ.get("THINGS_AREAS", "").split(",") if a.strip()]


# main
def all_areas():
    """every area in things, in its order, for choosing them in settings."""
    return [line for line in _osascript(AREAS_SCRIPT).splitlines() if line.strip()]


def projects():
    """open projects in the chosen areas, as dicts of id, name, and area, in area order: the
    order they were chosen in, else a to z with projects outside any area last."""
    if _cache["projects"] is None or time.time() - _cache["at"] > PROJECTS_TTL_S:
        chosen = chosen_areas()
        rows = []
        for line in _osascript(PROJECTS_SCRIPT, *chosen).splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                rows.append({"area": parts[0] or NO_AREA, "name": parts[1], "id": parts[2]})
        order = {a: i for i, a in enumerate(chosen)}
        _cache.update(at=time.time(), projects=sorted(rows, key=lambda r: (
            order.get(r["area"], len(order)), r["area"] == NO_AREA, r["area"].lower(), r["name"].lower())))
    return _cache["projects"]


def areas():
    """the areas the projects are in, in the order they're listed."""
    return list(dict.fromkeys(p["area"] for p in projects()))


def add_todo(title, notes="", project_id="", when="", deadline=""):
    """make one to-do. returns its things id."""
    return _osascript(ADD_SCRIPT, title, notes or "", project_id or "", when or "", deadline or "").strip()


def clear_cache():
    _cache["projects"] = None


def link(todo_id):
    return f"things:///show?id={todo_id}"
