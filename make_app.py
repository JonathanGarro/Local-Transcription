"""build the mac app in mac/ into an app you can keep in the dock, with your own icon.

usage:
    uv run make_app.py                      # uses assets/icon.png, makes a plain one if missing
    uv run make_app.py --icon ~/Pictures/my-icon.png

it needs xcode, whose tools have swiftui's macros. the app goes in /Applications/Boswell.app,
or is rebuilt where it is if you moved it to ~/Applications. it starts this project's app.py,
so the code, data, and outputs stay where they are. run this again after changing the code or
the icon. any square png works, 1024x1024 looks best.
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
ICON_PNG = BASE_DIR / "assets" / "icon.png"
# kept from when the app was called boswell native, since macos remembers its folder access by it
BUNDLE_ID = "local.transcription.native"
# boswell://meeting?id=... links, from answers and from to-dos in things, open the app
URL_SCHEME = "boswell"
MAC_DIR = BASE_DIR / "mac"
XCODE_DIR = Path("/Applications/Xcode.app/Contents/Developer")
ICON_SIZES = [16, 32, 128, 256, 512]
INSTALL_DIRS = [Path("/Applications"), Path.home() / "Applications"]


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
    if app_dir.exists():
        shutil.rmtree(app_dir)
    contents = app_dir / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    make_icns(ICON_PNG, contents / "Resources" / "icon.icns")
    shutil.copy2(binary, contents / "MacOS" / APP_NAME)
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump({"CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME,
                       "CFBundleIdentifier": BUNDLE_ID, "CFBundleExecutable": APP_NAME,
                       "CFBundleIconFile": "icon", "CFBundlePackageType": "APPL",
                       "CFBundleShortVersionString": "1.0", "LSMinimumSystemVersion": "26.0",
                       "NSHighResolutionCapable": True,
                       "CFBundleURLTypes": [{"CFBundleURLName": f"{BUNDLE_ID}.links",
                                             "CFBundleURLSchemes": [URL_SCHEME]}],
                       # the swift app starts the python server from here
                       "BoswellProjectDir": str(BASE_DIR)}, f)
    identity = signing_identity()
    if identity == "-":
        print("no apple development certificate, so macos will ask for folder access again after each rebuild")
    subprocess.run(["codesign", "--force", "--sign", identity, str(app_dir)], check=True, capture_output=True)
    # tell finder and the dock to pick up the new icon, and launch services the link type
    subprocess.run(["touch", str(app_dir)], check=False)
    subprocess.run(["/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister",
                    "-f", str(app_dir)], check=False)
    print(f"built {app_dir}")
    print("open it, then right-click its dock icon, Options, Keep in Dock")


if __name__ == "__main__":
    main()
