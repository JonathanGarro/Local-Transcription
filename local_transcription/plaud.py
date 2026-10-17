"""reprocess meetings imported from obsidian using their original plaud recordings.

the audio is downloaded with the plaud cli and runs through the pipeline into the imported
meeting's id, so the meeting keeps its obsidian note. the recording is not transcribed
again: the note already has the transcript, with the names fixed by hand, and plaud's copy
of it has the timestamps. lining the two up times each note line, so the app's transcript
reads like the note and still plays from any line. only who spoke when is worked out here,
for voice profiles, and the note's names are suggested for each speaker by how their turns
overlap the lines. suggestions still need confirming in review, nothing is enrolled on its own.
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

from local_transcription import library, merge, obsidian, pipeline

# config
PLAUD_TRANSCRIPT = "plaud_transcript.txt"
# '[02:40 - 03:08] Speaker 3: It does.'
PLAUD_LINE = re.compile(r"^\[([\d:]+) - ([\d:]+)\] ([^:]+): ?(.*)$")
# '[Maya Chen] It does.' or '[SPEAKER_01]: It does.'
NOTE_LINE = re.compile(r"^\[([^\]]+)\]:? ?(.*)$")
# labels that name nobody
GENERIC_LABEL = re.compile(r"^(speaker[ _]?\d+|unknown( \d+)?)$", re.IGNORECASE)
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


def timed_lines(plaud, note):
    """the note's transcript lines with plaud's times, as rows of start, end, label, text. the
    note was made from plaud's transcript, so each note line takes the times of the plaud
    lines its words line up with, and a line too edited to line up sits between its
    neighbours. a note with no transcript block uses plaud's own lines."""
    if not note:
        return plaud.copy()
    plaud_words, plaud_index = [], []
    for i, text in enumerate(plaud["text"]):
        for word in _words(text):
            plaud_words.append(word)
            plaud_index.append(i)
    note_words, note_index = [], []
    for j, (_, text) in enumerate(note):
        for word in _words(text):
            note_words.append(word)
            note_index.append(j)
    matcher = difflib.SequenceMatcher(None, plaud_words, note_words, autojunk=False)
    hits = {}
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            hits.setdefault(note_index[block.b + k], set()).add(plaud_index[block.a + k])
    rows = []
    for j, (label, text) in enumerate(note):
        matched = plaud.iloc[sorted(hits[j])] if j in hits else None
        rows.append({"start": matched["start"].min() if matched is not None else None,
                     "end": matched["end"].max() if matched is not None else None,
                     "label": label, "text": text})
    lines = pd.DataFrame(rows, columns=["start", "end", "label", "text"])
    lines["start"] = lines["start"].fillna(lines["end"].shift().ffill()).fillna(0)
    lines["end"] = lines["end"].fillna(lines["start"].shift(-1).bfill()).fillna(lines["start"])
    return lines


def speaker_names(turns, lines):
    """{speaker: {name, share}} for the speakers found here, from how their turns overlap
    lines whose label names someone."""
    result = {}
    for speaker, mine in turns.groupby("speaker"):
        overlap = Counter()
        for turn in mine.itertuples():
            hits = lines[(lines["start"] < turn.end) & (lines["end"] > turn.start)]
            for line in hits.itertuples():
                seconds = min(turn.end, line.end) - max(turn.start, line.start)
                overlap[line.name if isinstance(line.name, str) else None] += max(seconds, 0)
        total = sum(overlap.values())
        best = next(((n, s) for n, s in overlap.most_common() if n), None)
        if best and total and best[1] / total >= SPEAKER_MIN_SHARE:
            result[speaker] = {"name": best[0], "share": round(best[1] / total, 3)}
    return result


def note_segments(lines, turns):
    """the note's lines as pipeline segments, and the names they suggest for each speaker.
    a line goes to the speaker its name points at, so the transcript reads like the note, or
    else to the speaker whose turns overlap it most. returns (segments, suggestions)."""
    person = functools.lru_cache(maxsize=None)(_person)
    lines = lines.assign(name=[person(label) for label in lines["label"]])
    suggested = speaker_names(turns, lines)
    by_name = {}
    for speaker, guess in suggested.items():
        by_name.setdefault(guess["name"], []).append(speaker)
    overlapping = merge.assign_words(lines[["start", "end"]], turns)["speaker"]
    speakers = [by_name[n][0] if len(by_name.get(n, [])) == 1 else s for n, s in zip(lines["name"], overlapping)]
    segments = pd.DataFrame({"speaker": speakers, "start": lines["start"].astype(float),
                             "end": lines["end"].astype(float), "text": lines["text"].str.strip()})
    return segments, suggested


