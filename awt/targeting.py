"""Match Windhawk ``@include`` / ``@exclude`` patterns, describe the stand-in.

This module is deliberately pure Python: it does not start processes and does
not claim that a stand-in behaves like Explorer. Its output is a *description*
of how a pattern would select a process, alongside the runner's existing
runtime ``include_analysis`` which records what actually happened.
"""
import fnmatch
import re

# Every executable this bench can build is a stand-in, never the real product.
STAND_INS = {
    "testapp.exe": "generic Win32 window, file I/O and registry activity",
    "trayapp.exe": "notification-icon client (NIM_ADD/MODIFY/DELETE)",
    "enumwins.exe": "window-class probe, no mod interaction",
    "awt-desktop.exe": "scripted Win32 scene; synthetic Shell_TrayWnd classes",
    "awt-playground.exe": "static GDI specimen for before/after captures",
    "awt-host.exe": "in-process loader; generic Win32 activity in the host itself",
}

# Mapping is advisory: the runner may still fall back, and it always records the
# fallback reason in `include_analysis`. Only testapp/trayapp/enumwins can be
# built by builder.build_target today.
BUILDABLE_TARGETS = ("testapp.exe", "trayapp.exe", "enumwins.exe")


def _normalise(path):
    return path.replace("/", "\\").lower()


def matches(pattern, process, full_path=None):
    """Whether a Windhawk include/exclude pattern selects this process.

    Windhawk patterns use `*` / `?` wildcards and may be a bare executable name
    or a full path. Matching is case-insensitive, as on Windows. A full-path
    pattern is compared with the full path when one is supplied, and with the
    file name otherwise; a bare pattern never matches a path it does not end
    with. `*` alone matches any process.
    """
    pat = _normalise(pattern.strip())
    proc = _normalise(process or "")
    if not pat:
        return False
    if pat == "*":
        return True
    if "\\" in pat:
        if full_path:
            # A full-path pattern must not match just because the file name
            # agrees: that would select the wrong process in a report.
            return fnmatch.fnmatchcase(_normalise(full_path), pat)
        return fnmatch.fnmatchcase(proc, pat) or fnmatch.fnmatchcase(
            proc, pat.rsplit("\\", 1)[-1])
    if "\\" in proc:
        proc = proc.rsplit("\\", 1)[-1]
    return fnmatch.fnmatchcase(proc, pat)


def select(meta, analysis=None, available=None):
    """Describe target selection from parsed metadata.

    Returns a dict that is always safe to attach to a report: it says what was
    *selected statically*, what stand-in the bench would use, and that no shell
    semantics are implied. `available` restricts the suggested stand-in to
    targets that exist in a given runner.
    """
    analysis = analysis or {}
    includes = list(meta.get("include") or ["*"])
    excludes = list(meta.get("exclude") or [])
    allowed = list(available or BUILDABLE_TARGETS)
    entries = []
    for pattern in includes:
        excluded_by = [e for e in excludes if matches(e, pattern)]
        entries.append({"include": pattern,
                        "excluded_by": excluded_by,
                        "applies": not excluded_by})

    subjects = list(analysis.get("subjects") or [])
    applies = [e["include"] for e in entries if e["applies"]]
    if not applies:
        chosen, why = None, ("every @include is vetoed by @exclude; the bench "
                             "cannot select a process without a mod")
    elif any(re.search(r"startmenu|shellxperience|searchhost|start-menu", i, re.I)
             for i in applies):
        chosen = None
        why = ("the Start/XAML host has no faithful stand-in under Wine; the mod's "
               "shell behaviour is not_verifiable here")
    elif any("explorer" in i.lower() for i in applies):
        candidates = ["trayapp.exe", "testapp.exe"]
        chosen = next((c for c in candidates if c in allowed), None)
        why = ("explorer include mapped to a notification/generic stand-in; "
               "never to Explorer")
        if any("control" in i.lower() for i in applies):
            why += ("; the control.exe include is only probed and is not a second "
                    "execution target")
    elif any("control" in i.lower() for i in applies):
        chosen = "testapp.exe" if "testapp.exe" in allowed else (allowed[0] if allowed else None)
        why = "control-panel surface is only probed; a generic stand-in is used"
    elif "system-tray" in subjects:
        candidates = ["trayapp.exe", "testapp.exe"]
        chosen = next((c for c in candidates if c in allowed), None)
        why = "tray subject mapped to a notification client stand-in; never to Explorer"
    else:
        chosen = "testapp.exe" if "testapp.exe" in allowed else (allowed[0] if allowed else None)
        why = "no specific stand-in matched; generic Win32 stand-in selected"

    return {
        "schema": 1,
        "include": includes,
        "exclude": excludes,
        "entries": entries,
        "suggested_stand_in": chosen,
        "reason": why,
        "target_kind": "stand-in",
        "stand_in_role": STAND_INS.get(chosen) if chosen else None,
        "shell_semantics": "simulated",
        "note": ("Static selection only. The run records its actual target and any "
                 "fallback in include_analysis. A stand-in is not Explorer, a "
                 "common dialog, COM/WinRT or any other Windows shell component."),
    }
