"""echo cancellation for call recordings made on speakers rather than headphones.

with speakers, the mic hears the call as well as you, so in split mode the other people's
words would be transcribed again as yours. the call channel holds exactly what the mac
played, so it's the reference an echo canceller needs: an adaptive filter learns how the
speakers and the room turn it into what the mic hears, and subtracts that. what it misses is
then turned down wherever the remaining echo outweighs what's left, frequency by frequency.

this runs on the 16 khz channels after a recording is split, offline, so it can take its
time, about half a minute for an hour. the result is only used when it took a clear amount
of echo out while the call was talking, so a recording made on headphones is left as it was.
"""

import numpy as np
import soundfile as sf

# config
# block size, and how long an echo the filter can follow: half a second covers the speakers'
# delay and a room's reflections
BLOCK = 512
FILTER_S = 0.5
# how fast the filter adapts, at least and at most, as a share of each step it could take
STEP_MIN, STEP_MAX = 0.05, 0.7
# the residual suppression: how hard it leans on the echo estimate, and the least it lets through
OVERSUBTRACT = 4.0
FLOOR = 0.05
# the call counts as talking when its block is louder than this
REF_ACTIVE_DB = -45.0
# the echo taken out, while the call talked, that makes the result worth keeping
MIN_REDUCTION_DB = 6.0


# helpers
def _db(power):
    return 10 * np.log10(power + 1e-12)


def _adaptive_filter(mic, ref):
    """the mic with the linear echo of ref taken out, and the echo estimate, by a partitioned
    block frequency-domain nlms filter (overlap-save). the step size in each frequency follows
    how much of the error is still echo, so it learns fast while only the call is talking and
    barely moves while you talk over it."""
    parts = max(1, int(FILTER_S * 16000) // BLOCK)
    bins = BLOCK + 1
    blocks = len(mic) // BLOCK
    weights = np.zeros((parts, bins), complex)
    history = np.zeros((parts, bins), complex)
    power = np.full(bins, 1e-6)
    out = np.zeros(blocks * BLOCK, np.float32)
    echo = np.zeros(blocks * BLOCK, np.float32)
    previous = np.zeros(BLOCK)
    for b in range(blocks):
        frame = ref[b * BLOCK:(b + 1) * BLOCK]
        history = np.roll(history, 1, axis=0)
        history[0] = np.fft.rfft(np.concatenate([previous, frame]))
        previous = frame
        estimate = np.fft.irfft((weights * history).sum(axis=0))[BLOCK:]
        error = mic[b * BLOCK:(b + 1) * BLOCK] - estimate
        out[b * BLOCK:(b + 1) * BLOCK] = error
        echo[b * BLOCK:(b + 1) * BLOCK] = estimate
        error_f = np.fft.rfft(np.concatenate([np.zeros(BLOCK), error]))
        echo_f = np.fft.rfft(np.concatenate([np.zeros(BLOCK), estimate]))
        power = 0.9 * power + 0.1 * (np.abs(history[0]) ** 2)
        step = np.clip(np.abs(echo_f) ** 2 / (np.abs(error_f) ** 2 + 1e-9), STEP_MIN, STEP_MAX)
        weights += step * np.conj(history) * error_f / (parts * power + 1e-6)
    return out, echo


def _suppress(signal, echo):
    """turn down each frequency where the echo estimate outweighs the signal left. half
    overlapping frames, done a batch at a time so an hour doesn't need gigabytes."""
    window = np.hanning(BLOCK).astype(np.float32)
    hop = BLOCK // 2
    frames = 1 + (len(signal) - BLOCK) // hop
    if frames < 1:
        return signal
    halves = np.zeros((frames + 1, hop), np.float32)
    previous = np.ones(BLOCK // 2 + 1)
    batch = 4096
    for first in range(0, frames, batch):
        count = min(batch, frames - first)
        index = np.arange(BLOCK)[None, :] + hop * np.arange(first, first + count)[:, None]
        spectrum = np.fft.rfft(signal[index] * window, axis=1)
        echo_power = np.abs(np.fft.rfft(echo[index] * window, axis=1)) ** 2
        gain = np.clip(1 - OVERSUBTRACT * echo_power / (np.abs(spectrum) ** 2 + 1e-9), FLOOR, 1)
        # smoothed over time, so the gain doesn't flutter
        for f in range(count):
            previous = gain[f] = 0.6 * gain[f] + 0.4 * previous
        out = np.fft.irfft(spectrum * gain, axis=1).astype(np.float32) * window
        halves[first:first + count] += out[:, :hop]
        halves[first + 1:first + count + 1] += out[:, hop:]
    # hann windows applied twice and half overlapping add up to this, away from the ends
    norm = window[:hop] ** 2 + window[hop:] ** 2
    result = signal.copy()
    joined = (halves / np.maximum(norm, 1e-3)).ravel()
    result[hop:frames * hop] = joined[hop:frames * hop]
    return result


def _reduction(mic, cleaned, ref):
    """how much quieter the mic got, in db, in the blocks where only the call was talking:
    loud on the call side, and the mic no louder than the echo would make it."""
    n = min(len(mic), len(cleaned), len(ref)) // BLOCK
    shape = (n, BLOCK)
    ref_db = _db((ref[:n * BLOCK].reshape(shape) ** 2).mean(axis=1))
    mic_power = (mic[:n * BLOCK].reshape(shape) ** 2).mean(axis=1)
    clean_power = (cleaned[:n * BLOCK].reshape(shape) ** 2).mean(axis=1)
    talking = ref_db > REF_ACTIVE_DB
    if talking.sum() < 20:
        return 0.0
    # of the blocks with the call talking, the quieter half on the mic, which leaves out you
    # talking over them
    quiet = talking & (mic_power <= np.median(mic_power[talking]))
    return float(_db(mic_power[quiet].mean()) - _db(clean_power[quiet].mean()))


# main
def cancel(mic_wav, ref_wav, progress=print):
    """take the call's echo out of the mic channel, in place, when there is any. returns the
    db of echo taken out, or None when the mic was left as it was."""
    mic, rate = sf.read(str(mic_wav), dtype="float32")
    ref, _ = sf.read(str(ref_wav), dtype="float32")
    n = min(len(mic), len(ref))
    if n < BLOCK * 50:
        return None
    linear, echo = _adaptive_filter(mic[:n], ref[:n])
    cleaned = _suppress(linear, echo)
    reduction = _reduction(mic[:len(cleaned)], cleaned, ref[:len(cleaned)])
    if reduction < MIN_REDUCTION_DB:
        progress(f"  no echo of the call on the mic ({reduction:.1f} dB), left as it was")
        return None
    out = np.concatenate([cleaned, mic[len(cleaned):]])
    sf.write(str(mic_wav), out, rate, subtype="PCM_16")
    progress(f"  took the call's echo out of the mic, {reduction:.1f} dB quieter while the call talked")
    return reduction
