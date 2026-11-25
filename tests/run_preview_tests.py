#!/usr/bin/env python3
"""Real Wine acceptance of the visual path, not generated screenshot fixtures.

0 = executed assertions passed, 1 = failed, 3 = prerequisites unavailable.
All runtime artifacts stay in .sandbox/previews/ and .sandbox/preview-acceptance.json.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from awt import png, preview  # noqa: E402


def main():
    results = []
    summary = ROOT / ".sandbox/preview-acceptance.json"
    summary.parent.mkdir(exist_ok=True)
    try:
        for enabled in (True, False):
            result = preview.generate(ROOT / "examples/visual-playground.wh.cpp",
                                      settings_override={"enabled": enabled})
            results.append(result)
            print("preview enabled=%s: %s; artifacts=%s" % (enabled, result["status"], result["output"]), flush=True)
            if result["status"] == "not_executed":
                print("SKIPPED visual acceptance: " + result["reason"])
                return 3
            assert result["status"] == "captured", result.get("reason")
            left = png.read(result["phases"]["before"]["capture"])
            right = png.read(result["phases"]["after"]["capture"])
            assert (left.width, left.height) == (640, 380), "unexpected baseline client dimensions"
            assert (right.width, right.height) == (640, 380), "demo should not change geometry"
            assert result["phases"]["after"]["markers"]["init_returned"] is True
            assert all(p["cleanup_ok"] for p in result["phases"].values())
            # An independent observable: count the accent-coloured pixels in the
            # actual capture (not mod log text or a manually fabricated image).
            accent = bytes((24, 95, 160))
            def count_accent(image):
                return sum(image.pixels[i:i + 3] == accent for i in range(0, len(image.pixels), 3))
            assert count_accent(left) < 100, "baseline unexpectedly has the demo accent"
            if enabled:
                assert count_accent(right) > 10000, "real inline hook did not visibly paint the accent"
                assert result["visual_comparison"]["overall"]["status"] == "mismatch"
            else:
                assert count_accent(right) < 100, "disabled mod should not paint an accent"
                # Exact stability is deliberately checked for this fixed fixture,
                # not imposed as a correctness requirement on arbitrary mods.
                assert left.pixels == right.pixels, "disabled control differs from clean specimen"
            print("PASS: real client capture, hook init, accent assertion and cleanup (enabled=%s)" % enabled, flush=True)
        return 0
    except (AssertionError, OSError, ValueError) as exc:
        print("FAIL visual acceptance: %s" % exc, file=sys.stderr)
        return 1
    finally:
        summary.write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
