import os

APP_DISPLAY_NAME = "P-HAS"
APP_FULL_NAME = "Production Human Analytics Server"
APP_PUBLISHER = "P-HAS Team"
INSTALL_FOLDER_NAME = "P-HAS"
APP_EXE_NAME = "P-HAS.exe"


def _load_version():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_version.txt")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
            if value:
                return value
    except OSError:
        pass
    return "1.0.0"


APP_VERSION = _load_version()
