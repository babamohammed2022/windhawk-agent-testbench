"""Unit tests for `awt.pdb`: the pure-python MSF 7.0 / DBI / GSI reader.

The images are synthetic (built by `tests/unit/fixtures`), but they are laid out
the way Microsoft's public PDBs are: MSF 7.0 container, PDB-info stream, DBI
stream with a module-info substream, section headers in the optional debug
header, and public/global symbol streams framed as GSI.
"""
import os
import tempfile

from awt import pdb as pdb_mod
from awt import pe as pe_mod
from tests.unit.fixtures import build_pe, build_pdb, _guid_bytes

SHELL_SYMBOLS = [
    ("?ShowStartView@XamlLauncher@@UEAAJW4IMMERSIVELAUNCHERSHOWMETHOD@@"
     "W4IMMERSIVELAUNCHERSHOWFLAGS@@@Z", 1, 0x1234),
    ("?Invoke@CTaskFrame@@UEAAJXZ", 1, 0x5678),
    ("DllGetClassObject", 1, 0x9ABC),
    ("?s_sharedState@XamlLauncher@@0PEAVSharedState@@EB", 3, 0x100),
]


def _pdb(**kw):
    return pdb_mod.from_bytes(build_pdb(**kw), source="fixture")


def test_container_and_identity():
    age, guid = 4, _guid_bytes("TwinUI.PCShell.pdb")
    f = _pdb(age=age, guid=guid)
    info = f.info()
    assert info["pdb_signature_value"], info
    assert info["age"] == age, info
    assert info["container"]["block_size"] >= 512
    assert info["machine"] in ("x86-64", None), info["machine"]
    assert info["symbol_count"] >= len(SHELL_SYMBOLS)
    areas = info["record_areas"]
    assert areas and all(a.get("method") for a in areas), areas


def test_symbols_and_rvas():
    f = _pdb()
    names = {s["name"] for s in f.symbols()}
    for name, _seg, _off in SHELL_SYMBOLS:
        assert name in names, "%s missing from %s" % (name, sorted(names)[:4])
    by_name = f.by_name()
    # fixture section table: .text va=0x1000, .data va=0x21000, .rdata va=0x23000
    sections = {s["name"]: s["virtual_address"] for s in f.sections}
    assert sections == {".text": 0x1000, ".data": 0x21000, ".rdata": 0x23000}, sections
    sym = by_name["?Invoke@CTaskFrame@@UEAAJXZ"][0]
    assert sym["rva"] == sections[".text"] + 0x5678, hex(sym["rva"] or 0)
    data = by_name["?s_sharedState@XamlLauncher@@0PEAVSharedState@@EB"][0]
    assert data["rva"] == sections[".rdata"] + 0x100, hex(data["rva"] or 0)
    assert data["kind"] in ("public", "global-data", "local-data"), data["kind"]


def test_global_only_symbols_are_found():
    f = _pdb()
    names = {s["name"] for s in f.symbols()}
    assert "?StaticHelper@`anonymous namespace'@@YAXXZ" in names, sorted(names)
    assert "?g_taskbarCount@@3HA" in names


def test_empty_symbol_streams_do_not_crash():
    f = _pdb(public_symbols=[], global_symbols=[])
    assert f.symbols() == [], f.symbols()
    assert f.by_name() == {}
    assert f.names() == []


def test_unknown_segment_gives_no_rva():
    f = _pdb(public_symbols=[("?Weird@C@@QEAAXXZ", 9, 0x10)])
    sym = f.by_name()["?Weird@C@@QEAAXXZ"][0]
    assert sym["rva"] is None, sym
    assert f.warnings is not None


def test_proc_layout_probe():
    # PROC32 is documented with and without a `typind` field; the reader probes
    # both layouts instead of trusting one. Both must give the same answer.
    a = _pdb(proc_layout="cvinfo-typind")
    b = _pdb(proc_layout="no-typind")
    na = {s["name"]: s["rva"] for s in a.symbols()}
    nb = {s["name"]: s["rva"] for s in b.symbols()}
    assert na == nb, (na, nb)
    layouts = {area.get("proc_layout") for area in a.info()["record_areas"]
               if area.get("proc_layout")}
    assert layouts, a.info()["record_areas"]


def test_index_round_trip():
    f = _pdb()
    rows = f.index_rows()
    assert rows and all(len(r) == 6 for r in rows), rows[:2]
    restored = pdb_mod.PdbFile.from_index_rows(rows, source="cache")
    assert {s["name"]: s["rva"] for s in restored.symbols()} == \
           {s["name"]: s["rva"] for s in f.symbols()}
    assert restored.warnings, "a cached view must say it was not re-read"


def test_rejects_non_pdb():
    for garbage in (b"", b"Microsoft C/C++ MSF 7.00" + b"\0" * 10,
                    build_pe()):
        try:
            pdb_mod.from_bytes(garbage, source="garbage")
        except Exception as exc:      # PdbError and its struct/MSF causes
            assert isinstance(exc, Exception)
            continue
        raise AssertionError("garbage was accepted as a PDB")


def test_open_from_disk_and_is_pdb():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "TwinUI.PCShell.pdb")
        with open(path, "wb") as f:
            f.write(build_pdb())
        assert pdb_mod.is_pdb(path) is True
        pdb_file = pdb_mod.open(path)
        assert pdb_file.source == path
        assert pdb_file.symbols()
        pe_path = os.path.join(tmp, "twinui.pcshell.dll")
        with open(pe_path, "wb") as f:
            f.write(build_pe())
        assert pdb_mod.is_pdb(pe_path) is False


def test_pe_and_pdb_signatures_agree():
    """The two halves of a download: PE debug directory -> PDB store key."""
    guid = _guid_bytes("TwinUI.PCShell.pdb")
    image = pe_mod.read_bytes(build_pe(guid=guid, age=2))
    cv = image.codeview()
    f = _pdb(guid=guid, age=2)
    assert cv["signature"] == "%s%d" % (f.guid.upper(), f.age), (cv, f.guid, f.age)


def test_analyzer_documents_its_choices():
    a = pdb_mod.ANALYZER
    assert a["name"] == "awt-pdb"
    assert "llvm-pdbutil" in a["alternatives_evaluated"]
    assert "pdbparse" in a["alternatives_evaluated"]
