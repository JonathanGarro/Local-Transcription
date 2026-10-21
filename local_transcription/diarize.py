"""speaker diarization with pyannote."""

import os

import numpy as np
import pandas as pd

# config
DIARIZATION_REPO = "pyannote/speaker-diarization-community-1"
MIN_SPEAKERS = 2
MAX_SPEAKERS = 8
# speech regions closer than this are joined, and each region is padded on both sides
REGION_GAP_S = 2.0
REGION_PAD_S = 0.3


# helpers
def load_pipeline():
    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(DIARIZATION_REPO, token=os.environ.get("HF_TOKEN"))
    if pipeline is None:
        raise RuntimeError(f"could not load {DIARIZATION_REPO}, run uv run scripts/check_setup.py")
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    pipeline.to(torch.device(device))
    return pipeline


def annotation_to_frame(annotation):
    rows = [{"speaker": spk, "start": seg.start, "end": seg.end}
            for seg, _, spk in annotation.itertracks(yield_label=True)]
    return pd.DataFrame(rows, columns=["speaker", "start", "end"])


# main
def speech_regions(turns, gap=REGION_GAP_S, pad=REGION_PAD_S):
    """merge diarized turns from all speakers into padded (start, end) speech regions."""
    regions = []
    for start, end in sorted(zip(turns["start"], turns["end"])):
        start, end = max(0.0, start - pad), end + pad
        if regions and start - regions[-1][1] <= gap:
            regions[-1][1] = max(regions[-1][1], end)
        else:
            regions.append([start, end])
    return [tuple(r) for r in regions]


def diarize(wav, min_speakers=MIN_SPEAKERS, max_speakers=MAX_SPEAKERS, num_speakers=None):
    """returns (turns, exclusive_turns, embeddings) where embeddings maps speaker to a vector."""
    import soundfile as sf
    import torch

    # pass the waveform in memory so decoding does not depend on torchcodec
    samples, rate = sf.read(str(wav), dtype="float32")
    audio = {"waveform": torch.from_numpy(samples).unsqueeze(0), "sample_rate": rate}
    bounds = ({"num_speakers": num_speakers} if num_speakers
              else {"min_speakers": min_speakers, "max_speakers": max_speakers})
    output = load_pipeline()(audio, **bounds)

    turns = annotation_to_frame(output.speaker_diarization)
    exclusive = annotation_to_frame(output.exclusive_speaker_diarization)
    labels = output.speaker_diarization.labels()
    embeddings = {spk: np.asarray(output.speaker_embeddings[i]) for i, spk in enumerate(labels)}
    return turns, exclusive, embeddings
