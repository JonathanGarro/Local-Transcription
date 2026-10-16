"""meeting notes in the obsidian layout: frontmatter, agenda, discussion, transcript, and
action items.

a language model writes the agenda, discussion, and action items as json. the heading is
the meeting's own title, the project is the one picked in the app, and the frontmatter and
transcript block are filled in by code, so the transcript is never retyped by the model and
the people, teams, and organizations come from the library.

two backends: "local" runs an mlx model on this mac and nothing leaves the machine.
"claude" sends the transcript text (never audio or voice profiles) to the anthropic api.
"""

import json
import os
import re
from datetime import datetime

from local_transcription import library

# config
BACKENDS = ["local", "claude"]
DEFAULT_LOCAL_MODEL = "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit"
DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"
MAX_OUTPUT_TOKENS = 4000
LOCAL_TEMPERATURE = 0.2
INSTRUCTIONS = """You write meeting notes from a speaker-labeled transcript. Reply with a single JSON object and nothing else. It has these keys:

"agenda": a few sentences describing what the meeting was about.
"discussion": a list of topics in the order they came up. Each topic is an object {{"topic": "...", "points": ["...", "..."]}}. The points summarize the key things said, decided, or raised under that topic, one sentence each.
"action_items": the top three to five to-dos that are specifically for {owner}, each a short imperative sentence with enough context to act on later. Use fewer if the meeting produced fewer.

Rules:
- Plain text only. No bold, italics, headings, or bullet characters inside the strings.
- Never use em dashes. Use commas, periods, or parentheses instead.
- Do not invent facts that are not in the transcript.
- The transcript comes from speech recognition. Fix obvious mis-hearings of names and terms, using the spelling list when one is given.
- "Unknown" speakers were not identified. Refer to them by what they said rather than guessing a name."""


