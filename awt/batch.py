"""Sequential batch runs with conservative CI status and JUnit export.

Each full report is embedded in the batch JSON, since the shared runner's
.sandbox/report.json points only to the latest run. Execution successes mean
only what their verdict levels say, not that a mod is functionally correct.
"""
import argparse
import glob
import json
import os
import sys
import xml.etree.ElementTree as ET

SUCCESS_LEVELS = frozenset(("loads_no_crash", "public_hooks_verified"))


def discover_mods(paths):
    """Directories recurse; globs expand; missing explicit paths become errors."""
    seen, out = set(), []
    for path in paths or []:
        if os.path.isdir(path):
            candidates = sorted(glob.glob(os.path.join(path, "**", "*.wh.cpp"), recursive=True))
        elif glob.has_magic(path):
            candidates = sorted(p for p in glob.glob(path, recursive=True)
                                if os.path.isfile(p) and p.endswith(".wh.cpp"))
            if not candidates:
                candidates = [path]  # do not silently pass an unmatched glob
        else:
            candidates = [path]
        for candidate in candidates:
            identity = os.path.realpath(candidate)
            if identity not in seen:
                seen.add(identity)
                out.append(candidate)
    return out


def run_batch(mod_paths, runner_fn=None, **run_kwargs):
    """One failed run does not prevent collecting the rest; never parallelise
    the shared Wine prefix, logs or build directories."""
    if runner_fn is None:
        from .runner import run
        runner_fn = run
    results = []
    for mod_path in mod_paths:
        try:
            rep = runner_fn(mod_path, **run_kwargs)
            level = rep.get("verdict", {}).get("level", "unknown")
            mod_id = rep.get("mod", {}).get("id") or os.path.basename(mod_path)
            run = rep.get("run") or {}
            execution_problem = None
            if run.get("timed_out"):
                execution_problem = "execution timed out (legacy verdict level unchanged)"
            elif run.get("crashed") or (rep.get("crash") or {}).get("crashed"):
                execution_problem = "runtime reported crash evidence"
            elif level in SUCCESS_LEVELS and (run.get("init_returned") is not True
                                             or run.get("exit_code") != 0):
                execution_problem = "successful init and clean process completion not confirmed"
            results.append({"mod": mod_path, "id": mod_id, "level": level,
                            "execution_problem": execution_problem, "report": rep})
        except Exception as exc:
            results.append({"mod": mod_path, "id": None, "level": "error",
                            "error": "%s: %s" % (type(exc).__name__, exc), "report": None})
    counts = {}
    for result in results:
        counts[result["level"]] = counts.get(result["level"], 0) + 1
    return {"results": results, "summary": {"mods": len(results), "levels": counts},
            "note": "Execution outcomes only; successful execution does not prove behavioural correctness. Runs are sequential, not isolated storage/prefix snapshots."}


def batch_exit_code(batch):
    levels = batch["summary"]["levels"]
    if not batch["results"]:
        return 2
    if any(r.get("execution_problem") for r in batch["results"]):
        return 1
    if any(level not in SUCCESS_LEVELS | {"not_executed"} for level in levels):
        return 1
    if "not_executed" in levels:
        return 3  # mixed completed/skipped must not look fully tested
    return 0


def junit_xml(batch):
    """One testcase per mod; unknown outcomes fail closed, not_executed skips."""
    results = batch["results"]
    skipped = sum(r["level"] == "not_executed" and not r.get("execution_problem") for r in results)
    failures = sum(bool(r.get("execution_problem")) or r["level"] not in SUCCESS_LEVELS | {"not_executed"} for r in results)
    suite = ET.Element("testsuite", {"name": "windhawk-mod-execution",
                       "tests": str(len(results)), "failures": str(failures),
                       "errors": "0", "skipped": str(skipped)})
    for r in results:
        case = ET.SubElement(suite, "testcase", {"classname": "awt.execution",
                              "name": r["mod"]})
        verdict = (r.get("report") or {}).get("verdict") or {}
        if r["level"] == "not_executed" and not r.get("execution_problem"):
            ET.SubElement(case, "skipped", {"message": verdict.get("not_executed_reason")
                          or "no execution result available"})
        elif r["level"] not in SUCCESS_LEVELS or r.get("execution_problem"):
            fail = ET.SubElement(case, "failure", {"message": "level=%s" % r["level"]})
            fail.text = r.get("execution_problem") or r.get("error") or verdict.get("summary") or "see batch JSON report"
        ET.SubElement(case, "system-out").text = "level=%s; execution evidence only, not functional correctness" % r["level"]
    return ET.tostring(suite, encoding="unicode")


def summary_table(batch):
    lines = ["%-28s %s" % ("mod", "level")]
    for r in batch["results"]:
        lines.append("%-28s %s" % (r["id"] or r["mod"], r["level"]))
    lines.append("---- %d mod(s): %s" % (batch["summary"]["mods"], ", ".join(
        "%s=%d" % kv for kv in sorted(batch["summary"]["levels"].items()))))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="awt.batch")
    ap.add_argument("mods", nargs="+")
    ap.add_argument("--seconds", type=int, default=6)
    ap.add_argument("--backend", default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--junit", default=None)
    args = ap.parse_args(argv)
    mods = discover_mods(args.mods)
    if not mods:
        print("error: no .wh.cpp mods discovered", file=sys.stderr)
        return 2
    runner_fn = None
    if args.backend:
        from . import backends
        try:
            runner_fn = backends.get(args.backend).run
        except KeyError as exc:
            print("error: %s" % exc.args[0], file=sys.stderr)
            return 2
    batch = run_batch(mods, runner_fn, seconds=args.seconds)
    print(summary_table(batch))
    try:
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(batch, f, indent=2)
        if args.junit:
            with open(args.junit, "w", encoding="utf-8") as f:
                f.write(junit_xml(batch))
    except (OSError, ValueError) as exc:
        print("error: cannot write batch output: %s" % exc, file=sys.stderr)
        return 2
    return batch_exit_code(batch)


if __name__ == "__main__":
    sys.exit(main())
