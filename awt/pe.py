"""Minimal PE reader: exactly what the symbol matrix needs from a PE image.

Used to turn a module binary into a symbol-server identity:

* `TimeDateStamp` + `SizeOfImage`  -> the "PE signature" a symbol store indexes
  binaries under (`<name>/<TIMESTAMP:08X><SizeOfImage:x>/<name>`);
* the CodeView `RSDS` record of the debug directory -> PDB file name, GUID and
  age, which index the PDB (`<pdb>/<GUID><age>/<pdb>`).

Pure standard library: struct + os. Reads from a path or from bytes, never
executes anything, never needs Windows.
"""
import struct

__all__ = ["PeError", "PeImage", "read", "guid_to_symbol_server",
           "pe_signature", "pdb_signature"]

IMAGE_DIRECTORY_ENTRY_DEBUG = 6
IMAGE_DEBUG_TYPE_CODEVIEW = 2
IMAGE_FILE_MACHINE_I386 = 0x014C
IMAGE_FILE_MACHINE_AMD64 = 0x8664
IMAGE_FILE_MACHINE_ARM64 = 0xAA64

MACHINE_NAMES = {
    IMAGE_FILE_MACHINE_I386: "x86",
    IMAGE_FILE_MACHINE_AMD64: "x86-64",
    IMAGE_FILE_MACHINE_ARM64: "arm64",
    0x01C0: "arm",
    0x01C4: "armnt",
}


class PeError(ValueError):
    """Raised when a file is not a PE image, or is truncated/corrupt."""


def _u16(buf, off):
    if off + 2 > len(buf):
        raise PeError("truncated image at offset %d" % off)
    return struct.unpack_from("<H", buf, off)[0]


def _u32(buf, off):
    if off + 4 > len(buf):
        raise PeError("truncated image at offset %d" % off)
    return struct.unpack_from("<I", buf, off)[0]


def guid_to_symbol_server(guid: bytes) -> str:
    """RSDS GUID bytes -> the 32 hex characters a symbol store path uses.

    The first three fields are little-endian, the last 8 bytes are big-endian;
    that mixed order is what makes symbol-server GUIDs look "scrambled".
    """
    if len(guid) != 16:
        raise PeError("GUID must be 16 bytes, got %d" % len(guid))
    d1, d2, d3 = struct.unpack_from("<IHH", guid, 0)
    d4 = guid[8:16]
    return "%08X%04X%04X%s" % (d1, d2, d3, d4.hex().upper())


def pe_signature(timestamp: int, size_of_image: int) -> str:
    """Symbol-store key for a PE image: `<TIMESTAMP:08X><SizeOfImage:x>`."""
    return "%08X%x" % (timestamp & 0xFFFFFFFF, size_of_image & 0xFFFFFFFF)


def pdb_signature(guid_hex: str, age: int) -> str:
    """Symbol-store key for a PDB: `<GUID><age>`."""
    return "%s%d" % (guid_hex.upper(), int(age))