# helpers
def _models():
    return {"local_model": os.environ.get("LOCAL_SUMMARY_MODEL", DEFAULT_LOCAL_MODEL),
            "claude_model": os.environ.get("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL)}


def default_backend():
    backend = os.environ.get("SUMMARY_BACKEND", "local")
    return backend if backend in BACKENDS else "local"


def wikilink(name):
    # single quoted, as obsidian writes list properties itself
    return "'[[" + str(name).replace("'", "''") + "]]'"


def _yaml_list(key, values):
    """a list property the way obsidian stores it: one '- item' line per value."""
    if not values:
        return [f"{key}:"]
    return [f"{key}:"] + [f"- {wikilink(v)}" for v in values]


def _plain(text):
    # the model is told to avoid these, this catches any that slip through
    text = str(text or "").replace(" — ", ", ").replace("—", ", ").replace("–", "-")
    return re.sub(r"\*\*|__", "", text).strip()


def parse_json(text):
    """the first json object in a model reply, ignoring code fences or stray text."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"the model did not return json: {text[:200]!r}")
    return json.loads(text[start:end + 1])


def clean_summary(raw):
    discussion = []
    for topic in raw.get("discussion") or []:
        if isinstance(topic, dict):
            points = [_plain(p) for p in topic.get("points") or [] if _plain(p)]
            discussion.append({"topic": _plain(topic.get("topic")), "points": points})
        elif _plain(topic):
            discussion.append({"topic": _plain(topic), "points": []})
    return {"agenda": _plain(raw.get("agenda")), "discussion": discussion,
            "action_items": [_plain(a) for a in raw.get("action_items") or [] if _plain(a)]}


# people
def speaker_people(speakers, owner_id=None):
    """{speaker: details} for every named speaker, using library details where linked."""
    people = {}
    for row in speakers.itertuples():
        person_id = getattr(row, "person_id", None)
        details = library.person_details(person_id) if isinstance(person_id, str) else None
        if details:
            people[row.speaker] = details
        elif isinstance(row.name, str) and row.name:
            people[row.speaker] = {"person_id": None, "name": row.name, "full_name": row.name,
                                   "organization": None, "teams": []}
    return people


def transcript_lines(segments, speakers):
    """[Full Name] text lines, joining back-to-back segments from the same person."""
    people = speaker_people(speakers)
    unknown, labels, lines, previous = 0, {}, [], None
    for speaker in dict.fromkeys(segments["speaker"]):
        if speaker in people:
            labels[speaker] = people[speaker]["full_name"]
        else:
            unknown += 1
            labels[speaker] = f"Unknown {unknown}"
    for row in segments.itertuples():
        label = labels[row.speaker]
        if label == previous:
            lines[-1] += " " + row.text.strip()
        else:
            lines.append(f"[{label}] {row.text.strip()}")
            previous = label
    return lines


# models
def build_messages(lines, owner, meeting_date, attendees, terms):
    system = INSTRUCTIONS.format(owner=owner)
    header = [f"Meeting date: {meeting_date}", f"Attendees: {', '.join(attendees)}",
              f"These notes are written for {owner}."]
    if terms:
        header.append(f"Spelling list: {', '.join(terms)}")
    user = "\n".join(header) + "\n\nTranscript:\n" + "\n".join(lines)
    return system, user


def run_local(system, user, progress=None):
    from mlx_lm import load, stream_generate
    from mlx_lm.sample_utils import make_sampler

    model_name = _models()["local_model"]
    if progress:
        progress(f"loading {model_name.split('/')[-1]}")
    model, tokenizer = load(model_name)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    prompt = tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
    if progress:
        progress("writing the summary")
    text, count = "", 0
    for response in stream_generate(model, tokenizer, prompt, max_tokens=MAX_OUTPUT_TOKENS,
                                    sampler=make_sampler(temp=LOCAL_TEMPERATURE, top_p=0.9)):
        text += response.text
        count += 1
        if progress and count % 100 == 0:
            progress(f"  {count} tokens written")
    return text, model_name


def run_claude(system, user, progress=None):
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set in .env, add it or use the local backend")
    model_name = _models()["claude_model"]
    if progress:
        progress(f"sending the transcript text to {model_name}")
    client = anthropic.Anthropic()
    message = client.messages.create(model=model_name, max_tokens=MAX_OUTPUT_TOKENS, system=system,
                                     messages=[{"role": "user", "content": user}])
    return "".join(block.text for block in message.content if block.type == "text"), model_name


def summarize(lines, owner, meeting_date, attendees, terms, backend="local", progress=None):
    """ask the chosen model for the note sections. returns the cleaned summary dict."""
    if backend not in BACKENDS:
        raise ValueError(f"backend must be one of {BACKENDS}")
    system, user = build_messages(lines, owner, meeting_date, attendees, terms)
    runner = run_local if backend == "local" else run_claude
    text, model_name = runner(system, user, progress)
    try:
        raw = parse_json(text)
    except (ValueError, json.JSONDecodeError):
        if backend != "local":
            raise
        # small models occasionally break their json, one retry is usually enough
        if progress:
            progress("  the reply was not valid json, trying once more")
        text, model_name = runner(system, user + "\n\nReply with valid JSON only.", progress)
        raw = parse_json(text)
    summary = clean_summary(raw)
    summary.update(backend=backend, model=model_name, created=datetime.now().isoformat(timespec="seconds"))
    return summary


# main
def render_note(summary, date, title, segments, speakers, owner_id, project, transcript_ref):
    """the note markdown. people, teams, and organizations exclude the owner, who is on
    every note."""
    people = speaker_people(speakers)
    order = [s for s in dict.fromkeys(segments["speaker"]) if s in people]
    others = [people[s] for s in order if people[s]["person_id"] != owner_id]
    names = list(dict.fromkeys(p["full_name"] for p in others))
    teams = list(dict.fromkeys(t for p in others for t in p["teams"]))
    orgs = list(dict.fromkeys(p["organization"] for p in others if p["organization"]))

    lines = ["---", f"date: {date}"]
    lines += _yaml_list("people", names) + _yaml_list("teams", teams) + _yaml_list("organizations", orgs)
    lines += ["tags:", f"transcript: {json.dumps(transcript_ref, ensure_ascii=False)}",
              f"project: {wikilink(project)}" if project else "project:", "prep:", "---",
              f"# {date} - {title}", "", "## Agenda", "", summary["agenda"], "",
              "## Discussion", ""]
    for topic in summary["discussion"]:
        lines.append(f"- {topic['topic']}")
        lines += [f"\t- {point}" for point in topic["points"]]
    lines += ["", "## Transcript", "", "```"] + transcript_lines(segments, speakers) + ["```", "## Action Items", ""]
    lines += [f"- [ ] {item}" for item in summary["action_items"]]
    return "\n".join(lines) + "\n"
