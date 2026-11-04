"""Pixel metrics: per-pixel difference and SSIM, with and without numpy.

Two implementations of every metric exist, and the report always says which one
ran:

* `numpy` present  - SSIM with the standard 11x11 Gaussian window (sigma 1.5),
  computed on luma; per-pixel statistics vectorised.
* no numpy         - SSIM with non-overlapping 8x8 blocks and a uniform window
  (the classic "block SSIM"), in pure Python; per-pixel statistics in one pass
  over `bytes`.

Those two numbers are **not interchangeable**: a Gaussian-window SSIM and a block
SSIM of the same pair of images differ, sometimes by several percent. So
`metrics.implementation` is part of the result, thresholds can be set per
implementation, and a pure-Python result carries a caveat saying so.

Very large images are downscaled by an integer factor before comparison, and that
is recorded too (`working_size`, `coordinate_scale`): comparing 4K screenshots
pixel-by-pixel in pure Python is minutes of work for a number nobody reads.
"""
import math

SSIM_GAUSSIAN = "ssim-gaussian-11x11-numpy"
SSIM_BLOCK = "ssim-block-8x8-pure"
PER_PIXEL_NUMPY = "per-pixel-numpy"
PER_PIXEL_PURE = "per-pixel-pure"

# Above this many pixels, both implementations downscale by an integer factor.
MAX_PIXELS = {"numpy": 12_000_000, "pure": 400_000}

GAUSSIAN_11 = (0.00000116, 0.00011694, 0.00568179, 0.10366464, 0.29307337,
               0.29307337, 0.10366464, 0.00568179, 0.00011694, 0.00000116)


def numpy():
    """numpy as a module, or None. Never raises, never guesses."""
    try:
        import numpy as _numpy
        return _numpy
    except Exception:
        return None


def implementations():
    """Which implementations would run here (recorded in every report)."""
    available = numpy() is not None
    return {
        "numpy_available": available,
        "ssim": SSIM_GAUSSIAN if available else SSIM_BLOCK,
        "per_pixel": PER_PIXEL_NUMPY if available else PER_PIXEL_PURE,
        "caveat": None if available else (
            "numpy is not installed: SSIM was computed with non-overlapping 8x8 "
            "blocks and a uniform window instead of the standard 11x11 Gaussian "
            "window. The number is a valid structural similarity, but it is not "
            "directly comparable with an SSIM from scikit-image, Pillow or a "
            "paper - set thresholds for this implementation or install numpy."),
    }


