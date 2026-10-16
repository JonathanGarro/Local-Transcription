"""build a mac app you can keep in the dock, with your own icon.

usage:
    uv run make_app.py                      # uses assets/icon.png, makes a plain one if missing
    uv run make_app.py --icon ~/Pictures/my-icon.png

the app goes in ~/Applications/Local Transcription.app. it only starts this project's
app.py, so the code, data, and outputs stay where they are. run this again after changing
the icon. any square png works, 1024x1024 looks best.
"""

import argparse
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

# config
BASE_DIR = Path(__file__).parent.resolve()
APP_NAME = "Local Transcription"
APP_DIR = Path.home() / "Applications" / f"{APP_NAME}.app"
ICON_PNG = BASE_DIR / "assets" / "icon.png"
LOG_FILE = Path.home() / "Library" / "Logs" / f"{APP_NAME}.log"
BUNDLE_ID = "local.transcription.app"
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
    python = BASE_DIR / ".venv" / "bin" / "python"
    return f"""#!/bin/bash
cd "{BASE_DIR}" || exit 1
exec "{python}" app.py >> "{LOG_FILE}" 2>&1
"""


# main
def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--icon", type=Path, help=f"square png to use, copied to {ICON_PNG.relative_to(BASE_DIR)}")
    args = parser.parse_args()

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

    if APP_DIR.exists():
        shutil.rmtree(APP_DIR)
    contents = APP_DIR / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    make_icns(ICON_PNG, contents / "Resources" / "icon.icns")
    executable = contents / "MacOS" / APP_NAME
    executable.write_text(launcher_script())
    executable.chmod(0o755)
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump({"CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME,
                       "CFBundleIdentifier": BUNDLE_ID, "CFBundleExecutable": APP_NAME,
                       "CFBundleIconFile": "icon", "CFBundlePackageType": "APPL",
                       "CFBundleShortVersionString": "1.0", "LSMinimumSystemVersion": "13.0",
                       "NSHighResolutionCapable": True}, f)
    # tell finder and the dock to pick up the new icon
    subprocess.run(["touch", str(APP_DIR)], check=False)
    print(f"built {APP_DIR}")
    print("open it from ~/Applications, then right-click its dock icon, Options, Keep in Dock")


if __name__ == "__main__":
    main()
