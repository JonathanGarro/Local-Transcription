"""local library in sqlite: people, their voice entries, and processed meetings.

data/library.db holds who is who. people have stable ids, so renaming a person updates
every past transcript. each voice entry keeps its own vector, plus the meeting and speaker
it came from, so profiles can be re-averaged or pruned. the profile vector is the mean of
a person's current entries. deleting a person vacuums the database file so no copy of the
voice stays on disk. a person's name is their full name. their teams and organizations,
which meeting notes look up when a person is tagged, live in their obsidian note and are
read through directory. the older teams and organizations tables here are no longer used.
projects are a plain list to file meetings under.
"""

import json
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from local_transcription import directory
from local_transcription.embed import normalize

# config
BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "data" / "library.db"
OUTPUT_DIR = BASE_DIR / "outputs"
# folder layout used before the library existed, imported once on first use
LEGACY_PROFILE_DIR = BASE_DIR / "data" / "profiles"
LEGACY_BACKUP_DIR = BASE_DIR / "data" / "profiles_imported"
ENTRY_COLUMNS = ["entry_id", "person_id", "name", "added", "method", "source_type", "source_file",
                 "meeting_id", "speaker", "start", "end", "seconds"]
SCHEMA = """
create table if not exists people (
    person_id text primary key,
    name text not null unique collate nocase,
    created text not null
);
create table if not exists entries (
    entry_id text primary key,
    person_id text not null references people(person_id) on delete cascade,
    vector blob not null,
    added text not null,
    method text,
    source_type text,
    source_file text,
    meeting_id text,
    speaker text,
    start_s real,
    end_s real,
    seconds real
);
create table if not exists meetings (
    meeting_id text primary key,
    title text,
    date text,
    source_file text,
    source_type text,
    split integer,
    minutes real,
    status text,
    updated text
);
create table if not exists teams (
    team_id text primary key,
    name text not null unique collate nocase
);
create table if not exists organizations (
    org_id text primary key,
    name text not null unique collate nocase
);
create table if not exists person_teams (
    person_id text not null references people(person_id) on delete cascade,
    team_id text not null references teams(team_id) on delete cascade,
    primary key (person_id, team_id)
);
create table if not exists projects (
    project_id text primary key,
    name text not null unique collate nocase,
    created text not null
);
create table if not exists meeting_speakers (
    meeting_id text not null references meetings(meeting_id) on delete cascade,
    speaker text not null,
    person_id text references people(person_id) on delete set null,
    status text,
    score real,
    minutes real,
    primary key (meeting_id, speaker)
);
"""
# columns added after the first release, applied to older databases on open
ADDED_COLUMNS = {
    "people": {"org_id": "text references organizations(org_id) on delete set null"},
    "meetings": {"project_id": "text references projects(project_id) on delete set null",
                 "archived": "text", "sent_path": "text", "original_file": "text"},
}
_ready = False


# helpers
def _now():
    return datetime.now().isoformat(timespec="seconds")


def _new_id():
    return uuid.uuid4().hex[:10]


def _blob(vector):
    return normalize(vector).astype(np.float64).tobytes()


def _vector(blob):
    return np.frombuffer(blob, dtype=np.float64)


def _none(value):
    # pandas missing values become sql nulls
    return None if value is None or (isinstance(value, float) and np.isnan(value)) else value


@contextmanager
def connect():
    global _ready
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("pragma foreign_keys = on")
    try:
        if not _ready:
            con.executescript(SCHEMA)
            _add_columns(con)
            _drop_full_name(con)
            _ready = True
            _import_legacy(con)
        with con:
            yield con
    finally:
        con.close()


def _add_columns(con):
    for table, columns in ADDED_COLUMNS.items():
        existing = {row["name"] for row in con.execute(f"pragma table_info({table})")}
        for column, kind in columns.items():
            if column not in existing:
                con.execute(f"alter table {table} add column {column} {kind}")
    con.commit()


