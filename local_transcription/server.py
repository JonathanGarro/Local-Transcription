"""local web api for the app window. binds to 127.0.0.1 only, nothing is reachable from
other machines. one worker thread runs recordings through the pipeline one at a time so
jobs never compete for the gpu.
"""

import io
import json
import os
import queue
import re
import subprocess
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from local_transcription import ask, audio, directory, keychain, library, notes, pipeline, plaud, projects, render, settings, things, vocab

# config
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
AUDIO_TYPES = {".m4a", ".mp3", ".wav"}
CHANNELS = {"left": 0, "right": 1}
# progress messages from pipeline.run, in order, mapped to a share of the job
# how far back the inbox looks for recordings uploaded to plaud
PLAUD_DAYS = 14
STEPS = {"analyzing audio": 0.02, "diarizing": 0.10, "transcribing": 0.35,
         "transcribing mic": 0.75, "matching speakers": 0.88}
# note jobs, the token count moves the bar between writing and done
NOTE_STEPS = {"summarizing": 0.05, "loading": 0.10, "sending": 0.30, "writing the summary": 0.25}
NOTE_EXPECTED_TOKENS = 1500


# helpers
def _clean(value):
    # json has no nan
    if isinstance(value, float) and np.isnan(value):
        return None
    if isinstance(value, np.generic):
        return value.item()
    return value


def _records(frame):
    return [{k: _clean(v) for k, v in row.items()} for row in frame.to_dict("records")]


def _step_share(message):
    if message.startswith("transcribing") and message.endswith("mic"):
        return STEPS["transcribing mic"]
    return STEPS.get(message)


def _meeting_dir(meeting_id):
    try:
        return pipeline.meeting_dir(meeting_id)
    except ValueError as err:
        raise HTTPException(404, str(err))


def _dated_name(name):
    """'2026-10-16 - Zoom Call.mp3' stays as it is. a date elsewhere in the name moves to the
    front, and a name with no date gets today's."""
    path = Path(name)
    found = render.DATE_PATTERN.search(path.stem)
    if found and found.start() == 0:
        return name
    if found:
        parts = [path.stem[:found.start()].strip(" -_"), path.stem[found.end():].strip(" -_")]
        date, rest = found.group(1), " ".join(p for p in parts if p)
    else:
        date, rest = datetime.now().date().isoformat(), path.stem.strip(" -_")
    return f"{date} - {rest}{path.suffix}" if rest else f"{date}{path.suffix}"


def _plaud_file(recording):
    """the name, without extension, a plaud recording is saved under, dated by its recording
    day when its name has no date. the extension is plaud's, added on download."""
    name = recording["name"].replace("/", "-").strip()
    name = name if render.DATE_PATTERN.search(name) else f"{recording['date']} - {name}"
    # a placeholder extension, so a dot in the name is not taken for one
    return Path(_dated_name(f"{name}.audio")).stem


def _unique_path(folder, name):
    path = folder / name
    stem, suffix, n = path.stem, path.suffix, 2
    while path.exists():
        path = folder / f"{stem} ({n}){suffix}"
        n += 1
    return path


