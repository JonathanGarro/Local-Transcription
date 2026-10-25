"""run the transcription server the mac app talks to.

usage:
    uv run app.py    # serves on http://127.0.0.1:8765

the mac app in mac/ starts this itself, so it is only run by hand to work on the server.
"""

import os
import signal
import sys
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
class Stamped:
    """a stream that starts each line with the time, for the log the mac app writes it to."""

    def __init__(self, stream):
        self.stream, self.fresh = stream, True

    def write(self, text):
        out = []
        for part in text.splitlines(keepends=True):
            if self.fresh:
                out.append(time.strftime("%Y-%m-%d %H:%M:%S "))
            out.append(part)
            self.fresh = part.endswith("\n")
        self.stream.write("".join(out))
        return len(text)

    def __getattr__(self, name):
        return getattr(self.stream, name)


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
    if not sys.stdout.isatty():
        sys.stdout, sys.stderr = Stamped(sys.stdout), Stamped(sys.stderr)
    # the library moves out of the project folder the first time this version runs
    paths.migrate()
    print(f"serving on http://{HOST}:{PORT}, python {sys.version.split()[0]}, library in {paths.HOME}")
    if pid := os.environ.get("BOSWELL_APP_PID"):
        exit_with_app(int(pid))
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
