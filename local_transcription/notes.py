"""meeting notes, laid out by a note template (see templates.py). the standard one is the
obsidian layout: frontmatter, agenda, discussion, transcript, and, once the owner's to-dos
are sent to the to-do app from the app, action items linking to them.

a language model writes the template's written parts as json. the heading is the meeting's
own title, the project is the one picked in the app, and the frontmatter and transcript
block are filled in by code, so the transcript is never retyped by the model and the people
come from the library and their teams and organizations from obsidian.

two backends: "local" runs an mlx model on this mac and nothing leaves the machine.
"claude" sends the transcript text (never audio or voice profiles) to the anthropic api.
"""

import difflib
import json
import os
import re
from datetime import datetime

from local_transcription import keychain, library, settings, templates, todos

# config
BACKENDS = ["local", "claude"]
DEFAULT_LOCAL_MODEL = "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit"
MAX_OUTPUT_TOKENS = 4000
LOCAL_TEMPERATURE = 0.2
# how alike a transcript word and a word of a vocabulary term are for the term to go in the
# spelling list, so "gal prep" brings in GAL prep but a meeting that never comes near it doesn't
TERM_MATCH = 0.75
INSTRUCTIONS = """You write meeting notes from a speaker-labeled transcript. Reply with a single JSON object and nothing else. It has these keys:

{keys}

Rules:
- Plain text only. No bold, italics, headings, or bullet characters inside the strings.
- Never use em dashes. Use commas, periods, or parentheses instead.
- Do not invent facts that are not in the transcript. Write only what was said. A short transcript gets a short note, and a part with nothing in the transcript for it stays empty.
- The transcript comes from speech recognition. Fix obvious mis-hearings of names and terms, using the spelling list when one is given. The list only shows how words are spelled. Never mention a name or term from it that the transcript doesn't bring up.
- "Unknown" speakers were not identified. Refer to them by what they said rather than guessing a name."""
# how each kind of written part is described to the model
SHAPES = {
    "paragraph": "a string. {}",
    "bullets": "a list of strings, one sentence each, or an empty list when there are none. {}",
    "topics": 'a list of topics, each an object {{"topic": "...", "points": ["...", "..."]}}, with the points one sentence each. {}',
}


# helpers
def _models():
    return {"local_model": os.environ.get("LOCAL_SUMMARY_MODEL", DEFAULT_LOCAL_MODEL),
            "claude_model": settings.claude_model()}


def default_backend():
    return settings.summary_backend()


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


def _topics(value):
    topics = []
    for topic in value if isinstance(value, list) else []:
        if isinstance(topic, dict):
            points = [_plain(p) for p in topic.get("points") or [] if _plain(p)]
            topics.append({"topic": _plain(topic.get("topic")), "points": points})
        elif _plain(topic):
            topics.append({"topic": _plain(topic), "points": []})
    return topics


def clean_summary(raw, template=templates.STANDARD):
    """the model's reply as {key: value} for each written part of the template."""
    cleaned = {}
    for part in templates.sections(template):
        value = raw.get(part["key"])
        if part["kind"] == "paragraph":
            cleaned[part["key"]] = _plain(" ".join(map(str, value)) if isinstance(value, list) else value)
        elif part["kind"] == "bullets":
            items = value if isinstance(value, list) else [value] if value else []
            cleaned[part["key"]] = [_plain(v) for v in items if _plain(v)]
        else:
            cleaned[part["key"]] = _topics(value)
    return cleaned


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
            people[row.speaker] = {"person_id": None, "name": row.name, "organizations": [], "teams": []}
    return people


def transcript_lines(segments, speakers):
    """[Full Name] text lines, joining back-to-back segments from the same person."""
    people = speaker_people(speakers)
    unknown, labels, lines, previous = 0, {}, [], None
    for speaker in dict.fromkeys(segments["speaker"]):
        if speaker in people:
            labels[speaker] = people[speaker]["name"]
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
def relevant_terms(terms, lines):
    """the vocabulary terms with a word like one in the transcript. the rest would only give
    a model names to make a meeting out of when the transcript has little in it."""
    words = set(re.findall(r"[a-z0-9']+", " ".join(lines).lower()))
    def near(word):
        return word in words or bool(difflib.get_close_matches(word, words, n=1, cutoff=TERM_MATCH))
    return [t for t in terms if any(near(w) for w in re.findall(r"[a-z0-9']+", t.lower()))]


def build_messages(lines, owner, meeting_date, attendees, terms, template=templates.STANDARD):
    keys = "\n".join(f'"{part["key"]}": ' + SHAPES[part["kind"]].format(part["instructions"]).strip()
                     for part in templates.sections(template))
    system = INSTRUCTIONS.format(keys=keys)
    header = [f"Meeting date: {meeting_date}", f"Attendees: {', '.join(attendees)}",
              f"These notes are written for {owner}."]
    terms = relevant_terms(terms, lines)
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

    keychain.load()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("no Anthropic API key, add one in Settings or use the local backend")
    model_name = _models()["claude_model"]
    if progress:
        progress(f"sending the transcript text to {model_name}")
    client = anthropic.Anthropic()
    message = client.messages.create(model=model_name, max_tokens=MAX_OUTPUT_TOKENS, system=system,
                                     messages=[{"role": "user", "content": user}])
    usage = message.usage
    library.record_usage(message.model, "note", usage, settings.cost(
        message.model, usage.input_tokens, usage.output_tokens,
        usage.cache_creation_input_tokens, usage.cache_read_input_tokens))
    return "".join(block.text for block in message.content if block.type == "text"), model_name


