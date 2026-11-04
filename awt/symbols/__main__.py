"""Entry point: `python3 -m awt.symbols <mod.wh.cpp> [--matrix file.yaml]`."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
