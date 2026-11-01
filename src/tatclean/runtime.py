"""Recognize supported executable packaging without treating source runs as frozen."""

import sys


def is_packaged() -> bool:
    # PyInstaller sets sys.frozen; Nuitka supplies __compiled__ to compiled modules.
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()
