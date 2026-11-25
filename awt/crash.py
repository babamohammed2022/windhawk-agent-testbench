"""Conservative diagnostic triage, not a verdict or root-cause analysis.

Only host output and process/target outcomes are used, never mod-owned logs.
Exception-looking text is heuristic evidence. A Wine/ntdll/shim stack frame
alone cannot identify who caused a fault, so attribution remains unknown.
"""
import re

CRASH_PATTERNS = (
    re.compile(r"unhandled (exception|page fault)", re.I),
    re.compile(r"wine:.*crash", re.I),
    re.compile(r"0xc0000[0-9a-f]{3}", re.I),
)


def _exception_exit(code):
    # Windows return codes can be represented as signed 32-bit integers.
    if not isinstance(code, int) or -255 <= code < 0:
        return False
    return 0xC0000000 <= (code & 0xFFFFFFFF) <= 0xFFFFFFFF


def classify_crash(host_output, exit_code=None, timed_out=False,
                   load_failed=False, target_exit_code=None):
    """Return bounded evidence; a timeout is not proof of a deadlock."""
    evidence = []
    for line in (host_output or "").splitlines():
        if any(rx.search(line) for rx in CRASH_PATTERNS):
            evidence.append(line.strip()[:200])
            if len(evidence) == 8:
                break
    crashed = bool(evidence) or _exception_exit(exit_code) or _exception_exit(target_exit_code)
    if crashed:
        kind = "crash"
    elif load_failed:
        kind = "load_failure"
    elif timed_out:
        kind = "timeout"
    else:
        kind = "none"
    return {
        "kind": kind,
        "crashed": crashed,
        "timed_out": bool(timed_out),
        "faulting_module": "unknown" if crashed else None,
        "exit_code": exit_code,
        "target_exit_code": target_exit_code,
        "evidence": evidence,
        "note": ("diagnostic only: log matching is heuristic; a timeout may be "
                 "a slow environment, not a hang. Stack frames do not establish "
                 "root cause. No fault is attributed to mod, shim or Wine."),
    }
