"""local voice profiles. one folder per person under data/profiles, one .npy per entry.

data/profiles/<slug>/index.csv lists every entry so profiles can be pruned or re-averaged.
the profile vector is always the mean of the current entries, computed at load time.
"""

import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from local_transcription.embed import normalize

# config
PROFILE_DIR = Path(__file__).parent.parent / "data" / "profiles"
INDEX_COLUMNS = ["entry_id", "name", "added", "method", "source_type", "source_file",
                 "start", "end", "seconds"]


# helpers
def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot build a folder name from {name!r}")
    return slug


def person_dir(name):
    return PROFILE_DIR / slugify(name)


def read_index(folder):
    path = folder / "index.csv"
    if not path.exists():
        return pd.DataFrame(columns=INDEX_COLUMNS)
    return pd.read_csv(path, dtype={"entry_id": str})


def write_index(folder, index):
    index.to_csv(folder / "index.csv", index=False)


# main
def add_entry(name, vector, method, source_file="", source_type="other",
              start=None, end=None, seconds=None):
    """save one embedding for a person. returns the new entry id."""
    folder = person_dir(name)
    (folder / "entries").mkdir(parents=True, exist_ok=True)
    entry_id = uuid.uuid4().hex[:10]
    np.save(folder / "entries" / f"{entry_id}.npy", normalize(vector))
    row = {"entry_id": entry_id, "name": name, "added": datetime.now().isoformat(timespec="seconds"),
           "method": method, "source_type": source_type, "source_file": str(source_file),
           "start": start, "end": end, "seconds": None if seconds is None else round(seconds, 1)}
    index = read_index(folder)
    index = pd.concat([index, pd.DataFrame([row])], ignore_index=True) if len(index) else pd.DataFrame([row])
    write_index(folder, index)
    return entry_id


def all_entries():
    """every entry for every person as one dataframe."""
    if not PROFILE_DIR.exists():
        return pd.DataFrame(columns=INDEX_COLUMNS)
    frames = [read_index(d) for d in sorted(PROFILE_DIR.iterdir()) if d.is_dir()]
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=INDEX_COLUMNS)


def load_profiles():
    """{name: (mean vector, entry count)} built from the entries on disk."""
    profiles = {}
    for name, group in all_entries().groupby("name"):
        folder = person_dir(name)
        vectors = [np.load(folder / "entries" / f"{e}.npy") for e in group["entry_id"]
                   if (folder / "entries" / f"{e}.npy").exists()]
        if vectors:
            profiles[name] = (normalize(np.mean(vectors, axis=0)), len(vectors))
    return profiles


def entry_similarities(name):
    """cosine similarity of each entry to the person's mean, useful for spotting bad entries."""
    folder = person_dir(name)
    index = read_index(folder)
    if index.empty:
        return index
    vectors = np.array([np.load(folder / "entries" / f"{e}.npy") for e in index["entry_id"]])
    mean = normalize(vectors.mean(axis=0))
    return index.assign(similarity_to_mean=(vectors @ mean).round(3))


def remove_entry(entry_id):
    """delete one entry from whichever person holds it. returns the person's name or None."""
    for folder in PROFILE_DIR.iterdir() if PROFILE_DIR.exists() else []:
        index = read_index(folder)
        if entry_id in set(index["entry_id"]):
            (folder / "entries" / f"{entry_id}.npy").unlink(missing_ok=True)
            name = index.loc[index["entry_id"] == entry_id, "name"].iloc[0]
            index = index[index["entry_id"] != entry_id]
            if index.empty:
                shutil.rmtree(folder)
            else:
                write_index(folder, index)
            return name
    return None


def delete_person(name):
    """remove a person's whole profile from disk. returns True if it existed."""
    folder = person_dir(name)
    if not folder.exists():
        return False
    shutil.rmtree(folder)
    return True
