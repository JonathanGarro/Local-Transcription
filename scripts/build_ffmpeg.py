"""build the ffmpeg and ffprobe the app ships, from source, under the lgpl.

usage:
    uv run scripts/build_ffmpeg.py      # builds once, then make_app.py uses the cached copy

the release is pinned by checksum (its pgp signature was checked against ffmpeg's release
signing key when the checksum was taken). nothing gpl or nonfree is enabled, so the binaries
are lgpl 2.1, and the app carries that license and a note of how they were built. the build
is only what wemmick needs: decoders for the audio files it takes, output to wav and raw pcm,
and local files only, with no network code. it takes a few minutes the first time.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

# config
BASE_DIR = Path(__file__).resolve().parent.parent
VERSION = "9.0.2"
SOURCE_URL = f"https://ffmpeg.org/releases/ffmpeg-{VERSION}.tar.xz"
SOURCE_SHA256 = "8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e"
BUILD_DIR = BASE_DIR / "mac" / ".build" / f"ffmpeg-{VERSION}"
XCODE_DIR = Path("/Applications/Xcode.app/Contents/Developer")
# audio wemmick may be given: mp3 and m4a from call recorders, opus in ogg from plaud, wav,
# and the other common ones, so an unusual file still opens
DECODERS = ["aac", "aac_fixed", "aac_latm", "alac", "mp1", "mp1float", "mp2", "mp2float", "mp3", "mp3float",
            "opus", "vorbis", "flac", "ac3", "eac3", "wmav1", "wmav2", "amrnb", "amrwb", "adpcm_ima_wav", "adpcm_ms",
            "pcm_alaw", "pcm_mulaw", "pcm_u8", "pcm_s16le", "pcm_s16be", "pcm_s24le", "pcm_s24be", "pcm_s32le",
            "pcm_s32be", "pcm_f32le", "pcm_f32be", "pcm_f64le", "pcm_f64be"]
CONFIGURE = [
    "--enable-static", "--disable-shared", "--disable-debug", "--disable-doc", "--disable-ffplay",
    "--disable-network", "--disable-autodetect", "--disable-devices",
    "--disable-decoders", f"--enable-decoder={','.join(DECODERS)}",
    # what wemmick writes: 16 khz wav files and raw samples piped to python
    "--disable-encoders", "--enable-encoder=pcm_s16le,pcm_f32le",
    "--disable-muxers", "--enable-muxer=wav,pcm_s16le,pcm_f32le",
    "--disable-protocols", "--enable-protocol=file,pipe",
    "--extra-cflags=-mmacosx-version-min=14.0", "--extra-ldflags=-mmacosx-version-min=14.0",
]
BINARIES = ["ffmpeg", "ffprobe"]


# helpers
def _download(url, target):
    with urllib.request.urlopen(url) as response, target.open("wb") as f:
        shutil.copyfileobj(response, f)
    found = hashlib.sha256(target.read_bytes()).hexdigest()
    if found != SOURCE_SHA256:
        raise SystemExit(f"{url} has checksum {found}, not the pinned {SOURCE_SHA256}")


# main
def build():
    """the folder with the built binaries and their license files, building them if needed."""
    if all((BUILD_DIR / "bin" / name).exists() for name in BINARIES):
        return BUILD_DIR
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(Path.home())}
    if XCODE_DIR.exists():
        env["DEVELOPER_DIR"] = str(XCODE_DIR)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / f"ffmpeg-{VERSION}.tar.xz"
        print(f"downloading ffmpeg {VERSION}")
        _download(SOURCE_URL, archive)
        with tarfile.open(archive) as tar:
            tar.extractall(tmp, filter="data")
        source = Path(tmp) / f"ffmpeg-{VERSION}"
        staging = Path(tmp) / "out"
        print("configuring")
        subprocess.run(["./configure", f"--prefix={staging}", *CONFIGURE], cwd=source, env=env, check=True,
                       stdout=subprocess.DEVNULL)
        print("building, a few minutes")
        subprocess.run(["make", f"-j{os.cpu_count() or 4}"], cwd=source, env=env, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["make", "install"], cwd=source, env=env, check=True, stdout=subprocess.DEVNULL)
        if BUILD_DIR.exists():
            shutil.rmtree(BUILD_DIR)
        (BUILD_DIR / "bin").mkdir(parents=True)
        for name in BINARIES:
            shutil.copy2(staging / "bin" / name, BUILD_DIR / "bin" / name)
        shutil.copy2(source / "COPYING.LGPLv2.1", BUILD_DIR / "COPYING.LGPLv2.1")
        (BUILD_DIR / "README.txt").write_text(
            f"Wemmick includes ffmpeg {VERSION} and ffprobe, from the FFmpeg project (https://ffmpeg.org), "
            f"licensed under the GNU Lesser General Public License 2.1, in COPYING.LGPLv2.1.\n\n"
            f"They were built without changes from {SOURCE_URL}\n(sha256 {SOURCE_SHA256}) with:\n\n"
            f"./configure {' '.join(CONFIGURE)}\n")
    return BUILD_DIR


def main():
    folder = build()
    version = subprocess.run([str(folder / "bin" / "ffmpeg"), "-version"], capture_output=True, text=True)
    print(version.stdout.splitlines()[0])
    print(f"built in {folder}")


if __name__ == "__main__":
    sys.exit(main())
