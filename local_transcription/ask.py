"""ask questions about past meetings, answered by claude from the meeting library.

claude gets a few tools over the library: find meetings by person, project, title, or
date, read a meeting's note, read its transcript, and search every transcript for words.
it looks things up as it needs them and answers with links to the meetings it used. the
notes and transcript excerpts it reads are sent to the anthropic api, audio and voice
profiles never are.
"""

import functools
import json
import os
import re
from pathlib import Path

from local_transcription import library, pipeline, plaud, settings

# config
MAX_OUTPUT_TOKENS = 16000
# every tool call is a round trip, a question that needs more than this is cut off
MAX_TURNS = 20
# a transcript read in one go stops here, the rest is fetched by time
TRANSCRIPT_MAX_CHARS = 80000
SEARCH_MAX_HITS = 40
SYSTEM = """You answer questions about {owner}'s past meetings, using tools that read their meeting library: notes (summary, decisions, action items), full transcripts, and who attended. Today is {today}.

Look things up rather than guessing. Notes are the quickest source; read the transcript when the question needs exact wording, commitments, or detail a note leaves out. A meeting's people list comes from the people tagged in it, so someone may have spoken without being listed. Names in questions are often first names only; find_meetings matches any part of a name, and when that matches more than one person, say who you took it to be.

When you mention a meeting, link it as [title, date](meeting:MEETING_ID). To point at a moment in a recorded meeting, link the time as [12:34](meeting:MEETING_ID@SECONDS). Keep answers as short as the question allows, in plain markdown, and say plainly when the library doesn't have the answer."""

TOOLS = [
    {
        "name": "find_meetings",
        "description": "List meetings, newest first, filtered by any mix of person, project, words in the title, and date range. Returns each meeting's id, title, date, project, people, and whether it has a transcript. With no filters it returns the most recent meetings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "person": {"type": "string", "description": "Any part of a person's name, such as a first name."},
                "project": {"type": "string", "description": "Any part of a project name."},
                "title": {"type": "string", "description": "Words that appear in the meeting title."},
                "since": {"type": "string", "description": "Earliest date, YYYY-MM-DD."},
                "until": {"type": "string", "description": "Latest date, YYYY-MM-DD."},
                "limit": {"type": "integer", "description": "How many to return, 20 by default."},
            },
        },
    },
    {
        "name": "read_note",
        "description": "Read a meeting's note: its summary, decisions, action items, and properties, without the transcript.",
        "input_schema": {
            "type": "object",
            "properties": {"meeting_id": {"type": "string"}},
            "required": ["meeting_id"],
        },
    },
    {
        "name": "read_transcript",
        "description": "Read a meeting's transcript as timestamped lines with speaker names. Long meetings can be read a stretch at a time with start_minute and end_minute.",
        "input_schema": {
            "type": "object",
            "properties": {
                "meeting_id": {"type": "string"},
                "start_minute": {"type": "number"},
                "end_minute": {"type": "number"},
            },
            "required": ["meeting_id"],
        },
    },
    {
        "name": "search_transcripts",
        "description": "Search every transcript for lines containing all the given words (case-insensitive), newest meetings first. Can be narrowed by person, project, or date like find_meetings. Returns matching lines with their meeting, time, and speaker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "words": {"type": "string", "description": "Words that must all appear in a line."},
                "person": {"type": "string"},
                "project": {"type": "string"},
                "since": {"type": "string"},
                "until": {"type": "string"},
            },
            "required": ["words"],
        },
    },
]


