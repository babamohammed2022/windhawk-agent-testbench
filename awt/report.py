"""Stable JSON report schema (section 4) + human-readable summary."""
import json
import os
import time


def new_report(build_result, analysis) -> dict:
    meta = build_result["meta"]
    return {
        "schema": 1,
        "mod": {
            "id": build_result["mod_id"],
            "version": meta["raw"].get("version", "0.0.0"),
            "include": meta["include"],
            "architecture": meta["raw"].get("architecture", "x86-64"),
        },
        "build": {
            "ok": build_result["ok"],
            "errors": build_result["errors"],
            "warnings": build_result["warnings"],
        },
        "analysis": {
            "public_hooks": analysis["public_hooks"],
            "internal_hooks": analysis["internal_hooks"],
            "unknown_hooks": analysis["unknown_hooks"],
        },
        "include_analysis": [],
        "run": {},
        "shim": {"not_implemented_called": [], "simulated_with_limits": []},
        "verdict": {
            "level": "build_failed",
            "verified": [],
            "not_verifiable_here": [],
            "requires_real_windows": False,
            "summary": "",
        },
        "artifacts": {"screenshots": [], "wine_log": None, "dumps": []},
    }


def finish(rep, history_dir) -> dict:
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       ".sandbox", "report.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with open(os.path.join(history_dir, "%s-%s.json" % (rep["mod"]["id"], stamp)), "w",
              encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    print(summary(rep))
    return rep


def summary(rep) -> str:
    v = rep["verdict"]
    lines = [
        "=== automatic-windhawk-testing report ===",
        "mod            : %s v%s (include: %s)" % (
            rep["mod"]["id"], rep["mod"]["version"], ", ".join(rep["mod"]["include"])),
        "build          : %s (%d errors, %d warnings)" % (
            "ok" if rep["build"]["ok"] else "FAILED",
            len(rep["build"]["errors"]), len(rep["build"]["warnings"])),
        "public hooks   : %s" % (", ".join(rep["analysis"]["public_hooks"]) or "-"),
        "internal hooks : %s" % (", ".join(rep["analysis"]["internal_hooks"][:4]) or "-"),
        "run            : init=%s applied=%s fired=%s crashed=%s timeout=%s" % (
            rep["run"].get("init_returned"), rep["run"].get("hooks_applied"),
            rep["run"].get("hooks_fired") or "{}", rep["run"].get("crashed"),
            rep["run"].get("timed_out")),
        "verdict        : %s" % v["level"],
        "verified       : %s" % (", ".join(v["verified"]) or "-"),
        "NOT verifiable : %s" % (", ".join(v["not_verifiable_here"]) or "-"),
        "real Windows   : %s" % ("REQUIRED for the items above" if v["requires_real_windows"] else "not required"),
        "summary        : %s" % v["summary"],
    ]
    for e in rep["build"]["errors"][:8]:
        lines.append("  error %s:%d:%d %s" % (e["file"], e["line"], e["col"], e["message"]))
    return "\n".join(lines)
