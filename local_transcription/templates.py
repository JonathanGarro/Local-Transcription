"""note templates: the layout a meeting note is written in, as markdown with placeholders.

code fills in some placeholders and the model writes the others:

    {{date}} {{title}}                         the meeting's date and name
    {{people}} {{teams}} {{organizations}}     who was there, other than you, as obsidian links
    {{project}}                                the meeting's project, as a link
    {{transcript_link}}                        the wemmick:// link that opens the transcript
    {{transcript}}                             the transcript, in a code block
    {{action_items}}                           links to the to-dos sent from the meeting

    {{paragraph: what to write}}               a paragraph the model writes
    {{bullets: what to list}}                  a list the model writes, one sentence each
    {{topics: what to cover}}                  topics with points under each, two levels deep

a list placeholder alone after a frontmatter key, as in "people: {{people}}", becomes a list
property the way obsidian writes one. a section whose placeholders all come out empty, such
as action items before any to-dos are sent, is left out with its heading. the first "# "
heading names the note's file.

the built-in templates live here. your own are .md files in data/templates, named after the
template. a note remembers the template it was written with, so changing the choice in
settings only affects notes written after.
"""

import re

from local_transcription import paths

# config
TEMPLATES_DIR = paths.DATA_DIR / "templates"
DEFAULT = "Standard"
PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*(?::\s*(.*?))?\s*\}\}")
FILLED = {"date", "title", "people", "teams", "organizations", "project", "transcript_link", "transcript",
          "action_items"}
WRITTEN = {"paragraph", "bullets", "topics"}
LISTS = {"people", "teams", "organizations"}
# placeholders that become several lines, so they go on a line of their own
BLOCKS = {"transcript", "action_items", "bullets", "topics"}
HEADING = re.compile(r"^#{1,6} ")
NAME = re.compile(r"^[\w][\w '()&,.-]{0,60}$")

STANDARD = """---
date: {{date}}
people: {{people}}
teams: {{teams}}
organizations: {{organizations}}
tags:
transcript: {{transcript_link}}
project: {{project}}
prep:
---
# {{date}} - {{title}}

## Agenda

{{paragraph: A few sentences describing what the meeting was about.}}

## Discussion

{{topics: The topics in the order they came up. The points summarize the key things said, decided, or raised under each topic, one sentence each.}}

## Transcript

{{transcript}}
## Action Items

{{action_items}}
"""

SUMMARY = """---
date: {{date}}
people: {{people}}
project: {{project}}
transcript: {{transcript_link}}
---
# {{date}} - {{title}}

## Summary

{{paragraph: A short paragraph on what the meeting covered and where it landed.}}

## Key Points

{{bullets: The most important things said, decided, or raised.}}

## Action Items

{{action_items}}

## Transcript

{{transcript}}
"""

DECISIONS = """---
date: {{date}}
people: {{people}}
project: {{project}}
transcript: {{transcript_link}}
---
# {{date}} - {{title}}

## Summary

{{paragraph: Two or three sentences on what the meeting was for.}}

## Decisions

{{bullets: Decisions the group made, with the reasoning when it was given.}}

## Open Questions

{{bullets: Questions raised and left unanswered, or that need more information.}}

## Next Steps

{{bullets: What happens next, and who said they would do it.}}

## Action Items

{{action_items}}

## Transcript

{{transcript}}
"""

ONE_ON_ONE = """---
date: {{date}}
people: {{people}}
project: {{project}}
transcript: {{transcript_link}}
---
# {{date}} - {{title}}

## Updates

{{bullets: Progress and news each person shared.}}

## Discussion

{{topics: The topics in the order they came up, with the key points under each.}}

## Feedback

{{bullets: Feedback given in either direction.}}

## Follow-ups

{{bullets: Things either person agreed to do, check on, or bring up next time.}}

## Action Items

{{action_items}}

## Transcript

{{transcript}}
"""

BUILT_IN = {"Standard": STANDARD, "Summary": SUMMARY, "Decisions and Next Steps": DECISIONS,
            "One-on-One": ONE_ON_ONE}


# helpers
def _path(name):
    return TEMPLATES_DIR / f"{name}.md"


def _key(heading, used):
    base = re.sub(r"[^a-z0-9]+", "_", heading.lower()).strip("_") or "section"
    key, n = base, 2
    while key in used:
        key, n = f"{base}_{n}", n + 1
    used.add(key)
    return key


def frontmatter_end(lines):
    """the index of the line closing the frontmatter, or 0 when there is none."""
    if not lines or lines[0].strip() != "---":
        return 0
    return next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), 0)


# main
def names():
    """the built-in templates, then your own in alphabetical order."""
    own = sorted(p.stem for p in TEMPLATES_DIR.glob("*.md")) if TEMPLATES_DIR.is_dir() else []
    return list(BUILT_IN) + [n for n in own if n not in BUILT_IN]


def text(name):
    if name in BUILT_IN:
        return BUILT_IN[name]
    if _path(name).exists():
        return _path(name).read_text()
    raise KeyError(f"no template named {name}")


def sections(template):
    """the parts the model writes, in order, as dicts of key, kind, and instructions. each key
    is named after the heading above it, so the standard template's are agenda and discussion."""
    found, used, heading = [], set(), ""
    for line in template.split("\n"):
        if HEADING.match(line):
            heading = HEADING.sub("", line).strip()
        for match in PLACEHOLDER.finditer(line):
            if match.group(1) in WRITTEN:
                found.append({"key": _key(heading, used), "kind": match.group(1),
                              "instructions": (match.group(2) or "").strip()})
    return found


def check(template):
    """raise ValueError naming the first problem with a template, so it can be fixed before saving."""
    lines = template.split("\n")
    end = frontmatter_end(lines)
    for i, line in enumerate(lines, 1):
        for match in PLACEHOLDER.finditer(line):
            name, instructions = match.group(1), (match.group(2) or "").strip()
            if name not in FILLED | WRITTEN:
                raise ValueError(f"line {i}: there's no {{{{{name}}}}} placeholder")
            if name in WRITTEN and not instructions:
                raise ValueError(f"line {i}: say what to write, as in {{{{{name}: the decisions made}}}}")
            if i - 1 <= end and name in WRITTEN | {"transcript", "action_items"}:
                raise ValueError(f"line {i}: {{{{{name}}}}} can't go in the frontmatter")
            if i - 1 > end and name in BLOCKS and line.strip() != match.group(0):
                raise ValueError(f"line {i}: {{{{{name}}}}} needs a line of its own")
    if not sections(template):
        raise ValueError("add at least one part for the model to write, such as {{paragraph: a summary}}")


def save(name, template):
    name = name.strip()
    if name in BUILT_IN:
        raise ValueError(f"{name} is a built-in template, so give yours another name")
    if not NAME.match(name):
        raise ValueError("a template name can have letters, numbers, spaces and simple punctuation")
    check(template)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    _path(name).write_text(template.rstrip("\n") + "\n")


def delete(name):
    if name in BUILT_IN:
        raise ValueError("built-in templates can't be deleted")
    _path(name).unlink(missing_ok=True)


def for_note(summary):
    """the template a note was written with: its own saved copy for one of yours, else the
    built-in of that name, else the standard one, which notes from before templates used."""
    return summary.get("template_text") or BUILT_IN.get(summary.get("template") or DEFAULT, STANDARD)