# helpers
def _clock(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}" if seconds >= 3600 \
        else f"{seconds // 60}:{seconds % 60:02d}"


def _meetings():
    """every meeting with its people, newest first."""
    meetings = library.meetings()
    people = library.meeting_people()
    rows = []
    for m in meetings.itertuples():
        rows.append({"meeting_id": m.meeting_id, "title": m.title or m.meeting_id, "date": m.date,
                     "project": m.project if isinstance(m.project, str) else None,
                     "people": people.get(m.meeting_id, []),
                     "transcript": (pipeline.OUTPUT_DIR / m.meeting_id / "segments.json").exists()
                     or bool(m.sent_path), "sent_path": m.sent_path if isinstance(m.sent_path, str) else None})
    return rows


def _filter(rows, person=None, project=None, title=None, since=None, until=None):
    def has(text, part):
        return part.lower() in (text or "").lower()

    return [r for r in rows
            if (not person or any(has(n, person) for n in r["people"]))
            and (not project or has(r["project"], project))
            and (not title or all(has(r["title"], w) for w in title.split()))
            and (not since or (r["date"] or "") >= since)
            and (not until or (r["date"] or "") <= until)]


@functools.lru_cache(maxsize=512)
def _lines(meeting_id, stamp):
    """(seconds or None, speaker, text) for each transcript line. a recorded meeting reads
    its segments, an imported one the transcript in its note, which has no times. stamp is
    the source's modification time, so an edit reads afresh."""
    out_dir = pipeline.OUTPUT_DIR / meeting_id
    if (out_dir / "segments.json").exists():
        segments = json.loads((out_dir / "segments.json").read_text())
        return tuple((s.get("start"), s.get("name") or s.get("speaker"), s.get("text", "")) for s in segments)
    path = library.meeting_sent_path(meeting_id)
    if path and Path(path).exists():
        return tuple((None, label, text) for label, text in plaud.note_lines(Path(path).read_text(errors="replace")))
    return ()


def transcript_lines(meeting_id):
    out_dir = pipeline.OUTPUT_DIR / meeting_id
    source = out_dir / "segments.json"
    if not source.exists():
        path = library.meeting_sent_path(meeting_id)
        source = Path(path) if path else None
    stamp = source.stat().st_mtime if source and source.exists() else 0
    return _lines(meeting_id, stamp)


def _format(lines):
    return "\n".join(f"[{_clock(t)}] {who}: {text}" if t is not None else f"{who}: {text}" for t, who, text in lines)


# tools
def find_meetings(person=None, project=None, title=None, since=None, until=None, limit=20):
    rows = _filter(_meetings(), person, project, title, since, until)
    shown = rows[:max(1, min(int(limit or 20), 100))]
    found = {"total": len(rows), "meetings": [{k: r[k] for k in ("meeting_id", "title", "date", "project", "people", "transcript")}
                                              for r in shown]}
    if person:
        names = sorted({n for r in rows for n in r["people"] if person.lower() in n.lower()})
        found["people_matched"] = names
    return found


def read_note(meeting_id):
    out_dir = pipeline.OUTPUT_DIR / meeting_id
    path = library.meeting_sent_path(meeting_id)
    # the obsidian copy is the one edited by hand, the app's is the draft before sending
    source = Path(path) if path and Path(path).exists() else out_dir / "note.md"
    if not source.exists():
        return f"{meeting_id} has no note yet"
    text = source.read_text(errors="replace")
    return re.split(r"^## Transcript", text, maxsplit=1, flags=re.MULTILINE)[0].strip()


def read_transcript(meeting_id, start_minute=None, end_minute=None):
    lines = transcript_lines(meeting_id)
    if not lines:
        return f"{meeting_id} has no transcript"
    if start_minute is not None or end_minute is not None:
        start, end = (start_minute or 0) * 60, (end_minute if end_minute is not None else 1e9) * 60
        lines = tuple(line for line in lines if line[0] is None or start <= line[0] < end)
    text = _format(lines)
    if len(text) > TRANSCRIPT_MAX_CHARS:
        cut = text[:TRANSCRIPT_MAX_CHARS].rsplit("\n", 1)[0]
        last = re.findall(r"^\[([\d:]+)\]", cut, flags=re.MULTILINE)
        return cut + f"\n\n[cut off at {last[-1] if last else 'this point'}, read on with start_minute]"
    return text


def search_transcripts(words, person=None, project=None, since=None, until=None):
    terms = [w.lower() for w in words.split() if w.strip()]
    if not terms:
        return "give at least one word"
    hits, searched = [], 0
    for r in _filter(_meetings(), person, project, None, since, until):
        if not r["transcript"]:
            continue
        searched += 1
        for t, who, text in transcript_lines(r["meeting_id"]):
            if all(term in text.lower() for term in terms):
                hits.append({"meeting_id": r["meeting_id"], "title": r["title"], "date": r["date"],
                             "time": _clock(t) if t is not None else None,
                             "seconds": int(t) if t is not None else None, "speaker": who, "text": text})
                if len(hits) >= SEARCH_MAX_HITS:
                    return {"meetings_searched": searched, "hits": hits, "note": "stopped at the hit limit, narrow the search"}
    return {"meetings_searched": searched, "hits": hits}


HANDLERS = {"find_meetings": find_meetings, "read_note": read_note,
            "read_transcript": read_transcript, "search_transcripts": search_transcripts}


def describe(name, args):
    """a short line for the progress list, such as 'searching transcripts for budget'."""
    if name == "find_meetings":
        parts = [f"with {args['person']}" if args.get("person") else "", f"on {args['project']}" if args.get("project") else "",
                 f"titled {args['title']}" if args.get("title") else ""]
        return "finding meetings " + " ".join(p for p in parts if p) if any(parts) else "finding recent meetings"
    if name == "search_transcripts":
        return f"searching transcripts for \"{args.get('words', '')}\""
    what = "note" if name == "read_note" else "transcript"
    return f"reading the {what} of {args.get('meeting_id', '')}"


def saveable(messages):
    """the history as plain json. claude's reply blocks, thinking included, keep every field
    they came with, since they have to go back unchanged when the conversation carries on."""
    def block(b):
        return b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else b

    return [{**m, "content": [block(b) for b in m["content"]] if isinstance(m["content"], list) else m["content"]}
            for m in messages]


def _client():
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set in .env")
    return anthropic.Anthropic()


def title(question, answer_text):
    """a few words naming a conversation from its first question and answer."""
    client, model = _client(), settings.claude_model()
    response = client.beta.messages.create(
        model=model, max_tokens=1000, output_config={"effort": "low"},
        system="Name this conversation about the user's meetings in 3 to 6 words, like a document title. Reply with the title only, no quotes or punctuation at the end.",
        messages=[{"role": "user", "content": f"Question: {question}\n\nAnswer: {answer_text[:3000]}"}])
    library.record_usage(response.model, "title", response.usage, settings.cost(
        response.model, response.usage.input_tokens, response.usage.output_tokens,
        response.usage.cache_creation_input_tokens, response.usage.cache_read_input_tokens))
    text = "".join(b.text for b in response.content if b.type == "text").strip().strip('"').rstrip(".")
    return text[:80] or question[:60]


TODO_SYSTEM = """You turn an answer about {owner}'s meetings into to-dos for {owner}'s task manager. Today is {today}.

Include only actions assigned to {owner} or that {owner} took on, never things other people will do or points raised without anyone owning them. Write each title as a short imperative, like "Walk through the comment and review flow". In notes, say who asked for it and the detail needed to act on it, in a sentence or two, without links.

meeting_id is the meeting the action came from, from the answer's meeting links, and seconds the moment in it when the answer links one, else -1. project_id is the id of the project below that fits the action or its meeting's project best, or "" when none fits. when is "today", "tomorrow", or a YYYY-MM-DD date only when the answer says when to start, else "". deadline is a YYYY-MM-DD date only when the answer gives one, such as "before Wednesday's demo", else "".

Projects:
{projects}"""


MEETING_TODO_SYSTEM = """You read the transcript of one of {owner}'s meetings and list {owner}'s to-dos from it for their task manager. Today is {today}.

Include only tasks someone asked {owner} to do, and tasks {owner} committed to doing ("I'll send that over", "let me look into it"). Leave out tasks for other people, general "we should" ideas nobody took on, and things already done in the meeting. Speaker names come from voice matching and can be wrong now and then, so go by who is being addressed as well as who is talking. If {owner} has no tasks, return an empty list.

Write each title as a short imperative, like "Send Maya the dashboard mockups". In notes, say who asked for it, or that {owner} offered, and the detail needed to act on it, in a sentence or two. seconds is the time of the line where it was asked or offered, from the [m:ss] stamps, else -1. project_id is the id of the project below that fits the task or the meeting's project best, or "" when none fits. when is "today", "tomorrow", or a YYYY-MM-DD date only when the meeting says when to start, else "". deadline is a YYYY-MM-DD date only when one is given, such as "by Friday", else "".

Projects:
{projects}"""


def _todos(system, content, projects, purpose):
    """ask claude for to-dos in a fixed shape. returns them as dicts with unknown projects
    dropped."""
    from pydantic import BaseModel

    class Todo(BaseModel):
        title: str
        notes: str
        meeting_id: str
        seconds: int
        project_id: str
        when: str
        deadline: str

    class Todos(BaseModel):
        todos: list[Todo]

    client, model = _client(), settings.claude_model()
    response = client.messages.parse(
        model=model, max_tokens=8000, output_format=Todos, system=system,
        messages=[{"role": "user", "content": content}])
    library.record_usage(response.model, purpose, response.usage, settings.cost(
        response.model, response.usage.input_tokens, response.usage.output_tokens,
        response.usage.cache_creation_input_tokens, response.usage.cache_read_input_tokens))
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RuntimeError("Claude couldn't pull to-dos from this")
    known = {p["id"] for p in projects}
    return [{**t.model_dump(), "project_id": t.project_id if t.project_id in known else ""}
            for t in response.parsed_output.todos]


def _project_list(projects):
    return "\n".join(f"- {p['id']}: {p['name']} ({p['area']})" for p in projects) or "(none)"


def extract_todos(question, answer_text, meetings, projects, owner, today):
    """the actions assigned to the owner in an answer, as to-do dicts. meetings is
    {meeting_id: {title, date, project}} for the meetings the answer used, projects the
    task manager's projects as dicts of id, name, and area."""
    used = "\n".join(f"- {m}: {v['title']}, {v['date']}, project {v.get('project') or 'none'}" for m, v in meetings.items())
    todos = _todos(TODO_SYSTEM.format(owner=owner or "the user", today=today, projects=_project_list(projects)),
                   f"Question: {question}\n\nAnswer:\n{answer_text}\n\nMeetings in the answer:\n{used or '(none)'}",
                   projects, "todos")
    return [{**t, "meeting_id": t["meeting_id"] if t["meeting_id"] in meetings else ""} for t in todos]


def meeting_todos(meeting_id, meeting, projects, owner, today, already=()):
    """the owner's to-dos in one meeting, read from its whole transcript. meeting is
    {title, date, project, people}, already the titles of to-dos sent from it before."""
    lines = transcript_lines(meeting_id)
    if not lines:
        raise RuntimeError("this meeting has no transcript to read")
    content = (f"Meeting: {meeting['title']}, {meeting['date']}\nProject: {meeting.get('project') or 'none'}\n"
               f"People: {', '.join(meeting.get('people') or []) or 'unknown'}\n\nTranscript:\n{_format(lines)}")
    if already:
        content += "\n\nAlready in the task manager, so leave these out:\n" + "\n".join(f"- {t}" for t in already)
    todos = _todos(MEETING_TODO_SYSTEM.format(owner=owner or "the user", today=today, projects=_project_list(projects)),
                   content, projects, "meeting todos")
    return [{**t, "meeting_id": meeting_id} for t in todos]


# main
def answer(history, question, owner, today, progress=None):
    """continue a conversation with a new question. history is the message list from the
    last call, kept whole so its thinking blocks pass back unchanged. returns (answer text,
    new history, ids of the meetings the tools touched)."""
    client = _client()
    model = settings.claude_model()
    # a declined request is retried on a fallback model instead of stopping, where offered
    fallback = ({"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
                if model in settings.FALLBACK_MODELS else {})
    messages = [*history, {"role": "user", "content": question}]
    touched = []
    for _ in range(MAX_TURNS):
        response = client.beta.messages.create(
            model=model, max_tokens=MAX_OUTPUT_TOKENS,
            system=SYSTEM.format(owner=owner or "the user", today=today),
            tools=TOOLS, messages=messages,
            thinking={"type": "adaptive"}, output_config={"effort": "medium"},
            # the conversation so far is reused on every round trip
            cache_control={"type": "ephemeral"},
            **fallback,
        )
        library.record_usage(response.model, "ask", response.usage, settings.cost(
            response.model, response.usage.input_tokens, response.usage.output_tokens,
            response.usage.cache_creation_input_tokens, response.usage.cache_read_input_tokens))
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined to answer this one")
        calls = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not calls:
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            if response.stop_reason == "max_tokens":
                text += "\n\n(The answer was cut off at the length limit.)"
            return text, messages, list(dict.fromkeys(touched))
        results = []
        for call in calls:
            args = call.input if isinstance(call.input, dict) else {}
            if progress:
                progress(describe(call.name, args))
            try:
                result = HANDLERS[call.name](**args)
                touched += [args["meeting_id"]] if "meeting_id" in args else []
                if isinstance(result, dict):
                    touched += [m["meeting_id"] for m in result.get("meetings", []) + result.get("hits", [])]
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": result if isinstance(result, str) else json.dumps(result)})
            except Exception as err:
                results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                "content": f"{type(err).__name__}: {err}"})
        messages.append({"role": "user", "content": results})
    raise RuntimeError(f"stopped after {MAX_TURNS} lookups without an answer, try a narrower question")
