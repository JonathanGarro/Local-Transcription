"""local web api for the app window. binds to 127.0.0.1 only, nothing is reachable from
other machines. one worker thread runs recordings through the pipeline one at a time so
jobs never compete for the gpu.
"""

import io
import json
import os
import queue
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
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from local_transcription import audio, library, notes, pipeline, projects, render

# config
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
WEB_DIR = Path(__file__).parent / "web"
AUDIO_TYPES = {".m4a", ".mp3", ".wav"}
CHANNELS = {"left": 0, "right": 1}
# progress messages from pipeline.run, in order, mapped to a share of the job
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
        self._update(job, status="done", step="needs review" if review else "done",
                     progress=1.0, meeting_id=out_dir.name)
        self.auto_note(out_dir.name)

    def auto_note(self, meeting_id):
        """summarize a meeting once every speaker is named, unless it has a note or one is queued."""
        out_dir = pipeline.OUTPUT_DIR / meeting_id
        if (out_dir / "note.json").exists():
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


class NameRequest(BaseModel):
    name: str


class NoteRequest(BaseModel):
    backend: str = "local"
    # None lets the model suggest one of the library's projects
    project: str | None = None


class SendRequest(BaseModel):
    replace: bool = False


class NoteText(BaseModel):
    markdown: str


class DetailsRequest(BaseModel):
    organization: str = ""
    teams: list[str] = []


# main
def create_app():
    app = FastAPI(title="Local Transcription", docs_url=None, redoc_url=None)
    jobs = Jobs()
    pipeline.register_existing()
    # wavs rebuilt to replay an archived meeting are not kept between launches
    for meeting_id in library.meetings().query("archived.notna()")["meeting_id"] if len(library.meetings()) else []:
        pipeline.drop_audio(pipeline.OUTPUT_DIR / meeting_id)

    @app.get("/api/state")
    def state():
        meetings = _records(library.meetings())
        for m in meetings:
            m["note"] = (pipeline.OUTPUT_DIR / m["meeting_id"] / "note.json").exists()
        return {"jobs": jobs.snapshot(), "meetings": meetings,
                "owner": os.environ.get("MIC_OWNER")}

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
                "archived": info.get("archived"),
                "owner": meta.get("owner"), "speakers": result}

    @app.get("/api/meetings/{meeting_id}/clip")
    def clip(meeting_id: str, speaker: str, start: float, end: float):
        out_dir = _meeting_dir(meeting_id)
        meta = pipeline.load_run(out_dir)[0]
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
                "job": running[0] if running else None}

    @app.post("/api/meetings/{meeting_id}/note")
    def write_note(meeting_id: str, request: NoteRequest):
        _meeting_dir(meeting_id)
        if request.backend not in notes.BACKENDS:
            raise HTTPException(400, f"unknown backend {request.backend}")
        if request.backend == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
            raise HTTPException(400, "add ANTHROPIC_API_KEY to .env and restart the app to use Claude")
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

    @app.post("/api/meetings/{meeting_id}/open-obsidian")
    def open_obsidian(meeting_id: str):
        _meeting_dir(meeting_id)
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
        return {"teams": library.lookup_names("teams"), "organizations": library.lookup_names("organizations"),
                "projects": projects.all_projects(), "default_backend": notes.default_backend(),
                "claude_ready": bool(os.environ.get("ANTHROPIC_API_KEY"))}

    @app.post("/api/people/{person_id}/details")
    def details(person_id: str, request: DetailsRequest):
        if not library.person_details(person_id):
            raise HTTPException(404, "no such person")
        library.set_person_details(person_id, organization=request.organization, teams=request.teams)
        pipeline.rerender_people([person_id])
        return library.person_details(person_id)

    @app.get("/api/people")
    def people():
        return _records(library.people_summary())

    @app.post("/api/people/{person_id}/rename")
    def rename(person_id: str, request: NameRequest):
        try:
            pipeline.rename_person(person_id, request.name)
        except ValueError as err:
            raise HTTPException(400, str(err))
        return {"ok": True}

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
