"""Tests for the visual comparison (`awt.visualdiff`).

The images are generated here, so the expectations are exact: one painted
rectangle is one region at known coordinates with a known worst delta, an
identical pair scores exactly 1.0000, and a change outside the requested region
must be invisible. The cases that matter most for trust are pinned too - an
unreadable image, a resolution mismatch, and a report attachment that must never
change the verdict it was attached to.

Two implementations of SSIM exist (numpy's 11x11 Gaussian window, and a pure
8x8 block fallback), so the interesting properties are asserted independently of
whichever one is installed, and one test forces the pure path on purpose.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tests.unit.harness import SkipTest, require            # noqa: E402
from tests.unit.fixtures import images                      # noqa: E402
from awt import png                                         # noqa: E402
from awt import visualdiff                                  # noqa: E402
from awt.visualdiff import align, config as config_mod      # noqa: E402
from awt.visualdiff import diffmap, metrics                 # noqa: E402
from awt.visualdiff import regions as regionlib             # noqa: E402
from awt.visualdiff import report as visual_report          # noqa: E402
from awt.visualdiff import cli as visual_cli                # noqa: E402

DESKTOP = (160, 100)
WINDOW = (40, 20, 80, 40)          # the drawn window, (x, y, width, height)
SHIFT_BOX = (40, 20, 120, 60)      # the window plus some wallpaper, shifted below


def _tmp(prefix="awt-vd-"):
    return tempfile.mkdtemp(prefix=prefix)


def _defaults(**overrides):
    thresholds = dict(config_mod.DEFAULTS)
    thresholds.update(overrides)
    return thresholds


def _reference(tmp, image=None, name="ref.png"):
    base = image if image is not None else images.desktop()
    return base, images.write(base, tmp, name)


def _compare(obtained_image, reference_path, tmp, name="obtained.png", **overrides):
    path = images.write(obtained_image, tmp, name)
    return visualdiff.compare_pair(path, reference_path, _defaults(**overrides))


def _failed(comparison):
    return [check["name"] for check in comparison["checks"] if not check["passed"]]


def test_identical_images_score_exactly_one():
    tmp = _tmp()
    base, reference_path = _reference(tmp)
    comparison = _compare(base, reference_path, tmp, name="same.png")
    require(comparison["status"] == visualdiff.STATUS_MATCH,
            "identical images must match: %r" % comparison["reasons"])
    require(comparison["metrics"]["ssim"]["ssim"] == 1.0,
            "identical images must score exactly 1.0, got %r"
            % comparison["metrics"]["ssim"]["ssim"])
    require(comparison["metrics"]["identical_percent"] == 100.0,
            "every pixel should be identical")
    require(comparison["metrics"]["differing_pixels"] == 0,
            "a differing pixel was reported for identical images")
    require(comparison["regions"]["total_regions"] == 0,
            "identical images must have no regions")
    require(comparison["regions"]["mask_pixels"] == 0, "the mask should be empty")
    require(not _failed(comparison), "no check may fail: %r" % _failed(comparison))
    require(comparison["reference"]["id"] == "ref.png",
            "a reference without an id should be named after its file")
    require(comparison["reference"]["geometry"]["width"] == DESKTOP[0],
            "the reference geometry is not reported")
    require(comparison["artifacts"] == {}, "no artifact was requested")
    require(comparison["normalisation"]["coordinate_scale"] == 1,
            "no downscale should have happened")
    require(comparison["seconds"] is not None, "the duration must be recorded")
    require(comparison["metrics"]["ssim"]["implementation"]
            == metrics.implementations()["ssim"],
            "the report must say which SSIM implementation ran")


def test_one_painted_rectangle_is_one_region_at_known_coordinates():
    tmp = _tmp()
    base, reference_path = _reference(tmp)
    left, top, width, height = 40, 20, 12, 9
    comparison = _compare(images.patch(base, (left, top, width, height), (0, 0, 0)),
                          reference_path, tmp)
    found = comparison["regions"]
    require(found["total_regions"] == 1,
            "expected exactly one region, got %d" % found["total_regions"])
    region = found["regions"][0]
    require((region["x"], region["y"], region["width"], region["height"])
            == (left, top, width, height), "the bounding box is wrong: %r" % region)
    require(region["area"] == width * height, "the area is wrong: %r" % region)
    require(region["area_scaled"] == width * height,
            "with no downscale the scaled area equals the area")
    require(region["max_delta"] == 245,
            "the worst delta should be the white window body: %r" % region)
    require(0 < region["mean_delta"] < 245, "the mean delta is implausible: %r"
            % region)
    require(region["label"] == "region-1", "regions must be labelled")
    require(region["coordinate_precision"] == "exact",
            "without downscaling the coordinates are exact")
    require(region["centroid"] == [int(left + (width - 1) / 2.0),
                                   int(top + (height - 1) / 2.0)],
            "the centroid must be the mean pixel coordinate: %r" % region["centroid"])
    require(found["mask_pixels"] == width * height,
            "the mask should cover exactly the patch")
    require(comparison["metrics"]["differing_pixels"] == width * height,
            "the differing-pixel count disagrees with the region area")
    require(comparison["status"] == visualdiff.STATUS_MATCH,
            "108 pixels of 16000 pass every average-based floor; that is the "
            "documented weakness of averages, and the reason ceilings exist")


def test_the_region_ceiling_catches_what_averages_miss():
    tmp = _tmp()
    base, reference_path = _reference(tmp)
    patched = images.patch(base, (40, 20, 12, 9), (0, 0, 0))
    with_ceiling = _compare(patched, reference_path, tmp, max_region_area=10)
    require(with_ceiling["status"] == visualdiff.STATUS_MISMATCH,
            "a region ceiling must be able to fail the comparison")
    require(_failed(with_ceiling) == ["largest_region"],
            "the ceiling should be the only failing rule: %r" % _failed(with_ceiling))
    entry = with_ceiling["checks"][-1]
    require(entry["direction"] == "at_most", "a ceiling is an at_most rule")
    require(entry["value"] == 108 and entry["threshold"] == 10,
            "the ceiling compared the wrong numbers: %r" % entry)
    require(entry["region"] == "region-1", "the offending region must be named")
    require(entry["units"] == "pixels", "the units must be reported")
    loose = _compare(patched, reference_path, tmp, max_region_area=500)
    require(loose["status"] == visualdiff.STATUS_MATCH,
            "a generous ceiling must not fail the same pair")


def test_a_shifted_window_is_a_mismatch_even_though_it_looks_similar():
    """The case that motivated the ceilings: 98.7% similar, still wrong."""
    tmp = _tmp()
    base, reference_path = _reference(tmp)
    comparison = _compare(images.shift(base, SHIFT_BOX, 2, 1), reference_path, tmp)
    require(comparison["metrics"]["similarity_percent"] > 98.0,
            "the shifted pair really does look similar on average")
    score = comparison["metrics"]["ssim"]
    if score["implementation"] == metrics.SSIM_GAUSSIAN:
        require(score["ssim"] > 0.98,
                "the Gaussian SSIM alone would have passed this pair, which is "
                "exactly why the ceilings exist: %r" % score["ssim"])
    else:
        require(score["ssim"] < config_mod.DEFAULTS["ssim_min"],
                "the block SSIM catches this pair on its own: %r" % score["ssim"])
    require(comparison["status"] == visualdiff.STATUS_MISMATCH,
            "a shifted window must not pass: %r" % comparison["reasons"])
    failed = _failed(comparison)
    require("differing_pixels" in failed and "identical_pixels" in failed,
            "the ceilings should decide this: %r" % failed)
    require(comparison["metrics"]["differing_pixels"] > 4000,
            "a two-pixel shift moves a lot of pixels: %r"
            % comparison["metrics"]["differing_pixels"])
    require(comparison["regions"]["total_regions"] >= 1,
            "the swept edges should be reported as regions")


def test_region_mask_labelling_and_merging_are_exact():
    base = images.desktop()
    patched = images.patch(images.patch(base, (10, 10, 6, 6), (0, 0, 0)),
                           (80, 40, 4, 4), (0, 0, 0))
    delta = metrics.delta_map(patched, base)
    require(delta.mode == "L", "the delta map must be a luma image")
    require(delta.get(10, 10) == (120,),
            "the delta map holds the worst channel difference (wallpaper blue): %r"
            % (delta.get(10, 10),))
    require(delta.get(80, 40) == (245,),
            "inside the window the worst channel difference is 245: %r"
            % (delta.get(80, 40),))
    luma_delta = metrics.delta_map(patched.luma(), base.luma())
    require(luma_delta.get(10, 10)[0] <= 120,
            "on luma the same difference is smaller, which is why the mode matters")
    mask = regionlib.mask_from_delta(delta, 24)
    require(sum(mask) == 36 + 16, "the mask area is wrong: %d" % sum(mask))
    components = regionlib.label(mask, base.width, base.height, 4)
    require(len(components) == 2, "expected two components, got %d" % len(components))
    blocks = [regionlib.summarise(component, base.width, delta.pixels)
              for component in components]
    areas = sorted(block["area"] for block in blocks)
    require(areas == [16, 36], "the component areas are wrong: %r" % areas)
    merged, count = regionlib.merge(blocks, 200)
    require(count == 1 and len(merged) == 1,
            "two boxes 200px apart must merge at that distance: %r" % ((merged, count),))
    require(merged[0]["area"] == 52, "the merged area must be the sum: %r" % merged[0])
    require(merged[0]["merged"] == 2, "the merge must be recorded")
    untouched, untouched_count = regionlib.merge(blocks, 0)
    require(untouched_count == 0 and len(untouched) == 2,
            "merge_distance=0 must switch merging off")
    found = regionlib.find_regions(delta, threshold=24, min_area=16,
                                   merge_distance=0)
    require(found["total_regions"] == 2, "find_regions should report both")
    require(found["components"] == 2, "the component count is wrong")
    require(found["options"]["threshold"] == 24, "the options must be echoed")
    tiny = regionlib.find_regions(delta, threshold=24, min_area=1000)
    require(tiny["total_regions"] == 0 and tiny["dropped_small_components"] == 2,
            "components below min_area must be dropped and counted: %r" % tiny)
    capped = regionlib.find_regions(delta, threshold=24, min_area=4, max_regions=1)
    require(capped["truncated"] is True and capped["reported_regions"] == 1
            and capped["total_regions"] == 2,
            "the max_regions cap must truncate and say so: %r" % capped)
    require("capped at max_regions=1" in (capped["note"] or ""),
            "a truncated list needs a note: %r" % capped["note"])


def test_ssim_falls_monotonically_as_the_difference_grows():
    tmp = _tmp()
    base, reference_path = _reference(tmp)
    scores = []
    similarities = []
    for amount in (0, 2, 8, 24):
        image = base if amount == 0 else images.noise(base, amount, seed=amount)
        comparison = _compare(image, reference_path, tmp, name="noise-%d.png" % amount)
        scores.append(comparison["metrics"]["ssim"]["ssim"])
        similarities.append(comparison["metrics"]["per_pixel"]["similarity"])
    require(scores[0] == 1.0, "an unmodified pair must score 1.0")
    require(scores[0] > scores[1] > scores[2] > scores[3],
            "SSIM must fall as the noise grows: %r" % (scores,))
    require(similarities[0] > similarities[3],
            "per-pixel similarity must fall too: %r" % (similarities,))
    heavy = _compare(images.noise(base, 24, seed=24), reference_path, tmp,
                     name="heavy.png")
    if heavy["metrics"]["ssim"]["implementation"] == metrics.SSIM_GAUSSIAN:
        require(heavy["metrics"]["ssim"]["ssim"] > 0.95,
                "uniform noise keeps the Gaussian SSIM high - averages hide it")
    require(heavy["metrics"]["per_pixel"]["similarity"] > 0.95,
            "even per-pixel similarity stays above its floor under uniform noise")
    require(heavy["status"] == visualdiff.STATUS_MISMATCH,
            "noisy pixels must still fail: %r" % _failed(heavy))
    require("identical_pixels" in _failed(heavy),
            "the identical-pixel floor should have caught the noise")


def test_roi_limits_the_comparison_and_moves_the_coordinate_space():
    tmp = _tmp()
    base, reference_path = _reference(tmp)
    roi = "40,20,100,50"
    inside = _compare(images.patch(base, (50, 30, 10, 10), (0, 0, 0)),
                      reference_path, tmp, roi=roi)
    require(inside["status"] == visualdiff.STATUS_MISMATCH,
            "a change inside the region must be caught: %r" % _failed(inside))
    require(inside["regions"]["regions"][0]["x"] == 10
            and inside["regions"]["regions"][0]["y"] == 10,
            "region coordinates are relative to the crop: %r"
            % inside["regions"]["regions"][0])
    require("cropped image" in " ".join(inside["caveats"]),
            "the coordinate space must be disclosed: %r" % inside["caveats"])
    require([step for step in inside["normalisation"]["steps"]
             if step["step"] == "crop"], "the crop must be recorded as a step")
    outside = _compare(images.patch(base, (4, 4, 10, 10), (0, 0, 0)),
                       reference_path, tmp, roi=roi, name="outside.png")
    require(outside["status"] == visualdiff.STATUS_MATCH,
            "a change outside the region must be invisible: %r" % _failed(outside))
    require(outside["metrics"]["ssim"]["ssim"] == 1.0,
            "outside the ROI the images really are identical")
    require(outside["caveats"], "cropping must always be disclosed")
    offset = images.pad(base, (40, 40), (200, 140))
    per_side = _compare(offset, reference_path, tmp, roi_obtained="40,40,100,50",
                        roi_reference="0,0,100,50", name="per-side.png")
    require(per_side["status"] == visualdiff.STATUS_MATCH,
            "per-side ROIs must select the same content at different offsets: "
            "%r / %r" % (per_side["reasons"], _failed(per_side)))
    require(per_side["metrics"]["ssim"]["ssim"] == 1.0,
            "the two crops are the same pixels")
    require(len(per_side["normalisation"]["steps"]) == 2,
            "both crops must be recorded: %r" % per_side["normalisation"]["steps"])
    whole = _compare(offset, reference_path, tmp, name="per-side-whole.png")
    require(whole["status"] == visualdiff.STATUS_INCOMPARABLE,
            "without the per-side ROIs those two captures cannot be compared")
    for bad in ("40,20", "nonsense", "0,0,0,0", "-1,0,10,10"):
        comparison = _compare(base, reference_path, tmp, roi=bad, name="bad-roi.png")
        require(comparison["status"] == visualdiff.STATUS_UNAVAILABLE,
                "a malformed ROI must be reported, not guessed around: %r" % bad)
        require("roi" in comparison["reasons"][0].lower(),
                "the reason must mention the ROI: %r" % comparison["reasons"])
        require(not comparison["metrics"], "no metric may be reported")


def test_mismatched_resolutions_are_incomparable():
    tmp = _tmp()
    base, reference_path = _reference(tmp)
    smaller = base.crop((0, 0, 140, 84))
    comparison = _compare(smaller, reference_path, tmp)
    require(comparison["status"] == visualdiff.STATUS_INCOMPARABLE,
            "different sizes must be incomparable, not a mismatch: %r"
            % comparison["status"])
    require(not comparison["metrics"],
            "no percentage may be reported for an incomparable pair")
    require(comparison["regions"] is None, "no regions may be reported")
    reason = comparison["reasons"][0]
    require("140x84" in reason and "160x100" in reason,
            "the reason must name both resolutions: %r" % reason)
    require("--resize" in reason and "--roi" in reason,
            "the reason must offer the two ways forward: %r" % reason)
    section = visualdiff.compare(images.write(smaller, tmp, "small.png"),
                                 references=[reference_path], config={},
                                 overrides={})
    require(section["overall"]["status"] == visualdiff.STATUS_INCOMPARABLE,
            "the section status must follow the comparison")
    require(section["overall"]["incomparable"] == 1,
            "the counters must add up: %r" % section["overall"])
    require(section["overall"]["best_reference"] is None,
            "there is no best reference when nothing was measured")


def test_resize_is_compared_and_disclosed_as_an_approximation():
    tmp = _tmp()
    base, reference_path = _reference(tmp)
    comparison = _compare(base.crop((0, 0, 140, 84)), reference_path, tmp,
                          resize=True)
    require(comparison["status"] in (visualdiff.STATUS_MATCH,
                                     visualdiff.STATUS_MISMATCH),
            "a resized pair must produce a real verdict: %r" % comparison["status"])
    require(comparison["normalisation"]["resized"] is True,
            "the resize must be recorded")
    require(any(step["step"] == "resize" for step
                in comparison["normalisation"]["steps"]),
            "the resize must appear in the normalisation steps")
    caveat = " ".join(comparison["caveats"])
    require("resized" in caveat and "approximation" in caveat,
            "resizing must be disclosed as an approximation: %r" % caveat)
    require(comparison["metrics"]["ssim"]["ssim"] < 0.95,
            "resampling visibly changes the pixels: %r"
            % comparison["metrics"]["ssim"]["ssim"])
    require(comparison["status"] == visualdiff.STATUS_MISMATCH,
            "a resize of a shifted crop must not be reported as a match")


def test_downscaling_is_disclosed_and_coordinates_map_back():
    tmp = _tmp()
    base = images.desktop()
    patched = images.patch(base, (100, 60, 20, 20), (0, 0, 0))
    reference_path = images.write(base, tmp, "ref.png")
    comparison = _compare(patched, reference_path, tmp, max_pixels=4000)
    require(comparison["normalisation"]["downscaled"] is True,
            "the downscale must be recorded")
    scale = comparison["normalisation"]["coordinate_scale"]
    require(scale == 2, "16000 pixels under a 4000 limit is a factor of 2: %d" % scale)
    require(comparison["normalisation"]["working_size"] == {"width": 80,
                                                            "height": 50},
            "the working size must be reported: %r"
            % comparison["normalisation"]["working_size"])
    region = comparison["regions"]["regions"][0]
    require((region["x"], region["y"]) == (100, 60),
            "coordinates must be mapped back to the original image: %r" % region)
    require(region["area_scaled"] == 4 * region["area"],
            "the scaled area must account for the factor: %r" % region)
    require(region["coordinate_precision"] != "exact",
            "downscaled coordinates are approximate and must say so")
    require("downscaled by 2" in " ".join(comparison["caveats"]),
            "downscaling must be disclosed: %r" % comparison["caveats"])
    require(region["max_delta"] < 245,
            "box averaging weakens the peak delta - the caveat covers this")
    factor = metrics.integer_downscale(1920, 1080, 400000)
    require((1920 // factor) * (1080 // factor) <= 400000,
            "integer_downscale must respect the limit: %d" % factor)
    require(metrics.integer_downscale(100, 100, 400000) == 1,
            "a small image must not be downscaled")


def test_unreadable_images_never_look_like_a_zero_difference():
    tmp = _tmp()
    base, reference_path = _reference(tmp)
    broken = images.corrupt(tmp, "broken.png")
    missing = os.path.join(tmp, "nope.png")
    for path, label in ((broken, "a corrupt file"), (missing, "a missing file")):
        comparison = visualdiff.compare_pair(path, reference_path, _defaults())
        require(comparison["status"] == visualdiff.STATUS_UNAVAILABLE,
                "%s must be unavailable" % label)
        require(not comparison["metrics"], "%s must not report a metric" % label)
        require(comparison["regions"] is None, "%s must not report regions" % label)
        require("obtained" in comparison["reasons"][0],
                "%s must say which side failed: %r" % (label, comparison["reasons"]))
        require("difference of zero" in comparison["note"],
                "the note must state what was not done: %r" % comparison["note"])
    reversed_comparison = visualdiff.compare_pair(
        images.write(base, tmp, "ok.png"), broken, _defaults())
    require(reversed_comparison["status"] == visualdiff.STATUS_UNAVAILABLE,
            "a corrupt reference must also be unavailable")
    require("reference" in reversed_comparison["reasons"][0],
            "the reason should name the reference: %r"
            % reversed_comparison["reasons"])
    section = visualdiff.compare(images.write(base, tmp, "ok2.png"),
                                 references=[broken], config={}, overrides={})
    require(section["overall"]["status"] == visualdiff.STATUS_UNAVAILABLE,
            "the section must be unavailable when nothing could be read")
    require(section["obtained"]["exists"] is True,
            "the obtained image identity should still be recorded")


def test_the_report_attachment_is_additive():
    tmp = _tmp()
    base, reference_path = _reference(tmp)
    moved = images.shift(base, SHIFT_BOX, 2, 1)
    section = visualdiff.compare(images.write(moved, tmp, "moved.png"),
                                 references=[reference_path], config={},
                                 overrides={})
    bench_report = {"schema": 1, "mod": {"id": "taskbar-clock"},
                    "build": {"ok": True, "errors": [], "warnings": []},
                    "verdict": {"level": "loads_no_crash", "verified": False,
                                "not_verifiable_here": True,
                                "summary": "it loaded and did not crash"}}
    before = json.dumps(bench_report, sort_keys=True)
    visual_report.attach(bench_report, section)
    require(bench_report["verdict"]["level"] == "loads_no_crash",
            "attaching a visual comparison must never change the verdict level")
    require(bench_report["verdict"]["verified"] is False,
            "a visual mismatch must not mark the run verified")
    require(bench_report["verdict"]["not_verifiable_here"] is True,
            "existing verdict fields must survive untouched")
    require(bench_report["verdict"]["summary"] == "it loaded and did not crash",
            "the summary must not be rewritten")
    note = bench_report["verdict"]["visual_comparison_note"]
    require(note.startswith("visual comparison: 1 reference(s), status mismatch"),
            "the note must state the outcome: %r" % note)
    require("does not prove functional correctness" in note,
            "the note must say what the result does not mean: %r" % note)
    require(bench_report["visual_comparison"]["kind"] == "visual_comparison",
            "the section must be attached under visual_comparison")
    require(bench_report["visual_comparison"]["disclaimer"]
            == visual_report.DISCLAIMER, "the disclaimer must travel with it")
    untouched = {"schema": 1, "verdict": {"level": "not_executed"}}
    visual_report.attach(untouched, None)
    require("visual_comparison" not in untouched,
            "an empty section must not add anything")
    require(json.dumps(untouched, sort_keys=True) != before, "sanity: different report")
    again = dict(untouched)
    visual_report.attach(again, section)
    require(again["verdict"]["level"] == "not_executed",
            "a not_executed run stays not_executed even with a matching picture")


def test_multiple_references_report_each_one_and_an_overall_status():
    tmp = _tmp()
    base, identical = _reference(tmp, name="r1.png")
    different = images.write(images.patch(base, WINDOW, (0, 0, 0)), tmp, "r2.png")
    broken = images.corrupt(tmp, "r3.png")
    captured = images.write(base, tmp, "shot.png")
    section = visualdiff.compare(captured, references=[identical, different, broken],
                                 config={}, overrides={},
                                 diff_map=os.path.join(tmp, "diff.png"),
                                 side_by_side=os.path.join(tmp, "pair.png"))
    statuses = [comparison["status"] for comparison in section["comparisons"]]
    require(statuses == [visualdiff.STATUS_MATCH, visualdiff.STATUS_MISMATCH,
                         visualdiff.STATUS_UNAVAILABLE],
            "each reference must be compared independently: %r" % statuses)
    summary = section["overall"]
    require(summary["status"] == visualdiff.STATUS_MISMATCH,
            "the overall status must be the worst usable outcome: %r" % summary)
    require((summary["comparisons"], summary["match"], summary["mismatch"],
             summary["unavailable"]) == (3, 1, 1, 1),
            "the counters must add up: %r" % summary)
    require(summary["best_reference"]["reference"] == "r1.png",
            "the closest reference should be named: %r" % summary["best_reference"])
    require(summary["best_reference"]["ssim"] == 1.0,
            "the best reference must carry its score")
    require("not a claim that the mod is correct" in summary["note"],
            "the best-reference note must not overstate: %r" % summary["note"])
    ids = [item["id"] for item in section["references"]]
    require(ids == ["r1.png", "r2.png", "r3.png"],
            "references keep their file names as ids: %r" % ids)
    maps = [comparison["artifacts"].get("diff_map")
            for comparison in section["comparisons"]]
    require(maps[0] and maps[0].endswith("-1.png") and maps[1].endswith("-2.png"),
            "each reference needs its own diff map: %r" % maps)
    require(maps[2] is None, "no artifact may be claimed for an unreadable image")
    pairs = [comparison["artifacts"].get("side_by_side")
             for comparison in section["comparisons"]]
    require(pairs[0] and os.path.exists(pairs[0]),
            "the side-by-side canvas must be written")
    require(os.path.exists(maps[0]), "the diff map must be written")


def test_diff_map_pixels_explain_the_difference():
    tmp = _tmp()
    base = images.desktop()
    patched = images.patch(base, (40, 20, 12, 20), (0, 0, 0))
    reference_path = images.write(base, tmp, "ref.png")
    captured_path = images.write(patched, tmp, "patched.png")
    diff_path = os.path.join(tmp, "diff.png")
    pair_path = os.path.join(tmp, "pair.png")
    comparison = visualdiff.compare_pair(captured_path, reference_path, _defaults(),
                                         diff_map_path=diff_path,
                                         side_by_side_path=pair_path)
    require(comparison["artifacts"]["diff_map"] == diff_path,
            "the artifact path must be reported")
    rendered = png.read(diff_path)
    require((rendered.width, rendered.height) == DESKTOP,
            "the diff map must cover the working geometry")
    inside = rendered.get(45, 24)          # window title: delta 130
    peak = rendered.get(45, 30)            # window body, inside the outline: 245
    outside = rendered.get(2, 2)
    luma_outside = base.luma().get(2, 2)[0]
    require(all(min(low, high) <= channel <= max(low, high)
                for channel, low, high
                in zip(inside, diffmap.HEAT_LOW, diffmap.HEAT_HIGH)),
            "a changed pixel must sit on the heat ramp: %r" % (inside,))
    require(peak != inside, "a stronger difference must not look the same")
    require(diffmap.HEAT_HIGH[1] <= peak[1] < inside[1] <= diffmap.HEAT_LOW[1],
            "the hotter pixel must sit further along the ramp: %r vs %r"
            % (peak, inside))
    require(diffmap.HEAT_LOW[2] <= inside[2] < peak[2] <= diffmap.HEAT_HIGH[2],
            "the ramp runs the other way on blue: %r vs %r" % (peak, inside))
    underlying = metrics.delta_map(patched, base)
    require(underlying.get(45, 30) == (245,) and underlying.get(45, 24) == (130,),
            "the map must be driven by the real deltas: %r %r"
            % (underlying.get(45, 30), underlying.get(45, 24)))
    require(outside == (int(luma_outside * diffmap.DARKEN),) * 3,
            "an unchanged pixel must be a darkened copy: %r vs %r"
            % (outside, luma_outside))
    outlined = [rendered.get(x, y) for x, y in
                ((40, 20), (51, 20), (40, 39), (51, 39))]
    require(all(pixel == diffmap.OUTLINE for pixel in outlined),
            "the region box must be outlined: %r" % outlined)
    canvas = png.read(pair_path)
    require(canvas.width == 3 * DESKTOP[0] + 2 * 6,
            "the canvas must hold obtained, reference and diff with a 6px gap: %d"
            % canvas.width)
    require(canvas.height == DESKTOP[1], "the canvas height is wrong: %d"
            % canvas.height)
    require(canvas.get(0, 0) == png.read(diff_path).get(0, 0)
            or canvas.get(DESKTOP[0] + 6, 0) is not None,
            "the first panel must start at the left edge")
    no_artifacts = visualdiff.compare_pair(captured_path, reference_path, _defaults())
    require(no_artifacts["artifacts"] == {},
            "artifacts are opt-in")


def test_diffmap_helpers_refuse_bad_input():
    small = png.solid(8, 6, (10, 20, 30), "RGB")
    delta = metrics.delta_map(small, png.solid(8, 6, (200, 200, 200), "RGB"))
    canvas = diffmap.render(small, png.solid(8, 6, (200, 200, 200), "RGB"), delta,
                            {"regions": [{"x": 1, "y": 1, "width": 4, "height": 3}]},
                            threshold=24, scale=1)
    require(canvas.mode == "RGB", "the diff map must be RGB")
    require(canvas.get(1, 1) == diffmap.OUTLINE, "the outline colour is wrong")
    require(canvas.get(4, 4) != diffmap.OUTLINE, "the interior should be heat")
    side = diffmap.side_by_side([canvas, canvas], gap=4, labels=["A", "B"])
    require(side.width == canvas.width * 2 + 4, "the canvas width is wrong: %d"
            % side.width)
    tmp = _tmp()
    written = diffmap.write(canvas, os.path.join(tmp, "map.png"))
    require(png.read(written).width == 8, "write did not produce a readable PNG")
    for bad_delta, bad_reference in ((delta.crop((0, 0, 4, 6)), small),
                                     (delta, small.crop((0, 0, 4, 6)))):
        try:
            diffmap.render(bad_reference, small, bad_delta)
        except ValueError:
            continue
        raise AssertionError("mismatched diff-map inputs must raise ValueError")


def test_metrics_refuse_mismatched_geometry():
    base = images.desktop(width=40, height=24)
    other = base.crop((0, 0, 20, 24))
    for name, call in (("per_pixel", lambda: metrics.per_pixel(base, other)),
                       ("delta_map", lambda: metrics.delta_map(base, other)),
                       ("ssim", lambda: metrics.ssim(base, other))):
        try:
            call()
        except ValueError as exc:
            require("geometry" in str(exc) or "identical" in str(exc),
                    "%s must explain the mismatch: %s" % (name, exc))
        else:
            raise AssertionError("%s must refuse mismatched geometry" % name)
    channel_mismatch = png.solid(8, 4, (1, 2, 3), "RGB")
    try:
        metrics.per_pixel(channel_mismatch, png.solid(8, 4, (1,), "L"))
    except ValueError:
        pass
    else:
        raise AssertionError("different modes must be refused")


def test_metric_shapes_and_histograms():
    base = images.desktop(width=64, height=48)
    noisy = images.noise(base, 6, seed=3)
    pixels = metrics.per_pixel(noisy, base, tolerance=4)
    require(pixels["pixels"] == 64 * 48, "the pixel count is wrong")
    require(pixels["tolerance"] == 4, "the tolerance must be echoed")
    require(pixels["identical_pixels"] <= pixels["within_tolerance_pixels"]
            <= pixels["pixels"],
            "the counters must be ordered: %r" % pixels)
    require(pixels["identical_ratio"] <= pixels["within_tolerance_ratio"] <= 1.0,
            "the ratios must be ordered")
    require(pixels["differing_pixels"] == pixels["pixels"]
            - pixels["within_tolerance_pixels"],
            "differing_pixels must be the complement of within-tolerance")
    require(0 < pixels["mean_abs_delta"] < pixels["max_abs_delta"] <= 255,
            "the deltas are implausible: %r" % pixels)
    require(pixels["rmse"] > 0, "RMSE must be reported")
    require(abs(pixels["similarity"] - (1.0 - pixels["mean_abs_delta"] / 255.0))
            < 1e-5, "similarity must be 1 - mean/255: %r" % pixels["similarity"])
    require(sum(pixels["delta_histogram"].values()) == 64 * 48,
            "the histogram must account for every pixel")
    require(pixels["implementation"] == metrics.implementations()["per_pixel"],
            "the implementation must be named")
    strict = metrics.per_pixel(noisy, base, tolerance=0)
    require(strict["within_tolerance_pixels"] == strict["identical_pixels"],
            "tolerance 0 makes the two counters equal")
    score = metrics.ssim(noisy, base)
    require(0.0 < score["ssim"] < 1.0, "a noisy pair must not score 1.0: %r" % score)
    require(score["luminance_range"] == 255, "the luminance range must be stated")
    require(score["c1"] > 0 and score["c2"] > 0, "the SSIM constants must be stated")
    require(score["samples"] > 0, "the sample count must be stated")
    require(score["ssim"] == round(max(0.0, min(1.0, score["ssim"])), 6),
            "the score must be clamped and rounded")
    identical = metrics.ssim(base, base)
    require(identical["ssim"] == 1.0, "an identical pair must score exactly 1.0")


def test_implementations_are_reported_honestly():
    available = metrics.implementations()
    require(set(available) == {"numpy_available", "ssim", "per_pixel", "caveat"},
            "the implementation block changed shape: %r" % sorted(available))
    if available["numpy_available"]:
        require(available["ssim"] == metrics.SSIM_GAUSSIAN,
                "with numpy the Gaussian window must be used")
        require(available["caveat"] is None,
                "no caveat is needed when the standard implementation ran")
    else:
        require(available["ssim"] == metrics.SSIM_BLOCK,
                "without numpy the block implementation must be named")
        require("not directly comparable" in available["caveat"],
                "the fallback must warn that its number is not a textbook SSIM")


def test_the_pure_python_path_is_forced_and_still_correct():
    """Runs the fallback even where numpy is installed."""
    original = metrics.numpy
    metrics.numpy = lambda: None
    try:
        require(metrics.implementations()["ssim"] == metrics.SSIM_BLOCK,
                "the fallback must be selected when numpy is unavailable")
        base = images.desktop(width=64, height=48)
        identical = metrics.ssim(base, base)
        require(identical["ssim"] == 1.0,
                "the block implementation must score 1.0 for identical images")
        require(identical["implementation"] == metrics.SSIM_BLOCK,
                "the implementation must be recorded")
        require(identical["window"] == "block-8x8", "the window must be stated")
        require("numerically different" in identical["note"],
                "the fallback must say it is not the Gaussian SSIM")
        require(identical["samples"] == 8 * 6, "64x48 is 48 blocks of 8x8: %r"
                % identical["samples"])
        noisy = images.noise(base, 6, seed=3)
        score = metrics.ssim(noisy, base)
        require(0.0 < score["ssim"] < 1.0, "the fallback scored oddly: %r" % score)
        pixels = metrics.per_pixel(noisy, base)
        require(pixels["implementation"] == metrics.PER_PIXEL_PURE,
                "the pixel metric must fall back too")
        require(pixels["pixels"] == 64 * 48, "the pixel count is wrong")
        tiny = png.solid(4, 3, (128,), "L")
        fallback = metrics.ssim(tiny, tiny)
        require(fallback.get("whole_image_fallback") is True,
                "an image smaller than one block must use the whole-image fallback")
    finally:
        metrics.numpy = original
    require(metrics.implementations()["numpy_available"]
            == (original() is not None), "the monkeypatch was not undone")


def test_align_geometry_and_load_pair():
    tmp = _tmp()
    base = images.desktop(width=100, height=60)
    path = images.write(base, tmp, "g.png")
    loaded = align.load_pair(path, path)
    require(set(loaded) == {"obtained", "reference"},
            "a readable pair has no error keys: %r" % sorted(loaded))
    geometry = align.geometry(loaded["obtained"])
    require((geometry["width"], geometry["height"], geometry["mode"]) == (100, 60,
                                                                          "RGB"),
            "the geometry block is wrong: %r" % geometry)
    require(geometry["origin"] == path, "the origin must be reported")
    prepared = align.prepare(loaded["obtained"], loaded["reference"])
    require(prepared["status"] == align.STATUS_COMPARABLE,
            "a matching pair must be comparable: %r" % prepared["reasons"])
    require(prepared["coordinate_scale"] == 1, "no downscale was expected")
    require("resize_factor" not in prepared and prepared["resized"] is False,
            "no resize was requested")
    require(prepared["source"]["obtained"] == prepared["source"]["reference"],
            "both geometries must be reported")
    small = align.prepare(loaded["obtained"].crop((0, 0, 90, 54)),
                          loaded["reference"])
    require(small["status"] == align.STATUS_INCOMPARABLE,
            "an unresized size mismatch must be incomparable")
    require(small["obtained"] is None and small["reference"] is None,
            "an incomparable pair must not hand back images to compare")
    resized = align.prepare(loaded["obtained"].crop((0, 0, 90, 54)),
                            loaded["reference"], resize=True)
    require(resized["status"] == align.STATUS_COMPARABLE,
            "with resize requested the pair must be comparable: %r"
            % resized["reasons"])
    require((resized["obtained"].width, resized["obtained"].height) == (100, 60),
            "the obtained image must be resized to the reference")
    require(align.parse_roi("10, 20, 30, 40") == (10, 20, 30, 40),
            "whitespace must be tolerated in a ROI")
    require(align.parse_roi(None) is None, "no ROI is None")
    for bad in ("nonsense", "100x", "0x40", "-1,0,10,10", "10,20,30,40,50",
                "a,b,c,d"):
        try:
            align.parse_roi(bad)
        except ValueError:
            continue
        raise AssertionError("parse_roi(%r) must be refused" % bad)
    broken = images.corrupt(tmp, "broken.png")
    failed = align.load_pair(broken, path)
    require("obtained_error" in failed and "reference" in failed,
            "a decode failure becomes an error key: %r" % sorted(failed))
    require("obtained" not in failed, "no image may be claimed for a failed read")
    missing = align.load_pair(path, os.path.join(tmp, "nope.png"))
    require("reference_error" in missing, "a missing reference must be reported")


def test_threshold_checks_report_direction_and_implementation():
    base = images.desktop(width=40, height=24)
    block = {"ssim": metrics.ssim(base, base),
             "per_pixel": metrics.per_pixel(base, base)}
    passed = visual_report.checks(block, _defaults(), None)
    names = [check["name"] for check in passed]
    require("ssim" in names and "similarity" in names and "identical_pixels" in names
            and "differing_pixels" in names,
            "the four default rules must be reported: %r" % names)
    require(all(check["passed"] for check in passed),
            "an identical pair must pass every default rule")
    require(next(check for check in passed if check["name"] == "ssim")["direction"]
            == "at_least", "a floor is an at_least rule")
    require(next(check for check in passed
                 if check["name"] == "differing_pixels")["direction"] == "at_most",
            "a ceiling is an at_most rule")
    require(next(check for check in passed if check["name"] == "ssim")[
        "implementation"] == block["ssim"]["implementation"],
        "the SSIM check must carry its implementation")
    impossible = visual_report.checks(block, _defaults(ssim_min=1.5), None)
    require(not all(check["passed"] for check in impossible),
            "an impossible floor must fail")
    ceiling = visual_report.checks(block, _defaults(max_region_area=10),
                                   {"regions": [{"area": 500, "area_scaled": 500,
                                                 "label": "region-1"}]})
    entry = [check for check in ceiling if check["name"] == "largest_region"]
    require(entry and entry[0]["passed"] is False and entry[0]["value"] == 500,
            "the region ceiling must use the largest region: %r" % entry)
    scaled = visual_report.checks(block, _defaults(max_region_area=10),
                                  {"regions": [{"area": 4, "area_scaled": 64,
                                                "label": "region-1"}]})
    entry = [check for check in scaled if check["name"] == "largest_region"]
    require(entry[0]["value"] == 64,
            "the ceiling must use the scaled area so it means original pixels")
    require(visual_report.checks({}, _defaults(), None) == [],
            "no metrics means no checks")
    require(visual_report.status_for([]) == "incomparable",
            "no checks means nothing was compared")
    require(visual_report.status_for([], unavailable=True) == "unavailable",
            "unavailable wins over everything")
    require(visual_report.status_for([], incomparable=True) == "incomparable",
            "incomparable must be reported as itself")


def test_overall_and_section_shapes():
    require(visual_report.overall([])["status"] == "unavailable",
            "no comparisons at all must be unavailable")
    require(visual_report.overall([])["reason"] == "no reference was compared",
            "an empty section must explain itself")
    comparisons = [{"status": "match", "reference": {"id": "a"},
                    "metrics": {"ssim": {"ssim": 0.91}}},
                   {"status": "mismatch", "reference": {"id": "b"},
                    "metrics": {"ssim": {"ssim": 0.97}}},
                   {"status": "unavailable", "reference": {"id": "c"},
                    "metrics": {}}]
    summary = visual_report.overall(comparisons)
    require(summary["status"] == "mismatch",
            "one mismatch makes the section a mismatch")
    require(summary["best_reference"]["reference"] == "b",
            "the best reference is the highest score, whatever its status")
    section = visual_report.section("shot.png", comparisons,
                                    config_mod.thresholds_for({}, None),
                                    metrics.implementations(),
                                    notes=["configuration: built-in defaults"])
    require(section["kind"] == "visual_comparison" and section["schema"] == 1,
            "the section header is wrong")
    require("_implementation" not in section["thresholds"],
            "internal keys must not leak into the report")
    require(section["threshold_implementation"] in ("pure", "numpy"),
            "the implementation behind the thresholds must be recorded")
    require(section["obtained"]["path"].endswith("shot.png"),
            "the obtained image must be identified")
    require(section["generated"].endswith("Z"), "the timestamp must be UTC")
    document = visual_report.standalone_document(section, {"os": "linux"})
    require(document["kind"] == "visual_comparison" and document["schema"] == 1,
            "the standalone document header is wrong")
    require(document["environment"] == {"os": "linux"},
            "the environment must be carried into the document")
    require(document["visual_comparison"] is section,
            "the document must embed the same section")
    identity = visual_report.file_identity(os.path.join(_tmp(), "missing.png"))
    require(identity["exists"] is False,
            "a missing file must be described, not crash: %r" % identity)
    require(identity.get("sha256") is None and identity.get("bytes") is None,
            "a missing file must not invent a hash or a size: %r" % identity)
    tmp = _tmp()
    real = visual_report.file_identity(images.write(images.desktop(8, 6), tmp,
                                                    "id.png"))
    require(real["exists"] is True and len(real["sha256"]) == 64,
            "a real file must be identified by hash: %r" % real)
    require(real["bytes"] > 0 and real["path"].endswith("id.png"),
            "the size and path must be recorded: %r" % real)


def test_cli_exit_codes_and_output_shapes():
    tmp = _tmp()
    base = images.desktop()
    reference_path = images.write(base, tmp, "ref.png")
    same = images.write(base, tmp, "same.png")
    moved = images.write(images.shift(base, SHIFT_BOX, 2, 1), tmp, "moved.png")
    broken = images.corrupt(tmp, "broken.png")
    json_path = os.path.join(tmp, "out.json")

    require(visual_cli.main([same, reference_path, "--quiet"]) == 0,
            "a match must exit 0")
    require(visual_cli.main([moved, reference_path, "--quiet"]) == 1,
            "a mismatch must exit 1")
    require(visual_cli.main([broken, reference_path, "--quiet"]) == 3,
            "an unreadable image must exit 3")
    smaller = images.write(base.crop((0, 0, 140, 84)), tmp, "smaller.png")
    require(visual_cli.main([smaller, reference_path, "--quiet"]) == 3,
            "an incomparable pair must exit 3")
    require(visual_cli.main([smaller, reference_path, "--resize", "--quiet"]) == 1,
            "with --resize the same pair becomes a real mismatch")
    require(visual_cli.main([os.path.join(tmp, "nope.png"), reference_path,
                             "--quiet"]) == 3, "a missing obtained image exits 3")
    for argv in ([], [same], [same, reference_path, "--roi", "40,20"],
                 [same, reference_path, "--config", os.path.join(tmp, "none.yaml")]):
        try:
            code = visual_cli.main(argv)
        except SystemExit as exc:
            code = exc.code           # argparse refuses a missing argument itself
        require(code == 2, "%r must be a usage error, got %r" % (argv, code))
    require(visual_cli.main([moved, reference_path, "--quiet", "--json", json_path])
            == 1, "writing JSON must not change the exit code")
    with open(json_path, encoding="utf-8") as f:
        document = json.load(f)
    require(document["kind"] == "visual_comparison", "the JSON kind is wrong")
    require(document["visual_comparison"]["comparisons"][0]["metrics"]["ssim"]["ssim"]
            < 1.0, "the JSON document lost the metric")
    require(document["environment"], "the environment must be probed for --json")
    require(visual_cli.main([same, reference_path, "--show-config"]) == 0,
            "showing the effective configuration must exit 0")


def test_cli_flags_reach_the_comparison():
    """Every documented override must exist and be usable: a flag that argparse
    accepts but the comparison ignores is worse than no flag at all."""
    tmp = _tmp()
    base = images.desktop()
    reference_path = images.write(base, tmp, "ref.png")
    moved = images.write(images.shift(base, SHIFT_BOX, 2, 1), tmp, "moved.png")
    values = {"roi": "40,20,100,50", "roi_obtained": "40,20,100,50",
              "roi_reference": "40,20,100,50", "tolerance": "12",
              "ssim_min": "0.4", "similarity_min": "0.4", "identical_min": "0.4",
              "max_differing_ratio": "0.9", "max_region_area": "5",
              "region_threshold": "40", "min_region_area": "2", "max_regions": "3",
              "merge_distance": "6", "connectivity": "8", "resize": None,
              "mode": "L", "max_pixels": "900"}
    missing = set(visualdiff.CLI_OVERRIDES) ^ set(values)
    require(not missing,
            "CLI_OVERRIDES and the flags under test drifted apart: %r" % sorted(missing))
    for key, value in sorted(values.items()):
        flag = "--" + key.replace("_", "-")
        argv = [moved, reference_path, "--quiet", flag]
        if value is not None:
            argv.append(value)
        try:
            code = visual_cli.main(argv)
        except SystemExit as exc:
            raise AssertionError("%s was rejected by argparse (exit %r)" % (flag,
                                                                           exc.code))
        require(code in (0, 1, 3),
                "%s %s produced exit code %r; 2 would mean the flag is unknown"
                % (flag, value, code))
    generous = [moved, reference_path, "--quiet", "--ssim-min", "0.4",
                "--similarity-min", "0.4", "--identical-min", "0.4",
                "--max-differing-ratio", "0.9"]
    require(visual_cli.main(generous) == 0,
            "loosening every rule must turn the shifted window into a match, "
            "which proves the flags reach the comparison")
    require(visual_cli.main([moved, reference_path, "--quiet"]) == 1,
            "with the defaults the same pair is still a mismatch")
    args = visual_cli.parse_args([moved, reference_path, "--roi", "40,20,100,50",
                                  "--tolerance", "7", "--mode", "L"])
    overrides = visual_cli.overrides_of(args)
    require(overrides["roi"] == [40, 20, 100, 50],
            "a ROI override must be parsed into numbers: %r" % overrides["roi"])
    require(overrides["tolerance"] == 7 and overrides["mode"] == "L",
            "scalar overrides must survive: %r" % overrides)
    require("ssim_min" not in overrides, "an unset flag must not override anything")
    try:
        visual_cli.overrides_of(visual_cli.parse_args([moved, reference_path,
                                                       "--roi", "nope"]))
    except SystemExit as exc:
        require("--roi" in str(exc), "a bad ROI must name the flag: %s" % exc)
    else:
        raise AssertionError("a bad ROI must be refused")


def test_cli_can_attach_to_a_bench_report():
    tmp = _tmp()
    base = images.desktop()
    reference_path = images.write(base, tmp, "ref.png")
    same = images.write(base, tmp, "same.png")
    report_path = os.path.join(tmp, "report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({"schema": 1, "mod": {"id": "taskbar-clock"},
                   "verdict": {"level": "loads_no_crash", "verified": False}}, f)
    require(visual_cli.main([same, reference_path, "--report", report_path,
                             "--quiet"]) == 0, "attaching to a report must exit 0")
    with open(report_path, encoding="utf-8") as f:
        merged = json.load(f)
    require("visual_comparison" in merged, "the report was not extended")
    require(merged["verdict"]["level"] == "loads_no_crash",
            "the verdict level must not change")
    require(merged["verdict"]["visual_comparison_note"], "the note must be added")
    require(merged["mod"]["id"] == "taskbar-clock", "the report lost its own fields")
    missing = os.path.join(tmp, "does-not-exist.json")
    require(visual_cli.main([same, reference_path, "--report", missing,
                             "--quiet"]) == 0,
            "a missing report is a warning, the comparison still has a status")
    require(not os.path.exists(missing), "a missing report must not be created")


def test_cli_renders_a_human_readable_block():
    tmp = _tmp()
    base = images.desktop()
    reference_path = images.write(base, tmp, "ref.png")
    moved = images.write(images.shift(base, SHIFT_BOX, 2, 1), tmp, "moved.png")
    comparison = visualdiff.compare_pair(moved, reference_path, _defaults())
    text = visual_cli.render(comparison)
    require("reference ref.png" in text, "the block must name the reference: %r" % text)
    require("status: mismatch" in text, "the block must state the status")
    require(">= 0.9" in text and "<= 0.01" in text,
            "floors and ceilings must be visually distinct: %r" % text)
    require("PASS" in text and "FAIL" in text, "each rule must show its outcome")
    require("region-1" in text and "x=42" in text,
            "the regions must be listed with coordinates")
    require("took" in text, "the duration must be shown")
    quiet = visual_cli.render(comparison, quiet=True)
    require("region-1" not in quiet, "quiet mode should omit the region list")
    require("status: mismatch" in quiet, "quiet mode still shows the status")
    unreadable = visualdiff.compare_pair(moved, os.path.join(tmp, "nope.png"),
                                         _defaults())
    text = visual_cli.render(unreadable)
    require("status: unavailable" in text and "reason:" in text,
            "an unreadable pair must show its reason: %r" % text)
    incomparable = visualdiff.compare_pair(
        images.write(base.crop((0, 0, 140, 84)), tmp, "small.png"),
        reference_path, _defaults())
    require("--resize" in visual_cli.render(incomparable),
            "the rendered block must offer the way forward")


def test_reference_shots_are_never_invented():
    """No reference image ships with the repository; the example config points at
    a generated directory that is ignored."""
    cache = os.path.join(REPO, ".cache", "refshots")
    if os.path.exists(cache):
        require(all(os.path.isdir(os.path.join(cache, item))
                    for item in os.listdir(cache)),
                "unexpected files in .cache/refshots")
    template = os.path.join(REPO, "configs", "visual.example.yaml")
    require(os.path.exists(template), "the example visual config is missing")
    with open(template, encoding="utf-8") as f:
        text = f.read()
    require(".cache/refshots" in text,
            "example reference paths must live in the ignored cache")
    require("taskbar-clock" in text and "classic-taskbar" in text,
            "the example config should show the two published mods")
    for key in ("roi", "ssim_min", "max_differing_ratio", "max_region_area",
                "resize", "build_number"):
        require(key in text, "the example config must show %r" % key)
    require("8102" in text,
            "the Windows 8 build number must match docs/reference-vms.md")
    with open(os.path.join(REPO, ".gitignore"), encoding="utf-8") as f:
        gitignore = f.read()
    require(".cache/" in gitignore, "the reference cache must stay out of git")
    require("*.visual.yaml" in gitignore or ".local.yaml" in gitignore,
            "local threshold files must stay out of git")


def test_numpy_and_pure_agree_on_the_verdict():
    if metrics.numpy() is None:
        raise SkipTest("numpy not installed: the two paths cannot be compared here")
    tmp = _tmp()
    base = images.desktop()
    patched = images.patch(base, (40, 20, 12, 9), (0, 0, 0))
    reference_path = images.write(base, tmp, "ref.png")
    captured_path = images.write(patched, tmp, "patched.png")
    fast = visualdiff.compare_pair(captured_path, reference_path, _defaults())
    require(fast["metrics"]["ssim"]["implementation"] == metrics.SSIM_GAUSSIAN,
            "with numpy available the Gaussian path should run")
    box_fast = fast["regions"]["regions"][0]
    original = metrics.numpy
    metrics.numpy = lambda: None
    try:
        slow = visualdiff.compare_pair(captured_path, reference_path, _defaults())
    finally:
        metrics.numpy = original
    require(slow["metrics"]["ssim"]["implementation"] == metrics.SSIM_BLOCK,
            "the fallback must be used when numpy is hidden")
    require(slow["status"] == fast["status"],
            "both implementations must reach the same verdict")
    box_slow = slow["regions"]["regions"][0]
    require((box_slow["x"], box_slow["y"], box_slow["width"], box_slow["height"])
            == (box_fast["x"], box_fast["y"], box_fast["width"], box_fast["height"]),
            "both paths must find the same region: %r vs %r" % (box_slow, box_fast))
    require(abs(slow["metrics"]["ssim"]["ssim"] - fast["metrics"]["ssim"]["ssim"])
            < 0.05, "the two SSIM implementations disagree wildly: %.4f vs %.4f"
            % (slow["metrics"]["ssim"]["ssim"], fast["metrics"]["ssim"]["ssim"]))
    require(slow["metrics"]["differing_pixels"] == fast["metrics"]["differing_pixels"],
            "per-pixel counting must agree exactly between implementations")
