"""open the transcription app in its own window.

usage:
    uv run app.py              # app window
    uv run app.py --browser    # serve only, open http://127.0.0.1:8765 in a browser
"""

import argparse
import threading
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

# config
BASE_DIR = Path(__file__).parent
# values already set in the shell take precedence over .env
load_dotenv(BASE_DIR / ".env")
HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"

import uvicorn  # noqa: E402

from local_transcription.server import create_app  # noqa: E402


# helpers
def set_dock_icon():
    """use the app's icon and name in the dock and menu bar instead of python's."""
    icon = BASE_DIR / "assets" / "icon.png"
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle
    except ImportError:
        return
    info = NSBundle.mainBundle().infoDictionary()
    if info is not None:
        info["CFBundleName"] = "Local Transcription"
    if icon.exists():
        NSApplication.sharedApplication().setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(str(icon)))


TITLE_BAR_HEIGHT = 28


def drag_strip_class():
    """a see-through view across the top of the window that drags it like a title bar, and
    zooms it on a double click."""
    from AppKit import NSView

    class DragStrip(NSView):
        def mouseDown_(self, event):
            if event.clickCount() == 2:
                self.window().performZoom_(None)
            else:
                self.window().performWindowDragWithEvent_(event)

    return DragStrip


def hide_title_bar(window):
    """let the page run under a see-through title bar, keeping the window buttons, so the
    window reads as one surface. the page leaves room for the buttons."""
    from AppKit import (NSColor, NSFullSizeContentViewWindowMask, NSMakeRect, NSViewMinYMargin,
                        NSViewWidthSizable, NSWindowAbove, NSWindowTitleHidden)
    from PyObjCTools import AppHelper

    def apply():
        native = window.native
        if native.styleMask() & NSFullSizeContentViewWindowMask:
            return
        native.setStyleMask_(native.styleMask() | NSFullSizeContentViewWindowMask)
        native.setTitlebarAppearsTransparent_(True)
        native.setTitleVisibility_(NSWindowTitleHidden)
        # pywebview paints the title bar the window's color, clear it so the page shows through
        frame = native.contentView().superview()
        frame.subviews().lastObject().setBackgroundColor_(NSColor.clearColor())
        # and webkit would push the page down below the title bar
        webview = native.contentView()
        if webview.respondsToSelector_("_setAutomaticallyAdjustsContentInsets:"):
            webview._setAutomaticallyAdjustsContentInsets_(False)
        if webview.respondsToSelector_("setObscuredContentInsets:"):
            webview.setObscuredContentInsets_((0, 0, 0, 0))
        # the page now takes clicks where the title bar was, so a strip over it, under the
        # window buttons, keeps the window draggable from there
        size = frame.bounds().size
        strip = drag_strip_class().alloc().initWithFrame_(
            NSMakeRect(0, size.height - TITLE_BAR_HEIGHT, size.width, TITLE_BAR_HEIGHT))
        strip.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
        frame.addSubview_positioned_relativeTo_(strip, NSWindowAbove, webview)

    # appkit only takes window changes on the main thread
    AppHelper.callAfter(apply)


def wait_for_server(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(URL + "/api/state", timeout=1)
            return
        except OSError:
            time.sleep(0.2)
    raise SystemExit("server did not start")


# main
def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--browser", action="store_true", help="serve without opening a window")
    args = parser.parse_args()

    server = uvicorn.Server(uvicorn.Config(create_app(), host=HOST, port=PORT, log_level="warning"))
    if args.browser:
        print(f"serving on {URL}")
        server.run()
        return

    import webview

    set_dock_icon()
    threading.Thread(target=server.run, daemon=True).start()
    wait_for_server()
    window = webview.create_window("Local Transcription", URL, width=1200, height=820, min_size=(820, 560))
    # the page becomes the window's content once it loads
    window.events.loaded += lambda: hide_title_bar(window)
    webview.start()
    server.should_exit = True


if __name__ == "__main__":
    main()
