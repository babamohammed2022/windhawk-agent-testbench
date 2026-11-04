"""High-level PDB 7.0 reader: streams, DBI, section headers and symbols.

Design notes (the "why", so the choices stay auditable):

* **No dependency.** `llvm-pdbutil` and `pdbparse` were both evaluated:
  llvm-pdbutil is the most faithful reader but is not installed in the
  sandboxes this bench runs in (and cannot be installed without package
  network access); `pdbparse` is unmaintained, does not decode the DBI/public
  symbol streams of modern public PDBs and pulls in `construct`. This reader is
  ~500 lines of struct parsing over the MSF container and works offline.
  `llvm-pdbutil` is still used, when present, by `awt.symbols --cross-check` to
  validate this reader against a real PDB.
* **Only what the question needs.** The question is "does symbol X exist in
  module M of build B, and at which RVA". That needs the public symbol stream,
  the global symbol stream and the section headers -- not types, not lines, not
  source files.
* **Defensive.** Every offset is bounds-checked; a stream that cannot be parsed
  is reported as `unknown`, never as "symbol missing".
"""
import builtins
import os
import struct

from . import msf, records

__all__ = ["PdbError", "PdbFile", "open", "DEBUG_STREAM"]

DEBUG_STREAM = {
    "fpo": 0, "exception": 1, "fixup": 2, "omap_to_src": 3, "omap_from_src": 4,
    "section_hdr": 5, "token_rid_map": 6, "xdata": 7, "pdata": 8, "new_fpo": 9,
    "section_hdr_orig": 10,
}

MACHINE_NAMES = {0x014C: "x86", 0x8664: "x86-64", 0xAA64: "arm64", 0x01C4: "armnt"}

KIND_CODE = {"public": 0, "global-proc": 1, "local-proc": 2, "global-data": 3,
             "local-data": 4, "thunk": 5, "other": 9}
CODE_KIND = {v: k for k, v in KIND_CODE.items()}


class PdbError(ValueError):
    pass


def open(path: str) -> "PdbFile":
    """Read a PDB from disk.

    NOTE: this function shadows the builtin inside this module, so the builtin
    has to be spelled `builtins.open` everywhere here (the same trap the
    package __init__ falls into).
    """
    with builtins.open(path, "rb") as f:
        data = f.read()
    return PdbFile(data, source=path)


