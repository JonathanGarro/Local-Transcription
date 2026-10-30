"""audio normalization and channel handling with ffmpeg."""

import subprocess
from pathlib import Path

import numpy as np

# config
SAMPLE_RATE = 16000
# a channel this many db quieter than the loudest one is treated as empty. 40 db leaves out
# silence and hiss but keeps a quiet mic, such as airpods', or someone who mostly listened
DEAD_CHANNEL_DB = 40.0
# channels whose waveforms correlate less than this carry different sources, such as
# audio hijack writing the mic on one side and the zoom app on the other
SPLIT_MAX_CORR = 0.2
# energy-based speech detection for a clean, single-person mic channel
VAD_FRAME_S = 0.1
# normal speech on the mic peaks near -22 db, background noise sits between -40 and -60 db
VAD_THRESHOLD_DB = -40.0
VAD_MIN_GAP_S = 0.8
VAD_MIN_SPEECH_S = 0.3
VAD_PAD_S = 0.2


# helpers
def _run(cmd):
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def probe(src):
    out = _run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=channels,sample_rate:format=duration",
                "-of", "default=nw=1", str(src)]).decode()
    info = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    # an empty or unfinished file has no audio stream, or a duration of N/A
    if "channels" not in info or info.get("duration", "N/A") == "N/A":
        raise ValueError(f"{Path(src).name} has no audio in it")
    return {"channels": int(info["channels"]), "sample_rate": int(info["sample_rate"]),
            "duration": float(info["duration"])}


def decode(src, channels):
    # all channels as float32 at 16 khz, shape (samples, channels)
    raw = _run(["ffmpeg", "-v", "error", "-i", str(src), "-ar", str(SAMPLE_RATE),
                "-f", "f32le", "-"])
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, channels)


def _write_mono(src, dst, pan=None):
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    if pan:
        cmd += ["-af", f"pan=mono|c0={pan}"]
    cmd += ["-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(dst)]
    _run(cmd)


# main
def analyze(src):
    """probe a file and, for multichannel audio, measure per-channel levels and correlation."""
    info = probe(src)
    info.update(levels=None, correlation=None, live=list(range(info["channels"])))
    if info["channels"] == 1:
        return info
    samples = decode(src, info["channels"]).astype(np.float64)
    levels = 20 * np.log10(np.sqrt((samples ** 2).mean(axis=0)) + 1e-12)
    info["levels"] = [round(float(db), 1) for db in levels]
    info["live"] = [i for i, db in enumerate(levels) if db >= levels.max() - DEAD_CHANNEL_DB]
    if info["channels"] == 2:
        info["correlation"] = round(float(np.corrcoef(samples[:, 0], samples[:, 1])[0, 1]), 3)
    return info


def is_split(info):
    """true for stereo files whose two channels hold unrelated sources."""
    return info["channels"] == 2 and info["correlation"] is not None and \
        abs(info["correlation"]) < SPLIT_MAX_CORR


def normalize(src, dst, info=None):
    """convert any input to 16 khz mono 16-bit wav. near-silent channels are dropped
    instead of mixed in. returns the analysis with a short description added."""
    info = info or analyze(src)
    if info["channels"] == 1:
        how = "mono source"
        _write_mono(src, dst)
    else:
        desc = ", ".join(f"ch{i} {db:.1f} dB" for i, db in enumerate(info["levels"]))
        live = info["live"]
        if len(live) == info["channels"]:
            how = f"downmix ({desc})"
            _write_mono(src, dst)
        else:
            how = f"kept ch{live} ({desc})"
            _write_mono(src, dst, "+".join(f"{1 / len(live):.4f}*c{i}" for i in live))
    return {**info, "channel_handling": how}


def extract_channel(src, dst, channel):
    """write a single channel as 16 khz mono wav."""
    _write_mono(src, dst, f"c{channel}")


def clip(src, start, end, channel=None):
    """a stretch of a recording as 16 khz mono wav bytes, one channel of it if given.
    seeking before the input skips decoding the rest, so this is quick even on a long file."""
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(0, start):.3f}", "-t", f"{max(0, end - start):.3f}", "-i", str(src)]
    if channel is not None:
        cmd += ["-af", f"pan=mono|c0=c{channel}"]
    return _run(cmd + ["-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", "-f", "wav", "-"])


def frame_energy(wav):
    """the level of each VAD_FRAME_S frame in db, and the duration in seconds."""
    import soundfile as sf

    samples, rate = sf.read(str(wav), dtype="float32")
    frame = int(VAD_FRAME_S * rate)
    n = len(samples) // frame
    energy = 10 * np.log10((samples[:n * frame].reshape(n, frame).astype(np.float64) ** 2).mean(axis=1) + 1e-12)
    return energy, len(samples) / rate


def energy_regions(wav, threshold_db=VAD_THRESHOLD_DB):
    """speech regions on a clean single-speaker channel, found by frame energy."""
    energy, duration = frame_energy(wav)
    active = np.flatnonzero(energy > threshold_db)
    regions = []
    for i in active:
        start, end = i * VAD_FRAME_S, (i + 1) * VAD_FRAME_S
        if regions and start - regions[-1][1] <= VAD_MIN_GAP_S:
            regions[-1][1] = end
        else:
            regions.append([start, end])
    return [(max(0.0, s - VAD_PAD_S), min(duration, e + VAD_PAD_S))
            for s, e in regions if e - s >= VAD_MIN_SPEECH_S]
