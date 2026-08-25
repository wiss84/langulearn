"""LanguLearn has been renamed to ThirtyTutors and moved to a new PyPI
package. This module-level print fires for `import langulearn` too, not
just running the `langulearn` command - see cli.py for the same message
on that path.
"""

import sys

print(
    "LanguLearn has been renamed to ThirtyTutors.\n\n"
    "This package (langulearn) is no longer maintained and will not "
    "receive further updates.\n\n"
    "To install the current version, run:\n"
    "    pip install thirtytutors\n\n"
    "GitHub: https://github.com/wiss84/thirtytutors",
    file=sys.stderr,
)
