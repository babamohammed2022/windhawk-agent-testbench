"""A small, dependency-free PNG reader/writer and pixel toolbox.

The bench compares screenshots. Screenshots are PNGs. Everything else in
`awt.visualdiff` is arithmetic over pixels, so this module exists to make that
arithmetic runnable anywhere Python runs - no Pillow, no numpy, no libpng.

Supported on read: 8-bit colour types 0 (grey), 2 (RGB), 3 (palette, with
`tRNS`), 4 (grey+alpha) and 6 (RGBA); filters 0-4; `pHYs`, `gAMA` and `iCCP`
are recorded but not applied. 16-bit samples are reduced to their high byte and
that reduction is recorded. Interlaced PNGs (Adam7) are **rejected** rather than
half-decoded: a partial image must never be compared as if it were whole.

Supported on write: 8-bit grey, RGB and RGBA, filter 0, zlib-compressed IDAT.
That is all a difference map or a test fixture needs.

Pillow, when installed, is used by the tests to cross-check every decode against
a real implementation (`tests/unit/test_png.py`); it is never required at
runtime.
"""
import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"

MODES = {0: "L", 2: "RGB", 3: "P", 4: "LA", 6: "RGBA"}
CHANNELS = {"L": 1, "LA": 2, "RGB": 3, "RGBA": 4, "P": 1}
COLOR_TYPE_OF = {"L": 0, "LA": 4, "RGB": 2, "RGBA": 6}


class PngError(Exception):
    """A PNG that this reader refuses to decode, with the reason."""


