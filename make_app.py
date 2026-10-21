"""build a mac app you can keep in the dock, with your own icon.

usage:
    uv run make_app.py                      # uses assets/icon.png, makes a plain one if missing
    uv run make_app.py --icon ~/Pictures/my-icon.png
    uv run make_app.py --native             # the swiftui version in mac/, needs xcode

the app goes in ~/Applications/Boswell.app, or Boswell Native.app with --native. it only
starts this project's app.py, so the code, data, and outputs stay where they are. run this
again after changing the icon. any square png works, 1024x1024 looks best.
"""

import argparse
import os
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

# config
BASE_DIR = Path(__file__).parent.resolve()
APP_NAME = "Boswell"
NATIVE_NAME = "Boswell Native"
ICON_PNG = BASE_DIR / "assets" / "icon.png"
LOG_FILE = Path.home() / "Library" / "Logs" / f"{APP_NAME}.log"
BUNDLE_ID = "local.transcription.app"
NATIVE_BUNDLE_ID = "local.transcription.native"
MAC_DIR = BASE_DIR / "mac"
XCODE_DIR = Path("/Applications/Xcode.app/Contents/Developer")
ICON_SIZES = [16, 32, 128, 256, 512]


# helpers
def make_default_icon(path):
    """a plain rounded square with a waveform, until you choose your own."""
    from PIL import Image, ImageDraw

    size = 1024
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # macos icons sit inside a margin, with the rounded square at about 80 percent
    inset, radius = 100, 185
    top, bottom = (47, 111, 222), (24, 52, 110)
    gradient = Image.new("RGBA", (size, size))
    for y in range(size):
        t = y / size
        gradient.paste(tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,), (0, y, size, y + 1))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((inset, inset, size - inset, size - inset), radius, fill=255)
    image.paste(gradient, mask=mask)
    heights = [120, 260, 420, 300, 520, 360, 200, 330, 150]
    bar, gap = 44, 30
    left = (size - (len(heights) * bar + (len(heights) - 1) * gap)) // 2
    for i, h in enumerate(heights):
        x = left + i * (bar + gap)
        draw.rounded_rectangle((x, size // 2 - h // 2, x + bar, size // 2 + h // 2), bar // 2, fill=(255, 255, 255, 235))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def make_icns(png, target):
    """convert a png into the .icns file mac apps use."""
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for size in ICON_SIZES:
            for scale in (1, 2):
                name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
                subprocess.run(["sips", "-z", str(size * scale), str(size * scale), str(png),
                                "--out", str(iconset / name)], check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(target)], check=True)


def launcher_script():
    # exec keeps one process, so the dock shows one icon for the app
    # apps opened from finder get a bare PATH, so add homebrew for ffmpeg and ffprobe
    python = BASE_DIR / ".venv" / "bin" / "python"
    return f"""#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "{BASE_DIR}" || exit 1
exec "{python}" app.py >> "{LOG_FILE}" 2>&1
"""


def build_native():
    """compile the swiftui app in mac/ and return the binary."""
    env = dict(os.environ)
    # swiftui's macros only ship with xcode, not the command line tools
    if "DEVELOPER_DIR" not in env and XCODE_DIR.exists():
        env["DEVELOPER_DIR"] = str(XCODE_DIR)
    subprocess.run(["swift", "build", "-c", "release"], cwd=MAC_DIR, env=env, check=True)
    path = subprocess.run(["swift", "build", "-c", "release", "--show-bin-path"], cwd=MAC_DIR, env=env,
                          check=True, capture_output=True, text=True).stdout.strip()
    return Path(path) / APP_NAME


# main
def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--icon", type=Path, help=f"square png to use, copied to {ICON_PNG.relative_to(BASE_DIR)}")
    parser.add_argument("--native", action="store_true", help="build the swiftui version in mac/")
    args = parser.parse_args()
    name = NATIVE_NAME if args.native else APP_NAME
    app_dir = Path.home() / "Applications" / f"{name}.app"

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

    binary = build_native() if args.native else None
    if app_dir.exists():
        shutil.rmtree(app_dir)
    contents = app_dir / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    make_icns(ICON_PNG, contents / "Resources" / "icon.icns")
    executable = contents / "MacOS" / APP_NAME
    if binary:
        shutil.copy2(binary, executable)
    else:
        executable.write_text(launcher_script())
        executable.chmod(0o755)
    info = {"CFBundleName": APP_NAME, "CFBundleDisplayName": name,
            "CFBundleIdentifier": NATIVE_BUNDLE_ID if args.native else BUNDLE_ID, "CFBundleExecutable": APP_NAME,
            "CFBundleIconFile": "icon", "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "1.0", "LSMinimumSystemVersion": "15.0" if args.native else "13.0",
            "NSHighResolutionCapable": True}
    if args.native:
        # the swift app starts the python server from here
        info["BoswellProjectDir"] = str(BASE_DIR)
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump(info, f)
    if binary:
        # an ad hoc signature, so macos treats the bundle as one app
        subprocess.run(["codesign", "--force", "--sign", "-", str(app_dir)], check=True, capture_output=True)
    # tell finder and the dock to pick up the new icon
    subprocess.run(["touch", str(app_dir)], check=False)
    print(f"built {app_dir}")
    print("open it from ~/Applications, then right-click its dock icon, Options, Keep in Dock")


if __name__ == "__main__":
    main()
