"""`python3 -m awt.visualdiff <obtained.png> <reference.png> [...]`

Exit codes follow the bench's convention: 0 = every comparison matched its
thresholds, 1 = at least one mismatch (a real, computed difference), 3 = nothing
could be compared (unreadable image, resolution mismatch without --resize),
2 = usage error.
"""
import json
import os
import sys

from . import (CLI_OVERRIDES, STATUS_INCOMPARABLE, STATUS_MATCH, STATUS_MISMATCH,
               STATUS_UNAVAILABLE, compare, config as config_mod, metrics, report)


def parse_args(argv):
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m awt.visualdiff",
        description="Compare a screenshot with one or more references: SSIM, "
                    "per-pixel difference, a difference map and the coordinates of "
                    "every divergent region.")
    parser.add_argument("obtained", help="the screenshot to judge")
    parser.add_argument("references", nargs="*",
                        help="reference image(s); with --mod/--config they can come "
                             "from the configuration instead")
    parser.add_argument("--mod", help="mod source, to find <mod>.visual.yaml and its id")
    parser.add_argument("--mod-id", help="mod id to look up in the configuration")
    parser.add_argument("--config", help="thresholds/reference configuration file")
    parser.add_argument("--roi", help="crop both images: x,y,width,height")
    parser.add_argument("--roi-obtained", help="crop only the obtained image")
    parser.add_argument("--roi-reference", help="crop only the reference")
    parser.add_argument("--tolerance", type=int,
                        help="per-channel delta that still counts as identical")
    parser.add_argument("--ssim-min", type=float, help="SSIM floor (0..1)")
    parser.add_argument("--similarity-min", type=float,
                        help="per-pixel similarity floor (0..1)")
    parser.add_argument("--identical-min", type=float,
                        help="fraction of within-tolerance pixels required (0..1)")
    parser.add_argument("--region-threshold", type=int,
                        help="delta above which a pixel joins a divergent region")
    parser.add_argument("--min-region-area", type=int)
    parser.add_argument("--max-regions", type=int)
    parser.add_argument("--max-differing-ratio", type=float,
                        help="ceiling on the fraction of differing pixels (0..1)")
    parser.add_argument("--max-region-area", type=int,
                        help="ceiling on the largest divergent region, in pixels")
    parser.add_argument("--merge-distance", type=int,
                        help="merge region boxes this close to each other")
    parser.add_argument("--connectivity", type=int, choices=[4, 8])
    parser.add_argument("--resize", action="store_true",
                        help="allow a documented box resize when resolutions differ")
    parser.add_argument("--mode", choices=["RGB", "RGBA", "L", "LA"],
                        help="channel layout to compare in (default RGB)")
    parser.add_argument("--max-pixels", type=int,
                        help="working pixel budget before an integer downscale")
    parser.add_argument("--diffmap", help="write the difference map here (PNG)")
    parser.add_argument("--side-by-side", help="write obtained|reference|diff here")
    parser.add_argument("--json", help="write the visual_comparison section here")
    parser.add_argument("--report", help="attach the section to an existing bench "
                                         "report JSON and rewrite it")
    parser.add_argument("--quiet", action="store_true", help="only print the status")
    parser.add_argument("--show-config", action="store_true",
                        help="print the effective configuration and exit")
    return parser.parse_args(argv)


def overrides_of(args):
    out = {}
    for key in CLI_OVERRIDES:
        flag = key.replace("_", "-")
        value = getattr(args, key, None)
        if value is None:
            continue
        if key in ("roi", "roi_obtained", "roi_reference"):
            from . import align
            try:
                value = list(align.parse_roi(value))
            except ValueError as exc:
                raise SystemExit("error: --%s: %s" % (flag, exc))
        out[key] = value
    return out


