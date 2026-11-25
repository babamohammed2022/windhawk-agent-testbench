"""Hook coverage: did the bench even call the API the mod hooked?

A mod that hooks `CreateWindowExW` and never logs it is not necessarily broken:
if the host never created a window, the hook had nothing to fire on. Before this
module the report said only `loads_no_crash`, which conflates "the mod is inert"
with "the bench never drove the API". This module separates the two.

The host (`host/host.cpp`) emits one marker per driven API:

    [awt-host] activity CreateWindowExW

`parse_driven_apis` reads those markers; `hook_coverage` joins them with the
static analysis (`awt/analyzer.py`) and the observed firings
(`awt/verdict.count_hooks_fired`) into one entry per hooked API:

    fired              - a matching mod log was seen (count > 0)
    driven_no_log      - the bench attempted the API but no hook log was seen
    not_driven         - no activity marker was observed; nothing is claimed
    init_not_confirmed - successful mod initialisation was not observed

Descriptive only: like the visual comparison, this section never changes
`verdict.level`. It explains the level.
"""
import re

ACTIVITY_RE = re.compile(r"\[awt-host\] activity (\w+)")

# Static expectations, NOT runtime Wine conformance results. `expected` means
# a generic Win32 call; `partial` warns about shell simulation. This table is the contract between host/host.cpp and
# this module: every `activity` marker the host can emit must appear here, and
# every entry here must be emitted by the host. `tests/unit/test_bench_features.py`
# checks the host source against this table so the two cannot drift apart.
DRIVER_CATALOG = {
    "CreateFileW": {"wine_expectation": "expected",
                    "drives": "host self-mode file I/O loop"},
    "ReadFile": {"wine_expectation": "expected",
                 "drives": "host self-mode file I/O loop (read-back)"},
    "WriteFile": {"wine_expectation": "expected",
                  "drives": "host self-mode file I/O loop"},
    "CloseHandle": {"wine_expectation": "expected",
                    "drives": "host self-mode file I/O loop"},
    "CreateWindowExW": {"wine_expectation": "expected",
                        "drives": "host creates a real window"},
    "RegisterClassExW": {"wine_expectation": "expected",
                         "drives": "host registers its window class"},
    "SendMessageW": {"wine_expectation": "expected",
                     "drives": "host sends WM_NULL to its window"},
    "PostMessageW": {"wine_expectation": "expected",
                     "drives": "host posts WM_NULL to its window"},
    "SetWindowPos": {"wine_expectation": "expected",
                     "drives": "host moves its window"},
    "ShowWindow": {"wine_expectation": "expected",
                   "drives": "host shows its window"},
    "GetWindowRect": {"wine_expectation": "expected",
                      "drives": "host queries its window rect"},
    "GetProcAddress": {"wine_expectation": "expected",
                       "drives": "host resolves kernel32 exports"},
    "LoadLibraryW": {"wine_expectation": "expected",
                     "drives": "host loads kernel32 explicitly"},
    "RegOpenKeyExW": {"wine_expectation": "expected",
                      "drives": "host opens HKCU\\Software"},
    "RegQueryValueExW": {"wine_expectation": "expected",
                         "drives": "host queries a value under HKCU\\Software"},
    "GetSystemMetrics": {"wine_expectation": "expected",
                         "drives": "host queries SM_CXSCREEN"},
    "SystemParametersInfoW": {"wine_expectation": "expected",
                              "drives": "host queries SPI_GETBEEP"},
    "DrawTextW": {"wine_expectation": "expected",
                  "drives": "host draws text to its window DC"},
    "CreateFontW": {"wine_expectation": "expected",
                    "drives": "host creates + deletes a font"},
    "Shell_NotifyIconW": {"wine_expectation": "partial",
                          "drives": "host NIM_ADD / NIM_MODIFY / NIM_DELETE"},
}


def parse_driven_apis(host_output):
    """APIs whose attempted-call marker was observed, in first-seen order, de-duplicated."""
    seen = []
    for name in ACTIVITY_RE.findall(host_output or ""):
        if name not in seen:
            seen.append(name)
    return seen


def hook_coverage(analysis, hooks_fired, driven_apis, init_ok=True):
    """One entry per statically detected public hook.

    `driven_apis` is the list from `parse_driven_apis`; pass `None` when the
    caller does not know what was driven (older backends, not_executed reports)
    - every hook is then `not_driven` with `driven_unknown: True`, never
    `driven_no_log`, because claiming the bench drove an API it may not
    have driven would be a false accusation against the mod.
    """
    hooks_fired = hooks_fired or {}
    driven_unknown = driven_apis is None
    driven = set(driven_apis or [])
    entries = []
    for name in sorted(set((analysis or {}).get("public_hooks") or [])):
        fired = int(hooks_fired.get(name, 0) or 0)
        if not init_ok:
            status = "init_not_confirmed"
        elif fired > 0:
            status = "fired"
        elif driven_unknown or name not in driven:
            status = "not_driven"
        else:
            status = "driven_no_log"
        entry = {
            "api": name,
            "status": status,
            "fired_count": fired,
            "driven": (name in driven) if not driven_unknown else None,
            "wine_expectation": (DRIVER_CATALOG.get(name) or {}).get(
                "wine_expectation", "unknown"),
            "drives": (DRIVER_CATALOG.get(name) or {}).get("drives"),
        }
        if driven_unknown:
            entry["driven_unknown"] = True
        entries.append(entry)
    fired_n = sum(1 for e in entries if e["status"] == "fired")
    return {
        "entries": entries,
        "summary": {
            "hooks": len(entries),
            "fired": fired_n,
            "driven_no_log": sum(
                1 for e in entries if e["status"] == "driven_no_log"),
            "not_driven": sum(
                1 for e in entries if e["status"] == "not_driven"),
            "init_not_confirmed": sum(
                1 for e in entries if e["status"] == "init_not_confirmed"),
            "driven_apis": sorted(driven) if not driven_unknown else None,
            "driven_unknown": driven_unknown,
        },
        "note": ("descriptive only: activity markers record attempted calls, not "
                 "successful operations. Firings are inferred from mod-owned logs, "
                 "not independent hook instrumentation. Missing logs do not prove "
                 "a hook failed. Wine expectations are not runtime conformance results."),
    }
