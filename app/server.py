"""Headless phone server (no desktop window):  python -m app.server
Normally the desktop app runs it for you (Settings -> Phone & remote)."""
import faulthandler
import time

from app.core import paths  # noqa: F401  (PATH / model dirs first)


def main():
    faulthandler.enable(file=open(paths.DATA / "crash.log", "a", encoding="utf-8"), all_threads=True)
    from app.remote.server import get_server
    srv = get_server(print)
    ok, msg = srv.start()
    print(msg)
    if not ok:
        raise SystemExit(1)
    print("Open on your phone:", srv.best_url())
    try:
        while srv.running():
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()


if __name__ == "__main__":
    main()
