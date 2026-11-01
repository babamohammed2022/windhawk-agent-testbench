"""Command line: python3 -m awt.cli run <mod.wh.cpp> [--seconds N] [--mode self|inject] [--target t.exe]"""
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
    args = ap.parse_args(argv)
    if args.cmd == "run":
        rep = runner.run(args.mod, seconds=args.seconds, mode=args.mode, target=args.target)
        return 0 if rep["verdict"]["level"] not in ("build_failed",) else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
