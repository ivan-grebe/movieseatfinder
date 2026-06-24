import os

from backend.server import run_server


if __name__ == "__main__":
    run_server(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "4173")),
    )
