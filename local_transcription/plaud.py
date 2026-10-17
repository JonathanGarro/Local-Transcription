"""reprocess meetings imported from obsidian using their original plaud recordings.

the audio is downloaded with the plaud cli and runs through the normal pipeline into the
imported meeting's id, so the meeting keeps its obsidian note. the names in that note then
suggest who each new speaker is: plaud's transcript has timestamps and plaud's speaker
labels, the note has the same text with the names fixed by hand. aligning the two texts
gives each plaud label a name, and timing overlap carries that name to the speakers found
here. suggestions still need confirming in review, nothing is enrolled on its own.
"""

import difflib
import functools
import json
import os
import re
import subprocess
import urllib.request
from collections import Counter
from pathlib import Path

import pandas as pd

from local_transcription import library, obsidian, pipeline

# config
PLAUD_TRANSCRIPT = "plaud_transcript.txt"
# '[02:40 - 03:08] Speaker 3: It does.'
PLAUD_LINE = re.compile(r"^\[([\d:]+) - ([\d:]+)\] ([^:]+): ?(.*)$")
# '[Maya Chen] It does.' or '[SPEAKER_01]: It does.'
NOTE_LINE = re.compile(r"^\[([^\]]+)\]:? ?(.*)$")
# labels that name nobody
GENERIC_LABEL = re.compile(r"^(speaker[ _]?\d+|unknown( \d+)?)$", re.IGNORECASE)
# a plaud label takes a name only when most of its aligned words agree
LABEL_MIN_SHARE = 0.6
# a speaker here is suggested a name when most of its overlapping time agrees
SPEAKER_MIN_SHARE = 0.5


# helpers
def _seconds(stamp):
    seconds = 0
    for part in stamp.split(":"):
        seconds = seconds * 60 + int(part)
    return seconds


def _words(text):
    return re.findall(r"[a-z0-9']+", text.lower())


