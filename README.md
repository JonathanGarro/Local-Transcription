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

## Usage

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

Confirming re-renders the transcript and adds that speaker's voice from this meeting to their profile. Use `--no-enroll` to rename without touching profiles. Confirming the same speaker again replaces the entry it added before.

### Enrolling from a clip

```
uv run enroll.py "data/priya_sample.wav" "Priya"
uv run enroll.py "data/meeting.mp3" "Priya" --start 5:20 --end 6:10 --source plaud
```

The clip should contain only that person. 20 seconds or more works best.

### Managing profiles

```
uv run manage_profiles.py list
uv run manage_profiles.py show "Priya"          # entries with similarity to the mean
uv run manage_profiles.py remove-entry <id>
uv run manage_profiles.py delete "Priya"
```

Profiles live in `data/profiles/<name>/`, one `.npy` per entry plus an `index.csv`. The profile vector is the mean of the current entries, so pruning takes effect on the next run.

### Custom vocabulary

`data/vocabulary.txt` holds internal terms and names, one per line:

```
GAL prep = gal prep, galprep
```

Every term, plus every profile name, is passed to Whisper as a spelling hint on each 28 second chunk. Variants after `=` are replaced with the term in the final transcript (case-insensitive, whole words), which catches anything the hint misses. The file is under `data/`, so it is never committed.

## Privacy

`data/`, `outputs/`, and `.env` are gitignored, so audio, transcripts, voice profiles, and the token are never committed. Deleting a profile folder removes every trace of that voice.

## Layout

```
local_transcription/   pipeline modules (audio, diarize, asr, merge, embed, match, profiles, render, pipeline)
transcribe.py          process a recording
confirm.py             confirm names and grow profiles
enroll.py              add a voice sample
manage_profiles.py     list, prune, delete profiles
check_setup.py         verify ffmpeg, token, and models
probe_recordings.py    benchmark backends on sample files
```
