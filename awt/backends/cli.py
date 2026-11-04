"""`python3 -m awt.backends` - which backend can run here, and run one.

Listing is the point of this command: an agent (or a human) should be able to ask
"what can this machine actually execute?" and get reasons, not guesses.
"""
import json
import sys

from . import describe_all, get, names, select
from .base import exit_code_for, indicative_notes


def list_backends(as_json=False):
    entries = describe_all()
    if as_json:
        print(json.dumps({"schema": 1, "kind": "backend_capabilities",
                          "backends": entries}, indent=2, sort_keys=True))
        return 0
    width = max(len(e["name"]) for e in entries)
    print("execution backends (report shape is identical for all of them)")
    print("=" * 78)
    for entry in entries:
        env = entry["environment"]
        print("%-*s  %s" % (width, entry["name"],
                            "AVAILABLE" if entry["available"] else "unavailable"))
        print("%-*s  %s" % (width, "", entry["label"]))
        print("%-*s  reason: %s" % (width, "", entry["reason"]))
        print("%-*s  executes on: %s; shell profile: %s"
              % (width, "", entry["executes_on"], env.get("shell_profile")))
        for note in indicative_notes(env):
            print("%-*s  ! %s" % (width, "", note))
        for limitation in entry["limitations"]:
            print("%-*s  - %s" % (width, "", limitation))
        print("-" * 78)
    return 0


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m awt.backends",
        description="List execution backends, or run a mod through one of them.")
    parser.add_argument("--backend", choices=names(),
                        help="run with this backend instead of listing them")
    parser.add_argument("--mod", help="mod source (.wh.cpp) to run")
    parser.add_argument("--seconds", type=int, default=6)
    parser.add_argument("--mode", default="auto", help="auto|self|target")
    parser.add_argument("--target", help="target process for --mode target")
    parser.add_argument("--screenshot", action="store_true")
    parser.add_argument("--keep-running", action="store_true",
                        help="self-hosted-vm: leave the VM up after the run")
    parser.add_argument("--no-revert", action="store_true",
                        help="self-hosted-vm: do not restore the clean snapshot")
    parser.add_argument("--json", help="write the report JSON here")
    parser.add_argument("--list-json", action="store_true",
                        help="print the capability table as JSON")
    args = parser.parse_args(argv)

    if not args.backend:
        return list_backends(as_json=args.list_json)
    if not args.mod:
        parser.error("--backend requires --mod")
    backend = get(args.backend)
    rep = backend.run(args.mod, seconds=args.seconds, mode=args.mode,
                      target=args.target, screenshot=args.screenshot,
                      keep_running=args.keep_running, revert=not args.no_revert)
    text = json.dumps(rep, indent=2, sort_keys=True)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print("report written to %s" % args.json, file=sys.stderr)
    print(text)
    return exit_code_for(rep)


if __name__ == "__main__":
    sys.exit(main())
