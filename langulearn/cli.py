"""LanguLearn has been renamed to ThirtyTutors and moved to a new PyPI
package. This stub exists purely so `pip install langulearn` still
succeeds and points people to the real package, instead of erroring out
or (worse) silently installing something abandoned with no signal that
anything changed.
"""

import sys


def main() -> None:
    print(
        "LanguLearn has been renamed to ThirtyTutors.\n\n"
        "This package (langulearn) is no longer maintained and will not "
        "receive further updates.\n\n"
        "To install the current version, run:\n"
        "    pip install thirtytutors\n\n"
        "GitHub: https://github.com/wiss84/thirtytutors",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
