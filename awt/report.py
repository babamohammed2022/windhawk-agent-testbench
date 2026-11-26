"""Stable JSON report schema (section 4) + human-readable summary."""
import json
import os
import time

from . import fidelity, shellsurfaces


# Verdict levels, weakest to strongest. `not_executed` means no backend was able
# to compile or run anything, so no claim about the mod is made at all.
LEVELS = ("not_executed", "build_failed", "load_failed", "crashed",
          "loads_no_crash", "public_hooks_verified")


def new_report(build_result, analysis) -> dict:
    meta = build_result["meta"]
    source_path = build_result.get("mod_path")
    source = ""
    if source_path and os.path.isfile(source_path):
        try:
            with open(source_path, encoding="utf-8", errors="replace") as f:
                source = f.read()
        except OSError:
            source = ""
    rep = {
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
            "effective_settings": build_result.get("settings", {}),
            "settings_override_unknown": build_result.get("settings_override_unknown", []),
            "compiler_options": build_result.get("compiler_options") or {},
            "toolchain": build_result.get("toolchain") or {},
        },
        "analysis": {
            "public_hooks": analysis["public_hooks"],
            "internal_hooks": analysis["internal_hooks"],
            "unknown_hooks": analysis["unknown_hooks"],
        },
        "include_analysis": [],
        "run": {"backend": "unavailable"},
        "environment": {},
        "shim": {"not_implemented_called": [], "simulated_with_limits": []},
        # Descriptive diagnostics (never change the verdict level).
        "static_checks": {},
        "hook_coverage": {},
        "crash": {},
        "verdict": {
            "level": "build_failed",
            "verified": [],
            "not_verifiable_here": [],
            "requires_real_windows": False,
            "summary": "",
        },
        "artifacts": {"screenshots": [], "wine_log": None, "dumps": []},
    }
    fidelity.attach(rep, build_result, meta, source, analysis)
    if source:
        rep["shell_surfaces"] = shellsurfaces.analyze(source, meta, analysis)
    return rep


def attach_symbol_matrix(rep, section) -> dict:
    """Add the Feature-1 `symbol_matrix` section to a report.

    Purely additive: no existing key changes meaning, and a report without a
    matrix run simply has no `symbol_matrix` key. `section` may be the dict
    produced by `awt.symbols.verify.verify_mod`, or the path of a JSON document
    written by `python3 -m awt.symbols --json`.
    """
    if not section:
        return rep
    if isinstance(section, str):
        import json as _json
        with open(section, encoding="utf-8") as f:
            doc = _json.load(f)
        section = doc.get("symbol_matrix") or doc
    rep["symbol_matrix"] = section
    counts = ((section.get("summary") or {}).get("row_outcomes") or {})
    note = ("symbol matrix: %d symbol(s) x %d build(s); rows found=%d missing=%d "
            "ambiguous=%d unknown=%d"
            % ((section.get("summary") or {}).get("symbols") or 0,
               (section.get("summary") or {}).get("builds") or 0,
               counts.get("found", 0), counts.get("missing", 0),
               counts.get("ambiguous", 0), counts.get("unknown", 0)))
    rep.setdefault("verdict", {})["symbol_matrix_note"] = note
    return rep


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
            "ok" if rep["build"]["ok"] else
            ("not attempted (no toolchain)" if v["level"] == "not_executed" else "FAILED"),
            len(rep["build"]["errors"]), len(rep["build"]["warnings"])),
        "public hooks   : %s" % (", ".join(rep["analysis"]["public_hooks"]) or "-"),
        "internal hooks : %s" % (", ".join(rep["analysis"]["internal_hooks"][:4]) or "-"),
        "backend        : %s%s" % (rep["run"].get("backend", "?"),
                                    (" (%s)" % rep["run"]["reason"][:90])
                                    if rep["run"].get("reason") else ""),
        "run            : init=%s applied=%s fired=%s crashed=%s timeout=%s" % (
            rep["run"].get("init_returned"), rep["run"].get("hooks_applied"),
            rep["run"].get("hooks_fired") or "{}", rep["run"].get("crashed"),
            rep["run"].get("timed_out")),
        "verdict        : %s" % v["level"],
        "verified       : %s" % (", ".join(v["verified"]) or "-"),
        "NOT verifiable : %s" % (", ".join(v["not_verifiable_here"]) or "-"),
        "real Windows   : %s" % (
            "unknown (nothing was executed here)" if v["level"] == "not_executed" else
            ("REQUIRED for the items above" if v["requires_real_windows"] else "not required")),
        "summary        : %s" % v["summary"],
    ]
    matrix = rep.get("symbol_matrix")
    if matrix:
        counts = ((matrix.get("summary") or {}).get("row_outcomes") or {})
        lines.append("symbol matrix  : %d symbol(s) x %d build(s) -> found %d, "
                     "missing %d, ambiguous %d, unknown %d"
                     % ((matrix.get("summary") or {}).get("symbols") or 0,
                        (matrix.get("summary") or {}).get("builds") or 0,
                        counts.get("found", 0), counts.get("missing", 0),
                        counts.get("ambiguous", 0), counts.get("unknown", 0)))
        lines.append("                 (names only: nothing was executed; see "
                     "symbol_matrix.disclaimer)")
    sc = rep.get("static_checks") or {}
    if sc:
        s = sc.get("summary") or {}
        lines.append("static checks  : %d check(s) -> %d error(s), %d warning(s)"
                     % (s.get("checks", 0), s.get("errors", 0), s.get("warnings", 0)))
    shells = rep.get("shell_surfaces") or {}
    if shells:
        by = (shells.get("summary") or {}).get("by_coverage") or {}
        lines.append("shell surfaces : %d detected (simulated %d, not_verifiable %d)"
                     % ((shells.get("summary") or {}).get("detected", 0),
                        by.get("simulated", 0), by.get("not_verifiable", 0)))
    caps = rep.get("capabilities") or {}
    if caps:
        s = caps.get("summary") or {}
        lines.append("capabilities   : verified %d, simulated %d, not_verifiable %d, "
                     "not_executed %d"
                     % (s.get("verified", 0), s.get("simulated", 0),
                        s.get("not_verifiable", 0), s.get("not_executed", 0)))
    warnings = rep.get("fidelity_warnings") or []
    if warnings:
        lines.append("fidelity warns : %d (source references, not runtime claims)" %
                     len(warnings))
    hc = rep.get("hook_coverage") or {}
    if hc:
        s = hc.get("summary") or {}
        lines.append("hook coverage  : %d hook(s) -> fired %d, driven-no-log %d, "
                     "not-driven %d" % (s.get("hooks", 0), s.get("fired", 0),
                                        s.get("driven_no_log", 0),
                                        s.get("not_driven", 0)))
    cr = rep.get("crash") or {}
    if cr and cr.get("kind") not in (None, "none"):
        lines.append("crash          : kind=%s faulting=%s" % (
            cr.get("kind"), cr.get("faulting_module")))
    for e in rep["build"]["errors"][:8]:
        lines.append("  error %s:%d:%d %s" % (e["file"], e["line"], e["col"], e["message"]))
    return "\n".join(lines)
