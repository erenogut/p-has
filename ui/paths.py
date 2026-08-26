import os
import sys


def runtime_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundled_dir():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", runtime_dir())
    return runtime_dir()


def asset_path(name):
    candidates = (
        os.path.join(runtime_dir(), "assets", name),
        os.path.join(runtime_dir(), name),
        os.path.join(bundled_dir(), "assets", name),
        os.path.join(bundled_dir(), name),
    )
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return os.path.join(runtime_dir(), "assets", name)
