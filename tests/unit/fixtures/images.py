"""Synthetic screenshots for the visual-comparison tests.

Deterministic, small, generated at test time: no Microsoft pixels, no downloads,
nothing to commit. A "desktop" here is a gradient wallpaper with a taskbar strip
and a window - enough structure for SSIM and region detection to have something
to say, small enough for the pure-Python path to stay quick.

`build_raw_png` assembles a PNG byte by byte so the tests can exercise filter
types, bit depths and palettes that no writer in this repository produces.
"""
import os
import struct
import zlib

from awt import png


def desktop(width=160, height=100, taskbar=16, window=(40, 20, 80, 40)):
    """A synthetic desktop: gradient wallpaper, taskbar, three buttons, a window.

    `window` is (x, y, width, height), the same convention `patch` and `shift`
    use, so a test can name the window once and reuse it.
    """
    image = png.Image(width, height, "RGB")
    for y in range(height):
        for x in range(width):
            image.set(x, y, (30 + (x * 3) % 90, 60 + (y * 2) % 70, 120))
    bar_top = max(0, height - taskbar)
    for y in range(bar_top, height):
        for x in range(width):
            image.set(x, y, (40, 40, 46))
    for index in range(3):
        origin_x = 8 + index * 20
        for y in range(min(bar_top + 3, height), max(0, height - 3)):
            for x in range(min(origin_x, width), min(origin_x + 14, width)):
                if x >= 0:
                    image.set(x, y, (90, 140, 200))
    left, top, box_width, box_height = window
    right, bottom = left + box_width, top + box_height
    for y in range(top, min(bottom, height)):
        for x in range(left, min(right, width)):
            border = (x == left or y == top or x == right - 1 or y == bottom - 1)
            title = top < y < top + 8
            image.set(x, y, (120, 120, 130) if (border or title) else (245, 245, 245))
    return image


def patch(image, box, colour):
    """A copy of `image` with a solid rectangle drawn into it."""
    out = image.copy()
    x, y, width, height = box
    for row in range(y, min(y + height, image.height)):
        for column in range(x, min(x + width, image.width)):
            out.set(column, row, colour)
    return out


def shift(image, box, dx, dy):
    """A copy of `image` with the contents of `box` moved by (dx, dy)."""
    out = image.copy()
    x, y, width, height = box
    for row in range(height):
        for column in range(width):
            source_x, source_y = x + column, y + row
            target_x, target_y = source_x + dx, source_y + dy
            if (0 <= source_x < image.width and 0 <= source_y < image.height
                    and 0 <= target_x < image.width and 0 <= target_y < image.height):
                out.set(target_x, target_y, image.get(source_x, source_y))
    return out


def noise(image, amount, seed=1):
    """Deterministic pseudo-random perturbation of every channel."""
    out = image.copy()
    state = seed
    for index in range(len(out.pixels)):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        delta = (state >> 8) % (amount * 2 + 1) - amount
        out.pixels[index] = max(0, min(255, out.pixels[index] + delta))
    return out


def pad(image, offset, size, fill=(12, 34, 56)):
    """A copy of `image` placed at `offset` inside a larger canvas.

    Used to prove that per-side ROIs really do select different regions of two
    captures: the same pixels, at different coordinates.
    """
    canvas = png.Image(size[0], size[1], image.mode)
    for index in range(len(canvas.pixels)):
        canvas.pixels[index] = fill[index % len(fill)] if image.mode == "L" else 0
    if image.mode != "L":
        for index in range(len(canvas.pixels) // image.channels):
            for channel, value in enumerate(fill):
                canvas.pixels[index * image.channels + channel] = value
    for y in range(image.height):
        for x in range(image.width):
            canvas.set(x + offset[0], y + offset[1], image.get(x, y))
    return canvas


def write(image, directory, name):
    os.makedirs(directory, exist_ok=True)
    return png.write(os.path.join(directory, name), image)


def corrupt(directory, name="broken.png"):
    """A file with a PNG signature and nonsense after it."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    with open(path, "wb") as f:
        f.write(png.SIGNATURE + b"garbage" * 20)
    return path


def chunk(tag, body):
    return (struct.pack(">I", len(body)) + tag + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))


def build_raw_png(width, height, bit_depth, color_type, rows, palette=None,
                  trns=None, extra=b"", interlace=0, corrupt_crc=False):
    """A PNG assembled by hand: `rows` is a list of (filter_type, encoded_bytes)."""
    raw = b"".join(bytes([filter_type]) + line for filter_type, line in rows)
    header = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0,
                         interlace)
    idat = chunk(b"IDAT", zlib.compress(raw, 9))
    if corrupt_crc:
        idat = idat.replace(b"IDAT", b"IDAE")
    return (png.SIGNATURE + chunk(b"IHDR", header) + extra
            + (chunk(b"PLTE", palette) if palette else b"")
            + (chunk(b"tRNS", trns) if trns else b"")
            + idat + chunk(b"IEND", b""))


def paeth(a, b, c):
    prediction = a + b - c
    distance_a, distance_b, distance_c = (abs(prediction - a), abs(prediction - b),
                                          abs(prediction - c))
    if distance_a <= distance_b and distance_a <= distance_c:
        return a
    return b if distance_b <= distance_c else c


def encode_rows(lines, filter_types):
    """Encode whole scanlines with each of the five PNG filters."""
    rows = []
    previous = None
    for line, filter_type in zip(lines, filter_types):
        width = len(line)
        if filter_type == 0:
            encoded = bytes(line)
        elif filter_type == 1:
            encoded = bytes((line[i] - (line[i - 1] if i else 0)) & 0xFF
                            for i in range(width))
        elif filter_type == 2:
            encoded = bytes((line[i] - (previous[i] if previous else 0)) & 0xFF
                            for i in range(width))
        elif filter_type == 3:
            encoded = bytes((line[i] - (((line[i - 1] if i else 0)
                                         + (previous[i] if previous else 0)) >> 1))
                            & 0xFF for i in range(width))
        elif filter_type == 4:
            encoded = bytes((line[i] - paeth(line[i - 1] if i else 0,
                                             previous[i] if previous else 0,
                                             previous[i - 1] if (previous and i)
                                             else 0)) & 0xFF
                            for i in range(width))
        else:
            raise ValueError("filter type %d does not exist" % filter_type)
        rows.append((filter_type, encoded))
        previous = line
    return rows