def _drop_full_name(con):
    # people once had a short name plus a full name. the full name becomes the name, unless
    # another person already has it
    if "full_name" not in {row["name"] for row in con.execute("pragma table_info(people)")}:
        return
    con.execute("""update people set name = full_name where full_name is not null
                   and not exists (select 1 from people o where o.name = people.full_name
                                   and o.person_id != people.person_id)""")
    con.execute("alter table people drop column full_name")
    con.commit()


def _query(sql, params=()):
    with connect() as con:
        rows = con.execute(sql, params).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


# people
def find_person(name):
    """person id for a name (case-insensitive), or None."""
    with connect() as con:
        row = con.execute("select person_id from people where name = ?", (name.strip(),)).fetchone()
    return row["person_id"] if row else None


def ensure_person(name, con=None):
    """person id for a name, creating the person if needed."""
    name = name.strip()
    if not name:
        raise ValueError("a person needs a name")
    if con is None:
        with connect() as con:
            return ensure_person(name, con)
    row = con.execute("select person_id from people where name = ?", (name,)).fetchone()
    if row:
        return row["person_id"]
    person_id = _new_id()
    con.execute("insert into people (person_id, name, created) values (?, ?, ?)",
                (person_id, name, _now()))
    return person_id


def person_names():
    """{person_id: name} for everyone in the library."""
    people = _query("select person_id, name from people")
    return dict(zip(people["person_id"], people["name"])) if len(people) else {}


def people_summary():
    """one row per person with entry, meeting, and audio totals."""
    people = _query("""
        select p.person_id, p.name, p.created,
               (select count(*) from entries e where e.person_id = p.person_id) as entries,
               (select coalesce(sum(seconds), 0) from entries e where e.person_id = p.person_id) as profile_seconds,
               (select group_concat(distinct source_type) from entries e where e.person_id = p.person_id) as sources,
               (select count(distinct ms.meeting_id) from meeting_speakers ms
                 where ms.person_id = p.person_id) as meetings,
               (select coalesce(sum(ms.minutes), 0) from meeting_speakers ms
                 where ms.person_id = p.person_id) as minutes_heard,
               (select max(m.date) from meeting_speakers ms join meetings m using (meeting_id)
                 where ms.person_id = p.person_id) as last_heard
        from people p
        order by p.name collate nocase""")
    if len(people):
        # teams and organizations come from obsidian, joined like the other text columns
        index = directory.all_details()
        found = [directory.details(name, index) for name in people["name"]]
        people.insert(3, "organizations", [", ".join(d["organizations"]) for d in found])
        people.insert(4, "teams", [", ".join(d["teams"]) for d in found])
    return people


def person_details(person_id):
    """name, organizations, and teams for one person, the last two from their obsidian note."""
    with connect() as con:
        row = con.execute("select person_id, name from people where person_id = ?", (person_id,)).fetchone()
    if not row:
        return None
    return {**dict(row), **directory.details(row["name"])}


def set_person_details(person_id, organizations=None, teams=None):
    """write the fields notes use into the person's obsidian note. None leaves one as it is,
    an empty list clears it."""
    details = person_details(person_id)
    if not details:
        raise ValueError("no such person")
    directory.save(details["name"], teams=teams, organizations=organizations)


def lookup_names(table):
    """every name in teams, organizations, or projects, sorted."""
    if table not in ("teams", "organizations", "projects"):
        raise ValueError(f"no lookup table {table}")
    names = _query(f"select name from {table} order by name collate nocase")
    return list(names["name"]) if len(names) else []


def ensure_project(name):
    with connect() as con:
        row = con.execute("select project_id from projects where name = ?", (name.strip(),)).fetchone()
        if row:
            return row["project_id"]
        project_id = _new_id()
        con.execute("insert into projects values (?, ?, ?)", (project_id, name.strip(), _now()))
    return project_id


def set_meeting_project(meeting_id, project_name):
    """file a meeting under a project, or clear it with a blank name."""
    project_id = ensure_project(project_name) if project_name and project_name.strip() else None
    with connect() as con:
        con.execute("update meetings set project_id = ? where meeting_id = ?", (project_id, meeting_id))


