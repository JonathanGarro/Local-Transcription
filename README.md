# Local Transcription

Offline meeting transcription with speaker diarization and voice profile matching, built for Apple Silicon. Audio, transcripts, and voice profiles never leave the machine.

## Pipeline

1. **Normalize** to 16 kHz mono WAV with ffmpeg. Near-silent channels in stereo files are dropped instead of mixed in.
2. **Diarize** with pyannote `speaker-diarization-community-1` on the GPU (MPS), with 2 to 8 speakers by default.
3. **Transcribe** with mlx-whisper `large-v3-turbo`, only inside the speech regions found by diarization. This stops Whisper from inventing text during silence.
4. **Merge** word timestamps with speaker turns. Short fragments at turn edges are folded into the surrounding sentence or turn.
5. **Match** each speaker's embedding against enrolled profiles by cosine similarity. A name is assigned at 0.6 or above, otherwise the speaker is marked unknown and a review file is written.
6. **Render** a markdown transcript with frontmatter (title, date, attendees, source file).

On a 30 minute meeting the whole run takes about 2 to 3 minutes on an M3 Max. mlx-whisper ran 5 to 6x faster than faster-whisper (CPU only on macOS) with near identical output, so it is the default.

## Setup

```
uv sync
cp .env.example .env    # add a Hugging Face read token
uv run check_setup.py   # downloads and tests the models
uv run check_setup.py --offline
```

Accept the model terms at https://hf.co/pyannote/speaker-diarization-community-1 with the account that owns the token. After the offline check passes, uncomment `HF_HUB_OFFLINE=1` in `.env`.

## App

```
uv run app.py              # opens the app window
uv run app.py --browser    # or serve on http://127.0.0.1:8765 and use a browser
```

To keep it in the Dock, build a Mac app with your own icon (any square PNG, 1024x1024 is best):

```
uv run make_app.py --icon ~/Pictures/my-icon.png
```

It goes in `~/Applications/Local Transcription.app` and just starts `app.py` from this folder. Run it again after changing the icon. Without `--icon` it uses `assets/icon.png`. The app's log is `~/Library/Logs/Local Transcription.log`.

- **Inbox:** drop recordings to queue them. One file runs at a time, with live progress. Dropped files are copied into `data/`. A file that was already processed is only re-run if you confirm, because re-running replaces its confirmed names.
- **Review:** each unknown speaker gets a few short clips with what they said, suggested names with scores, and a name field that searches existing people or creates a new one. You can also mark a speaker as the same person as another speaker (diarization split them), leave a speaker unnamed, or name them without adding the voice to their profile. Identified speakers are collapsed, with a "wrong?" link. Nothing is saved until you press Apply.
- **Transcript:** click any line to play from there, show the file in Finder, or copy the markdown.
- **Note:** writes the meeting note (see below), with a choice of model and project.
- **Send to Obsidian:** after you review the note, copies it into `MEETING_NOTES_DIR` as `<date> - <heading>.md` and moves the meeting to the Archive. An existing file with that name is only replaced after a second click. The meeting's folder in `outputs/` is renamed to the same name, so it matches what you see in Obsidian. **Move to Inbox** in the Archive brings a meeting back.

The flow is: drop a file in the Inbox, it is transcribed, you confirm speakers if asked, the note is written automatically with `SUMMARY_BACKEND` once every speaker is named, then you review and send it.
- **People:** everyone in the library with their full name, organization, teams, meetings and voice clips. Edits rewrite every past transcript and note they appear in.

The server listens on 127.0.0.1 only and the page loads nothing from the internet.

## Command line

Put recordings in `data/`. File names like `2026-10-14 - Manager Check-In.mp3` give the transcript its date and title.

```
uv run transcribe.py "data/2026-10-14 - Manager Check-In.mp3" --source plaud
```

Outputs go to `outputs/<recording name>/`:

| File | Contents |
|---|---|
| `transcript.md` | speaker labeled transcript with frontmatter |
| `segments.json` | speaker, name, start, end, text |
| `review.csv` | written when any speaker is unknown |
| `speakers.csv` | match scores per speaker |
| `words.csv`, `turns.csv` | word timestamps and diarization turns |

### Split mode for Audio Hijack recordings

Audio Hijack writes your mic to the left channel and the Zoom app to the right. When a stereo file's two channels are unrelated (correlation under 0.2), the pipeline switches to split mode on its own:

- the mic channel is transcribed and labeled with `MIC_OWNER` from `.env`, with no voice matching needed
- diarization and matching run on the call channel only, and your profile is left out of the candidates
- if the mic channel yields fewer than 5 words, you are treated as absent

Set `MIC_CHANNEL=right` in `.env` if your setup is reversed, or pass `--split off` to treat a file as a single mix.

### Naming speakers

Either fill in the `name` column of `review.csv` and run:

