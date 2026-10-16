"""end to end pipeline: normalize, diarize, transcribe, merge, match, render.

the command line scripts and the gui are thin wrappers around the functions here. every
meeting is registered in the library, and transcripts are always rendered from the current
names of the people in it, so renames and merges reach every past transcript.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from local_transcription import asr, audio, diarize, embed, match, library, merge, notes, render, vocab

# config
OUTPUT_DIR = Path(__file__).parent.parent / "outputs"
SAMPLE_CHARS = 160
# in split mode the mic channel is one known person and gets this speaker id
MIC_SPEAKER = "MIC"
DEFAULT_OWNER = "Me"
# fewer mic words than this (a stray "um") means the owner did not take part
MIC_MIN_WORDS = 5
# statuses that need no review. skipped speakers are left unnamed on purpose
RESOLVED = ["matched", "confirmed", "mic", "skipped"]
# review clips: up to this many per speaker, each this long at most, spread over the meeting
CLIP_COUNT = 3
CLIP_MIN_S = 2.0
CLIP_MAX_S = 8.0
CLIP_SPACING_S = 60.0


# helpers
def _log(progress, message):
    if progress:
        progress(message)


def display_names(speakers, segments):
    """{speaker: name}, numbering unknown speakers in order of first appearance."""
    known = {s: n for s, n in zip(speakers["speaker"], speakers["name"]) if isinstance(n, str) and n}
    names, unknown = {}, 0
    for speaker in dict.fromkeys(segments["speaker"]):
        if speaker in known:
            names[speaker] = known[speaker]
        else:
            unknown += 1
            names[speaker] = f"Unknown {unknown}"
    return names


def speaker_samples(segments):
    # longest segment per speaker so the review file shows something recognizable
    seg = segments.assign(seconds=segments["end"] - segments["start"])
    longest = seg.loc[seg.groupby("speaker")["seconds"].idxmax()]
    return longest.set_index("speaker")[["start", "text"]]


def link_people(speakers):
    """fill person_id from names where it is missing, and names from the library where a
    person_id exists, so renames in the library flow into the transcript."""
    speakers = speakers.copy()
    if "person_id" not in speakers:
        speakers["person_id"] = None
    current = library.person_names()
    for i, row in speakers.iterrows():
        person_id, name = row["person_id"], row["name"]
        if isinstance(person_id, str) and person_id in current:
            speakers.at[i, "name"] = current[person_id]
        elif isinstance(name, str) and name:
            speakers.at[i, "person_id"] = library.find_person(name)
    return speakers


def meeting_status(speakers):
    return "done" if speakers["status"].isin(RESOLVED).all() else "needs review"


def write_outputs(out_dir, segments, speakers, meta):
    """render every output file from segments and speakers, and register the meeting.
    this is the single place outputs are written, so filing elsewhere can hook in here."""
    speakers = link_people(speakers)
    names = display_names(speakers, segments)
    records = segments.assign(name=segments["speaker"].map(names))
    records = records[["speaker", "name", "start", "end", "text"]]
    (out_dir / "segments.json").write_text(json.dumps(records.to_dict("records"), indent=2))
    title, date = render.title_and_date(meta["source"])
    meta = {**meta, "title": meta.get("title") or title, "date": meta.get("date") or date}
    markdown = render.render_markdown(segments, names, meta["source"], meta["title"], meta["date"])
    (out_dir / "transcript.md").write_text(markdown)
    speakers.to_csv(out_dir / "speakers.csv", index=False)
    minutes = round(float(segments["end"].max()) / 60, 1) if len(segments) else None
    library.save_meeting(out_dir.name, meta, speakers, meeting_status(speakers), minutes)
    render_note_file(out_dir, meta, segments, speakers)
    review = out_dir / "review.csv"
    if meeting_status(speakers) == "done":
        review.unlink(missing_ok=True)
    else:
        write_review(out_dir, speakers, segments)
    return names


def write_review(out_dir, speakers, segments):
    samples = speaker_samples(segments)
    review = speakers[["speaker", "status", "score", "best_name", "best_score",
                       "second_name", "second_score", "minutes"]].copy()
    review.insert(1, "label", review["speaker"].map(display_names(speakers, segments)))
    review["sample_at"] = review["speaker"].map(samples["start"]).map(render.fmt_time)
    review["sample_text"] = review["speaker"].map(samples["text"]).str.slice(0, SAMPLE_CHARS)
    # fill in or correct this column, then run confirm.py
    review["name"] = speakers["name"].fillna("")
    path = out_dir / "review.csv"
    review.to_csv(path, index=False)
    return path


def load_run(out_dir):
    out_dir = Path(out_dir)
    meta = json.loads((out_dir / "run.json").read_text())
    segments = pd.DataFrame(json.loads((out_dir / "segments.json").read_text()))
    speakers = pd.read_csv(out_dir / "speakers.csv",
                           dtype={"name": str, "entry_id": str, "person_id": str, "status": str})
    if "person_id" not in speakers:
        speakers["person_id"] = None
    vectors = dict(np.load(out_dir / "speaker_embeddings.npz"))
    return meta, segments, speakers, vectors


def meeting_dir(meeting_id):
    out_dir = OUTPUT_DIR / meeting_id
    if not (out_dir / "run.json").exists():
        raise ValueError(f"no processed meeting named {meeting_id}")
    return out_dir


def speaker_audio(out_dir, meta, speaker):
    """the cleanest wav for one speaker: their own channel in split mode, else the mix."""
    if meta.get("split"):
        wav = out_dir / ("mic_16k.wav" if speaker == MIC_SPEAKER else "remote_16k.wav")
        if wav.exists():
            return wav
    return out_dir / "audio_16k.wav"


# main
def run(source, threshold=match.THRESHOLD, num_speakers=None, min_speakers=diarize.MIN_SPEAKERS,
        max_speakers=diarize.MAX_SPEAKERS, title=None, source_type="other", language=None,
        split="auto", mic_channel=0, owner=None, out_dir=None, progress=print):
    """process one recording. returns (output folder, speakers dataframe, review path or None).

    split: "auto" uses split mode for stereo files whose channels hold unrelated sources,
    "on" forces it, "off" always downmixes. in split mode mic_channel is the owner's mic and
    the other channel is everyone else. out_dir defaults to outputs/<file name>, a re-run of
    a meeting whose folder was renamed passes its folder."""
    source = Path(source)
    out_dir = Path(out_dir) if out_dir else OUTPUT_DIR / source.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    wav = out_dir / "audio_16k.wav"
    owner = owner or DEFAULT_OWNER

    _log(progress, "analyzing audio")
    info = audio.analyze(source)
    use_split = split == "on" or (split == "auto" and audio.is_split(info))
    if use_split and info["channels"] != 2:
        raise ValueError(f"split mode needs a stereo file, {source.name} has {info['channels']} channels")
    # the downmix is kept for playback and is what gets processed outside split mode
    info = audio.normalize(source, wav, info)
    if use_split:
        _log(progress, f"  {info['duration'] / 60:.1f} min, split mode: ch{mic_channel} is {owner}, "
                       f"ch{1 - mic_channel} is the call (correlation {info['correlation']})")
        mic_wav, main_wav = out_dir / "mic_16k.wav", out_dir / "remote_16k.wav"
        audio.extract_channel(source, mic_wav, mic_channel)
        audio.extract_channel(source, main_wav, 1 - mic_channel)
        # the owner is on the mic channel, so the call side has one fewer speaker
        min_speakers, max_speakers = max(1, min_speakers - 1), max(1, max_speakers - 1)
        num_speakers = max(1, num_speakers - 1) if num_speakers else None
    else:
        _log(progress, f"  {info['duration'] / 60:.1f} min, {info['channel_handling']}")
        main_wav = wav

    known = library.load_profiles()
    terms, replacements = vocab.load()
    prompt = vocab.build_prompt(terms, sorted(known))

    _log(progress, "diarizing")
    turns, exclusive, _ = diarize.diarize(main_wav, min_speakers, max_speakers, num_speakers)

    _log(progress, "transcribing")
    words, detected = asr.transcribe_mlx(main_wav, language=language,
                                         regions=diarize.speech_regions(turns), prompt=prompt)
    words = merge.assign_words(words, exclusive)
    words = merge.smooth_islands(merge.snap_to_sentences(words))
    segments = merge.words_to_segments(words)

    mic_regions = []
    if use_split:
        mic_regions = audio.energy_regions(mic_wav)
        if mic_regions:
            _log(progress, f"transcribing {owner}'s mic")
            mic_words, _ = asr.transcribe_mlx(mic_wav, language=detected, regions=mic_regions, prompt=prompt)
            mic_words["speaker"] = MIC_SPEAKER
            if len(mic_words) < MIC_MIN_WORDS:
                _log(progress, f"  only {len(mic_words)} words on the mic, treating {owner} as absent")
                mic_regions, mic_words = [], mic_words.iloc[0:0]
        if mic_regions:
            words = pd.concat([words, mic_words], ignore_index=True).sort_values("start")
            # segment each channel on its own so crosstalk does not chop turns apart
            segments = pd.concat([segments, merge.words_to_segments(mic_words)], ignore_index=True)
            segments = segments.sort_values("start").reset_index(drop=True)
            mic_turns = pd.DataFrame(mic_regions, columns=["start", "end"]).assign(speaker=MIC_SPEAKER)
            exclusive = pd.concat([exclusive, mic_turns[["speaker", "start", "end"]]], ignore_index=True)
    words.to_csv(out_dir / "words.csv", index=False)
    exclusive.to_csv(out_dir / "turns.csv", index=False)
    segments["text"] = segments["text"].map(lambda t: vocab.apply(t, replacements))

    _log(progress, "matching speakers")
    embedder = embed.Embedder()
    call_turns = exclusive[exclusive["speaker"] != MIC_SPEAKER]
    speaker_info = embedder.speakers(embed.load_audio(main_wav), call_turns)
    vectors = {spk: vec for spk, (vec, _) in speaker_info.items()}
    # the owner cannot be on the call side in split mode
    candidates = {n: p for n, p in known.items() if not (use_split and n == owner)}
    speakers = match.match_speakers(vectors, candidates, threshold)

    if use_split and MIC_SPEAKER in set(segments["speaker"]):
        vector, seconds = embedder.embed_spans(embed.load_audio(mic_wav), mic_regions)
        if vector is not None:
            vectors[MIC_SPEAKER] = vector
            speaker_info[MIC_SPEAKER] = (vector, seconds)
        mic_row = {"speaker": MIC_SPEAKER, "name": owner, "status": "mic", "score": np.nan,
                   "best_name": None, "best_score": np.nan, "second_name": None, "second_score": np.nan}
        speakers = pd.concat([speakers, pd.DataFrame([mic_row])], ignore_index=True)
    np.savez(out_dir / "speaker_embeddings.npz", **vectors)

    talk = exclusive.assign(seconds=exclusive["end"] - exclusive["start"]).groupby("speaker")
    speakers["minutes"] = speakers["speaker"].map(talk["seconds"].sum() / 60).round(1)
    speakers["first_start"] = speakers["speaker"].map(talk["start"].min()).round(2)
    speakers["embedded_s"] = speakers["speaker"].map({s: round(v[1], 1) for s, v in speaker_info.items()})
    speakers["entry_id"] = None
    speakers["person_id"] = None
    # speakers that never got a transcribed word are dropped from the outputs
    speakers = speakers[speakers["speaker"].isin(segments["speaker"])].reset_index(drop=True)

    meta = {"source": str(source), "title": title, "date": None, "source_type": source_type,
            "threshold": threshold, "language": detected, "prompt": prompt, "split": use_split,
            "mic_channel": mic_channel if use_split else None, "owner": owner if use_split else None}
    (out_dir / "run.json").write_text(json.dumps(meta, indent=2))
    # a re-run replaces the meeting, so entries the previous run added are stale
    for entry_id in library.all_entries().query("meeting_id == @out_dir.name")["entry_id"]:
        library.remove_entry(entry_id, out_dir.name)
    write_outputs(out_dir, segments, speakers, meta)
    review = out_dir / "review.csv"
    return out_dir, speakers, review if review.exists() else None


def apply_names(out_dir, mapping, enroll=True, skip=()):
    """set confirmed names ({speaker: name}), re-render the transcript, and add each confirmed
    speaker's meeting embedding to that person's profile. enroll is True, False, or a set of
    speakers to enroll. speakers in skip are left unnamed and no longer need review."""
    out_dir = Path(out_dir)
    meta, segments, speakers, vectors = load_run(out_dir)
    added = []
    for speaker in [*mapping, *skip]:
        if speaker not in set(speakers["speaker"]):
            raise ValueError(f"{speaker} is not in {out_dir.name}, see speakers.csv")
    for speaker in [*mapping, *skip]:
        row = speakers["speaker"] == speaker
        previous = speakers.loc[row, "entry_id"].iloc[0]
        if isinstance(previous, str) and previous:
            # relabeling a speaker replaces the profile entry it added before
            library.remove_entry(previous, out_dir.name)
            speakers.loc[row, "entry_id"] = None
        if speaker in skip:
            speakers.loc[row, ["name", "person_id", "status"]] = [None, None, "skipped"]
            continue
        name = mapping[speaker].strip()
        speakers.loc[row, "name"] = name
        speakers.loc[row, "person_id"] = library.ensure_person(name)
        if speakers.loc[row, "status"].iloc[0] != "mic":
            speakers.loc[row, "status"] = "confirmed"
        wants = enroll if isinstance(enroll, bool) else speaker in enroll
        if wants and speaker in vectors:
            entry_id = library.add_entry(
                name, vectors[speaker], method="confirm", source_file=Path(meta["source"]).name,
                source_type=meta.get("source_type", "other"),
                seconds=speakers.loc[row, "embedded_s"].iloc[0],
                meeting_id=out_dir.name, speaker=speaker)
            speakers.loc[row, "entry_id"] = entry_id
            added.append((name, entry_id))
    write_outputs(out_dir, segments, speakers, meta)
    return link_people(speakers), added


def merge_speakers(out_dir, source, target):
    """fold one diarized speaker into another when diarization split a person in two."""
    out_dir = Path(out_dir)
    meta, segments, speakers, vectors = load_run(out_dir)
    if source == target or not {source, target} <= set(speakers["speaker"]):
        raise ValueError(f"cannot merge {source} into {target}")
    if MIC_SPEAKER in (source, target):
        raise ValueError("the mic channel cannot be merged with a call speaker")
    src = speakers[speakers["speaker"] == source].iloc[0]
    dst_row = speakers["speaker"] == target
    dst = speakers[dst_row].iloc[0]

    # the merged voice is the duration-weighted mean of both
    if source in vectors and target in vectors:
        w_src, w_dst = np.nan_to_num([float(src["embedded_s"]), float(dst["embedded_s"])])
        merged = vectors[source] * w_src + vectors[target] * w_dst
        vectors[target] = embed.normalize(merged if w_src + w_dst else vectors[target])
        speakers.loc[dst_row, "embedded_s"] = round(w_src + w_dst, 1)
    vectors.pop(source, None)
    np.savez(out_dir / "speaker_embeddings.npz", **vectors)
    speakers.loc[dst_row, "minutes"] = round(float(np.nansum([src["minutes"], dst["minutes"]])), 1)
    speakers.loc[dst_row, "first_start"] = min(src["first_start"], dst["first_start"])
    for entry_id in (src["entry_id"], dst["entry_id"]):
        if isinstance(entry_id, str) and entry_id:
            library.remove_entry(entry_id, out_dir.name)
    speakers.loc[dst_row, "entry_id"] = None
    speakers = speakers[speakers["speaker"] != source].reset_index(drop=True)

    segments["speaker"] = segments["speaker"].replace(source, target)
    for name in ("words.csv", "turns.csv"):
        table = pd.read_csv(out_dir / name)
        table["speaker"] = table["speaker"].replace(source, target)
        table.to_csv(out_dir / name, index=False)
    write_outputs(out_dir, segments, speakers, meta)

    # a confirmed target gets its profile entry back with the merged voice
    if isinstance(dst["name"], str) and dst["status"] == "confirmed" and isinstance(dst["entry_id"], str):
        apply_names(out_dir, {target: dst["name"]})
    return target


def rerender(meeting_ids):
    """re-render transcripts after people were renamed or merged in the library."""
    for meeting_id in meeting_ids:
        out_dir = OUTPUT_DIR / meeting_id
        if (out_dir / "run.json").exists():
            meta, segments, speakers, _ = load_run(out_dir)
            write_outputs(out_dir, segments, speakers, meta)


def rename_meeting(out_dir, title):
    """change a meeting's title in the transcript, the note heading, and the library. the
    folder keeps its name so links to it still work."""
    out_dir = Path(out_dir)
    # the date is added in front of the title, so drop one typed at the start
    title = re.sub(r"^\d{4}-\d{2}-\d{2}[\s_-]*", "", title.strip()).strip() or title.strip()
    meta, segments, speakers, _ = load_run(out_dir)
    meta["title"] = title
    (out_dir / "run.json").write_text(json.dumps(meta, indent=2))
    write_outputs(out_dir, segments, speakers, meta)
    note_path = out_dir / "note.md"
    if note_path.exists() and json.loads((out_dir / "note.json").read_text()).get("edited"):
        # an edited note is otherwise left alone, but its heading follows the meeting's name
        date = meeting_title(meta)[1]
        text = re.sub(r"^# .*$", lambda _: f"# {date} - {title}", note_path.read_text(), count=1, flags=re.M)
        note_path.write_text(text)
    return title


def rename_person(person_id, new_name):
    rerender(library.rename_person(person_id, new_name))


def merge_people(source_id, target_id):
    rerender(library.merge_people(source_id, target_id))


def register_existing():
    """add output folders made before the library existed. safe to run more than once."""
    known = set(library.meetings().get("meeting_id", []))
    added = [d.name for d in library.legacy_meeting_dirs() if d.name not in known]
    rerender(added)
    return added


def clips(out_dir, speaker, count=CLIP_COUNT):
    """short review clips for one speaker: their longest segments, spread over the meeting,
    each cut to at most CLIP_MAX_S, with the words spoken inside the clip."""
    out_dir = Path(out_dir)
    segments = pd.DataFrame(json.loads((out_dir / "segments.json").read_text()))
    mine = segments[segments["speaker"] == speaker]
    mine = mine.assign(seconds=mine["end"] - mine["start"]).sort_values("seconds", ascending=False)
    picked = []
    for spacing in (CLIP_SPACING_S, 0.0):
        for row in mine.itertuples():
            if len(picked) >= count:
                break
            if row.seconds < CLIP_MIN_S and picked:
                break
            if all(abs(row.start - p["start"]) > spacing for p in picked) and \
                    all(row.start != p["start"] for p in picked):
                picked.append({"start": row.start, "end": min(row.end, row.start + CLIP_MAX_S)})
    words = pd.read_csv(out_dir / "words.csv")
    words = words[words["speaker"] == speaker]
    for clip in picked:
        inside = words[(words["start"] >= clip["start"] - 0.05) & (words["end"] <= clip["end"] + 0.05)]
        clip["text"] = "".join(inside["word"].astype(str)).strip()
        clip["start"], clip["end"] = round(clip["start"], 2), round(clip["end"], 2)
    return sorted(picked, key=lambda c: c["start"])


def suggestions(out_dir, speaker, count=3):
    """closest profiles for one speaker, scored against the library as it is now."""
    out_dir = Path(out_dir)
    meta, _, _, vectors = load_run(out_dir)
    if speaker not in vectors:
        return []
    known = library.load_profiles()
    owner = meta.get("owner")
    if meta.get("split") and speaker != MIC_SPEAKER:
        known = {n: p for n, p in known.items() if n != owner}
    scores = sorted(((float(vectors[speaker] @ vec), name) for name, (vec, _) in known.items()), reverse=True)
    return [{"name": name, "score": round(score, 3)} for score, name in scores[:count]]


# notes
def render_note_file(out_dir, meta, segments, speakers):
    """rewrite note.md from the saved summary, if this meeting has one."""
    path = out_dir / "note.json"
    if not path.exists():
        return None
    summary = json.loads(path.read_text())
    if summary.get("edited"):
        # hand edits win. only the project line follows changes made in the app
        project = library.meeting_project(out_dir.name)
        return set_frontmatter(out_dir / "note.md", {
            "project": notes.wikilink(project) if project else "",
            "transcript": json.dumps(transcript_uri(out_dir), ensure_ascii=False)})
    title, date = meeting_title(meta)
    transcript_ref = transcript_uri(out_dir)
    markdown = notes.render_note(summary, date, title, segments, link_people(speakers), summary.get("owner_id"),
                                 library.meeting_project(out_dir.name), transcript_ref)
    (out_dir / "note.md").write_text(markdown)
    return out_dir / "note.md"


def meeting_title(meta):
    """(title, date): the name you gave the meeting, or the one from its file name."""
    title, date = render.title_and_date(meta["source"])
    return meta.get("title") or title, meta.get("date") or date


def transcript_uri(out_dir):
    # a file link, so it opens from the vault copy of the note too
    return (Path(out_dir) / "transcript.md").resolve().as_uri()


def set_frontmatter(note_path, values):
    """swap single-line frontmatter fields ({key: yaml value}) in a note, leaving the rest as
    written. used for edited notes and for the vault copy."""
    if not note_path.exists():
        return None
    lines = note_path.read_text().split("\n")
    closing = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), 0)
    for i in range(1, closing):
        key = lines[i].partition(":")[0]
        if key in values:
            lines[i] = f"{key}: {values[key]}".rstrip()
    note_path.write_text("\n".join(lines))
    return note_path


def move_meeting(out_dir, name):
    """rename a meeting's output folder, and with it the meeting id in the library. returns the
    new folder. a name another meeting already uses gets a number added."""
    out_dir = Path(out_dir)
    target, n = OUTPUT_DIR / name, 2
    while target.exists() and target.resolve() != out_dir.resolve():
        target = OUTPUT_DIR / f"{name} ({n})"
        n += 1
    if target == out_dir:
        return out_dir
    out_dir.rename(target)
    library.rename_meeting_id(out_dir.name, target.name)
    meta, segments, speakers, _ = load_run(target)
    render_note_file(target, meta, segments, speakers)
    return target


def save_note_edits(out_dir, markdown):
    """keep a hand-edited note. later re-renders leave it alone until the edits are discarded
    or the note is rewritten."""
    out_dir = Path(out_dir)
    path = out_dir / "note.json"
    summary = json.loads(path.read_text())
    summary["edited"] = datetime.now().isoformat(timespec="seconds")
    (out_dir / "note.md").write_text(markdown)
    path.write_text(json.dumps(summary, indent=2))


def discard_note_edits(out_dir):
    """go back to the note as the model wrote it, with current names and project."""
    out_dir = Path(out_dir)
    path = out_dir / "note.json"
    summary = json.loads(path.read_text())
    summary.pop("edited", None)
    path.write_text(json.dumps(summary, indent=2))
    meta, segments, speakers, _ = load_run(out_dir)
    return render_note_file(out_dir, meta, segments, speakers)


def notes_dir():
    folder = os.environ.get("MEETING_NOTES_DIR")
    return Path(folder).expanduser() if folder else None


def note_filename(out_dir):
    """'2026-10-15 - Q1 Roadmap Working Group.md', from the note's heading so
    an edited heading is respected."""
    text = (out_dir / "note.md").read_text()
    heading = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), "")
    if not heading:
        meta = json.loads((out_dir / "run.json").read_text())
        title, date = render.title_and_date(meta["source"])
        heading = f"{meta.get('date') or date} - {meta.get('title') or title}"
    # characters obsidian or the file system will not take in a name
    return re.sub(r'[\\/:*?"<>|#^\[\]]', "-", heading).strip(" .") + ".md"


def send_note(out_dir, replace=False):
    """copy note.md into the obsidian meeting notes folder, rename the output folder to match,
    and move the meeting to the archive. an existing file is only replaced when asked. returns
    (the note written, the meeting's folder)."""
    out_dir = Path(out_dir)
    folder = notes_dir()
    if not folder:
        raise ValueError("set MEETING_NOTES_DIR in .env to your obsidian meeting notes folder")
    if not folder.is_dir():
        raise ValueError(f"the meeting notes folder does not exist: {folder}")
    if not (out_dir / "note.md").exists():
        raise ValueError("this meeting has no note yet")
    target = folder / note_filename(out_dir)
    if target.exists() and not replace:
        raise FileExistsError(target.name)
    # the output folder takes the vault note's name, which also fixes the transcript link
    out_dir = move_meeting(out_dir, target.stem)
    target.write_text((out_dir / "note.md").read_text())
    library.archive_meeting(out_dir.name, target)
    return target, out_dir


def write_note(out_dir, backend=None, project=None, owner=None, progress=print):
    """summarize a meeting into note.md. project is a project name to file it under, or None
    to keep the current one. the owner is who the action items are for."""
    out_dir = Path(out_dir)
    meta, segments, speakers, _ = load_run(out_dir)
    speakers = link_people(speakers)
    backend = backend or notes.default_backend()
    owner = owner or meta.get("owner") or os.environ.get("MIC_OWNER") or DEFAULT_OWNER
    owner_id = library.find_person(owner)
    owner_full = (library.person_details(owner_id) or {}).get("full_name", owner) if owner_id else owner
    date = meta.get("date") or render.title_and_date(meta["source"])[1]
    lines = notes.transcript_lines(segments, speakers)
    attendees = [line[1:line.index("]")] for line in lines]
    terms, _ = vocab.load()

    _log(progress, f"summarizing with the {backend} model")
    summary = notes.summarize(lines, owner_full, date, list(dict.fromkeys(attendees)), terms,
                              backend=backend, progress=progress)
    summary["owner_id"] = owner_id
    (out_dir / "note.json").write_text(json.dumps(summary, indent=2))
    if project is not None:
        library.set_meeting_project(out_dir.name, project)
    return render_note_file(out_dir, {**meta, "date": date}, segments, speakers)


def set_project(out_dir, project):
    """file a meeting under a project (blank clears it) and refresh its note."""
    out_dir = Path(out_dir)
    library.set_meeting_project(out_dir.name, project)
    meta, segments, speakers, _ = load_run(out_dir)
    return render_note_file(out_dir, meta, segments, speakers)


def rerender_people(person_ids):
    """refresh transcripts and notes after a person's full name, team, or organization changed."""
    rerender(sorted({m for p in person_ids for m in library.meetings_for_person(p)}))
