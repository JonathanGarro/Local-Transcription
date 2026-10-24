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
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

# config
BASE_DIR = Path(__file__).parent.resolve()
APP_NAME = "Boswell"
AI_HELPER = "BoswellAI"
ICON_PNG = BASE_DIR / "assets" / "icon.png"
# kept from when the app was called boswell native, since macos remembers its folder access by it
BUNDLE_ID = "local.transcription.native"
# boswell://meeting?id=... links, from answers and from to-dos in things, open the app
URL_SCHEME = "boswell"
MAC_DIR = BASE_DIR / "mac"
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
    """compile the swiftui app in mac/ and return the folder with its binaries: the app, and
    BoswellAI, the helper that runs apple intelligence for the server."""
    env = xcode_env()
    subprocess.run(["swift", "build", "-c", "release"], cwd=MAC_DIR, env=env, check=True)
    path = subprocess.run(["swift", "build", "-c", "release", "--show-bin-path"], cwd=MAC_DIR, env=env,
                          check=True, capture_output=True, text=True).stdout.strip()
    return Path(path)


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

    binaries = build_native()
    if app_dir.exists():
        shutil.rmtree(app_dir)
    contents = app_dir / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    make_icon(ICON_PNG, contents / "Resources")
    for name in (APP_NAME, AI_HELPER):
        shutil.copy2(binaries / name, contents / "MacOS" / name)
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump({"CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME,
                       "CFBundleIdentifier": BUNDLE_ID, "CFBundleExecutable": APP_NAME,
                       "CFBundleIconFile": "AppIcon", "CFBundleIconName": "AppIcon", "CFBundlePackageType": "APPL",
                       "CFBundleShortVersionString": "1.0", "LSMinimumSystemVersion": "26.0",
                       "NSHighResolutionCapable": True,
                       "CFBundleURLTypes": [{"CFBundleURLName": f"{BUNDLE_ID}.links",
                                             "CFBundleURLSchemes": [URL_SCHEME]}],
                       # the swift app starts the python server from here
                       "BoswellProjectDir": str(BASE_DIR)}, f)
    identity = signing_identity()
    if identity == "-":
        print("no apple development certificate, so macos will ask for folder access again after each rebuild")
    # the helper is signed first, since the app's signature covers the code inside it
    subprocess.run(["codesign", "--force", "--sign", identity, str(contents / "MacOS" / AI_HELPER)],
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
