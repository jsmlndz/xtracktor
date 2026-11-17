#!/usr/bin/env python3
"""Build dist/xtracktor.pyz from src/ - standard library only.

    python3 tools/build_pyz.py

src/ holds the xtracktor package plus a root __main__.py that calls
sys.exit(main()). Do not use zipapp's -m option instead: its generated
__main__ discards main()'s return value, so exit codes would be lost.
"""

import os
import sys
import zipapp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
OUT = os.path.join(ROOT, "dist", "xtracktor.pyz")


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    zipapp.create_archive(SRC, OUT, interpreter="/usr/bin/env python3",
                          filter=lambda path: "__pycache__" not in path.parts)
    print("built %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
