import sys
import uvicorn

# Backwards-compatible entrypoint.
#
# - For development: `uvicorn main:app --reload` still works.
# - For packaged Windows EXE: the server spawns the same executable with
#   `--comms`, which is handled here.

if "--comms" in sys.argv:
    from scoreboard import comms as _comms

    _comms.main()
    raise SystemExit(0)

from scoreboard.server import app  # noqa: F401


if __name__ == "__main__":
    # host=None binds to all interfaces, both IPv4 and IPv6. "0.0.0.0" binds
    # only to IPv4 and "::" only to IPv6.
    uvicorn.run("scoreboard.server:app", host=None, port=8000)