def render(comparison, quiet=False):
    """The human-readable block for one comparison."""
    lines = []
    reference = comparison.get("reference") or {}
    lines.append("reference %s: %s" % (reference.get("id"), reference.get("path")))
    lines.append("  status: %s" % comparison.get("status"))
    for reason in comparison.get("reasons") or []:
        lines.append("  reason: %s" % reason)
    for check in comparison.get("checks") or []:
        compare_with = "<=" if check.get("direction") == "at_most" else ">="
        value = check["value"]
        lines.append("  %-18s %9s  %s %-8s %s%s"
                     % (check["name"],
                        ("%.4f" % value) if isinstance(value, float) else value,
                        compare_with, check["threshold"],
                        "PASS" if check["passed"] else "FAIL",
                        "" if not check.get("implementation")
                        else "   (%s)" % check["implementation"]))
    block = comparison.get("metrics") or {}
    per_pixel = block.get("per_pixel") or {}
    if per_pixel:
        lines.append("  pixels: %d identical %s%%, within tolerance %s%%, "
                     "differing %d; mean |delta| %s, max %s, RMSE %s"
                     % (per_pixel.get("pixels") or 0,
                        block.get("identical_percent"),
                        block.get("within_tolerance_percent"),
                        per_pixel.get("differing_pixels") or 0,
                        per_pixel.get("mean_abs_delta"),
                        per_pixel.get("max_abs_delta"), per_pixel.get("rmse")))
    found = comparison.get("regions") or {}
    if found:
        lines.append("  regions: %d above delta %s (%d px of mask, %d small "
                     "component(s) dropped)%s"
                     % (found.get("total_regions") or 0,
                        (found.get("options") or {}).get("region_threshold")
                        or (found.get("options") or {}).get("threshold"),
                        found.get("mask_pixels") or 0,
                        found.get("dropped_small_components") or 0,
                        "; list truncated to %d" % found["reported_regions"]
                        if found.get("truncated") else ""))
        if not quiet:
            for region in (found.get("regions") or [])[:12]:
                lines.append("    %-9s x=%-5d y=%-5d w=%-4d h=%-4d area=%-6d "
                             "max=%-3d mean=%-7s centroid=%s"
                             % (region["label"], region["x"], region["y"],
                                region["width"], region["height"], region["area"],
                                region["max_delta"], region["mean_delta"],
                                region["centroid"]))
            if len(found.get("regions") or []) > 12:
                lines.append("    ... %d more (see --json)"
                             % (len(found["regions"]) - 12))
    for note in comparison.get("caveats") or []:
        lines.append("  ! %s" % note)
    for note in comparison.get("notes") or []:
        lines.append("  . %s" % note)
    for name, path in (comparison.get("artifacts") or {}).items():
        lines.append("  %s: %s" % (name.replace("_", " "), path))
    lines.append("  took %.2fs" % (comparison.get("seconds") or 0.0))
    return "\n".join(lines)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        overrides = overrides_of(args)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        config = config_mod.load(args.config, mod_path=args.mod)
    except config_mod.ConfigError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.show_config:
        print(json.dumps(config_mod.describe(config, args.mod_id
                                             or _mod_id(args.mod)),
                         indent=2, sort_keys=True, default=str))
        return 0

    references = list(args.references)
    if not references:
        references = config_mod.references_for(config, args.mod_id or _mod_id(args.mod))
    if not references:
        print("error: no reference image was given and none is configured for this "
              "mod (see configs/visual.example.yaml)", file=sys.stderr)
        return 2
    if not os.path.exists(args.obtained):
        print("error: obtained image not found: %s" % args.obtained, file=sys.stderr)
        return 3

    section = compare(args.obtained, references=references, mod_path=args.mod,
                      mod_id=args.mod_id, config=config, overrides=overrides,
                      diff_map=args.diffmap, side_by_side=args.side_by_side)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report.standalone_document(section, _environment()), f,
                      indent=2, sort_keys=True)
        print("json: %s" % args.json, file=sys.stderr)
    if args.report:
        _attach_to_report(args.report, section)

    if not args.quiet:
        implementation = metrics.implementations()
        print("obtained: %s" % args.obtained)
        print("implementation: ssim=%s per-pixel=%s"
              % (implementation["ssim"], implementation["per_pixel"]))
        if implementation["caveat"]:
            print("! %s" % implementation["caveat"])
        print("-" * 78)
        for comparison in section["comparisons"]:
            print(render(comparison))
            print("-" * 78)
        summary = section["overall"]
        print("overall: %s (%d match, %d mismatch, %d incomparable, %d unavailable)"
              % (summary["status"].upper(), summary["match"], summary["mismatch"],
                 summary["incomparable"], summary["unavailable"]))
        if summary.get("best_reference"):
            best = summary["best_reference"]
            print("best reference: %s (SSIM %.4f, %s)"
                  % (best.get("reference"), best["ssim"], best.get("status")))
    else:
        print(section["overall"]["status"])
    print("disclaimer: %s" % report.DISCLAIMER, file=sys.stderr)

    status = section["overall"]["status"]
    if status == STATUS_MATCH:
        return 0
    if status == STATUS_MISMATCH:
        return 1
    if status in (STATUS_INCOMPARABLE, STATUS_UNAVAILABLE):
        return 3
    return 1


def _mod_id(mod_path):
    if not mod_path:
        return None
    from . import _mod_id_of
    return _mod_id_of(mod_path)


def _environment():
    try:
        from .. import env as host_env
        return host_env.probe()
    except Exception:                                   # pragma: no cover
        return {}


def _attach_to_report(path, section):
    """Attach the section to a bench report produced by any backend."""
    try:
        with open(path, encoding="utf-8") as f:
            rep = json.load(f)
    except (OSError, ValueError) as exc:
        print("error: --report %s: %s" % (path, exc), file=sys.stderr)
        return
    report.attach(rep, section)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, sort_keys=True)
        f.write("\n")
    print("attached visual_comparison to %s" % path, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
