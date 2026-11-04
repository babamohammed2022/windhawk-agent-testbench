"""Synthetic PDB/PE fixtures used by the unit tests.

The sandbox cannot reach `msdl.microsoft.com`, so the readers are exercised
against files that are *structurally real*: a valid MSF 7.0 container with a DBI
stream, an optional debug header, section headers, a GSI-framed public symbol
stream and a global symbol stream, and a valid PE32+ image carrying an RSDS
CodeView record. Field layouts follow Microsoft's CodeView/PDB definitions as
mirrored by LLVM (see awt/pdb/reader.py).

What this does NOT prove: that a real Microsoft PDB parses identically. That is
checked on a machine with network access by `python3 -m awt.symbols
--cross-check` (llvm-pdbutil) and by a live matrix run; see docs/symbol-matrix.md.
"""
import struct

# --- MSF --------------------------------------------------------------------
MSF_MAGIC = b"Microsoft C/C++ MSF 7.00\r\n\x1aDS\0\0\0"

# --- CodeView record types ---------------------------------------------------
S_END = 0x0006
S_OBJNAME = 0x1101
S_LDATA32 = 0x110C
S_GDATA32 = 0x110D
S_PUB32 = 0x110E
S_LPROC32 = 0x110F
S_GPROC32 = 0x1110

GSI_SIGNATURE = 0xFFFFFFFF
GSI_VERSION = 0xB06D9093


def _record(rec_type: int, body: bytes) -> bytes:
    length = len(body) + 2  # the length field counts the type field too
    if length % 2:
        body += b"\0"
        length += 1
    return struct.pack("<HH", length, rec_type) + body


def _cstr(name: str) -> bytes:
    return name.encode("utf-8") + b"\0"


def pub32(name: str, segment: int, offset: int, flags: int = 0x02) -> bytes:
    """S_PUB32: flags, offset, segment, name."""
    return _record(S_PUB32, struct.pack("<IIH", flags, offset, segment) + _cstr(name))


def gproc32(name: str, segment: int, offset: int, length: int = 0x40,
            type_index: int = 0x1000) -> bytes:
    """S_GPROC32: parent, end, next, len, dbgStart, dbgEnd, typind, off, seg,
    flags, name (Microsoft cvinfo.h PROC32)."""
    body = struct.pack("<IIIIIII", 0, 0, 0, length, 0, 0, type_index)
    body += struct.pack("<IH", offset, segment)
    body += struct.pack("<B", 0)
    return _record(S_GPROC32, body + _cstr(name))


def gproc32_no_typind(name: str, segment: int, offset: int, length: int = 0x40) -> bytes:
    """The layout variant that omits `typind`, for the reader's layout probe."""
    body = struct.pack("<IIIIII", 0, 0, 0, length, 0, 0)
    body += struct.pack("<IH", offset, segment)
    body += struct.pack("<B", 0)
    return _record(S_GPROC32, body + _cstr(name))


def gdata32(name: str, segment: int, offset: int, type_index: int = 0x74) -> bytes:
    """S_GDATA32: typeId, offset, segment, name."""
    return _record(S_GDATA32, struct.pack("<IIH", type_index, offset, segment) + _cstr(name))


def objname(name: str) -> bytes:
    return _record(S_OBJNAME, struct.pack("<I", 0) + _cstr(name))