def integer_downscale(width, height, limit):
    """The smallest integer factor that brings the image under `limit` pixels."""
    factor = 1
    while (width // factor) * (height // factor) > limit and factor < 64:
        factor += 1
    return factor


def per_pixel(a, b, tolerance=0):
    """Per-pixel difference statistics between two same-size luma/RGB images.

    Returns absolute numbers on the 0-255 scale plus ratios in 0..1. `tolerance`
    is the per-channel delta that still counts as "the same colour", which is how
    a comparison survives lossy encoding or a one-off rounding difference.
    """
    if (a.width, a.height, a.channels) != (b.width, b.height, b.channels):
        raise ValueError("per_pixel needs identical geometry and mode: %r vs %r"
                         % (a.describe(), b.describe()))
    module = numpy()
    if module is not None:
        return _per_pixel_numpy(module, a, b, tolerance)
    return _per_pixel_pure(a, b, tolerance)


def _per_pixel_numpy(np, a, b, tolerance):
    left = np.frombuffer(bytes(a.pixels), dtype=np.uint8).reshape(
        a.height, a.width, a.channels).astype(np.int32)
    right = np.frombuffer(bytes(b.pixels), dtype=np.uint8).reshape(
        b.height, b.width, b.channels).astype(np.int32)
    delta = np.abs(left - right)
    per_pixel_delta = delta.max(axis=2)
    total = a.width * a.height
    identical = int((per_pixel_delta == 0).sum())
    within = int((per_pixel_delta <= tolerance).sum())
    mean_abs = float(delta.mean())
    max_abs = int(delta.max()) if total else 0
    rmse = float(math.sqrt((delta.astype(np.float64) ** 2).mean())) if total else 0.0
    histogram = _histogram_from_counts(np.bincount(per_pixel_delta.ravel(),
                                                   minlength=256)[:256])
    return {
        "implementation": PER_PIXEL_NUMPY,
        "tolerance": int(tolerance),
        "pixels": total,
        "identical_pixels": identical,
        "identical_ratio": round(identical / total, 6) if total else None,
        "within_tolerance_pixels": within,
        "within_tolerance_ratio": round(within / total, 6) if total else None,
        "differing_pixels": total - within,
        "differing_ratio": round((total - within) / total, 6) if total else None,
        "mean_abs_delta": round(mean_abs, 4),
        "max_abs_delta": max_abs,
        "rmse": round(rmse, 4),
        "similarity": round(1.0 - mean_abs / 255.0, 6),
        "delta_histogram": histogram,
    }


def _per_pixel_pure(a, b, tolerance):
    channels = a.channels
    total = a.width * a.height
    identical = within = 0
    delta_sum = 0
    delta_count = 0
    max_abs = 0
    square_sum = 0
    buckets = [0] * 256
    for y in range(a.height):
        row_a = a.row(y)
        row_b = b.row(y)
        for x in range(a.width):
            start = x * channels
            worst = 0
            for c in range(channels):
                d = row_a[start + c] - row_b[start + c]
                if d < 0:
                    d = -d
                if d > worst:
                    worst = d
                delta_sum += d
                square_sum += d * d
                delta_count += 1
            buckets[worst] += 1
            if worst == 0:
                identical += 1
            if worst <= tolerance:
                within += 1
            if worst > max_abs:
                max_abs = worst
    mean_abs = delta_sum / delta_count if delta_count else 0.0
    return {
        "implementation": PER_PIXEL_PURE,
        "tolerance": int(tolerance),
        "pixels": total,
        "identical_pixels": identical,
        "identical_ratio": round(identical / total, 6) if total else None,
        "within_tolerance_pixels": within,
        "within_tolerance_ratio": round(within / total, 6) if total else None,
        "differing_pixels": total - within,
        "differing_ratio": round((total - within) / total, 6) if total else None,
        "mean_abs_delta": round(mean_abs, 4),
        "max_abs_delta": max_abs,
        "rmse": round(math.sqrt(square_sum / delta_count), 4) if delta_count else 0.0,
        "similarity": round(1.0 - mean_abs / 255.0, 6),
        "delta_histogram": _histogram_from_counts(buckets),
    }


def _histogram_from_counts(counts):
    """Coarse buckets, so the report stays small and readable."""
    bands = ((0, 0), (1, 4), (5, 8), (9, 16), (17, 32), (33, 64), (65, 128),
             (129, 255))
    out = {}
    for low, high in bands:
        total = int(sum(counts[low:high + 1]))
        if total:
            out["%d-%d" % (low, high)] = total
    return out


def delta_map(a, b):
    """A luma image of the per-pixel maximum channel difference (0-255)."""
    from .. import png as png_mod
    if (a.width, a.height, a.channels) != (b.width, b.height, b.channels):
        raise ValueError("delta_map needs identical geometry and mode: %r vs %r"
                         % (a.describe(), b.describe()))
    out = png_mod.Image(a.width, a.height, "L")
    channels = a.channels
    for y in range(a.height):
        row_a, row_b = a.row(y), b.row(y)
        line = bytearray(a.width)
        for x in range(a.width):
            start = x * channels
            worst = 0
            for c in range(channels):
                d = row_a[start + c] - row_b[start + c]
                if d < 0:
                    d = -d
                if d > worst:
                    worst = d
            line[x] = worst
        out.pixels[y * a.width:(y + 1) * a.width] = line
    return out


def ssim(a, b):
    """Structural similarity of two same-size images, on luma, in 0..1."""
    if (a.width, a.height) != (b.width, b.height):
        raise ValueError("ssim needs identical geometry: %dx%d vs %dx%d"
                         % (a.width, a.height, b.width, b.height))
    left = a.luma() if a.mode != "L" else a
    right = b.luma() if b.mode != "L" else b
    module = numpy()
    if module is not None:
        return _ssim_numpy(module, left, right)
    return _ssim_blocks(left, right)


def _ssim_numpy(np, a, b, window=GAUSSIAN_11):
    """11x11 Gaussian-window SSIM (Wang et al. 2004), L = 255."""
    left = np.frombuffer(bytes(a.pixels), dtype=np.uint8).reshape(
        a.height, a.width).astype(np.float64)
    right = np.frombuffer(bytes(b.pixels), dtype=np.uint8).reshape(
        b.height, b.width).astype(np.float64)
    kernel = np.asarray(window, dtype=np.float64)
    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2

    def filter_gaussian(image):
        padded = np.pad(image, ((4, 4), (4, 4)), mode="reflect")
        horizontal = _convolve_axis(padded, kernel, axis=1)
        return _convolve_axis(horizontal, kernel, axis=0)

    mean_left = filter_gaussian(left)
    mean_right = filter_gaussian(right)
    mean_left_square = filter_gaussian(left * left)
    mean_right_square = filter_gaussian(right * right)
    mean_left_right = filter_gaussian(left * right)
    variance_left = mean_left_square - mean_left * mean_left
    variance_right = mean_right_square - mean_right * mean_right
    covariance = mean_left_right - mean_left * mean_right
    numerator = (2 * mean_left * mean_right + c1) * (2 * covariance + c2)
    denominator = ((mean_left ** 2 + mean_right ** 2 + c1)
                   * (variance_left + variance_right + c2))
    surface = numerator / denominator
    value = float(surface.mean())
    return {
        "implementation": SSIM_GAUSSIAN,
        "window": "gaussian-11x11",
        "sigma": 1.5,
        "luminance_range": 255,
        "c1": c1,
        "c2": c2,
        "ssim": round(max(0.0, min(1.0, value)), 6),
        "samples": int(surface.size),
        "map_available": True,
    }


def _convolve_axis(padded, kernel, axis):
    """Separable convolution of an 11-tap kernel along one axis (numpy)."""
    import numpy as np
    size = kernel.size
    half = size // 2
    if axis == 1:
        shape = (padded.shape[0], padded.shape[1] - size + 1, size)
        strides = (padded.strides[0], padded.strides[1], padded.strides[1])
    else:
        shape = (padded.shape[0] - size + 1, padded.shape[1], size)
        strides = (padded.strides[0], padded.strides[1], padded.strides[0])
    windows = np.lib.stride_tricks.as_strided(padded, shape=shape, strides=strides,
                                              writeable=False)
    return np.tensordot(windows, kernel, axes=([-1], [0]))


def _ssim_blocks(a, b, block=8):
    """Block SSIM: non-overlapping blocks, uniform window. Pure Python."""
    width, height = a.width, a.height
    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2
    total = 0.0
    blocks = 0
    skipped = 0
    for top in range(0, height - block + 1, block):
        rows_a = [a.row(y) for y in range(top, top + block)]
        rows_b = [b.row(y) for y in range(top, top + block)]
        for left in range(0, width - block + 1, block):
            sum_a = sum_b = 0
            for row_a, row_b in zip(rows_a, rows_b):
                window_a = row_a[left:left + block]
                window_b = row_b[left:left + block]
                sum_a += sum(window_a)
                sum_b += sum(window_b)
            count = block * block
            mean_a = sum_a / count
            mean_b = sum_b / count
            variance_a = variance_b = covariance = 0.0
            for row_a, row_b in zip(rows_a, rows_b):
                for index in range(block):
                    da = row_a[left + index] - mean_a
                    db = row_b[left + index] - mean_b
                    variance_a += da * da
                    variance_b += db * db
                    covariance += da * db
            variance_a /= count - 1
            variance_b /= count - 1
            covariance /= count - 1
            numerator = (2 * mean_a * mean_b + c1) * (2 * covariance + c2)
            denominator = ((mean_a ** 2 + mean_b ** 2 + c1)
                           * (variance_a + variance_b + c2))
            total += numerator / denominator
            blocks += 1
    if not blocks:
        skipped = 1
        # Smaller than one block: fall back to a single global comparison rather
        # than reporting a similarity of nothing at all.
        sum_a = sum(a.pixels)
        sum_b = sum(b.pixels)
        count = max(1, width * height)
        mean_a, mean_b = sum_a / count, sum_b / count
        variance_a = variance_b = covariance = 0.0
        for value_a, value_b in zip(a.pixels, b.pixels):
            da = value_a - mean_a
            db = value_b - mean_b
            variance_a += da * da
            variance_b += db * db
            covariance += da * db
        if count > 1:
            variance_a /= count - 1
            variance_b /= count - 1
            covariance /= count - 1
        numerator = (2 * mean_a * mean_b + c1) * (2 * covariance + c2)
        denominator = ((mean_a ** 2 + mean_b ** 2 + c1)
                       * (variance_a + variance_b + c2))
        total = numerator / denominator
        blocks = 1
    return {
        "implementation": SSIM_BLOCK,
        "window": "block-%dx%d" % (block, block),
        "sigma": None,
        "luminance_range": 255,
        "c1": c1,
        "c2": c2,
        "ssim": round(max(0.0, min(1.0, total / blocks)), 6),
        "samples": blocks,
        "map_available": False,
        "whole_image_fallback": bool(skipped),
        "note": ("uniform-window block SSIM: structurally comparable, numerically "
                 "different from a Gaussian-window SSIM"),
    }
