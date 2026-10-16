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

    threading.Thread(target=server.run, daemon=True).start()
    wait_for_server()
    webview.create_window("Local Transcription", URL, width=1200, height=820, min_size=(820, 560))
    webview.start()
    server.should_exit = True


if __name__ == "__main__":
    main()
