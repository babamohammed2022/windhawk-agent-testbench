"""Offline, heuristic source diagnostics; not a C++ or settings-schema validator.

Literal setting names and common lifecycle signatures are recognised. Macros,
conditional compilation, complex/nested YAML and dynamic keys can defeat the
checks. Findings never change a runtime verdict or prove functional correctness.
"""
import argparse
import json
import re
import sys

from . import analyzer, compileroptions, fidelity, metadata

# Preserve strings while removing comments (including the metadata/settings).
# C++ raw strings and macro expansion are not fully parsed: this is a linter.
TOKENS = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.S)


def _code(source):
    return TOKENS.sub(lambda m: " " if m.group().startswith(("//", "/*")) else m.group(), source)


def run_checks(source, mod_path=None):
    checks = []

    def add(cid, severity, message):
        checks.append({"id": cid, "severity": severity, "message": message})

    meta = metadata.parse_metadata(source)
    raw = meta.get("raw") or {}
    settings = metadata.parse_settings(source)
    code = _code(source)
    # Remove literals too when looking for function signatures.
    signatures = TOKENS.sub(" ", code)
    ana = analyzer.analyze(code)
    if not re.search(r"//\s*==WindhawkMod==", source):
        add("metadata.present", "error", "Windhawk metadata block not found")
    elif not raw.get("id"):
        add("metadata.id", "error", "metadata has no parsed @id")
    if raw and not raw.get("version"):
        add("metadata.version", "warning", "metadata has no @version")
    if raw and not re.search(r"//\s*@include\b", source):
        add("metadata.include", "warning", "no @include: parsed default is * (all processes)")
    arches = raw.get("architecture", "x86-64").split()
    if "x86-64" not in arches:
        add("architecture.supported", "warning", "this bench builds x86-64 only; declared architecture is %r" % " ".join(arches))
    elif len(arches) > 1:
        add("architecture.partial", "warning", "only the x86-64 variant is tested by this bench")

    if not re.search(r"\bBOOL\s+(?:WINAPI\s+)?Wh_ModInit\s*\(", signatures):
        add("lifecycle.modinit", "warning", "BOOL Wh_ModInit signature not detected; the bench builder requires it (macros may hide it)")
    for name in ("Wh_ModAfterInit", "Wh_ModBeforeUninit", "Wh_ModUninit", "Wh_ModSettingsChanged"):
        if re.search(r"\b" + name + r"\s*\(", signatures):
            add("lifecycle." + name, "info", name + " referenced in source; consult runtime markers for actual invocation")

    referenced = set()
    for match in re.finditer(r'Wh_Get(?:Int|String)Setting\s*\(\s*L?"([^"\n]+)"', code):
        # Only simple literal keys are comparable to the flat settings parser.
        name = match.group(1)
        if re.fullmatch(r"[\w-]+", name):
            referenced.add(name)
    declared = set(settings)
    for name in sorted(referenced - declared):
        add("settings.undeclared", "warning", "literal setting %r has no parsed default; check spelling or parser limitations" % name)
    for name in sorted(declared - referenced):
        add("settings.unused", "info", "setting %r has no detected simple literal read (dynamic/indexed reads are not analysed)" % name)
    if ana.get("internal_hooks") or ana.get("internal_modules"):
        add("hooks.internal", "warning", "internal symbols/modules need Windows execution; a symbol matrix checks availability only")
    if not ana.get("public_hooks") and not ana.get("internal_hooks"):
        add("hooks.none", "info", "no hook call detected heuristically")

    # API fidelity: an unknown name or a stub is more useful to an agent than a
    # silent compile error. These are static observations only.
    for api in fidelity.api_inventory(source)["apis"]:
        if api["status"] == "unknown_api":
            add("api.unknown", "warning", "%s is not in the cached official headers; "
                "do not assume it exists in this shim" % api["name"])
        elif api["status"] == "stub":
            add("api.stub", "warning", "%s is a stub in this shim; calling it cannot "
                "demonstrate real Windhawk behaviour" % api["name"])
    opts = compileroptions.classify(meta.get("compilerOptions") or [])
    for rejected in opts["rejected"]:
        add("compiler_option.rejected", "warning", "@compilerOptions %s will not be "
            "applied: %s" % (rejected["flag"], rejected["reason"]))
    for name in opts["unverified_libraries"]:
        add("library.unverified", "info", "-l%s is not in the known import-library set; "
            "the linker result decides" % name)
    unverifiable = (
        ("tool_mod.not_verifiable", r"\bWH_TOOL_MOD\b", "tool mods need Windhawk's "
         "dedicated tool process; not verifiable here"),
        ("shell.com_private", r"I[A-Z]\w*Shell\w*|IShellItem|ITrayNotify",
         "private shell COM interfaces are not available in this bench"),
        ("shell.dui_xaml", r"\bDUI\b|Windows\.UI\.Xaml|twinui\.pcshell\.dll",
         "DUI/XAML shell internals are not verifiable under Wine"),
    )
    for cid, pattern, message in unverifiable:
        if re.search(pattern, source, re.I):
            add(cid, "warning", message)
    errors = sum(c["severity"] == "error" for c in checks)
    warnings = sum(c["severity"] == "warning" for c in checks)
    return {
        "checks": checks,
        "summary": {"checks": len(checks), "errors": errors, "warnings": warnings,
                    "infos": len(checks) - errors - warnings},
        "note": "Heuristic static checks only: no compilation/execution. Macros, raw strings, nested settings and dynamic keys may be misclassified. Never changes verdict.level.",
    }


def check_file(mod_path):
    with open(mod_path, encoding="utf-8", errors="replace") as f:
        return run_checks(f.read(), mod_path)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="awt.staticchecks")
    ap.add_argument("mod")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    try:
        result = check_file(args.mod)
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    for c in result["checks"]:
        print("%-7s %s: %s" % (c["severity"], c["id"], c["message"]))
    s = result["summary"]
    print("---- %d check(s): %d error(s), %d warning(s), %d info(s)"
          % (s["checks"], s["errors"], s["warnings"], s["infos"]))
    return 1 if s["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
