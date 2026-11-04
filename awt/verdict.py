"""Marker parsing and the verdict ladder, shared by every backend.

This is the logic that used to live inline in `awt/runner.py` (the Wine path). It
is a top-level module, not part of `awt/backends/`, so `awt.runner` can use it
without importing the backend registry (which imports the runner).
It is here, separate, for two reasons:

* a native Windows run and a Wine run must reach the *same* verdict from the
  *same* host output, otherwise the two backends are not comparable;
* it is pure text in / dict out, so it can be unit-tested on a machine that has
  no Wine and no Windows at all - which is where this bench is developed.

The host (`host/host.cpp`) and the shim log these markers:

    [awt-host] load failed err=<N>
    [awt-host] init returned <N>          or   autoinit returned <N>   (mod log)
    [awt-host] hooks applied count <N>    or   hooks applied <N>       (mod log)
    [awt-host] target exit code <N>
"""
import re

LOAD_FAILED_RE = re.compile(r"\[awt-host\] load failed err=(\d+)")
INIT_RE = re.compile(r"\[awt-host\] init returned (-?\d+)")
AUTOINIT_RE = re.compile(r"autoinit returned (\d+)")
HOOKS_RE = re.compile(r"\[awt-host\] hooks applied count (\d+)")
HOOKS_MOD_RE = re.compile(r"hooks applied (\d+)")
TARGET_EXIT_RE = re.compile(r"\[awt-host\] target exit code (\d+)")
CRASH_RES = (re.compile(r"unhandled (exception|page fault)", re.I),
             re.compile(r"wine:.*crash", re.I),
             # a native Windows host prints the exception code without "wine:"
             re.compile(r"0xc0000[0-9a-f]{3}", re.I))

# init return values that mean "the mod refused or failed to initialise"
BAD_INIT = (0, 2, 3, 5, 6, 7)


def parse_host_markers(host_output, mod_log=""):
    """Everything a verdict needs, out of the two logs. No side effects."""
    host_output = host_output or ""
    mod_log = mod_log or ""

    load_failed_match = LOAD_FAILED_RE.search(host_output)
    init_match = INIT_RE.search(host_output) or AUTOINIT_RE.search(mod_log)
    init_ret = int(init_match.group(1)) if init_match else None
    hooks_match = HOOKS_RE.search(host_output) or HOOKS_MOD_RE.search(mod_log)
    hooks_applied = int(hooks_match.group(1)) if hooks_match else 0
    if init_ret != 1:
        # a mod that did not initialise applied nothing, whatever it printed
        hooks_applied = 0
    exit_match = TARGET_EXIT_RE.search(host_output)
    target_exit = int(exit_match.group(1), 0) if exit_match else None
    # Crash patterns are matched against the *host's* output only. A mod log is
    # the mod's own text: a mod that prints "0xc0000005" while working fine must
    # not be reported as a crash. This is the rule the Wine runner always used.
    crashed = any(rx.search(host_output) for rx in CRASH_RES) \
        or (target_exit is not None and target_exit >= 0xC0000000)
    return {
        "load_failed": bool(load_failed_match),
        "load_error": int(load_failed_match.group(1)) if load_failed_match else None,
        "init_returned_code": init_ret,
        "init_returned": (init_ret == 1) if init_ret is not None else None,
        "hooks_applied": hooks_applied,
        "target_exit_code": target_exit,
        "crashed": crashed,
    }


def count_hooks_fired(mod_log, public_hooks):
    """How often each public-API hook actually ran, per the mod's own log."""
    fired = {}
    mod_log = mod_log or ""
    for name in public_hooks or []:
        count = len(re.findall(re.escape(name) + r"[:\(]", mod_log))
        if count:
            fired[name] = count
    return fired


def verdict_level(markers, hooks_fired):
    """The ladder, weakest to strongest. Unchanged from the Wine-only bench."""
    if markers["load_failed"] or markers["init_returned_code"] in BAD_INIT:
        return "load_failed"
    if markers["crashed"]:
        return "crashed"
    if hooks_fired:
        return "public_hooks_verified"
    return "loads_no_crash"


def verdict_lists(analysis, markers, hooks_fired):
    """`verified` / `not_verifiable_here` / `requires_real_windows`."""
    verified = ["build"]
    if not markers["load_failed"] and markers["init_returned_code"] == 1:
        verified.append("load")
    if hooks_fired:
        verified.append("public API hooks")
    not_verifiable = []
    for symbol in analysis.get("internal_hooks") or []:
        not_verifiable.append("internal-symbols: %s" % symbol)
    for module in analysis.get("internal_modules") or []:
        not_verifiable.append("internal-module: %s" % module)
    not_verifiable = sorted(set(not_verifiable))
    return verified, not_verifiable, bool(not_verifiable)


def apply_run_result(rep, analysis, markers, hooks_fired, exit_code=None,
                     timed_out=False, run_log=None, screenshot=None,
                     stub_log="", target=None, mode=None):
    """Fill `run`, `shim`, `verdict` and `artifacts` of a report. Additive only."""
    rep.setdefault("run", {}).update({
        "target": target,
        "mode": mode,
        "init_returned": markers["init_returned"],
        "init_returned_code": markers["init_returned_code"],
        "hooks_applied": markers["hooks_applied"],
        "hooks_fired": hooks_fired,
        "crashed": markers["crashed"],
        "exit_code": exit_code,
        "timed_out": timed_out,
        "target_exit_code": markers["target_exit_code"],
    })
    shim = rep.setdefault("shim", {"not_implemented_called": [],
                                   "simulated_with_limits": []})
    shim["not_implemented_called"] = sorted(set((stub_log or "").split()))
    if analysis.get("internal_hooks") or analysis.get("internal_modules"):
        for api in ("Wh_FindFirstSymbol", "Wh_HookSymbols"):
            if api not in shim["simulated_with_limits"]:
                shim["simulated_with_limits"].append(api)

    verdict = rep.setdefault("verdict", {})
    verdict["level"] = verdict_level(markers, hooks_fired)
    verified, not_verifiable, requires_real = verdict_lists(
        analysis, markers, hooks_fired)
    verdict["verified"] = verified
    verdict["not_verifiable_here"] = not_verifiable
    verdict["requires_real_windows"] = requires_real
    verdict["summary"] = ("level=%s; verified=%s; not verifiable here=%s"
                          % (verdict["level"], ", ".join(verified) or "nothing",
                             ", ".join(not_verifiable) or "nothing"))

    artifacts = rep.setdefault("artifacts", {"screenshots": [], "wine_log": None,
                                             "dumps": []})
    if run_log:
        artifacts["wine_log"] = run_log
    if screenshot:
        shots = list(artifacts.get("screenshots") or [])
        if screenshot not in shots:
            shots.append(screenshot)
        artifacts["screenshots"] = shots
    return rep
