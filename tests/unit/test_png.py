"""Tests for the dependency-free PNG reader/writer (`awt/png.py`).

The decoder is checked three ways: against images this module writes itself,
against PNGs assembled byte by byte (every filter type, 1/2/4/16-bit depths,
palettes with tRNS), and - when Pillow happens to be installed - against a real
implementation, byte for byte. Without Pillow those cross-checks are skipped and
reported as skips, not as passes.
"""
import os
import struct
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tests.unit.harness import SkipTest, require            # noqa: E402
from tests.unit.fixtures import images                      # noqa: E402
from awt import png                                         # noqa: E402


def _tmp():
    return tempfile.mkdtemp(prefix="awt-png-")


def test_signature_and_helpers():
    require(png.SIGNATURE == b"\x89PNG\r\n\x1a\n", "the PNG signature changed")
    tmp = _tmp()
    path = images.write(png.solid(4, 3, (1, 2, 3), "RGB"), tmp, "solid.png")
    require(png.is_png(path), "is_png rejected our own file")
    require(not png.is_png(os.path.join(tmp, "missing.png")),
            "is_png must be False for a missing file")
    with open(os.path.join(tmp, "notpng.bin"), "wb") as f:
        f.write(b"GIF89a....")
    require(not png.is_png(os.path.join(tmp, "notpng.bin")),
            "is_png accepted a GIF")


def test_round_trip_every_mode():
    tmp = _tmp()
    for mode, colour in (("L", (77,)), ("LA", (77, 128)), ("RGB", (10, 200, 30)),
                         ("RGBA", (10, 200, 30, 90))):
        original = png.solid(7, 5, colour, mode=mode)
        path = os.path.join(tmp, "solid-%s.png" % mode)
        png.write(path, original)
        decoded = png.read(path)
        require(decoded.mode == mode, "%s came back as %s" % (mode, decoded.mode))
        require((decoded.width, decoded.height) == (7, 5), "geometry changed")
        require(bytes(decoded.pixels) == bytes(original.pixels),
                "%s pixels changed on a round trip" % mode)
        require(decoded.get(3, 2) == tuple(
            (list(colour) + [255] * 4)[:png.CHANNELS[mode]]), "get() disagrees")


def test_round_trip_gradient_and_meta():
    tmp = _tmp()
    original = png.gradient(33, 21, "RGB")
    path = images.write(original, tmp, "gradient.png")
    decoded = png.read(path)
    require(bytes(decoded.pixels) == bytes(original.pixels),
            "a non-uniform image did not survive the round trip")
    require(decoded.meta.get("origin") == path, "the origin is not recorded")
    require(any(entry.startswith("IHDR") for entry in decoded.meta["chunks"]),
            "the chunk list is not recorded")


def test_every_filter_type():
    lines = [bytes((x * 13 + y * 7) % 256 for x in range(9)) for y in range(5)]
    data = images.build_raw_png(9, 5, 8, 0, images.encode_rows(lines, [0, 1, 2, 3, 4]))
    decoded = png.from_bytes(data, origin="filters")
    require(decoded.mode == "L", "an 8-bit grey image must decode as L")
    for y, line in enumerate(lines):
        require(bytes(decoded.row(y)) == line,
                "row %d decoded wrong (filter %d)" % (y, y))


def test_sub_byte_depths_are_scaled():
    cases = [(1, 8, 0b10110010, (1, 0, 1, 1, 0, 0, 1, 0)),
             (2, 8, 0b01_00_11_10, (1, 0, 3, 2)),
             (4, 8, 0b00101111, (2, 15))]
    for bit_depth, width, byte, expected_raw in cases:
        scale = {1: 255, 2: 85, 4: 17}[bit_depth]
        per_byte = 8 // bit_depth
        row_bytes = bytes([byte] * ((width + per_byte - 1) // per_byte))
        data = images.build_raw_png(width, 1, bit_depth, 0, [(0, row_bytes)])
        decoded = png.from_bytes(data, origin="%d-bit" % bit_depth)
        got = tuple(decoded.get(x, 0)[0] for x in range(width))
        tiled = tuple(expected_raw[x % len(expected_raw)] for x in range(width))
        want = tuple(value * scale for value in tiled)
        require(got == want, "%d-bit decode: %r != %r" % (bit_depth, got, want))


def test_16_bit_samples_are_reduced_and_recorded():
    rows = [(0, b"".join(struct.pack(">HHH", x * 257, 65535 - x * 257, 128 * 257)
                         for x in range(3))) for _ in range(2)]
    data = images.build_raw_png(3, 2, 16, 2, rows)
    decoded = png.from_bytes(data, origin="16-bit")
    require(decoded.mode == "RGB", "16-bit truecolour must decode as RGB")
    require("16-bit" in (decoded.meta.get("sample_reduction") or ""),
            "the reduction to 8 bits must be recorded: %r" % decoded.meta)
    require(decoded.get(0, 0) == (0, 255, 128), "high bytes not used: %r"
            % (decoded.get(0, 0),))


def test_palette_and_transparency():
    palette = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 250, 250, 250])
    rows = [(0, bytes([0, 1, 2, 3, 0, 1, 2, 3])) for _ in range(2)]
    data = images.build_raw_png(8, 2, 8, 3, rows, palette=palette,
                                trns=bytes([0, 128, 255, 64]))
    decoded = png.from_bytes(data, origin="palette")
    require(decoded.mode == "RGBA", "palette images decode to RGBA")
    require(decoded.get(0, 0) == (255, 0, 0, 0), "tRNS alpha ignored: %r"
            % (decoded.get(0, 0),))
    require(decoded.get(1, 0) == (0, 255, 0, 128), "tRNS alpha wrong: %r"
            % (decoded.get(1, 0),))
    require(decoded.get(3, 1) == (250, 250, 250, 64), "tRNS alpha wrong: %r"
            % (decoded.get(3, 1),))


