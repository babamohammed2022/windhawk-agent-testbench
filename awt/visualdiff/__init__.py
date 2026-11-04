"""Automatic visual comparison of screenshots (Feature 3).

    python3 -m awt.visualdiff obtained.png reference.png
    python3 -m awt.visualdiff obtained.png ref-a.png ref-b.png --diffmap diff.png
    python3 -m awt.visualdiff obtained.png --mod examples/taskbar-clock.wh.cpp

The point is to give an agent enough information to fix a mod without a human
looking at a picture: a similarity percentage (SSIM *and* per-pixel), a
difference map, and a list of divergent regions with coordinates, area and worst
delta. `docs/visual-diff.md` explains the numbers and their limits; the
disclaimer below travels with every result.

Nothing here needs Pillow or numpy. With numpy the SSIM uses the standard 11x11
Gaussian window; without it, a documented 8x8 block SSIM - and the report says
which one ran, because the two numbers are not interchangeable.
"""
import os
import time

from .. import png as png_mod
from . import align, config as config_mod, diffmap, metrics, regions, report

STATUS_MATCH = "match"
STATUS_MISMATCH = "mismatch"
STATUS_INCOMPARABLE = "incomparable"
STATUS_UNAVAILABLE = "unavailable"

CLI_OVERRIDES = ("roi", "roi_obtained", "roi_reference", "tolerance", "ssim_min",
                 "similarity_min", "identical_min", "max_differing_ratio",
                 "max_region_area", "region_threshold", "min_region_area",
                 "max_regions", "merge_distance", "connectivity", "resize", "mode",
                 "max_pixels")


def compare_pair(obtained_path, reference_path, thresholds, reference=None,
                 diff_map_path=None, side_by_side_path=None):
    """Compare one obtained image with one reference. Never raises for bad input.

    The returned dict is one entry of `visual_comparison.comparisons`; its
    `status` is `match`, `mismatch`, `incomparable` (the two cannot be compared
    as they are) or `unavailable` (a file could not be read at all).
    """
    started = time.time()
    reference = dict(reference or {})
    reference.setdefault("id", os.path.basename(reference_path or "?"))
    reference["path"] = reference_path
    thresholds = dict(thresholds or config_mod.DEFAULTS)

    loaded = align.load_pair(obtained_path, reference_path)
    problems = [value for key, value in loaded.items() if key.endswith("_error")]
    if problems:
        return {
            "reference": reference,
            "status": STATUS_UNAVAILABLE,
            "reasons": problems,
            "notes": [],
            "caveats": [],
            "metrics": {},
            "regions": None,
            "checks": [],
            "normalisation": None,
            "artifacts": {},
            "seconds": round(time.time() - started, 3),
            "note": "nothing was compared; an unreadable image is never presented "
                    "as a difference of zero",
        }

    try:
        prepared = align.prepare(
            loaded["obtained"], loaded["reference"],
            roi=thresholds.get("roi"), roi_obtained=thresholds.get("roi_obtained"),
            roi_reference=thresholds.get("roi_reference"),
            resize=bool(thresholds.get("resize")),
            mode=thresholds.get("mode") or "RGB",
            max_pixels=thresholds.get("max_pixels"))
    except ValueError as exc:
        # a malformed ROI or an unsupported mode is a configuration problem, not
        # something to crash on: report it the same way as an unreadable image
        return {
            "reference": reference,
            "status": STATUS_UNAVAILABLE,
            "reasons": ["the pair could not be prepared for comparison: %s" % exc],
            "notes": [],
            "caveats": [],
            "metrics": {},
            "regions": None,
            "checks": [],
            "normalisation": None,
            "artifacts": {},
            "seconds": round(time.time() - started, 3),
            "note": "nothing was compared; a bad ROI or mode is reported, never "
                    "guessed around",
        }
    reference["geometry"] = prepared["source"]["reference"]
    base = {
        "reference": reference,
        "reasons": prepared["reasons"],
        "notes": prepared["notes"],
        "caveats": prepared["caveats"],
        "normalisation": {"steps": prepared["steps"], "resized": prepared["resized"],
                          "downscaled": prepared["downscaled"],
                          "coordinate_scale": prepared["coordinate_scale"],
                          "working_size": prepared.get("working_size"),
                          "obtained_geometry": prepared["source"]["obtained"],
                          "reference_geometry": prepared["source"]["reference"]},
        "artifacts": {},
        "seconds": None,
    }
    if prepared["status"] != align.STATUS_COMPARABLE:
        base.update({"status": STATUS_INCOMPARABLE, "metrics": {}, "regions": None,
                     "checks": [], "seconds": round(time.time() - started, 3),
                     "note": "no percentage is reported for images that cannot be "
                             "compared as they are"})
        return base

    left, right = prepared["obtained"], prepared["reference"]
    delta = metrics.delta_map(left, right)
    per_pixel = metrics.per_pixel(left, right, thresholds.get("tolerance") or 0)
    ssim_block = metrics.ssim(left, right)
    found = regions.find_regions(
        delta, threshold=thresholds.get("region_threshold"),
        min_area=thresholds.get("min_region_area"),
        max_regions=thresholds.get("max_regions"),
        merge_distance=thresholds.get("merge_distance"),
        connectivity=thresholds.get("connectivity"),
        scale=prepared["coordinate_scale"])
    checks = report.checks({"ssim": ssim_block, "per_pixel": per_pixel},
                           thresholds, regions_block=found)
    status = report.status_for(checks)

    artifacts = {}
    map_image = diffmap.render(left, right, delta, found,
                               threshold=thresholds.get("region_threshold") or 24,
                               scale=prepared["coordinate_scale"])
    if diff_map_path:
        try:
            png_mod.write(diff_map_path, map_image)
            artifacts["diff_map"] = diff_map_path
        except (OSError, png_mod.PngError) as exc:
            base["notes"].append("the difference map could not be written: %s" % exc)
    if side_by_side_path:
        try:
            canvas = diffmap.side_by_side([left, right, map_image],
                                          labels=["OBTAINED", "REFERENCE", "DIFF"])
            png_mod.write(side_by_side_path, canvas)
            artifacts["side_by_side"] = side_by_side_path
        except (OSError, png_mod.PngError, ValueError) as exc:
            base["notes"].append("the side-by-side image could not be written: %s"
                                 % exc)

    base.update({
        "status": status,
        "metrics": {
            "ssim": ssim_block,
            "per_pixel": per_pixel,
            "ssim_percent": round(ssim_block["ssim"] * 100, 3),
            "similarity_percent": round(per_pixel["similarity"] * 100, 3),
            "identical_percent": round((per_pixel["identical_ratio"] or 0) * 100, 3),
            "within_tolerance_percent": round(
                (per_pixel["within_tolerance_ratio"] or 0) * 100, 3),
            "differing_pixels": per_pixel["differing_pixels"],
            "implementation": {"ssim": ssim_block["implementation"],
                               "per_pixel": per_pixel["implementation"]},
        },
        "regions": found,
        "checks": checks,
        "artifacts": artifacts,
        "seconds": round(time.time() - started, 3),
    })
    if ssim_block.get("whole_image_fallback"):
        base["caveats"].append("the images are smaller than one SSIM block, so a "
                              "single whole-image comparison was used")
    return base


