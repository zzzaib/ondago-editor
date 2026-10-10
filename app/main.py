"""OnDaGo Editor entry point:  python -m app.main"""
import faulthandler

from app.core import paths  # noqa: F401  (sets up PATH/DLLs/model dirs before heavy imports)

# Native crashes (Qt, CUDA, ONNX...) close the app without a Python traceback.
# faulthandler writes the Python stack of every thread to data/crash.log when that happens.
_crash = open(paths.DATA / "crash.log", "a", encoding="utf-8")
faulthandler.enable(file=_crash, all_threads=True)


def main():
    import sys
    from app.gui.main_window import run
    run(background="--background" in sys.argv)


if __name__ == "__main__":
    main()
