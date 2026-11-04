"""Divergent regions: where the two images disagree, with coordinates.

A difference percentage tells an agent *that* something changed. To fix it, the
agent needs *where*: this module turns the per-pixel delta image into a list of
bounding boxes, each with its area, its worst and average delta, and its
centroid, so a margin can be nudged or a colour corrected without a human
looking at the picture.

The mask is labelled with an explicit stack flood fill (4-connectivity) - no
scipy, no recursion limit. Boxes smaller than `min_area` are dropped as noise and
counted, and the list is capped at `max_regions` with `truncated: true` rather
than silently shortened.
"""
DEFAULTS = {"threshold": 24, "min_area": 16, "max_regions": 50,
            "merge_distance": 0, "connectivity": 4}


def mask_from_delta(delta_image, threshold):
    """A 0/1 bytearray: 1 where the difference is above `threshold`."""
    mask = bytearray(delta_image.width * delta_image.height)
    pixels = delta_image.pixels
    for index, value in enumerate(pixels):
        if value > threshold:
            mask[index] = 1
    return mask


def label(mask, width, height, connectivity=4):
    """Connected-component labelling. Returns a list of pixel-index lists."""
    seen = bytearray(width * height)
    components = []
    neighbours = ((1, 0), (-1, 0), (0, 1), (0, -1)) if connectivity == 4 else (
        (1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
    for start in range(width * height):
        if not mask[start] or seen[start]:
            continue
        stack = [start]
        seen[start] = 1
        component = []
        while stack:
            index = stack.pop()
            component.append(index)
            x = index % width
            y = index // width
            for dx, dy in neighbours:
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    neighbour = ny * width + nx
                    if mask[neighbour] and not seen[neighbour]:
                        seen[neighbour] = 1
                        stack.append(neighbour)
        components.append(component)
    return components


def summarise(component, width, delta_pixels, scale=1):
    """Bounding box and delta statistics for one component."""
    min_x = min_y = 1 << 30
    max_x = max_y = -1
    total = 0
    worst = 0
    for index in component:
        x = index % width
        y = index // width
        if x < min_x:
            min_x = x
        if x > max_x:
            max_x = x
        if y < min_y:
            min_y = y
        if y > max_y:
            max_y = y
        value = delta_pixels[index]
        total += value
        if value > worst:
            worst = value
    area = len(component)
    box_width = max_x - min_x + 1
    box_height = max_y - min_y + 1
    centroid_x = sum(index % width for index in component) / area
    centroid_y = sum(index // width for index in component) / area
    return {
        "x": int(min_x * scale),
        "y": int(min_y * scale),
        "width": int(box_width * scale),
        "height": int(box_height * scale),
        "area": area,
        "area_scaled": int(area * scale * scale),
        "box_coverage": round(area / float(box_width * box_height), 4),
        "max_delta": int(worst),
        "mean_delta": round(total / float(area), 3),
        "centroid": [int(centroid_x * scale), int(centroid_y * scale)],
        "coordinate_scale": int(scale),
        "coordinate_precision": ("exact" if scale == 1 else
                                 "+/-%d px (computed on a %dx downscaled image)"
                                 % (scale, scale)),
    }


def merge(regions, distance):
    """Merge boxes that are within `distance` pixels of each other (0 = off)."""
    if not distance or distance <= 0 or len(regions) < 2:
        return regions, 0
    out = []
    merged_count = 0
    for region in sorted(regions, key=lambda item: -item["area"]):
        target = None
        for existing in out:
            if _close(existing, region, distance):
                target = existing
                break
        if target is None:
            out.append(dict(region))
            continue
        merged_count += 1
        x0 = min(target["x"], region["x"])
        y0 = min(target["y"], region["y"])
        x1 = max(target["x"] + target["width"], region["x"] + region["width"])
        y1 = max(target["y"] + target["height"], region["y"] + region["height"])
        target["x"], target["y"] = x0, y0
        target["width"], target["height"] = x1 - x0, y1 - y0
        target["area"] += region["area"]
        target["area_scaled"] += region["area_scaled"]
        target["max_delta"] = max(target["max_delta"], region["max_delta"])
        target["mean_delta"] = round(
            (target["mean_delta"] + region["mean_delta"]) / 2.0, 3)
        target["merged"] = target.get("merged", 1) + 1
    return out, merged_count


def _close(a, b, distance):
    gap_x = max(0, max(a["x"], b["x"]) - min(a["x"] + a["width"], b["x"] + b["width"]))
    gap_y = max(0, max(a["y"], b["y"]) - min(a["y"] + a["height"],
                                             b["y"] + b["height"]))
    return gap_x <= distance and gap_y <= distance


def find_regions(delta_image, threshold=None, min_area=None, max_regions=None,
                 merge_distance=None, scale=1, connectivity=None):
    """The full region analysis, ready to drop into the report."""
    options = dict(DEFAULTS)
    for key, value in (("threshold", threshold), ("min_area", min_area),
                       ("max_regions", max_regions),
                       ("merge_distance", merge_distance),
                       ("connectivity", connectivity)):
        if value is not None:
            options[key] = value
    width, height = delta_image.width, delta_image.height
    mask = mask_from_delta(delta_image, options["threshold"])
    mask_pixels = sum(mask)
    components = label(mask, width, height, options["connectivity"])
    regions = []
    dropped = 0
    for component in components:
        if len(component) < options["min_area"]:
            dropped += 1
            continue
        region = summarise(component, width, delta_image.pixels, scale=scale)
        regions.append(region)
    regions.sort(key=lambda item: (-item["area"], item["y"], item["x"]))
    regions, merged_count = merge(regions, options["merge_distance"])
    for index, region in enumerate(regions, start=1):
        region["label"] = "region-%d" % index
    total = len(regions)
    truncated = total > options["max_regions"]
    listed = regions[:options["max_regions"]]
    return {
        "options": options,
        "mask_pixels": int(mask_pixels),
        "mask_ratio": round(mask_pixels / float(width * height), 6)
        if width * height else None,
        "components": len(components),
        "dropped_small_components": dropped,
        "merged_components": merged_count,
        "total_regions": total,
        "reported_regions": len(listed),
        "truncated": truncated,
        "regions": listed,
        "note": ("the list is capped at max_regions=%d; %d further region(s) exist "
                 "and are counted in total_regions" % (options["max_regions"],
                                                       total - len(listed))
                 if truncated else None),
    }
