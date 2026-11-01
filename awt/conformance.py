"""Compare the shim against the OFFICIAL Windhawk headers (section 3.4).

The shim compiles *including* the official windhawk_api_internal.h, so any
signature mismatch is a compile error by construction; this module produces the
human-readable table implemented / stub / missing and keeps docs/shim-status.md
up to date. Run: python3 -m awt.conformance
"""
import os
import re
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFICIAL = os.path.join(REPO, "docs", "official", "windhawk_api_internal.h")
API_H = os.path.join(REPO, "docs", "official", "windhawk_api.h")
SHIM = os.path.join(REPO, "shim", "shim.cpp")

DECL = re.compile(r"^(?:BOOL|void|int|size_t|HANDLE|PCWSTR|const\s+WH_URL_CONTENT\s*\*)\s+(InternalWh_\w+)\s*\(", re.M)


def official_functions():
    src = open(OFFICIAL, encoding="utf-8").read()
    return re.findall(r"InternalWh_\w+", src) and DECL.findall(src)


def shim_status():
    src = open(SHIM, encoding="utf-8").read()
    table = {}
    for name in official_functions():
        m = re.search(r"\b" + name + r"\s*\([^)]*\)\s*\{", src)
        if not m:
            table[name] = "missing"
            continue
        body_start = m.end()
        depth, i = 1, body_start
        while i < len(src) and depth:
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
            i += 1
        body = src[body_start:i]
        table[name] = "stub" if "RecordStub(" in body else "implemented"
    return table


def public_wrappers():
    src = open(API_H, encoding="utf-8").read()
    return sorted(set(re.findall(r"inline\s+[\w\*: &<>]+?\s+(Wh_\w+)\s*\(", src)))


def main():
    table = shim_status()
    wrappers = public_wrappers()
    missing = [k for k, v in table.items() if v == "missing"]
    stubs = sorted(k for k, v in table.items() if v == "stub")
    impl = sorted(k for k, v in table.items() if v == "implemented")
    lines = [
        "# shim-status.md",
        "",
        "Conformance of `shim/shim.cpp` against the official headers cached in",
        "`docs/official/` (windhawk_api.h + windhawk_api_internal.h, Windhawk v1.7.3",
        "distribution headers; engine-side mods_api.h kept for reference).",
        "",
        "Last check: %s" % time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "",
        "Signature mismatches: **0 by construction** — the shim is compiled including the",
        "official `windhawk_api_internal.h`, so a wrong signature cannot build.",
        "",
        "| InternalWh function | status |",
        "|---|---|",
    ]
    for k in sorted(table):
        lines.append("| `%s` | %s |" % (k, table[k]))
    lines += [
        "",
        "Public `Wh_*` inline wrappers seen in the official header (%d): %s" % (len(wrappers), ", ".join("`%s`" % w for w in wrappers)),
        "",
        "Behavioural notes (assumed where the docs are silent, see AGENTS.md):",
        "- missing int setting -> 0; missing string setting -> empty string (assumed)",
        "- hook operations queued by `Wh_SetFunctionHook` apply at `Wh_ApplyHookOperations`",
        "  and automatically when `Wh_ModInit` returns (host calls the shim export)",
        "- symbol lookup (`Wh_FindFirstSymbol`, `Wh_HookSymbols`) always fails in the",
        "  sandbox and is reported as `unverifiable: internal-symbols`",
    ]
    out = os.path.join(REPO, "docs", "shim-status.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("conformance: %d implemented, %d stub, %d missing" % (len(impl), len(stubs), len(missing)))
    if missing:
        print("MISSING:", ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
