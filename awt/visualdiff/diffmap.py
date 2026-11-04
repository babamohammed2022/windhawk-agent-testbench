"""The difference map: a picture of where two screenshots disagree.

The map is a diagnostic, not evidence: it is derived from the same numbers that
are in the report, and its purpose is to let a human (or an agent with vision)
see the shape of a problem - a whole taskbar shifted by two pixels reads
differently from one glyph rendered in the wrong weight.

Rendering, at the working (possibly downscaled) resolution:

* identical or near-identical pixels -> the obtained image's luma, darkened, so
  context stays readable;
* differing pixels -> a heat ramp, yellow at the threshold up to red/magenta at
  a full 255 delta;
* each reported region -> a cyan outline with a corner tick, drawn from the same
  bounding boxes the report lists, so picture and coordinates cannot disagree.
"""
from .. import png as png_mod

HEAT_LOW = (255, 214, 64)        # just above the threshold
HEAT_HIGH = (255, 32, 96)        # a full-scale difference
OUTLINE = (0, 229, 255)
DARKEN = 0.45


def _heat(delta, threshold):
    """Interpolate the heat ramp between the threshold and 255."""
    span = max(1, 255 - threshold)
    ratio = min(1.0, max(0.0, (delta - threshold) / float(span)))
    return tuple(int(low + (high - low) * ratio)
                 for low, high in zip(HEAT_LOW, HEAT_HIGH))