# jobs
class Jobs:
    def __init__(self):
        self.items = []
        self.lock = threading.Lock()
        self.queue = queue.Queue()
        threading.Thread(target=self._work, daemon=True).start()

    def add(self, path, source_type, meeting_id=None):
        return self._queue({"kind": "transcribe", "file": path.name, "path": str(path),
                            "source_type": source_type, "meeting_id": meeting_id})

    def add_plaud(self, plaud_id, name):
        # the audio is downloaded when the job starts, so the queue stays quick to fill
        return self._queue({"kind": "transcribe", "file": name, "path": None, "source_type": "auto",
                            "meeting_id": None, "plaud_id": plaud_id})

    def add_note(self, meeting_id, backend, project):
        return self._queue({"kind": "note", "file": f"Note for {meeting_id}", "meeting_id": meeting_id,
                            "backend": backend, "project": project})

    def _queue(self, fields):
        job = {"job_id": uuid.uuid4().hex[:8], "status": "queued", "step": "waiting",
               "progress": 0.0, "error": None, **fields}
        with self.lock:
            self.items.append(job)
        self.queue.put(job)
        return job

    def snapshot(self):
        with self.lock:
            return [{k: v for k, v in job.items() if k != "path"} for job in self.items]

    def dismiss(self, job_id):
        with self.lock:
            self.items = [j for j in self.items if not (j["job_id"] == job_id and j["status"] in ("done", "failed"))]

    def _update(self, job, **fields):
        with self.lock:
            job.update(fields)

    def _work(self):
        while True:
            job = self.queue.get()
            self._update(job, status="processing", step="starting")
            try:
                if job["kind"] == "note":
                    self._note(job)
                else:
                    self._transcribe(job)
            except Exception as err:
                traceback.print_exc()
                self._update(job, status="failed", step="failed", error=str(err) or type(err).__name__)

    def _transcribe(self, job):
        def progress(message):
            share = _step_share(message.strip())
            if message.startswith("  "):
                self._update(job, detail=message.strip())
            elif share is not None:
                self._update(job, step=message, progress=share)

        if job.get("plaud_id") and not job["path"]:
            self._update(job, step="downloading from plaud", progress=0.0)
            DATA_DIR.mkdir(exist_ok=True)
            # plaud's audio is mp3
            stem = _unique_path(DATA_DIR, f"{job['file']}.mp3").stem
            def downloaded(done, total):
                mb = f"{done / 1e6:.0f} of {total / 1e6:.0f} MB" if total else f"{done / 1e6:.0f} MB"
                # the download is the first sliver of the bar, before analysis starts
                self._update(job, detail=mb, progress=STEPS["analyzing audio"] * (done / total if total else 0))

            self._update(job, path=str(plaud.download_audio(job["plaud_id"], DATA_DIR, stem, downloaded)))
        source_type = job["source_type"]
        if source_type == "auto":
            info = audio.analyze(job["path"])
            source_type = "zoom" if audio.is_split(info) else "plaud" if info["channels"] == 1 else "other"
            self._update(job, source_type=source_type)
        out_dir, speakers, review = pipeline.run(
            job["path"], source_type=source_type, progress=progress,
            mic_channel=CHANNELS.get(os.environ.get("MIC_CHANNEL", "left"), 0),
            owner=os.environ.get("MIC_OWNER"),
            out_dir=pipeline.OUTPUT_DIR / job["meeting_id"] if job.get("meeting_id") else None)
        if job.get("plaud_id"):
            library.save_plaud_import(job["plaud_id"], out_dir.name)
        self._update(job, status="done", step="needs review" if review else "done",
                     progress=1.0, meeting_id=out_dir.name)
        self.auto_note(out_dir.name)

    def auto_note(self, meeting_id):
        """summarize a meeting once every speaker is named, unless it has a note or one is queued.
        a meeting imported from obsidian already has its note there."""
        out_dir = pipeline.OUTPUT_DIR / meeting_id
        if (out_dir / "note.json").exists() or library.meeting_sent_path(meeting_id):
            return None
        row = library.meetings().query("meeting_id == @meeting_id")
        if not len(row) or row["status"].iloc[0] != "done":
            return None
        if any(j.get("kind") == "note" and j["meeting_id"] == meeting_id and j["status"] in ("queued", "processing")
               for j in self.snapshot()):
            return None
        return self.add_note(meeting_id, notes.default_backend(), None)

    def _note(self, job):
        def progress(message):
            text = message.strip()
            if text.endswith("tokens written"):
                written = int(text.split()[0])
                self._update(job, detail=text, progress=min(0.95, 0.25 + 0.7 * written / NOTE_EXPECTED_TOKENS))
                return
            share = next((v for k, v in NOTE_STEPS.items() if text.startswith(k)), None)
            self._update(job, step=text, **({"progress": share} if share is not None else {}))

        pipeline.write_note(pipeline.meeting_dir(job["meeting_id"]), backend=job["backend"],
                            project=job["project"], progress=progress)
        self._update(job, status="done", step="done", progress=1.0)


class ApplyRequest(BaseModel):
    names: dict[str, str] = {}
    no_enroll: list[str] = []
    skip: list[str] = []
    merges: list[tuple[str, str]] = []


class VocabEntry(BaseModel):
    term: str
    variants: list[str] = []


class VocabRequest(BaseModel):
    entries: list[VocabEntry]


class SettingsRequest(BaseModel):
    accent: str | None = None
    claude_model: str | None = None


class TodosRequest(BaseModel):
    turn: int


class Todo(BaseModel):
    title: str
    notes: str = ""
    meeting_id: str = ""
    seconds: int = -1
    project_id: str = ""
    when: str = ""
    deadline: str = ""


class AddTodosRequest(BaseModel):
    todos: list[Todo]
    # a meeting whose note's action items become the to-dos added
    note_meeting_id: str | None = None


class AskRequest(BaseModel):
    question: str
    # None starts a new conversation
    conversation_id: str | None = None


class HideRequest(BaseModel):
    hidden: bool = True


class PlaudRequest(BaseModel):
    plaud_ids: list[str]


class NameRequest(BaseModel):
    name: str


class NoteRequest(BaseModel):
    backend: str = "local"
    # None lets the model suggest one of the library's projects
    project: str | None = None


class SendRequest(BaseModel):
    replace: bool = False


class KeyRequest(BaseModel):
    key: str


