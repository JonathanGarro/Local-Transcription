"""build the mac app in mac/ into an app you can keep in the dock, with your own icon.

usage:
    uv run make_app.py                      # uses assets/icon.png, makes a plain one if missing
    uv run make_app.py --icon ~/Pictures/my-icon.png

it needs xcode, whose tools have swiftui's macros. the app goes in /Applications/Boswell.app,
or is rebuilt where it is if you moved it to ~/Applications. any square png works, 1024x1024
looks best.

the app doesn't need this project folder to run. the server's code is copied into the app,
with ffmpeg and ffprobe built from source under the lgpl (scripts/build_ffmpeg.py), and uv, which installs python and the server's packages from uv.lock into
~/Library/Application Support/Boswell/env when the app first opens, and again only when an
update changes uv.lock. the library is in the library folder (see local_transcription/paths.py).
run this again after changing the code or the icon, since the app runs its own copy of the code.
"""

import argparse
import json
import tomllib
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# config
BASE_DIR = Path(__file__).parent.resolve()
APP_NAME = "Boswell"
ICON_PNG = BASE_DIR / "assets" / "icon.png"
BUNDLE_ID = "io.github.jonathangarro.boswell"
# the id before, whose preferences the app copies over the first time it opens
OLD_BUNDLE_ID = "local.transcription.native"
# boswell://meeting?id=... links, from answers and from to-dos in things, open the app
URL_SCHEME = "boswell"
MAC_DIR = BASE_DIR / "mac"
# what the app needs of this folder to run the server, copied into Contents/Resources/server.
# pyproject.toml and uv.lock are what the python environment is installed from
SERVER_FILES = ["app.py", "local_transcription", "pyproject.toml", "uv.lock", ".python-version"]
XCODE_DIR = Path("/Applications/Xcode.app/Contents/Developer")
INSTALL_DIRS = [Path("/Applications"), Path.home() / "Applications"]


