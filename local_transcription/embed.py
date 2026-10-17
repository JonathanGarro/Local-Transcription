"""speaker embeddings from the wespeaker model bundled with the diarization pipeline.

enrollment clips and meeting speakers both go through this module so their vectors
live in the same space.
"""

import numpy as np

from local_transcription.diarize import DIARIZATION_REPO

# config
# turns shorter than this give noisy embeddings and are skipped when possible
MIN_TURN_S = 1.5
# trim turn edges, where overlap and boundary errors live
EDGE_TRIM_S = 0.2
# long stretches are cut into chunks of this length before embedding
CHUNK_S = 10.0
# cap the work per speaker by using only the longest turns
MAX_TURNS = 40


# helpers
def normalize(vec):
    vec = np.asarray(vec, dtype=np.float64)
    return vec / (np.linalg.norm(vec) + 1e-12)


def load_audio(wav):
    import soundfile as sf
    import torch

    samples, rate = sf.read(str(wav), dtype="float32")
    return {"waveform": torch.from_numpy(samples).unsqueeze(0), "sample_rate": rate}


def chunks(start, end, size=CHUNK_S, min_size=MIN_TURN_S):
    # split [start, end] into pieces of about `size` seconds, dropping a short remainder
    pieces = []
    while end - start >= min_size:
        stop = min(end, start + size)
        if end - stop < min_size:
            stop = end
        pieces.append((start, stop))
        start = stop
    return pieces


# main
class Embedder:
    def __init__(self):
        import torch
        from pyannote.audio import Inference, Model

        model = Model.from_pretrained(DIARIZATION_REPO, subfolder="embedding")
        self.inference = Inference(model, window="whole")
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.inference.to(torch.device(device))

    def embed_spans(self, audio, spans):
        """duration-weighted mean of normalized chunk embeddings over (start, end) spans."""
        from pyannote.core import Segment

        duration = audio["waveform"].shape[-1] / audio["sample_rate"]
        vectors, weights = [], []
        for start, end in spans:
            # turn ends rounded past the last sample cannot be cropped
            for a, b in chunks(start, min(end, duration)):
                vectors.append(normalize(self.inference.crop(audio, Segment(a, b))))
                weights.append(b - a)
        if not vectors:
            return None, 0.0
        return normalize(np.average(vectors, axis=0, weights=weights)), float(sum(weights))

    def clip(self, audio, start=None, end=None):
        """embedding for one stretch of audio, the whole file by default."""
        duration = audio["waveform"].shape[-1] / audio["sample_rate"]
        start = 0.0 if start is None else start
        end = duration if end is None else min(end, duration)
        return self.embed_spans(audio, [(start, end)])

    def speakers(self, audio, turns):
        """one embedding per diarized speaker from its longest exclusive turns.
        returns {speaker: (vector, seconds_used)}."""
        duration = audio["waveform"].shape[-1] / audio["sample_rate"]
        turns = turns.assign(end=turns["end"].clip(upper=duration))
        turns = turns.assign(seconds=turns["end"] - turns["start"])
        result = {}
        for speaker, group in turns.groupby("speaker"):
            usable = group[group["seconds"] >= MIN_TURN_S + 2 * EDGE_TRIM_S]
            if usable.empty:
                # nothing long enough, fall back to the longest turn untrimmed
                usable, trim = group.nlargest(1, "seconds"), 0.0
            else:
                usable, trim = usable.nlargest(MAX_TURNS, "seconds"), EDGE_TRIM_S
            spans = [(s + trim, e - trim) for s, e in zip(usable["start"], usable["end"])]
            vector, seconds = self.embed_spans(audio, spans)
            if vector is None:
                # very short speakers: embed whatever exists, even under the minimum
                from pyannote.core import Segment
                s, e = spans[0]
                vector, seconds = normalize(self.inference.crop(audio, Segment(s, e))), e - s
            result[speaker] = (vector, seconds)
        return result
