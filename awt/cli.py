"""Command line.

    python3 -m awt.cli run <mod.wh.cpp> [--seconds N] [--mode self|inject] [--target t.exe]
                                        [--symbols-json symbol-matrix.json]
                                        [--backend wine|windows-runner|self-hosted-vm]
                                        [--settings-json file.json] [--settings k=v ...]
    python3 -m awt.cli check <mod.wh.cpp> [--json out.json]   (static checks, no execution)
    python3 -m awt.cli batch <dir|mod...> [--seconds N] [--backend ...] [--json ...] [--junit ...]
    python3 -m awt.cli symbols <mod.wh.cpp> [--matrix file.yaml]   (== python3 -m awt.symbols)
    python3 -m awt.cli backends [--list-json]                     (== python3 -m awt.backends)

Exit codes are the same everywhere: 0 = a verdict exists (including `crashed`,
which is a result about the mod), 1 = the mod did not compile, 3 = nothing could
be executed here and no claim is made.
"""
import argparse
import json
import re
import sys

from . import builder, runner


def _load_settings_override(settings_json, settings_kv):
    """Merge --settings-json and --settings key=value into one override dict."""
    override = {}
    if settings_json:
        with open(settings_json, encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("--settings-json must contain a JSON object")
            override.update(data)
    for item in settings_kv or []:
        if "=" not in item:
            raise ValueError("--settings expects key=value, got %r" % item)
        key, value = item.split("=", 1)
        if not key:
            raise ValueError("--settings requires a non-empty key")
        # numbers and booleans keep their type, like the YAML defaults
        if value in ("true", "false"):
            override[key] = 1 if value == "true" else 0
        elif re.fullmatch(r"-?[0-9]+", value):
            override[key] = int(value)
        else:
            override[key] = value
    return builder.validate_settings_override(override) if override else None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "preview":
        from . import preview
        return preview.main(argv[1:])
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
    r.add_argument("--settings-json", default=None,
                   help="JSON object of setting overrides (merged over the "
                        "mod's declared defaults)")
    r.add_argument("--settings", action="append", default=[],
                   help="setting override as key=value (repeatable)")
    sub.add_parser("preview", help="capture a Win32 specimen without/with mod; HTML + PNG + JSON")
    c = sub.add_parser("check", help="static checks only, no execution")
    c.add_argument("mod")
    c.add_argument("--json", default=None)
    b = sub.add_parser("batch", help="run many mods, one summary")
    b.add_argument("mods", nargs="+")
    b.add_argument("--seconds", type=int, default=6)
    b.add_argument("--backend", default=None)
    b.add_argument("--json", default=None)
    b.add_argument("--junit", default=None)
    s = sub.add_parser("symbols", help="check a mod's symbols across Windows builds")
    s.add_argument("rest", nargs=argparse.REMAINDER)
    sub.add_parser("backends", help="list execution backends, or run one "
                                    "(== python3 -m awt.backends)")
    args = ap.parse_args(argv)
    if args.cmd == "symbols":
        from .symbols import cli as symbols_cli
        return symbols_cli.main(args.rest or None)
    if args.cmd == "check":
        from . import staticchecks
        return staticchecks.main([args.mod] +
                                 (["--json", args.json] if args.json else []))
    if args.cmd == "batch":
        from . import batch as batch_mod
        return batch_mod.main(argv[1:])
    if args.cmd == "run":
        try:
            override = _load_settings_override(args.settings_json, args.settings)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
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
                              symbols=args.symbols_json,
                              settings_override=override)
        else:
            # the default path is unchanged: the Wine runner, exactly as before
            rep = runner.run(args.mod, seconds=args.seconds, mode=args.mode,
                             target=args.target, screenshot=args.screenshot,
                             symbols=args.symbols_json,
                             settings_override=override)
        from .backends.base import exit_code_for
        return exit_code_for(rep)
    return 2


if __name__ == "__main__":
    sys.exit(main())
