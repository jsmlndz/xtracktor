# Entry point of xtracktor.pyz:  python3 xtracktor.pyz <command> ...
import sys

from xtracktor.cli import main

sys.exit(main())