def meeting_project(meeting_id):
    with connect() as con:
        row = con.execute("""select pr.name from meetings m join projects pr using (project_id)
                             where m.meeting_id = ?""", (meeting_id,)).fetchone()
    return row["name"] if row else None


def rename_person(person_id, new_name):
    """rename a person. returns the meeting ids whose transcripts need re-rendering."""
    new_name = new_name.strip()
    other = find_person(new_name)
    if other and other != person_id:
        raise ValueError(f"{new_name} already exists, merge the two people instead")
    with connect() as con:
        con.execute("update people set name = ? where person_id = ?", (new_name, person_id))
    return meetings_for_person(person_id)


def merge_people(source_id, target_id):
    """move every entry and meeting appearance from source into target, then remove source.
    returns the meeting ids whose transcripts need re-rendering."""
    if source_id == target_id:
        raise ValueError("cannot merge a person into themselves")
    affected = meetings_for_person(source_id)
    with connect() as con:
        con.execute("update entries set person_id = ? where person_id = ?", (target_id, source_id))
        con.execute("update meeting_speakers set person_id = ? where person_id = ?", (target_id, source_id))
        con.execute("""insert or ignore into person_teams select ?, team_id from person_teams
                       where person_id = ?""", (target_id, source_id))
        con.execute("""update people set org_id = coalesce(org_id, (select org_id from people
                       where person_id = ?)) where person_id = ?""", (source_id, target_id))
        con.execute("delete from people where person_id = ?", (source_id,))
    return affected


def delete_person(person_id):
    """remove a person and every voice entry, then vacuum so nothing stays in the file.
    past transcripts keep the name as plain text. returns True if the person existed."""
    with connect() as con:
        gone = con.execute("delete from people where person_id = ?", (person_id,)).rowcount > 0
    if gone:
        vacuum()
    return gone


def vacuum():
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("vacuum")
    finally:
        con.close()


# entries
def add_entry(name, vector, method, source_file="", source_type="other", start=None, end=None,
              seconds=None, meeting_id=None, speaker=None):
    """save one embedding for a person, creating the person if needed. returns the entry id."""
    entry_id = _new_id()
    with connect() as con:
        person_id = ensure_person(name, con)
        con.execute("insert into entries values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (entry_id, person_id, _blob(vector), _now(), method, source_type, str(source_file),
                     meeting_id, speaker, _none(start), _none(end),
                     None if _none(seconds) is None else round(float(seconds), 1)))
    return entry_id


def all_entries(person_id=None):
    """entries without vectors, one row each."""
    sql = """select e.entry_id, e.person_id, p.name, e.added, e.method, e.source_type, e.source_file,
                    e.meeting_id, e.speaker, e.start_s as start, e.end_s as end, e.seconds
             from entries e join people p using (person_id)"""
    params = ()
    if person_id:
        sql, params = sql + " where e.person_id = ?", (person_id,)
    entries = _query(sql + " order by p.name collate nocase, e.added", params)
    return entries if len(entries) else pd.DataFrame(columns=ENTRY_COLUMNS)


def entry_vectors(person_id):
    """(entry ids, matrix of vectors) for one person."""
    with connect() as con:
        rows = con.execute("select entry_id, vector from entries where person_id = ? order by added",
                           (person_id,)).fetchall()
    return [r["entry_id"] for r in rows], np.array([_vector(r["vector"]) for r in rows])


def load_profiles():
    """{name: (mean vector, entry count)} for everyone with at least one entry."""
    with connect() as con:
        rows = con.execute("""select p.name, e.vector from entries e
                              join people p using (person_id)""").fetchall()
    grouped = {}
    for row in rows:
        grouped.setdefault(row["name"], []).append(_vector(row["vector"]))
    return {name: (normalize(np.mean(vecs, axis=0)), len(vecs)) for name, vecs in grouped.items()}


def entry_similarities(person_id):
    """cosine similarity of each entry to the person's mean, useful for spotting bad entries."""
    entries = all_entries(person_id)
    if entries.empty:
        return entries
    ids, vectors = entry_vectors(person_id)
    mean = normalize(vectors.mean(axis=0))
    sims = dict(zip(ids, (vectors @ mean).round(3)))
    return entries.assign(similarity_to_mean=entries["entry_id"].map(sims))