def test_sub_byte_palette_indexes_are_not_scaled():
    """The classic trap: a 4-bit index of 15 means entry 15, not white."""
    palette = bytes([10, 20, 30] * 16)
    rows = [(0, bytes([0b00011111, 0b00000001])) for _ in range(1)]
    data = images.build_raw_png(4, 1, 4, 3, rows, palette=palette)
    decoded = png.from_bytes(data, origin="palette-4bit")
    got = [decoded.get(x, 0)[:3] for x in range(4)]
    entries = [(palette[i * 3], palette[i * 3 + 1], palette[i * 3 + 2])
               for i in (1, 15, 0, 1)]
    require(got == entries, "4-bit palette indexes decoded as %r, expected %r"
            % (got, entries))


def test_phys_gamma_and_text_chunks_are_recorded():
    extra = (images.chunk(b"pHYs", struct.pack(">IIB", 2835, 2835, 1))
             + images.chunk(b"gAMA", struct.pack(">I", 45455))
             + images.chunk(b"tEXt", b"Comment\x00made by a test"))
    data = images.build_raw_png(4, 2, 8, 0, [(0, bytes(4)), (0, bytes(4))],
                                extra=extra)
    decoded = png.from_bytes(data, origin="meta")
    require(decoded.meta["phys"]["dpi"] == (72.01, 72.01),
            "pHYs was not converted to dpi: %r" % decoded.meta.get("phys"))
    require(abs(decoded.meta["gamma"] - 0.45455) < 1e-6, "gAMA not recorded")
    require(decoded.meta["text"]["Comment"] == "made by a test", "tEXt not recorded")


def test_unsupported_or_corrupt_input_is_refused():
    lines = [bytes(8)] * 5
    good = images.build_raw_png(8, 5, 8, 0, [(0, line) for line in lines])
    cases = [
        ("not a PNG", b"GIF89a\x00\x00\x00\x00"),
        ("interlaced", images.build_raw_png(8, 5, 8, 0, [(0, line) for line in lines],
                                            interlace=1)),
        ("truncated", good[:40]),
        ("bad CRC", images.build_raw_png(8, 5, 8, 0, [(0, line) for line in lines],
                                         corrupt_crc=True)),
        ("no IDAT", png.SIGNATURE + images.chunk(
            b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 0, 0, 0, 0))
         + images.chunk(b"IEND", b"")),
        ("palette index out of range", images.build_raw_png(
            4, 1, 8, 3, [(0, bytes([0, 1, 9, 3]))], palette=bytes([1, 2, 3] * 2))),
        ("JPEG bytes", b"\xff\xd8\xff\xe0" + b"0" * 40),
    ]
    for label, data in cases:
        try:
            png.from_bytes(data, origin=label)
        except png.PngError as exc:
            require(label.split()[0].lower() in str(exc).lower()
                    or len(str(exc)) > 10,
                    "the reason for refusing %r is unhelpful: %s" % (label, exc))
        else:
            raise AssertionError("%s must be refused, not decoded" % label)
    require("interlaced" in str(_error_for(cases[1][1])).lower(),
            "the interlace refusal must say why")


def _error_for(data):
    try:
        png.from_bytes(data, origin="x")
    except png.PngError as exc:
        return str(exc)
    return ""


