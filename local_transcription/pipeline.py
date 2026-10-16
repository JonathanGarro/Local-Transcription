"""end to end pipeline: normalize, diarize, transcribe, merge, match, render.

the command line scripts are thin wrappers around run() and apply_names() so a gui can
call the same functions later.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from local_transcription import asr, audio, diarize, embed, match, merge, profiles, render, vocab

# config
OUTPUT_DIR = Path(__file__).parent.parent / "outputs"
SAMPLE_CHARS = 160
# in split mode the mic channel is one known person and gets this speaker id
MIC_SPEAKER = "MIC"
DEFAULT_OWNER = "Me"
# fewer mic words than this (a stray "um") means the owner did not take part
MIC_MIN_WORDS = 5
# statuses that need no review
RESOLVED = ["matched", "confirmed", "mic"]


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


def write_outputs(out_dir, segments, speakers, meta):
    names = display_names(speakers, segments)
    records = segments.assign(name=segments["speaker"].map(names))
    records = records[["speaker", "name", "start", "end", "text"]]
    (out_dir / "segments.json").write_text(json.dumps(records.to_dict("records"), indent=2))
    markdown = render.render_markdown(segments, names, meta["source"], meta.get("title"), meta.get("date"))
    (out_dir / "transcript.md").write_text(markdown)
    speakers.to_csv(out_dir / "speakers.csv", index=False)
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
    speakers = pd.read_csv(out_dir / "speakers.csv", dtype={"name": str, "entry_id": str})
    vectors = dict(np.load(out_dir / "speaker_embeddings.npz"))
    return meta, segments, speakers, vectors


# main
def run(source, threshold=match.THRESHOLD, num_speakers=None, min_speakers=diarize.MIN_SPEAKERS,
        max_speakers=diarize.MAX_SPEAKERS, title=None, source_type="other", language=None,
        split="auto", mic_channel=0, owner=None, progress=print):
    """process one recording. returns (output folder, speakers dataframe, review path or None).

    split: "auto" uses split mode for stereo files whose channels hold unrelated sources,
    "on" forces it, "off" always downmixes. in split mode mic_channel is the owner's mic and
    the other channel is everyone else."""
    source = Path(source)
    out_dir = OUTPUT_DIR / source.stem
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

    known = profiles.load_profiles()
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
    # speakers that never got a transcribed word are dropped from the outputs
    speakers = speakers[speakers["speaker"].isin(segments["speaker"])].reset_index(drop=True)

    meta = {"source": str(source), "title": title, "date": None, "source_type": source_type,
            "threshold": threshold, "language": detected, "prompt": prompt, "split": use_split,
            "mic_channel": mic_channel if use_split else None, "owner": owner if use_split else None}
    (out_dir / "run.json").write_text(json.dumps(meta, indent=2))
    write_outputs(out_dir, segments, speakers, meta)

    review = None
    if not speakers["status"].isin(RESOLVED).all():
        review = write_review(out_dir, speakers, segments)
    return out_dir, speakers, review


def apply_names(out_dir, mapping, enroll=True):
    """set confirmed names ({speaker: name}), re-render the transcript, and optionally add
    each confirmed speaker's meeting embedding to that person's profile."""
    out_dir = Path(out_dir)
    meta, segments, speakers, vectors = load_run(out_dir)
    added = []
    for speaker, name in mapping.items():
        if speaker not in set(speakers["speaker"]):
            raise ValueError(f"{speaker} is not in {out_dir.name}, see speakers.csv")
        row = speakers["speaker"] == speaker
        previous = speakers.loc[row, "entry_id"].iloc[0]
        if isinstance(previous, str) and previous:
            # relabeling a speaker replaces the profile entry it added before
            profiles.remove_entry(previous)
            speakers.loc[row, "entry_id"] = None
        speakers.loc[row, "name"] = name
        speakers.loc[row, "status"] = "confirmed"
        if enroll and speaker in vectors:
            entry_id = profiles.add_entry(
                name, vectors[speaker], method="confirm", source_file=Path(meta["source"]).name,
                source_type=meta.get("source_type", "other"),
                seconds=speakers.loc[row, "embedded_s"].iloc[0])
            speakers.loc[row, "entry_id"] = entry_id
            added.append((name, entry_id))
    write_outputs(out_dir, segments, speakers, meta)
    review = out_dir / "review.csv"
    if speakers["status"].isin(RESOLVED).all() and review.exists():
        review.unlink()
    return speakers, added
