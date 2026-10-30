"""match diarized speakers to enrolled voice profiles."""

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

# config
THRESHOLD = 0.6
COLUMNS = ["speaker", "name", "status", "score", "best_name", "best_score", "second_name", "second_score"]


# main
def match_speakers(speaker_vectors, profiles, threshold=THRESHOLD):
    """speaker_vectors: {speaker: vector}. profiles: {name: (vector, n_entries)}.
    each profile can be assigned to at most one speaker. returns one row per speaker."""
    speakers = sorted(speaker_vectors)
    names = sorted(profiles)
    rows = []
    # no one on the call, as when the owner records only themselves, leaves no speakers
    if not speakers:
        return pd.DataFrame(columns=COLUMNS)
    if not names:
        for spk in speakers:
            rows.append({"speaker": spk, "name": None, "status": "unknown", "score": np.nan,
                         "best_name": None, "best_score": np.nan,
                         "second_name": None, "second_score": np.nan})
        return pd.DataFrame(rows, columns=COLUMNS)

    sims = np.array([[float(speaker_vectors[s] @ profiles[n][0]) for n in names] for s in speakers])
    # one-to-one assignment so two speakers cannot both become the same person
    row_idx, col_idx = linear_sum_assignment(-sims)
    assigned = dict(zip(row_idx, col_idx))

    for i, spk in enumerate(speakers):
        order = np.argsort(-sims[i])
        best = order[0]
        second = order[1] if len(order) > 1 else None
        j = assigned.get(i)
        score = sims[i, j] if j is not None else np.nan
        matched = j is not None and score >= threshold
        rows.append({
            "speaker": spk,
            "name": names[j] if matched else None,
            "status": "matched" if matched else "unknown",
            "score": round(score, 3) if j is not None else np.nan,
            "best_name": names[best], "best_score": round(sims[i, best], 3),
            "second_name": names[second] if second is not None else None,
            "second_score": round(sims[i, second], 3) if second is not None else np.nan,
        })
    return pd.DataFrame(rows, columns=COLUMNS)