def summarize(lines, owner, meeting_date, attendees, terms, backend="local", progress=None,
              template_name=templates.DEFAULT):
    """ask the chosen model for the note sections, in the named template. returns the summary
    dict, with the written parts under sections and the template it was written with."""
    if backend not in BACKENDS:
        raise ValueError(f"backend must be one of {BACKENDS}")
    template = templates.text(template_name)
    system, user = build_messages(lines, owner, meeting_date, attendees, terms, template)
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
    summary = {"sections": clean_summary(raw, template), "template": template_name}
    if template_name not in templates.BUILT_IN:
        # a copy of your own template, so editing or deleting it later leaves this note as it is
        summary["template_text"] = template
    summary.update(backend=backend, model=model_name, created=datetime.now().isoformat(timespec="seconds"))
    return summary


# main
def _written(kind, value):
    """a written part as note lines."""
    if kind == "paragraph":
        return [value] if value else []
    if kind == "bullets":
        return [f"- {item}" for item in value or []]
    lines = []
    for topic in value or []:
        lines.append(f"- {topic['topic']}")
        lines += [f"\t- {point}" for point in topic["points"]]
    return lines


def _fill_line(line, values, front, parts):
    """one template line as note lines, and whether it had a placeholder. values holds what
    code fills in, parts the written parts still to place, in order."""
    matches = list(templates.PLACEHOLDER.finditer(line))
    if not matches:
        return [line], False
    if front:
        key = re.match(r"^([^:]+):\s*\{\{\s*(\w+)\s*\}\}\s*$", line)
        if key:
            name, value = key.group(2), values[key.group(2)]
            if name in templates.LISTS:
                return _yaml_list(key.group(1), value), True
            if name == "project":
                return [f"{key.group(1)}: {wikilink(value)}" if value else f"{key.group(1)}:"], True
            if name in ("transcript_link", "title"):
                return [f"{key.group(1)}: {json.dumps(value, ensure_ascii=False)}"], True
            return [f"{key.group(1)}: {value}".rstrip()], True
    if len(matches) == 1 and line.strip() == matches[0].group(0):
        name = matches[0].group(1)
        if name in templates.WRITTEN:
            part = parts.pop(0)
            return _written(part["kind"], part["value"]), True
        if name == "transcript":
            return ["```"] + values["transcript"] + ["```"], True
        if name == "action_items":
            return values["action_items"], True

    def inline(match):
        name = match.group(1)
        if name in templates.WRITTEN:
            return " ".join(_written("paragraph", parts.pop(0)["value"]))
        if name in templates.LISTS:
            return ", ".join(f"[[{v}]]" for v in values[name])
        if name == "project":
            return f"[[{values['project']}]]" if values["project"] else ""
        return str(values[name] or "")

    return [templates.PLACEHOLDER.sub(inline, line)], True


def fill(template, values, written):
    """the template with its placeholders filled in. values is {placeholder: value} for the
    parts code fills, written {key: value} for the parts the model wrote. a section whose
    placeholders all came out empty is left out, heading and all."""
    rows = template.rstrip("\n").split("\n")
    end = templates.frontmatter_end(rows)
    parts = [{**part, "value": written.get(part["key"])} for part in templates.sections(template)]
    out = []
    for line in rows[:end + 1] if end else []:
        out += _fill_line(line, values, True, parts)[0]
    # the rest in sections, each a heading and the lines up to the next one
    chunks = [[]]
    for line in rows[end + 1 if end else 0:]:
        if templates.HEADING.match(line):
            chunks.append([])
        chunks[-1].append(line)
    for chunk in chunks:
        heading = chunk[:1] if chunk and templates.HEADING.match(chunk[0]) else []
        body, placed = [], False
        for line in chunk[len(heading):]:
            lines, had = _fill_line(line, values, False, parts)
            body += lines
            placed = placed or had
        if heading and placed and not any(line.strip() for line in body):
            continue
        out += [line for h in heading for line in _fill_line(h, values, False, parts)[0]] + body
    return "\n".join(out).rstrip("\n") + "\n"


def heading(template, date, title):
    """the template's first "# " heading for a meeting, or None when it has none."""
    for line in template.split("\n"):
        if line.startswith("# "):
            return templates.PLACEHOLDER.sub(lambda m: {"date": date, "title": title}.get(m.group(1), ""), line)
    return None


def render_note(summary, date, title, segments, speakers, owner_id, project, transcript_ref):
    """the note markdown, in the template it was written with. people, teams, and
    organizations exclude the owner, who is on every note."""
    people = speaker_people(speakers)
    order = [s for s in dict.fromkeys(segments["speaker"]) if s in people]
    others = [people[s] for s in order if people[s]["person_id"] != owner_id]
    values = {"date": date, "title": title, "project": project, "transcript_link": transcript_ref,
              "people": list(dict.fromkeys(p["name"] for p in others)),
              "teams": list(dict.fromkeys(t for p in others for t in p["teams"])),
              "organizations": list(dict.fromkeys(o for p in others for o in p["organizations"])),
              "transcript": transcript_lines(segments, speakers), "action_items": action_item_lines(summary)}
    # notes from before templates kept the agenda and discussion at the top level
    return fill(templates.for_note(summary), values, summary.get("sections") or summary)


def action_item_lines(summary):
    """the owner's to-dos sent to the to-do app from the app, each a link to its to-do, which
    is where it gets ticked off. none until some are sent."""
    # square brackets would end the link text early
    return ["- [{}]({})".format(re.sub(r"[\[\]]", "", item["title"]), todos.link(item["id"], item.get("app")))
            for item in summary.get("things_items") or []]