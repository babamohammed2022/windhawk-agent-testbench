"""Command line.

    python3 -m awt.cli run <mod.wh.cpp> [--seconds N] [--mode self|inject] [--target t.exe]
                                        [--symbols-json symbol-matrix.json]
                                        [--backend wine|windows-runner|self-hosted-vm]
    python3 -m awt.cli symbols <mod.wh.cpp> [--matrix file.yaml]   (== python3 -m awt.symbols)
    python3 -m awt.cli backends [--list-json]                     (== python3 -m awt.backends)

Exit codes are the same everywhere: 0 = a verdict exists (including `crashed`,
which is a result about the mod), 1 = the mod did not compile, 3 = nothing could
be executed here and no claim is made.
"""
import argparse
import sys

from . import runner


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "backends":
        # handled before argparse: `awt.cli backends --list-json` starts with an
        # option, which nargs=REMAINDER does not capture reliably.
        from .backends import cli as backends_cli
        return backends_cli.main(argv[1:] or None)
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
    r.add_argument("--screenshot", action="store_true",
                   help="capture the desktop during the run")
    r.add_argument("--backend", default=None,
                   help="execute with this backend instead of the default Wine "
                        "path: wine | windows-runner | self-hosted-vm "
                        "(python3 -m awt.backends lists what is available here)")
    s = sub.add_parser("symbols", help="check a mod's symbols across Windows builds")
    s.add_argument("rest", nargs=argparse.REMAINDER)
    sub.add_parser("backends", help="list execution backends, or run one "
                                    "(== python3 -m awt.backends)")
    args = ap.parse_args(argv)
    if args.cmd == "symbols":
        from .symbols import cli as symbols_cli
        return symbols_cli.main(args.rest or None)
    if args.cmd == "run":
        if args.backend:
            from . import backends
            try:
                backend = backends.get(args.backend)
            except KeyError as exc:
                print("error: %s" % (exc.args[0] if exc.args else exc),
                      file=sys.stderr)
                return 2
            rep = backend.run(args.mod, seconds=args.seconds, mode=args.mode,
                              target=args.target, screenshot=args.screenshot,
                              symbols=args.symbols_json)
        else:
            # the default path is unchanged: the Wine runner, exactly as before
            rep = runner.run(args.mod, seconds=args.seconds, mode=args.mode,
                             target=args.target, screenshot=args.screenshot,
                             symbols=args.symbols_json)
        from .backends.base import exit_code_for
        return exit_code_for(rep)
    return 2


if __name__ == "__main__":
    sys.exit(main())
