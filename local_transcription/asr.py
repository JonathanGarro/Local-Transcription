"""speech recognition backends. both return a words dataframe: word, start, end, probability."""

import re

import numpy as np
import pandas as pd

# config
MLX_WHISPER_REPO = "mlx-community/whisper-large-v3-turbo"
FASTER_WHISPER_MODEL = "large-v3-turbo"
WORD_COLUMNS = ["word", "start", "end", "probability"]
SAMPLE_RATE = 16000
# skip silent gaps longer than this many seconds when they follow a likely hallucination
HALLUCINATION_SILENCE_S = 2.0
# shared decoding options. not conditioning on previous text stops repetition loops
DECODE_OPTIONS = {"word_timestamps": True, "condition_on_previous_text": False,
                  "hallucination_silence_threshold": HALLUCINATION_SILENCE_S}
# phrases whisper invents on noise or silence. a segment that is only one of these is dropped
# unless whisper was confident in it, so a real "thank you" survives
HALLUCINATIONS = {"thank you", "thanks", "thank you very much", "thanks for watching",
                  "thank you for watching", "see you next time", "bye", "you", "so", "okay"}
HALLUCINATION_MIN_PROB = 0.8
# whisper's own limit for repetitive output. segments still above it after its retries are loops
MAX_COMPRESSION_RATIO = 2.4
# chunks stay under one whisper window so every window sees the prompt
CHUNK_MAX_S = 28.0
# long stretches are cut at the quietest point in this window before the limit
SPLIT_SEARCH_S = 8.0
SPLIT_FRAME_S = 0.1


# helpers
def flatten_regions(regions):
    # [(start, end), ...] to the flat [start, end, start, end] list whisper expects
    if not regions:
        return "0"
    return [round(t, 2) for region in regions for t in region]


def quietest_point(samples, start, end):
    # center of the lowest energy frame between start and end, in seconds
    frame = int(SPLIT_FRAME_S * SAMPLE_RATE)
    a, b = int(start * SAMPLE_RATE), int(end * SAMPLE_RATE)
    window = samples[a:b]
    n = len(window) // frame
    if n == 0:
        return end
    energy = (window[:n * frame].reshape(n, frame) ** 2).mean(axis=1)
    return start + (int(np.argmin(energy)) + 0.5) * SPLIT_FRAME_S


def make_chunks(regions, samples, max_s=CHUNK_MAX_S):
    """pack speech regions into chunks no longer than max_s, splitting long regions at pauses."""
    pieces = []
    for start, end in regions:
        while end - start > max_s:
            cut = quietest_point(samples, start + max_s - SPLIT_SEARCH_S, start + max_s)
            pieces.append((start, cut))
            start = cut
        pieces.append((start, end))
    chunks = []
    for start, end in pieces:
        if chunks and end - chunks[-1][0] <= max_s:
            chunks[-1] = (chunks[-1][0], end)
        else:
            chunks.append((start, end))
    return chunks


def is_hallucination(segment):
    """true for repetition loops, runs of zero-length words, and stock phrases whisper
    produces on noise when it was not confident in them."""
    words = segment.get("words", [])
    if segment.get("compression_ratio", 0) > MAX_COMPRESSION_RATIO:
        return True
    if words and sum(w["end"] - w["start"] <= 0 for w in words) > len(words) / 2:
        return True
    text = re.sub(r"[^\w\s']", "", segment["text"]).strip().lower()
    if not words or text not in HALLUCINATIONS:
        return False
    return float(np.mean([w["probability"] for w in words])) < HALLUCINATION_MIN_PROB


def is_prompt_echo(text, prompt):
    # whisper sometimes reads the prompt back on near-silent audio
    text = text.strip().lower()
    return bool(text) and text in prompt.lower()


# main
def transcribe_mlx(wav, language=None, regions=None, prompt=None):
    import mlx_whisper
    from mlx_whisper.audio import load_audio

    if not prompt:
        result = mlx_whisper.transcribe(str(wav), path_or_hf_repo=MLX_WHISPER_REPO, language=language,
                                        clip_timestamps=flatten_regions(regions), **DECODE_OPTIONS)
        words = [{k: w[k] for k in WORD_COLUMNS} for seg in result["segments"]
                 if not is_hallucination(seg) for w in seg.get("words", [])]
        return pd.DataFrame(words, columns=WORD_COLUMNS), result.get("language")

    samples = np.array(load_audio(str(wav)))
    if not regions:
        regions = [(0.0, len(samples) / SAMPLE_RATE)]
    words = []
    for start, end in make_chunks(regions, samples):
        clip = samples[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)]
        result = mlx_whisper.transcribe(clip, path_or_hf_repo=MLX_WHISPER_REPO, language=language,
                                        initial_prompt=prompt, **DECODE_OPTIONS)
        # detect the language once, then hold it so short chunks are not misread
        language = language or result.get("language")
        for seg in result["segments"]:
            if is_prompt_echo(seg["text"], prompt) or is_hallucination(seg):
                continue
            for w in seg.get("words", []):
                words.append({"word": w["word"], "start": w["start"] + start,
                              "end": w["end"] + start, "probability": w["probability"]})
    return pd.DataFrame(words, columns=WORD_COLUMNS), language


def transcribe_faster(wav, language=None, regions=None):
    from faster_whisper import WhisperModel

    # ctranslate2 has no metal backend, so cpu int8 is the best option on a mac
    model = WhisperModel(FASTER_WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(wav), language=language,
                                      clip_timestamps=flatten_regions(regions), **DECODE_OPTIONS)
    words = [{"word": w.word, "start": w.start, "end": w.end, "probability": w.probability}
             for seg in segments for w in (seg.words or [])]
    return pd.DataFrame(words, columns=WORD_COLUMNS), info.language