class PdbFile:
    def __init__(self, data: bytes, source: str = "<memory>"):
        self.source = source
        self.msf = msf.MsfFile(data, source=source)
        self.warnings = []
        self._read_pdb_info()
        self._read_dbi()
        self._symbols = None
        self._by_name = None

    # -- streams -------------------------------------------------------------
    def _read_pdb_info(self):
        self.version = self.signature = self.age = None
        self.guid = None
        self.named_streams = {}
        if not self.msf.has_stream(msf.STREAM_PDB_INFO):
            self.warnings.append("PDB info stream (1) missing")
            return
        data = self.msf.read_stream(msf.STREAM_PDB_INFO)
        if len(data) < 28:
            self.warnings.append("PDB info stream too small (%d bytes)" % len(data))
            return
        self.version, self.signature, self.age = struct.unpack_from("<III", data, 0)
        self.guid = _guid_text(data[12:28])
        try:
            self.named_streams = _named_streams(data, 28)
        except Exception as exc:  # named streams are informational only
            self.warnings.append("named stream map unreadable (%s)" % exc)

    def _read_dbi(self):
        if not self.msf.has_stream(msf.STREAM_DBI):
            raise PdbError("%s: DBI stream (3) missing; not a usable PDB" % self.source)
        data = self.msf.read_stream(msf.STREAM_DBI)
        if len(data) < 64:
            raise PdbError("%s: DBI stream too small (%d bytes)" % (self.source, len(data)))
        # DbiHdr (64 bytes): sig, ver, age, gsi, verhdr, pub, pdbsig, symrec,
        # pdbrbld, then the substream sizes modinfo/seccon/secmap/filinfo/tsmap,
        # mfcindex, dbghdr, ecinfo, flags, machine, pad.
        (version_signature, version_header, self.dbi_age, self.global_stream_index,
         build_number, self.public_stream_index, pdb_dll_version, self.sym_record_stream,
         pdb_dll_rbld, mod_info_size, section_contrib_size, section_map_size,
         src_file_info_size, type_server_size, mfc_type_server_index,
         optional_dbg_header_size, ec_substream_size, flags, machine,
         _padding) = struct.unpack_from("<iIIHHHHHHiiiiiIiiHHI", data, 0)
        self.dbi_version_header = version_header
        self.build_number = build_number
        self.machine = machine
        self.machine_name = MACHINE_NAMES.get(machine, "0x%04X" % machine)
        self.flags = flags
        if version_signature != -1:
            self.warnings.append("DBI version signature is %d, expected -1"
                                 % version_signature)
        if struct.calcsize("<iIIHHHHHHiiiiiIiiHHI") != 64:  # pragma: no cover
            raise PdbError("internal error: DBI header layout mismatch")

        # Substreams follow in a fixed order; only the module info and the
        # optional debug header are needed here, the rest is skipped by size.
        off = 64
        self.modules = _read_module_info(data, off, mod_info_size, self.source,
                                         self.warnings)
        off += mod_info_size
        off += section_contrib_size
        off += section_map_size
        off += src_file_info_size
        off += type_server_size
        off += ec_substream_size
        self.optional_debug_header = []
        if optional_dbg_header_size:
            count = optional_dbg_header_size // 2
            if off + count * 2 <= len(data):
                self.optional_debug_header = list(
                    struct.unpack_from("<%dH" % count, data, off))
            else:
                self.warnings.append("optional debug header truncated")
        self.sections = self._read_sections()

    def _read_sections(self):
        """IMAGE_SECTION_HEADER array from the DBI optional debug header."""
        for key in ("section_hdr", "section_hdr_orig"):
            index = DEBUG_STREAM[key]
            if index < len(self.optional_debug_header):
                stream_index = self.optional_debug_header[index]
                if stream_index in (0xFFFF, 0) or not self.msf.has_stream(stream_index):
                    continue
                data = self.msf.read_stream(stream_index)
                out = []
                for i in range(len(data) // 40):
                    base = i * 40
                    name = data[base:base + 8].rstrip(b"\0").decode("latin-1")
                    (virtual_size, virtual_address, raw_size, raw_ptr, _, _,
                     nreloc, nline, characteristics) = struct.unpack_from(
                        "<IIIIIIHHI", data, base + 8)
                    out.append({"index": i + 1, "name": name,
                                "virtual_size": virtual_size,
                                "virtual_address": virtual_address,
                                "raw_size": raw_size, "raw_ptr": raw_ptr,
                                "characteristics": characteristics})
                if out:
                    self._section_stream = stream_index
                    return out
        self.warnings.append("no section header stream in the DBI optional debug "
                             "header; RVA values will be unavailable")
        return []

    # -- symbols -------------------------------------------------------------
    def _symbol_streams(self):
        """(stream index, label) pairs to read symbols from, in priority order."""
        out = []
        if self.public_stream_index and self.msf.has_stream(self.public_stream_index):
            out.append((self.public_stream_index, "public"))
        if self.global_stream_index and self.msf.has_stream(self.global_stream_index):
            out.append((self.global_stream_index, "global"))
        return out

    def symbols(self) -> list:
        """All public/global symbols, with RVA resolved through section headers."""
        if self._symbols is None:
            self._symbols = self._parse_symbols()
        return self._symbols

    def _parse_symbols(self):
        out = []
        self.record_areas = []
        streams = self._symbol_streams()
        if not streams:
            self.warnings.append("neither the public nor the global symbol stream is "
                                 "present; symbol lookups will be 'unknown'")
            return out
        for stream_index, label in streams:
            data = self.msf.read_stream(stream_index)
            gsi = records.gsi_header(data)
            try:
                start, method = records.locate_record_area(data)
            except records.RecordError as exc:
                self.warnings.append("%s symbol stream %d: %s" % (label, stream_index, exc))
                continue
            area = {"stream": stream_index, "label": label,
                    "record_offset": start, "method": method,
                    "gsi": gsi, "bytes": len(data)}
            symbols, layout, score = self._decode_area(data[start:], stream_index, label)
            area.update(symbols=len(symbols), proc_layout=layout, layout_score=score)
            out.extend(symbols)
            self.record_areas.append(area)
        return out

    def _decode_area(self, area: bytes, stream_index: int, label: str):
        """Decode one record area, choosing the PROC layout the file uses.

        PROC32 is documented with a `typind` field between dbgEnd and the
        address, but descriptions of the record disagree about it. Rather than
        trusting one, both layouts are tried and the one that produces coherent
        segments and printable names wins; the choice is reported so a reader
        can audit it.
        """
        best = ([], None, -1)
        for layout in records.PROC_LAYOUTS:
            symbols, score = self._decode_once(area, stream_index, label, layout)
            if score > best[2]:
                best = (symbols, layout, score)
        return best

    def _decode_once(self, area: bytes, stream_index: int, label: str, layout: str):
        symbols, procs, coherent = [], 0, 0
        for _off, _len, rec_type, body in records.iter_records(area):
            sym = records.decode_symbol(rec_type, body, stream=stream_index,
                                        proc_layout=layout)
            name = sym.get("name")
            if not name or sym.get("segment") is None:
                continue
            if rec_type in records.PROC_RECORDS:
                procs += 1
                if 1 <= sym["segment"] <= max(len(self.sections), 1) and \
                        name.isprintable():
                    coherent += 1
            sym["rva"] = self.rva(sym["segment"], sym["offset"])
            sym["source"] = label
            symbols.append(sym)
        score = len(symbols) * 2 + (coherent * 4 if procs else 0) - \
            (procs - coherent) * 8
        return symbols, score

    def rva(self, segment, offset):
        """segment:offset -> RVA, or None when the segment is unknown."""
        if not segment or offset is None:
            return None
        if segment - 1 >= len(self.sections):
            return None
        return self.sections[segment - 1]["virtual_address"] + offset

    def by_name(self) -> dict:
        """name -> [Symbol] (a name can legitimately appear more than once)."""
        if self._by_name is None:
            index = {}
            for sym in self.symbols():
                index.setdefault(sym["name"], []).append(sym)
            self._by_name = index
        return self._by_name

    def names(self) -> list:
        return list(self.by_name().keys())

    def index_rows(self) -> list:
        """Compact rows used by the on-disk symbol cache."""
        return [[sym.get("rva"), KIND_CODE.get(sym.get("kind"), 9),
                 sym.get("source"), sym.get("segment"), sym.get("offset"),
                 sym.get("name")] for sym in self.symbols()]

    @staticmethod
    def from_index_rows(rows, source="<cache>") -> "PdbFile":
        """Rebuild the symbol view from a cache written by `index_rows`."""
        pdb = PdbFile.__new__(PdbFile)
        pdb.source = source
        pdb.warnings = ["symbols restored from cache; container not re-read"]
        pdb.sections = []
        pdb.record_areas = []
        syms = []
        for rva, kind, src, segment, offset, name in rows:
            syms.append(records.Symbol(name=name, kind=CODE_KIND.get(kind, "other"),
                                       rva=rva, source=src, segment=segment,
                                       offset=offset, is_function=kind in (0, 1, 2, 5)))
        pdb._symbols = syms
        pdb._by_name = None
        pdb.guid = pdb.age = None
        pdb.machine_name = None
        return pdb

    def info(self) -> dict:
        return {
            "source": self.source,
            "container": self.msf.info(),
            "pdb_version": self.version,
            "pdb_signature_value": self.signature,
            "age": self.age,
            "guid": self.guid,
            "dbi_version_header": getattr(self, "dbi_version_header", None),
            "machine": getattr(self, "machine_name", None),
            "build_number": getattr(self, "build_number", None),
            "streams": {
                "public": self.public_stream_index if hasattr(self, "public_stream_index") else None,
                "global": self.global_stream_index if hasattr(self, "global_stream_index") else None,
                "sym_record": self.sym_record_stream if hasattr(self, "sym_record_stream") else None,
                "sections": getattr(self, "_section_stream", None),
            },
            "sections": len(self.sections),
            "modules": len(getattr(self, "modules", [])),
            "symbol_count": len(self.symbols()),
            "record_areas": getattr(self, "record_areas", []),
            "warnings": self.warnings,
        }


def _guid_text(raw: bytes) -> str:
    d1, d2, d3 = struct.unpack_from("<IHH", raw, 0)
    return "%08X%04X%04X%s" % (d1, d2, d3, raw[8:16].hex().upper())


def _named_streams(data: bytes, off: int) -> dict:
    """The PDB named-stream map ('/Names', '/src/header', ...). Informational."""
    count = struct.unpack_from("<I", data, off)[0]
    off += 4
    if count > 4096 or off + count * 4 > len(data):
        return {}
    offsets = struct.unpack_from("<%dI" % count, data, off)
    off += count * 4
    length = struct.unpack_from("<I", data, off)[0]
    off += 4
    if length > len(data) - off:
        return {}
    blob = data[off:off + length]
    out = {}
    for value in offsets:
        if value >= length:
            continue
        end = blob.find(b"\0", value)
        name = blob[value:end if end != -1 else length].decode("utf-8", "replace")
        if name:
            out[name] = value
    return out


def _read_module_info(data: bytes, off: int, size: int, source: str, warnings: list) -> list:
    """DBI module info records: name, object and the stream holding its symbols."""
    out = []
    end = off + size
    if size <= 0 or end > len(data):
        if size:
            warnings.append("module info substream out of bounds")
        return out
    pos = off
    while pos + 64 <= end:
        try:
            flags, sym_stream = struct.unpack_from("<HH", data, pos + 32)
            sym_size, line_size, c13_size = struct.unpack_from("<III", data, pos + 36)
            num_files = struct.unpack_from("<H", data, pos + 48)[0]
            name_start = pos + 64
            name_end = data.find(b"\0", name_start, end)
            if name_end == -1:
                break
            module_name = data[name_start:name_end].decode("utf-8", "replace")
            obj_start = name_end + 1
            obj_end = data.find(b"\0", obj_start, end)
            object_name = data[obj_start:obj_end].decode("utf-8", "replace") \
                if obj_end != -1 else ""
            out.append({"module": module_name, "object": object_name,
                        "sym_stream": sym_stream, "sym_bytes": sym_size,
                        "line_bytes": line_size, "c13_line_bytes": c13_size,
                        "num_files": num_files})
            pos = obj_end + 1 if obj_end != -1 else end
            pos = (pos + 3) & ~3  # records are 4-byte aligned
        except struct.error as exc:
            warnings.append("module info parse stopped: %s" % exc)
            break
    return out
