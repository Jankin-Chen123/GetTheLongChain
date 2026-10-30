"""Check this project's exact pins using only the Python standard library.

Exit codes: 0 = satisfied, 1 = repair needed, 2 = invalid requirements.
Only unconditional name==version pins are supported, deliberately failing
closed if the requirements format changes.
"""

import importlib.metadata
import importlib.util
from pathlib import Path
import re
import sys


def check(requirements: Path) -> int:
    pins = []
    try:
        for number, raw in enumerate(requirements.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]*)\s*==\s*([A-Za-z0-9][A-Za-z0-9.!+_-]*)", line)
            if not match:
                raise ValueError(f"line {number}: expected name==version")
            pins.append(match.groups())
        if not pins:
            raise ValueError("no pinned dependencies found")
    except (OSError, UnicodeError, ValueError) as error:
        print(f"Cannot check requirements: {error}", file=sys.stderr)
        return 2

    satisfied = True
    for name, expected in pins:
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed = None
        # The project's distributions use matching module names (hyphen -> underscore).
        available = importlib.util.find_spec(name.replace("-", "_")) is not None
        if installed != expected or not available:
            print(f"Dependency repair needed: {name}=={expected} (installed: {installed or 'missing'}, module present: {available})")
            satisfied = False
    return 0 if satisfied else 1


if __name__ == "__main__":
    sys.exit(check(Path(sys.argv[1])))
