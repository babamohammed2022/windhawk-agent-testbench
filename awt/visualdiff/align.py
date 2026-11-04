"""Normalisation: what has to be true before two images may be compared.

Two screenshots are comparable when they have the same geometry and the same
channel layout. When they do not, this module either makes them comparable in an
explicit, recorded way (an ROI crop, a documented box resize) or refuses — and a
refusal produces `status: incomparable` with the two sizes in the report, never a
similarity percentage computed after a silent rescale.

Very large images are downscaled by an integer factor so the pure-Python path
stays usable; that factor is recorded and every coordinate in the report is
scaled back with its precision stated.
"""
from .. import png as png_mod
from . import metrics

STATUS_COMPARABLE = "comparable"
STATUS_INCOMPARABLE = "incomparable"


def parse_roi(value):
    """`x,y,w,h` (list or string) -> (x, y, w, h) integers."""
    if value is None:
        return None
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace(";", ",").split(",")]
    else:
        parts = list(value)
    if len(parts) != 4:
        raise ValueError("an ROI is x,y,width,height (4 numbers), got %r" % (value,))
    try:
        numbers = [float(part) for part in parts]
    except (TypeError, ValueError):
        raise ValueError("an ROI must be four numbers, got %r" % (value,))
    box = []
    for number in numbers:
        # is_integer() is False for NaN and the infinities too, so a typo cannot
        # quietly become a crop of the wrong strip
        if not float(number).is_integer():
            raise ValueError("an ROI must be four whole pixels, got %r" % (value,))
        box.append(int(number))
    box = tuple(box)
    if box[0] < 0 or box[1] < 0:
        raise ValueError("an ROI origin cannot be negative, got %r" % (value,))
    if box[2] <= 0 or box[3] <= 0:
        raise ValueError("an ROI must have a positive size, got %r" % (value,))
    return box


def geometry(image):
    """The facts about an image that a comparison has to agree on."""
    meta = image.meta or {}
    phys = meta.get("phys") or {}
    return {
        "width": image.width,
        "height": image.height,
        "mode": image.mode,
        "channels": image.channels,
        "dpi": phys.get("dpi"),
        "phys_unit": phys.get("unit"),
        "gamma": meta.get("gamma"),
        "icc_profile": bool(meta.get("icc_profile")),
        "sample_reduction": meta.get("sample_reduction"),
        "origin": meta.get("origin"),
    }


def prepare(obtained, reference, roi=None, roi_obtained=None, roi_reference=None,
            resize=False, mode="RGB", max_pixels=None):
    """Normalise a pair. Returns a dict with the images to compare and why.

    `roi` applies to both; `roi_obtained` / `roi_reference` override per image,
    which is what makes a taskbar strip comparable when the two captures differ
    elsewhere.
    """
    notes = []
    caveats = []
    reasons = []
    steps = []

    left, right = obtained, reference
    left_roi = parse_roi(roi_obtained if roi_obtained is not None else roi)
    right_roi = parse_roi(roi_reference if roi_reference is not None else roi)
    if left_roi:
        left = left.crop(left_roi)
        steps.append({"image": "obtained", "step": "crop", "roi": list(left_roi)})
    if right_roi:
        right = right.crop(right_roi)
        steps.append({"image": "reference", "step": "crop", "roi": list(right_roi)})
    if left_roi or right_roi:
        caveats.append("only the cropped region was compared; differences outside "
                       "it are invisible to this result, and region coordinates are "
                       "relative to the cropped image (add the ROI origin to map "
                       "them back)")

    source = {"obtained": geometry(obtained), "reference": geometry(reference)}
    if source["obtained"]["dpi"] and source["reference"]["dpi"] \
            and source["obtained"]["dpi"] != source["reference"]["dpi"]:
        notes.append("the pHYs chunks differ (%s vs %s dpi): pixel values are "
                     "compared as stored, no resampling for DPI was attempted"
                     % (source["obtained"]["dpi"], source["reference"]["dpi"]))
    if source["obtained"]["icc_profile"] != source["reference"]["icc_profile"] \
            or source["obtained"]["gamma"] != source["reference"]["gamma"]:
        notes.append("colour-management metadata differs (gAMA/iCCP); this reader "
                     "does not apply colour profiles, so values are compared as "
                     "stored in the file")
    for name in ("obtained", "reference"):
        if source[name]["sample_reduction"]:
            notes.append("%s: %s" % (name, source[name]["sample_reduction"]))

    if left.mode != mode:
        left = left.convert(mode)
        steps.append({"image": "obtained", "step": "convert", "to": mode})
    if right.mode != mode:
        right = right.convert(mode)
        steps.append({"image": "reference", "step": "convert", "to": mode})

    resized = False
    if (left.width, left.height) != (right.width, right.height):
        if resize:
            before = (left.width, left.height)
            left = left.resize_box(right.width, right.height)
            resized = True
            steps.append({"image": "obtained", "step": "resize",
                          "from": "%dx%d" % before,
                          "to": "%dx%d" % (right.width, right.height),
                          "method": "box-average"})
            caveats.append("the obtained image was resized with a box average to "
                           "match the reference (%dx%d -> %dx%d): resizing blurs "
                           "edges, so both the similarity and the difference "
                           "percentages are approximations"
                           % (before[0], before[1], right.width, right.height))
        else:
            reasons.append("resolution mismatch: obtained is %dx%d, reference is "
                           "%dx%d. Pass --resize to compare them anyway (recorded "
                           "as an approximation) or crop both to a common region "
                           "with --roi" % (left.width, left.height, right.width,
                                           right.height))

    status = STATUS_INCOMPARABLE if reasons else STATUS_COMPARABLE
    if status != STATUS_COMPARABLE:
        return {"status": status, "reasons": reasons, "notes": notes,
                "caveats": caveats, "steps": steps, "source": source,
                "obtained": None, "reference": None, "coordinate_scale": 1,
                "resized": False, "downscaled": False}

    # keep the pure-Python path usable on big captures
    implementation = "numpy" if metrics.numpy() is not None else "pure"
    limit = max_pixels or metrics.MAX_PIXELS[implementation]
    original_pixels = right.width * right.height
    factor = metrics.integer_downscale(right.width, right.height, limit)
    downscaled = factor > 1
    if downscaled:
        left = left.resize_box(left.width // factor, left.height // factor)
        right = right.resize_box(right.width // factor, right.height // factor)
        steps.append({"image": "both", "step": "downscale", "factor": factor,
                      "reason": "%d pixels exceeds the %s working limit of %d"
                                % (original_pixels, implementation, limit),
                      "method": "box-average"})
        caveats.append("both images were downscaled by %d (box average) to keep the "
                       "%s implementation usable: region coordinates are approximate "
                       "to +/-%d px and small one-pixel differences are averaged away"
                       % (factor, implementation, factor))
    return {
        "status": status,
        "obtained": left,
        "reference": right,
        "reasons": reasons,
        "notes": notes,
        "caveats": caveats,
        "steps": steps,
        "source": source,
        "working_size": {"width": right.width, "height": right.height},
        "coordinate_scale": factor,
        "resized": resized,
        "downscaled": downscaled,
        "implementation": implementation,
    }


def load_pair(obtained_path, reference_path):
    """Read both files, turning a decode failure into a reason, not a traceback."""
    images = {}
    for label, path in (("obtained", obtained_path), ("reference", reference_path)):
        try:
            images[label] = png_mod.read(path)
        except png_mod.PngError as exc:
            images["%s_error" % label] = "%s: %s" % (label, exc)
        except OSError as exc:
            images["%s_error" % label] = "%s: %s" % (label, exc)
    return images
