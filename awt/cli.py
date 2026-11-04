"""Command line.

    python3 -m awt.cli run <mod.wh.cpp> [--seconds N] [--mode self|inject] [--target t.exe]
                                        [--symbols-json symbol-matrix.json]
    python3 -m awt.cli symbols <mod.wh.cpp> [--matrix file.yaml]   (== python3 -m awt.symbols)
"""
import argparse
import sys

from . import runner


def main(argv=None):
    ap = argparse.ArgumentParser(prog="awt")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="build + run + report a mod")
    r.add_argument("mod")
    r.add_argument("--seconds", type=int, default=6)
    r.add_argument("--mode", default="auto", choices=["auto", "self", "inject"])
    r.add_argument("--target", default=None)
    r.add_argument("--symbols-json", default=None,
                   help="attach a symbol_matrix section produced by "
                        "`python3 -m awt.symbols --json ...` to this report")
    s = sub.add_parser("symbols", help="check a mod's symbols across Windows builds")
    s.add_argument("rest", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    if args.cmd == "symbols":
        from .symbols import cli as symbols_cli
        return symbols_cli.main(args.rest or None)
    if args.cmd == "run":
        rep = runner.run(args.mod, seconds=args.seconds, mode=args.mode,
                         target=args.target, symbols=args.symbols_json)
        level = rep["verdict"]["level"]
        if level == "build_failed":
            return 1          # the mod itself does not compile
        if level == "not_executed":
            return 3          # this environment could not run anything: no claim made
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
