"""Unit tests for `awt.pe`: the PE/COFF reader the symbol pipeline depends on.

Everything here runs on synthetic images built by `tests/unit/fixtures`, so it
needs no Windows, no network and no Microsoft binary.
"""
import os
import struct
import tempfile

from awt import pe as pe_mod
from tests.unit.fixtures import build_pe, _guid_bytes


def _image(**kw):
    return pe_mod.read_bytes(build_pe(**kw), source="fixture")


def test_pe_headers():
    image = _image(timestamp=0xBC86BB4D, size_of_image=0x9A5000)
    assert image.machine_name == "x86-64", image.machine_name
    assert image.timestamp == 0xBC86BB4D
    assert image.size_of_image == 0x9A5000
    assert image.sections, "a PE with no section table cannot map RVAs"
    assert image.sections[0]["name"] == ".rdata"


def test_pe_signature_format():
    # The symbol-store key is `<TIMESTAMP:08X><SizeOfImage:x>`: upper-case,
    # zero-padded timestamp followed by a lower-case, unpadded size. Microsoft's
    # server accepts it case-insensitively, but the shape must be exact.
    sig = pe_mod.pe_signature(0xBC86BB4D, 0x9A5000)
    assert sig == "BC86BB4D9a5000", sig
    image = _image()
    assert image.summary()["pe_signature"] == sig


def test_codeview_identity():
    guid = _guid_bytes("TwinUI.PCShell.pdb")
    image = _image(pdb_name="TwinUI.PCShell.pdb", guid=guid, age=3)
    cv = image.codeview()
    assert cv["format"] == "RSDS", cv
    assert cv["pdb_name"] == "TwinUI.PCShell.pdb", cv
    assert cv["age"] == 3
    assert len(cv["guid"]) == 32 and all(c in "0123456789ABCDEF" for c in cv["guid"])
    assert cv["signature"] == pe_mod.pdb_signature(cv["guid"], 3)
    # the mixed-endian GUID rendering is what makes symbol-store paths look odd
    d1, d2, d3 = struct.unpack_from("<IHH", guid, 0)
    assert cv["guid"].startswith("%08X%04X%04X" % (d1, d2, d3)), cv["guid"]


def test_pdb_path_with_directories():
    image = _image(pdb_name="C:\\src\\TwinUI.PCShell.pdb")
    # build_pe encodes whatever it is given; the reader must reduce it to a file
    # name for the symbol-store path while keeping the original for the report.
    cv = image.codeview()
    assert cv["pdb_name"].endswith(".pdb")
    assert "\\" not in cv["pdb_name"] and "/" not in cv["pdb_name"]


def test_no_debug_directory():
    image = _image(with_debug_directory=False)
    assert image.codeview() == {}, "an image without a debug directory has no PDB id"


def test_rva_to_offset():
    image = _image()
    section = image.sections[0]
    rva = section["virtual_address"] + 0x40
    off = image.rva_to_offset(rva, 4)
    assert off is not None
    assert off == section["raw_ptr"] + 0x40, (off, section)
    assert image.rva_to_offset(0xFFFFF000, 4) is None


def test_rejects_non_pe():
    for garbage in (b"", b"MZ" + b"\0" * 100, b"not a pe at all" * 20):
        try:
            pe_mod.read_bytes(garbage)
        except pe_mod.PeError:
            continue
        raise AssertionError("garbage input was accepted as a PE image")


def test_read_from_disk():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "twinui.pcshell.dll")
        with open(path, "wb") as f:
            f.write(build_pe())
        image = pe_mod.read(path)
        assert image.source == path
        assert image.codeview()["pdb_name"] == "TwinUI.PCShell.pdb"


def test_guid_length_validation():
    try:
        pe_mod.guid_to_symbol_server(b"\x01\x02\x03")
    except pe_mod.PeError:
        return
    raise AssertionError("a short GUID was accepted")


def test_machine_names():
    assert pe_mod.MACHINE_NAMES.get(0x8664) == "x86-64"
    assert pe_mod.MACHINE_NAMES.get(0x14C) == "x86"
    assert pe_mod.MACHINE_NAMES.get(0xAA64) == "arm64"
