"""Additive, evidence-based fidelity sections for the JSON report.

Rule of this module: a capability is ``verified`` only when this bench really
executed something that demonstrates it. Emulated behaviour is ``simulated``,
missing prerequisites are ``not_executed``, and anything that needs the real
Windhawk engine or a real Windows shell is ``not_verifiable``. These sections
never modify ``verdict.level`` and never turn a simulation into a pass.
"""
import os
import re

from . import conformance, targeting

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFICIAL_API = os.path.join(REPO, "docs", "official", "windhawk_api.h")
OFFICIAL_UTILS = os.path.join(REPO, "docs", "official", "windhawk_utils.h")

CALL_RE = re.compile(
    r"\b(?:(WindhawkUtils)::([A-Za-z_]\w*)|(Wh_[A-Za-z0-9_]+))\s*\(")
COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'", re.S)

# Windhawk Utils helpers are C++ inline code in the official header, not engine
# APIs. Their status is the status of the engine call underneath them; helpers
# that need a real Windows window/shell are explicitly not verified here.
UTILS_UNDERLYING = {
    "SetFunctionHook": "InternalWh_SetFunctionHook",
    "HookSymbols": "InternalWh_HookSymbols",
    "StringSetting": "InternalWh_GetStringSetting",
    "SetWindowSubclassFromAnyThread": None,
    "RemoveWindowSubclassFromAnyThread": None,
}

# Exports implemented by the mod, not calls into the engine API. Treating them
# as unknown APIs would be a false warning on nearly every real mod.
LIFECYCLE = {
    "Wh_ModInit", "Wh_ModAfterInit", "Wh_ModBeforeUninit", "Wh_ModUninit",
    "Wh_ModSettingsChanged",
}

# Wh_Log is a macro in the non-editing build and an empty inline body in the
# WH_EDITING variant; neither body names InternalWh_Log, so map it explicitly.
WRAPPER_FALLBACK = {"Wh_Log": "InternalWh_Log"}


def _code(source):
    return COMMENT_RE.sub(lambda m: " " if m.group().startswith(("//", "/*")) else
                          m.group(), source or "")


def _wh_to_internal(header):
    """Map public wrapper names to engine calls found in their inline bodies."""
    mapping = dict(WRAPPER_FALLBACK)
    for match in re.finditer(r"\binline\s+[\w\s\*:&<>,]+?\b(Wh_\w+)\s*\([^;{]*\)\s*\{",
                             header):
        start = match.end() - 1
        depth, index = 0, start
        while index < len(header):
            if header[index] == "{":
                depth += 1
            elif header[index] == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        body = header[start:index]
        internal = re.search(r"\b(InternalWh_\w+)\s*\(", body)
        if internal and match.group(1) not in mapping:
            mapping[match.group(1)] = internal.group(1)
    return mapping


