#!/usr/bin/env python3
"""Optional runtime probe with the public Win7/8/8.1 taskbar recreation mod.

This does NOT assert that the mod works. It checks that the simulated desktop
observes and logs what happens when that real mod is loaded here: a clean
refusal (build or init failure) is an honest result; a crash or a silent run is
not. The mod source is never vendored: point --mod at your own clone.

0 = probe ran and produced an honest observation, 1 = crash/silent failure,
3 = prerequisites or the external mod are unavailable (not a pass).
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from awt import desktop  # noqa: E402

DEFAULT_MOD = (ROOT / ".sandbox" / "windhawk-mods" / "mods" /
               "win81-taskbar-recreation.wh.cpp")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mod", type=Path, default=DEFAULT_MOD)
    args = parser.parse_args(argv)
    if not args.mod.is_file():
        print("SKIPPED taskbar probe: mod not found at %s (clone upstream into "
              ".sandbox/windhawk-mods or pass --mod)" % args.mod)
        return 3

    summary = ROOT / ".sandbox" / "taskbar-probe.json"
    summary.parent.mkdir(exist_ok=True)
    try:
        result = desktop.generate(args.mod, scenario="taskbar-compat",
                                  expect_mod_effect=False)
    except (OSError, ValueError) as exc:
        print("FAIL taskbar probe: %s" % exc, file=sys.stderr)
        return 1
    finally:
        if 'result' in locals():
            summary.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")

    print("taskbar probe: status=%s report=%s" % (result["status"], result["output"]))
    if result["status"] == "not_executed":
        print("SKIPPED taskbar probe: " + result.get("reason", ""))
        return 3
    crashed = result["host"].get("crashed") if result.get("host") else None
    errors = result.get("errors") or []
    if crashed or any("unhandled-exception" in e for e in errors):
        print("FAIL taskbar probe: the environment reported a crash; see the report")
        return 1
    if result["status"] == "build_failed":
        build_errors = (result.get("build") or {}).get("errors") or []
        print("PROBE OK: mod refused at build time (%d error(s)); no mod was loaded "
              "and no functionality is claimed" % len(build_errors))
        return 0
    if result["status"] == "run_failed":
        print("PROBE OK: run stopped cleanly (%s); no crash markers. Mod functionality "
              "is not claimed" % result.get("reason", ""))
        return 0
    if result["status"] != "passed":
        print("FAIL taskbar probe: unexpected status %s" % result["status"])
        return 1
    internal = (result.get("mod_analysis") or {}).get("internal_hooks") or []
    print("PROBE OK: scene completed without a crash; %d internal hook(s) remain "
          "not verifiable in this shim" % len(internal))
    print("This is an observation about the simulated desktop, NOT evidence that the "
          "taskbar recreation works on Windows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
