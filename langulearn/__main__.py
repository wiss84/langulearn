"""Enables `python -m langulearn` (used internally by the Windows desktop
shortcut via pythonw.exe -m langulearn, so it launches with no console
window flash - see cli.py's _create_shortcut_windows). Equivalent to
running the `langulearn` console-script entry point directly.
"""

from .cli import main

if __name__ == "__main__":
    main()