class NoteText(BaseModel):
    markdown: str


class DetailsRequest(BaseModel):
    organizations: list[str] = []
    teams: list[str] = []


FORWARD_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Boswell</title>
<body style="font: 15px -apple-system, sans-serif; margin: 40px; color: #555">
<p id="message">Boswell runs as a Mac app now. Open it from your Applications folder.</p>
<script>
  const [, page, id, tab, at] = location.hash.split("/");
  if (["transcript", "review", "imported"].includes(page) && id) {
    const link = "boswell://meeting?id=" + encodeURIComponent(decodeURIComponent(id))
      + (tab === "at" && at ? "&at=" + at : "");
    document.getElementById("message").innerHTML = 'Opening the meeting in Boswell. <a href="' + link + '">Open it</a> if nothing happens.';
    location.replace(link);
  }
</script>
"""


# main
def create_app():
    app = FastAPI(title="Boswell", docs_url=None, redoc_url=None)
    keychain.load()
    jobs = Jobs()
    pipeline.register_existing()
    # wavs rebuilt to replay an archived meeting are not kept between launches. a meeting with
    # no run.json yet is still being processed, by scripts/import_plaud.py say, and needs its wavs
    for meeting_id in library.meetings().query("archived.notna()")["meeting_id"] if len(library.meetings()) else []:
        if (pipeline.OUTPUT_DIR / meeting_id / "run.json").exists():
            pipeline.drop_audio(pipeline.OUTPUT_DIR / meeting_id)

    @app.get("/api/state")
    def state():
        meetings = _records(library.meetings())
        for m in meetings:
            m["note"] = (pipeline.OUTPUT_DIR / m["meeting_id"] / "note.json").exists()
        return {"jobs": jobs.snapshot(), "meetings": meetings,
                "owner": os.environ.get("MIC_OWNER")}

    def vocabulary_state():
        rows = vocab.entries()
        # the share of whisper's spelling hint the terms take, names fill whatever is left
        return {"entries": rows, "used": len(", ".join(r["term"] for r in rows)), "max": vocab.PROMPT_MAX_CHARS}

    def settings_state():
        return {**settings.load(), "claude_model": settings.claude_model(), "accents": settings.ACCENTS,
                "models": [{"id": k, **v} for k, v in settings.MODELS.items()],
                "key": settings.masked_key(), "key_source": keychain.source(), "usage": library.usage_totals()}

    @app.get("/api/settings")
    def get_settings():
        return settings_state()

    # the anthropic key goes in the keychain. it is never logged or sent back, only its masked form
    @app.put("/api/settings/key")
    def save_key(request: KeyRequest):
        try:
            keychain.save(request.key)
        except ValueError as err:
            raise HTTPException(400, str(err))
        return settings_state()

    @app.delete("/api/settings/key")
    def remove_key():
        keychain.remove()
        return settings_state()

    @app.post("/api/settings/key/move")
    def move_key():
        try:
            keychain.move_from_env()
        except ValueError as err:
            raise HTTPException(400, str(err))
        return settings_state()

    @app.post("/api/settings/key/check")
    def check_key():
        # listing models is free, and fails the same way a real request would on a bad key
        try:
            ask._client().models.list(limit=1)
        except Exception as err:
            message = getattr(err, "message", None) or type(err).__name__
            return {"ok": False, "error": str(message)}
        return {"ok": True}

    @app.put("/api/settings")
    def put_settings(request: SettingsRequest):
        try:
            settings.save(accent=request.accent, claude_model=request.claude_model)
        except ValueError as err:
            raise HTTPException(400, str(err))
        return settings_state()

    # questions about past meetings. answers take a while, so a question runs in its own
    # thread and the page polls it. conversations are saved in the library after each answer
    asks = {}

    def meeting_link(meeting_id, seconds=-1):
        """a boswell:// link the mac app opens the meeting from, at a moment of its transcript
        when given. None for a meeting with neither a transcript nor an imported note."""
        if not (pipeline.OUTPUT_DIR / meeting_id / "segments.json").exists() and not library.meeting_sent_path(meeting_id):
            return None
        return f"boswell://meeting?id={quote(meeting_id, safe='')}" + (f"&at={seconds}" if seconds >= 0 else "")

    def run_ask(ask_id, conversation_id, question):
        job = asks[ask_id]
        try:
            saved = library.conversation(conversation_id) or {"turns": [], "messages": [], "title": None}
            text, history, touched = ask.answer(
                saved["messages"], question, os.environ.get("MIC_OWNER"),
                datetime.now().date().isoformat(), progress=lambda step: job["steps"].append(step))
            mentioned = re.findall(r"\(meeting:([^)@]+)", text)
            links = {m: meeting_link(m) for m in dict.fromkeys([*touched, *mentioned])}
            links = {m: l for m, l in links.items() if l}
            turn = {"question": question, "answer": text, "links": links, "at": datetime.now().isoformat(timespec="seconds")}
            library.save_conversation(conversation_id, saved["turns"] + [turn], ask.saveable(history))
            if not saved["title"]:
                try:
                    library.rename_conversation(conversation_id, ask.title(question, text))
                except Exception:
                    # a conversation without a name still works, it shows its first question
                    traceback.print_exc()
            job.update(status="done", answer=text, links=links,
                       title=(library.conversation(conversation_id) or {}).get("title"))
        except Exception as err:
            traceback.print_exc()
            job.update(status="failed", error=str(err) or type(err).__name__)

    @app.post("/api/ask")
    def start_ask(request: AskRequest):
        if not request.question.strip():
            raise HTTPException(400, "ask a question")
        conversation_id = request.conversation_id or uuid.uuid4().hex[:8]
        ask_id = uuid.uuid4().hex[:8]
        asks[ask_id] = {"status": "working", "steps": [], "conversation_id": conversation_id}
        threading.Thread(target=run_ask, args=(ask_id, conversation_id, request.question.strip()), daemon=True).start()
        return {"ask_id": ask_id, "conversation_id": conversation_id}

    @app.get("/api/conversations")
    def conversations(page: int = 1):
        found = library.conversations_page(page)
        return {**found, "items": _records(found["items"]) if len(found["items"]) else []}

    @app.get("/api/conversations/{conversation_id}")
    def get_conversation(conversation_id: str):
        found = library.conversation(conversation_id)
        if not found:
            raise HTTPException(404, "no such conversation")
        return {k: _clean(v) if k != "turns" else v for k, v in found.items() if k != "messages"}

    @app.post("/api/conversations/{conversation_id}/rename")
    def rename_conversation(conversation_id: str, request: NameRequest):
        if not request.name.strip():
            raise HTTPException(400, "the name cannot be blank")
        library.rename_conversation(conversation_id, request.name.strip())
        return {"ok": True}

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str):
        library.delete_conversation(conversation_id)
        return {"ok": True}

    @app.get("/api/things/projects")
    def things_projects():
        try:
            return {"areas": things.areas(), "projects": things.projects()}
        except RuntimeError as err:
            raise HTTPException(502, str(err))

    def file_projects(todos, open_projects, meetings):
        """the meeting's project charter in obsidian names its things project, which beats
        claude's guess. claude's guess beats the inbox."""
        open_ids = {p["id"] for p in open_projects}
        for todo in todos:
            charter = projects.things_project_id(meetings.get(todo["meeting_id"], {}).get("project"))
            if charter in open_ids:
                todo.update(project_id=charter, project_source="charter")
            else:
                todo["project_source"] = "claude" if todo["project_id"] else "inbox"

    @app.post("/api/meetings/{meeting_id}/todos")
    def meeting_todos(meeting_id: str):
        row = library.meetings().query("meeting_id == @meeting_id")
        if not len(row):
            raise HTTPException(404, f"no meeting named {meeting_id}")
        info = _records(row)[0]
        meeting = {"title": info["title"] or meeting_id, "date": info["date"], "project": info.get("project"),
                   "people": library.meeting_people().get(meeting_id, [])}
        try:
            open_projects = things.projects()
            todos = ask.meeting_todos(meeting_id, meeting, open_projects, os.environ.get("MIC_OWNER"),
                                      datetime.now().date().isoformat(),
                                      [t["title"] for t in library.meeting_things_todos(meeting_id)])
        except RuntimeError as err:
            raise HTTPException(502, str(err))
        file_projects(todos, open_projects, {meeting_id: meeting})
        library.mark_todos_checked(meeting_id)
        return {"todos": todos, "projects": open_projects, "areas": things.areas(),
                "meetings": {meeting_id: {"title": meeting["title"], "date": meeting["date"]}}}

    @app.post("/api/conversations/{conversation_id}/todos")
    def conversation_todos(conversation_id: str, request: TodosRequest):
        found = library.conversation(conversation_id)
        if not found or not 0 <= request.turn < len(found["turns"]):
            raise HTTPException(404, "no such answer")
        turn = found["turns"][request.turn]
        rows = library.meetings().set_index("meeting_id")
        meetings = {m: {"title": rows.at[m, "title"] or m, "date": rows.at[m, "date"],
                        "project": _clean(rows.at[m, "project"])}
                    for m in turn.get("links", {}) if m in rows.index}
        try:
            open_projects = things.projects()
            todos = ask.extract_todos(turn["question"], turn["answer"], meetings, open_projects,
                                      os.environ.get("MIC_OWNER"), datetime.now().date().isoformat())
        except RuntimeError as err:
            raise HTTPException(502, str(err))
        file_projects(todos, open_projects, meetings)
        return {"todos": todos, "projects": open_projects, "areas": things.areas(),
                "meetings": {m: {"title": v["title"], "date": v["date"]} for m, v in meetings.items()}}

    @app.post("/api/things/add")
    def add_todos(request: AddTodosRequest):
        dated = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        rows = library.meetings().set_index("meeting_id")
        added = []
        for todo in request.todos:
            if not todo.title.strip():
                continue
            when = todo.when if todo.when in ("", "today", "tomorrow") or dated.match(todo.when) else ""
            deadline = todo.deadline if dated.match(todo.deadline or "") else ""
            # the note says where the to-do came from, with links to the meeting
            notes = [todo.notes.strip()]
            if todo.meeting_id in rows.index:
                title, date = rows.at[todo.meeting_id, "title"] or todo.meeting_id, rows.at[todo.meeting_id, "date"]
                at = f" at {ask._clock(todo.seconds)}" if todo.seconds >= 0 else ""
                notes.append(f"From {title}, {date}{at}.")
                sent = library.meeting_sent_path(todo.meeting_id)
                if sent:
                    notes.append(f"obsidian://open?path={quote(sent, safe='')}")
                link = meeting_link(todo.meeting_id, todo.seconds)
                if link:
                    notes.append(link)
            try:
                added.append({"title": todo.title.strip(), "id": things.add_todo(
                    todo.title.strip(), "\n\n".join(n for n in notes if n), todo.project_id, when, deadline)})
                library.record_things_todo(added[-1]["id"], todo.meeting_id if todo.meeting_id in rows.index else None,
                                           added[-1]["title"], "note" if request.note_meeting_id else "ask")
            except RuntimeError as err:
                failed = f"added {len(added)}, then Things said: {err}"
                break
        else:
            failed = None
        vault = None
        if request.note_meeting_id and added:
            # the note's action items become links to these to-dos, after any sent before
            out_dir = _meeting_dir(request.note_meeting_id)
            earlier = json.loads((out_dir / "note.json").read_text()).get("things_items") or []
            vault = pipeline.set_action_items(out_dir, earlier + added)
        if failed:
            raise HTTPException(502, failed)
        return {"added": len(added), "vault": vault}

    @app.get("/api/ask/{ask_id}")
    def ask_status(ask_id: str):
        if ask_id not in asks:
            raise HTTPException(404, "no such question")
        return asks[ask_id]

    @app.get("/api/vocabulary")
    def vocabulary():
        return vocabulary_state()

    @app.put("/api/vocabulary")
    def save_vocabulary(request: VocabRequest):
        try:
            vocab.save([e.model_dump() for e in request.entries])
        except ValueError as err:
            raise HTTPException(400, str(err))
        return vocabulary_state()

    @app.get("/api/plaud/recent")
    def plaud_recent():
        try:
            recordings = plaud.recent(PLAUD_DAYS)
        except FileNotFoundError:
            raise HTTPException(503, "the plaud cli is not installed, see the readme")
        except RuntimeError as err:
            raise HTTPException(502, f"plaud: {err}")
        imported = library.plaud_imports()
        hidden = library.plaud_hidden()
        meetings = library.meetings()
        # recordings brought in before imports were tracked are known by name
        known = set(meetings["meeting_id"]) | {Path(f).stem for col in ("source_file", "original_file")
                                                for f in meetings[col].dropna()} if len(meetings) else set()
        queued = {j.get("plaud_id"): j for j in jobs.snapshot() if j.get("plaud_id") and j["status"] != "failed"}
        for r in recordings:
            r["file"] = _plaud_file(r)
            r["meeting_id"] = imported.get(r["plaud_id"]) or (r["file"] if r["file"] in known else None)
            r["status"] = ("imported" if r["meeting_id"] else queued[r["plaud_id"]]["status"] if r["plaud_id"] in queued
                           else "new")
            r["hidden"] = r["plaud_id"] in hidden
            # where its row opens: the app's note, else the obsidian one, else the review
            out_dir = pipeline.OUTPUT_DIR / (r["meeting_id"] or "")
            r["open"] = (None if not r["meeting_id"] else "note" if (out_dir / "note.json").exists()
                         else "imported" if library.meeting_sent_path(r["meeting_id"]) else "review")
        return {"days": PLAUD_DAYS, "recordings": recordings}

    @app.post("/api/plaud/{plaud_id}/hide")
    def plaud_hide(plaud_id: str, request: HideRequest):
        library.set_plaud_hidden(plaud_id, request.hidden)
        return {"ok": True}

    @app.post("/api/plaud/import")
    def plaud_import(request: PlaudRequest):
        busy = {j.get("plaud_id") for j in jobs.snapshot() if j["status"] in ("queued", "processing")}
        recordings = {r["plaud_id"]: r for r in plaud.recent(PLAUD_DAYS)}
        queued = []
        for plaud_id in request.plaud_ids:
            if plaud_id in busy or plaud_id not in recordings:
                continue
            queued.append(jobs.add_plaud(plaud_id, _plaud_file(recordings[plaud_id]))["job_id"])
        return {"queued": queued}

    @app.post("/api/upload")
    async def upload(files: list[UploadFile] = File(...), source_type: str = Form("auto"),
                     rerun: bool = Form(False)):
        if source_type not in ("auto", "plaud", "zoom", "other"):
            raise HTTPException(400, f"unknown source {source_type}")
        DATA_DIR.mkdir(exist_ok=True)
        queued, skipped = [], []
        for upload_file in files:
            name = Path(upload_file.filename or "").name
            if Path(name).suffix.lower() not in AUDIO_TYPES:
                skipped.append({"file": name, "reason": "not an m4a, mp3, or wav file"})
                continue
            body = await upload_file.read()
            dated = _dated_name(name)
            # sent meetings have their folder and recording renamed after the note, so look
            # the file up by the name it arrived with too
            meeting_id = library.find_meeting_by_source(dated) or Path(dated).stem
            already = (pipeline.OUTPUT_DIR / meeting_id / "run.json").exists()
            if already and not rerun:
                # re-running would replace the confirmed names, so only do it when asked
                skipped.append({"file": name, "reason": "already processed", "meeting_id": meeting_id})
                continue
            path = DATA_DIR / dated
            if already:
                # reuse the copy already in data/, under whatever name it has now
                kept = pipeline.source_path(json.loads((pipeline.OUTPUT_DIR / meeting_id / "run.json").read_text()))
                if kept.exists() and kept.stat().st_size == len(body):
                    path = kept
            if path.exists() and path.stat().st_size != len(body):
                # a different recording with the same name, keep both
                path = _unique_path(DATA_DIR, path.name)
            if not path.exists():
                path.write_bytes(body)
            queued.append(jobs.add(path, source_type, meeting_id if already else None)["job_id"])
        return {"queued": queued, "skipped": skipped}

    @app.post("/api/jobs/{job_id}/dismiss")
    def dismiss(job_id: str):
        jobs.dismiss(job_id)
        return {"ok": True}

    @app.get("/api/meetings/{meeting_id}")
    def meeting(meeting_id: str):
        out_dir = _meeting_dir(meeting_id)
        meta, segments, speakers, _ = pipeline.load_run(out_dir)
        speakers = pipeline.link_people(speakers)
        labels = pipeline.display_names(speakers, segments)
        row = library.meetings().query("meeting_id == @meeting_id")
        info = _records(row)[0] if len(row) else {}
        result = []
        for spk in _records(speakers):
            spk["label"] = labels.get(spk["speaker"], spk["speaker"])
            spk["resolved"] = spk["status"] in pipeline.RESOLVED
            spk["clips"] = pipeline.clips(out_dir, spk["speaker"])
            spk["suggestions"] = pipeline.suggestions(out_dir, spk["speaker"])
            result.append(spk)
        result.sort(key=lambda s: s.get("first_start") or 0)
        return {"meeting_id": meeting_id, "title": info.get("title"), "date": info.get("date"),
                "status": info.get("status"), "minutes": info.get("minutes"),
                "source_file": info.get("source_file"), "split": bool(meta.get("split")),
                "archived": info.get("archived"), "sent_path": info.get("sent_path"),
                "note": (out_dir / "note.json").exists(), "owner": meta.get("owner"), "speakers": result}

    @app.get("/api/meetings/{meeting_id}/clip")
    def clip(meeting_id: str, speaker: str, start: float, end: float):
        out_dir = _meeting_dir(meeting_id)
        meta = pipeline.load_run(out_dir)[0]
        # cut from the original recording, so the playback wavs need not be rebuilt first
        source = pipeline.source_path(meta)
        if source.exists():
            mic = meta.get("mic_channel") or 0
            channel = (mic if speaker == pipeline.MIC_SPEAKER else 1 - mic) if meta.get("split") else None
            return Response(audio.clip(source, start, end, channel), media_type="audio/wav")
        try:
            wav = pipeline.speaker_audio(out_dir, meta, speaker)
        except ValueError as err:
            raise HTTPException(404, str(err))
        rate = sf.info(str(wav)).samplerate
        samples, rate = sf.read(str(wav), start=int(max(0, start) * rate), stop=int(end * rate), dtype="float32")
        buffer = io.BytesIO()
        sf.write(buffer, samples, rate, format="WAV", subtype="PCM_16")
        return Response(buffer.getvalue(), media_type="audio/wav")

    @app.get("/api/meetings/{meeting_id}/audio")
    def full_audio(meeting_id: str):
        try:
            wav = pipeline.ensure_audio(_meeting_dir(meeting_id))
        except ValueError as err:
            raise HTTPException(404, str(err))
        return FileResponse(wav, media_type="audio/wav")

    @app.get("/api/meetings/{meeting_id}/transcript")
    def transcript(meeting_id: str):
        out_dir = _meeting_dir(meeting_id)
        segments = pipeline.load_run(out_dir)[1]
        # join back-to-back segments from the same person, as the markdown does
        turns = []
        for seg in _records(segments):
            if turns and turns[-1]["name"] == seg["name"]:
                turns[-1]["text"] += " " + seg["text"]
                turns[-1]["end"] = seg["end"]
            else:
                turns.append(dict(seg))
        return {"turns": turns, "markdown": (out_dir / "transcript.md").read_text(),
                "path": str((out_dir / "transcript.md").relative_to(BASE_DIR))}

    @app.post("/api/meetings/{meeting_id}/apply")
    def apply(meeting_id: str, request: ApplyRequest):
        out_dir = _meeting_dir(meeting_id)
        try:
            for source, target in request.merges:
                pipeline.merge_speakers(out_dir, source, target)
            merged = {source for source, _ in request.merges}
            names = {s: n.strip() for s, n in request.names.items() if n.strip() and s not in merged}
            enroll = set(names) - set(request.no_enroll)
            if names or request.skip:
                pipeline.apply_names(out_dir, names, enroll=enroll,
                                     skip=[s for s in request.skip if s not in merged])
        except ValueError as err:
            raise HTTPException(400, str(err))
        jobs.auto_note(meeting_id)
        return meeting(meeting_id)

    @app.post("/api/meetings/{meeting_id}/reveal")
    def reveal(meeting_id: str):
        subprocess.run(["open", "-R", str(_meeting_dir(meeting_id) / "transcript.md")], check=False)
        return {"ok": True}

    @app.get("/api/meetings/{meeting_id}/note")
    def note(meeting_id: str):
        out_dir = _meeting_dir(meeting_id)
        path, summary = out_dir / "note.md", out_dir / "note.json"
        running = [j for j in jobs.snapshot() if j.get("kind") == "note" and j["meeting_id"] == meeting_id
                   and j["status"] in ("queued", "processing")]
        # a sent note shows what is in obsidian
        vault = pipeline.sync_from_vault(out_dir) if summary.exists() and not running else None
        info = json.loads(summary.read_text()) if summary.exists() else {}
        return {"exists": path.exists(), "markdown": path.read_text() if path.exists() else "",
                "path": str(path.relative_to(BASE_DIR)), "project": library.meeting_project(meeting_id),
                "backend": info.get("backend"), "model": info.get("model"), "created": info.get("created"),
                "edited": info.get("edited"), "sent_path": library.meeting_sent_path(meeting_id),
                "archived": _records(library.meetings().query("meeting_id == @meeting_id"))[0].get("archived"),
                "notes_dir": str(pipeline.notes_dir() or ""), "vault": vault,
                "job": running[0] if running else None,
                "todos_sent": library.meeting_things_todos(meeting_id),
                "todos_checked": _records(library.meetings().query("meeting_id == @meeting_id"))[0].get("todos_checked")}

    @app.post("/api/meetings/{meeting_id}/note")
    def write_note(meeting_id: str, request: NoteRequest):
        _meeting_dir(meeting_id)
        if request.backend not in notes.BACKENDS:
            raise HTTPException(400, f"unknown backend {request.backend}")
        if request.backend == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
            raise HTTPException(400, "add your Anthropic API key in Settings to use Claude")
        project = request.project.strip() if request.project and request.project.strip() else None
        return jobs.add_note(meeting_id, request.backend, project)

    @app.post("/api/meetings/{meeting_id}/note/text")
    def save_note(meeting_id: str, request: NoteText):
        out_dir = _meeting_dir(meeting_id)
        if not (out_dir / "note.json").exists():
            raise HTTPException(404, "this meeting has no note yet")
        pipeline.save_note_edits(out_dir, request.markdown)
        return {"ok": True}

    @app.get("/api/meetings/{meeting_id}/note/diff")
    def note_diff(meeting_id: str):
        out_dir = _meeting_dir(meeting_id)
        if not (out_dir / "note.json").exists():
            raise HTTPException(404, "this meeting has no note yet")
        rows, base = pipeline.note_diff(out_dir)
        return {"rows": rows, "base": base, "added": sum(r["kind"] == "add" for r in rows),
                "removed": sum(r["kind"] == "del" for r in rows)}

    @app.post("/api/meetings/{meeting_id}/note/discard")
    def discard_note(meeting_id: str):
        pipeline.discard_note_edits(_meeting_dir(meeting_id))
        return note(meeting_id)

    @app.post("/api/meetings/{meeting_id}/note/send")
    def send_note(meeting_id: str, request: SendRequest):
        out_dir = _meeting_dir(meeting_id)
        if any(j.get("kind") == "note" and j["meeting_id"] == meeting_id and j["status"] in ("queued", "processing")
               for j in jobs.snapshot()):
            raise HTTPException(400, "the note is still being written")
        try:
            path, out_dir = pipeline.send_note(out_dir, replace=request.replace)
        except FileExistsError as err:
            raise HTTPException(409, f"{err} already exists in the meeting notes folder")
        except pipeline.VaultChanged:
            raise HTTPException(409, "The Obsidian copy was edited after it was sent, sending replaces those edits")
        except ValueError as err:
            raise HTTPException(400, str(err))
        return {"path": str(path), "meeting_id": out_dir.name}

    @app.post("/api/meetings/{meeting_id}/note/pull")
    def pull_note(meeting_id: str):
        pipeline.sync_from_vault(_meeting_dir(meeting_id), force=True)
        return note(meeting_id)

    @app.get("/api/meetings/{meeting_id}/imported")
    def imported(meeting_id: str):
        # a meeting imported from obsidian has its note only in the vault, so it is read from there
        row = library.meetings().query("meeting_id == @meeting_id and sent_path.notna()")
        if not len(row):
            raise HTTPException(404, f"no imported meeting named {meeting_id}")
        info = _records(row)[0]
        path = Path(info["sent_path"])
        people = library.meeting_speaker_rows(meeting_id)
        names = library.person_names()
        return {**info, "people": sorted(names[p] for p in people.get("person_id", []) if p in names),
                "markdown": path.read_text() if path.exists() else None,
                "processed": (pipeline.OUTPUT_DIR / meeting_id / "run.json").exists()}

    @app.post("/api/meetings/{meeting_id}/open-obsidian")
    def open_obsidian(meeting_id: str):
        path = library.meeting_sent_path(meeting_id)
        if not path or not Path(path).exists():
            raise HTTPException(404, "the note is not in the vault yet, send it first")
        # obsidian finds the vault from the file's path
        subprocess.run(["open", f"obsidian://open?path={quote(path, safe='')}"], check=False)
        return {"ok": True}

    @app.post("/api/meetings/{meeting_id}/unarchive")
    def unarchive(meeting_id: str):
        _meeting_dir(meeting_id)
        library.unarchive_meeting(meeting_id)
        return {"ok": True}

    @app.post("/api/meetings/{meeting_id}/title")
    def title(meeting_id: str, request: NameRequest):
        if not request.name.strip():
            raise HTTPException(400, "the title cannot be blank")
        return {"title": pipeline.rename_meeting(_meeting_dir(meeting_id), request.name)}

    @app.post("/api/meetings/{meeting_id}/project")
    def project(meeting_id: str, request: NameRequest):
        pipeline.set_project(_meeting_dir(meeting_id), request.name)
        return {"project": library.meeting_project(meeting_id)}

    @app.get("/api/lookups")
    def lookups():
        return {"teams": directory.lookup_names("teams"), "organizations": directory.lookup_names("organizations"),
                "projects": projects.all_projects(), "default_backend": notes.default_backend(),
                "claude_ready": bool(os.environ.get("ANTHROPIC_API_KEY"))}

    @app.post("/api/people/{person_id}/details")
    def details(person_id: str, request: DetailsRequest):
        if not library.person_details(person_id):
            raise HTTPException(404, "no such person")
        try:
            library.set_person_details(person_id, organizations=request.organizations, teams=request.teams)
        except ValueError as e:
            raise HTTPException(400, str(e))
        pipeline.rerender_people([person_id])
        return library.person_details(person_id)

    @app.get("/api/people")
    def people():
        return _records(library.people_summary())

    @app.get("/api/home")
    def home():
        return library.home_stats(owner=os.environ.get("MIC_OWNER"))

    @app.get("/api/people/{person_id}/meetings")
    def person_meetings(person_id: str):
        return _records(library.person_meetings(person_id))

    @app.post("/api/people/{person_id}/rename")
    def rename(person_id: str, request: NameRequest):
        try:
            pipeline.rename_person(person_id, request.name)
        except ValueError as err:
            raise HTTPException(400, str(err))
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def forward():
        # to-dos added to things before the mac app link to the old web version, as
        # http://127.0.0.1:8765/#/transcript/ID/at/SECONDS. the part after # never reaches the
        # server, so this page reads it and hands the meeting to the mac app
        return FORWARD_PAGE

    return app
