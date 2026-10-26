"""to-dos into apple reminders, through its applescript dictionary.

lists stand in for projects, grouped by the account they're in, and to-dos with no list go
in the default list. reminders has a due date but no start date: the deadline is the due
date, and when there's none the to-do's when is. a to-do with both is reminded at 9 on its
when day. the first use asks for permission to control reminders.
"""

import subprocess
import time
import urllib.parse

# config
PROJECTS_TTL_S = 60
# a reminder's id from applescript is x-apple-reminder://<uuid>, and this opens it
SHOW_URL = "x-apple-reminderkit://REMCDReminder/{}"
REMIND_HOUR = 9

PROJECTS_SCRIPT = """
tell application "Reminders"
  set out to ""
  repeat with l in lists
    set acct to ""
    try
      set acct to name of container of l
    end try
    set out to out & acct & tab & (name of l) & tab & (id of l) & linefeed
  end repeat
  return out
end tell
"""

# argv: title, notes, list id or "", when ("", "today", "tomorrow", or YYYY-MM-DD), deadline (YYYY-MM-DD or ""), remind hour
ADD_SCRIPT = """
on dayOf(s)
  set d to current date
  if s is "tomorrow" then set d to d + 1 * days
  if s is not "today" and s is not "tomorrow" then
    set day of d to 1
    set year of d to (text 1 thru 4 of s) as integer
    set month of d to (text 6 thru 7 of s) as integer
    set day of d to (text 9 thru 10 of s) as integer
  end if
  set time of d to 0
  return d
end dayOf

on run argv
  set {theTitle, theNotes, listId, theWhen, theDeadline, remindHour} to argv
  tell application "Reminders"
    if listId is "" then
      set theList to default list
    else
      set theList to list id listId
    end if
    set r to make new reminder at end of theList with properties {name:theTitle, body:theNotes}
    if theDeadline is not "" then
      set allday due date of r to my dayOf(theDeadline)
      if theWhen is not "" then set remind me date of r to (my dayOf(theWhen)) + (remindHour as integer) * hours
    else if theWhen is not "" then
      set allday due date of r to my dayOf(theWhen)
    end if
    return id of r
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
            message = "Heron isn't allowed to control Reminders. Allow it in System Settings, Privacy & Security, Automation"
        raise RuntimeError(message or "reminders did not answer")
    return result.stdout


# main
def projects():
    """reminders lists, as dicts of id, name, and area, where the area is the account."""
    if _cache["projects"] is None or time.time() - _cache["at"] > PROJECTS_TTL_S:
        rows = []
        for line in _osascript(PROJECTS_SCRIPT).splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                rows.append({"area": parts[0] or "Lists", "name": parts[1], "id": parts[2]})
        _cache.update(at=time.time(), projects=rows)
    return _cache["projects"]


def areas():
    found = []
    for p in projects():
        if p["area"] not in found:
            found.append(p["area"])
    return found


def add_todo(title, notes="", project_id="", when="", deadline=""):
    """make one reminder. returns its id."""
    return _osascript(ADD_SCRIPT, title, notes or "", project_id or "", when or "", deadline or "",
                      str(REMIND_HOUR)).strip()


def link(todo_id):
    uuid = str(todo_id).rsplit("/", 1)[-1]
    return SHOW_URL.format(urllib.parse.quote(uuid))
