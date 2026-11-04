"""CodeView symbol records and the GSI (global/public symbol index) containers.

Record layouts follow Microsoft's CodeView definitions as mirrored by LLVM
(llvm/DebugInfo/PDB/Raw/RawTypes.h). Only the records needed to answer "does
this symbol exist, and at which RVA" are decoded; everything else is skipped by
length, which keeps the reader tolerant of record types it does not know.
"""
import struct

__all__ = ["RecordError", "iter_records", "decode_symbol", "locate_record_area",
           "GSI_SIGNATURE", "GSI_VERSION", "Symbol"]

# --- record types (subset that matters here) ---------------------------------
S_END = 0x0006
S_OBJNAME = 0x1101
S_THUNK32 = 0x1102
S_BLOCK32 = 0x1103
S_LDATA32 = 0x110C
S_GDATA32 = 0x110D
S_PUB32 = 0x110E
S_LPROC32 = 0x110F
S_GPROC32 = 0x1110
S_PROCREFT = 0x1127
S_LPROCREFT = 0x1128
S_COMPILE3 = 0x113C
S_GMANPROC = 0x1121
S_LMANPROC = 0x1122
S_LPROC32_ID = 0x1146
S_GPROC32_ID = 0x1147
S_POGOTOCOMPILERID = 0x113E
S_LINKERINFO = 0x1123
S_SEPCODE = 0x1132
S_FILESTATIC = 0x1135
S_LOCAL = 0x1137
S_DEFRANGE = 0x113A
S_FRAMEPROC = 0x1142

PROC_RECORDS = {S_LPROC32, S_GPROC32, S_GMANPROC, S_LMANPROC, S_LPROC32_ID,
                S_GPROC32_ID}
DATA_RECORDS = {S_LDATA32, S_GDATA32, S_FILESTATIC}

KIND_NAMES = {
    S_PUB32: "public",
    S_GPROC32: "global-proc",
    S_LPROC32: "local-proc",
    S_GPROC32_ID: "global-proc-id",
    S_LPROC32_ID: "local-proc-id",
    S_GMANPROC: "global-managed-proc",
    S_LMANPROC: "local-managed-proc",
    S_GDATA32: "global-data",
    S_LDATA32: "local-data",
    S_THUNK32: "thunk",
    S_OBJNAME: "objname",
    S_PROCREFT: "proc-ref",
    S_LPROCREFT: "local-proc-ref",
    S_COMPILE3: "compile3",
}

GSI_SIGNATURE = 0xFFFFFFFF
GSI_VERSION = 0xB06D9093

# CV_PUBSYMFLAGS
PUBSYM_CODE = 0x0001
PUBSYM_FUNCTION = 0x0002
PUBSYM_MANAGED = 0x0004
PUBSYM_MSIL = 0x0008


class RecordError(ValueError):
    pass


class Symbol(dict):
    """A decoded symbol record: name, kind, segment, offset and (later) RVA."""

    __getattr__ = dict.get

    def __repr__(self):  # pragma: no cover - debugging aid
        return "Symbol(%s, %s, seg=%s off=%s rva=%s)" % (
            self.get("name"), self.get("kind"), self.get("segment"),
            self.get("offset"), self.get("rva"))


def _name(body, start):
    end = body.find(b"\0", start)
    if end == -1:
        end = len(body)
    return body[start:end].decode("utf-8", "replace")


def iter_records(data: bytes, limit=None):
    """Yield (offset, length, type, body) for every record in a symbol area."""
    off = 0
    end = len(data) if limit is None else min(limit, len(data))
    while off + 4 <= end:
        rec_len, rec_type = struct.unpack_from("<HH", data, off)
        if rec_len < 2 or off + 2 + rec_len > end:
            raise RecordError("invalid record at 0x%X (len=%d, remaining=%d)"
                              % (off, rec_len, end - off))
        yield off, rec_len, rec_type, data[off + 4:off + 2 + rec_len]
        off += 2 + rec_len


# Byte offsets inside a PROC record body (the 4-byte length/type header is
# already stripped). Microsoft's cvinfo.h PROC32 is:
#   pParent pEnd pNext len dbgStart dbgEnd typind off seg flags name
# LLVM's ProcSym matches. Some descriptions omit `typind`; `PROC_LAYOUTS` keeps
# both so the reader can pick the one the file actually uses (see
# reader.PdbFile._parse_symbols).
PROC_LAYOUTS = {
    "cvinfo-typind": {"offset": 28, "segment": 32, "name": 35},
    "no-typind": {"offset": 24, "segment": 28, "name": 31},
}
DEFAULT_PROC_LAYOUT = "cvinfo-typind"

THUNK_LAYOUT = {"offset": 12, "segment": 16, "name": 22}


