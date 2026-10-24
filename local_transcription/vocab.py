"""custom vocabulary: spelling hints for whisper and exact replacements in the transcript.

data/vocabulary.txt holds one term per line. variants after an equals sign are replaced
with the term, matched case-insensitively on whole words:

    GAL prep = gal prep, galprep
    Kathryn

lines starting with # are ignored. the file lives under data/ so it is never committed.
"""

import re

from local_transcription import paths

# config
VOCAB_PATH = paths.DATA_DIR / "vocabulary.txt"
# whisper reads at most about 220 prompt tokens, so keep the hint well under that
PROMPT_MAX_CHARS = 600
TEMPLATE = """# custom terms and names, one per line. this file is gitignored.
# terms are passed to whisper as spelling hints. profile names are added automatically.
# variants after = are replaced with the term in the transcript (case-insensitive, whole words).
# example:
#   GAL prep = gal prep, galprep
"""


# helpers
def _variant_pattern(variant):
    # allow any run of spaces or hyphens where the variant has one
    parts = [re.escape(p) for p in re.split(r"[\s-]+", variant.strip()) if p]
    return re.compile(r"(?<!\w)" + r"[\s-]*".join(parts) + r"(?!\w)", re.IGNORECASE)


# main
def load(path=VOCAB_PATH):
    """returns (terms, replacements) where replacements is a list of (pattern, term)."""
    if not path.exists():
        return [], []
    terms, replacements = [], []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        term, _, variants = line.partition("=")
        term = term.strip()
        terms.append(term)
        for variant in variants.split(","):
            if variant.strip():
                replacements.append((_variant_pattern(variant), term))
    return terms, replacements


def build_prompt(terms, names=()):
    """a bare comma list. whisper copies spelling from its prompt, and a list leaks into the
    transcript far less than a sentence like 'names and terms: ...' does."""
    # custom terms come first so they survive the length cap
    kept = []
    for item in dict.fromkeys([*terms, *names]):
        if len(", ".join(kept + [item])) + 1 > PROMPT_MAX_CHARS:
            break
        kept.append(item)
    return ", ".join(kept) + "." if kept else None


def apply(text, replacements):
    for pattern, term in replacements:
        text = pattern.sub(term, text)
    return text


def entries(path=VOCAB_PATH):
    """the file as a list of {"term", "variants"}, for editing in the app."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            term, _, variants = line.partition("=")
            rows.append({"term": term.strip(), "variants": [v.strip() for v in variants.split(",") if v.strip()]})
    return rows


def save(rows, path=VOCAB_PATH):
    """write entries back as the file, one term per line under the usual header. a term may
    not hold = or a comma, which the file uses to separate variants."""
    lines = []
    for row in rows:
        term = row["term"].strip()
        if not term:
            continue
        if "=" in term or "," in term:
            raise ValueError(f"{term} can't hold = or a comma")
        variants = [v.strip() for v in row.get("variants", []) if v.strip() and "=" not in v]
        lines.append(f"{term} = {', '.join(variants)}" if variants else term)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE + "\n" + "\n".join(lines) + ("\n" if lines else ""))


def ensure_template(path=VOCAB_PATH):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TEMPLATE)
    return path