def remove_entry(entry_id, meeting_id=None):
    """delete one entry. returns the person's name or None. with meeting_id, only an entry
    that meeting created is removed, so a copied output folder cannot touch another
    meeting's entries. the person stays in the library so past meetings keep their link,
    but with no entries they are no longer matched."""
    where, params = "e.entry_id = ?", [entry_id]
    if meeting_id is not None:
        where, params = where + " and e.meeting_id = ?", params + [meeting_id]
    with connect() as con:
        row = con.execute(f"""select p.name from entries e join people p using (person_id)
                              where {where}""", params).fetchone()
        if row:
            con.execute("delete from entries where entry_id = ?", (entry_id,))
    return row["name"] if row else None


# meetings
def save_meeting(meeting_id, meta, speakers, status, minutes=None):
    """record a processed meeting and who spoke in it."""
    with connect() as con:
        con.execute("""insert into meetings (meeting_id, title, date, source_file, source_type,
                       split, minutes, status, updated) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       on conflict(meeting_id) do update set title = excluded.title,
                       date = excluded.date, source_file = excluded.source_file,
                       source_type = excluded.source_type, split = excluded.split,
                       minutes = coalesce(excluded.minutes, meetings.minutes),
                       status = excluded.status, updated = excluded.updated""",
                    (meeting_id, meta.get("title"), meta.get("date"), Path(meta["source"]).name,
                     meta.get("source_type"), int(bool(meta.get("split"))), minutes, status, _now()))
        con.execute("delete from meeting_speakers where meeting_id = ?", (meeting_id,))
        known = {r["person_id"] for r in con.execute("select person_id from people")}
        for row in speakers.itertuples():
            person_id = _none(getattr(row, "person_id", None))
            con.execute("insert into meeting_speakers values (?, ?, ?, ?, ?, ?)",
                        (meeting_id, row.speaker, person_id if person_id in known else None, row.status,
                         _none(row.score), _none(row.minutes)))


def save_imported_meeting(meeting_id, title, date, note_path, person_ids):
    """record a meeting known only from its obsidian note, with no recording. it goes straight
    to the archive, and the people the note names are linked as its speakers."""
    with connect() as con:
        con.execute("""insert into meetings (meeting_id, title, date, source_type, status, updated,
                       archived, sent_path) values (?, ?, ?, 'obsidian', 'imported', ?, ?, ?)""",
                    (meeting_id, title, date, _now(), _now(), str(note_path)))
        for person_id in dict.fromkeys(person_ids):
            # no diarized speaker ids without a recording, so the person stands in
            con.execute("""insert into meeting_speakers (meeting_id, speaker, person_id, status)
                           values (?, ?, ?, 'imported')""", (meeting_id, person_id, person_id))


def meetings():
    """every processed meeting, newest first."""
    table = _query("""select m.*, pr.name as project from meetings m
                      left join projects pr using (project_id) order by m.date desc, m.updated desc""")
    return table


def archive_meeting(meeting_id, sent_path):
    """mark a meeting as sent to obsidian, which moves it from the inbox to the archive."""
    with connect() as con:
        con.execute("update meetings set archived = ?, sent_path = ? where meeting_id = ?",
                    (_now(), str(sent_path), meeting_id))


def rename_meeting_id(old_id, new_id):
    """move a meeting to a new id (its output folder name), keeping its speakers and entries."""
    with connect() as con:
        columns = ", ".join(r["name"] for r in con.execute("pragma table_info(meetings)") if r["name"] != "meeting_id")
        # copy, repoint, then delete, so the speaker rows never point at a missing meeting
        con.execute(f"insert into meetings (meeting_id, {columns}) select ?, {columns} from meetings "
                    "where meeting_id = ?", (new_id, old_id))
        con.execute("update meeting_speakers set meeting_id = ? where meeting_id = ?", (new_id, old_id))
        con.execute("update entries set meeting_id = ? where meeting_id = ?", (new_id, old_id))
        con.execute("delete from meetings where meeting_id = ?", (old_id,))


