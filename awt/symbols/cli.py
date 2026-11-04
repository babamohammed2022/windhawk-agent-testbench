"""Command line for Feature 1: `python3 -m awt.symbols <mod.wh.cpp>`.

    # what does this mod ask Windows for, and does it exist on each build?
    python3 -m awt.symbols examples/internal-symbol-demo.wh.cpp

    # only list the extracted requests (no download, no network at all)
    python3 -m awt.symbols mod.wh.cpp --list

    # a subset of builds, and the machine-readable section on disk
    python3 -m awt.symbols mod.wh.cpp --builds win11-23h2,win11-24h2 \
        --json .sandbox/symbol-matrix.json

Exit codes:
    0  at least one (symbol, build) cell was actually resolved
    2  usage error / unreadable mod
    3  nothing could be checked - every cell is 'unknown' (no network, no
       signature, unreadable PDB). This is the honest "not executed" of this
       feature: it makes no claim about the mod.
"""
import argparse
import json
import os
import sys
import time

from .. import env as host_env
from . import extract, match, matrix as matrix_mod, table, verify, winbindex


def build_parser():
    ap = argparse.ArgumentParser(
        prog="python3 -m awt.symbols",
        description="Check a Windhawk mod's internal symbols against a matrix of "
                    "Windows builds, using public PDBs (no Windows required).")
    ap.add_argument("mod", nargs="?", help="path to the .wh.cpp mod source")
    ap.add_argument("--matrix", default=None,
                    help="build matrix YAML (default configs/symbol-matrix.yaml)")
    ap.add_argument("--builds", default=None,
                    help="comma separated build ids to check (default: all enabled)")
    ap.add_argument("--modules", default=None,
                    help="comma separated module names to restrict the check to")
    ap.add_argument("--arch", default=None, choices=["x86-64", "x86"],
                    help="override the matrix architecture")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="write the symbol_matrix section (standalone document) here")
    ap.add_argument("--list", action="store_true",
                    help="only print the extracted symbol requests; no network")
    ap.add_argument("--offline", action="store_true",
                    help="never touch the network (cells become 'unknown')")
    ap.add_argument("--max-requests", type=int, default=0,
                    help="cap how many requests are checked (0 = all)")
    ap.add_argument("--rows", type=int, default=40,
                    help="how many symbol rows the terminal table shows (0 = all)")
    ap.add_argument("--cross-check", action="store_true",
                    help="also run llvm-pdbutil over the PDB when it is installed")
    ap.add_argument("--llvm-undname", action="store_true",
                    help="use llvm-undname for undecoration when it is installed")
    ap.add_argument("--refresh-pinned", default=None, metavar="MODULES",
                    help="comma separated modules: re-read their signatures from "
                         "winbindex and print a YAML block to paste into the matrix")
    ap.add_argument("--quiet", action="store_true", help="only print the table")
    ap.add_argument("--verbose", "-v", action="store_true")
    return ap


def _split(value):
    return [v.strip() for v in str(value).split(",") if v.strip()] if value else None


def print_requests(extraction, stream=sys.stdout):
    requests = extraction.get("requests") or []
    print("mod id       : %s" % (extraction.get("mod_id") or "?"), file=stream)
    print("requests     : %d" % len(requests), file=stream)
    print("modules      : %s"
          % (", ".join(sorted({r.get("module") for r in requests if r.get("module")}))
             or "-"), file=stream)
    if extraction.get("preprocessor_notes"):
        print("preprocessor : %d note(s)" % len(extraction["preprocessor_notes"]),
              file=stream)
        for note in extraction["preprocessor_notes"][:5]:
            print("               %s" % note, file=stream)
    print("", file=stream)
    print("  %-6s %-18s %-22s %s" % ("line", "origin", "module (basis)", "symbol"),
          file=stream)
    for req in requests:
        module = "%s (%s)" % (req.get("module") or "-", req.get("module_basis") or "-")
        print("  %-6s %-18s %-22s %s%s"
              % (req.get("line"), req.get("origin"), _clip(module, 22),
                 _clip(req.get("symbol") or "?", 96),
                 "  [optional]" if req.get("optional") else ""), file=stream)
        alts = req.get("alternatives") or []
        if len(alts) > 1:
            print("  %-6s %-18s %-22s alternatives: %d, first match wins"
                  % ("", "", "", len(alts)), file=stream)


def _clip(text, width):
    text = str(text or "")
    return text if len(text) <= width else text[:width - 1] + "\u2026"


def write_json(path, section):
    document = {
        "schema": 1,
        "kind": "symbol_matrix",
        "generated": section.get("generated"),
        "environment": host_env.probe(),
        "symbol_matrix": section,
    }
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2)
    return document


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.refresh_pinned:
        matrix = matrix_mod.load(args.matrix)
        builds = matrix_mod.select_builds(matrix, _split(args.builds),
                                          include_disabled=True)
        resolver = winbindex.Resolver(
            cache_dir=(matrix.get("winbindex") or {}).get("cache_dir"),
            offline=args.offline, verbose=args.verbose)
        data = resolver.refresh_pinned(_split(args.refresh_pinned),
                                       [b["id"] for b in builds])
        sys.stdout.write(winbindex.pinned_yaml(data))
        for note in resolver.notes:
            print("# %s" % note)
        return 0 if data else 3

    if not args.mod:
        build_parser().error("a mod source file is required")
    if not os.path.isfile(args.mod):
        print("error: no such mod file: %s" % args.mod, file=sys.stderr)
        return 2

    with open(args.mod, encoding="utf-8", errors="replace") as f:
        source = f.read()
    extraction = extract.extract_requests(source, path=args.mod)

    if args.list:
        print_requests(extraction)
        return 0 if extraction.get("requests") else 3

    section = verify.verify_mod(
        args.mod, matrix_path=args.matrix, builds=_split(args.builds),
        modules=_split(args.modules), arch=args.arch,
        offline=True if args.offline else None, verbose=args.verbose,
        cross_check=args.cross_check, use_llvm_undname=args.llvm_undname,
        requests=extraction, max_requests=args.max_requests)

    if not args.quiet:
        print(table.render(section, max_rows=args.rows))
    if args.json_out:
        write_json(args.json_out, section)
        print("json         : %s" % args.json_out)

    counts = (section.get("summary") or {}).get("row_outcomes") or {}
    resolved = sum(counts.get(k, 0) for k in ("found", "missing", "ambiguous", "mixed"))
    if not section.get("symbols") or resolved == 0:
        if not args.quiet:
            print("not executed : no (symbol, build) cell could be resolved; "
                  "no claim is made about this mod")
        return 3
    return 0


if __name__ == "__main__":                    # pragma: no cover
    sys.exit(main())
