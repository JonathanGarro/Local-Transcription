"""the to-do app chosen in settings: things 3, todoist, or apple reminders, one at a time.

each app's module has the same few functions: projects() and areas() for the project menu,
add_todo() to make one, and link() to open one. this picks the module and says what to call
the app and where a to-do with no project goes.
"""

from local_transcription import reminders, settings, things, todoist

# config
APPS = {
    "things": {"module": things, "name": "Things", "inbox": "Inbox"},
    "todoist": {"module": todoist, "name": "Todoist", "inbox": "Inbox"},
    "reminders": {"module": reminders, "name": "Reminders", "inbox": "Default list"},
}


# main
def current():
    """the chosen app's key, or None when to-dos are off."""
    app = settings.load()["todo_app"]
    return app if app in APPS else None


def app(key=None):
    """the chosen app's module. raises when to-dos are off."""
    key = key or current()
    if key not in APPS:
        raise RuntimeError("no to-do app is chosen, pick one in Settings")
    return APPS[key]["module"]


def name(key=None):
    key = key or current()
    return APPS[key]["name"] if key in APPS else "your to-do app"


def inbox(key=None):
    key = key or current()
    return APPS[key]["inbox"] if key in APPS else "Inbox"


def link(todo_id, key=None):
    """the link that opens a to-do, in the app it was sent to. to-dos from before there was a
    choice went to things."""
    return APPS.get(key or "things", APPS["things"])["module"].link(todo_id)