def _cli(*args):
    result = subprocess.run(["plaud", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip().splitlines()[-1])
    return result.stdout


def _glob_escape(name):
    # meeting names can hold [ and ], which glob reads as a character set
    return re.sub(r"([\[\]*?])", r"[\1]", name)


def file_id(link):
    """the api id for a plaud web link. the web app drops the of_ prefix the api needs."""
    last = str(link).rstrip("/").rsplit("/", 1)[-1]
    return last if last.startswith("of_") else f"of_{last}"


def plaud_link(note_path):
    """the plaud link in a note's transcript property, or None."""
    link = obsidian.frontmatter(Path(note_path).read_text(encoding="utf-8", errors="replace")).get("transcript")
    return link if isinstance(link, str) and "plaud.ai/" in link else None


def download_audio(plaud_id, folder, stem):
    """save a recording's audio as <stem> plus plaud's file type. returns the path. plaud
    hands out a link that expires after a day, so it is fetched fresh each time."""
    url = next((line.strip() for line in _cli("audio", plaud_id).splitlines() if line.strip().startswith("https://")), None)
    if not url:
        raise RuntimeError(f"plaud gave no audio link for {plaud_id}")
    target = Path(folder) / f"{stem}{Path(url.split('?', 1)[0]).suffix or '.ogg'}"
    partial = target.with_name(target.name + ".partial")
    with urllib.request.urlopen(url) as response, partial.open("wb") as f:
        while chunk := response.read(1 << 20):
            f.write(chunk)
    partial.replace(target)
    return target


def save_transcript(plaud_id, target):
    _cli("transcript", plaud_id, "-o", str(target))
    return target


def plaud_lines(path):
    """plaud's transcript as rows of start, end, label, text."""
    rows = []
    for line in Path(path).read_text().splitlines():
        found = PLAUD_LINE.match(line.strip())
        if found:
            start, end, label, text = found.groups()
            rows.append({"start": _seconds(start), "end": _seconds(end), "label": label.strip(), "text": text})
    return pd.DataFrame(rows, columns=["start", "end", "label", "text"])


def note_lines(text):
    """(label, text) for each line of the note's transcript block."""
    after = text.split("## Transcript", 1)
    if len(after) < 2:
        return []
    lines = []
    for line in after[1].splitlines():
        found = NOTE_LINE.match(line.strip())
        if found:
            lines.append((found.group(1).strip(), found.group(2)))
    return lines


def _person(label):
    """the library's name for a label, or None for labels like Speaker 3. older notes use a
    first name ('Priya') or a shorter name ('Lena Fischer'), which count when exactly one
    person's first and last names fit."""
    if GENERIC_LABEL.match(label):
        return None
    names = library.person_names()
    person_id = library.find_person(label)
    if person_id:
        return names.get(person_id)
    parts = label.lower().split()
    fits = [n for n in names.values() if n.lower().split()[0] == parts[0]
            and (len(parts) == 1 or n.lower().split()[-1] == parts[-1])]
    return fits[0] if len(fits) == 1 else None


def label_names(plaud, note):
    """{plaud label: name}. plaud labels that already are a known name keep it. the rest take
    the name the note gives the same words, when the alignment mostly agrees."""
    # one lookup per label, not per word
    person = functools.lru_cache(maxsize=None)(_person)
    plaud_words = [(w, label) for label, text in zip(plaud["label"], plaud["text"]) for w in _words(text)]
    note_words = [(w, label) for label, text in note for w in _words(text)]
    matcher = difflib.SequenceMatcher(None, [w for w, _ in plaud_words], [w for w, _ in note_words], autojunk=False)
    votes = {}
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            name = person(note_words[block.b + k][1])
            if name:
                votes.setdefault(plaud_words[block.a + k][1], Counter())[name] += 1
    names = {}
    for label in plaud["label"].unique():
        own = person(label)
        if own:
            names[label] = own
        elif label in votes:
            name, count = votes[label].most_common(1)[0]
            if count / sum(votes[label].values()) >= LABEL_MIN_SHARE:
                names[label] = name
    return names


def speaker_names(segments, plaud, names):
    """{speaker: {name, share}} for the speakers found here, from how their time overlaps
    plaud lines whose label has a name."""
    named = plaud.assign(name=plaud["label"].map(names))
    result = {}
    for speaker, mine in segments.groupby("speaker"):
        overlap = Counter()
        for seg in mine.itertuples():
            hits = named[(named["start"] < seg.end) & (named["end"] > seg.start)]
            for line in hits.itertuples():
                seconds = min(seg.end, line.end) - max(seg.start, line.start)
                overlap[line.name if isinstance(line.name, str) else None] += max(seconds, 0)
        total = sum(overlap.values())
        best = next(((n, s) for n, s in overlap.most_common() if n), None)
        if best and total and best[1] / total >= SPEAKER_MIN_SHARE:
            result[speaker] = {"name": best[0], "share": round(best[1] / total, 3)}
    return result


def suggest_from_note(out_dir, note_path):
    """write the note's suggested names for each speaker. returns them."""
    out_dir = Path(out_dir)
    plaud = plaud_lines(out_dir / PLAUD_TRANSCRIPT)
    note = note_lines(Path(note_path).read_text(encoding="utf-8", errors="replace"))
    segments = pd.DataFrame(json.loads((out_dir / "segments.json").read_text()))
    suggested = speaker_names(segments, plaud, label_names(plaud, note)) if len(plaud) and note else {}
    (out_dir / pipeline.NOTE_NAMES).write_text(json.dumps(suggested, indent=2))
    return suggested


# main
def reprocess(meeting_id, progress=print):
    """download an imported meeting's plaud recording, run it through the pipeline into the
    same meeting, and suggest names from its note. returns (output folder, suggestions)."""
    note_path = library.meeting_sent_path(meeting_id)
    link = plaud_link(note_path) if note_path and Path(note_path).exists() else None
    if not link:
        raise ValueError(f"{meeting_id} has no plaud link in its note")
    plaud_id = file_id(link)
    out_dir = pipeline.OUTPUT_DIR / meeting_id
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = pipeline.OUTPUT_DIR.parent / "data"
    # a download from an earlier try is reused
    source = next((p for p in data_dir.glob(f"{_glob_escape(meeting_id)}.*") if not p.name.endswith(".partial")), None)
    if not source:
        progress("downloading from plaud")
        source = download_audio(plaud_id, data_dir, meeting_id)
    save_transcript(plaud_id, out_dir / PLAUD_TRANSCRIPT)
    title = library.meetings().query("meeting_id == @meeting_id")["title"].iloc[0]
    # some plaud uploads are stereo zoom recordings, which split mode handles as the app does
    pipeline.run(source, source_type="plaud", title=title, out_dir=out_dir, progress=progress,
                 owner=os.environ.get("MIC_OWNER"), mic_channel=1 if os.environ.get("MIC_CHANNEL") == "right" else 0)
    return out_dir, suggest_from_note(out_dir, note_path)