class PeImage:
    """Parsed headers of a PE image (no sections are loaded into memory)."""

    def __init__(self, data: bytes, source: str = "<memory>"):
        self.data = data
        self.source = source
        self._parse()

    def _parse(self):
        data = self.data
        if len(data) < 0x40 or data[:2] != b"MZ":
            raise PeError("%s: not a PE image (missing MZ header)" % self.source)
        e_lfanew = _u32(data, 0x3C)
        if data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
            raise PeError("%s: bad PE signature at 0x%X" % (self.source, e_lfanew))
        coff = e_lfanew + 4
        self.machine = _u16(data, coff)
        self.number_of_sections = _u16(data, coff + 2)
        self.timestamp = _u32(data, coff + 4)
        self.characteristics = _u16(data, coff + 18)
        opt = coff + 20
        self.optional_header_size = _u16(data, coff + 16)
        self.magic = _u16(data, opt)
        if self.magic == 0x20B:
            self.pe32_plus = True
            self.size_of_image = _u32(data, opt + 56)
            self.number_of_rva_and_sizes = _u32(data, opt + 108)
            self.data_directory = opt + 112
        elif self.magic == 0x10B:
            self.pe32_plus = False
            self.size_of_image = _u32(data, opt + 56)
            self.number_of_rva_and_sizes = _u32(data, opt + 92)
            self.data_directory = opt + 96
        else:
            raise PeError("%s: unknown optional header magic 0x%X" % (self.source, self.magic))
        self.sections = self._parse_sections(opt + self.optional_header_size)

    def _parse_sections(self, off):
        out = []
        for i in range(self.number_of_sections):
            base = off + i * 40
            if base + 40 > len(self.data):
                raise PeError("%s: truncated section table" % self.source)
            name = self.data[base:base + 8].rstrip(b"\0").decode("latin-1")
            virtual_size, virtual_address, raw_size, raw_ptr = struct.unpack_from(
                "<IIII", self.data, base + 8)
            out.append({"name": name, "virtual_size": virtual_size,
                        "virtual_address": virtual_address, "raw_size": raw_size,
                        "raw_ptr": raw_ptr})
        return out

    @property
    def machine_name(self) -> str:
        return MACHINE_NAMES.get(self.machine, "0x%04X" % self.machine)

    def rva_to_offset(self, rva: int, size: int = 1):
        for sec in self.sections:
            start = sec["virtual_address"]
            end = start + max(sec["virtual_size"], sec["raw_size"])
            if start <= rva and rva + size <= end:
                return sec["raw_ptr"] + (rva - start)
        return None

    def debug_entries(self) -> list:
        """IMAGE_DEBUG_DIRECTORY entries of the image (usually one)."""
        if self.number_of_rva_and_sizes <= IMAGE_DIRECTORY_ENTRY_DEBUG:
            return []
        va = _u32(self.data, self.data_directory + IMAGE_DIRECTORY_ENTRY_DEBUG * 8)
        size = _u32(self.data, self.data_directory + IMAGE_DIRECTORY_ENTRY_DEBUG * 8 + 4)
        if not va or not size:
            return []
        off = self.rva_to_offset(va, min(size, 28))
        if off is None:
            return []
        out = []
        for i in range(size // 28):
            base = off + i * 28
            if base + 28 > len(self.data):
                break
            (characteristics, timestamp, major, minor, dtype, size_of_data,
             address_of_raw, pointer_to_raw) = struct.unpack_from("<IIHHIIII", self.data, base)
            out.append({"type": dtype, "size_of_data": size_of_data,
                        "address_of_raw_data": address_of_raw,
                        "pointer_to_raw_data": pointer_to_raw,
                        "timestamp": timestamp})
        return out

    def codeview(self) -> dict:
        """The RSDS/PDB identity of this image, or {} when it carries none."""
        for entry in self.debug_entries():
            if entry["type"] != IMAGE_DEBUG_TYPE_CODEVIEW:
                continue
            ptr = entry["pointer_to_raw_data"]
            size = entry["size_of_data"]
            if not ptr or ptr + 24 > len(self.data):
                continue
            sig = self.data[ptr:ptr + 4]
            if sig != b"RSDS":
                # RSD0/other variants exist but are not what modern Windows ships.
                continue
            guid = self.data[ptr + 4:ptr + 20]
            age = _u32(self.data, ptr + 20)
            end = self.data.find(b"\0", ptr + 24, ptr + 24 + max(size, 512))
            name = self.data[ptr + 24:end if end != -1 else ptr + 24 + 260]
            return {
                "format": "RSDS",
                "pdb_name": name.decode("latin-1").replace("\\", "/").split("/")[-1],
                "pdb_path": name.decode("latin-1"),
                "guid": guid_to_symbol_server(guid),
                "age": age,
                "signature": pdb_signature(guid_to_symbol_server(guid), age),
            }
        return {}

    def summary(self) -> dict:
        cv = self.codeview()
        return {
            "source": self.source,
            "machine": self.machine_name,
            "timestamp": self.timestamp,
            "size_of_image": self.size_of_image,
            "pe_signature": pe_signature(self.timestamp, self.size_of_image),
            "sections": len(self.sections),
            "pdb": cv,
        }


def read(path: str) -> PeImage:
    with open(path, "rb") as f:
        return PeImage(f.read(), source=path)


def read_bytes(data: bytes, source="<memory>") -> PeImage:
    return PeImage(data, source=source)