def gsi_stream(records: bytes, hash_records: bytes = b"", bucket_count: int = 0) -> bytes:
    """A public/global symbol stream: GSI header, bucket offsets, hash records,
    bitmap, then the raw records."""
    hr_size = len(hash_records)
    bucket_offsets = struct.pack("<%dI" % (hr_size // 8), *([0] * (hr_size // 8))) \
        if hr_size else b""
    bitmap = b"\0" * (((bucket_count + 7) // 8 + 3) // 4 * 4)
    return (struct.pack("<IIII", GSI_SIGNATURE, GSI_VERSION, hr_size, bucket_count)
            + bucket_offsets + hash_records + bitmap + records)


def sections_stream(sections) -> bytes:
    """IMAGE_SECTION_HEADER array. `sections` = [(name, vsize, va, rawsize, rawptr)]."""
    out = b""
    for name, vsize, va, rawsize, rawptr in sections:
        out += name.encode("latin-1").ljust(8, b"\0")
        out += struct.pack("<IIIIIIHHI", vsize, va, rawsize, rawptr, 0, 0, 0, 0, 0x60000020)
    return out


def _dbi_stream(global_stream: int, public_stream: int, sym_record_stream: int,
                module_info: bytes, optional_debug_header: list,
                machine: int = 0x8664) -> bytes:
    header = struct.pack(
        "<iIIHHHHHHiiiiiIiiHHI",
        -1,             # sig
        19990903,       # ver (DBIImpv70)
        1,              # age
        global_stream,  # gsi
        0x1000 | 1,     # build number (newVersionFlag | minor)
        public_stream,  # pub
        0,              # pdbsig (pdb dll version)
        sym_record_stream,
        0,              # pdbrbld
        len(module_info),   # modinfo
        0,                  # seccon
        0,                  # secmap
        0,                  # filinfo
        0,                  # tsmap
        0,                  # mfcindex
        len(optional_debug_header) * 2,  # dbghdr
        0,                  # ecinfo
        0,                  # flags
        machine,            # machine
        0,                  # pad
    )
    opt = struct.pack("<%dH" % len(optional_debug_header), *optional_debug_header)
    return header + module_info + opt


def _module_info(module_name: str, object_name: str, sym_stream: int) -> bytes:
    body = struct.pack("<i", 0)                       # opened (unused)
    body += struct.pack("<HHiiIHHII", 1, 0, 0, 0x40, 0x60000020, 0, 0, 0, 0)  # seccon
    body += struct.pack("<HH", 0, sym_stream)         # flags, module stream
    body += struct.pack("<III", 0x100, 0, 0)          # cbSyms, cbOldLines, cbLines
    body += struct.pack("<HH", 1, 0)                  # nFiles, pad
    body += struct.pack("<III", 0, 0, 0)              # offset, cbSrcFiles, cbPdbPath
    body += _cstr(module_name) + _cstr(object_name)
    body += b"\0" * ((-len(body)) % 4)
    return body


def _pdb_info_stream(age: int = 1, guid: bytes = None, named: dict = None) -> bytes:
    guid = guid if guid is not None else bytes(range(16))
    out = struct.pack("<III", 20000404, 0x5A4C4F52, age) + guid
    named = named if named is not None else {"/Names": 5}
    names = list(named)
    blob = b"".join(_cstr(n) for n in names)
    offsets = []
    pos = 0
    for n in names:
        offsets.append(pos)
        pos += len(n) + 1
    out += struct.pack("<I", len(names))
    out += struct.pack("<%dI" % len(names), *offsets)
    out += struct.pack("<I", len(blob)) + blob
    out += struct.pack("<II", 0, 0)  # empty hash table (bucket count, bitmap size)
    return out


def _tpi_stream() -> bytes:
    # TPI header: version, header size, index begin/end, hash offsets, etc.
    return struct.pack("<IIIIIIIII", 20040203, 56, 0x1000, 0x1000, 0, 0, 0, 0, 0)


def build_msf(streams: dict, block_size: int = 1024) -> bytes:
    """Lay streams out into an MSF 7.0 container and return the file bytes."""
    num_streams = max(streams) + 1
    sizes = [len(streams.get(i, b"")) for i in range(num_streams)]

    # block 0 = super block, block 1 = free block map, then stream data,
    # then the directory, then the block map.
    blocks = [b"", b"\xff" * block_size]
    stream_blocks = []
    for i in range(num_streams):
        data = streams.get(i, b"")
        indices = []
        for off in range(0, len(data), block_size):
            indices.append(len(blocks))
            blocks.append(data[off:off + block_size].ljust(block_size, b"\0"))
        stream_blocks.append(indices)

    directory = struct.pack("<I", num_streams)
    directory += struct.pack("<%dI" % num_streams,
                             *[s if s else 0xFFFFFFFF for s in sizes])
    for indices in stream_blocks:
        if indices:
            directory += struct.pack("<%dI" % len(indices), *indices)
    dir_blocks = []
    for off in range(0, len(directory), block_size):
        dir_blocks.append(len(blocks))
        blocks.append(directory[off:off + block_size].ljust(block_size, b"\0"))
    block_map_addr = len(blocks)
    block_map = struct.pack("<%dI" % len(dir_blocks), *dir_blocks)
    blocks.append(block_map.ljust(block_size, b"\0"))

    super_block = MSF_MAGIC + struct.pack(
        "<IIIIII", block_size, 1, len(blocks), len(directory), 0, block_map_addr)
    blocks[0] = super_block.ljust(block_size, b"\0")
    return b"".join(blocks)


def build_pdb(public_symbols=None, global_symbols=None, sections=None,
              module="twinui.pcshell.dll", age=1, guid=None, machine=0x8664,
              hash_records=b"", bucket_count=0, proc_layout="cvinfo-typind") -> bytes:
    """A complete, self-consistent PDB 7.0 image for tests.

    Default content mirrors the kind of symbols a shell mod asks for: a
    decorated C++ member function, a plain C export, a static (global-only)
    function and a data symbol.
    """
    sections = sections if sections is not None else [
        (".text", 0x1F000, 0x1000, 0x1F000, 0x400),
        (".data", 0x2000, 0x21000, 0x1000, 0x20000),
        (".rdata", 0x8000, 0x23000, 0x8000, 0x21000),
    ]
    public_symbols = public_symbols if public_symbols is not None else [
        ("?ShowStartView@XamlLauncher@@UEAAJW4IMMERSIVELAUNCHERSHOWMETHOD@@W4IMMERSIVELAUNCHERSHOWFLAGS@@@Z", 1, 0x1234),
        ("?Invoke@CTaskFrame@@UEAAJXZ", 1, 0x5678),
        ("DllGetClassObject", 1, 0x9ABC),
        ("?s_sharedState@XamlLauncher@@0PEAVSharedState@@EB", 3, 0x100),
    ]
    global_symbols = global_symbols if global_symbols is not None else [
        ("?StaticHelper@`anonymous namespace'@@YAXXZ", 1, 0x4321),
        ("?g_taskbarCount@@3HA", 2, 0x20),
    ]

    pub_records = objname(module)
    for name, segment, offset in public_symbols:
        pub_records += pub32(name, segment, offset)
    pub_records += _record(S_END, b"")

    proc_encoder = gproc32 if proc_layout == "cvinfo-typind" else gproc32_no_typind
    glob_records = b""
    for entry in global_symbols:
        name, segment, offset = entry
        is_data = segment in (2, 3) and not name.startswith("?Static")
        glob_records += gdata32(name, segment, offset) if is_data \
            else proc_encoder(name, segment, offset)
    glob_records += _record(S_END, b"")

    # Stream assignment: 1 info, 2 tpi, 3 dbi, 4 ipi, 5 sections, 6 public, 7 global.
    optional_debug_header = [0xFFFF] * 12
    optional_debug_header[5] = 5     # section headers (original index 10 unused)
    streams = {
        1: _pdb_info_stream(age=age, guid=guid),
        2: _tpi_stream(),
        4: _tpi_stream(),
        5: sections_stream(sections),
        6: gsi_stream(pub_records, hash_records=hash_records,
                      bucket_count=bucket_count),
        7: gsi_stream(glob_records),
    }
    streams[3] = _dbi_stream(global_stream=7, public_stream=6, sym_record_stream=8,
                             module_info=_module_info(module, module + ".obj", 9),
                             optional_debug_header=optional_debug_header,
                             machine=machine)
    return build_msf(streams)


def build_pe(timestamp=0xBC86BB4D, size_of_image=0x9A5000, machine=0x8664,
             pdb_name="TwinUI.PCShell.pdb", guid=None, age=1,
             with_debug_directory=True) -> bytes:
    """A minimal PE32+ image whose debug directory points at an RSDS record."""
    guid = _guid_bytes(pdb_name) if guid is None else guid
    file_alignment = 0x200
    headers_size = 0x400
    rdata_raw = headers_size
    rdata_va = 0x2000
    debug_dir_rva = rdata_va
    rsds_rva = rdata_va + 0x100
    rsds_raw = rdata_raw + 0x100

    dos = bytearray(headers_size)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x80)

    nt = bytearray()
    nt += b"PE\0\0"
    nt += struct.pack("<HHIIIHH", machine, 1, timestamp, 0, 0, 0xF0, 0x22)
    opt = bytearray()
    opt += struct.pack("<H", 0x20B)          # PE32+
    opt += struct.pack("<BB", 14, 0)         # linker version
    opt += struct.pack("<III", 0x1000, 0, 0)  # sizes of code/init/uninit
    opt += struct.pack("<I", 0x1000)         # entry point
    opt += struct.pack("<I", 0x1000)         # base of code
    opt += struct.pack("<Q", 0x180000000)    # image base
    opt += struct.pack("<II", 0x1000, file_alignment)
    opt += struct.pack("<HHHHHH", 6, 0, 0, 0, 6, 0)
    opt += struct.pack("<I", 0)              # win32 version
    opt += struct.pack("<II", size_of_image, headers_size)
    opt += struct.pack("<IH", 0, 2)          # checksum, subsystem (GUI)
    opt += struct.pack("<H", 0x160)          # dll characteristics
    opt += struct.pack("<QQQQ", 0x100000, 0x1000, 0x100000, 0x1000)
    opt += struct.pack("<II", 0, 16)         # loader flags, number of rva and sizes
    directories = [(0, 0)] * 16
    if with_debug_directory:
        directories[6] = (debug_dir_rva, 28)
    for va, size in directories:
        opt += struct.pack("<II", va, size)
    nt += opt
    # section table
    nt += b".rdata\0\0".ljust(8, b"\0")
    nt += struct.pack("<IIIIIIHHI", 0x200, rdata_va, 0x200, rdata_raw, 0, 0, 0, 0,
                      0x40000040)
    image = bytearray(headers_size + 0x200)
    image[0:len(dos)] = dos
    image[0x80:0x80 + len(nt)] = nt

    debug_dir = struct.pack("<IIHHIIII", 0, timestamp, 0, 0, 2, 0x60,
                            rsds_rva, rsds_raw)
    image[rdata_raw:rdata_raw + len(debug_dir)] = debug_dir

    if len(guid) != 16:
        raise ValueError("guid must be 16 raw bytes")
    rsds = b"RSDS" + guid + struct.pack("<I", age) + pdb_name.encode("ascii") + b"\0"
    image[rsds_raw:rsds_raw + len(rsds)] = rsds
    return bytes(image)


def _guid_bytes(seed: str) -> bytes:
    """A stable, valid GUID derived from a string (Data1/2/3 little-endian)."""
    import hashlib
    digest = hashlib.sha1(seed.encode("utf-8")).digest()
    d1 = int.from_bytes(digest[0:4], "big")
    d2 = int.from_bytes(digest[4:6], "big")
    d3 = int.from_bytes(digest[6:8], "big")
    return struct.pack("<IHH", d1, d2, d3) + digest[8:16]