def _meeting_lines(out_dir, note_path):
    # the note's transcript timed by plaud's, the lines every transcript here is built from
    plaud = plaud_lines(Path(out_dir) / PLAUD_TRANSCRIPT)
    return timed_lines(plaud, note_lines(Path(note_path).read_text(encoding="utf-8", errors="replace")))


def check_mic(out_dir):
    """in split mode the mic side is taken to be the owner. when the note says someone else
    spoke there, such as a colleague sharing the mic, it goes back to review instead. returns
    the note's name for it then, else None."""
    out_dir = Path(out_dir)
    note_names = out_dir / pipeline.NOTE_NAMES
    meta, segments, speakers, _ = pipeline.load_run(out_dir)
    suggested = json.loads(note_names.read_text()).get(pipeline.MIC_SPEAKER) if note_names.exists() else None
    row = speakers["speaker"] == pipeline.MIC_SPEAKER
    # a mic side left with no lines of the note has nothing to review
    if not suggested or not row.any() or speakers.loc[row, "status"].iloc[0] != "mic" \
            or suggested["name"] == speakers.loc[row, "name"].iloc[0] \
            or not (segments["speaker"] == pipeline.MIC_SPEAKER).any():
        return None
    speakers.loc[row, ["name", "person_id", "status"]] = [None, None, "unknown"]
    pipeline.write_outputs(out_dir, segments, speakers, meta)
    return suggested["name"]


# main
def reprocess(meeting_id, progress=print):
    """download an imported meeting's plaud recording and run it through the pipeline into
    the same meeting. the transcript is the note's, so only who spoke when is worked out
    here, and the note's names are suggested for each speaker. returns (output folder,
    suggestions)."""
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
    lines = _meeting_lines(out_dir, note_path)
    found = {}

    def transcript(turns):
        segments, found["suggested"] = note_segments(lines, turns)
        return segments

    title = library.meetings().query("meeting_id == @meeting_id")["title"].iloc[0]
    # some plaud uploads are stereo zoom recordings, which split mode handles as the app does.
    # with no transcript to reuse, the recording is transcribed here
    pipeline.run(source, source_type="plaud", title=title, out_dir=out_dir, progress=progress,
                 owner=os.environ.get("MIC_OWNER"), mic_channel=1 if os.environ.get("MIC_CHANNEL") == "right" else 0,
                 transcript=transcript if len(lines) else None)
    suggested = found.get("suggested", {})
    (out_dir / pipeline.NOTE_NAMES).write_text(json.dumps(suggested, indent=2))
    check_mic(out_dir)
    return out_dir, suggested


def retime(meeting_id):
    """swap the transcript of a meeting reprocessed before transcripts came from the note for
    the note's, keeping its speakers and every confirmed name. returns the suggestions."""
    out_dir = pipeline.OUTPUT_DIR / meeting_id
    note_path = library.meeting_sent_path(meeting_id)
    if not (out_dir / PLAUD_TRANSCRIPT).exists() or not note_path or not Path(note_path).exists():
        raise ValueError(f"{meeting_id} was not reprocessed from plaud, or its note is gone")
    lines = _meeting_lines(out_dir, note_path)
    if not len(lines):
        raise ValueError(f"{meeting_id} has no transcript lines in its note or from plaud")
    meta, _, speakers, _ = pipeline.load_run(out_dir)
    segments, suggested = note_segments(lines, pd.read_csv(out_dir / "turns.csv"))
    # unnamed speakers left with no lines have nothing to review. named ones keep their
    # confirmations and profile entries
    speakers = speakers[speakers["speaker"].isin(segments["speaker"]) | speakers["status"].isin(pipeline.RESOLVED)]
    pipeline.write_outputs(out_dir, segments, speakers.reset_index(drop=True), meta)
    (out_dir / pipeline.NOTE_NAMES).write_text(json.dumps(suggested, indent=2))
    check_mic(out_dir)
    return suggested
