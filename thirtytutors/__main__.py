"""Enables `python -m thirtytutors` (used internally by the Windows desktop
shortcut via pythonw.exe -m thirtytutors, so it launches with no console
window flash - see cli.py's _create_shortcut_windows). Equivalent to
running the `thirtytutors` console-script entry point directly.
"""

from .cli import main

if __name__ == "__main__":
    main()