# helpers
def make_default_icon(path):
    """a plain rounded square with a waveform, until you choose your own."""
    from PIL import Image, ImageDraw

    size = 1024
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # full bleed, since macos 26 masks icons to its own squircle and sets anything
    # smaller on a dark backdrop
    top, bottom = (47, 111, 222), (24, 52, 110)
    gradient = Image.new("RGBA", (size, size))
    for y in range(size):
        t = y / size
        gradient.paste(tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,), (0, y, size, y + 1))
    image.paste(gradient)
    heights = [150, 320, 520, 370, 640, 450, 250, 410, 190]
    bar, gap = 54, 37
    left = (size - (len(heights) * bar + (len(heights) - 1) * gap)) // 2
    for i, h in enumerate(heights):
        x = left + i * (bar + gap)
        draw.rounded_rectangle((x, size // 2 - h // 2, x + bar, size // 2 + h // 2), bar // 2, fill=(255, 255, 255, 235))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def xcode_env():
    """the environment for xcode's tools. swiftui's macros and actool only ship with
    xcode, not the command line tools."""
    env = dict(os.environ)
    if "DEVELOPER_DIR" not in env and XCODE_DIR.exists():
        env["DEVELOPER_DIR"] = str(XCODE_DIR)
    return env


def make_icon(png, resources):
    """compile a png into the asset catalog mac apps use. macos 26 shrinks icons that
    only come as an .icns onto a dark squircle, so it goes in an icon composer icon."""
    with tempfile.TemporaryDirectory() as tmp:
        icon = Path(tmp) / "AppIcon.icon"
        (icon / "Assets").mkdir(parents=True)
        shutil.copy2(png, icon / "Assets" / "art.png")
        (icon / "icon.json").write_text(json.dumps({
            "groups": [{"layers": [{"image-name": "art.png", "name": "art", "glass": False}]}],
            "supported-platforms": {"squares": "shared"}}))
        subprocess.run(["xcrun", "actool", str(icon), "--compile", str(resources), "--platform", "macosx",
                        "--minimum-deployment-target", "26.0", "--app-icon", "AppIcon",
                        "--output-partial-info-plist", str(Path(tmp) / "partial.plist")],
                       env=xcode_env(), check=True, capture_output=True)


def version():
    """(the version from pyproject.toml, the build number), which is the commit count."""
    project = tomllib.loads((BASE_DIR / "pyproject.toml").read_text())["project"]["version"]
    count = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=BASE_DIR, capture_output=True, text=True)
    return project, count.stdout.strip() or "1"


def signing_identity():
    """the apple development certificate xcode made, if there is one. macos remembers
    permissions like documents folder access by the app's signature, and an ad hoc one
    changes with every build, so it would ask again after each rebuild."""
    found = subprocess.run(["security", "find-identity", "-v", "-p", "codesigning"],
                           capture_output=True, text=True).stdout
    for line in found.splitlines():
        if '"Apple Development:' in line:
            return line.split()[1]
    return "-"


def copy_env_file():
    """the app reads .env from the library folder, so a first build copies this folder's there."""
    sys.path.insert(0, str(BASE_DIR))
    from local_transcription import paths

    source, target = BASE_DIR / ".env", paths.HOME / ".env"
    if source.exists() and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        target.chmod(0o600)
        print(f"copied .env to {target}, which is the one the app reads")


def build_native():
    """compile the swiftui app in mac/ and return the binary."""
    env = xcode_env()
    subprocess.run(["swift", "build", "-c", "release"], cwd=MAC_DIR, env=env, check=True)
    path = subprocess.run(["swift", "build", "-c", "release", "--show-bin-path"], cwd=MAC_DIR, env=env,
                          check=True, capture_output=True, text=True).stdout.strip()
    return Path(path) / APP_NAME


# main
def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--icon", type=Path, help=f"square png to use, copied to {ICON_PNG.relative_to(BASE_DIR)}")
    args = parser.parse_args()
    # rebuild in place if the app was moved to /Applications, so there is only one copy
    app_dir = next((d / f"{APP_NAME}.app" for d in INSTALL_DIRS if (d / f"{APP_NAME}.app").exists()),
                   INSTALL_DIRS[0] / f"{APP_NAME}.app")

    if args.icon:
        source = args.icon.expanduser()
        if not source.exists():
            raise SystemExit(f"no file at {source}")
        ICON_PNG.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != ICON_PNG:
            # sips also converts jpg or heic to png
            subprocess.run(["sips", "-s", "format", "png", str(source), "--out", str(ICON_PNG)],
                           check=True, capture_output=True)
    elif not ICON_PNG.exists():
        make_default_icon(ICON_PNG)
        print(f"made a plain icon at {ICON_PNG.relative_to(BASE_DIR)}, pass --icon to use your own")

    binary = build_native()
    sys.path.insert(0, str(BASE_DIR / "scripts"))
    import build_ffmpeg

    ffmpeg = build_ffmpeg.build()
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv isn't on the PATH, and the app ships a copy of it")
    copy_env_file()
    if app_dir.exists():
        shutil.rmtree(app_dir)
    contents = app_dir / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    make_icon(ICON_PNG, contents / "Resources")
    server = contents / "Resources" / "server"
    server.mkdir()
    for name in SERVER_FILES:
        source = BASE_DIR / name
        if source.is_dir():
            shutil.copytree(source, server / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, server / name)
    shutil.copy2(binary, contents / "MacOS" / APP_NAME)
    # the uv the app installs python with, the one this project is built with
    shutil.copy2(Path(uv).resolve(), contents / "MacOS" / "uv")
    # ffmpeg reads the recordings. the lgpl asks for its license and how it was built to go along
    for name in build_ffmpeg.BINARIES:
        shutil.copy2(ffmpeg / "bin" / name, contents / "MacOS" / name)
    licenses = contents / "Resources" / "licenses"
    (licenses / "ffmpeg").mkdir(parents=True)
    for name in ("COPYING.LGPLv2.1", "README.txt"):
        shutil.copy2(ffmpeg / name, licenses / "ffmpeg" / name)
    # uv is mit or apache 2.0, at the holder's choice, so both go along
    shutil.copytree(BASE_DIR / "assets" / "licenses" / "uv", licenses / "uv")
    uv_version = subprocess.run([uv, "--version"], capture_output=True, text=True).stdout.strip()
    (licenses / "uv" / "README.txt").write_text(
        f"Boswell includes {uv_version}, from Astral (https://github.com/astral-sh/uv), unchanged. It's licensed under "
        "either the MIT license or the Apache License 2.0, in LICENSE-MIT and LICENSE-APACHE.\n")
    (licenses / "boswell").mkdir()
    shutil.copy2(BASE_DIR / "LICENSE", licenses / "boswell" / "LICENSE")
    print(subprocess.run([uv, "--version"], capture_output=True, text=True).stdout.strip(), "goes in the app")
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump({"CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME,
                       "CFBundleIdentifier": BUNDLE_ID, "CFBundleExecutable": APP_NAME,
                       "CFBundleIconFile": "AppIcon", "CFBundleIconName": "AppIcon", "CFBundlePackageType": "APPL",
                       "CFBundleShortVersionString": version()[0], "CFBundleVersion": version()[1],
                       "LSMinimumSystemVersion": "26.0", "BoswellOldBundleID": OLD_BUNDLE_ID,
                       "NSHighResolutionCapable": True,
                       "CFBundleURLTypes": [{"CFBundleURLName": f"{BUNDLE_ID}.links",
                                             "CFBundleURLSchemes": [URL_SCHEME]}]}, f)
    identity = signing_identity()
    if identity == "-":
        print("no apple development certificate, so macos will ask for folder access again after each rebuild")
    # the helpers are signed first, since the app's signature covers the code inside it
    for helper in ["uv", *build_ffmpeg.BINARIES]:
        subprocess.run(["codesign", "--force", "--sign", identity, str(contents / "MacOS" / helper)],
                       check=True, capture_output=True)
    subprocess.run(["codesign", "--force", "--sign", identity, str(app_dir)], check=True, capture_output=True)
    # tell finder and the dock to pick up the new icon, and launch services the link type
    subprocess.run(["touch", str(app_dir)], check=False)
    subprocess.run(["/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister",
                    "-f", str(app_dir)], check=False)
    print(f"built {app_dir}")
    print("open it, then right-click its dock icon, Options, Keep in Dock")


if __name__ == "__main__":
    main()
