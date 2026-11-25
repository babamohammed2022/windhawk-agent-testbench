#!/usr/bin/env python3
"""Real Wine acceptance for the simulated desktop lab (separate from unit tests).

0 = executed assertions passed, 1 = failed, 3 = prerequisites unavailable.
Generated images in unit tests are fixtures; only this script can establish
that desktop.cpp compiled and that a real Win32 scene was captured.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from awt import desktop, png  # noqa: E402

MOD = ROOT / "examples" / "visual-playground.wh.cpp"


def main():
    results = []
    summary = ROOT / ".sandbox/desktop-acceptance.json"
    summary.parent.mkdir(exist_ok=True)
    try:
        for enabled in (True, False):
            result = desktop.generate(MOD, settings_override={"enabled": enabled},
                                       expect_mod_effect=enabled)
            results.append(result)
            print("desktop enabled=%s: %s; artifacts=%s"
                  % (enabled, result["status"], result["output"]), flush=True)
            if result["status"] == "not_executed":
                print("SKIPPED desktop acceptance: " + result["reason"])
                return 3
            if result["status"] != "passed":
                for name in ("host.log", "mod.log", "stubs.log", "cleanup.log", "xvfb.log"):
                    path = Path(result["output"]) / name
                    if path.is_file():
                        print("--- %s (tail) ---" % name)
                        print(path.read_text(encoding="utf-8", errors="replace")[-4000:])
                raise AssertionError(result.get("reason", result["status"]))
            image = png.read(result["artifacts"]["screenshot"])
            assert (image.width, image.height) == (960, 600), "unexpected desktop geometry"
            assert all(a["passed"] for a in result["assertions"]), "observable assertion failed"
            accent = desktop.count_color(image, (24, 95, 160))
            notice = desktop.count_color(image, (250, 220, 120))
            assert notice > 1000, "the simulated notification was not visible"
            if enabled:
                assert accent > 10000, "the real DrawTextW hook did not paint the accent"
                assert result["host"]["init_returned"] is True
            else:
                assert accent < 100, "disabled demo unexpectedly painted the accent"
            assert result["cleanup"]["ok"] is True, "cleanup did not complete"
            assert result["process"]["explorer"] is False
            assert result["profile"] == "win10", "the Win10-like profile was not selected"
            assert result["shell_probe"], "no EnumWindows probe was logged"
            assert result["appbar"], "no appbar call was logged"
            assert any(a["id"] == "no_crash" and a["passed"]
                       for a in result["assertions"]), "no crash assertion was checked"
            print("PASS: scripted windows, click, notice, capture, accent assertion "
                  "and cleanup (enabled=%s)" % enabled, flush=True)
        return 0
    except (AssertionError, OSError, ValueError, KeyError) as exc:
        print("FAIL desktop acceptance: %s" % exc, file=sys.stderr)
        return 1
    finally:
        summary.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