class Image:
    """An 8-bit image: `pixels` is a row-major bytearray of `channels` per pixel."""

    def __init__(self, width, height, mode="RGBA", pixels=None, meta=None):
        if width <= 0 or height <= 0:
            raise PngError("an image must have positive dimensions, got %dx%d"
                           % (width, height))
        if mode not in CHANNELS:
            raise PngError("unsupported mode %r (use L, LA, RGB or RGBA)" % mode)
        self.width = int(width)
        self.height = int(height)
        self.mode = mode
        self.channels = CHANNELS[mode]
        self.meta = dict(meta or {})
        expected = self.width * self.height * self.channels
        if pixels is None:
            self.pixels = bytearray(expected)
        else:
            self.pixels = bytearray(pixels)
            if len(self.pixels) != expected:
                raise PngError("pixel data is %d bytes, expected %d for %dx%d %s"
                               % (len(self.pixels), expected, width, height, mode))

    # -- pixel access -------------------------------------------------------
    def get(self, x, y):
        """The pixel at (x, y) as a tuple of `channels` values."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError("pixel (%d, %d) is outside %dx%d"
                             % (x, y, self.width, self.height))
        start = (y * self.width + x) * self.channels
        return tuple(self.pixels[start:start + self.channels])

    def set(self, x, y, value):
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError("pixel (%d, %d) is outside %dx%d"
                             % (x, y, self.width, self.height))
        start = (y * self.width + x) * self.channels
        self.pixels[start:start + self.channels] = bytes(value)

    def row(self, y):
        start = y * self.width * self.channels
        return self.pixels[start:start + self.width * self.channels]

    def luma(self):
        """Greyscale equivalent (Rec. 601 weights, integer arithmetic)."""
        if self.mode == "L":
            return Image(self.width, self.height, "L", bytes(self.pixels),
                         dict(self.meta, source_mode="L"))
        out = bytearray(self.width * self.height)
        step = self.channels
        has_alpha = self.mode in ("LA", "RGBA")
        src = self.pixels
        index = 0
        for pixel_start in range(0, len(src), step):
            if self.mode == "LA":
                value = src[pixel_start]
            else:
                r, g, b = src[pixel_start], src[pixel_start + 1], src[pixel_start + 2]
                value = (r * 299 + g * 587 + b * 114) // 1000
            if has_alpha:
                # Composite over white first: a transparent pixel in one capture
                # and a white pixel in the other are the same visible colour.
                alpha = src[pixel_start + step - 1]
                if alpha != 255:
                    value = (value * alpha + 255 * (255 - alpha)) // 255
            out[index] = value
            index += 1
        return Image(self.width, self.height, "L", bytes(out),
                     dict(self.meta, source_mode=self.mode))

    def crop(self, box):
        """`box` is (x, y, width, height) in pixels; out-of-range is an error."""
        x, y, width, height = (int(v) for v in box)
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            raise PngError("a crop box must be positive: %r" % (box,))
        if x + width > self.width or y + height > self.height:
            raise PngError("crop %r does not fit in %dx%d"
                           % (box, self.width, self.height))
        out = bytearray(width * height * self.channels)
        row_bytes = width * self.channels
        for line in range(height):
            start = ((y + line) * self.width + x) * self.channels
            out[line * row_bytes:(line + 1) * row_bytes] = \
                self.pixels[start:start + row_bytes]
        return Image(width, height, self.mode, bytes(out),
                     dict(self.meta, cropped_from="%dx%d+%d+%d"
                          % (self.width, self.height, x, y)))

    def convert(self, mode):
        """L / LA / RGB / RGBA conversions (no dithering, no colour management)."""
        if mode == self.mode:
            return Image(self.width, self.height, mode, bytes(self.pixels),
                         dict(self.meta))
        if mode not in CHANNELS or mode == "P":
            raise PngError("cannot convert to %r" % mode)
        out = Image(self.width, self.height, mode, meta=dict(
            self.meta, converted_from=self.mode))
        for y in range(self.height):
            for x in range(self.width):
                value = self.get(x, y)
                out.set(x, y, _convert_pixel(value, self.mode, mode))
        return out

    def resize_box(self, width, height):
        """Box-average resize. Deliberately simple and documented as such.

        Averaging is the honest choice when two captures have different
        geometries: nearest-neighbour invents sharpness, and a comparison of a
        resized image is already an approximation - `awt.visualdiff` says so in
        the report instead of letting the number look authoritative.
        """
        width, height = int(width), int(height)
        if width <= 0 or height <= 0:
            raise PngError("resize target must be positive: %dx%d" % (width, height))
        out = Image(width, height, self.mode, meta=dict(
            self.meta, resized_from="%dx%d" % (self.width, self.height),
            resize_method="box-average"))
        for target_y in range(height):
            source_y0 = target_y * self.height // height
            source_y1 = max(source_y0 + 1, (target_y + 1) * self.height // height)
            for target_x in range(width):
                source_x0 = target_x * self.width // width
                source_x1 = max(source_x0 + 1, (target_x + 1) * self.width // width)
                totals = [0] * self.channels
                count = 0
                for y in range(source_y0, source_y1):
                    row = self.row(y)
                    for x in range(source_x0, source_x1):
                        start = x * self.channels
                        for c in range(self.channels):
                            totals[c] += row[start + c]
                        count += 1
                out.set(target_x, target_y,
                        tuple(total // count for total in totals))
        return out

    def copy(self):
        return Image(self.width, self.height, self.mode, bytes(self.pixels),
                     dict(self.meta))

    def describe(self):
        return {"width": self.width, "height": self.height, "mode": self.mode,
                "channels": self.channels, "meta": dict(self.meta)}

    def __repr__(self):
        return "<Image %dx%d %s>" % (self.width, self.height, self.mode)


def _convert_pixel(value, source, target):
    if source == "L":
        grey = value[0]
        rgb = (grey, grey, grey)
        alpha = 255
    elif source == "LA":
        rgb = (value[0], value[0], value[0])
        alpha = value[1]
    elif source == "RGB":
        rgb = tuple(value)
        alpha = 255
    else:
        rgb = tuple(value[:3])
        alpha = value[3]
    if target == "L":
        return ((rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) // 1000,)
    if target == "LA":
        return ((rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) // 1000, alpha)
    if target == "RGB":
        return rgb
    return rgb + (alpha,)


# ---------------------------------------------------------------- reading ----
def read(path):
    with open(path, "rb") as f:
        return from_bytes(f.read(), origin=path)


def from_bytes(data, origin=None):
    """Decode a PNG. Raises PngError with a reason instead of guessing."""
    if not data.startswith(SIGNATURE):
        raise PngError("%s: not a PNG (bad signature %r)"
                       % (origin or "<bytes>", data[:8]))
    position = len(SIGNATURE)
    chunks = []
    width = height = bit_depth = color_type = interlace = None
    palette = transparency = None
    idat = []
    meta = {"origin": origin}
    while position + 8 <= len(data):
        length = struct.unpack(">I", data[position:position + 4])[0]
        tag = data[position + 4:position + 8]
        body_start = position + 8
        body_end = body_start + length
        if body_end + 4 > len(data):
            raise PngError("%s: chunk %s is truncated" % (origin or "<bytes>", tag))
        body = data[body_start:body_end]
        crc_stored = struct.unpack(">I", data[body_end:body_end + 4])[0]
        if zlib.crc32(tag + body) & 0xFFFFFFFF != crc_stored:
            raise PngError("%s: chunk %s has a bad CRC (corrupt file)"
                           % (origin or "<bytes>", tag.decode("latin-1")))
        chunks.append((tag.decode("latin-1"), length))
        if tag == b"IHDR":
            (width, height, bit_depth, color_type, compression, filter_method,
             interlace) = struct.unpack(">IIBBBBB", body)
            if compression != 0 or filter_method != 0:
                raise PngError("%s: unknown compression/filter method" % origin)
            if interlace != 0:
                raise PngError("%s: interlaced (Adam7) PNGs are not decoded - a "
                               "partial image must never be compared as whole"
                               % (origin or "<bytes>"))
            if color_type not in MODES:
                raise PngError("%s: unsupported colour type %d" % (origin, color_type))
            if bit_depth not in (1, 2, 4, 8, 16):
                raise PngError("%s: unsupported bit depth %d" % (origin, bit_depth))
            if bit_depth == 16:
                meta["sample_reduction"] = "16-bit samples reduced to their high byte"
            if bit_depth > 8 and color_type == 3:
                raise PngError("%s: palettes cannot use %d-bit entries"
                               % (origin, bit_depth))
        elif tag == b"PLTE":
            palette = body
        elif tag == b"tRNS":
            transparency = body
        elif tag == b"IDAT":
            idat.append(body)
        elif tag == b"pHYs":
            x, y, unit = struct.unpack(">IIB", body)
            meta["phys"] = {"x": x, "y": y, "unit": unit,
                            "dpi": (round(x * 0.0254, 2), round(y * 0.0254, 2))
                            if unit == 1 else None}
        elif tag == b"gAMA":
            meta["gamma"] = struct.unpack(">I", body)[0] / 100000.0
        elif tag == b"iCCP":
            meta["icc_profile"] = True
            meta["icc_name"] = body.split(b"\x00", 1)[0].decode("latin-1")
        elif tag == b"sRGB":
            meta["srgb"] = True
        elif tag == b"tEXt" or tag == b"iTXt":
            key, _, value = body.partition(b"\x00")
            meta.setdefault("text", {})[key.decode("latin-1")] = \
                value.decode("latin-1", "replace")[:200]
        elif tag == b"IEND":
            break
        position = body_end + 4
    if width is None:
        raise PngError("%s: no IHDR chunk" % (origin or "<bytes>"))
    if not idat:
        raise PngError("%s: no IDAT chunk (no pixel data)" % (origin or "<bytes>"))
    meta["chunks"] = ["%s(%d)" % (tag, length) for tag, length in chunks]
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as exc:
        raise PngError("%s: IDAT does not decompress (%s)"
                       % (origin or "<bytes>", exc))

    samples_per_pixel = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    if bit_depth < 8 and color_type != 3:
        samples_per_pixel = 1
    bits_per_pixel = samples_per_pixel * bit_depth
    bytes_per_pixel = max(1, bits_per_pixel // 8)
    if color_type == 3:
        if palette is None:
            raise PngError("%s: colour type 3 without a PLTE chunk"
                           % (origin or "<bytes>"))
        bytes_per_pixel = 1
    stride = (width * bits_per_pixel + 7) // 8
    expected = stride * height + height          # one filter byte per row
    if len(raw) < expected:
        raise PngError("%s: decompressed data is %d bytes, expected %d (truncated "
                       "image)" % (origin or "<bytes>", len(raw), expected))

    unfiltered = _unfilter(raw, height, stride, bytes_per_pixel)
    if color_type == 3:
        image = _expand_palette(unfiltered, width, height, palette, transparency,
                                bit_depth)
    else:
        image = _expand_samples(unfiltered, width, height, color_type, bit_depth,
                                transparency)
    image.meta.update(meta)
    return image


def _unfilter(raw, height, stride, bpp):
    """Reverse PNG's per-scanline filters (0-4)."""
    out = bytearray(stride * height)
    previous = bytearray(stride)
    position = 0
    for y in range(height):
        filter_type = raw[position]
        position += 1
        line = bytearray(raw[position:position + stride])
        position += stride
        if filter_type == 0:
            pass
        elif filter_type == 1:                      # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif filter_type == 2:                      # Up
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:                      # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:                      # Paeth
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                up_left = previous[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, previous[i], up_left)) & 0xFF
        else:
            raise PngError("unknown filter type %d on row %d" % (filter_type, y))
        out[y * stride:(y + 1) * stride] = line
        previous = line
    return out