```
uv run confirm.py "outputs/2026-10-14 - Manager Check-In"
```

or name speakers directly:

```
uv run confirm.py "outputs/2026-10-14 - Manager Check-In" SPEAKER_01=Alex SPEAKER_00=Priya
```

Confirming re-renders the transcript and adds that speaker's voice from this meeting to their profile. Use `--no-enroll` to rename without touching profiles, or `--skip SPEAKER_00` to leave a speaker unnamed (for example a video playing on someone's side). Confirming the same speaker again replaces the entry it added before.

### Enrolling from a clip

```
uv run enroll.py "data/priya_sample.wav" "Priya"
uv run enroll.py "data/meeting.mp3" "Priya" --start 5:20 --end 6:10 --source plaud
```

The clip should contain only that person. 20 seconds or more works best.

### Managing profiles

```
uv run manage_profiles.py list
uv run manage_profiles.py show "Priya"              # entries with similarity to the mean
uv run manage_profiles.py rename "Katherine" "Kathryn"  # updates every past transcript
uv run manage_profiles.py merge "Pri" "Priya"       # fold the first person into the second
uv run manage_profiles.py remove-entry <id>
uv run manage_profiles.py delete "Priya"
```

People, voice entries and meetings live in `data/library.db` (SQLite). Each person has a stable id, and each voice entry records the meeting and speaker it came from. The profile vector is the mean of a person's current entries, so pruning takes effect on the next run. Deleting a person removes their entries and vacuums the database file. Past transcripts keep their name as plain text.

### Custom vocabulary

`data/vocabulary.txt` holds internal terms and names, one per line:

```
GAL prep = gal prep, galprep
```

Every term, plus every profile name, is passed to Whisper as a spelling hint on each 28 second chunk. Variants after `=` are replaced with the term in the final transcript (case-insensitive, whole words), which catches anything the hint misses. The file is under `data/`, so it is never committed.

## Meeting notes

```
uv run summarize.py "outputs/2026-10-13 - Dashboard Review"
uv run summarize.py "outputs/2026-10-13 - Dashboard Review" --backend claude --project "Customer Dashboard"
```

Writes `note.md` in the Obsidian layout: frontmatter, Agenda, Discussion, Transcript and Action Items. The model writes the agenda, discussion and your top three to five action items. Code fills in the rest:

- the heading is `# <date> - <meeting name>`, using the name you gave the meeting (or its file name), never one the model makes up

- `people` are the confirmed speakers other than you, as `[[Full Name]]` links, stored one per line the way Obsidian writes list properties
- `teams` and `organizations` are looked up from those people in the library
- `project` is the one you pick, the model never chooses one

Projects come from your Obsidian project index. Set `PROJECT_INDEX_DIR` in `.env` to the folder with one note per project. The app reads only the `project status` line of each note. The picker lists In-Progress projects first, then everything else from A to Z, and you can search by typing. A name that isn't in the index is still accepted.
- the transcript block is the confirmed transcript as `[Full Name] text` lines, never retyped by the model

Set people's details in the People page, or:

```
uv run manage_profiles.py set "Maya" --full-name "Maya Chen" --org "Northwind Foundation" --team "Operations Team"
uv run manage_profiles.py projects --add "Customer Dashboard"
```

Two backends, chosen per note or with `SUMMARY_BACKEND` in `.env`:

| | local (default) | claude |
|---|---|---|
| Model | Qwen3-30B-A3B Instruct, 4-bit MLX (17 GB download) | `claude-opus-5-5`, override with `CLAUDE_MODEL` |
| Data | nothing leaves the Mac | transcript text goes to the Anthropic API, never audio or voice profiles |
| Needs | the model in the Hugging Face cache | `ANTHROPIC_API_KEY` in `.env` |

To download the local model once, run with the hub online:

```
HF_HUB_OFFLINE=0 uv run python -c "from huggingface_hub import snapshot_download; snapshot_download('mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit')"
```

## Privacy

`data/`, `outputs/`, and `.env` are gitignored, so audio, transcripts, voice profiles, and the token are never committed. The only step that can send anything off the Mac is a note written with the claude backend, and only when you choose it. Deleting a person removes their voice entries from `data/library.db`. Each meeting's `speaker_embeddings.npz` in `outputs/` also holds that meeting's voices, so delete the meeting folder too for a complete removal.

## Layout

```
local_transcription/   pipeline modules (audio, diarize, asr, merge, embed, match, library, render, pipeline)
local_transcription/server.py, web/   local api and app pages
app.py                 open the app window
transcribe.py          process a recording
confirm.py             confirm names and grow profiles
summarize.py           write an obsidian meeting note
enroll.py              add a voice sample
manage_profiles.py     list, prune, delete profiles
check_setup.py         verify ffmpeg, token, and models
probe_recordings.py    benchmark backends on sample files
```