def compare(obtained_path, references=None, mod_path=None, mod_id=None,
            config_path=None, overrides=None, diff_map=None, side_by_side=None,
            config=None):
    """Compare one obtained image against a set of references -> the section."""
    overrides = dict(overrides or {})
    if config is None:
        config = config_mod.load(config_path, mod_path=mod_path)
    if mod_id is None and mod_path:
        mod_id = _mod_id_of(mod_path)
    reference_list = [_as_reference(item, index) for index, item
                      in enumerate(references)] if references else \
        config_mod.references_for(config, mod_id)
    if not reference_list:
        return report.section(
            obtained_path, [], config_mod.thresholds_for(config, mod_id),
            metrics.implementations(), references=[],
            notes=["no reference was configured for mod %r and none was given on "
                   "the command line" % mod_id])

    comparisons = []
    for index, reference in enumerate(reference_list):
        thresholds = config_mod.thresholds_for(config, mod_id, reference)
        for key, value in overrides.items():
            if value is not None and key in thresholds:
                thresholds[key] = value
        reference_path = reference.get("path")
        if reference_path and not os.path.isabs(reference_path):
            from .. import env as host_env
            candidate = os.path.join(host_env.REPO, reference_path)
            if os.path.exists(candidate):
                reference_path = candidate
        map_path = _numbered(diff_map, index, len(reference_list))
        pair_path = _numbered(side_by_side, index, len(reference_list))
        comparisons.append(compare_pair(
            obtained_path, reference_path, thresholds, reference=reference,
            diff_map_path=map_path, side_by_side_path=pair_path))
    return report.section(
        obtained_path, comparisons,
        config_mod.thresholds_for(config, mod_id,
                                  reference_list[0] if len(reference_list) == 1
                                  else None),
        metrics.implementations(),
        references=[{"id": r.get("id"), "path": r.get("path"),
                     "build_number": r.get("build_number"),
                     "edition": r.get("edition"), "theme": r.get("theme"),
                     "resolution": r.get("resolution"),
                     "captured_at": r.get("captured_at"),
                     "source": r.get("source")}
                    for r in reference_list],
        notes=["configuration: %s" % config.get("_source")]
              + (["mod id: %s" % mod_id] if mod_id else []))


def _as_reference(item, index):
    """A reference may be given as a path string or as a configuration entry."""
    if isinstance(item, dict):
        reference = dict(item)
        reference.setdefault("id", "reference-%d" % (index + 1))
        return reference
    return {"id": os.path.basename(str(item)) or "reference-%d" % (index + 1),
            "path": str(item)}


def _numbered(path, index, total):
    """`diff.png` -> `diff.png` for one reference, `diff-1.png` … for several."""
    if not path or total <= 1:
        return path
    stem, extension = os.path.splitext(path)
    return "%s-%d%s" % (stem, index + 1, extension or ".png")


def _mod_id_of(mod_path):
    try:
        from .. import metadata as metadata_mod
        with open(mod_path, encoding="utf-8", errors="replace") as f:
            meta = metadata_mod.parse_metadata(f.read())
        return (meta.get("raw") or {}).get("id") or None
    except OSError:
        return None


__all__ = ["CLI_OVERRIDES", "STATUS_INCOMPARABLE", "STATUS_MATCH",
           "STATUS_MISMATCH", "STATUS_UNAVAILABLE", "align", "compare",
           "compare_pair", "config_mod", "diffmap", "metrics", "regions", "report"]