def find_meeting_by_source(source_file):
    """the meeting made from this audio file name, if any. matches the name the file has now
    or the one it had before it was renamed to match its note."""
    row = _query("select meeting_id from meetings where source_file = ? or original_file = ?",
                 (source_file, source_file))
    return row["meeting_id"].iloc[0] if len(row) else None


def rename_source_file(meeting_id, new_name):
    """record a renamed recording, keeping the first name it had so dropping it again is
    still recognized."""
    with connect() as con:
        con.execute("""update meetings set original_file = coalesce(original_file, source_file),
                       source_file = ? where meeting_id = ?""", (new_name, meeting_id))
        old = con.execute("select original_file from meetings where meeting_id = ?", (meeting_id,)).fetchone()
        con.execute("update entries set source_file = ? where meeting_id = ? and source_file = ?",
                    (new_name, meeting_id, old["original_file"]))


def unarchive_meeting(meeting_id):
    """back to the inbox. the sent path is kept so sending again finds the same file."""
    with connect() as con:
        con.execute("update meetings set archived = null where meeting_id = ?", (meeting_id,))


def meeting_sent_path(meeting_id):
    row = _query("select sent_path from meetings where meeting_id = ?", (meeting_id,))
    return row["sent_path"].iloc[0] if len(row) and isinstance(row["sent_path"].iloc[0], str) else None


def meeting_speaker_rows(meeting_id):
    return _query("select * from meeting_speakers where meeting_id = ?", (meeting_id,))


def meetings_for_person(person_id):
    table = _query("select distinct meeting_id from meeting_speakers where person_id = ?", (person_id,))
    return list(table["meeting_id"]) if len(table) else []


def delete_meeting(meeting_id):
    with connect() as con:
        con.execute("delete from meetings where meeting_id = ?", (meeting_id,))


# legacy import
def _import_legacy(con):
    """bring profiles from data/profiles/<slug>/ into the database once, and register
    meetings already in outputs/. the old folder is moved aside, not deleted."""
    if not LEGACY_PROFILE_DIR.exists():
        return
    if con.execute("select count(*) from entries").fetchone()[0]:
        return
    # entry ids recorded in each meeting's speakers.csv tell us which speaker an entry came from
    origins = {}
    for speakers_csv in OUTPUT_DIR.glob("*/speakers.csv"):
        table = pd.read_csv(speakers_csv, dtype={"entry_id": str})
        if "entry_id" in table:
            for speaker, entry_id in zip(table["speaker"], table["entry_id"]):
                if isinstance(entry_id, str):
                    origins[entry_id] = (speakers_csv.parent.name, speaker)
    count = 0
    with con:
        for folder in sorted(p for p in LEGACY_PROFILE_DIR.iterdir() if p.is_dir()):
            index_path = folder / "index.csv"
            if not index_path.exists():
                continue
            index = pd.read_csv(index_path, dtype={"entry_id": str})
            for row in index.itertuples():
                npy = folder / "entries" / f"{row.entry_id}.npy"
                if not npy.exists():
                    continue
                person_id = ensure_person(row.name, con)
                meeting_id, speaker = origins.get(row.entry_id, (None, None))
                con.execute("insert into entries values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (row.entry_id, person_id, _blob(np.load(npy)), row.added, row.method,
                             row.source_type, row.source_file, meeting_id, speaker,
                             _none(row.start), _none(row.end), _none(row.seconds)))
                count += 1
    shutil.move(str(LEGACY_PROFILE_DIR), str(LEGACY_BACKUP_DIR))
    print(f"imported {count} profile entries into {DB_PATH.relative_to(BASE_DIR)}, "
          f"old folder moved to {LEGACY_BACKUP_DIR.relative_to(BASE_DIR)}")


def legacy_meeting_dirs():
    """output folders from full pipeline runs, used to register meetings made before the library."""
    return sorted(p.parent for p in OUTPUT_DIR.glob("*/run.json") if (p.parent / "segments.json").exists())


