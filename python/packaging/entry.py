"""Entry script of the PyInstaller build."""

import multiprocessing
import sys

if __name__ == "__main__":
    # Libraries in here start helper processes through multiprocessing, which in a frozen
    # app means running this executable again: let multiprocessing take those runs.
    multiprocessing.freeze_support()
    from scribble.app import main

    sys.exit(main())
