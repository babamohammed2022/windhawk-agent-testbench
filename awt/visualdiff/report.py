"""The `visual_comparison` report section.

Additive, like every other section this bench grows: attaching it never changes
`verdict.level`, `verified`, `not_verifiable_here` or `requires_real_windows`.
Two screenshots looking alike is not evidence that a mod works, and two looking
different is not evidence that it is broken - the section says that in words, in
the JSON, every time.
"""
import hashlib
import os
import time

DISCLAIMER = (
    "Visual similarity does not prove functional correctness. Two identical "
    "screenshots can come from a mod that did nothing at all, and two different "
    "screenshots can both be correct (a different clock, a different theme, a "
    "different animation frame, a different reference build). This section "
    "compares pixels: it says where an obtained capture differs from a reference "
    "capture and by how much. It is evidence about appearance, never about "
    "behaviour - behaviour is what run/verdict report.")

STATUSES = ("match", "mismatch", "incomparable", "unavailable")
SCHEMA = 1


def file_identity(path):
    """Size and digest of an image, so a comparison can be tied to exact bytes."""
    identity = {"path": path, "exists": bool(path and os.path.exists(path))}
    if not identity["exists"]:
        return identity
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                digest.update(block)
        identity["sha256"] = digest.hexdigest()
        identity["bytes"] = os.path.getsize(path)
        identity["mtime"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(path)))
    except OSError as exc:                       # pragma: no cover
        identity["error"] = str(exc)
    return identity


def checks(metrics_block, thresholds, regions_block=None):
    """Threshold checks: value, threshold, direction, and whether it passed.

    `direction` matters. Similarity floors (`at_least`) are what everybody
    expects, but they hide small sharp changes: a window shifted by two pixels
    still leaves 98% of the image identical. The ceilings (`at_most`) on the
    differing-pixel ratio and on the largest divergent region are what catch
    that, and both are recorded in the report so a reader can see which rule
    decided the status.
    """
    out = []
    ssim_value = ((metrics_block or {}).get("ssim") or {}).get("ssim")
    per_pixel = (metrics_block or {}).get("per_pixel") or {}
    if ssim_value is not None and thresholds.get("ssim_min") is not None:
        out.append({"name": "ssim", "value": ssim_value,
                    "threshold": thresholds["ssim_min"], "units": "0..1",
                    "direction": "at_least",
                    "implementation": (metrics_block["ssim"].get("implementation")
                                       if metrics_block.get("ssim") else None),
                    "passed": ssim_value >= thresholds["ssim_min"]})
    for name, key in (("similarity", "similarity_min"),
                      ("identical_pixels", "identical_min")):
        value = per_pixel.get("similarity" if name == "similarity"
                              else "within_tolerance_ratio")
        if value is not None and thresholds.get(key) is not None:
            out.append({"name": name, "value": value, "threshold": thresholds[key],
                        "units": "0..1", "direction": "at_least",
                        "passed": value >= thresholds[key]})
    differing = per_pixel.get("differing_ratio")
    if differing is not None and thresholds.get("max_differing_ratio") is not None:
        out.append({"name": "differing_pixels", "value": differing,
                    "threshold": thresholds["max_differing_ratio"], "units": "0..1",
                    "direction": "at_most",
                    "passed": differing <= thresholds["max_differing_ratio"]})
    ceiling = thresholds.get("max_region_area")
    listed = ((regions_block or {}).get("regions") or [])
    if ceiling is not None and listed:
        largest = max(region.get("area_scaled") or region.get("area") or 0
                      for region in listed)
        out.append({"name": "largest_region", "value": largest,
                    "threshold": ceiling, "units": "pixels", "direction": "at_most",
                    "passed": largest <= ceiling,
                    "region": (sorted(listed, key=lambda item: -(
                        item.get("area_scaled") or item.get("area") or 0))
                        or [{}])[0].get("label")})
    return out


def status_for(comparison_checks, incomparable=False, unavailable=False):
    if unavailable:
        return "unavailable"
    if incomparable:
        return "incomparable"
    if not comparison_checks:
        return "incomparable"
    return "match" if all(item["passed"] for item in comparison_checks) else "mismatch"


def overall(comparisons):
    """One status for the whole section, plus the best-matching reference."""
    statuses = [c.get("status") for c in comparisons or []]
    if not statuses:
        return {"status": "unavailable", "comparisons": 0, "match": 0,
                "mismatch": 0, "incomparable": 0, "unavailable": 0,
                "best_reference": None,
                "reason": "no reference was compared"}
    counts = dict((name, statuses.count(name)) for name in STATUSES)
    if counts["match"] == len(statuses):
        status = "match"
    elif counts["mismatch"]:
        status = "mismatch"
    elif counts["incomparable"]:
        status = "incomparable"
    else:
        status = "unavailable"
    best = None
    best_value = -1.0
    for comparison in comparisons:
        value = ((comparison.get("metrics") or {}).get("ssim") or {}).get("ssim")
        if value is None:
            continue
        if value > best_value:
            best_value = value
            best = {"reference": (comparison.get("reference") or {}).get("id"),
                    "ssim": value,
                    "status": comparison.get("status")}
    return {"status": status, "comparisons": len(statuses),
            "match": counts["match"], "mismatch": counts["mismatch"],
            "incomparable": counts["incomparable"],
            "unavailable": counts["unavailable"],
            "best_reference": best,
            "note": ("the best-matching reference is the least different one; it is "
                     "not a claim that the mod is correct")}


def section(obtained_path, comparisons, thresholds, implementation, references=None,
            generated=None, notes=None):
    """Assemble the section that goes into the report (or a standalone JSON)."""
    summary = overall(comparisons)
    return {
        "schema": SCHEMA,
        "kind": "visual_comparison",
        "generated": generated or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "disclaimer": DISCLAIMER,
        "obtained": file_identity(obtained_path),
        "references": references or [],
        "implementation": implementation,
        "thresholds": {key: value for key, value in (thresholds or {}).items()
                       if not key.startswith("_")},
        "threshold_implementation": (thresholds or {}).get("_implementation"),
        "comparisons": comparisons,
        "overall": summary,
        "notes": notes or [],
    }


def attach(rep, visual_section):
    """Add `visual_comparison` to a bench report. Purely additive."""
    if not visual_section:
        return rep
    rep["visual_comparison"] = visual_section
    summary = visual_section.get("overall") or {}
    best = summary.get("best_reference") or {}
    note = ("visual comparison: %d reference(s), status %s"
            % (summary.get("comparisons", 0), summary.get("status")))
    if best.get("ssim") is not None:
        note += ("; best SSIM %.4f against %s"
                 % (best["ssim"], best.get("reference") or "?"))
    note += ". Visual similarity does not prove functional correctness."
    rep.setdefault("verdict", {})["visual_comparison_note"] = note
    return rep


def standalone_document(visual_section, environment=None):
    """The `--json` document: the same shape as `awt.symbols --json`."""
    return {"schema": 1, "kind": "visual_comparison",
            "generated": visual_section.get("generated"),
            "environment": environment or {},
            "visual_comparison": visual_section}