def api_inventory(source, shim_status=None):
    """Every Windhawk Utils / Wh_* call visible in the source, with its status.

    This is static analysis: seeing a call is not evidence that it executed.
    An API unknown to the cached official headers is reported as such instead
    of being silently accepted (the mod may be targeting a newer Windhawk, or
    the name may be a typo).
    """
    shim_status = shim_status if shim_status is not None else conformance.shim_status()
    api_h = open(OFFICIAL_API, encoding="utf-8").read()
    utils_h = open(OFFICIAL_UTILS, encoding="utf-8").read()
    wh_map = _wh_to_internal(api_h)
    known_wh = set(re.findall(r"\bWh_[A-Za-z0-9_]+\s*\(", api_h + utils_h))
    known_wh = {name[:-1].strip() for name in known_wh}
    known_utils = set(UTILS_UNDERLYING)

    apis, warnings, seen = [], [], set()
    for match in CALL_RE.finditer(_code(source)):
        namespace, name = match.group(1), match.group(2) or match.group(3)
        if namespace:
            full = "WindhawkUtils::" + name
            if name not in known_utils:
                status, detail = "unknown_api", "helper not in the cached official utils header"
            elif UTILS_UNDERLYING[name] is None:
                status, detail = ("wine_dependent",
                                  "inline helper uses real Win32 windowing APIs; "
                                  "no Windhawk engine evidence here")
            else:
                inner = UTILS_UNDERLYING[name]
                status = {"stub": "stub", "missing": "not_in_shim"}.get(
                    shim_status.get(inner), "shim_implemented")
                detail = "backed by %s (%s)" % (inner, shim_status.get(inner, "unknown"))
        else:
            full = name
            if name in LIFECYCLE:
                status, detail = ("lifecycle_export",
                                  "export implemented by the mod, not an engine API call")
            elif name not in known_wh:
                status, detail = ("unknown_api",
                                  "not present in the cached Windhawk 1.7.3 headers")
            else:
                inner = wh_map.get(name)
                status = {"stub": "stub", "missing": "not_in_shim"}.get(
                    shim_status.get(inner), "shim_implemented") if inner else "wrapper_only"
                detail = ("backed by %s (%s)" % (inner, shim_status.get(inner, "unknown"))
                          if inner else "inline wrapper; no separate engine call")
        if full in seen:
            continue
        seen.add(full)
        apis.append({"name": full, "status": status, "detail": detail,
                     "evidence": "static source reference only; execution not proven"})
        if status == "unknown_api":
            warnings.append({"id": "api.unknown", "severity": "warning",
                             "message": "%s is not in the cached official headers; "
                                        "it may be a typo or a newer API" % full})
        elif status == "stub":
            warnings.append({"id": "api.stub", "severity": "warning",
                             "message": "%s is a stub in this shim: it cannot demonstrate "
                                        "real Windhawk behaviour" % full})

    return {"schema": 1, "apis": apis, "warnings": warnings,
            "header_basis": "docs/official/windhawk_api.h + windhawk_utils.h (v1.7.3)",
            "note": ("Static inventory. 'shim_implemented' means the shim has an "
                     "implementation; it does not mean the call ran or that the "
                     "behaviour matches the Windhawk engine.")}


def _status(value, evidence, note=""):
    entry = {"status": value, "evidence": evidence}
    if note:
        entry["note"] = note
    return entry


