"""to-dos into todoist, through its api with a personal token.

the token is made in todoist's settings, integrations, developer, and kept in the macos
keychain like the anthropic key. TODOIST_API_TOKEN in .env works too. projects are the
user's open projects, grouped under their parent, and to-dos with no project go in the
inbox. todoist has no start date, so a to-do's when becomes its due date and its deadline
todoist's deadline.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from local_transcription import keychain

# config
API_URL = "https://api.todoist.com/api/v1"
TASK_URL = "https://app.todoist.com/app/task/{}"
ACCOUNT = "TODOIST_API_TOKEN"
# where top level projects are grouped in the project menu
TOP_LEVEL = "Projects"
PROJECTS_TTL_S = 60
TIMEOUT_S = 20

_cache = {"at": 0, "projects": None}


# helpers
def _stored():
    return keychain.get(ACCOUNT)


def token():
    """the token in use, from the keychain, else .env, or None."""
    return _stored() or os.environ.get(ACCOUNT, "").strip() or None


def _request(method, path, params=None, body=None):
    key = token()
    if not key:
        raise RuntimeError("no Todoist token, add one in Settings")
    url = f"{API_URL}/{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    request = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            raise RuntimeError("Todoist didn't accept the token. Replace it in Settings") from None
        raise RuntimeError(f"Todoist answered {err.code}: {err.read().decode(errors='replace')[:200]}") from None
    except urllib.error.URLError as err:
        raise RuntimeError(f"couldn't reach Todoist: {err.reason}") from None


def _all_projects():
    rows, cursor = [], None
    while True:
        page = _request("GET", "projects", {"limit": 200, **({"cursor": cursor} if cursor else {})})
        rows += page.get("results", [])
        cursor = page.get("next_cursor")
        if not cursor:
            return rows


# main
def save_token(value):
    value = value.strip()
    if len(value) < 20 or " " in value:
        raise ValueError("that doesn't look like a Todoist API token, which is 40 letters and numbers")
    keychain.put(ACCOUNT, value)
    _cache["projects"] = None


def remove_token():
    keychain.forget(ACCOUNT)
    _cache["projects"] = None


def token_source():
    if _stored():
        return "keychain"
    return "env" if os.environ.get(ACCOUNT, "").strip() else None


def masked_token():
    key = token()
    return "*" * 24 + key[-3:] if key and len(key) > 3 else None


def check():
    """raises when the token doesn't work."""
    _request("GET", "projects", {"limit": 1})


def projects():
    """open projects other than the inbox, as dicts of id, name, and area, where the area is
    the parent project's name, or Projects for those at the top."""
    if _cache["projects"] is None or time.time() - _cache["at"] > PROJECTS_TTL_S:
        found = [p for p in _all_projects() if not p.get("is_archived") and not p.get("is_deleted")]
        names = {p["id"]: p["name"] for p in found}
        rows = [{"id": p["id"], "name": p["name"], "area": names.get(p.get("parent_id"), TOP_LEVEL)}
                for p in found if not p.get("inbox_project")]
        _cache.update(at=time.time(), projects=rows)
    return _cache["projects"]


def areas():
    found = []
    for p in projects():
        if p["area"] not in found:
            found.append(p["area"])
    return sorted(found, key=lambda a: a != TOP_LEVEL)


def add_todo(title, notes="", project_id="", when="", deadline=""):
    """make one task. when is "", "today", "tomorrow", or YYYY-MM-DD, deadline YYYY-MM-DD or
    "". returns its todoist id."""
    body = {"content": title, "description": notes or ""}
    if project_id:
        body["project_id"] = project_id
    if when in ("today", "tomorrow"):
        body["due_string"] = when
        body["due_lang"] = "en"
    elif when:
        body["due_date"] = when
    if deadline:
        body["deadline_date"] = deadline
    return str(_request("POST", "tasks", body=body)["id"])


def link(todo_id):
    return TASK_URL.format(urllib.parse.quote(str(todo_id)))
