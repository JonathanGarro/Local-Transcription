"""markdown transcript output."""

import json
import re
from datetime import datetime
from pathlib import Path

# config
DATE_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})")


# helpers
def fmt_time(seconds):
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def title_and_date(source):
    """pull a date and title out of names like '2026-10-14 - Manager Check-In.mp3'."""
    stem = Path(source).stem
    found = DATE_PATTERN.search(stem)
    if found:
        date = found.group(1)
        title = (stem[:found.start()] + stem[found.end():]).strip(" -_") or stem
    else:
        # no date in the name: use the file's modified date, or today if it has moved
        path = Path(source)
        stamp = datetime.fromtimestamp(path.stat().st_mtime) if path.exists() else datetime.now()
        date, title = stamp.date().isoformat(), stem
    return title, date


def yaml_value(value):
    # json strings and lists are valid yaml and handle quoting for us
    return json.dumps(value, ensure_ascii=False)


# main
def render_markdown(segments, names, source, title=None, date=None):
    """segments: dataframe with speaker, start, end, text. names: {speaker: display name}."""
    auto_title, auto_date = title_and_date(source)
    title, date = title or auto_title, date or auto_date
    order = list(dict.fromkeys(segments["speaker"]))
    attendees = [names.get(spk, spk) for spk in order]

    lines = ["---", f"title: {yaml_value(title)}", f"date: {date}",
             f"attendees: {yaml_value(attendees)}", f"source_file: {yaml_value(Path(source).name)}",
             "---", "", f"# {title}", ""]
    # join back-to-back segments from the same person into one turn
    previous = None
    for row in segments.itertuples():
        name = names.get(row.speaker, row.speaker)
        if name == previous:
            lines[-2] += " " + row.text
            continue
        lines += [f"**{name}** [{fmt_time(row.start)}]", row.text, ""]
        previous = name
    return "\n".join(lines)