def capabilities(rep, build_result=None, meta=None, source="", analysis=None):
    """Per-capability evidence matrix, derived only from data already in the report."""
    build_result = build_result or {}
    meta = meta or {}
    analysis = analysis or {}
    run = rep.get("run") or {}
    verdict_block = rep.get("verdict") or {}
    level = verdict_block.get("level")
    executed = level not in (None, "not_executed", "build_failed")
    not_run = _status("not_executed", "no mod execution happened in this report")
    items = {}

    if build_result.get("ok"):
        items["compile"] = _status("verified", "the compiler produced the mod DLL here")
    elif (not build_result or build_result.get("toolchain_missing") or
          build_result.get("errors") and "not attempted" in str(build_result["errors"])):
        items["compile"] = not_run
    else:
        items["compile"] = _status("failed", "the compiler was invoked and reported errors")

    if executed and run.get("init_returned") is True:
        items["load"] = _status("verified", "Wh_ModInit returned success in an executed process")
    else:
        items["load"] = not_run if not executed else _status(
            "failed", "mod initialisation was not confirmed")

    fired = run.get("hooks_fired") or {}
    if executed and fired:
        items["public_hooks"] = _status(
            "verified", "hook firings observed in the executed mod's log: %s"
            % ", ".join(sorted(fired)),
            "counts come from mod-owned logs, not independent instrumentation")
    else:
        items["public_hooks"] = not_run if not executed else _status(
            "simulated", "no hook firing was observed; absence of logs is not proof")

    items["internal_symbols"] = _status(
        "not_verifiable", "the shim cannot resolve PDB/export symbols; the offline "
        "symbol matrix checks names only",
        "a configured symbol table would be simulated and must never upgrade a verdict")
    items["settings"] = (_status("simulated", "settings are read from a flat JSON file "
                                 "generated from the YAML block, not from the Windhawk UI")
                         if executed else not_run)
    items["persistent_storage"] = (_status("simulated", "per-mod text file under "
                                            ".sandbox/storage, not Windhawk's storage")
                                   if executed else not_run)
    items["target_shell"] = _status(
        "simulated", "targets are declared Win32 stand-ins; no Explorer or shell "
        "component is loaded")
    if executed:
        items["injection"] = _status(
            "simulated" if (run.get("mode") == "self" or not run.get("mode")) else
            "not_verifiable",
            "this bench uses LoadLibraryW in its own host; remote injection and "
            "AppInit_DLLs are unavailable under Wine (docs/limitations.md)")
    else:
        items["injection"] = not_run

    tool_mod = bool(re.search(r"\bWH_TOOL_MOD\b", source or "") or
                    "tool" in str(meta.get("raw", {}).get("architecture", "")).lower())
    items["tool_mod"] = (_status("not_verifiable",
                                 "no dedicated tool process exists in this bench")
                         if tool_mod else _status("not_applicable", "mod is not a tool mod"))
    stub_refs = [a["name"] for a in (api_inventory(source)["apis"])
                 if a["status"] in ("stub", "not_in_shim")]
    for cap, why in (
            ("disassembly", "Wh_Disasm is a stub in the shim"),
            ("url_fetch", "Wh_GetUrlContent is a stub in the shim"),
            ("dynamic_select", "dynamic-select options are stubs in the shim")):
        prefix = {"disassembly": "Wh_Disasm", "url_fetch": "Wh_GetUrlContent",
                  "dynamic_select": "Wh_SetDynamicSelectOption"}[cap]
        items[cap] = (_status("not_verifiable", why)
                      if any(a.startswith(prefix) or prefix in a for a in stub_refs)
                      else _status("not_applicable", "not referenced by the mod"))
    items["unload_hook_integrity"] = _status(
        "not_verifiable", "no byte-level check that unload restored hooked functions")
    if (rep.get("artifacts") or {}).get("screenshots"):
        items["screenshot"] = _status("verified", "a real capture file was produced here",
                                      "pixels do not prove functional correctness")
    else:
        items["screenshot"] = _status("not_applicable", "no screenshot was requested or captured")

    summary = {}
    for entry in items.values():
        summary[entry["status"]] = summary.get(entry["status"], 0) + 1
    return {"schema": 1, "items": items, "summary": summary,
            "note": ("verified = executed here; simulated = emulated by this bench; "
                     "not_verifiable = needs real Windhawk/Windows; not_executed = no "
                     "evidence; failed = execution attempted and failed. Descriptive "
                     "only: never changes verdict.level.")}


def attach(rep, build_result=None, meta=None, source="", analysis=None):
    """Additively attach all fidelity sections to a report; never mutate verdict."""
    build_result = build_result or {}
    meta = meta or {}
    source = source or ""
    inv = api_inventory(source)
    rep["api_inventory"] = inv
    rep["capabilities"] = capabilities(rep, build_result, meta, source, analysis)
    rep["target_selection"] = targeting.select(meta, analysis or {})
    warnings = list(inv["warnings"])
    opts = build_result.get("compiler_options") or {}
    for rejected in opts.get("rejected") or []:
        warnings.append({"id": "compiler_option.rejected", "severity": "warning",
                         "message": "@compilerOptions %s was not applied: %s" % (
                             rejected["flag"], rejected["reason"])})
    for name in opts.get("unverified_libraries") or []:
        warnings.append({"id": "library.unverified", "severity": "warning",
                         "message": "-l%s is outside the known import-library set; "
                                    "only the linker result can decide" % name})
    for item in (analysis or {}).get("internal_hooks") or []:
        warnings.append({"id": "symbols.not_verifiable", "severity": "warning",
                         "message": "internal symbol %s cannot be resolved under this shim" % item})
    if rep["capabilities"]["items"]["tool_mod"]["status"] == "not_verifiable":
        warnings.append({"id": "tool_mod.not_verifiable", "severity": "warning",
                         "message": "tool mods need a dedicated Windhawk tool process "
                                    "which this bench does not provide"})
    rep["fidelity_warnings"] = warnings
    return rep