def _paeth(left, up, up_left):
    initial = left + up - up_left
    distance_left = abs(initial - left)
    distance_up = abs(initial - up)
    distance_up_left = abs(initial - up_left)
    if distance_left <= distance_up and distance_left <= distance_up_left:
        return left
    if distance_up <= distance_up_left:
        return up
    return up_left


def _raw_sample(row, index, bit_depth):
    """One sample at `index` (counting samples, not bytes), NOT scaled.

    Palette indexes must never be scaled: with 4-bit pixels an index of 15 means
    entry 15, not white. Sample *values* are scaled by `_sample`.
    """
    if bit_depth == 8:
        return row[index]
    if bit_depth == 16:
        return row[index * 2]                      # high byte; recorded in meta
    per_byte = 8 // bit_depth
    byte = row[index // per_byte]
    shift = bit_depth * (per_byte - 1 - (index % per_byte))
    return (byte >> shift) & SCALE_MASK[bit_depth]


def _sample(row, index, bit_depth):
    """One sample value, scaled to 8 bits (1 -> 255, 2 -> 85, 4 -> 17)."""
    value = _raw_sample(row, index, bit_depth)
    if bit_depth < 8:
        return value * SCALE_TO_8[bit_depth]
    return value


SCALE_MASK = {1: 0x01, 2: 0x03, 4: 0x0F}
SCALE_TO_8 = {1: 255, 2: 85, 4: 17}


def _expand_samples(unfiltered, width, height, color_type, bit_depth, transparency):
    """Any supported colour type / bit depth -> 8-bit L, LA, RGB or RGBA."""
    samples = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    mode = {1: "L", 3: "RGB", 2: "LA", 4: "RGBA"}[samples]
    transparent_grey = None
    if color_type == 0 and transparency and len(transparency) >= 2:
        # tRNS on a grey image names one 16-bit sample value that is transparent
        value = struct.unpack(">H", transparency[0:2])[0]
        transparent_grey = value >> 8 if bit_depth == 16 else value & 0xFF
        mode = "LA"
    if color_type == 2 and transparency and len(transparency) >= 6:
        # tRNS on a truecolour image names one transparent RGB triple
        mode = "RGBA"
    channels = CHANNELS[mode]
    stride = (width * samples * bit_depth + 7) // 8
    out = bytearray(width * height * channels)
    transparent_rgb = None
    if color_type == 2 and transparency and len(transparency) >= 6:
        if bit_depth == 16:
            transparent_rgb = tuple(transparency[i * 2] for i in range(3))
        else:
            transparent_rgb = tuple(transparency[0:3])
    for y in range(height):
        row = unfiltered[y * stride:(y + 1) * stride]
        for x in range(width):
            values = [_sample(row, x * samples + s, bit_depth)
                      for s in range(samples)]
            if transparent_grey is not None:
                values.append(0 if values[0] == transparent_grey else 255)
            elif transparent_rgb is not None:
                values.append(0 if tuple(values) == transparent_rgb else 255)
            start = (y * width + x) * channels
            out[start:start + channels] = bytes(values[:channels])
    return Image(width, height, mode, bytes(out),
                 meta={"transparency": bool(transparency)})


def _expand_palette(unfiltered, width, height, palette, transparency, bit_depth=8):
    """Colour type 3 -> RGBA (tRNS gives per-index alpha).

    Palette indexes can be 1, 2, 4 or 8 bits wide, so they go through the same
    sample reader as everything else instead of assuming one byte each.
    """
    alphas = list(transparency) if transparency else []
    stride = (width * bit_depth + 7) // 8
    out = bytearray(width * height * 4)
    for y in range(height):
        row = unfiltered[y * stride:(y + 1) * stride]
        for x in range(width):
            index = _raw_sample(row, x, bit_depth)
            start = index * 3
            if start + 3 > len(palette):
                raise PngError("palette index %d is out of range (%d entries)"
                               % (index, len(palette) // 3))
            alpha = alphas[index] if index < len(alphas) else 255
            pixel = (y * width + x) * 4
            out[pixel] = palette[start]
            out[pixel + 1] = palette[start + 1]
            out[pixel + 2] = palette[start + 2]
            out[pixel + 3] = alpha
    return Image(width, height, "RGBA", bytes(out), meta={"palette": True})


# ---------------------------------------------------------------- writing ----
def write(path, image):
    data = to_bytes(image)
    with open(path, "wb") as f:
        f.write(data)
    return path


def to_bytes(image):
    if image.mode == "P":
        raise PngError("palette images cannot be written by this module")
    color_type = COLOR_TYPE_OF[image.mode]
    header = struct.pack(">IIBBBBB", image.width, image.height, 8, color_type,
                         0, 0, 0)
    stride = image.width * image.channels
    raw = bytearray()
    for y in range(image.height):
        raw.append(0)                                  # filter 0: no filtering
        raw += image.row(y)
    return (SIGNATURE
            + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + _chunk(b"IEND", b""))


def _chunk(tag, body):
    return (struct.pack(">I", len(body)) + tag + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))


# --------------------------------------------------------------- factories ---
def solid(width, height, colour, mode="RGBA"):
    """A single-colour image: test fixtures and difference-map backgrounds."""
    image = Image(width, height, mode)
    channels = CHANNELS[mode]
    value = bytes((list(colour) + [255] * channels)[:channels])
    for y in range(height):
        for x in range(width):
            image.set(x, y, value)
    return image


def gradient(width, height, mode="RGB"):
    """A deterministic, non-uniform image: exercises every filter and channel."""
    image = Image(width, height, mode)
    channels = CHANNELS[mode]
    for y in range(height):
        for x in range(width):
            value = [(x * 7 + y * 3) % 256, (x * 3 + y * 11) % 256,
                     (x * 13 + y * 5) % 256, (x + y) % 256]
            image.set(x, y, tuple(value[:channels]))
    return image


def is_png(path):
    try:
        with open(path, "rb") as f:
            return f.read(8) == SIGNATURE
    except OSError:
        return False
