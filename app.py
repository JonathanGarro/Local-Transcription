"""run the transcription server the mac app talks to.

usage:
    uv run app.py    # serves on http://127.0.0.1:8765

the mac app in mac/ starts this itself, so it is only run by hand to work on the server.
"""

from pathlib import Path

from dotenv import load_dotenv

# config
BASE_DIR = Path(__file__).parent
# values already set in the shell take precedence over .env
load_dotenv(BASE_DIR / ".env")
HOST = "127.0.0.1"
PORT = 8765

import uvicorn  # noqa: E402

from local_transcription.server import create_app  # noqa: E402


# main
def main():
    print(f"serving on http://{HOST}:{PORT}")
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
