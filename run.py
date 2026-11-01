"""Source-tree launcher; installation is not required when using this file."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from tatclean.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
