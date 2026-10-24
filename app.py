"""run the transcription server the mac app talks to.

usage:
    uv run app.py    # serves on http://127.0.0.1:8765

the mac app in mac/ starts this itself, so it is only run by hand to work on the server.
"""

import os
import signal
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

# config
BASE_DIR = Path(__file__).parent
# values already set in the shell take precedence over .env
load_dotenv(BASE_DIR / ".env")
HOST = "127.0.0.1"
PORT = 8765

import uvicorn  # noqa: E402

from local_transcription import paths  # noqa: E402
from local_transcription.server import create_app  # noqa: E402


# helpers
def exit_with_app(pid):
    """stop when the mac app that started this goes away, even if it was force quit
    or crashed, so a server is not left running for the next launch to find."""
    def watch():
        while os.getppid() == pid:
            time.sleep(2)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=watch, daemon=True).start()


# main
def main():
    # the library moves out of the project folder the first time this version runs
    paths.migrate()
    print(f"serving on http://{HOST}:{PORT}")
    if pid := os.environ.get("BOSWELL_APP_PID"):
        exit_with_app(int(pid))
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