def decode_symbol(rec_type: int, body: bytes, stream: int = None,
                  proc_layout: str = DEFAULT_PROC_LAYOUT) -> Symbol:
    """Decode one record into a Symbol, or return an empty Symbol when unknown."""
    sym = Symbol(kind=KIND_NAMES.get(rec_type, "0x%04X" % rec_type),
                 type=rec_type, stream=stream, segment=None, offset=None,
                 name=None, flags=None, is_function=False)
    if rec_type == S_PUB32:
        if len(body) < 10:
            return sym
        flags, offset, segment = struct.unpack_from("<IIH", body, 0)
        sym.update(flags=flags, offset=offset, segment=segment,
                   name=_name(body, 10),
                   is_function=bool(flags & PUBSYM_FUNCTION))
    elif rec_type in PROC_RECORDS:
        layout = PROC_LAYOUTS.get(proc_layout, PROC_LAYOUTS[DEFAULT_PROC_LAYOUT])
        if len(body) < layout["name"]:
            return sym
        offset, segment = struct.unpack_from("<IH", body, layout["offset"])
        sym.update(offset=offset, segment=segment,
                   name=_name(body, layout["name"]), is_function=True,
                   proc_layout=proc_layout)
    elif rec_type in DATA_RECORDS:
        if len(body) < 10:
            return sym
        _, offset, segment = struct.unpack_from("<IIH", body, 0)
        sym.update(offset=offset, segment=segment, name=_name(body, 10),
                   is_function=False)
    elif rec_type == S_THUNK32:
        layout = THUNK_LAYOUT
        if len(body) < layout["name"]:
            return sym
        offset, segment = struct.unpack_from("<IH", body, layout["offset"])
        sym.update(offset=offset, segment=segment, name=_name(body, layout["name"]),
                   is_function=True)
    elif rec_type in (S_PROCREFT, S_LPROCREFT):
        if len(body) >= 8:
            sym["reference_offset"] = struct.unpack_from("<I", body, 4)[0]
            sym["reference_module"] = struct.unpack_from("<H", body, 8)[0] \
                if len(body) >= 10 else None
        sym["name"] = _name(body, 10) if len(body) > 10 else None
    return sym


def _walk_records_ok(data: bytes, start: int) -> int:
    """Return the offset reached by walking records from `start`, or -1."""
    off = start
    count = 0
    end = len(data)
    while off + 4 <= end:
        rec_len, rec_type = struct.unpack_from("<HH", data, off)
        if rec_len < 2 or rec_len % 2 or rec_type > 0x8000:
            return -1
        if off + 2 + rec_len > end:
            return -1
        off += 2 + rec_len
        count += 1
    return off if (off == end and count > 0) else -1


def locate_record_area(data: bytes, hint: int = 0) -> tuple:
    """Find where the raw symbol records start inside a GSI hash stream.

    A public/global symbol stream is: 16-byte GSI header, bucket offset array,
    hash records, a bitmap, then the raw records. The bucket/bitmap sizes are
    derived from the header, but published PDBs have been observed with layouts
    that do not match every description of them, so the derived offsets are
    treated as *hints*: each candidate is validated by walking the records and
    requiring the walk to end exactly at the end of the stream. The last resort
    is an aligned scan. Returns (offset, method).
    """
    if len(data) < 16:
        raise RecordError("stream too small to hold a GSI header")
    signature, version, hr_size, bucket_count = struct.unpack_from("<IIII", data, 0)
    candidates = []
    if signature == GSI_SIGNATURE and version == GSI_VERSION:
        header_end = 16
        bucket_offsets = (hr_size // 8) * 4
        for bitmap in _bitmap_sizes(bucket_count):
            candidates.append((header_end + bucket_offsets + hr_size + bitmap, "structured"))
        candidates.append((header_end + bucket_offsets + hr_size, "structured-no-bitmap"))
        candidates.append((header_end, "header-only"))
    else:
        candidates.append((hint, "hint"))
    for off, method in candidates:
        if 0 <= off < len(data) and _walk_records_ok(data, off) == len(data):
            return off, method
    for off in range(0, len(data), 4):
        if _walk_records_ok(data, off) == len(data):
            return off, "scan"
    raise RecordError("no valid symbol-record area found (stream is %d bytes, "
                      "GSI signature=%r)" % (len(data), hex(signature)))


def _bitmap_sizes(bucket_count: int):
    """Plausible bitmap byte counts, most likely first."""
    if bucket_count <= 0:
        return [0]
    out = []
    for size in (((bucket_count + 7) // 8 + 3) // 4) * 4, \
                ((bucket_count + 7) // 8), \
                (bucket_count // 8) + 4, \
                (bucket_count // 8):
        if size >= 0 and size not in out:
            out.append(size)
    return out


def gsi_header(data: bytes) -> dict:
    if len(data) < 16:
        return {"valid": False}
    signature, version, hr_size, bucket_count = struct.unpack_from("<IIII", data, 0)
    return {"valid": signature == GSI_SIGNATURE and version == GSI_VERSION,
            "signature": signature, "version": version,
            "hash_record_bytes": hr_size, "bucket_count": bucket_count}