def render(obtained, reference, delta, regions=None, threshold=24, scale=1):
    """Build the difference map for a prepared (same-geometry) pair."""
    if (obtained.width, obtained.height) != (delta.width, delta.height):
        raise ValueError("the delta image must match the obtained image")
    width, height = delta.width, delta.height
    out = png_mod.Image(width, height, "RGB")
    luma = obtained.luma() if obtained.mode != "L" else obtained
    pixels = out.pixels
    delta_pixels = delta.pixels
    luma_pixels = luma.pixels
    for index in range(width * height):
        value = delta_pixels[index]
        if value > threshold:
            pixels[index * 3:index * 3 + 3] = bytes(_heat(value, threshold))
        else:
            grey = int(luma_pixels[index] * DARKEN)
            pixels[index * 3] = grey
            pixels[index * 3 + 1] = grey
            pixels[index * 3 + 2] = grey
    for region in (regions or {}).get("regions", []):
        x = max(0, region["x"] // scale)
        y = max(0, region["y"] // scale)
        box_width = max(1, region["width"] // scale)
        box_height = max(1, region["height"] // scale)
        _outline(out, x, y, box_width, box_height)
        _corner_tick(out, x, y, box_width, box_height)
    return out


def _outline(image, x, y, width, height, thickness=1):
    for step in range(thickness):
        for column in range(max(0, x - step), min(image.width, x + width + step)):
            for row in (y - step, y + height - 1 + step):
                if 0 <= row < image.height:
                    image.set(column, row, OUTLINE)
        for row in range(max(0, y - step), min(image.height, y + height + step)):
            for column in (x - step, x + width - 1 + step):
                if 0 <= column < image.width:
                    image.set(column, row, OUTLINE)


def _corner_tick(image, x, y, width, height, length=6):
    """A small tick so a 1-pixel-high region is still visible in the map."""
    for offset in range(length):
        for column, row in ((x - 2 - offset, y - 2), (x + width + 1 + offset, y - 2),
                            (x - 2, y - 2 - offset), (x + width + 1, y - 2 - offset)):
            if 0 <= column < image.width and 0 <= row < image.height:
                image.set(column, row, OUTLINE)


def side_by_side(images, gap=6, background=(24, 24, 24), labels=None):
    """Place images next to each other on one canvas (same height, padded)."""
    images = [image.convert("RGB") if image.mode != "RGB" else image
              for image in images]
    height = max(image.height for image in images)
    width = sum(image.width for image in images) + gap * (len(images) - 1)
    canvas = png_mod.solid(width, height, background, mode="RGB")
    offset = 0
    for image in images:
        for y in range(image.height):
            row = image.row(y)
            start = ((y) * width + offset) * 3
            canvas.pixels[start:start + len(row)] = row
        offset += image.width + gap
    if labels:
        _write_labels(canvas, images, labels, gap)
    return canvas


def _write_labels(canvas, images, labels, gap):
    """A 5x7 bitmap caption above each panel - no font files, no Pillow."""
    offset = 0
    for image, label in zip(images, labels):
        for index, character in enumerate(str(label)[:24]):
            glyph = GLYPHS.get(character.upper())
            if not glyph:
                continue
            origin_x = offset + 2 + index * 6
            origin_y = 2
            for row_index, row in enumerate(glyph):
                for column_index, bit in enumerate(row):
                    if bit == "1":
                        x = origin_x + column_index
                        y = origin_y + row_index
                        if 0 <= x < canvas.width and 0 <= y < canvas.height:
                            canvas.set(x, y, (255, 255, 255))
        offset += image.width + gap
    return canvas


GLYPHS = {
    "A": ("01110", "10001", "11111", "10001", "10001", "00000", "00000"),
    "B": ("11110", "10001", "11110", "10001", "11110", "00000", "00000"),
    "C": ("01111", "10000", "10000", "10000", "01111", "00000", "00000"),
    "D": ("11110", "10001", "10001", "10001", "11110", "00000", "00000"),
    "E": ("11111", "10000", "11110", "10000", "11111", "00000", "00000"),
    "F": ("11111", "10000", "11110", "10000", "10000", "00000", "00000"),
    "G": ("01111", "10000", "10011", "10001", "01111", "00000", "00000"),
    "H": ("10001", "10001", "11111", "10001", "10001", "00000", "00000"),
    "I": ("11111", "00100", "00100", "00100", "11111", "00000", "00000"),
    "J": ("00111", "00010", "00010", "10010", "01100", "00000", "00000"),
    "K": ("10001", "10010", "11100", "10010", "10001", "00000", "00000"),
    "L": ("10000", "10000", "10000", "10000", "11111", "00000", "00000"),
    "M": ("10001", "11011", "10101", "10001", "10001", "00000", "00000"),
    "N": ("10001", "11001", "10101", "10011", "10001", "00000", "00000"),
    "O": ("01110", "10001", "10001", "10001", "01110", "00000", "00000"),
    "P": ("11110", "10001", "11110", "10000", "10000", "00000", "00000"),
    "Q": ("01110", "10001", "10101", "10010", "01101", "00000", "00000"),
    "R": ("11110", "10001", "11110", "10010", "10001", "00000", "00000"),
    "S": ("01111", "10000", "01110", "00001", "11110", "00000", "00000"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00000", "00000"),
    "U": ("10001", "10001", "10001", "10001", "01110", "00000", "00000"),
    "V": ("10001", "10001", "10001", "01010", "00100", "00000", "00000"),
    "W": ("10001", "10001", "10101", "11011", "10001", "00000", "00000"),
    "X": ("10001", "01010", "00100", "01010", "10001", "00000", "00000"),
    "Y": ("10001", "01010", "00100", "00100", "00100", "00000", "00000"),
    "Z": ("11111", "00010", "00100", "01000", "11111", "00000", "00000"),
    "0": ("01110", "10001", "10011", "11001", "01110", "00000", "00000"),
    "1": ("00100", "01100", "00100", "00100", "01110", "00000", "00000"),
    "2": ("01110", "10001", "00110", "01000", "11111", "00000", "00000"),
    "3": ("11110", "00001", "01110", "00001", "11110", "00000", "00000"),
    "4": ("10010", "10010", "11111", "00010", "00010", "00000", "00000"),
    "5": ("11111", "10000", "11110", "00001", "11110", "00000", "00000"),
    "6": ("01110", "10000", "11110", "10001", "01110", "00000", "00000"),
    "7": ("11111", "00001", "00010", "00100", "01000", "00000", "00000"),
    "8": ("01110", "10001", "01110", "10001", "01110", "00000", "00000"),
    "9": ("01110", "10001", "01111", "00001", "01110", "00000", "00000"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    "-": ("00000", "00000", "11111", "00000", "00000", "00000", "00000"),
    ":": ("00000", "00100", "00000", "00100", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00100", "00000", "00000"),
    "/": ("00001", "00010", "00100", "01000", "10000", "00000", "00000"),
}


def write(image, path):
    """Write a map (or a side-by-side canvas) and return the path."""
    return png_mod.write(path, image)