def test_crop_convert_and_bounds():
    image = png.gradient(20, 12, "RGBA")
    cropped = image.crop((4, 3, 8, 5))
    require((cropped.width, cropped.height) == (8, 5), "crop size wrong")
    require(cropped.get(0, 0) == image.get(4, 3), "crop offset wrong")
    require("cropped_from" in cropped.meta, "the crop is not recorded in meta")
    for bad in ((-1, 0, 4, 4), (0, 0, 0, 4), (18, 0, 8, 8)):
        try:
            image.crop(bad)
        except png.PngError:
            continue
        raise AssertionError("crop %r must be refused" % (bad,))
    grey = image.convert("L")
    require(grey.mode == "L" and grey.channels == 1, "convert to L failed")
    require(grey.get(5, 5) == _luma(image.get(5, 5)),
            "luma weights changed: %r vs %r" % (grey.get(5, 5), _luma(image.get(5, 5))))
    require(image.convert("RGB").mode == "RGB", "convert to RGB failed")
    try:
        image.get(20, 0)
    except IndexError:
        pass
    else:
        raise AssertionError("out-of-range pixel access must raise IndexError")


def _luma(rgba):
    return ((rgba[0] * 299 + rgba[1] * 587 + rgba[2] * 114) // 1000,)


def test_luma_composites_transparency_over_white():
    image = png.Image(2, 1, "RGBA")
    image.set(0, 0, (0, 0, 0, 0))          # fully transparent black
    image.set(1, 0, (0, 0, 0, 255))        # opaque black
    luma = image.luma()
    require(luma.get(0, 0) == (255,), "transparent must composite to white: %r"
            % (luma.get(0, 0),))
    require(luma.get(1, 0) == (0,), "opaque black must stay black")


def test_resize_box_averages():
    image = png.Image(4, 2, "L")
    for x, value in enumerate((0, 100, 200, 255)):
        image.set(x, 0, (value,))
        image.set(x, 1, (value,))
    smaller = image.resize_box(2, 1)
    require(smaller.get(0, 0) == (50,), "box average wrong: %r" % (smaller.get(0, 0),))
    require(smaller.get(1, 0) == (227,), "box average wrong: %r" % (smaller.get(1, 0),))
    require(smaller.meta["resize_method"] == "box-average",
            "the resize method must be recorded")
    require(smaller.meta["resized_from"] == "4x2", "the source size must be recorded")
    try:
        image.resize_box(0, 1)
    except png.PngError:
        pass
    else:
        raise AssertionError("a zero-sized resize must be refused")


def test_image_rejects_bad_construction():
    for width, height, mode in ((0, 4, "RGB"), (4, -1, "RGB"), (4, 4, "CMYK")):
        try:
            png.Image(width, height, mode)
        except png.PngError:
            continue
        raise AssertionError("Image(%r, %r, %r) must be refused" % (width, height, mode))
    try:
        png.Image(2, 2, "RGB", pixels=bytes(3))
    except png.PngError:
        pass
    else:
        raise AssertionError("a pixel buffer of the wrong length must be refused")


def test_pillow_agrees_byte_for_byte_when_installed():
    try:
        from PIL import Image as PillowImage
    except ImportError:
        raise SkipTest("Pillow not installed: the decode cross-check cannot run")
    tmp = _tmp()
    base = png.gradient(11, 7, "RGB")
    cases = []
    grey = PillowImage.new("L", (9, 5))
    grey.putdata([(x * 29 + y * 13) % 256 for y in range(5) for x in range(9)])
    cases.append((grey, "L"))
    pillow_rgb = PillowImage.frombytes("RGB", (base.width, base.height),
                                       bytes(base.pixels))
    cases.append((pillow_rgb, "RGB"))
    cases.append((pillow_rgb.convert("RGBA"), "RGBA"))
    cases.append((grey.convert("LA"), "LA"))
    cases.append((pillow_rgb.convert("P", palette=PillowImage.ADAPTIVE, colors=16),
                  "RGBA"))
    binary = PillowImage.new("1", (13, 5))
    binary.putdata([1 if (x * 3 + y) % 2 else 0 for y in range(5) for x in range(13)])
    cases.append((binary, "L"))
    for index, (image, expected_mode) in enumerate(cases):
        path = os.path.join(tmp, "pillow-%d.png" % index)
        image.save(path)
        decoded = png.read(path)
        require(decoded.mode == expected_mode,
                "case %d: expected mode %s, got %s" % (index, expected_mode,
                                                       decoded.mode))
        reference = PillowImage.open(path)
        reference.load()
        reference = reference.convert(decoded.mode)
        require(reference.size == (decoded.width, decoded.height),
                "case %d: geometry differs" % index)
        require(bytes(reference.tobytes()) == bytes(decoded.pixels),
                "case %d (%s): pixels differ from Pillow's decode" % (index,
                                                                      expected_mode))


def test_pillow_can_read_what_we_write():
    try:
        from PIL import Image as PillowImage
    except ImportError:
        raise SkipTest("Pillow not installed")
    tmp = _tmp()
    original = images.desktop(24, 16)
    path = images.write(original, tmp, "ours.png")
    reference = PillowImage.open(path)
    reference.load()
    require(reference.size == (24, 16), "Pillow read a different size")
    require(bytes(reference.convert("RGB").tobytes()) == bytes(original.pixels),
            "Pillow decoded our PNG differently")
