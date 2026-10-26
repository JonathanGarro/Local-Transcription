"""where wemmick keeps your library: recordings, meeting folders, the database, settings, note
templates and .env, in ~/Library/Application Support/Wemmick, or wherever WEMMICK_HOME says
(set in the environment, or in the project folder's .env for scripts). inside it, data/ holds the recordings and the rest, and outputs/ a folder per meeting.

older versions kept data/ and outputs/ in the project folder. migrate() moves them over once,
when the server starts, so the code can be updated or moved without touching the library.
the app was called heron, and boswell before that. see OLD_HOMES for their library folders.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# config
PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_HOME = Path("~/Library/Application Support/Wemmick").expanduser()
# the library's folder while the app was called heron, and boswell before that, newest first.
# the mac app renames it when it first opens, once the old app has quit. until then, this
# reads it where it is, and never moves it, since the old app may still be running from it
OLD_HOMES = [Path(f"~/Library/Application Support/{name}").expanduser() for name in ("Heron", "Boswell")]
# WEMMICK_HOME moves the library. HERON_HOME and BOSWELL_HOME are the same setting from before
_chosen_home = os.environ.get("WEMMICK_HOME") or os.environ.get("HERON_HOME") or os.environ.get("BOSWELL_HOME")
if _chosen_home:
    HOME = Path(_chosen_home).expanduser()
elif DEFAULT_HOME.exists():
    HOME = DEFAULT_HOME
else:
    HOME = next((old for old in OLD_HOMES if old.is_dir()), DEFAULT_HOME)
DATA_DIR = HOME / "data"
OUTPUT_DIR = HOME / "outputs"
OLD_DATA_DIR = PROJECT_DIR / "data"
OLD_OUTPUT_DIR = PROJECT_DIR / "outputs"
# what the server prints, written there by the mac app
LOG_FILE = Path.home() / "Library" / "Logs" / "Wemmick.log"
# the app reads .env from the library folder, since it runs its own copy of the code rather
# than this project folder. values already set, as by a script from the project's .env, stay
ENV_FILE = HOME / ".env"
load_dotenv(ENV_FILE)


# helpers
def relative(path):
    """a path inside the library as one relative to it, such as data/<recording>, so it still
    works if the library moves. a path outside it is kept as it is."""
    path = Path(path)
    try:
        return str(path.relative_to(HOME))
    except ValueError:
        return str(path)


def meeting_folder(name):
    """a meeting's folder from a script's argument: a path to it, or just its name, such as
    "2026-10-13 - Dashboard Review"."""
    path = Path(name).expanduser()
    return path if path.is_dir() else OUTPUT_DIR / path.name


def _relink_sources(old_data):
    """point each meeting's recording at its new home. run.json kept the full path, in the
    project folder's data/, which becomes data/<name> inside the library."""
    for run in OUTPUT_DIR.glob("*/run.json"):
        meta = json.loads(run.read_text())
        source = Path(meta.get("source") or "")
        if source.is_absolute() and source.is_relative_to(old_data):
            meta["source"] = str(Path("data") / source.relative_to(old_data))
            run.write_text(json.dumps(meta, indent=2))


# main
def migrate(log=print):
    """move data/ and outputs/ from the project folder into the library, once. on the same
    disk this is a rename, so it's instant and nothing is copied. when the library already
    has either folder, nothing moves, so the two are never merged or split between places.
    returns the folders moved."""
    moves = [(old, new) for old, new in ((OLD_DATA_DIR, DATA_DIR), (OLD_OUTPUT_DIR, OUTPUT_DIR))
             if old.is_dir() and old.resolve() != new.resolve()]
    taken = [new for _, new in moves if new.exists()]
    if taken:
        log(f"not moving data/ and outputs/ out of {PROJECT_DIR}, since {', '.join(map(str, taken))} "
            f"already exists. move what you need by hand")
        return []
    if not moves:
        return []
    HOME.mkdir(parents=True, exist_ok=True)
    for old, new in moves:
        try:
            os.rename(old, new)
        except OSError as err:
            # another disk would mean copying gigabytes, which isn't done behind your back
            raise RuntimeError(f"couldn't move {old} to {new} ({err}). Move it by hand, or set WEMMICK_HOME "
                               f"in .env to a folder on the same disk") from None
        log(f"moved {old} to {new}")
    if (OLD_DATA_DIR, DATA_DIR) in moves:
        _relink_sources(OLD_DATA_DIR)
    return moves
