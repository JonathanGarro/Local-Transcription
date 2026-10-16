"""assign transcribed words to diarized speaker turns."""

import pandas as pd

# config
# runs of at most this many words, lasting under this many seconds, count as boundary noise
ISLAND_MAX_WORDS = 2
ISLAND_MAX_S = 1.0
# a sentence edge of at most this many words that crosses into another turn joins the sentence
SNAP_MAX_WORDS = 4
SENTENCE_END = (".", "?", "!")


# main
def assign_words(words, turns):
    """label each word with the speaker whose turn overlaps it most. words with no overlap
    take the nearest turn."""
    words = words.copy()
    speakers = []
    for start, end in zip(words["start"], words["end"]):
        overlap = (turns["end"].clip(upper=end) - turns["start"].clip(lower=start)).clip(lower=0)
        if overlap.max() > 0:
            speakers.append(turns.loc[overlap.idxmax(), "speaker"])
        else:
            mid = (start + end) / 2
            gap = pd.concat([(turns["start"] - mid).abs(), (turns["end"] - mid).abs()], axis=1).min(axis=1)
            speakers.append(turns.loc[gap.idxmin(), "speaker"])
    words["speaker"] = speakers
    return words


def snap_to_sentences(words, max_words=SNAP_MAX_WORDS):
    """when a sentence is split across two speakers and one side is a short edge, give the
    whole sentence to the other side. ties go to whoever started the sentence."""
    words = words.copy().reset_index(drop=True)
    ends = words["word"].str.strip().str.endswith(SENTENCE_END)
    sentence_id = ends.shift(fill_value=False).cumsum()
    for _, idx in words.groupby(sentence_id).groups.items():
        labels = words.loc[idx, "speaker"]
        run_id = (labels != labels.shift()).cumsum()
        if run_id.nunique() != 2:
            continue
        sizes = labels.groupby(run_id).size()
        first, second = sizes.index
        if sizes[second] <= max_words and sizes[second] <= sizes[first]:
            words.loc[idx, "speaker"] = labels[run_id == first].iloc[0]
        elif sizes[first] <= max_words and sizes[first] < sizes[second]:
            words.loc[idx, "speaker"] = labels[run_id == second].iloc[0]
    return words


def smooth_islands(words, max_words=ISLAND_MAX_WORDS, max_seconds=ISLAND_MAX_S):
    """relabel tiny speaker runs sandwiched between runs of one other speaker. these are
    usually word timestamps landing a little past a turn boundary."""
    words = words.copy().reset_index(drop=True)
    changed = True
    while changed:
        changed = False
        run_id = (words["speaker"] != words["speaker"].shift()).cumsum()
        runs = words.groupby(run_id).agg(speaker=("speaker", "first"), n=("word", "size"),
                                         start=("start", "first"), end=("end", "last"))
        runs["prev"] = runs["speaker"].shift()
        runs["next"] = runs["speaker"].shift(-1)
        islands = runs[(runs["n"] <= max_words) & (runs["end"] - runs["start"] < max_seconds)
                       & (runs["prev"] == runs["next"]) & runs["prev"].notna()]
        if len(islands):
            first = islands.index[0]
            words.loc[run_id == first, "speaker"] = islands.loc[first, "prev"]
            changed = True
    return words


def words_to_segments(words, max_gap=1.5):
    """group consecutive words from the same speaker into segments."""
    block = ((words["speaker"] != words["speaker"].shift())
             | (words["start"] - words["end"].shift() > max_gap)).cumsum()
    segments = words.groupby(block).agg(speaker=("speaker", "first"), start=("start", "first"),
                                        end=("end", "last"), text=("word", "".join))
    segments["text"] = segments["text"].str.strip()
    return segments.reset_index(drop=True)
